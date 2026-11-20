# Property-test backend: acceptance criteria as deterministic property tests

`tools/property_backend.py` is the **second backend**. It shares the report schema
([`report-schema.json`](report-schema.json)), the policy engine
([`policy-engine.md`](policy-engine.md)) and the lock with the Dafny backend
([`dafny-backend.md`](dafny-backend.md)) — which is precisely what makes it a validation of
the adapter contract: a second producer can emit a report the same engine decides, with no
schema or engine change. This document is normative.

```
Obligation Set (acceptance-criterion obligations) --+
TypeScript source (pure subset) --------------------+--property_backend--> report --policy_engine--> decision
property-test sidecar (domain, samples, seed) -----+
```

## What it runs, and what it may claim

Only obligations of kind `acceptance-criterion` are handled; any other kind in the set is
refused (exit 2, no report) — contract clauses belong to the Dafny backend, and the two
backends never mix in one set. A criterion's statement is `ensures <expr>`, where `<expr>`
must be inside the shared expression subset (the same one `ts_adapter.py` parses), minus
`==>` (not executable) and bare `/` (real division in TypeScript), both refused with a
located message rather than guessed.

The sidecar (e.g. `gcd.properties.json`) declares exactly four keys: `function`, `params`
(each a `{"domain": "nat", "min": m, "max": n}` with `0 <= m <= n`), `samples`
(`1..100000`), and `seed` (integer). The backend parses the source with the translator's own
parser, requires the declared function to exist with exactly the declared parameters, and
generates the sample with a fixed documented PRNG (mulberry32 over the seed), so the same
sidecar always produces the same sample, bit for bit.

The backend writes a generated Node runner that imports the source (type stripping,
`--experimental-strip-types`, Node ≥ 22.6), executes the function on every sampled input,
and checks each criterion's expression on every result, one check per obligation so each gets
its own verdict. The run's wall time, CPU time and peak RSS are recorded
(`getrusage(RUSAGE_CHILDREN)`, same as the Dafny backend).

| Runner says | Outcome | Evidence |
|---|---|---|
| every check held on every input | `passed` | `property-tested`, soundness `empirical` |
| some check failed | `failed`, with the violating inputs as diagnostics | `property-tested`, `empirical` |
| the process was killed | `timeout` | `property-tested`, `empirical`, no verdict |
| Node errored, or printed no verdict | `failed`, with Node's error lines — **no obligation is passed** | `property-tested`, `empirical` |

A sample is not a proof: the evidence kind is `property-tested` and the schema's ceilings
force soundness `empirical`, so a property test can never be reported as a proof. The
report's `scope` names the sample size, the seed, the domain and the per-obligation failure
count; its `limitations` say the sample is not a proof. The policy engine's lattice ranks
`property-tested` below `machine-checked-model`, so a policy that demands machine-checked
evidence rejects it (`BLOCK-EVIDENCE-BELOW-REQUIRED`), and a policy that accepts
`property-tested` for `acceptance-criterion` obligations accepts a passing run — under the
lock, exactly as for the Dafny backend.

## Identity and determinism

The report pins the source digest, the set digest, the canonical sidecar digest, the policy
digest and the Node version in its `report_id`, and names the producer
(`local://selyth-property-backend/0.1.0`, kind `local-subprocess`) and the toolchain
(`node` + version). Two runs with the same inputs and a pinned `--produced-at` give the same
`report_id` and the same outcomes; only the measured cost fields differ (wall/CPU are not
reproducible, as everywhere in Core). Trust is `local-structural`, identical to the Dafny
backend: single machine, no isolation claim.

## Example

[`examples/gcd/`](examples/gcd/README.md) carries `gcd.properties.obligations.json` (two
acceptance criteria: the result divides both inputs; the result is at most each input),
`gcd.properties.json` (200 samples, `a` and `b` in 1..40, seed 20261120 — inside the
contract precondition's domain), `gcd.properties.policy.json` (accept `property-tested` for
acceptance criteria, with a `cost_budget`) and the human-approved
`gcd.properties.lock.json`. `make test-properties` runs `tools/test-properties.py`, which
checks the report against the schema, the decision under the lock, replay, determinism, and
that a one-line bug in `gcd.ts` is rejected with `BLOCK-FAILED` naming each criterion.
