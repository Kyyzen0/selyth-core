# Verification model

## Claim and evidence taxonomy

The brief's six statuses — PROVEN, TESTED, ASSERTED, UNVERIFIED, FAILED, UNKNOWN — are not mutually exclusive, and treating them as an enumeration is the most common error in this design space. They mix three orthogonal axes. *Epistemic strength* is how well we know a claim holds. *Provenance* is who said so. *Outcome* is what the check returned.

The correct model is a product, not an enum. A claim is a record over four fields:

- `subject` — the content hash of the artefact the claim is about
- `obligation` — the specific contract, invariant, or intent statement
- `evidence` — the kind of evidence produced, with a confidence ceiling
- `outcome` — the result of applying that evidence

This structure is not invented for Selyth. It is the structure supply-chain attestation already uses, where an attestation binds a subject digest to a predicate type and a producer identity [31][32]. Selyth's verification report is the same shape with a different predicate type, and reusing that shape is a correctness decision as much as an engineering one.

On the evidence axis, the following ceilings apply and should be treated as invariants of the system rather than defaults:

| Evidence kind | Method | Ceiling | Blocking? |
|---|---|---|---|
| `kernel-proven` | Dafny/Z3 discharges the obligation with no `assume`, no `sorry`, no axioms | Sound for the fragment | Yes, always |
| `machine-checked-model` | Obligation discharged against a model with a documented semantic gap to production | Sound for the model only | Yes, with the gap stated |
| `property-tested` | Executed against generated cases with shrinking | Empirical | Only if policy demands |
| `runtime-asserted` | Checked on observed inputs at execution time | Empirical, per-run | Only if policy demands |
| `llm-corroborated` | Blinded round-trip informalisation against the natural-language intent [8] | Probabilistic — 96.3% reported | **Never on its own** |
| `human-asserted` | Stated by a human, no machine check | None | Only with explicit waiver |

The rule that matters: `llm-corroborated` must never be promotable to `kernel-proven`, and must never independently satisfy a blocking obligation. This is the direct consequence of the vericoding benchmark's design, where success requires all obligations discharged with no bypass constructs and the specification left unchanged [10].

## Acceptance policy and blocking rules

A policy is a separate, content-addressed artifact. It is evaluated over a report, never over source. The evaluation function is a total function from (policy, report) to a decision, and — this is the design constraint that makes the whole thing defensible — the function is deterministic because its inputs are fixed data, not a solver run.

Blocking rules, in order of precedence:

1. Any `kernel-proven` obligation with outcome FAILED blocks.
2. Any obligation whose required evidence kind exceeds the evidence actually supplied blocks.
3. Any specification the audit channel reports as unsatisfiable blocks, because a gate that cannot detect that its own specification is contradictory will accept code that satisfies nothing [13].
4. Any non-additive modification to a generated proof artifact blocks. LemmaScript enforces exactly this: the diff between the generated file and the proof file must be additions-only, and the checker fails the build otherwise [4][7].
5. Any use of `assume`, `sorry`, or an equivalent trust-escape construct blocks [10].

## Policy integrity under adversarial modification

This is the security property the brief names, and it is not a taxonomy problem. Four distinct attacks exist and each needs a different structural defence.

The agent can edit the policy file. Defence: policies are content-addressed and referenced by hash from the acceptance report; a policy change is a breaking change requiring human signature.

The agent can add a suppression. Defence: suppressions are a separate artefact from proofs, are themselves subject to a monotonicity rule — a suppression may narrow an obligation's scope but never delete it — and appear in the report as a visible count.

The agent can downgrade a claim. Defence: the taxonomy has no path from a weaker evidence kind to a stronger one within a single evaluation. Promotion requires new evidence, produced by a verifier outside the agent's reach.

The agent can narrow the scope of a check. Defence: this is the hard one, and it is the failure NIST actually observed — o4-mini passing tasks by removing pre-existing checks or adding test-case-specific logic [16]. The structural answer is the repository-wide obligation graph, so that removing a check surfaces as an unreferenced obligation. Vero's approach generalises it: each agent must discharge obligations over a *frozen* API and specification layer, with the API signatures held constant [12][13].

One property is worth stating explicitly because it is inherited from proof-carrying code and is genuinely elegant: a modified proof either becomes invalid, becomes valid but irrelevant, or remains valid and safe. Necula and Lee's tamperproofness property [33][34] means a system does not need signatures to know whether tampering occurred — it only needs the proof to stop checking. Applying that to agent work means the additions-only invariant gives tamper detection without a trusted third party, which is exactly the budget-zero case.

## Provenance and the trust boundary

Reuse in-toto and SLSA rather than inventing provenance. A Selyth verification report is an in-toto Statement whose subject is the program content hash and whose predicate type is Selyth's claim ledger [31][32]. The properties that must be inherited: attestations bind to artifacts rather than releases; published attestations are immutable; consumers ignore unrecognised fields; and external parameters are untrusted and must be verified downstream.

Hashing alone proves nothing, and the manifesto's instinct here is correct. SLSA is explicit that the builder must be trusted, that there is no option but to trust the builder, and that the builder identity represents the transitive closure of entities trusted to have run the build faithfully [31]. The equivalent question for Selyth is not *what does the hash cover* but *who ran the verifier*. If the agent runs the verifier inside its own sandbox, the report is self-attestation and carries no evidentiary weight — Berkeley RDI demonstrated exactly this failure mode across eight major benchmarks, where a `conftest.py` hook inside the agent-controlled container forced every test to report as passing [17].

The architectural answer is a hard constraint: the verifier process must be outside the agent's filesystem, its output must be extracted through a controlled channel, and policy evaluation must read the extracted report rather than anything the agent can write. This is the same rule the field has already converged on, and it is listed as non-negotiable in both the Berkeley prescriptions and the OWASP guidance [18][19].

One further design point follows from SLSA's immutability requirement [32]: reports must be bound to content hashes, not branches. Keying verification results to a branch makes them unfalsifiable, because the subject changes underneath them.

## Execution profiles and determinism scope

Principle 3 as written promises more than any implementation can deliver, and the hedge "wherever determinism is promised" is doing all the work. Replace it with three named profiles and state each one's guarantees and non-guarantees.

The `pure` profile offers total functions, no ambient state, and reproducibility from a content hash. Execution is reproducible. Verification is not deterministic in the strict sense — Dafny is seed-dependent, and one project measured a 30% failure rate over 100 runs — but **Resource Units are deterministic where wall-clock is not** [24]. Any latency claim must therefore be expressed in Resource Units with a coefficient-of-variation ceiling, which Dafny's own tooling recommends setting below 20% [24]. Claiming deterministic timing is not merely wrong; it is measurably so.

The `io` profile adds declared effects. Determinism is scoped to *decision* determinism: given identical inputs and identical environment observations, control flow is reproducible. Floating point remains explicitly outside the guarantee. LemmaScript's disclosure is the model to copy: it maps JavaScript's `number` onto Dafny's mathematical `int`, states that IEEE 754 precision loss is therefore not modelled, and says so on the front page [6]. Naming the trust boundary is what makes the guarantee honest.

The `concurrent` profile offers declared ordering or a proof obligation that ordering does not matter. Determinism is not promised; reproducibility of a given schedule is.

The portable core should be declared as PURE only. Floating-point semantics, process and thread behaviour, and wall-clock time are never portable and must be named as permanently outside the guarantee rather than as a progressive roadmap item. The manifesto's current framing of portability as something to be progressively supported is where the principle is weakest.

---

## References


