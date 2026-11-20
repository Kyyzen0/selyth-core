# Performance of the verification gate — first measurements

**Question (E-05 / P-01 / F1 in `docs/architecture/decision-register.md`):** can the fast
tier finish a single unit in under two seconds on ordinary hardware, and the deep tier under
two minutes? F1 says a slower gate gets bypassed, invisibly. Until now the 2 s / 2 min figures
were targets, not measurements. This document records the first real ones, produced by
`make bench` (tools/bench.py) against the ledger example (20 obligations, real Dafny 4.11.0),
and draws the consequence for `cost_budget` — which the policy engine now actually enforces
(`BLOCK-COST-BUDGET`, rule 6 of `docs/specifications/policy-engine.md`).

## Method

- One full pipeline run: `tools/ts_adapter.py lower` → `tools/dafny_backend.py verify`
  (Dafny 4.11.0, bundled z3 4.12.1, `--resource-limit 1000000 --verification-time-limit 60`)
  → `tools/policy_engine.py decide` under the committed lock.
- Per obligation the report records `resource_units` (solver units — **deterministic** for a
  fixed tool build and input, per risk R7) and `wall_ms` / `cpu_ms` / `peak_rss_bytes`
  (recorded for humans, explicitly not soundness inputs).
- The bench report is valid for `docs/specifications/bench-schema.json` and is checked in CI
  (`tools/test-dafny-backend.py::check_bench`); the schema and the pure `summarise` are tested
  without a prover by `tools/test-bench.py`.
- Environment of the run: CI runner `ubuntu-24.04`, x64, Dafny 4.11.0 from the official
  release archive, z3 4.12.1 pinned by the backend.

## Results

<!-- Filled from the CI run of `make bench` (job test-dafny). resource_units are stable
     across runs of the same build; wall_ms varies run to run and is reported as measured. -->

| Obligation | outcome | resource_units | wall_ms | cpu_ms | peak_rss_bytes |
|---|---|---|---|---|---|
| *(pending first CI run)* | | | | | |

Totals: *(pending first CI run)*.

## Reading

*(pending first CI run)* — answered against the 2 s fast / 2 min deep targets: per-obligation
wall_ms, the slowest obligation, the total for the 20-obligation module, and what that implies
for a `cost_budget` on a real policy.

## Consequence for `cost_budget`

*(pending first CI run)* — a recommended `deep_wall_ms` / `max_resource_units` /
`max_peak_rss_bytes` grounded in these measurements rather than in the round targets, with
`fast_wall_ms` kept as the (non-blocking) fast-tier target per rule 6.

## Limits

- One module, one machine, one tool build. Dafny's solver is seed-dependent (R7: one measured
  project saw a 30% failure rate over 100 runs), so `wall_ms` is not reproducible; only
  `resource_units` is claimed deterministic, and only for this build.
- The backend verifies one obligation at a time and re-verifies the whole workspace file per
  obligation (see `tools/test-ledger.py`), so the cost scales as obligations × file size. That
  is a property of this backend, not of the gate's design.
