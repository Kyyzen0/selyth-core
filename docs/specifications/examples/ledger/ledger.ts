// Money arithmetic in cents: the TypeScript the Obligation Set ledger.obligations.json is about.
// A pure subset (no I/O, no mutation of arguments), small enough to model by hand in Dafny
// (ledger.workspace.dfy). Every amount is a safe integer; callers must keep products below 2^53.
//
// The proof contract lives in the code as selyth: comments: the obligation markers at the head of
// each function (and of the loop), the caller methods after the function they call, the helper
// lemma at the top level, the loop invariant inside the loop, the proof hint at the head of fee.

// selyth:preamble lemma MulMono(a: nat, b: nat, c: nat)
// selyth:preamble   requires b <= c
// selyth:preamble   ensures a * b <= a * c
// selyth:preamble   decreases a
// selyth:preamble {
// selyth:preamble   if a > 0 { MulMono(a - 1, b, c); }
// selyth:preamble }

export function deposit(balance: number, amount: number, _cap: number): number {
  // selyth:obligation LED-01 LED-02 LED-03
  return balance + amount;
}
// selyth:caller method Credit(balance: nat, amount: nat, cap: nat)
// selyth:caller   requires balance + amount <= cap
// selyth:caller {
// selyth:caller   var credited := Deposit(balance, amount, cap);
// selyth:caller }

export function withdraw(balance: number, amount: number): number {
  // selyth:obligation LED-04 LED-05 LED-06
  return balance - amount;
}
// selyth:caller method Spend(balance: nat, price: nat)
// selyth:caller   requires price <= balance
// selyth:caller {
// selyth:caller   var left := Withdraw(balance, price);
// selyth:caller }

/** Fee in basis points of an amount (floor). */
export function fee(amount: number, bps: number): number {
  // selyth:obligation LED-07 LED-08 LED-09
  // selyth:hint if bps <= 10000 { MulMono(amount, bps, 10000); }
  return Math.floor((amount * bps) / 10000);
}
// selyth:caller method Charge(amount: nat, bps: nat)
// selyth:caller   requires bps <= 10000
// selyth:caller {
// selyth:caller   var charged := Fee(amount, bps);
// selyth:caller }

export function clamp(x: number, lo: number, hi: number): number {
  // selyth:obligation LED-10 LED-11 LED-12
  if (x < lo) return lo;
  if (x > hi) return hi;
  return x;
}
// selyth:caller method Limit(x: int, lo: int, hi: int)
// selyth:caller   requires lo <= hi
// selyth:caller {
// selyth:caller   var limited := Clamp(x, lo, hi);
// selyth:caller }

/** Split a total between `parts` people: [share each, remainder]. */
export function share(total: number, parts: number): [number, number] {
  // selyth:obligation LED-13 LED-14 LED-15
  if (parts === 0) return [0, total];
  return [Math.floor(total / parts), total % parts];
}
// selyth:caller method SplitBill(total: nat, people: nat)
// selyth:caller   requires people > 0
// selyth:caller {
// selyth:caller   var share, remainder := Share(total, people);
// selyth:caller }

/**
 * Add a fixed step once per period.
 *
 * `observe` is a test hook, called at the head of every iteration and once more when the loop
 * exits, with the loop counter and the running total: the points where a loop invariant must
 * hold. It does not change the result and the Dafny model has no counterpart for it.
 */
export function accrue(balance: number, step: number, periods: number,
                       observe?: (i: number, total: number) => void): number {
  // selyth:obligation LED-19
  let total = balance;
  let i = 0;
  while (true) {
    // selyth:invariant 0 <= i <= periods && total == balance + i * step
    // selyth:obligation LED-16 LED-17 LED-18
    observe?.(i, total);
    if (i >= periods) break;
    total += step;
    i++;
  }
  return total;
}

export function transfer(from: number, to: number, amount: number): [number, number] {
  // selyth:obligation LED-20
  return [from - amount, to + amount];
}
