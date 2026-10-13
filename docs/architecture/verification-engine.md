# Verification Engine, Backend Adapters, and Evidence Semantics

**Status:** Design phase.

## What counts as evidence

The engine's only job is to run backends and record honestly what came back. The central discipline is that **evidence strength, verification outcome, and provenance are three separate axes** and are never collapsed into a single status. Phase C identified collapsing them as the most common error in this design space, and the report schema encodes the separation structurally rather than by convention.

Evidence strength — the normative lattice, weakest first:

| Evidence kind | What produced it | Ceiling | Sufficient alone? |
|---|---|---|---|
| `human-asserted` | A named human waived it | none | Only with an explicit, recorded waiver |
| `llm-corroborated` | Blinded round-trip informalisation of intent | probabilistic | **Never** |
| `runtime-asserted` | Checked on observed inputs during execution | empirical | Only if the policy asks for it |
| `property-tested` | Generated cases with shrinking | empirical | Only if the policy asks for it |
| `machine-checked-model` | Discharged against a model with a declared gap | sound-for-model | Yes, if the gap is stated |
| `kernel-proven` | Dafny/Boogie/Z3 discharged the obligation with no bypass construct | sound-for-fragment | Yes |

Verification outcome is orthogonal: `proven`, `failed`, `timeout`, `not-run`, `unknown`. Provenance is orthogonal again: which tool, which version, which scope, which assumptions.

The report schema encodes the consequences. An obligation with outcome `proven` can only carry a machine-checked evidence kind. A `timeout` may carry no soundness claim at all, because a check that did not reach a verdict produced no conclusion. A `local-subprocess` producer may not claim it was outside the agent boundary. These are not style preferences; they are the difference between a report and a decoration.

## Backend adapter contract

An adapter is admitted only if it can state its own limits. The contract:

```
declare:
  evidence_ceiling:      the strongest evidence kind it may emit
  obligation_kinds:      which obligation kinds it can handle
  guarantee_scope:       the fragment or model its guarantee holds over
  requires_semantic_gap: whether a gap must be declared, and how
  identity:              name + version, recorded in every report
```

Two rules make this contract load-bearing rather than decorative.

The ceiling is **not negotiable per call**. An adapter that produces `kernel-proven` for some inputs and `property-tested` for others declares `kernel-proven` and downgrades itself, so that the report records the weaker guarantee. A backend that can occasionally produce a genuine proof may do so — but it does so through a second adapter, so that the guarantee a reader sees is never conditional on a condition they have to discover.

The identity is **recorded unconditionally**. Every report carries the toolchain that produced it. A result whose provenance cannot be reconstructed is not evidence, and a reader who cannot tell whether Z3 or a hand-written stub produced a result cannot weigh it.

## Primary backend: Dafny

Dafny is the primary backend and the only one V0.1 ships. The reasons are specific rather than general.

It reports the highest measured LLM success rate of any verified language: 82.2% on the largest vericoding benchmark, against 44.2% for Verus and 26.8% for Lean, over 12,504 specifications and 55,397 recorded experiments. Its pure-verification rate improved from 68% to 96% in roughly a year. It is MIT-licensed, self-contained in a single binary, and already trusted industrially — Amazon's Automated Reasoning group maintains it and has used it to verify AWS authorisation logic, and Intel has used it for hardware cryptographic libraries.

It is also, by Phase C's finding, the right *substrate* precisely because Selyth is not building a language yet. Adopting the strongest existing verifier means the verification result is not the bottleneck, so the experiment actually tests the layer Selyth is proposing.

**Invocation is by subprocess, not by library.** Dafny is driven as a command-line tool. This is a packaging decision with three consequences, all intended: the €0 budget needs no managed runtime, since Boogie's .NET requirement is avoided entirely; the adapter boundary is a serialised contract, so moving the verifier to a container or a remote host later is a transport change rather than a rewrite; and the process boundary is where a real isolation boundary would eventually be inserted.

**What Dafny cannot do, stated plainly.** It verifies what the human wrote, not what the human meant. It is not deterministic across runs — one measured project saw a 30% failure rate over 100 runs — so no result is a soundness input. It times out on real code: across 782 DafnyBench programs, only 230 verified, and 66 failed on a 20-second limit. And its guarantees hold for the fragment, not for the production artifact: if the model of the code differs from the code, the proof is about the model. That last point is why `machine-checked-model` is a distinct rung rather than a footnote.

## Secondary backends, and why they are cheap

Three cheap checks run in the fast tier and require no solver:

- **Schema validation** — the report and policy validate against their schemas. Structural integrity of the evidence chain itself.
- **Type checking** — delegated to the target language's own checker (TypeScript's `tsc`, for V0.1). Emits `runtime-asserted` at best; a type checker is not a prover and must not be allowed to claim otherwise.
- **Policy integrity** — recompute the digests, verify the additions-only invariant on proof files, confirm the policy digest matches what the report claims.

These are not a fallback when Dafny is unavailable. They are a distinct tier, because most obligations in most changes are structural rather than algebraic, and a gate that pays solver cost for a schema check will eventually be turned off.

## Two verification tiers

| | Fast | Deep |
|---|---|---|
| Trigger | Every save, every edit | On request to accept a change |
| Target | Under 2 s | Under 2 min |
| Contains | Schema, types, policy integrity, cached evidence consistency | Formal proofs, property-based tests, full evidence regeneration |
| On timeout | Record `timeout`, escalate | Reject if the obligation is required, else escalate |
| Failure | Blocks the edit | Decides acceptance |

These are **experimental objectives, not guarantees**, and the report records them as such. The reason the fast tier exists is not performance aesthetics: a gate that does not return inside human attention span gets bypassed, and the bypass does not appear in the report. The tier split is the mechanism that keeps the gate inside the loop.

## Caching

Cached evidence is keyed on the conjunction of the obligation digest, the backend identity and version, the adapter configuration, and the resource limits. A change to any of them invalidates the entry.

Caching soundness rests on a property that must be stated rather than assumed: **L2 is derived and regenerating it from the same L1 digest must produce a byte-identical artifact.** If an adapter is non-deterministic, its cache is unsound in a way that looks exactly like a stale cache. The first test written for any adapter is therefore a determinism test, not a correctness test.

Cache hits are recorded as evidence with a `cache_hit: true` marker and the original cost. A cached result that is presented as a fresh run is a provenance lie, and the report distinguishes them.

## Cost measurement

Wall-clock time is recorded for humans and is explicitly **not** a soundness input or a reproducibility basis. The deterministic measure is the solver's resource count: for a fixed Z3 build, input and platform, the resource count is reproducible where elapsed time is not. Dafny's own tooling exposes this and recommends watching its coefficient of variation, treating a value above roughly 20% as a signal that a result is fragile.

This is the practical form of a claim that is otherwise easy to make and hard to keep: Selyth will not claim that verification is deterministic. It will claim that the *policy evaluation over a fixed report* is deterministic, which is a total function over data and therefore trivially so. The distinction is recorded in `trust.limitations` on every report.

Every deep run records resource units, wall time, CPU time and peak memory. The cost budget in the policy is expressed in those units, so that a slow machine and a fast one reach the same decision.

## Failure behaviour

Fail closed, with one documented exception.

A missing obligation, a malformed report, a schema violation, a timeout on a required obligation, or a policy digest mismatch all block. These are the conditions under which a gate is most likely to be quietly disabled by someone in a hurry, so they are the ones made structural.

The exception is a human waiver, which is a first-class record naming the person and the obligation, visible in the report, and counted. A system with no escape hatch gets bypassed; a system with a recorded, named, visible escape hatch does not.
