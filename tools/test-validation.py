#!/usr/bin/env python3
"""Replay and harden the Selyth schema tests under an independent validator.

tools/test-schemas.py checks the schemas with tools/minischema.py, a validator
written alongside them. This runner re-checks the very same fixtures with the
`jsonschema` library (Draft 2020-12) and adds what a self-written validator
cannot give:

  * schema hygiene: each schema is a valid Draft 2020-12 schema with no
    duplicate keys, no unknown keywords and no dangling $ref;
  * the 23 legacy fixtures, imported unchanged: 2 must be accepted, 21 rejected;
  * for every rejection, the *intended* rule must be among the reported
    errors. A fixture that fails for an unrelated reason proves nothing;
  * the validator self-tests: the hygiene checks are shown to be able to fail.

Run with `make test` (see README.md), or `python tools/test-validation.py`
inside the pinned environment.
"""
import importlib.metadata
import importlib.util
import sys
from pathlib import Path

import validate

ROOT = Path(__file__).resolve().parent.parent


def _load_legacy():
    """Import tools/test-schemas.py as-is; its fixtures are the baseline to preserve."""
    spec = importlib.util.spec_from_file_location(
        "legacy_test_schemas", ROOT / "tools" / "test-schemas.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = _load_legacy()
REPORT, POLICY = legacy.REPORT, legacy.POLICY
mutate, good_report, good_policy = legacy.mutate, legacy.good_report, legacy.good_policy

# For each legacy rejection, the (instance path, keyword[, message fragment]) of the
# rule it is meant to exercise. Written from the intent recorded in each fixture's
# `why`, not copied from validator output.
LEGACY_REASONS = {
    "report/overclaim-proven-by-llm": ("$.obligations[0].evidence.kind", "enum"),
    "report/llm-claiming-soundness": ("$.obligations[0].evidence.soundness", "const"),
    "report/kernel-claiming-model-soundness": ("$.obligations[0].evidence.soundness", "const"),
    "report/timeout-claiming-soundness": ("$.obligations[0].evidence.soundness", "enum"),
    "report/failed-without-diagnostic": ("$.obligations[0].diagnostics", "minItems"),
    "report/accepted-with-violation": ("$.decision.violations", "maxItems"),
    "report/rejected-without-violation": ("$.decision.violations", "minItems"),
    "report/needs-human-without-escalation": ("$.decision.escalations", "minItems"),
    "report/accepted-on-untrusted-policy": ("$.trust.policy_approval_trusted", "const"),
    "report/local-subprocess-claiming-isolation": ("$.producer.outside_agent_boundary", "const"),
    "report/isolated-trust-model-must-deny-boundary":
        ("$.trust.verifier_outside_agent_boundary", "const"),
    "report/model-without-semantic-gap": ("$.obligations[0].evidence.semantic_gap", "minLength"),
    "report/human-asserted-without-waiver":
        ("$.obligations[0].evidence", "required", "waiver_id"),
    "report/empty-trust-limitations": ("$.trust.limitations", "minItems"),
    "report/empty-obligations": ("$.obligations", "minItems"),
    "report/unpinned-policy-digest": ("$.policy.policy_digest", "pattern"),
    "policy/escalate-without-target": ("$.rules[1]", "required", "escalate_to"),
    "policy/empty-rules": ("$.rules", "minItems"),
    "policy/rule-without-requires": ("$.rules[0].requires", "minProperties"),
    "policy/unknown-approver": ("$.approved_by", "minLength"),
    "policy/loose-suppression": ("$.suppression.mode", "enum"),
}


def model_check(**evidence):
    """A report whose first obligation is a machine-checked-model result."""
    return mutate(lambda c: c["obligations"][0]["evidence"].update(
        {"kind": "machine-checked-model",
         "semantic_gap": "Integers are unbounded in the model; production uses 64-bit.",
         **evidence}), good_report)


def empirical_check(kind, soundness):
    """A coherent rejected report whose first obligation failed an empirical check.

    The outcome is `failed` so that no other rule restricts the soundness claim:
    only the kind-to-soundness rule is left to bite.
    """
    def apply(c):
        c["obligations"][0].update({"outcome": "failed", "diagnostics": ["counterexample found"]})
        c["obligations"][0]["evidence"].update({"kind": kind, "soundness": soundness})
        c["decision"].update({"result": "rejected", "violations": [
            {"rule_id": "RULE-CORE-PROVEN", "obligation_id": "OBL-0007", "message": "check failed"}]})
    return mutate(apply, good_report)


# Structural cases added on top of the legacy suite:
# (name, schema, instance, must_pass, why, reason-for-rejection or None)
ADDED = [
    ("report/model-check-valid", REPORT, model_check(soundness="sound-for-model"), True,
     "a model check claiming model soundness is legitimate", None),
    ("report/model-check-claiming-fragment-soundness", REPORT, model_check(), False,
     "fragment soundness is the ceiling of kernel-proven evidence, not of a model check",
     ("$.obligations[0].evidence.soundness", "const")),
    ("report/property-tested-valid", REPORT, empirical_check("property-tested", "empirical"), True,
     "an empirical check claiming empirical soundness is legitimate", None),
    ("report/property-tested-claiming-fragment-soundness", REPORT,
     empirical_check("property-tested", "sound-for-fragment"), False,
     "testing samples behaviours; it cannot claim soundness for a fragment",
     ("$.obligations[0].evidence.soundness", "const")),
    ("report/runtime-asserted-claiming-model-soundness", REPORT,
     empirical_check("runtime-asserted", "sound-for-model"), False,
     "a runtime assertion observes one execution; it cannot claim soundness for a model",
     ("$.obligations[0].evidence.soundness", "const")),
]

FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


def matching(problems, reason):
    path, keyword, *fragment = reason
    return [p for p in problems if p.path == path and p.keyword == keyword
            and (not fragment or fragment[0] in p.message)]


def run_case(name, schema, instance, must_pass, reason):
    problems = validate.validate(instance, schema)
    accepted = not problems
    expected, got = ("PASS" if must_pass else "FAIL"), ("PASS" if accepted else "FAIL")
    label = f"{name:48s} expected={expected:4s} got={got:4s}"
    listing = "\n".join(f"    {p.path}: {p.message} ({p.keyword})" for p in problems)
    if accepted != must_pass:
        return check(False, label, listing or "    (no error reported)")
    if must_pass or reason is None:
        return check(True, label)
    if not matching(problems, reason):
        return check(False, label + "  wrong reason",
                     f"    expected an error {reason}\n    reported:\n{listing}")
    return check(True, f"{label} <- {reason[0]} ({reason[1]})")


def check_schemas():
    heading("Schema hygiene (Draft 2020-12)")
    schemas = {}
    for path in (REPORT, POLICY):
        try:
            schemas[path] = validate.load_schema(path)
            check(True, f"{path.name}: valid schema, no duplicate key, unknown keyword or dangling $ref")
        except validate.SchemaError as exc:
            check(False, f"{path.name}: unusable schema", f"    {exc}")
    return schemas


def check_validator_self_tests():
    heading("Validator self-tests (the hygiene checks can fail)")
    base = {"$schema": validate.DIALECT, "type": "object"}
    for label, schema, fragment in [
        ("a misspelled keyword", {**base, "additionalProperies": False}, "unknown keyword"),
        ("an unasserted format",
         {**base, "properties": {"t": {"type": "string", "format": "date-time"}}}, "'format'"),
        ("a dangling $ref",
         {**base, "properties": {"t": {"$ref": "#/$defs/nope"}}}, "does not resolve"),
        ("another dialect", {"$schema": "http://json-schema.org/draft-07/schema#"},
         "$schema must be"),
    ]:
        problems = validate.lint_schema(schema)
        check(any(fragment in p for p in problems), f"lint flags {label}", f"    got {problems}")
    for label, text in [("a duplicate object key", '{"a": {"b": 1, "b": 2}}'),
                        ("NaN", '{"a": NaN}')]:
        try:
            validate.loads(text)
            check(False, f"loader refuses {label}", "    parsed without error")
        except validate.SchemaError:
            check(True, f"loader refuses {label}")


def check_legacy(schemas):
    heading("Legacy fixtures from tools/test-schemas.py, replayed unchanged")
    cases = legacy.CASES
    rejected = {c[0] for c in cases if not c[3]}
    counts = (len(cases), len(cases) - len(rejected), len(rejected))
    check(counts == (23, 2, 21), "suite intact: 23 cases, 2 accepted, 21 rejected",
          f"    found {counts[0]} cases, {counts[1]} accepted, {counts[2]} rejected")
    check(rejected == set(LEGACY_REASONS), "every rejected case declares the rule it exercises",
          f"    differences: {sorted(rejected ^ set(LEGACY_REASONS))}")
    for name, schema_path, instance, must_pass, _why in cases:
        run_case(name, schemas[schema_path], instance, must_pass, LEGACY_REASONS.get(name))


def check_added(schemas):
    heading("Added structural cases")
    for name, schema_path, instance, must_pass, _why, reason in ADDED:
        run_case(name, schemas[schema_path], instance, must_pass, reason)


def main():
    version = importlib.metadata.version("jsonschema")
    print(f"Independent validator: jsonschema {version}, JSON Schema Draft 2020-12")
    schemas = check_schemas()
    check_validator_self_tests()
    if len(schemas) == 2:
        check_legacy(schemas)
        check_added(schemas)

    total_failed = len(FAILURES)
    print(f"\n{'FAILED' if total_failed else 'all checks passed'}: {total_failed} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
