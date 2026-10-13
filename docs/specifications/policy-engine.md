# Policy engine

`(policy, report, obligation_set) -> decision`, implemented by `tools/policy_engine.py` (`decide`). This document is normative; the implementation follows it. The policy and report schemas leave several points open (the audit's "contrat de politique incomplet"); this document closes them, and every choice is the one that fails closed.

## Purity

`decide` takes three parsed JSON documents and returns a decision. It reads no file, uses no network, clock or randomness, calls no model, and does not mutate its inputs. Reading files and checking documents against their schemas happen outside it (`evaluate_files`). Equal inputs give an equal output, byte for byte once serialised with sorted keys. The report's own `decision` is a claim, not an input: only `replay` reads it, to check that it equals what the engine decides.

## The decision

The result has the shape of `decision` in `report-schema.json`. Per obligation, exactly one of three things happens: accepted, a violation, or an escalation. Then:

- any violation → `rejected` (escalations, if any, are listed too);
- else any escalation → `needs-human`;
- else `accepted`. An acceptance needs every obligation accepted by a rule, so it always has at least one evaluated rule.

Violations and escalations are ordered by `obligation_id`; each names the rule that produced it (`rule_id`, or a built-in id below) and, when relevant, the obligation.

## Step 1 — integrity (whole report)

If any check fails the result is `rejected` with one violation per problem, no obligation is judged, and `rules_evaluated` is 0. Checks:

| `rule_id` | Fails when |
|---|---|
| `INTEGRITY-POLICY-INVALID` | two rules of the policy share a `rule_id` |
| `INTEGRITY-POLICY-DIGEST-MISMATCH` | `report.policy.policy_digest` ≠ `report.decision.policy_digest` |
| `INTEGRITY-POLICY-NOT-PINNED` | `report.policy.policy_digest` ≠ the digest of the supplied policy |
| `INTEGRITY-POLICY-IDENTITY` | `policy_id`, `policy_version`, `approved_by` or `approval` differ between report and policy |
| `INTEGRITY-TRUST-INCOHERENT` | the report trusts the policy approval while the policy's `approval` is `unverified` |
| `INTEGRITY-COVERAGE` | the report does not cover the Obligation Set exactly ([coverage rules](obligation-set-canonicalization.md)) |

**Policy digest:** `sha256:` + SHA-256 of the policy as JSON with strings in Unicode NFC, object keys sorted by code point, no whitespace, minimal escapes, UTF-8. Arrays keep their order, because rule order is meaning; an integral float is the integer. Reformatting or reordering keys keeps the digest; reordering rules changes it.

## Step 2 — each obligation, in `obligation_id` order

Blocks come first and cannot be overridden by any rule. Evidence strength is the lattice in `policy_engine.EVIDENCE_LATTICE`, weakest to strongest: `human-asserted < llm-corroborated < runtime-asserted < property-tested < machine-checked-model < kernel-proven`. It is a fixed table of the engine; the position of a value in a JSON `enum` is never used (the two schemas list the kinds in opposite orders).

| Order | Condition | Result | `rule_id` |
|---|---|---|---|
| 1 | outcome `failed` | violation | `BLOCK-FAILED` |
| 2 | outcome `timeout` | `policy.on_timeout` (`reject` if absent) | `BLOCK-TIMEOUT` |
| 3 | outcome `not-run`, `unknown`, or any other value | `policy.on_unknown_evidence` | `BLOCK-NO-VERDICT` |
| 4 | outcome `proven` with evidence that is not machine-checked | violation | `BLOCK-PROVEN-WITHOUT-MACHINE-EVIDENCE` |
| 5 | evidence weaker than the Obligation Set's `required_evidence_kind` | violation | `BLOCK-EVIDENCE-BELOW-REQUIRED` |

Row 4 repeats what the report schema forbids, so the engine does not depend on its input having been schema-checked. Row 5 reads the requirement from the frozen Obligation Set, not from what the report says. An escalation chosen by rows 2 or 3 goes to `policy.escalation.requested_of`, or to `approved_by` if absent.

## Step 3 — policy rules

Rules are tried in order; the first that decides ends the obligation.

- **Selection.** `applies_to` selectors are conjunctive: `obligation_kinds` (the obligation's kind is listed), `paths` (an affected component starts with a listed prefix; advisory, see the security model), `all_obligations: true` (always). `all_obligations: false` selects nothing by itself; a rule with no selector never applies. Kinds and components come from the Obligation Set.
- **Requirements.** `min_evidence_kind` (lattice position); `requires_intent_corroboration` (the evidence kind is `llm-corroborated`: the only signal a one-evidence-per-obligation report can carry); `non_empty_assumptions` (read literally: the evidence lists at least one assumption; this reading is flagged as doubtful in the policy schema).
- **Action.** `accept` decides only when its requirements are met, and otherwise passes to the next rule. `reject` and `escalate` are guards: they decide when their requirements are **not** met, and otherwise pass. This is the reading under which the example policy makes sense (an `escalate` rule requiring intent corroboration escalates what lacks it).
- **No rule decides** → violation `DEFAULT-NO-RULE-DECIDES`. A policy that does not cover an obligation never accepts it.

## Step 4 — trust

If `report.trust.policy_approval_trusted` is false, an escalation `TRUST-POLICY-APPROVAL` to `approved_by` is added, so the result is at best `needs-human`. The schema already forbids an `accepted` report in that state; the engine never produces one.

## What this version cannot do

- **Passing empirical evidence is unrepresentable.** The report schema has no outcome meaning "passed" for `property-tested`, `runtime-asserted` or `human-asserted` evidence: `proven` is reserved for machine-checked kinds. Such evidence arrives as `unknown` and is stopped at row 3. So those kinds, and policy values `min_evidence_kind` below `machine-checked-model`, cannot lead to acceptance yet. This is a gap in the report schema to settle before the adapter, not something the engine papers over.
- `requires_intent_corroboration` can never be met together with a proof, for the same reason (one evidence entry per obligation); an `escalate` guard on it fires for every obligation it reaches.
- `cost_budget`, `suppression` and the fields a report echoes from the set (`statement`, `kind`, …) are not enforced.
