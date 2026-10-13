# ADR-003 — Representation of informal and formal intention

**Status:** Accepted
**Date:** 2026-10-13
**Deciders:** Project owner, principal architect
**Related:** ADR-001, `docs/architecture/intent-and-ir.md`, `docs/architecture/core-components.md`

## Context

Selyth's manifesto makes intent a first-class concept, and Phase C identified this as the only place the project can still differentiate. Every verifier in existence — Dafny, Verus, F*, Lean, refinement-type systems — takes a specification the human has already written and checks the code against it. None of them check that the specification is what the human meant.

The intent-formalization literature names the distance between the two the *intent gap* and describes a spectrum: lightweight tests that disambiguate likely misinterpretations, through full functional specifications in verification-aware languages, to domain-specific languages from which correct code is synthesised. Claimcheck attacks it directly — "Dafny verifies proofs, claimcheck confirms intent" — by blinded round-trip informalisation, and reports 96.3% accuracy in two-pass mode and 86.1% single-prompt.

The same benchmark that produced that number also reported something that cuts against the whole direction: adding natural-language descriptions does not significantly improve vericoding success. That finding is about *proofs*, not about *ambiguity detection*, and the two are different measurements. But it is the strongest available evidence against the feature, and any design that ignores it is not being honest about its odds.

## Decision

**Intent is a separately-graded object. It is carried in the obligation set, and it never enters the proof chain.**

Four commitments.

**An Intent Document and an Obligation Set are different artefacts.** The first is informal, natural language, and may be incomplete or ambiguous. The second is formal, machine-checkable, and complete. A formal obligation is not a more precise piece of intent; it is a different kind of artefact, and the gap between them is the unsolved problem rather than an implementation detail.

**Intent corroboration is a distinct evidence kind, permanently probabilistic, and never sufficient on its own.** It sits near the bottom of the evidence lattice, beneath property-based testing. A policy rule may *require* intent corroboration as an additional condition; it may never be *satisfied* by it alone. The schema enforces this structurally.

**Ambiguities are recorded and escalated, never resolved by the system.** Three detection mechanisms feed ambiguity records: structural inspection, which is free and deterministic; differential detection, which generates candidate inputs on which two plausible implementations would disagree and asks the human to classify; and semantic corroboration, which is probabilistic. A system that resolves its own ambiguities has stopped asking the right question.

**Unresolved assumptions are first-class records, not comments.** Premises the human has not verified — a third-party library behaving as documented, a float comparison being acceptable — are written down. An unstated assumption is how a proof becomes false later, and the failure is invisible because the proof was genuinely correct when written.

## Consequences

**Positive.** The system is honest about the one thing it cannot do, which makes everything else it claims credible. Intent data is useful now — ambiguity detection does not need to be sound to be useful — while remaining structurally incapable of producing a "verified" label. And the intent artefacts are exactly what a future Selyth Language frontend will need, so the investment carries forward to Stage 3.

**Negative.** A meaningful part of the differentiation strategy rests on an assumption the available evidence does not support. E-01 in the decision register is the experiment that tests it, the prior is that it fails, and the stopping condition is that the corroboration engine is cut if it does. Intent *records* survive that cut; the engine does not. The cost of being wrong is a few weeks of building a feature that gets removed, which is the cheapest way to be wrong here.

**Neutral.** The probabilistic nature of the best available tool means a human can still write a policy that treats intent corroboration as sufficient. The design cannot prevent that; it can only make the mistake visible in the schema's own field description, and the decision register states the risk.

## Alternatives considered

**Make intent part of the obligation's soundness.** Rejected. This is the failure the project exists to prevent: a 96.3% probability becoming an unearned "verified" label, which decays into a comment within months because people trust what the tool says.

**Drop intent entirely and focus on verification of formal obligations.** Rejected for now. It is the same bet as building a language — verification against a spec the human wrote — and it gives up the only defensible differentiation. If E-01 fails, this becomes the correct position, which is why it is the fallback rather than the starting point.

**Use formal intent only: require every intent statement to be formalised before it counts.** Rejected as a V0.1 requirement, because it collapses "the human defines" into "the human can already write contracts", which is Dafny plus AutoProof, eighteen years old. It is a reasonable long-term discipline and an unreasonable V0.1 gate.

**Make ambiguity detection LLM-based only.** Rejected. The differential mechanism targets exactly the points where intent is load-bearing, and its cost is proportional to the ambiguity rather than to the program size. Using the cheaper, better-targeted mechanism first also reduces how much load the probabilistic one has to carry.

## Open questions

- **Q1 — Does the differential generator need to see implementations?** Comparing two candidates is cheaper and biases toward the candidates supplied; comparing against the obligation directly does not. Unresolved.
- **Q2 — Can obligations be extended without a parser?** A constrained DSL means changing the grammar invalidates every stored digest. A permissive schema-validated format may age better. Unresolved, tracked as P-03.
- **Q3 — Can the ambiguity set be bounded?** Currently open-ended, which means a human could be asked to classify indefinitely. A bound is necessary; its location is unresolved, tracked as P-06.
- **Q4 — Is corroboration worth its cost at all?** Unresolved, and the prior is no. Tracked as E-02.

## References

- Intent formalization and the intent gap: `docs/research/prior-art.md`
- Vericoding benchmark, on natural-language descriptions: arXiv:2509.22908
- Claimcheck: `docs/research/prior-art.md`
- TiCoder differential disambiguation, via the intent formalization literature
