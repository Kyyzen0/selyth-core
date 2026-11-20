// Third-party code: src/gcd.ts from github.com/pallosp/congruence_solver
// (commit 150bfb575c7ad1bc31fc4db6335e3969681652ef, MIT License, (c) pallosp).
// The greatest common divisor by the Euclidean algorithm.
//
// Ported to the pure subset of docs/specifications/ts-to-dafny.md with a one-line diff,
// recorded here because the subset is deliberately conservative:
//   - the return type `: number` is added (the subset requires it)
// `return Math.abs(a)` is translated as-is: Math.abs is in the subset (an Abs helper on int,
// exact on the nat domain declared in gcd.translation.json).
// discoverLargestCollatz-style helpers of the original repo use arrays and spreads,
// which the subset refuses; they are not part of this example.

export function gcd(a: number, b: number): number {
  while (b !== 0) {
    const t = b;
    b = a % b;
    a = t;
  }
  return Math.abs(a);
}
