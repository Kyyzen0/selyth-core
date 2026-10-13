# TypeScript to Dafny translation

`tools/ts_to_dafny.py` turns a pure subset of TypeScript into the Dafny methods of a proof workspace, so that the model being verified is **derived from the code** instead of being written beside it and left to drift. `tools/test-ts-to-dafny.py` tests it; `tools/test-ledger.py` verifies the result with a real Dafny.

`ts_to_dafny.py CODE.ts --spec SPEC.json [-o OUT.dfy]` prints the workspace (exit 0), or `not translatable: <reason>` (exit 2). Same inputs, same bytes.

## What is translated

| TypeScript | Dafny |
|---|---|
| `export function f(a: number, ...): number` | `method F(a: nat\|int, ...) returns (result: nat\|int)` |
| `[number, number]` result | several named results; `return [x, y]` is `r1, r2 := x, y;` |
| `let`, `const`, `=`, `+=`, `-=`, `++` | `var`, `:=`, `x := x + e`, `x := x - e` |
| `if` / `else`, blocks, early `return` | the same, with `return;` after the result assignment |
| `while (c)` or `while (true) { if (e) break; ... }` | `while c` / `while !e` (a comparison is inverted, not wrapped) |
| `Math.floor(a / b)`, `%` | `a / b`, `a % b`, only on provably non-negative operands |
| `===`, `!==`, `&&`, `\|\|`, `!`, comparisons | `==`, `!=`, `&&`, `\|\|`, `!`, comparisons |
| a trailing optional function parameter and its calls | dropped (a test hook has no counterpart in the proof); the arguments must be side-effect-free expressions, otherwise refused |

Anything else is refused with the reason: strings, classes, calls, ternaries, `for`, `switch`, non-integer literals, `==`, a bare `/`, other `Math` functions, a second loop in a function, a name that is not declared.

## What the code cannot say

A sidecar JSON specification (`examples/ledger/ledger.translation.json`) supplies what is not in the code, and is the **only hand-written part**:

- which parameters are `nat` and the names and types of the results (these are contract and typing choices);
- the `// selyth:obligation` markers, and the loop invariant with its own marker;
- scaffolding that is not an obligation: the lemmas (`preamble`), a proof hint (`body_prefix`), and the callers that prove the preconditions (`callers`).

The specification and the code must name the same functions with the same number of results; otherwise the translation is refused.

## What it does not promise

- **Numbers are unbounded integers.** A TypeScript number is a double, exact below 2^53. The proof holds for the integers; nothing checks that callers stay in the safe range (the ledger's obligations list it as an assumption).
- **Sign.** JavaScript truncates and Dafny floors `/` and `%` toward the Euclidean quotient, so they differ on negative operands. The translator infers non-negativity from the `nat` declarations (literals, `nat` names, sums and products of such, locals that are only ever increased by such values) and refuses `/` and `%` otherwise. That inference is conservative: it can refuse correct code, never accept the mismatching one.
- **Division by zero** is not the translator's concern: Dafny must prove the divisor non-zero, or verification fails.
- **The declarations in the specification are trusted.** Declaring a parameter `nat` is a precondition the callers must meet; the caller methods in the specification prove it only for themselves.
- **Faithfulness of the hand-written parts.** Invariants and callers are not derived from the code. A wrong invariant fails verification; a wrong caller can make a precondition look proven for the wrong call sites (the backend refuses a precondition with no call site, or an ambiguous method name).
- **Expressions are checked, semantics are not proven.** The test suite compares random expressions read as TypeScript and as the emitted Dafny, and mutants of the translator are caught; there is no proof that the translator is correct.
