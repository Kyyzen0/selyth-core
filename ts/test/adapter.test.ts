// The TypeScript adapter is a port: its contract is the Python adapter's output.
// These tests compare the two implementations byte for byte, and pin the same
// vectors as tools/test-ts-adapter.py.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { ADAPTER_VERSION, PYTHON_WHITESPACE, canonicalExpression, lower, render } from "../src/adapter.ts";
import { ObligationSetError, compareCodePoints, parseObligationSet, setDigest, type Obligation, type ObligationSet } from "../src/canonical.ts";
import { parseStrict, StrictJsonError } from "../src/json.ts";

const root = fileURLToPath(new URL("../..", import.meta.url));
const demoPath = join(root, "docs/specifications/examples/queue.obligations.json");
const python = process.env["SELYTH_PYTHON"] ?? join(root, ".venv/bin/python");
const BACKEND = "4.11.0";
const GOLDEN = "sha256:4f20d0d13af2656c2f3c6b15c83f0c9260d0385799ecc4b53fd2c1f1952499eb";

const demo = (): ObligationSet => parseObligationSet(readFileSync(demoPath, "utf8"));
const lowered = (d: ObligationSet = demo()): string => render(lower(d, BACKEND));

function withFile<T>(document: unknown, fn: (path: string) => T): T {
  const dir = mkdtempSync(join(tmpdir(), "selyth-ts-"));
  try {
    const path = join(dir, "set.json");
    writeFileSync(path, JSON.stringify(document), "utf8");
    return fn(path);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
const pythonLower = (path: string) =>
  spawnSync(python, [join(root, "tools/ts_adapter.py"), "lower", path, "--backend-version", BACKEND], { encoding: "utf8" });
const tsCli = (path: string, env: NodeJS.ProcessEnv = process.env, cwd?: string) =>
  spawnSync(process.execPath, [join(root, "ts/src/cli.ts"), "lower", path, "--backend-version", BACKEND], { encoding: "utf8", env, ...(cwd ? { cwd } : {}) });

function richSet(): ObligationSet {
  const d = demo();
  d.obligations.forEach((o, i) => {
    o.assumptions = [..."fbdaec"].map((c) => `assumption ${c} of ${i}`);
    o.affected_components = ["～/x.ts", "😀/x.ts", "z/mod.ts", "é/mod.ts", "éx/mod.ts", "a/mod.ts"];
    if (i === 1) o.intent_statement = "Café reste ouvert. 😀";
  });
  const base = d.obligations[0] as Obligation;
  const extra = (id: string, kind: string, statement: string): void => {
    d.obligations.push({ ...structuredClone(base), obligation_id: id, kind, statement });
  };
  extra("U-1", "permission", "ensures result > 0");
  extra("U-2", "postcondition", "requires x > 0");
  extra("U-3", "postcondition", "ensures f(x) > 0");
  extra("U-4", "postcondition", "ensures");
  extra("U-5", "postcondition", `ensures x > 0${"0".repeat(5000)}`);
  extra("U-6", "postcondition", "ensures a ==> b ==> c && !(d || e) - (f - g) == -(-h) % 007");
  extra("U-7", "invariant", "invariant 0 <= (a+b) < c.d .e");
  extra("U-8", "termination", "decreases  \n\t n - 1 ");
  extra("U-9", "postcondition", "ensures x > 0");
  return d;
}

test("TypeScript and Python adapters emit byte-identical IR (demonstration set)", () => {
  const py = pythonLower(demoPath);
  assert.equal(py.status, 0, py.stderr);
  assert.equal(lowered(), py.stdout);
});

test("TypeScript and Python adapters agree on a rich set (unicode, many assumptions, unsupported, huge literal)", () => {
  const d = richSet();
  withFile(d, (path) => {
    const py = pythonLower(path);
    assert.equal(py.status, 0, py.stderr);
    assert.equal(tsCli(path).stdout, py.stdout);
    assert.equal(lowered(parseObligationSet(readFileSync(path, "utf8"))), py.stdout);
  });
});

test("set digests agree with the Python tool", () => {
  const py = spawnSync(python, [join(root, "tools/obligation_set.py"), "digest", demoPath], { encoding: "utf8" });
  assert.equal(py.stdout.trim(), setDigest(demo()));
  withFile(richSet(), (path) => {
    const rich = spawnSync(python, [join(root, "tools/obligation_set.py"), "digest", path], { encoding: "utf8" });
    assert.equal(rich.stdout.trim(), setDigest(parseObligationSet(readFileSync(path, "utf8"))));
  });
});

test("the demonstration IR equals the golden pinned by the Python suite, and the version matches", () => {
  assert.equal(ADAPTER_VERSION, "0.2.1");
  assert.equal("sha256:" + createHash("sha256").update(lowered()).digest("hex"), GOLDEN);
});

test("deterministic: repeated runs, other time zones, locales and working directories", () => {
  const first = lowered();
  assert.equal(lowered(), first);
  const outputs = new Set<string>();
  for (const [TZ, LANG] of [["UTC", "C"], ["Asia/Tokyo", "fr_FR.UTF-8"], ["America/Sao_Paulo", "en_US.UTF-8"]] as const) {
    const run = tsCli(demoPath, { ...process.env, TZ, LANG, LC_ALL: LANG }, tmpdir());
    assert.equal(run.status, 0, run.stderr);
    outputs.add(run.stdout);
  }
  assert.deepEqual([...outputs], [first]);
});

test("formatting and ordering of the Obligation Set do not change the IR", () => {
  const first = lowered();
  const d = demo();
  const reversed = { ...d, obligations: [...d.obligations].reverse().map((o) => ({ ...o, assumptions: [...o.assumptions].reverse() })) };
  assert.equal(lowered(parseObligationSet(JSON.stringify(reversed, null, 8))), first);
  assert.equal(lowered(parseObligationSet(JSON.stringify(d).replace(/,/g, ",\r\n"))), first);
});

test("canonical expressions (same vectors as the Python suite)", () => {
  const vectors: Array<[string, string]> = [
    ["a+b*c", "a + b * c"], ["(a+b)*c", "(a + b) * c"], ["((a))", "a"], ["a - (b - c)", "a - (b - c)"],
    ["a-b-c", "a - b - c"], ["!(a&&b)", "!(a && b)"], ["!a&&b", "!a && b"], ["-x + 1", "-x + 1"],
    ["a ==> b ==> c", "a ==> b ==> c"], ["(a ==> b) ==> c", "(a ==> b) ==> c"], ["a||b&&c", "a || b && c"],
    ["(a||b)&&c", "(a || b) && c"], ["0<=i  <  n", "0 <= i < n"], ["x.y .z>=0", "x.y.z >= 0"],
    ["x%2==0", "x % 2 == 0"], ["true&&false", "true && false"], ["x == 0000", "x == 0"],
  ];
  for (const [source, want] of vectors) assert.equal(canonicalExpression(source), want, source);
  assert.equal(canonicalExpression(`x > ${"0".repeat(10)}${"9".repeat(5000)}`), `x > ${"9".repeat(5000)}`);
  for (const bad of ["f(x) > 0", "a[0]", 's == "x"', "a ? b : c", "x = 1", "1.5 > x", "x >", "x > 0 )", "", "a > b > c"]) {
    assert.throws(() => canonicalExpression(bad), /./, bad);
  }
});

test("strict parsing and validation refuse what JSON.parse tolerates", () => {
  assert.throws(() => parseStrict('{"a":1,"a":2}'), StrictJsonError);
  assert.throws(() => parseStrict('{"a":"\\ud800"}'), StrictJsonError);
  assert.throws(() => parseStrict("﻿{}"), StrictJsonError);
  assert.throws(() => parseStrict('{"a":NaN}'), StrictJsonError);
  const text = readFileSync(demoPath, "utf8");
  assert.throws(() => parseObligationSet(text.replace('"set_id": "queue"', '"set_id": "queue", "set_id": "x"')), ObligationSetError);
  assert.throws(() => parseObligationSet(JSON.stringify({ ...demo(), obligations: [] })), ObligationSetError);
  const dup = demo();
  (dup.obligations[1] as Obligation).obligation_id = "OBL-0007";
  assert.throws(() => parseObligationSet(JSON.stringify(dup)), ObligationSetError);
  const nfc = demo();
  (nfc.obligations[0] as Obligation).assumptions = ["café", "café"];
  assert.throws(() => parseObligationSet(JSON.stringify(nfc)), ObligationSetError);
});

test("ordering is by code point, not by UTF-16 code unit", () => {
  assert.ok(compareCodePoints("～", "😀") < 0); // U+FF5E < U+1F600; UTF-16 `<` says the opposite
  assert.ok("😀" < "～");
});

test("malformed UTF-8 is refused by both adapters, not repaired to U+FFFD", () => {
  const dir = mkdtempSync(join(tmpdir(), "selyth-ts-"));
  try {
    const original = readFileSync(demoPath);
    const at = original.indexOf(Buffer.from("Bounded queue")) + 7;
    const corrupted = Buffer.concat([original.subarray(0, at), Buffer.from([0xff, 0xfe]), original.subarray(at)]);
    const path = join(dir, "bad.json");
    writeFileSync(path, corrupted);
    assert.equal(tsCli(path).status, 1);
    assert.equal(pythonLower(path).status, 1);
    assert.match(tsCli(path).stderr, /UTF-8/);
    assert.match(pythonLower(path).stderr, /UTF-8/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the CLI exits 2 without --backend-version and 1 on an invalid set", () => {
  assert.equal(spawnSync(process.execPath, [join(root, "ts/src/cli.ts"), "lower", demoPath], { encoding: "utf8" }).status, 2);
  assert.equal(tsCli(join(root, "docs/specifications/report-schema.json")).status, 1);
});

test("vacuous statements are refused by both ports, with the same rule (R10)", () => {
  for (const statement of ["ensures true", "invariant (true)", "ensures result == result", "invariant size <= size"]) {
    const vacuous = demo();
    (vacuous.obligations[0] as Obligation).statement = statement;
    assert.throws(() => parseObligationSet(JSON.stringify(vacuous)), /vacuous statement/);
    withFile(vacuous, (path) => {
      assert.equal(tsCli(path).status, 1);
      assert.equal(pythonLower(path).status, 1);
      assert.match(tsCli(path).stderr, /vacuous/);
      assert.match(pythonLower(path).stderr, /vacuous/);
    });
  }
  for (const statement of ["ensures result.length >= 0", "ensures (size == size) && result > 0"]) {
    const ok = demo();
    (ok.obligations[0] as Obligation).statement = statement;
    parseObligationSet(JSON.stringify(ok)); // must not throw: the lint never refuses a meaningful statement
  }
});

test("whitespace is exactly Python's str.isspace() set, over every code point", () => {
  const py = spawnSync(python, ["-c", "print(','.join(str(c) for c in range(0x110000) if chr(c).isspace()))"], { encoding: "utf8" });
  assert.equal(py.status, 0, py.stderr);
  const expected = new Set(py.stdout.trim().split(",").map(Number));
  const ours = new Set<number>();
  for (let c = 0; c < 0x110000; c++) if (PYTHON_WHITESPACE.test(String.fromCodePoint(c))) ours.add(c);
  assert.deepEqual([...ours].sort((a, b) => a - b), [...expected].sort((a, b) => a - b));
  assert.ok(!ours.has(0xfeff) && ours.has(0x85) && ours.has(0x1c)); // where ECMAScript's \s differs
});

test("differential: odd whitespace, astral and control characters lower identically in both adapters", () => {
  const chars = [..." \t\n\v\f\r\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u200a\u2028\u2029\u202f\u205f\u3000",
                 "\ufeff", "\u180e", "\u200b", "\u200c", "\u2060", "\0", "\x7f", "\x01", "é", "e\u0301", "😀", "\u{10FFFF}", "\uFF5E", "#", "$", "\\", "'", "`", "~", "@", "?", "[", "{", ",", ";", ":"];
  const d = demo();
  const base = d.obligations[0] as Obligation;
  d.obligations = [];
  let n = 0;
  const add = (kind: string, statement: string): void => {
    d.obligations.push({ ...structuredClone(base), obligation_id: `F-${String(n++).padStart(4, "0")}`, kind, statement });
  };
  for (const c of chars) {
    for (const statement of [`ensures${c}x > 0`, `ensures x >${c}0`, `ensures x > 0${c}`, `${c}ensures x > 0`, `ensures x ${c} 0`, `ensures${c}`, `ensures x > ${c}`, `ensures ${c}`]) add("postcondition", statement);
    add("invariant", `invariant 0 <=${c}i`);
  }
  withFile(d, (path) => {
    const py = pythonLower(path);
    assert.equal(py.status, 0, py.stderr);
    const ts = tsCli(path);
    assert.equal(ts.status, 0, ts.stderr);
    assert.equal(ts.stdout, py.stdout);
    const ir = JSON.parse(py.stdout) as { units: unknown[]; unsupported: unknown[] };
    assert.ok(ir.units.length > 20 && ir.unsupported.length > 20, "the fuzz set exercises both outcomes");
  });
});
