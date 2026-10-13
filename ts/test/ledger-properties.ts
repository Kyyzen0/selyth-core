// The ledger obligations as executable predicates over an implementation, shared by the unit
// test (ledger.test.ts) and the evaluation oracle (../eval/oracle.ts). Sampling is not evidence:
// it is an oracle that does not depend on Dafny, the translator or the proof model.

import assert from "node:assert/strict";

export type Ledger = {
  deposit(balance: number, amount: number, cap: number): number;
  withdraw(balance: number, amount: number): number;
  fee(amount: number, bps: number): number;
  clamp(x: number, lo: number, hi: number): number;
  share(total: number, parts: number): [number, number];
  accrue: Accrue;
  transfer(from: number, to: number, amount: number): [number, number];
};

// Deterministic generator: a failing run must be reproducible.
let state = 123456789;
const next = (): number => (state = (Math.imul(state, 1103515245) + 12345) >>> 0) / 2 ** 32;
export const int = (max: number): number => Math.floor(next() * (max + 1));
export const SAMPLES = 2000;
export const M = 1_000_000; // keeps every product below 2^53, the documented assumption

export type Check = (sample: () => boolean) => void;
export const forAll: Check = (sample) => {
  for (let i = 0; i < SAMPLES; i++) assert.ok(sample(), `counterexample on sample ${i}`);
};

export type Accrue = (balance: number, step: number, periods: number, observe?: (i: number, total: number) => void) => number;

/** The loop obligations, observed on a run of `impl` through its hook (at every iteration head and at exit). */
export function loopProperties(impl: Accrue): { bounded: boolean; terminates: boolean; accumulates: boolean } {
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

export const observed = (impl: Accrue, pick: "bounded" | "terminates" | "accumulates"): boolean => {
  try { return loopProperties(impl)[pick]; } catch { return false; }
};

// Preconditions are necessary: where one fails, the code does break the matching guarantee.
export function buildProperties(m: Ledger): Record<string, () => boolean> {
  const { deposit, withdraw, fee, clamp, share, accrue, transfer } = m;
  return {
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
}

