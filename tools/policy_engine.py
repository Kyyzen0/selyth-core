#!/usr/bin/env python3
"""Deterministic policy engine: (policy, report, obligation_set) -> decision.

The semantics are specified in docs/specifications/policy-engine.md; this module
follows that document. `decide` is a pure function of three parsed JSON documents:
it opens no file, uses no network, clock or randomness, calls no model, and does not
mutate its inputs. Everything that touches the outside world (reading files,
checking documents against their schemas) lives in `evaluate_files` and `main`.

Fail closed. Every case the policy does not cover, every outcome that is not a
verdict, and every inconsistency JSON Schema cannot see produces `rejected` or
`needs-human`, never `accepted`.

Usage:
  policy_engine.py decide        <policy.json> <report.json> <obligation-set.json>
  policy_engine.py replay        <policy.json> <report.json> <obligation-set.json>
  policy_engine.py policy-digest <policy.json>
Exit status: decide/replay 0 accepted (replay: report's decision reproduced),
1 rejected (replay: not reproduced), 3 needs-human, 2 usage or unreadable/invalid input.
"""
import hashlib
import json
import sys
from pathlib import Path

import obligation_set as oset
import validate

ROOT = Path(__file__).resolve().parent.parent
SPECS = ROOT / "docs" / "specifications"

# Evidence strength, weakest to strongest. Defined here, not read from the position of
# values in a JSON `enum`: the report schema lists kinds strongest-first and the policy
# schema weakest-first, and `enum` order has no meaning in JSON Schema anyway.
EVIDENCE_LATTICE = ("human-asserted", "llm-corroborated", "runtime-asserted",
                    "property-tested", "machine-checked-model", "kernel-proven")
RANK = {kind: i for i, kind in enumerate(EVIDENCE_LATTICE)}
MACHINE_CHECKED = ("machine-checked-model", "kernel-proven")


def _canonical_value(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("policies contain integers only")
        return int(value)
    if isinstance(value, str):
        return oset._nfc(value)
    if isinstance(value, list):
        return [_canonical_value(v) for v in value]
    if isinstance(value, dict):
        return {oset._nfc(k): _canonical_value(v) for k, v in value.items()}
    return value


def policy_digest(policy):
    """sha256 of the policy's canonical JSON: NFC strings, sorted keys, no whitespace.

    Array order is preserved (rules are ordered), and an integral float equals the
    integer. Unlike an Obligation Set, no array of a policy is normalised.
    """
    text = json.dumps(_canonical_value(policy), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def policy_problems(policy):
    """Inconsistencies of a policy that JSON Schema cannot state."""
    ids = [r["rule_id"] for r in policy["rules"]]
    return [f"rule_id {i!r} is used by more than one rule" for i in sorted(set(ids))
            if ids.count(i) > 1]


def _violation(rule_id, message, obligation_id=None):
    return {"rule_id": rule_id, "obligation_id": obligation_id, "message": message}


def _escalation(reason, requested_of, obligation_id=None):
    return {"reason": reason, "requested_of": requested_of, "obligation_id": obligation_id}


def _applies(rule, obligation):
    """Does the rule's `applies_to` select this obligation? Selectors are conjunctive."""
    selectors = rule["applies_to"]
    checks = []
    if selectors.get("all_obligations") is True:
        checks.append(True)
    if "obligation_kinds" in selectors:
        checks.append(obligation["kind"] in selectors["obligation_kinds"])
    if "paths" in selectors:
        checks.append(any(c.startswith(p) for c in obligation["affected_components"]
                          for p in selectors["paths"]))
    return bool(checks) and all(checks)


def _requires_met(requires, evidence):
    kind = evidence["kind"]
    if "min_evidence_kind" in requires and RANK[kind] < RANK[requires["min_evidence_kind"]]:
        return False
    if requires.get("requires_intent_corroboration") and kind != "llm-corroborated":
        return False
    if requires.get("non_empty_assumptions") and not evidence["assumptions"]:
        return False
    return True


def _target(policy):
    return policy.get("escalation", {}).get("requested_of") or policy["approved_by"]


def _on(policy_action, policy, rule_id, message, oid):
    if policy_action == "escalate":
        return None, _escalation(f"{rule_id}: {message}", _target(policy), oid)
    return _violation(rule_id, message, oid), None


def _judge(entry, set_obligation, policy, evaluated):
    """Return (violation, escalation); (None, None) means this obligation is accepted."""
    oid, outcome, evidence = entry["obligation_id"], entry["outcome"], entry["evidence"]
    kind = evidence["kind"]
    if outcome == "failed":
        return _violation("BLOCK-FAILED", "the check failed", oid), None
    if outcome == "timeout":
        return _on(policy.get("on_timeout", "reject"), policy, "BLOCK-TIMEOUT",
                   "the check timed out and reached no verdict", oid)
    if outcome != "proven":  # not-run, unknown, and any value this engine does not know
        return _on(policy["on_unknown_evidence"], policy, "BLOCK-NO-VERDICT",
                   f"outcome {outcome!r} is not a verdict", oid)
    if kind not in MACHINE_CHECKED:
        return _violation("BLOCK-PROVEN-WITHOUT-MACHINE-EVIDENCE",
                          f"outcome 'proven' with {kind!r} evidence", oid), None
    required = set_obligation["required_evidence_kind"]
    if RANK[kind] < RANK[required]:
        return _violation("BLOCK-EVIDENCE-BELOW-REQUIRED",
                          f"{kind!r} evidence is weaker than the required {required!r}", oid), None
    for rule in policy["rules"]:
        evaluated.add(rule["rule_id"])
        if not _applies(rule, set_obligation):
            continue
        met = _requires_met(rule["requires"], evidence)
        if rule["action"] == "accept":
            if met:
                return None, None
        elif not met:
            if rule["action"] == "reject":
                return _violation(rule["rule_id"], "requirements of the rule are not met",
                                  oid), None
            return None, _escalation(f"{rule['rule_id']}: requirements of the rule are not met",
                                     rule["escalate_to"], oid)
    return _violation("DEFAULT-NO-RULE-DECIDES",
                      "no rule of the policy accepts, rejects or escalates this obligation",
                      oid), None


def _integrity(policy, report, obligation_set):
    """Reasons the three documents cannot be decided together."""
    problems = [("INTEGRITY-POLICY-INVALID", p) for p in policy_problems(policy)]
    claimed, decided = report["policy"], report["decision"]["policy_digest"]
    if claimed["policy_digest"] != decided:
        problems.append(("INTEGRITY-POLICY-DIGEST-MISMATCH",
                         f"policy.policy_digest {claimed['policy_digest']} differs from "
                         f"decision.policy_digest {decided}"))
    actual = policy_digest(policy)
    if claimed["policy_digest"] != actual:
        problems.append(("INTEGRITY-POLICY-NOT-PINNED",
                         f"the report pins {claimed['policy_digest']} but the supplied policy "
                         f"has digest {actual}"))
    for field, name in (("policy_id", "policy_id"), ("policy_version", "policy_version"),
                        ("approved_by", "approved_by"), ("approval", "approval")):
        if claimed[field] != policy[name]:
            problems.append(("INTEGRITY-POLICY-IDENTITY",
                             f"report.policy.{field} {claimed[field]!r} differs from the "
                             f"policy's {policy[name]!r}"))
    if report["trust"]["policy_approval_trusted"] and policy["approval"] == "unverified":
        problems.append(("INTEGRITY-TRUST-INCOHERENT",
                         "the report trusts a policy whose approval is 'unverified'"))
    problems += [("INTEGRITY-COVERAGE", p) for p in oset.check_coverage(obligation_set, report)]
    return problems


def decide(policy, report, obligation_set):
    """Return the decision (shaped like `decision` in report-schema.json).

    Preconditions: the three documents are valid against their schemas. The report's own
    `decision` is a claim and is ignored except for the digest it names.
    """
    # Digests are taken over NFC text, so two spellings of one string are one string:
    # decide on the normalised documents or a pinned digest would not pin the decision.
    policy, report, obligation_set = (_canonical_value(d) for d in (policy, report, obligation_set))
    problems = _integrity(policy, report, obligation_set)
    if problems:
        return {"result": "rejected", "policy_digest": policy_digest(policy),
                "rules_evaluated": 0,
                "violations": [_violation(rule_id, message) for rule_id, message in problems],
                "escalations": []}

    by_id = {o["obligation_id"]: o for o in obligation_set["obligations"]}
    violations, escalations, evaluated = [], [], set()
    for entry in sorted(report["obligations"], key=lambda e: e["obligation_id"]):
        violation, escalation = _judge(entry, by_id[entry["obligation_id"]], policy, evaluated)
        if violation:
            violations.append(violation)
        if escalation:
            escalations.append(escalation)
    if not report["trust"]["policy_approval_trusted"]:
        escalations.append(_escalation(
            "TRUST-POLICY-APPROVAL: the policy approval is not trusted, so nothing may be "
            "accepted on its authority", policy["approved_by"]))
    result = "rejected" if violations else "needs-human" if escalations else "accepted"
    return {"result": result, "policy_digest": policy_digest(policy),
            "rules_evaluated": len(evaluated), "violations": violations,
            "escalations": escalations}


def replay(policy, report, obligation_set):
    """Does the decision recorded in the report equal what the engine decides?"""
    return decide(policy, report, obligation_set) == report["decision"]


def canonical_decision(decision):
    return json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evaluate_files(policy_path, report_path, set_path):
    """Read and structurally validate the three documents, then decide. Does the I/O."""
    policy = validate.load_json(policy_path)
    report = validate.load_json(report_path)
    document = oset.load_obligation_set(set_path)
    for name, instance, schema_file in (("policy", policy, "policy-schema.json"),
                                        ("report", report, "report-schema.json")):
        problems = validate.validate(instance, validate.load_schema(SPECS / schema_file))
        if problems:
            raise validate.SchemaError(f"{name} is not valid: " + "; ".join(
                f"{p.path}: {p.message}" for p in problems))
    return policy, report, document


def main(argv):
    arity = {"decide": 3, "replay": 3, "policy-digest": 1}
    if len(argv) < 2 or arity.get(argv[1]) != len(argv) - 2:
        print(__doc__.strip().split("Usage:")[1].split("Exit status")[0].rstrip())
        return 2
    try:
        if argv[1] == "policy-digest":
            print(policy_digest(validate.load_json(argv[2])))
            return 0
        policy, report, document = evaluate_files(*argv[2:])
    except (OSError, ValueError, oset.ObligationSetError, validate.SchemaError) as exc:
        print(f"[ERROR] {exc}")
        return 2
    if argv[1] == "replay":
        same = replay(policy, report, document)
        print(f"[{'PASS' if same else 'FAIL'}] the decision recorded in {argv[3]} "
              f"{'is' if same else 'is not'} what the engine decides")
        return 0 if same else 1
    decision = decide(policy, report, document)
    print(canonical_decision(decision))
    return {"accepted": 0, "rejected": 1, "needs-human": 3}[decision["result"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
