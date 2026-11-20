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

CI run `44662318728`, job `test-dafny`, 2026-11-20 (`make bench` on the ledger example).
`resource_units` are stable across runs of the same build (the ledger totals 1 281 714 in both
the CI run and a local run); `wall_ms` varies run to run and is reported as measured.
`cpu_ms` and `peak_rss_bytes` are recorded since the backend measures the verifier subprocess
with `getrusage(RUSAGE_CHILDREN)`: `cpu_ms` is the exact per-run delta of user+system time,
`peak_rss_bytes` is `ru_maxrss` (a cumulative maximum, hence an upper bound per run).

| Obligation | outcome | resource_units | wall_ms |
|---|---|---|---|
| LED-01 | proven | 63 315 | 882 |
| LED-02 | proven | 63 499 | 883 |
| LED-03 | proven | 63 978 | 885 |
| LED-04 | proven | 63 296 | 892 |
| LED-05 | proven | 63 461 | 903 |
| LED-06 | proven | 64 195 | 897 |
| LED-07 | proven | 63 444 | 894 |
| LED-08 | proven | 64 122 | 887 |
| LED-09 | proven | 64 663 | 890 |
| LED-10 | proven | 63 248 | 889 |
| LED-11 | proven | 64 651 | 876 |
| LED-12 | proven | 63 760 | 880 |
| LED-13 | proven | 63 390 | 886 |
| LED-14 | proven | 70 663 | 898 |
| LED-15 | proven | 65 156 | 877 |
| LED-16 | proven | 63 068 | 876 |
| LED-17 | proven | 63 112 | 871 |
| LED-18 | proven | 61 635 | 884 |
| LED-19 | proven | 65 219 | 874 |
| LED-20 | proven | 63 839 | 875 |
| **TOTAL** | **accepted** | **1 281 714** | **17 699** |

CPU and memory (local run, same Dafny 4.11.0 build, sandbox machine): per obligation
`cpu_ms` 951–1 040 and `peak_rss_bytes` ≈ 109–110 MiB (the cumulative max settles after the
first obligation); totals for the module: `cpu_ms` ≈ 20 800, `peak_rss_bytes` ≈ 2.19 GiB as a
summed bound (the per-run bound is the ~110 MiB max).

Toolchain: Dafny 4.11.0, Boogie bundled-with-dafny, z3 4.12.1. Decision: `accepted` (under the
committed lock). `timed_out`: 0.

## Reading

Against the 2 s fast / 2 min deep targets (E-05/P-01): **on a unit of this size the fast tier is
met** — every obligation verified in 871–903 ms wall-clock, i.e. under half the 2 s target, and
the whole 20-obligation module took 17.7 s, far under the 2-minute deep target. The per-obligation
cost is nearly constant (~64 k resource units, ~0.88 s) because the backend re-verifies the whole
workspace file per obligation, so the module's total cost scales as obligations × file size
(already noted in `tools/test-ledger.py`). On a larger module the fast tier will not hold per
obligation — which is exactly what the tier split is for, and why `fast_wall_ms` stays a
non-blocking target in rule 6.

## Consequence for `cost_budget`

Grounded in these measurements rather than in the round targets, a realistic V0.1 policy for a
module of this size:

- `max_resource_units: 100000` per obligation — the observed maximum is 70 663, so this leaves
  ~40% headroom while still catching a runaway solver. Prefer resource units over wall-clock for
  ceilings: they are the deterministic metric (R7).
- `deep_wall_ms: 10000` per obligation — ~11× the observed maximum (903 ms), so it blocks only a
  genuinely stuck verification, not a slow machine.
- `fast_wall_ms: 2000` stays the non-blocking fast-tier target (rule 6); measured per-obligation
  wall_ms is ~0.9 s, so the target is currently met.
- `max_peak_rss_bytes: 1073741824` (1 GiB) per obligation is now usable: the backend records
  `peak_rss_bytes` from `ru_maxrss`, the observed value is ~110 MiB per obligation on the
  ledger (max, cumulative bound), so 1 GiB leaves ~10× headroom while still catching a verifier
  that balloons. A cpu ceiling (`cpu_ms`) is also recordable, but wall-clock and resource units
  remain the primary ceilings — `cpu_ms` is exact, `peak_rss_bytes` is an upper bound.

## Third-party code: the gcd example

`make bench EXAMPLE=gcd` runs the same measurement on the third-party module
(`docs/specifications/examples/gcd/`, `gcd` from `pallosp/congruence_solver`, MIT): 4
obligations on a loop with an invariant, versus 20 on the hand-written ledger.

CI run `44676492840`, job `test-dafny`, 2026-11-20 (`make bench EXAMPLE=gcd`):

| Obligation | outcome | resource_units | wall_ms |
|---|---|---|---|
| GCD-01 (precondition) | proven | 15 230 | 906 |
| GCD-02 (postcondition) | proven | 15 471 | 904 |
| GCD-03 (termination) | proven | 15 230 | 902 |
| **TOTAL** | **accepted** | **45 931** | **2 712** |

| Module | obligations | resource_units total | wall_ms total | resource_units / obligation | resource_units / TS line | lines of TS |
|---|---|---|---|---|---|---|
| ledger (hand-written) | 20 | 1 281 714 | 17 699 | ~64 086 | ~24 648 | 52 |
| gcd (third-party) | 3 | 45 931 | 2 712 | ~15 310 | ~4 593 | 10 |

CPU and memory for the gcd (local run): per obligation `cpu_ms` 891–966 and `peak_rss_bytes`
≈ 93 MiB; module totals `cpu_ms` ≈ 2 800 and `peak_rss_bytes` (max) ≈ 93 MiB.

Reading: the per-obligation cost tracks the **workspace file size**, not the obligation — each
obligation re-verifies the whole file (ledger: 120 lines of Dafny for 52 of TS; gcd: a single
loop). The third-party module costs ~15.3 k resource units per obligation against the ledger's
~64 k, i.e. ~4× cheaper per obligation, in the same ~0.9 s wall-clock band. The fast-tier question
(E-05) holds for both: every obligation of both modules verifies in under 1 s on the CI runner.

## Limits

- One module, one machine, one tool build. Dafny's solver is seed-dependent (R7: one measured
  project saw a 30% failure rate over 100 runs), so `wall_ms` is not reproducible; only
  `resource_units` is claimed deterministic, and only for this build.
- The backend verifies one obligation at a time and re-verifies the whole workspace file per
  obligation (see `tools/test-ledger.py`), so the cost scales as obligations × file size. That
  is a property of this backend, not of the gate's design.
