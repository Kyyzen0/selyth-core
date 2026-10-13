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
    "report/kernel-claiming-model-soundness": ("$.obligations[0].evidence.soundness", "enum"),
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


def no_verdict(outcome, soundness):
    """A coherent rejected report whose first (kernel) obligation reached no verdict."""
    def apply(c):
        c["obligations"][0].update({"outcome": outcome})
        c["obligations"][0]["evidence"].update({"soundness": soundness})
        c["obligations"][0]["evidence"]["cost"].update({"timed_out": outcome == "timeout"})
        c["decision"].update({"result": "rejected", "violations": [
            {"rule_id": "BLOCK-NO-VERDICT", "obligation_id": "OBL-0007", "message": "no verdict"}]})
    return mutate(apply, good_report)


# Structural cases added on top of the legacy suite:
# (name, schema, instance, must_pass, why, reason-for-rejection or None)
ADDED = [
    ("report/model-check-valid", REPORT, model_check(soundness="sound-for-model"), True,
     "a model check claiming model soundness is legitimate", None),
    ("report/model-check-claiming-fragment-soundness", REPORT, model_check(), False,
     "fragment soundness is the ceiling of kernel-proven evidence, not of a model check",
     ("$.obligations[0].evidence.soundness", "enum")),
    ("report/timeout-without-soundness", REPORT, no_verdict("timeout", "none"), True,
     "a kernel check that timed out is reportable, with no soundness claim", None),
    ("report/not-run-without-soundness", REPORT, no_verdict("not-run", "none"), True,
     "a kernel check that was not run is reportable, with no soundness claim", None),
    ("report/proven-without-soundness", REPORT,
     mutate(lambda c: c["obligations"][0]["evidence"].update(soundness="none"), good_report), False,
     "a proof supports a soundness claim; 'none' is only for checks without a verdict",
     ("$.obligations[0].evidence.soundness", "enum")),
    ("report/machine-model-proven-without-soundness", REPORT, model_check(soundness="none"), False,
     "a proven model check must claim model soundness",
     ("$.obligations[0].evidence.soundness", "enum")),
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

def remote_report():
    """A coherent report claiming remote attestation, with a remote producer."""
    def apply(c):
        c["producer"].update({"identity": "https://verifier.example/selyth-verify@0.1.0",
                              "kind": "remote-service", "outside_agent_boundary": True})
        c["trust"].update({"model": "remote-attested", "verifier_outside_agent_boundary": True})
    return mutate(apply, good_report)


def isolated_report(kind):
    """A report claiming local isolation, produced by a producer of the given kind."""
    def apply(c):
        c["producer"].update({"kind": kind, "outside_agent_boundary": kind != "local-subprocess"})
        c["trust"].update({"model": "local-isolated", "verifier_outside_agent_boundary": True})
    return mutate(apply, good_report)


def only_core_rule(policy):
    """The policy keeps RULE-CORE-PROVEN (postcondition, invariant) and loses its catch-all."""
    policy["rules"] = policy["rules"][:1]


# Controls for the new structural rules: legitimate documents that must keep validating,
# so that a rule which rejects everything cannot pass for a rule which rejects the right thing.
ADDED += [
    ("report/accepted-with-one-rule-evaluated", REPORT,
     mutate(lambda c: c["decision"].update({"rules_evaluated": 1}), good_report), True,
     "the smallest legitimate acceptance has evaluated exactly one rule", None),
    ("report/remote-attested-valid", REPORT, remote_report(), True,
     "a remote service outside the boundary may claim remote attestation", None),
    ("report/local-isolated-valid", REPORT, isolated_report("container"), True,
     "a container outside the boundary may claim a verifier outside the boundary", None),
]

# Adversarial scenarios of docs/roadmap/next-tasks-for-claude.md, task 1.3. Each belongs to
# one layer (docs/specifications/validation-layers.md):
#   structural: JSON Schema can state the rule, so a validator must reject the document now;
#   semantic:   the rule compares values across fields or documents, which JSON Schema cannot
#               do. Every document is structurally valid; rejecting it is the policy engine's job.
# (name, schema, instance, why, reason-for-rejection)
ADVERSARIAL_STRUCTURAL = [
    ("report/accepted-with-zero-rules-evaluated", REPORT,
     mutate(lambda c: c["decision"].update({"rules_evaluated": 0}), good_report),
     "an acceptance that no rule had the chance to reject is vacuous",
     ("$.decision.rules_evaluated", "minimum")),
    ("report/remote-trust-with-local-producer", REPORT,
     mutate(lambda c: c["producer"].update(
         {"kind": "local-subprocess", "outside_agent_boundary": False}), remote_report()),
     "a local subprocess cannot back a remote-attested trust claim",
     ("$.producer.kind", "const")),
    ("report/remote-trust-with-inside-boundary-producer", REPORT,
     mutate(lambda c: c["producer"].update({"outside_agent_boundary": False}), remote_report()),
     "a remote service that declares itself inside the boundary cannot back remote attestation",
     ("$.producer.outside_agent_boundary", "const")),
    ("report/outside-boundary-claim-with-local-producer", REPORT,
     isolated_report("local-subprocess"),
     "the trust section may not claim a verifier outside the boundary that the producer denies",
     ("$.producer.outside_agent_boundary", "const")),
]

# (name, [(schema, instance), ...], why, rule_id the policy engine rejects it with)
ADVERSARIAL_SEMANTIC = [
    ("report/policy-digest-mismatch",
     [(REPORT, mutate(lambda c: c["decision"].update({"policy_digest": "sha256:" + "d" * 64}),
                      good_report))],
     "decision.policy_digest differs from policy.policy_digest: JSON Schema cannot compare "
     "two values of the same instance",
     "INTEGRITY-POLICY-DIGEST-MISMATCH"),
    ("policy/no-rule-applies-to-obligation",
     [(POLICY, mutate(only_core_rule, good_policy)),
      (REPORT, mutate(lambda c: c["obligations"][0].update(
          {"kind": "termination", "statement": "decreases queue.length"}), good_report))],
     "the policy has no rule for a `termination` obligation, yet the report claims acceptance: "
     "matching a rule to an obligation needs both documents",
     "DEFAULT-NO-RULE-DECIDES"),
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


def check_adversarial(schemas):
    heading("Adversarial scenarios (docs/roadmap/next-tasks-for-claude.md, task 1)")
    for name, schema_path, instance, _why, reason in ADVERSARIAL_STRUCTURAL:
        run_case(f"structural  {name}", schemas[schema_path], instance, False, reason)
    engine_text = ((ROOT / "tools" / "policy_engine.py").read_text(encoding="utf-8")
                   + (ROOT / "docs" / "specifications" / "policy-engine.md").read_text(encoding="utf-8"))
    for name, documents, _why, engine_rule in ADVERSARIAL_SEMANTIC:
        problems = [p for schema_path, instance in documents
                    for p in validate.validate(instance, schemas[schema_path])]
        if problems:
            check(False, f"semantic    {name}: now rejected by JSON Schema",
                  "    Reclassify it as structural and give it the rejection reason:\n"
                  + "\n".join(f"    {p.path}: {p.message} ({p.keyword})" for p in problems))
        else:
            # The actual rejection is exercised by tools/test-policy-engine.py, which needs
            # the third document (the Obligation Set) this suite does not build.
            check(engine_rule in engine_text,
                  f"semantic    {name}: valid for JSON Schema; the policy engine rejects it "
                  f"as {engine_rule}", f"    {engine_rule} is not defined by the engine")


def check_rule_coverage(schemas):
    """Neutralise each allOf rule in turn: some fixture must notice, for the right reason.

    Verdicts alone are not enough. A rule can be deleted and every fixture keep its verdict
    when another rule rejects the same document, so a fixture only counts as noticing if
    its intended rejection reason disappears too.
    """
    heading("Rule coverage (every allOf rule is exercised by a fixture)")
    cases = ([(n, sp, i, mp, LEGACY_REASONS.get(n)) for n, sp, i, mp, _w in legacy.CASES]
             + [(n, sp, i, mp, r) for n, sp, i, mp, _w, r in ADDED]
             + [(n, sp, i, False, r) for n, sp, i, _w, r in ADVERSARIAL_STRUCTURAL])

    def notices(schema_path, schema):
        for name, case_schema, instance, must_pass, reason in cases:
            if case_schema != schema_path:
                continue
            problems = validate.validate(instance, schema)
            if (not problems) != must_pass or (reason and not matching(problems, reason)):
                return True
        return False

    for schema_path, schema in schemas.items():
        total, untested = 0, []
        for where, sub in validate.iter_subschemas(schema):
            rules = sub.get("allOf", []) if isinstance(sub, dict) else []
            for i, rule in enumerate(rules):
                total += 1
                rules[i] = {}  # a schema that accepts everything
                try:
                    if not notices(schema_path, schema):
                        untested.append(f"{where}.allOf[{i}]")
                finally:
                    rules[i] = rule
        check(not untested, f"{schema_path.name}: {total} allOf rules, each exercised by a fixture",
              "    no fixture notices when these are removed:\n    " + "\n    ".join(untested))


def main():
    version = importlib.metadata.version("jsonschema")
    print(f"Independent validator: jsonschema {version}, JSON Schema Draft 2020-12")
    schemas = check_schemas()
    check_validator_self_tests()
    if len(schemas) == 2:
        check_legacy(schemas)
        check_added(schemas)
        check_adversarial(schemas)
        check_rule_coverage(schemas)

    total_failed = len(FAILURES)
    print(f"\n{'FAILED' if total_failed else 'all checks passed'}: {total_failed} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
