// Obligation Set canonical form and digests (docs/specifications/obligation-set-canonicalization.md).
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { Ajv2020 } from "ajv/dist/2020.js";
import { parseStrict, StrictJsonError, type Json } from "./json.ts";

export class ObligationSetError extends Error {}

export interface Obligation {
  obligation_id: string;
  kind: string;
  statement: string;
  required_evidence_kind: string;
  intent_statement?: string;
  assumptions: string[];
  affected_components: string[];
}
export interface ObligationSet {
  selyth_obligation_set_version: string;
  set_id: string;
  specification_version: string;
  title?: string;
  obligations: Obligation[];
}

/** Unicode code point order, which is also UTF-8 byte order (UTF-16 `<` is not). */
export function compareCodePoints(a: string, b: string): number {
  const x = Array.from(a, (c) => c.codePointAt(0) as number);
  const y = Array.from(b, (c) => c.codePointAt(0) as number);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    if (x[i] !== y[i]) return (x[i] as number) < (y[i] as number) ? -1 : 1;
  }
  return x.length - y.length;
}

function nfc(value: Json): Json {
  if (typeof value === "string") return value.normalize("NFC");
  if (Array.isArray(value)) return value.map(nfc);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k.normalize("NFC"), nfc(v)]));
  }
  return value;
}

function sortedSet(values: string[], what: string): string[] {
  const out = [...values].sort(compareCodePoints);
  const dupes = out.filter((v, i) => i > 0 && v === out[i - 1]);
  if (dupes.length > 0) throw new ObligationSetError(`duplicate ${what} after normalisation: ${JSON.stringify(dupes)}`);
  return out;
}

function canonicalObligation(raw: Obligation): Obligation {
  const o = nfc(raw as unknown as Json) as unknown as Obligation;
  return { ...o, assumptions: sortedSet(o.assumptions, "assumption"),
           affected_components: sortedSet(o.affected_components, "affected component") };
}

export function canonicalForm(document: ObligationSet): ObligationSet {
  const normalised = nfc(document as unknown as Json) as unknown as ObligationSet;
  const obligations = normalised.obligations.map(canonicalObligation);
  sortedSet(obligations.map((o) => o.obligation_id), "obligation_id");
  obligations.sort((a, b) => compareCodePoints(a.obligation_id, b.obligation_id));
  return { ...normalised, obligations };
}

/** json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False) of the Python tools. */
export function serialise(value: Json, indent?: number): string {
  const sorted = (v: Json): Json => {
    if (Array.isArray(v)) return v.map(sorted);
    if (v !== null && typeof v === "object") {
      return Object.fromEntries(Object.keys(v).sort(compareCodePoints).map((k) => [k, sorted((v as { [k: string]: Json })[k] as Json)]));
    }
    return v;
  };
  return JSON.stringify(sorted(value), null, indent);
}

const digestOf = (text: string): string => "sha256:" + createHash("sha256").update(text, "utf8").digest("hex");

export const canonicalText = (document: ObligationSet): string =>
  serialise(canonicalForm(document) as unknown as Json);
export const setDigest = (document: ObligationSet): string => digestOf(canonicalText(document));

export function obligationDigests(document: ObligationSet): Map<string, string> {
  return new Map(canonicalForm(document).obligations.map((o) => [o.obligation_id, digestOf(serialise(o as unknown as Json))]));
}

export function sha256Text(text: string): string {
  return digestOf(text);
}

/** Decode UTF-8 strictly: readFileSync(path, "utf8") would silently turn malformed bytes into U+FFFD. */
export function decodeUtf8(bytes: Uint8Array): string {
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    throw new ObligationSetError("not valid UTF-8");
  }
}

const schemaUrl = new URL("../../docs/specifications/obligation-set-schema.json", import.meta.url);

const SELF_COMPARISON = /^([A-Za-z_][A-Za-z0-9_]*)\s*(==|<=|>=)\s*\1$/;

/** Why an obligation expression would prove nothing, or null. Mirrors the Python lint in
 *  tools/obligation_set.py exactly: only syntactic vacuity is refused — the literal `true`,
 *  and a name compared with itself (`x == x`, `x <= x`, `x >= x`), optionally parenthesised. */
function vacuousReason(expression: string): string | null {
  let text = expression.trim();
  while (text.startsWith("(") && text.endsWith(")")) {
    let depth = 0;
    let wraps = true;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (ch === "(") depth++;
      else if (ch === ")") {
        depth--;
        if (depth === 0 && i < text.length - 1) { wraps = false; break; }
      }
    }
    if (!wraps) break;
    text = text.slice(1, -1).trim();
  }
  if (text === "true") return "the expression is the literal `true`";
  const m = SELF_COMPARISON.exec(text);
  if (m) return `a comparison of ${JSON.stringify(m[1])} with itself is always true`;
  return null;
}

/** Parse strictly, validate against the shared JSON Schema, then run the semantic checks. */
export function parseObligationSet(text: string): ObligationSet {
  let parsed: Json;
  try {
    parsed = parseStrict(text);
  } catch (e) {
    if (e instanceof StrictJsonError) throw new ObligationSetError(`not valid JSON: ${e.message}`);
    throw e;
  }
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  const validate = ajv.compile(JSON.parse(readFileSync(schemaUrl, "utf8")));
  if (!validate(parsed)) {
    throw new ObligationSetError("schema violation: " + (validate.errors ?? []).map((e) => `${e.instancePath || "$"} ${e.message}`).join("; "));
  }
  const document = parsed as unknown as ObligationSet;
  const canonical = canonicalForm(document); // duplicates after normalisation
  for (const obligation of canonical.obligations) {
    const m = /^(\S+)\s+(.*)$/.exec(obligation.statement);
    if (!m) continue; // no expression after the keyword: the adapter reports it as unsupported
    const reason = vacuousReason(m[2]);
    if (reason !== null) {
      throw new ObligationSetError(`obligation ${obligation.obligation_id}: vacuous statement ` +
        `${JSON.stringify(obligation.statement)}: ${reason}`);
    }
  }
  return document;
}
