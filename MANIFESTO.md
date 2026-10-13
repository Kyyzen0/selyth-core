# Selyth — Founding Manifesto

**Selyth** is an experimental project on trustworthy AI-assisted software development.

**Version:** 0.2 — October 2026
**Status:** Experimental design, pre-prototype
**Recommendation of record:** PIVOT (see Phase C technical research)

## Principles

**1. Human authorship of intent.** Humans define objectives, constraints, and acceptance policy. Agents propose implementations. No system may accept a change that a human has not authorised at the policy level.

**2. The specification is the trust anchor.** A change is measured against obligations that a human has authored and that are frozen independently of the change. Implementation is an artefact to be checked, not the source of truth.

**3. Verification before autonomy.** An agent's change is accepted only against a machine-checked obligation set. A claim that cannot be machine-checked is recorded as such and is visibly distinct from one that can.

**4. Intent is a first-class, separately-graded object.** Every obligation carries a natural-language statement. The relationship between that statement and the formal obligation is itself an object of verification, with its own evidence kind, its own confidence ceiling, and its own failure modes. It never silently inherits the status of the formal proof.

**5. Determinism is scoped, not global.** Pure profiles promise reproducibility from content. Timings, floating-point semantics, and concurrent schedules are explicitly outside the guarantee and are named as such in the report.

**6. The verifier is outside the agent's reach.** A verification report is evidence only when the verifying process is structurally unable to be influenced by the agent whose work it judges. Self-attestation is not evidence.

**7. Monotonic acceptance.** The agent may narrow an obligation's scope but may never delete it, downgrade its required evidence, or modify a generated proof artifact. Every such weakening is a structural error, not a policy violation to be adjudicated.

**8. Model independence.** No proprietary model, paid API, or external service is required to execute, verify, or gate. Models propose; they are never part of the decision function.

**9. Verification cost is a design constraint.** A gate that does not return within human attention span will be bypassed, and the bypass will be invisible. Latency is reported in deterministic cost units, not wall-clock time.

**10. Portability is declared by exclusion.** The portable core is pure. Floating point, process semantics, and wall-clock time are permanently outside the guarantee.

**11. Economic sustainability.** The verification loop must run on a single ordinary machine, in ordinary time, at zero marginal cost. Any proposal that requires a cluster, a paid service, or unbounded agent iterations fails this principle before it fails any other.

**12. Reuse over reinvention.** Existing provers, solvers, annotation conventions, and provenance standards are used as they are. Selyth builds the layer that does not exist; it does not rebuild the layers that do.

## Commitments

Selyth prioritises correctness, honest limitations, and human control over impressive demonstrations. It will publish measurements that reduce the apparent value of its own thesis, and it will remove any component those measurements do not support.

**The human defines the obligations. The agent proposes. A verifier outside the agent's reach decides.**

---
