// The ledger obligations, run against the real TypeScript on pseudo-random inputs.
//
// This is NOT evidence in the sense of the report schema: sampling proves nothing. It guards
// the one gap the Dafny proofs cannot: the proof workspace models ledger.ts by hand, and a
// model can drift from the code. Every obligation id must have a predicate here, and each
// predicate must hold on the code and fail on a deliberately wrong one.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { accrue, clamp, deposit, fee, share, transfer, withdraw } from "../../docs/specifications/examples/ledger/ledger.ts";

const set = JSON.parse(readFileSync(new URL("../../docs/specifications/examples/ledger/ledger.obligations.json", import.meta.url), "utf8")) as {
  obligations: Array<{ obligation_id: string }>;
};

// Deterministic generator: a failing run must be reproducible.
let state = 123456789;
const next = (): number => (state = (Math.imul(state, 1103515245) + 12345) >>> 0) / 2 ** 32;
const int = (max: number): number => Math.floor(next() * (max + 1));
const SAMPLES = 2000;
const M = 1_000_000; // keeps every product below 2^53, the documented assumption

type Check = (sample: () => boolean) => void;
const forAll: Check = (sample) => {
  for (let i = 0; i < SAMPLES; i++) assert.ok(sample(), `counterexample on sample ${i}`);
};

type Accrue = (balance: number, step: number, periods: number, observe?: (i: number, total: number) => void) => number;

/** The loop obligations, observed on a run of `impl` through its hook (at every iteration head and at exit). */
function loopProperties(impl: Accrue): { bounded: boolean; terminates: boolean; accumulates: boolean } {
  const b = int(M), step = int(M), periods = int(50);
  let bounded = true, accumulates = true, terminates = true, visits = 0, last = Infinity;
  impl(b, step, periods, (i, total) => {
    if (++visits > periods + 2) throw new Error("the loop did not stop");
    bounded &&= 0 <= i && i <= periods;
    accumulates &&= total === b + i * step;
    terminates &&= periods - i < last && periods - i >= 0;
    last = periods - i;
  });
  return { bounded, terminates, accumulates };
}

const observed = (impl: Accrue, pick: "bounded" | "terminates" | "accumulates"): boolean => {
  try { return loopProperties(impl)[pick]; } catch { return false; }
};

// Preconditions are necessary: where one fails, the code does break the matching guarantee.
const properties: Record<string, () => boolean> = {
  "LED-01": () => { const b = int(M), a = int(M) + 1, cap = b + int(a - 1); return deposit(b, a, cap) > cap; },
  "LED-02": () => { const b = int(M), a = int(M); return deposit(b, a, b + a) === b + a; },
  "LED-03": () => { const b = int(M), a = int(M), cap = b + a + int(M); return deposit(b, a, cap) <= cap; },
  "LED-04": () => { const b = int(M), a = b + 1 + int(M); return withdraw(b, a) < 0; },
  "LED-05": () => { const b = int(M), a = int(b); return withdraw(b, a) === b - a; },
  "LED-06": () => { const b = int(M), a = int(b), r = withdraw(b, a); return 0 <= r && r <= b; },
  "LED-07": () => { const a = 1 + int(M), bps = 10001 + int(M); return fee(a, bps) > a; },
  "LED-08": () => { const a = int(M), bps = int(10000); return fee(a, bps) <= a; },
  "LED-09": () => { const a = int(M); return fee(a, 0) === 0; },
  "LED-10": () => { const lo = 1 + int(M), hi = int(lo - 1), x = int(2 * M); const r = clamp(x, lo, hi); return !(lo <= r && r <= hi); },
  "LED-11": () => { const lo = int(M), hi = lo + int(M), x = int(3 * M) - M; const r = clamp(x, lo, hi); return lo <= r && r <= hi; },
  "LED-12": () => { const lo = int(M), hi = lo + int(M), x = lo + int(hi - lo); return clamp(x, lo, hi) === x; },
  "LED-13": () => { const t = int(M); const [, rem] = share(t, 0); return !(rem < 0); },
  "LED-14": () => { const t = int(M), p = 1 + int(1000); const [s, r] = share(t, p); return s * p + r === t; },
  "LED-15": () => { const t = int(M), p = 1 + int(1000); const [, r] = share(t, p); return r < p; },
  "LED-16": () => loopProperties(accrue).bounded,
  "LED-17": () => loopProperties(accrue).terminates,
  "LED-18": () => loopProperties(accrue).accumulates,
  "LED-19": () => { const b = int(M), step = int(M), n = int(50); return accrue(b, step, n) === b + n * step; },
  "LED-20": () => { const f = int(M), t = int(M), a = int(f); const [f2, t2] = transfer(f, t, a); return f2 + t2 === f + t; },
};

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
