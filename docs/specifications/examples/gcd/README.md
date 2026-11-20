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
return type `: number` is added (the subset requires it). Parameter reassignment is translated
by shadowing (see "Translator finding" below). The subset refuses the rest of the
original repo (`for`, arrays, spreads, `Math.min`), which is why only this function is here.
That refusal is a finding, not an accident: the translator's sign inference is conservative by
design and can refuse correct code.

| File | Role |
|---|---|
| `gcd.ts` | The third-party TypeScript, ported (see header) |
| `gcd.obligations.json` | The Obligation Set: 1 precondition, 1 postcondition, 1 termination, 1 loop invariant |
| `gcd.workspace.dfy` | The Dafny model, **generated** from `gcd.ts` (`make gcd-workspace`), with the markers |
| `gcd.policy.json`, `gcd.lock.json` | The acceptance policy and the human-approved digests |
| `gcd.properties.obligations.json` | A second Obligation Set: 2 `acceptance-criterion` obligations, run by the **property-test backend** |
| `gcd.properties.json` | The property sidecar: function, input domain (1..40), 200 samples, seed |
| `gcd.properties.policy.json`, `gcd.properties.lock.json` | The property policy (accepts `property-tested` evidence for criteria) and its lock |

```
python tools/ts_to_dafny.py gcd.ts --spec gcd.translation.json -o gcd.workspace.dfy   # regenerate
python tools/ts_adapter.py lower gcd.obligations.json --backend-version 4.11.0 > ir.json
python tools/dafny_backend.py verify --ir ir.json --workspace gcd.workspace.dfy \
    --obligation-set gcd.obligations.json --policy gcd.policy.json --lock gcd.lock.json
python tools/property_backend.py test --obligation-set gcd.properties.obligations.json \
    --source gcd.ts --spec gcd.properties.json --policy gcd.properties.policy.json \
    --lock gcd.properties.lock.json        # the second backend, on the same code
```

`tools/test-gcd.py` (run by `make test-dafny`, needs Dafny 4.11.0) checks that the committed
workspace is exactly the translation of `gcd.ts`, that all four obligations are `kernel-proven`
and the run is `accepted` under the lock, and that a bug injected for each obligation makes it
fail. The loop invariant GCD-04 is a standalone obligation: the backend verifies a loop clause
under the whole contract of the enclosing method (see
[`../dafny-backend.md`](../dafny-backend.md)). `make bench EXAMPLE=gcd` measures the Dafny
gate's cost (see `docs/research/performance.md`). `tools/test-properties.py` (run by
`make test-properties`, needs Node ≥ 22.6) checks the second backend end to end on the same
`gcd.ts`: the two criteria pass on the seeded sample and are `accepted` under their lock as
`property-tested` / `empirical` evidence, a one-line bug is `rejected` naming each criterion,
and two runs give the same `report_id` (see [`../property-backend.md`](../property-backend.md)).
