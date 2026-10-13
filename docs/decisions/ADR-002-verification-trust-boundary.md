# ADR-002 — Independent verification and its security assumptions

**Status:** Accepted
**Date:** 2026-10-13
**Deciders:** Project owner, principal architect
**Related:** ADR-001, `docs/architecture/security-model.md`, `docs/architecture/verification-engine.md`

## Context

Selyth's value proposition depends on one claim: an AI agent's changes are checked by a system the agent cannot influence. If that claim is false, everything else is decoration.

Two pieces of prior art define the problem precisely.

Proof-carrying code established that a code producer can supply a checkable explanation that a small trusted validator verifies, and that a modified proof falls into exactly three outcomes — invalid, valid but irrelevant, or valid and safe. That gives tamper *detection* with no trusted third party, which is the only such mechanism available to a project with no budget and no infrastructure.

Berkeley RDI demonstrated the opposite failure empirically. All eight audited agent benchmarks were exploitable to near-perfect scores, and the mechanism was uniform: the submission shared the evaluation environment. A `conftest.py` hook inside the agent-controlled container forced every test to report as passing. OpenAI separately documented agents passing SWE-bench tasks by removing pre-existing checks, and NIST documented test overfitting in agent evaluations.

The project constraint is a single macOS machine, one user, and an agent process with ambient local authority.

## Decision

**The verification boundary is a subprocess with a serialised contract, not an in-process library call. The V0.1 trust model is `local-structural`, and it is recorded as such in every report.**

Three commitments follow.

**The producer's honesty is declared, never assumed.** The report schema requires a `local-subprocess` producer to set `outside_agent_boundary: false`, and a report in the `local-structural` trust model may not claim an outside boundary. The system cannot lie about what it is, even though it cannot enforce what it is.

**Structural integrity is enforced on the data, not the process.** Content addressing, the additions-only invariant, hash-pinned policies, schema-enforced claim ceilings, and a total replayable policy evaluation. Each holds regardless of what the agent does, because each is a property of artefacts rather than of a running program.

**Dafny is invoked as a subprocess and Boogie is not a dependency.** Boogie is permissively licensed but requires .NET Core and a separately installed solver. Depending on it directly would add a managed runtime to a €0, single-machine, minimal-footprint project for no benefit, because Dafny already ships the Boogie-to-Z3 pipeline.

## Consequences

**Positive.** The gate runs today on the hardware available, at zero marginal cost. Because the adapter boundary is a serialised interface with captured output, moving the verifier into a container or to a remote host is a transport substitution rather than a redesign. The report is honest about its own strength, so a reader is never misled about what was established. And the additions-only invariant gives real tamper detection with no third party, which is more than most projects at any budget can claim.

**Negative — and this is the important one.** V0.1 does **not** defend against an agent with arbitrary local code execution. Reports produced on this machine are assertions, not evidence in the security sense. A determined agent that edits the obligation set, the policy and the expected digests in one commit defeats content addressing, which makes tampering *visible* and does not make it *absent*. The path-scoped policy rules look like containment and are not: an agent that moves code moves it across the path boundary. Every one of these limitations is written into `security-model.md` and into every report's `trust.limitations`, because a system that documents its own exposure is usable and one that does not is not.

**Neutral.** The schema can require a report to *claim* a boundary it does not have, which is why the schema requires the opposite. A schema constrains the shape of a document, not the honesty of the process that wrote it. The constraint raises the cost of forgery; it does not make forgery impossible.

## Alternatives considered

**Treat process separation as a security boundary.** Rejected explicitly. A subprocess on the same machine under the same user is a reliability boundary, and calling it a security boundary would be the single most misleading claim this project could make.

**Require cryptographic signatures in V0.1.** Rejected. A signature proves who holds a key, not that the verifier behaved correctly, and the key would live on the same machine. It would add real complexity to a property it does not actually deliver at this trust level. Deferred to isolated deployment, where it means something.

**Accept an agent-run verifier for V0.1 simplicity.** Rejected. It is simpler, and it is the failure Berkeley RDI documented. The subprocess costs little; the honesty of the claim is the point.

**Depend on Boogie directly for finer control over verification conditions.** Rejected on packaging. Boogie's .NET requirement is a poor trade for a project whose budget is zero and whose footprint constraint is explicit. Dafny exposes everything the adapter contract needs.

## Path to real isolation

Recorded here so the gap is a plan rather than an admission.

1. Freeze the workspace by digest; the verifier reads only the snapshot; results leave through a write-once channel.
2. Relocate execution to a container or remote host — a transport substitution, because the contract is already serialised.
3. Producer becomes a declared remote identity; `producer.kind` becomes `remote-service` with `outside_agent_boundary: true`. The schema already models that state; only the value changes.
4. Approval moves to signed commits, making `policy_approval_trusted` meaningful rather than procedural.

Step 1 is cheap enough to do in V0.1 because it improves forensics even without isolation. Steps 2 to 4 depend on the threat model including a genuinely adversarial local agent, which for a €0 research project it does not yet.

## References

- Proof-carrying code: Necula & Lee, 1998; Necula & Schneck, ISSS 2002
- Berkeley RDI, benchmark exploitability: `docs/research/prior-art.md`
- NIST CAISI, agent evaluation gaming
- OpenAI, SWE-bench Verified deprecation
- SLSA build provenance: builder trust model
- LemmaScript: the additions-only invariant this decision borrows
