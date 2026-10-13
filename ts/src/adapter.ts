// TypeScript adapter: Obligation Set -> Verification IR (docs/specifications/verification-ir.md).
// A pure function of the Obligation Set and the declared verification-tool version.
import { canonicalForm, compareCodePoints, obligationDigests, serialise, setDigest, sha256Text, type ObligationSet } from "./canonical.ts";
import type { Json } from "./json.ts";

export const ADAPTER_NAME = "selyth-ts-adapter";
// Changing what `lower` emits for any input requires changing this value.
export const ADAPTER_VERSION = "0.2.1";
export const IR_VERSION = "0.2.0";
export const BACKEND_NAME = "dafny";

const CLAUSES: Record<string, string> = { precondition: "requires", postcondition: "ensures", invariant: "invariant", termination: "decreases" };

export class ExpressionError extends Error {}

// Python's str.isspace() set, which `str.split()`, `str.strip()` and `re`'s `\s` use. ECMAScript's `\s`
// differs (it has U+FEFF; it lacks U+001C-001F and U+0085), so it must not be used here.
const WS = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
export const PYTHON_WHITESPACE = new RegExp(`^[${WS}]$`, "u");
const LEADING = new RegExp(`^[${WS}]+`, "u");
const TRAILING = new RegExp(`[${WS}]+$`, "u");
const TOKEN = new RegExp(`[${WS}]*(==>|<=|>=|==|!=|&&|\\|\\||[-+*/%<>!().]|[A-Za-z_][A-Za-z0-9_]*|[0-9]+)`, "uy");
const IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** First Unicode code point (Python's s[:1]); slice(0, 1) would cut an astral character in half. */
const firstCodePoint = (text: string): string => {
  const cp = text.codePointAt(0);
  return cp === undefined ? "" : String.fromCodePoint(cp);
};

function tokenize(source: string): string[] {
  const text = source.replace(TRAILING, "");
  const tokens: string[] = [];
  let pos = 0;
  while (pos < text.length) {
    TOKEN.lastIndex = pos;
    const m = TOKEN.exec(text);
    if (!m) throw new ExpressionError(`unsupported character U+${firstCodePoint(text.slice(pos).replace(LEADING, "")).codePointAt(0)?.toString(16).toUpperCase().padStart(4, "0")}`);
    tokens.push(m[1] as string);
    pos = TOKEN.lastIndex;
  }
  return tokens;
}

type Node =
  | { t: "id" | "int" | "bool"; v: string }
  | { t: "member"; base: Node; name: string }
  | { t: "un"; op: string; operand: Node }
  | { t: "cmp"; ops: string[]; operands: Node[] }
  | { t: "bin"; op: string; left: Node; right: Node };

class Parser {
  private i = 0;
  private readonly tokens: string[];
  constructor(tokens: string[]) { this.tokens = tokens; }
  private peek(): string | undefined { return this.tokens[this.i]; }
  private take(): string | undefined { return this.tokens[this.i++]; }
  parse(): Node {
    if (this.tokens.length === 0) throw new ExpressionError("empty expression");
    const node = this.implies();
    if (this.peek() !== undefined) throw new ExpressionError(`unexpected '${this.peek()}'`);
    return node;
  }
  private implies(): Node {
    const left = this.left(["||"], () => this.conjunction());
    if (this.peek() === "==>") { this.take(); return { t: "bin", op: "==>", left, right: this.implies() }; }
    return left;
  }
  private left(ops: string[], operand: () => Node): Node {
    let node = operand();
    while (this.peek() !== undefined && ops.includes(this.peek() as string)) {
      const op = this.take() as string;
      node = { t: "bin", op, left: node, right: operand() };
    }
    return node;
  }
  private conjunction(): Node { return this.left(["&&"], () => this.comparison()); }
  private comparison(): Node {
    const operands = [this.additive()];
    const ops: string[] = [];
    while (this.peek() !== undefined && ["<", "<=", ">", ">=", "==", "!="].includes(this.peek() as string)) {
      ops.push(this.take() as string);
      operands.push(this.additive());
    }
    if (ops.length > 1 && !ops.every((o) => o === "<" || o === "<=")) throw new ExpressionError("only chains of < and <= are supported");
    return ops.length === 0 ? (operands[0] as Node) : { t: "cmp", ops, operands };
  }
  private additive(): Node { return this.left(["+", "-"], () => this.multiplicative()); }
  private multiplicative(): Node { return this.left(["*", "/", "%"], () => this.unary()); }
  private unary(): Node {
    const p = this.peek();
    if (p === "!" || p === "-") { this.take(); return { t: "un", op: p, operand: this.unary() }; }
    return this.postfix();
  }
  private postfix(): Node {
    let node = this.primary();
    while (this.peek() === ".") {
      this.take();
      const name = this.take();
      if (name === undefined || !IDENT.test(name)) throw new ExpressionError("member access needs an identifier");
      node = { t: "member", base: node, name };
    }
    return node;
  }
  private primary(): Node {
    const token = this.take();
    if (token === "(") {
      const node = this.implies();
      if (this.take() !== ")") throw new ExpressionError("missing ')'");
      return node;
    }
    if (token === undefined) throw new ExpressionError("unexpected end of expression");
    if (token === "true" || token === "false") return { t: "bool", v: token };
    if (/^[0-9]+$/.test(token)) return { t: "int", v: token.replace(/^0+/, "") || "0" }; // textual: no bounded integer type
    if (IDENT.test(token)) return { t: "id", v: token };
    throw new ExpressionError(`unexpected '${token}'`);
  }
}

const BINARY: Record<string, number> = { "==>": 1, "||": 2, "&&": 3, "+": 5, "-": 5, "*": 6, "/": 6, "%": 6 };
const precedence = (n: Node): number => (n.t === "bin" ? (BINARY[n.op] as number) : n.t === "cmp" ? 4 : n.t === "un" ? 7 : 8);

function print(n: Node): string {
  switch (n.t) {
    case "id": case "int": case "bool": return n.v;
    case "member": return `${precedence(n.base) >= 8 ? print(n.base) : `(${print(n.base)})`}.${n.name}`;
    case "un": {
      const text = print(n.operand);
      const wrap = precedence(n.operand) < 7 || (n.op === "-" && n.operand.t === "un" && n.operand.op === "-");
      return n.op + (wrap ? `(${text})` : text);
    }
    case "cmp": {
      const parts = n.operands.map((o) => (precedence(o) >= 5 ? print(o) : `(${print(o)})`));
      return n.ops.reduce((out, op, k) => `${out} ${op} ${parts[k + 1]}`, parts[0] as string);
    }
    case "bin": {
      const prec = BINARY[n.op] as number;
      const rightAssoc = n.op === "==>";
      let lt = print(n.left), rt = print(n.right);
      if (precedence(n.left) < prec || (rightAssoc && precedence(n.left) === prec)) lt = `(${lt})`;
      if (precedence(n.right) < prec || (!rightAssoc && precedence(n.right) === prec)) rt = `(${rt})`;
      return `${lt} ${n.op} ${rt}`;
    }
  }
}

export function canonicalExpression(text: string): string {
  return print(new Parser(tokenize(text)).parse());
}

type Lowered = { unit: { keyword: string; expression: string } } | { unsupported: string };

function lowerObligation(kind: string, statement: string): Lowered {
  const keyword = CLAUSES[kind];
  if (keyword === undefined) return { unsupported: `kind '${kind}' cannot be lowered to a ${BACKEND_NAME} clause` };
  // Python's str.split(None, 1): split on the first run of whitespace.
  const m = new RegExp(`^[${WS}]*([^${WS}]+)(?:[${WS}]+([\\s\\S]*))?$`, "u").exec(statement);
  if (!m || m[1] !== keyword) return { unsupported: `a ${kind} statement must start with '${keyword}'` };
  if (m[2] === undefined || m[2] === "") return { unsupported: `no expression after '${keyword}'` };
  try {
    return { unit: { keyword, expression: canonicalExpression(m[2]) } };
  } catch (e) {
    if (e instanceof ExpressionError) return { unsupported: `expression outside the supported subset: ${e.message}` };
    throw e;
  }
}

export function reproducibilityKey(setDigestValue: string, backendVersion: string): string {
  return sha256Text(serialise({
    adapter: { name: ADAPTER_NAME, version: ADAPTER_VERSION },
    backend: { name: BACKEND_NAME, version: backendVersion },
    ir_version: IR_VERSION,
    obligation_set_digest: setDigestValue,
  }));
}

export function lower(document: ObligationSet, backendVersion: string): Json {
  const canonical = canonicalForm(document);
  const digest = setDigest(document);
  const digests = obligationDigests(document);
  const units: Json[] = [];
  const unsupported: Json[] = [];
  for (const o of canonical.obligations) {
    const entry: { [k: string]: Json } = {
      obligation_id: o.obligation_id, obligation_digest: digests.get(o.obligation_id) as string, kind: o.kind,
      statement: o.statement, required_evidence_kind: o.required_evidence_kind,
    };
    if (o.intent_statement !== undefined) entry["intent_statement"] = o.intent_statement;
    const result = lowerObligation(o.kind, o.statement);
    if ("unit" in result) {
      units.push({ ...entry, clause: result.unit, context: { assumptions: o.assumptions, affected_components: o.affected_components } });
    } else {
      unsupported.push({ ...entry, affected_components: o.affected_components, reason: result.unsupported });
    }
  }
  return {
    selyth_ir_version: IR_VERSION,
    reproducibility_key: reproducibilityKey(digest, backendVersion),
    obligation_set: { digest, set_id: canonical.set_id, specification_version: canonical.specification_version },
    adapter: { name: ADAPTER_NAME, version: ADAPTER_VERSION },
    backend: { name: BACKEND_NAME, version: backendVersion },
    units, unsupported,
  };
}

/** The only serialisation of an IR: indented, sorted keys, one final newline. */
export const render = (ir: Json): string => serialise(ir, 2) + "\n";
