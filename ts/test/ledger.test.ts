// The ledger obligations, run against the real TypeScript on pseudo-random inputs.
//
// This is NOT evidence in the sense of the report schema: sampling proves nothing. It guards
// the one gap the Dafny proofs cannot: the proof workspace models ledger.ts, and a model can
// drift from the code. Every obligation id must have a predicate here, and each predicate must
// hold on the code and fail on a deliberately wrong one. The predicates live in
// ledger-properties.ts so that the evaluation oracle runs the very same ones.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import * as ledger from "../../docs/specifications/examples/ledger/ledger.ts";
import { accrue } from "../../docs/specifications/examples/ledger/ledger.ts";
import { buildProperties, forAll, int, M, observed, SAMPLES, type Accrue } from "./ledger-properties.ts";

const set = JSON.parse(readFileSync(new URL("../../docs/specifications/examples/ledger/ledger.obligations.json", import.meta.url), "utf8")) as {
  obligations: Array<{ obligation_id: string }>;
};

const properties = buildProperties(ledger);

test("every obligation of the ledger has an executable predicate, and no predicate lacks an obligation", () => {
  assert.deepEqual(Object.keys(properties).sort(), set.obligations.map((o) => o.obligation_id).sort());
});

for (const [id, property] of Object.entries(properties)) {
  test(`${id} holds on ${SAMPLES} pseudo-random inputs of the real code`, () => forAll(property));
}

test("the loop predicates observe the real accrue, and fail on loops that break each obligation", () => {
  // Each wrong loop gives the right final answer for some inputs, so only observing the loop can tell.
  const extraIteration: Accrue = (b, s, p, o) => { let t = b; for (let i = 0; i <= p; i++) { o?.(i, t); t += s; } o?.(p + 1, t); return t - s; };
  const noProgress: Accrue = (b, s, p, o) => { let t = b; let i = 0; while (i < p) { o?.(i, t); t += s; i += 0; if (t > b + 5 * s + 1) i = p; } o?.(p, t); return t; };
  const driftingTotal: Accrue = (b, s, p, o) => { let t = b; for (let i = 0; i < p; i++) { o?.(i, t); t += s + 1; } o?.(p, t - p); return t - p; };
  forAll(() => observed(accrue, "bounded") && observed(accrue, "terminates") && observed(accrue, "accumulates"));
  assert.throws(() => forAll(() => observed(extraIteration, "bounded")));
  assert.throws(() => forAll(() => observed(noProgress, "terminates")));
  assert.throws(() => forAll(() => observed(driftingTotal, "accumulates")));
});

test("accrue behaves the same with and without the hook", () => {
  forAll(() => { const b = int(M), s = int(M), n = int(50); return accrue(b, s, n) === accrue(b, s, n, () => {}); });
});

test("the predicates can fail: a deliberately wrong implementation is caught", () => {
  const wrongWithdraw = (b: number, a: number): number => b - a - 1;
  const wrongFee = (a: number, bps: number): number => Math.floor((a * bps) / 100);
  const wrongShare = (t: number, p: number): [number, number] => [Math.floor(t / p) + 1, t % p];
  const wrongTransfer = (f: number, t: number, a: number): [number, number] => [f - a, t + a + 1];
  assert.throws(() => forAll(() => { const b = int(M), a = int(b); return wrongWithdraw(b, a) === b - a; }));
  assert.throws(() => forAll(() => { const a = int(M), bps = int(10000); return wrongFee(a, bps) <= a; }));
  assert.throws(() => forAll(() => { const t = int(M), p = 1 + int(1000); const [s, r] = wrongShare(t, p); return s * p + r === t; }));
  assert.throws(() => forAll(() => { const f = int(M), t = int(M), a = int(f); const [f2, t2] = wrongTransfer(f, t, a); return f2 + t2 === f + t; }));
});
