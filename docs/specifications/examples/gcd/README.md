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

**Port to the pure subset** (`docs/specifications/ts-to-dafny.md`), two lines, both recorded in
the header of `gcd.ts`: `return Math.abs(a)` → `return a` (identical on the `nat` domain the
sidecar declares; the subset allows no `Math` function except `Math.floor(a / b)`), and the
return type `: number` is added (the subset requires it). The subset refuses the rest of the
original repo (`for`, arrays, spreads, `Math.min`), which is why only this function is here.
That refusal is a finding, not an accident: the translator's sign inference is conservative by
design and can refuse correct code.

| File | Role |
|---|---|
| `gcd.ts` | The third-party TypeScript, ported (see header) |
| `gcd.obligations.json` | The Obligation Set: 1 precondition, 1 postcondition, 1 termination, 1 loop invariant |
| `gcd.workspace.dfy` | The Dafny model, **generated** from `gcd.ts` (`make gcd-workspace`), with the markers |
| `gcd.policy.json`, `gcd.lock.json` | The acceptance policy and the human-approved digests |

```
python tools/ts_to_dafny.py gcd.ts --spec gcd.translation.json -o gcd.workspace.dfy   # regenerate
python tools/ts_adapter.py lower gcd.obligations.json --backend-version 4.11.0 > ir.json
python tools/dafny_backend.py verify --ir ir.json --workspace gcd.workspace.dfy \
    --obligation-set gcd.obligations.json --policy gcd.policy.json --lock gcd.lock.json
```

`tools/test-gcd.py` (run by `make test-dafny`, needs Dafny 4.11.0) checks that the committed
workspace is exactly the translation of `gcd.ts`, that all four obligations are `kernel-proven`
and the run is `accepted` under the lock, and that a bug injected for each obligation makes it
fail. `make bench EXAMPLE=gcd` measures its cost (see `docs/research/performance.md`).
