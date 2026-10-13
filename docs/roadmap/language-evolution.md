# Language Evolution — Core to the Universal Language

**Status:** Design phase. This document is a direction, not a plan, and every stage below is gated by the one before it.

## The gating rule

**Stage 3 does not open until the Stage 1 evaluation completes.**

This is the single most important line in this document, and it exists because the most likely failure mode for this project is not technical. Building a language is more satisfying than running an experiment that might produce a null result. A vision that is preserved indefinitely without being tested becomes a way to avoid measuring.

The gate is concrete: the evaluation in `core-v0.1.md` runs, results are recorded, and the stopping conditions are applied. If the intent layer is cut, it is cut before the language begins, not after.

## Stage 1 → Stage 2 → Stage 3

### Stage 1 — Selyth Core (current)

Obligations, verification IR, evidence reports, acceptance policy, a deterministic policy engine, and a TypeScript adapter. No language dependency, by construction.

**Exit condition:** the evaluation completes and shows either a measurable reduction in false acceptance, or a documented null result.

### Stage 2 — Selyth Intent

A structured intention and specification system: a real authoring surface, ambiguity detection, assumption tracking, and the differential-ambiguity generator.

The ordering is deliberate. Intent is more valuable after the evidence and policy machinery exists, because intent without somewhere to land produces prose. And the ambiguity mechanisms are experimental — the decision register marks them EXPERIMENTAL — so they belong in a stage where an experiment is expected.

**Exit condition:** ambiguity records demonstrably reduce human review time, or the mechanisms are cut.

### Stage 3 — Selyth Language

A language with its own syntax, type system, semantics and compiler, designed for humans and agents together.

The language inherits Selyth Core without modifying it. Its job is narrow: parse Selyth source into an Obligation Set and a Verification IR, then hand both upward. It owns the language. Core owns the evidence and the decision. That boundary is what allows a Selyth Language program and a TypeScript program to be governed by the same policy — and that capability is the actual argument for the language's existence, not the language itself.

**Prerequisites before Stage 3 opens:** the evaluation is complete; the obligation-set decay failure mode has not materialised; a maintainer exists beyond one person, or the project has demonstrated it is maintainable by one.

**Exit condition:** a Selyth program can be verified under the same policy as a program in a conventional language, with no policy change.

### Stage 4 — Selyth Universal

Progressive expansion: native and cross-platform targets, a standard library, a package ecosystem, editor tooling, and developer workflows.

The realistic order is WASM first, then a native backend through Cranelift, then LLVM when there is a measured need for its optimisation. The portable core is the `PURE` execution profile, and every target declares which of floating-point semantics, threading and system-call surface it pins and which it leaves unspecified.

## What each stage inherits

| Inherited | From | Unchanged by |
|---|---|---|
| Evidence lattice and its ceilings | Stage 1 | Every later stage |
| Report schema and claim ceilings | Stage 1 | Every later stage |
| Policy model and suppression rules | Stage 1 | Every later stage |
| Additions-only invariant | Stage 1 | Every later stage |
| Trust model and its stated limitations | Stage 1 | Isolated deployment narrows the limitations; it does not rewrite the model |

A later stage that wants to change any of these is proposing a new ADR, not an implementation detail. That is the point of recording them.

## Timeline honesty

No dates. Not because planning is unhelpful, but because the honest answer depends on a measurement that has not been made. A schedule built on an unmeasured hypothesis is a schedule built to be missed, and a missed schedule on a €0 project is how a research project turns into a stalled one.

What can be stated: the Stage 1 milestones are weeks, not months, and each ends in something runnable. The uncertainty is entirely in Stage 3, where the language-design decisions — memory management in particular — are genuinely open and depend on experiments that cannot run until a language exists.

## What would make this roadmap wrong

- **If the evaluation shows no benefit from the governance layer**, Stage 2 has nothing to attach to, and the correct outcome is a documented negative result rather than a pivot to Stage 3.
- **If Dafny's ecosystem moves faster than Core can track it**, the adapter contract is the mitigation, and its failure would suggest the abstraction was drawn in the wrong place.
- **If the intent gap turns out to be closable deterministically** — which would be a significant research result and is currently unproven either way — then a meaningful part of the language's justification dissolves, because the language was justified partly by being the container for a problem that would no longer need a container.
- **If obligation sets do decay**, as failure mode F2 predicts, the whole structure is built on an artefact humans cannot maintain, and that is a social failure no schema can prevent.
