# Intent Representation, Specification Layers, and the IR Boundary

**Status:** Design phase.

## Why Core has no program representation

The obvious design — a single universal IR that represents programs, obligations and intent in one structure — is rejected. Three reasons, in order of weight.

First, a representation that can hold program semantics has to commit to a semantics. Committing to one makes Core language-dependent, and the stage-1/stage-3 independence invariant collapses. Committing to none makes it a document format, and then it carries no information the checker did not already have.

Second, the five concerns in this pipeline have genuinely different lifecycles. An Obligation Set is human-authored and changes rarely. A Verification IR is regenerated on every run. An Evidence Report is immutable once written. A Policy Decision is derived. Modelling them as one mutable structure would mean versioning rules for all four at the speed of the fastest, which is precisely how a verification cache silently goes stale.

Third, the evidence supports the layered shape. LemmaScript's working design keeps specifications in comments beside TypeScript and puts the proof machinery in a purpose-built prover, with a generated file that is always regeneratable and a proof file that only ever receives additions. That separation is a working artifact, not a theory.

## The five layers

| Layer | Content | Mutability | Produced by | Consumed by |
|---|---|---|---|---|
| L0 Intent Document | Natural-language goals, motivations, unresolved questions | Human-edited | Human | Humans, ambiguity detection |
| L1 Obligation Set | Formal obligations, assumptions, permissions, acceptance criteria | Human-edited, frozen by digest | Human | L2, policy engine |
| L2 Verification IR | Backend-specific lowering of L1 with proof context | Regenerated every run | Backend adapter | Verification backend |
| L3 Evidence Report | Outcomes, evidence kinds, tool identity, cost, scope | Immutable once written | Verification engine | Policy engine, humans |
| L4 Policy Decision | accepted / rejected / needs-human, with reasons | Derived | Policy engine | Humans, CI |

**L3 — Selyth Language's Execution IR — does not exist and is not planned for Core.** It arrives in Stage 3, as a separate artifact that a Selyth Language frontend produces and that Core never reads. The absence is the invariant.

## Layer boundaries, stated as rules

1. **L1 does not contain code.** An obligation refers to behaviour; it does not embed it. An obligation set that could be satisfied by editing the obligation is not a specification.
2. **L2 is derived and disposable.** Deleting it and re-running the adapter must produce a byte-identical artifact from the same L1 digest. If it does not, the adapter is non-deterministic and its results are not comparable.
3. **L2 is the only layer a backend sees.** No backend receives an Obligation Set directly, and no backend reads an Intent Document. This is what lets a future Selyth Language frontend produce L2 without any backend knowing the language exists.
4. **L3 never carries information L2 did not produce.** Every field is either derived from a backend result or is an explicit declaration about that result. There is no free-text escape hatch for a backend to describe its own adequacy.
5. **L4 is a pure function of (policy, report).** It reads no other state, opens no files of its own, and is therefore replayable and diffable.

## Intent as a separately-graded object

Phase C's sharpest finding is that every verifier in existence checks code against a specification the human has already written, and none of them check that the specification is what the human meant. That gap is the intent gap, and it is where Selyth's remaining differentiation lives.

The architectural response is to treat intent as an object with its own verification status, never folded into the proof chain.

- Every obligation may carry an `intent_statement` in natural language.
- Whether that statement is faithfully formalised is itself an obligation, with its own evidence entry.
- That entry's evidence kind is `llm-corroborated` and its soundness is `probabilistic`, permanently.
- It can be *required* by a policy rule. It can never *satisfy* one alone.

The reasoning is worth stating plainly, because it is the load-bearing claim of Principle 4 in the revised manifesto. A probability of 96.3% is not a proof. A model that is wrong 4% of the time, asked to confirm that a specification matches its prose, will eventually confirm a mismatch. Building that into the acceptance chain would reproduce exactly the failure the manifesto was written to prevent: an unearned "verified" label that decays into a comment. Kept separate, it is still valuable — it is an ambiguity detector, and ambiguity detection does not need to be sound to be useful.

## Detecting ambiguity

Three mechanisms, in increasing order of cost and decreasing order of confidence.

**Structural ambiguity** is free and deterministic: an obligation with no bound, a precondition nobody can violate, a postcondition that holds for every possible output, an invariant that mentions no state. These are detectable by inspection and are almost always authoring mistakes.

**Differential ambiguity** is the mechanism with evidence behind it. Generate candidate inputs on which two plausible implementations would produce different outputs, and ask the human to classify. This targets exactly the points where intent is load-bearing: where the specification permits more than one reading and the readings matter. It is the part of TiCoder's approach worth keeping, because the cost is proportional to the ambiguity rather than to the program size.

**Semantic corroboration** is the LLM round-trip: informalise the formal obligation without seeing the prose, then compare the back-translation against it. It catches what the first two miss, and it is probabilistic, so it produces ambiguity records for a human to weigh rather than a verdict.

All three produce **Ambiguity Records**, and all three escalate. None of them resolves anything. A system that resolves its own ambiguities is a system that has stopped asking the right question.

## Integration with the future Selyth Language

Stage 3 inherits this design without modifying it. A Selyth Language frontend's job is narrow and specific: parse Selyth source into an Obligation Set (L1) and a Verification IR (L2), then hand both upward. The frontend owns the language; Core owns the evidence and the decision.

What the frontend does **not** do is decide anything. It does not choose what counts as proof, it does not evaluate a policy, and it does not decide whether a change is acceptable. Those stay in Core, which is what allows a Selyth Language program and a TypeScript program to be governed by the same policy.

The one place the future language will legitimately push back is the **dual representation** question: humans will want readable source, agents will want a structured form with stable identifiers. The resolution belongs in Stage 3, but the constraint it must respect is already fixed here — the structured form is L2, L2 is derived, and the readable form is the source of truth for humans while L1 is the source of truth for verification. Two projections, one obligation set, no lossy round trip required to pass the gate.

## Open questions

These are tracked, not decided. Each would change the design if answered differently.

- **Q1 — Does a differential-ambiguity generator need to know the implementation?** If it only compares two candidate implementations, it needs them. If it compares the implementation against the obligation directly, it does not. The first is cheaper to build and biases toward the candidates it was given.
- **Q2 — Is L1 extensible without a parser?** If obligations are authored in a constrained DSL, adding an obligation form means changing the grammar and invalidating every stored digest. A permissive format with schema validation may age better.
- **Q3 — Can the ambiguity set be made finite?** Right now, ambiguity detection is open-ended, which means a human could be asked to classify forever. A bound is needed, and where to put it is unresolved.
- **Q4 — Is semantic corroboration worth its cost at all?** Phase C found the vericoding benchmark reports that natural-language descriptions do not significantly improve outcomes. That finding is about proofs, not about ambiguity detection, and the two are different measurements. Until Selyth measures it, the answer is unknown, and the honest default is to leave the feature off.
