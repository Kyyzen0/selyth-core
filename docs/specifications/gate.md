# The gate: one Obligation Set, one policy, one lock, one report

`tools/gate.py` is the single entry point of a verification run. It runs the **Dafny backend** over the obligations the adapter can lower (contract clauses: precondition, postcondition, invariant, termination) and the **property-test backend** ([`property-backend.md`](property-backend.md)) over the `acceptance-criterion` obligations, merges the per-obligation entries into **one** report valid for the unchanged [`report-schema.json`](report-schema.json), and lets the policy engine decide **once** over the whole set under one lock. This document is normative.

```
Obligation Set (mixed kinds) --ts_adapter--> IR --+
proof workspace (.dfy) ---------------------------+--gate.py--> ONE report --policy_engine--> decision
TypeScript source + property sidecar -------------+        (Dafny entries + property entries)
```

## Rules

1. **One set, one policy, one lock.** All obligations of the example live in one Obligation Set; the policy has one rule per evidence tier (e.g. machine-checked for contract clauses, property-tested for acceptance criteria); the lock freezes the digests of that set and that policy. There is no per-backend policy or lock to desynchronise.
2. **One report, one decision.** The merged report lists every obligation exactly once (the gate refuses to build it otherwise), each entry carrying the `producer_tool` that really produced its evidence. The top-level producer is the gate (`local://selyth-gate/0.1.0`) with the union of the toolchain; subjects bind the workspace, the TypeScript source and the set by digest.
3. **A kind no backend handles is `not-run`**, with the adapter's reason, and the engine rejects it (`BLOCK-NO-VERDICT`): a set with `acceptance-criterion` obligations run without `--source`/`--spec` is refused outright (exit 2), because silently not-running them would be a hole.
4. **The Dafny pass keeps all its own checks** (`check_workspace`: set/IR/workspace consistency, orphan markers) and verifies only the lowerable units; the property pass keeps its own (sidecar, domain, subset expressions). The gate adds only the merge and the single decision.
5. Exit codes are the backends': 0 accepted, 1 rejected, 3 needs-human, 2 error. `make check [EXAMPLE=...]` runs the gate on an example and additionally checks the report schema, the lock and the replay; `make bench EXAMPLE=gcd` benches the merged report when the example has a property sidecar.

## Example

[`examples/gcd/`](examples/gcd/README.md) is the merged example: six obligations in `gcd.obligations.json` (GCD-01..04 contract clauses, GCD-P1/P2 acceptance criteria), one policy with two rules, one lock, one sidecar `gcd.properties.json`. One `gate.py run` proves the contracts with Dafny 4.11.0 and passes the criteria with Node on 200 seeded inputs, and the engine accepts the single report under the lock. `tools/test-gcd.py` runs it with a mutation per obligation (workspace bugs for the clauses, a source bug for the criteria).
