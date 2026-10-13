# Validation layers

A Selyth document is validated in two layers with different owners. Keeping them apart stops two opposite mistakes: expecting JSON Schema to enforce a rule it cannot express, and assuming a rule is enforced because it is written in prose.

## Layer 1 — structural validation (JSON Schema)

**Owner:** the schemas in this directory, checked under Draft 2020-12 by `tools/validate.py` (`make test`).

**Scope:** one document, against its own schema. JSON Schema can state:

- types, required fields, enumerations, patterns, lengths and counts measured against **constants**;
- conditional rules (`if`/`then`) whose condition and consequence compare fields to constants, including across sibling objects of the same document. `decision.result = accepted` implying `trust.policy_approval_trusted = true` is such a rule.

JSON Schema **cannot** state:

- equality or ordering between two values of the instance (`policy.policy_digest` against `decision.policy_digest`);
- a count or sum measured against another part of the instance or against another document (`rules_evaluated` against the number of rules in the policy);
- uniqueness by a field (`obligation_id`), or that one list covers another;
- anything involving a second document, a recomputed digest, or a ranking.

## Layer 2 — semantic validation (policy engine)

**Owner:** the deterministic policy engine (coverage of an Obligation Set by a report is already checked by `tools/obligation_set.py`, see [`obligation-set-canonicalization.md`](obligation-set-canonicalization.md)), `(policy, report, obligation_set) -> decision` (`docs/roadmap/next-tasks-for-claude.md`, tasks 2 and 3). Implemented by `tools/policy_engine.py` ([specification](policy-engine.md)). A document that passes layer 1 is well-formed, not consistent.

## Where each invariant lives

| Invariant | Layer | Enforced today by | Fixture |
|---|---|---|---|
| Evidence kinds cannot claim a stronger soundness than their ceiling | structural | `evidence.allOf`, `obligation_result.allOf` | legacy fixtures, `report/property-tested-claiming-fragment-soundness`, `report/model-check-claiming-fragment-soundness` |
| `accepted` carries no violation or escalation; `rejected` names a violation; `needs-human` names an escalation | structural | `decision.allOf` | legacy fixtures |
| `accepted` requires a trusted policy approval | structural | root `allOf[0]` | `report/accepted-on-untrusted-policy` |
| `accepted` requires at least one evaluated rule | structural | `decision.allOf[0]` | `report/accepted-with-zero-rules-evaluated` |
| A local subprocess is not outside the agent boundary | structural | `producer.allOf[0]` | `report/local-subprocess-claiming-isolation` |
| `trust` cannot claim a verifier outside the boundary that the producer denies | structural | root `allOf[1]` | `report/outside-boundary-claim-with-local-producer` |
| `remote-attested` requires a remote-service producer outside the boundary | structural | root `allOf[2]` | `report/remote-trust-with-local-producer`, `report/remote-trust-with-inside-boundary-producer` |
| `policy.policy_digest` equals `decision.policy_digest` | **semantic** | policy engine, `INTEGRITY-POLICY-DIGEST-MISMATCH` | `report/policy-digest-mismatch`, `tools/test-policy-engine.py` |
| Every obligation has an applicable rule, otherwise a safe default decides | **semantic** | policy engine, `DEFAULT-NO-RULE-DECIDES` (rejected) | `policy/no-rule-applies-to-obligation`, `tools/test-policy-engine.py` |
| The policy digest is the digest of the canonical policy content | **semantic** | policy engine, `INTEGRITY-POLICY-NOT-PINNED` | `tools/test-policy-engine.py` |
| `rules_evaluated` is the number of rules the engine really evaluated | **semantic** | `policy_engine.replay` (the recorded decision must equal the engine's) | `tools/test-policy-engine.py`, "Replay" |
| `decision.result` is what the policy yields for the obligations' outcomes and evidence | **semantic** | `policy_engine.replay` | same |
| The report lists every obligation of the Obligation Set exactly once, with the same id and digest, and names the set digest | **semantic** | `tools/obligation_set.py cover` (`check_coverage`) | `tools/test-obligation-set.py`, "Coverage" |
| `obligation_id` values are unique within a report, and within an Obligation Set | **semantic** | `check_coverage` (report), `parse_obligation_set` (set) | same |
| `min_evidence_kind` compares positions on the evidence lattice, not on JSON enum order | **semantic** | `policy_engine.EVIDENCE_LATTICE` | `tools/test-policy-engine.py`, "Evidence lattice" |

The last row matters because `enum` order carries no meaning in JSON Schema, so the "normative order" stated in a schema description is not something layer 1 can check.

Two things are constrained by neither layer today: `produced_at` is described as an RFC 3339 timestamp but declares no `format` (and `format` is annotation-only under Draft 2020-12), and `suppression.requires_human_approval` / `suppression.recorded_in_report` accept `false` in every mode.

## How the test suite reflects the split

- A structural scenario must be **rejected** by `tools/validate.py`, and the rejection must include the intended rule, not merely any error.
- A semantic scenario is **valid** for JSON Schema by construction. `tools/test-validation.py` asserts that, and that the policy engine names the rule that rejects it; `tools/test-policy-engine.py` runs the real rejection. If JSON Schema starts rejecting one, the run fails and says to reclassify it as structural.
- A new conditional rule added to a schema without a fixture that exercises it fails the "rule coverage" check.
