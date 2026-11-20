# The gcd: third-party code through the whole gate

Roadmap item 1 of `docs/roadmap/next-tasks-for-claude.md`: the ledger was **written for this
project**; this example runs the full pipeline (translate → verify → decide, under a lock) on
code **from a third party**, to show the gate is not tuned to its own example.

**Source.** `src/gcd.ts` from [`pallosp/congruence_solver`](https://github.com/pallosp/congruence_solver),
commit `150bfb575c7ad1bc31fc4db6335e3969681652ef`, **MIT License**. The Euclidean gcd:

```ts
export function gcd(a: number, b: number): number {
  while (b !== 0) {
    const t = b;
    b = a % b;
    a = t;
  }
  return a;
}
```

**Port to the pure subset** (`docs/specifications/ts-to-dafny.md`), one line, recorded in
the header of `gcd.ts`: the return type `: number` is added (the subset requires it).
`return Math.abs(a)` is translated as-is — `Math.abs` is in the subset (an `Abs` helper on
`int`, exact on the `nat` domain the sidecar declares), so the third-party line survives
unchanged. Parameter reassignment is translated by shadowing (see "Translator finding" below).
The subset still refuses the rest of the original repo (arrays, spreads, `Math.min`), which is
why only this function is here. That refusal is a finding, not an accident: the translator's
sign inference is conservative by design and can refuse correct code.

| File | Role |
|---|---|
| `gcd.ts` | The third-party TypeScript, ported (see header) |
| `gcd.obligations.json` | The Obligation Set: 1 precondition, 1 postcondition, 1 termination, 1 loop invariant, 2 acceptance criteria |
| `gcd.workspace.dfy` | The Dafny model, **generated** from `gcd.ts` (`make gcd-workspace`), with the markers |
| `gcd.policy.json`, `gcd.lock.json` | The acceptance policy (one rule per evidence tier) and the human-approved digests |
| `gcd.properties.json` | The property sidecar: function, input domain (1..40), 200 samples, seed |

```
python tools/ts_to_dafny.py gcd.ts --spec gcd.translation.json -o gcd.workspace.dfy   # regenerate
python tools/gate.py run --obligation-set gcd.obligations.json --workspace gcd.workspace.dfy \
    --policy gcd.policy.json --lock gcd.lock.json \
    --source gcd.ts --spec gcd.properties.json    # ONE report: Dafny proofs + property tests
```

`tools/test-gcd.py` (run by `make test-dafny`, needs Dafny 4.11.0 and Node ≥ 22.6) checks that the
committed workspace is exactly the translation of `gcd.ts`, that one `gate.py` run merges both
backends into a single report — the four contract obligations `kernel-proven` by Dafny, the two
criteria `passed` by Node — accepted under the lock and replayed, and that a bug injected for
each obligation (workspace bug for the clauses, source bug for the criteria) is rejected naming
it. The loop invariant GCD-04 is a standalone obligation: the backend verifies a loop clause
under the whole contract of the enclosing method (see [`../dafny-backend.md`](../dafny-backend.md)).
`make bench EXAMPLE=gcd` measures the merged gate's cost (see
[`../gate.md`](../gate.md) and `docs/research/performance.md`). `tools/test-properties.py` (run by
`make test-properties`) checks the property backend standalone on the criteria view of the same
set (see [`../property-backend.md`](../property-backend.md)).
