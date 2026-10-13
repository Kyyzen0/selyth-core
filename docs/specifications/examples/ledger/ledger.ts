// Money arithmetic in cents: the TypeScript the Obligation Set ledger.obligations.json is about.
// A pure subset (no I/O, no mutation of arguments), small enough to model by hand in Dafny
// (ledger.workspace.dfy). Every amount is a safe integer; callers must keep products below 2^53.

export function deposit(balance: number, amount: number, _cap: number): number {
  return balance + amount;
}

export function withdraw(balance: number, amount: number): number {
  return balance - amount;
}

/** Fee in basis points of an amount (floor). */
export function fee(amount: number, bps: number): number {
  return Math.floor((amount * bps) / 10000);
}

export function clamp(x: number, lo: number, hi: number): number {
  if (x < lo) return lo;
  if (x > hi) return hi;
  return x;
}

/** Split a total between `parts` people: [share each, remainder]. */
export function share(total: number, parts: number): [number, number] {
  if (parts === 0) return [0, total];
  return [Math.floor(total / parts), total % parts];
}

/** Add a fixed step once per period. */
export function accrue(balance: number, step: number, periods: number): number {
  let total = balance;
  for (let i = 0; i < periods; i++) {
    total += step;
  }
  return total;
}

export function transfer(from: number, to: number, amount: number): [number, number] {
  return [from - amount, to + amount];
}
