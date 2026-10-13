# Selyth

**The human defines. The agent proposes. A verifier the agent cannot influence decides.**

Selyth is an experimental open-source project on trustworthy AI-assisted software development. Its long-term ambition is a universal programming language designed jointly for human developers and AI agents. Its first deliverable is not that language — it is the verification foundation such a language would need, built now, against an existing prover, on an ordinary machine, at zero marginal cost.

**Status:** Design phase. No implementation exists. Every architectural choice is recorded with an explicit status in [`docs/architecture/decision-register.md`](docs/architecture/decision-register.md), so that no open question silently becomes a decision.

> **Next implementation work:** read [`docs/audit-phase-a.md`](docs/audit-phase-a.md) and the ordered work items in [`docs/roadmap/next-tasks-for-claude.md`](docs/roadmap/next-tasks-for-claude.md). They address the data and policy contracts required before the TypeScript adapter begins.

## The four stages

| Stage | Deliverable | Depends on the language? |
|---|---|---|
| **1 — Selyth Core** | Verification framework: obligations, evidence reports, acceptance policy, one backend adapter | No |
| **2 — Selyth Intent** | Structured intention and specification system, with ambiguity detection | No |
| **3 — Selyth Language** | An original language with its own syntax, type system, semantics and compiler | Yes |
| **4 — Selyth Universal** | Cross-platform targets, libraries, tooling, workflows | Yes |

The independence of Stage 1 from Stage 3 is structural, not aspirational: **Core contains no execution intermediate representation and no type system of its own.** Core reasons about obligations and evidence. It never needs to know what a program does, only what was claimed about it and what was shown. The language arrives later as one more producer of obligations, and nothing in Core changes when it does.

## What the verification foundation does

A human writes down obligations — preconditions, postconditions, invariants, termination conditions, permissions, acceptance criteria — and commits them. An agent proposes an implementation. A backend checks the obligations. The result is a machine-readable report that records, per obligation, what was checked, by which tool version, over what scope, at what cost, and under what assumptions. A deterministic policy engine — a total function from (policy, report) to a decision — turns that report into `accepted`, `rejected`, or `needs-human`.

Three properties do the work:

**Reports cannot overclaim their evidence.** The JSON Schema encodes the ceilings structurally. An obligation with outcome `proven` requires a machine-checked evidence kind, so a language model cannot produce a proof. A timed-out check may carry no soundness claim at all. A local subprocess may not claim it ran outside the agent's boundary. Twenty-one of the twenty-one negative test cases assert that overclaiming, stale-policy reuse and self-attestation all fail validation.

**Evidence strength, outcome and provenance are three separate axes.** Whether something was proven, how well it was proven, and who proved it are recorded independently. Collapsing them into a single status is the most common error in this design space and the one that turns a gate into a label.

**The agent cannot silently weaken the policy.** Policies are content-addressed and pinned by digest in every report they decide, so a change makes prior reports visibly stale. The additions-only invariant on proof artefacts detects tampering with no trusted third party. Suppression is forbidden in V0.1.

## Why the language is not being built yet

Phase C investigated this directly and the answer was not reassuring for the original plan. At least eighteen projects already claim the "AI-native language with first-class intent" position, and every one is unadopted — between zero and ninety-eight stars, almost all single-author, one archived. The two with real institutional weight, from ETH Zürich and from a team shipping into a service at roughly 150 million daily users, independently concluded that they should not build a language at all, and said so in their own design documents.

The verification substrate is also not a gap. Dafny reaches 82.2% LLM success on the largest vericoding benchmark and improved from 68% to 96% in about a year. A new language begins at zero, with roughly 779 GitHub results against more than five million for Java or Python.

The reasoning is in [`docs/research/prior-art.md`](docs/research/prior-art.md), and the decision in [`docs/decisions/ADR-001-core-first.md`](docs/decisions/ADR-001-core-first.md).

## What this project does not do

It does not claim to solve the intent gap. No tool in existence checks that a formal specification matches what a human actually meant, and Selyth does not close that gap in V0.1 — it records the distance honestly and escalates the cases where it matters.

It does not claim security on a single machine. [`docs/architecture/security-model.md`](docs/architecture/security-model.md) states the limitation precisely: V0.1 provides **structural integrity, not isolation**. A local subprocess is a reliability boundary, not a security boundary, and a report produced on the same machine by the same user is an assertion. The report schema forces that admission rather than hiding it.

It does not claim deterministic verification. The solver is seed-dependent; one measured project saw a 30% failure rate over 100 runs. What is deterministic is the policy evaluation over a fixed report, which is a total function over data.

It does not claim repository-scale verification. The strongest frontier agent fully solves 27 of 43 multi-module instances on the most demanding public benchmark, with 10 unsolved by any configuration.

## Repository layout

```
MANIFESTO.md                          Founding principles, revised
README.md                             This file
docs/
  architecture/
    overview.md                       Ecosystem architecture, Core → Language
    core-components.md                Component responsibilities and boundaries
    intent-and-ir.md                  Intent representation, layers, IR boundary
    verification-engine.md            Backends, adapter contract, evidence semantics
    verification-model.md             Claim taxonomy, policy semantics, trust boundary
    security-model.md                 Threat model, trust boundaries, isolation path
    language-vision.md                Long-term language design options
    architecture-options.md           New language vs extension vs framework
    decision-register.md              DECIDED / PROPOSED / EXPERIMENTAL / DEFERRED / REJECTED
  decisions/
    ADR-001-core-first.md             Build the framework before the language
    ADR-002-verification-trust-boundary.md
    ADR-003-intent-representation.md
  specifications/
    report-schema.json                Verification report schema
    policy-schema.json                Acceptance policy schema
  roadmap/
    core-v0.1.md                      Minimal prototype scope and milestones
    language-evolution.md             Core to the universal language
    validation-plan.md                Evaluation protocol (Phase C)
  research/
    prior-art.md                      Comparative analysis of prior art
    technical-risks.md                Prioritised risk register
  rapport-final.md                    Phase C report, in French
tools/
  minischema.py                       Scoped JSON Schema validator
  test-schemas.py                     Schema fixtures: 23 cases, 21 must fail
```

## Running the schema tests

```
python3 tools/test-schemas.py
```

Twenty-three cases: two valid documents that must pass, and twenty-one that must fail, covering every anti-overclaim rule, every decision-consistency rule, and every policy-integrity constraint. The validator covers only the JSON Schema keywords these two schemas use, and the test asserts that it uses no keyword the validator does not implement — so a schema cannot quietly rely on a rule that is never checked.

The first implementation task in `docs/roadmap/core-v0.1.md` is to replay these same fixtures under a production validator.

## Requirements

Nothing to run today. The design targets a €0 marginal-cost stack: MIT-licensed Dafny, Boogie and Z3, invoked as a subprocess; one ordinary machine; no paid API; no cloud infrastructure; no model required for execution, verification or acceptance. Models propose. They are never part of the decision function.

## Licence and contributing

Not yet chosen; see the decision register. The architecture assumes permissive licensing throughout and no dependency on a proprietary or paid component, because a €0, single-maintainer project cannot absorb one. Licence verification for every dependency is a blocking pre-implementation task and has not been completed for all of them.

Community contributions are anticipated but not yet structured. The most likely useful contributions are backend adapters, because the adapter contract is designed to make them additive — but that contract has not yet survived contact with a second implementation, which is exactly why V0.1 ships one.
