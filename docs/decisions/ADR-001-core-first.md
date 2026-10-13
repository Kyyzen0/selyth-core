# ADR-001 — Build the verification framework first, without abandoning the language vision

**Status:** Accepted
**Date:** 2026-10-13
**Deciders:** Project owner, principal architect
**Supersedes:** The Phase C proposal to build a programming language first
**Related:** ADR-002, ADR-003, `docs/architecture/overview.md`, `docs/roadmap/language-evolution.md`

## Context

Selyth's founding manifesto commits to a universal programming language designed for both humans and AI agents. Phase C investigated whether that language should be built now and concluded that it should not.

The evidence was uncomfortable. At least eighteen projects already claim the "AI-native language with first-class intent" position, and every one is unadopted — between zero and ninety-eight stars, almost all single-author, one archived. The two with genuine institutional weight, Djinnlang from ETH Zürich and LemmaScript from a team shipping into a service at roughly 150 million daily users, independently reached the same conclusion in their own design documents: *there is no new language; there is an existing prover, and the toolchain bridges them.*

The substrate is not a gap. Dafny reaches 82.2% LLM success on the largest vericoding benchmark, over 12,504 specifications and 55,397 recorded experiments, and its pure-verification rate improved from 68% to 96% in about a year. A new language begins at zero, with roughly 779 GitHub results for its ecosystem against more than five million for Java or Python.

Abandoning the language would also be wrong. The vision is legitimate, the four-stage progression is coherent, and the strongest available criticism of the current toolchain is that it verifies code against a specification the human has already written and never checks the specification itself. That gap is real, and it is the only place Selyth can still differentiate.

## Decision

Build Selyth Core first, in four stages, with the language preserved as Stage 3 and gated on the Stage 1 evaluation completing.

Enforce the independence invariant structurally: **Core contains no execution intermediate representation and no type system of its own.** Core reasons about obligations and evidence. It never needs to know what a program does, only what was claimed about it and what was shown. A future Selyth Language becomes one more producer of obligations and one more verification target; nothing in Core changes when it arrives.

Stage 3 does not open until the Stage 1 evaluation completes and its stopping conditions are applied.

## Consequences

**Positive.** The prototype is buildable in weeks rather than years, on the hardware and budget actually available. Verification quality is not the bottleneck, so the experiment actually tests the layer Selyth proposes. The language vision is preserved with a real gate rather than being quietly traded away. And the independence invariant, if it holds, means Stage 3 is additive: a genuine Selyth Language can be governed by the same policy as a TypeScript program, which is the strongest available argument for the language's existence.

**Negative.** The project can be stopped at Stage 1 having produced a well-documented negative result about governance layers over existing tools. That is a real cost and it is accepted. The language work is deferred by an amount nobody can currently quantify. And Core carries a permanent risk of being useful only for the one language it was first built against — mitigated by the adapter contract in ADR-002, not eliminated.

**Neutral.** The four-stage structure is a commitment, and commitments of this kind have a way of becoming permanent. The gate on Stage 3 is the mitigation, and it is written into the roadmap rather than held in intention.

## Alternatives considered

**Build the language now.** Rejected. Eighteen prior attempts produced no adoption; the best-evidenced one chose not to build a language at all; and the verification substrate is stronger than anything a solo effort would produce.

**Abandon the language permanently.** Rejected. The intent gap is a real unsolved problem, and a language is a defensible long-term container for it. The problem is timing, not direction.

**Build a specification language only (Djinnlang-style).** Rejected for now. It is the closest existing analogue to Stage 3 and it is academically credible, but it arrives before the evaluation has answered whether the governance layer earns its place. Revisit if Stage 1 succeeds.

**Build the evidence layer on an existing language's own test infrastructure instead of a formal verifier.** Rejected. The evidence that a test suite is an insufficient oracle is now strong: OpenAI deprecated SWE-bench Verified after finding at least 59.4% of an audited subset had test suites rejecting correct submissions, and Berkeley RDI found 16 distinct exploit types across 13 benchmarks. A test suite as the primary oracle reproduces a design known to be invalid.

## References

- Phase C research: `docs/research/prior-art.md`, `docs/research/technical-risks.md`, `docs/architecture/verification-model.md`
- Vericoding benchmark: arXiv:2509.22908
- Vero benchmark: arXiv:2608.13522
- Djinnlang: arXiv:2609.23954
- LemmaScript design rationale: `docs/research/prior-art.md`
