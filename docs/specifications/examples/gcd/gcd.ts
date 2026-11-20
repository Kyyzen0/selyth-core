// Third-party code: src/gcd.ts from github.com/pallosp/congruence_solver
// (commit 150bfb575c7ad1bc31fc4db6335e3969681652ef, MIT License, (c) pallosp).
// The greatest common divisor by the Euclidean algorithm.
//
// Ported to the pure subset of docs/specifications/ts-to-dafny.md with a two-line diff,
// recorded here because the subset is deliberately conservative:
//   - `return Math.abs(a)` -> `return a`  (identical on the nat domain declared in
//     gcd.translation.json; the subset allows no Math function but Math.floor(a / b))
//   - the return type `: number` is added (the subset requires it)
// discoverLargestCollatz-style helpers of the original repo use for-loops, arrays and
// spreads, which the subset refuses; they are not part of this example.

export function gcd(a: number, b: number): number {
  while (b !== 0) {
    const t = b;
    b = a % b;
    a = t;
  }
  return a;
}
