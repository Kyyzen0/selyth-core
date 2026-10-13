# Selyth Core — Component Responsibilities and Boundaries

**Status:** Design phase.

## Intent and Specification Model

This component owns everything a human writes down, and nothing a machine infers.

The distinction that makes the rest of the system work is between an **Intent Document** (informal, natural language, may be incomplete or ambiguous) and an **Obligation Set** (formal, machine-checkable, complete). A formal obligation is not a more precise piece of intent — it is a different kind of artifact, and Phase C established that the gap between them is the unsolved part of the problem.

An Obligation Set contains, per obligation:

| Field | Purpose |
|---|---|
| Stable identifier | Survives refactoring; referenced by reports and policies |
| Statement | The formal claim, in the backend's obligation language |
| Kind | precondition, postcondition, invariant, termination, permission, acceptance-criterion, resource-bound |
| Required evidence kind | What the policy demands, recorded so a reader sees the gap between demanded and supplied evidence without opening the policy |
| Intent reference | Optional link to the natural-language intent this formalises |
| Assumptions | Premises the obligation depends on, stated rather than implied |

Two record types sit alongside the obligations and are first-class, not comments.

**Assumption records** capture premises the human has not verified — a third-party library behaving as documented, a float comparison being acceptable, an external service being reachable. An assumption that is not written down becomes a false proof later, and the failure is invisible because the proof was genuinely correct when written.

**Ambiguity records** capture places where two defensible implementations would behave differently. The detection mechanism is the one with evidence behind it: rather than asking a model whether the intent is clear, generate candidate test inputs on which *different plausible implementations disagree*, and ask the human to classify. This targets exactly the points where the intent is load-bearing, and it is the part of TiCoder's approach that Phase C found worth reusing. Ambiguities escalate to the human; they never resolve themselves.

## Program Representation

Core deliberately does **not** have a program representation. This is the decision that makes the stage-1/stage-3 independence structural rather than aspirational, and it is argued in full in `intent-and-ir.md`.

What Core has instead is a **Verification IR**: a per-backend lowering target that carries obligations and their proof context, and nothing about how a program executes. A Dafny adapter lowers obligations into Dafny constructs. A future type-checker adapter lowers them into type-level obligations. A future Selyth Language frontend produces a Verification IR without Core knowing that the language exists.

The boundary rule is one-directional: information flows from the Obligation Set down into the IR, and results flow back up as outcomes. The IR never carries information the verification question does not need.

## Verification Engine

The engine runs backends and normalises what they say. It does not judge whether the evidence is adequate — that is the policy engine's job, and separating the two is what prevents the system from grading its own homework.

**The adapter contract.** Every backend adapter must declare, before it runs, what it is entitled to produce:

- The evidence kinds it may emit (at most one rung above its weakest real guarantee)
- The obligation kinds it can handle
- The scope within which its guarantees hold
- Its identity and version, recorded in every report it produces
- Whether it requires a semantic gap to be declared, and what that gap is

A backend that cannot state its own limits is not admitted. This is the mechanism that stops a future contributor from writing an adapter that quietly reports `kernel-proven` for something it merely type-checked.

**The evidence lattice** is normative, weakest first, and the order is fixed by the report schema rather than configured per policy:

```
human-asserted  <  llm-corroborated  <  runtime-asserted
                <  property-tested   <  machine-checked-model  <  kernel-proven
```

Two properties of this lattice matter. It is **monotone**: an obligation can be re-verified with stronger evidence, never promoted upward within a single evaluation. And `llm-corroborated` sits near the bottom on purpose — the intent gap is real, and the best available tool for probing it reports 96.3% accuracy, which is a probability, not a guarantee. A probabilistic check can be *required* as an additional condition; it can never *satisfy* one on its own.

## Evidence Report

The report is the product. It is the artifact a reviewer reads, a policy consumes, and a later change diffs against. Its schema is `docs/specifications/report-schema.json`.

The schema's conditional rules are not documentation. They make overclaiming structurally impossible, and `tools/test-schemas.py` asserts this with 17 report cases, of which 16 must fail validation:

- `outcome: proven` requires a machine-checked evidence kind, so a language model cannot produce a proof
- an LLM check must declare `probabilistic` soundness
- a kernel proof must declare `sound-for-fragment` soundness, never `sound-for-model`
- a `machine-checked-model` check must state a non-empty semantic gap
- a timed-out or skipped check may carry no soundness claim at all
- an `accepted` decision may carry no violations and no escalations
- an `accepted` decision requires `trust.policy_approval_trusted`
- a `local-subprocess` producer may not claim it was outside the agent boundary
- `trust.limitations` may not be empty, because no V0.1 run honestly established everything it checked

Two structural details are inherited deliberately from in-toto. The report binds a **subject digest**, not a branch, so a result cannot be silently reapplied to changed code. And the report does **not** contain its own hash — a file cannot contain the hash of itself. The report's digest belongs to an outer envelope, exactly as in in-toto and SLSA attestation.

## Policy Engine

A total, deterministic function from `(policy, report)` to a decision. Not a heuristic, not a scoring function, not a model call. The same inputs always produce the same decision, which is what allows a decision to be explained, diffed and replayed.

The decision is three-valued, and collapsing it would be a design error:

| Result | Meaning | Human action |
|---|---|---|
| `accepted` | Every rule satisfied, no violation, no escalation | None |
| `rejected` | A rule failed; the violated rule is named | Fix the change |
| `needs-human` | Genuine uncertainty: unresolved ambiguity, unverified assumption, evidence below the requirement but above the blocking floor | Decide deliberately |

`rejected` and `needs-human` call for different human actions. Merging them turns a gate into theatre, because "I do not know" and "this is wrong" deserve different responses.

**Policy integrity.** A policy is a separate content-addressed artifact, pinned by digest in every report it decides. A policy change is a breaking change: the digest changes, and every report produced under the old digest becomes visibly stale. That is the structural answer to "an agent must not weaken acceptance policies" — an agent that edits the policy changes its hash, and the change is visible in the next report rather than invisible in the current one.

Suppression is the practical path to policy weakening, so it is where the schema is tightest: `mode` is `forbidden` in V0.1, and the schema requires human approval and in-report recording to be configured even while suppression is off. Narrowing-only suppression is modelled but not enabled, pending the evaluation.

## Trust principals

Six principals, and the boundary between them is the security model in `security-model.md`:

1. **Human owner** — the only party that may approve a policy or waive an obligation
2. **AI agent** — proposes changes; never approves, never weakens
3. **Source workspace** — the agent's mutable territory
4. **Verification environment** — reads frozen artifacts, emits reports
5. **Policy engine** — a pure function over its two inputs
6. **Artifact storage** — holds content-addressed inputs and immutable reports

## What is deliberately absent from Core

No type system of its own. No effect system. No parser. No execution model. No agent orchestration. No multi-agent coordination. No graphical interface. No database server. No network service.

Each of these is a Stage 2, 3 or 4 concern, or a V0.2 concern, or out of scope entirely. A component that cannot be justified by a question the evaluation actually asks does not get built.
