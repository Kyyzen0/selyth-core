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
  policy_engine.py decide        <policy.json> <report.json> <obligation-set.json> [--lock LOCK.json]
  policy_engine.py replay        <policy.json> <report.json> <obligation-set.json> [--lock LOCK.json]
  policy_engine.py policy-digest <policy.json>
  policy_engine.py lock          <policy.json> <obligation-set.json> --approved-by NAME
Exit status: decide/replay 0 accepted (replay: report's decision reproduced),
1 rejected (replay: not reproduced), 3 needs-human, 2 usage or unreadable/invalid input.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import obligation_set as oset
import validate

ROOT = Path(__file__).resolve().parent.parent
SPECS = ROOT / "docs" / "specifications"
# Schemas are read once, when the module is imported: `decide` itself does no I/O.
_SCHEMAS = {"policy": validate.load_schema(SPECS / "policy-schema.json"),
            "report": validate.load_schema(SPECS / "report-schema.json"),
            "obligation set": validate.load_schema(oset.SET_SCHEMA),
            "lock": validate.load_schema(SPECS / "lock-schema.json")}

# Evidence strength, weakest to strongest. Defined here, not read from the position of
# values in a JSON `enum`: the report schema lists kinds strongest-first and the policy
# schema weakest-first, and `enum` order has no meaning in JSON Schema anyway.
EVIDENCE_LATTICE = ("human-asserted", "llm-corroborated", "runtime-asserted",
                    "property-tested", "machine-checked-model", "kernel-proven")
RANK = {kind: i for i, kind in enumerate(EVIDENCE_LATTICE)}
MACHINE_CHECKED = ("machine-checked-model", "kernel-proven")
EMPIRICAL = ("human-asserted", "llm-corroborated", "runtime-asserted", "property-tested")


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


def _judge(entry, set_obligation, policy, evaluated, waivers_allowed):
    """Return (violation, escalation); (None, None) means this obligation is accepted."""
    oid, outcome, evidence = entry["obligation_id"], entry["outcome"], entry["evidence"]
    kind = evidence["kind"]
    if outcome == "failed":
        return _violation("BLOCK-FAILED", "the check failed", oid), None
    if outcome == "timeout":
        return _on(policy.get("on_timeout", "reject"), policy, "BLOCK-TIMEOUT",
                   "the check timed out and reached no verdict", oid)
    if outcome not in ("proven", "passed"):  # not-run, unknown, and any value this engine does not know
        return _on(policy["on_unknown_evidence"], policy, "BLOCK-NO-VERDICT",
                   f"outcome {outcome!r} is not a verdict", oid)
    if outcome == "proven" and kind not in MACHINE_CHECKED:
        return _violation("BLOCK-PROVEN-WITHOUT-MACHINE-EVIDENCE",
                          f"outcome 'proven' with {kind!r} evidence", oid), None
    if outcome == "passed":
        if kind not in EMPIRICAL:
            return _violation("BLOCK-PASSED-WITHOUT-EMPIRICAL-EVIDENCE",
                              f"outcome 'passed' with {kind!r} evidence: a machine check that "
                              "succeeded is a proof", oid), None
        if kind == "llm-corroborated":
            return _violation("BLOCK-LLM-NOT-SUFFICIENT",
                              "a model's corroboration is probabilistic and can never accept an "
                              "obligation alone", oid), None
        if kind == "human-asserted":
            # A human waiver is a suppression: the policy decides whether any is allowed.
            if policy["suppression"]["mode"] == "forbidden":
                return _violation("BLOCK-WAIVER-FORBIDDEN",
                                  "the policy forbids suppression, so a human waiver cannot accept "
                                  "an obligation", oid), None
            if not evidence.get("waiver_id"):
                return _violation("BLOCK-WAIVER-MISSING",
                                  "a human assertion needs a recorded waiver", oid), None
            if oid not in waivers_allowed:
                return _violation("BLOCK-WAIVER-LIMIT",
                                  f"the policy allows at most {policy['suppression']['max_active']} "
                                  "active waivers", oid), None
    required = set_obligation["required_evidence_kind"]
    if RANK[kind] < RANK[required]:
        return _violation("BLOCK-EVIDENCE-BELOW-REQUIRED",
                          f"{kind!r} evidence is weaker than the required {required!r}", oid), None
    budget = policy.get("cost_budget")
    if not isinstance(budget, dict):
        budget = {}
    cost = evidence.get("cost")
    if not isinstance(cost, dict):
        cost = {}
    for metric, ceiling in (("resource_units", "max_resource_units"),
                            ("peak_rss_bytes", "max_peak_rss_bytes"),
                            ("wall_ms", "deep_wall_ms")):
        limit = budget.get(ceiling)
        if limit is None:
            continue
        if metric not in cost:
            return _violation("BLOCK-COST-BUDGET",
                              f"the policy caps {ceiling} at {limit} but the report records no {metric}",
                              oid), None
        if cost[metric] > limit:
            return _violation("BLOCK-COST-BUDGET",
                              f"cost {metric} {cost[metric]} exceeds the policy ceiling "
                              f"{ceiling} {limit}", oid), None
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


def make_lock(policy, obligation_set, approved_by):
    """The lock a human approves: the digests of the policy and the Obligation Set."""
    return {"selyth_lock_version": "0.1.0", "obligation_set_digest": oset.set_digest(obligation_set),
            "policy_digest": policy_digest(policy), "approved_by": approved_by}


def _input_problems(policy, report, obligation_set, lock):
    """Why the documents are not valid inputs: the engine accepts nothing it cannot read."""
    problems = []
    named = [("policy", policy), ("report", report), ("obligation set", obligation_set)]
    if lock is not None:
        named.append(("lock", lock))
    try:
        for name, document in named:
            for p in validate.validate(document, _SCHEMAS[name]):
                problems.append(("INTEGRITY-INPUT-INVALID", f"{name}: {p.path}: {p.message} ({p.keyword})"))
        if not problems:
            oset.canonical_form(obligation_set)  # duplicates the schema cannot state
    except oset.ObligationSetError as exc:
        problems.append(("INTEGRITY-INPUT-INVALID", f"obligation set: {exc}"))
    except RecursionError:
        problems.append(("INTEGRITY-INPUT-INVALID", "a document is nested too deeply to be read"))
    return problems


def _digest_or_zero(policy):
    try:
        return policy_digest(policy)
    except (ValueError, TypeError, RecursionError, OverflowError):
        return "sha256:" + "0" * 64


def _lock_problems(policy, obligation_set, lock):
    problems = []
    if lock["policy_digest"] != policy_digest(policy):
        problems.append(("INTEGRITY-POLICY-NOT-APPROVED",
                         f"the policy has digest {policy_digest(policy)} but the lock approved "
                         f"{lock['policy_digest']}: the policy was changed after approval"))
    if lock["obligation_set_digest"] != oset.set_digest(obligation_set):
        problems.append(("INTEGRITY-OBLIGATION-SET-NOT-FROZEN",
                         f"the Obligation Set has digest {oset.set_digest(obligation_set)} but the lock "
                         f"froze {lock['obligation_set_digest']}: the obligations were changed after approval"))
    return problems


def _integrity(policy, report, obligation_set, lock=None):
    """Reasons the three documents cannot be decided together."""
    problems = _lock_problems(policy, obligation_set, lock) if lock is not None else []
    problems += [("INTEGRITY-POLICY-INVALID", p) for p in policy_problems(policy)]
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


def decide(policy, report, obligation_set, lock=None, *, validate_inputs=True):
    """Return the decision (shaped like `decision` in report-schema.json).

    Total: any JSON values give a decision. A document that is not valid against its schema
    is rejected (INTEGRITY-INPUT-INVALID), never judged, so the engine does not depend on
    its caller having checked. The report's own `decision` is a claim and is ignored except
    for the digest it names. With a lock, the policy and the Obligation Set must be the ones
    it approved. `validate_inputs=False` skips the schema check and is for tests that probe
    the semantics with deliberately malformed evidence; it is not a mode for a gate.
    """
    if validate_inputs:
        invalid = _input_problems(policy, report, obligation_set, lock)
        if invalid:
            return {"result": "rejected", "policy_digest": _digest_or_zero(policy), "rules_evaluated": 0,
                    "violations": [_violation(rule_id, message) for rule_id, message in invalid],
                    "escalations": []}
    # Digests are taken over NFC text, so two spellings of one string are one string:
    # decide on the normalised documents or a pinned digest would not pin the decision.
    policy, report, obligation_set = (_canonical_value(d) for d in (policy, report, obligation_set))
    problems = _integrity(policy, report, obligation_set, lock)
    if problems:
        return {"result": "rejected", "policy_digest": policy_digest(policy),
                "rules_evaluated": 0,
                "violations": [_violation(rule_id, message) for rule_id, message in problems],
                "escalations": []}

    by_id = {o["obligation_id"]: o for o in obligation_set["obligations"]}
    violations, escalations, evaluated = [], [], set()
    waived = sorted(e["obligation_id"] for e in report["obligations"]
                    if e["outcome"] == "passed" and e["evidence"]["kind"] == "human-asserted")
    limit = policy["suppression"].get("max_active")
    waivers_allowed = set(waived if limit is None else waived[:limit])  # first by obligation_id
    for entry in sorted(report["obligations"], key=lambda e: e["obligation_id"]):
        violation, escalation = _judge(entry, by_id[entry["obligation_id"]], policy, evaluated,
                                       waivers_allowed)
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


def replay(policy, report, obligation_set, lock=None):
    """Does the decision recorded in the report equal what the engine decides?"""
    recorded = report.get("decision") if isinstance(report, dict) else None
    return decide(policy, report, obligation_set, lock) == recorded


def canonical_decision(decision):
    return json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_lock(path):
    lock = validate.load_json(path)
    problems = validate.validate(lock, validate.load_schema(SPECS / "lock-schema.json"))
    if problems:
        raise validate.SchemaError("lock is not valid: " + "; ".join(f"{p.path}: {p.message}" for p in problems))
    return lock


def load_valid(path, name, schema_file):
    """Read a JSON document and require it to be valid against its schema."""
    instance = validate.load_json(path)
    problems = validate.validate(instance, validate.load_schema(SPECS / schema_file))
    if problems:
        raise validate.SchemaError(f"{name} is not valid: " + "; ".join(
            f"{p.path}: {p.message}" for p in problems))
    return instance


def evaluate_files(policy_path, report_path, set_path, lock_path=None):
    """Read and structurally validate the documents, then decide. Does the I/O."""
    policy = load_valid(policy_path, "policy", "policy-schema.json")
    report = load_valid(report_path, "report", "report-schema.json")
    document = oset.load_obligation_set(set_path)
    return policy, report, document, load_lock(lock_path) if lock_path else None


def build_parser():
    ap = argparse.ArgumentParser(prog="policy_engine.py",
                                 description=__doc__.split("Usage:")[0].strip())
    sub = ap.add_subparsers(dest="command", required=True)
    for name, help_text in (("decide", "decide (policy, report, obligation set)"),
                            ("replay", "check that a report's recorded decision is the engine's")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("policy")
        p.add_argument("report")
        p.add_argument("obligation_set", metavar="obligation-set")
        p.add_argument("--lock", help="the human-approved lock (policy + Obligation Set digests)")
    sub.add_parser("policy-digest", help="print the canonical digest of a policy").add_argument("policy")
    lock = sub.add_parser("lock", help="print the lock a human approves for a policy and a set")
    lock.add_argument("policy")
    lock.add_argument("obligation_set", metavar="obligation-set")
    lock.add_argument("--approved-by", required=True, help="the approving human")
    return ap


def main(argv):
    args = build_parser().parse_args(argv[1:])
    try:
        if args.command == "policy-digest":
            print(policy_digest(validate.load_json(args.policy)))
            return 0
        if args.command == "lock":
            # An approval artifact for a document that cannot be used would have to be redone.
            print(json.dumps(make_lock(load_valid(args.policy, "policy", "policy-schema.json"),
                                       oset.load_obligation_set(args.obligation_set), args.approved_by),
                             indent=2, sort_keys=True))
            return 0
        policy, report, document, lock = evaluate_files(args.policy, args.report, args.obligation_set,
                                                         getattr(args, "lock", None))
    except (OSError, ValueError, oset.ObligationSetError, validate.SchemaError) as exc:
        print(f"[ERROR] {exc}")
        return 2
    if args.command == "replay":
        same = replay(policy, report, document, lock)
        print(f"[{'PASS' if same else 'FAIL'}] the decision recorded in {args.report} "
              f"{'is' if same else 'is not'} what the engine decides")
        return 0 if same else 1
    decision = decide(policy, report, document, lock)
    print(canonical_decision(decision))
    return {"accepted": 0, "rejected": 1, "needs-human": 3}[decision["result"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
