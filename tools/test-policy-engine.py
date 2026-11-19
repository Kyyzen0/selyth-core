#!/usr/bin/env python3
"""Tests for the deterministic policy engine (docs/specifications/policy-engine.md).

  * scenarios: every integrity failure, block, default and escalation produces the
    decision the specification gives, naming the right rule and obligation;
  * safety, exhaustively: over every combination of outcome, evidence kind, required
    kind, rule action and rule threshold, `accepted` is only ever produced for an
    obligation that is proven, by machine-checked evidence, at least as strong as required;
  * determinism: same inputs, same bytes, across runs, hash seeds and input ordering;
    and `decide` touches no file, socket, process, clock or randomness;
  * replay: the decision a report records is the one the engine reproduces.

Run with `make test`, or `python tools/test-policy-engine.py` in the pinned environment.
"""
import copy
import importlib.util
import itertools
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import obligation_set as oset
import policy_engine as engine
import validate

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = _load("legacy_test_schemas", "test-schemas.py")
osets = _load("test_obligation_set", "test-obligation-set.py")

REPORT_SCHEMA = validate.load_schema(oset.REPORT_SCHEMA)
POLICY_SCHEMA = validate.load_schema(ROOT / "docs" / "specifications" / "policy-schema.json")
DECISION_SCHEMA = {"$schema": validate.DIALECT, "$ref": "#/$defs/decision",
                   "$defs": REPORT_SCHEMA["$defs"]}
FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


# ------------------------------------------------------------------ fixtures

def the_set():
    return osets.demo()  # OBL-0007 postcondition, OBL-0008 invariant, OBL-0009 termination


def accept_all_policy(min_kind="machine-checked-model"):
    policy = copy.deepcopy(legacy.good_policy)
    policy["rules"] = [{"rule_id": "RULE-ALL", "applies_to": {"all_obligations": True},
                        "requires": {"min_evidence_kind": min_kind}, "action": "accept"}]
    return policy


def build_report(policy, document, edit=None):
    """A report covering `document` and pinning `policy`; `edit(report)` may then change it."""
    report = osets.report_for(document)
    digest = engine.policy_digest(policy)
    report["policy"] = {"policy_id": policy["policy_id"], "policy_version": policy["policy_version"],
                        "policy_digest": digest, "approved_by": policy["approved_by"],
                        "approval": policy["approval"]}
    report["decision"]["policy_digest"] = digest
    report["trust"]["policy_approval_trusted"] = policy["approval"] != "unverified"
    if edit:
        edit(report)
    return report


CEILING = {"property-tested": "empirical", "runtime-asserted": "empirical", "llm-corroborated": "probabilistic",
           "human-asserted": "none", "kernel-proven": "sound-for-fragment"}


def valid_report(report):
    return not validate.validate(report, REPORT_SCHEMA)


def entry(report, oid):
    return next(e for e in report["obligations"] if e["obligation_id"] == oid)


def decide(policy, report, document, lock=None):
    """The engine's semantics, probed with evidence that is deliberately not schema-valid
    (unknown outcomes, incoherent soundness). The default, validating path is tested in
    check_input_validation() and by every other suite, which call engine.decide directly."""
    return engine.decide(policy, report, document, lock, validate_inputs=False)


def rule_ids(decision):
    return [v["rule_id"] for v in decision["violations"]]


def scenario(label, policy, report, document, result, violations=(), escalation_rules=(),
             obligation=None):
    d = decide(policy, report, document)
    problems = validate.validate(d, DECISION_SCHEMA)
    ok = (d["result"] == result and rule_ids(d) == list(violations)
          and [e["reason"].split(":")[0] for e in d["escalations"]] == list(escalation_rules)
          and not problems
          and (obligation is None or {v["obligation_id"] for v in d["violations"]}
               | {e["obligation_id"] for e in d["escalations"]} == {obligation}))
    return check(ok, f"{label}: {result}", f"    got {json.dumps(d, indent=1)}\n    schema: {problems}")


# ------------------------------------------------------------------ scenarios

def check_scenarios():
    heading("Integrity: refused as a whole")
    document, policy = the_set(), accept_all_policy()
    scenario("a coherent report is accepted", policy, build_report(policy, document), document,
             "accepted")
    d = decide(policy, build_report(policy, document), document)
    check(d["rules_evaluated"] == 1 and d["policy_digest"] == engine.policy_digest(policy),
          "accepted decision evaluated 1 rule and names the policy digest", f"    {d}")

    other = "sha256:" + "d" * 64
    scenario("policy digest differs from decision digest (roadmap 1.3)", policy,
             build_report(policy, document, lambda r: r["decision"].update(policy_digest=other)),
             document, "rejected", ["INTEGRITY-POLICY-DIGEST-MISMATCH"])
    edited = copy.deepcopy(policy)
    edited["rules"][0]["requires"]["min_evidence_kind"] = "human-asserted"
    scenario("policy edited after the report pinned it", edited, build_report(policy, document),
             document, "rejected", ["INTEGRITY-POLICY-NOT-PINNED"])
    scenario("report names another policy version", policy,
             build_report(policy, document, lambda r: r["policy"].update(policy_version="9.9.9")),
             document, "rejected", ["INTEGRITY-POLICY-IDENTITY"])
    scenario("report names another approver", policy,
             build_report(policy, document, lambda r: r["policy"].update(approved_by="agent")),
             document, "rejected", ["INTEGRITY-POLICY-IDENTITY"])
    dup = accept_all_policy()
    dup["rules"].append(copy.deepcopy(dup["rules"][0]))
    scenario("two rules share a rule_id", dup, build_report(dup, document), document,
             "rejected", ["INTEGRITY-POLICY-INVALID"])
    unverified = accept_all_policy()
    unverified["approval"] = "unverified"
    scenario("report trusts an unverified policy", unverified,
             build_report(unverified, document, lambda r: r["trust"].update(
                 policy_approval_trusted=True)), document, "rejected",
             ["INTEGRITY-TRUST-INCOHERENT"])

    omitted = build_report(policy, document, lambda r: r["obligations"].pop())
    scenario("report omits an obligation (task 2 coverage)", policy, omitted, document,
             "rejected", ["INTEGRITY-COVERAGE"])
    check(decide(policy, omitted, document)["rules_evaluated"] == 0,
          "an integrity failure judges no obligation (rules_evaluated 0)")
    scenario("report invents an obligation", policy,
             build_report(policy, document, lambda r: r["obligations"].append(
                 {**copy.deepcopy(r["obligations"][0]), "obligation_id": "OBL-0777"})),
             document, "rejected", ["INTEGRITY-COVERAGE"])
    stale = build_report(policy, document)
    changed = copy.deepcopy(document)
    changed["obligations"][0]["statement"] += " && true"
    scenario("the set was edited after the report", policy, stale, changed, "rejected",
             ["INTEGRITY-COVERAGE"] * 3)  # set digest, obligation digest, and the echoed statement

    heading("Blocks and defaults (fail closed)")
    for outcome, rule in (("failed", "BLOCK-FAILED"),):
        scenario(f"outcome {outcome}", policy,
                 build_report(policy, document, lambda r: entry(r, "OBL-0008").update(
                     outcome=outcome, diagnostics=["counterexample"])),
                 document, "rejected", [rule], obligation="OBL-0008")
    for action, result in (("reject", "rejected"), ("escalate", "needs-human")):
        timeout = accept_all_policy()
        timeout["on_timeout"] = action
        timeout["escalation"] = {"requested_of": "owner"}
        report = build_report(timeout, document, lambda r: entry(r, "OBL-0008").update(
            outcome="timeout"))
        scenario(f"timeout with on_timeout={action}", timeout, report, document, result,
                 ["BLOCK-TIMEOUT"] if action == "reject" else [],
                 [] if action == "reject" else ["BLOCK-TIMEOUT"], obligation="OBL-0008")
    check(decide(accept_all_policy(), build_report(accept_all_policy(), document, lambda r: entry(
        r, "OBL-0008").update(outcome="timeout")), document)["result"] == "rejected",
        "timeout with no on_timeout in the policy defaults to rejected")
    for action, result in (("reject", "rejected"), ("escalate", "needs-human")):
        for outcome in ("not-run", "unknown", "a-value-from-the-future"):
            unknown = accept_all_policy()
            unknown["on_unknown_evidence"] = action
            report = build_report(unknown, document, lambda r: entry(r, "OBL-0009").update(
                outcome=outcome))
            d = decide(unknown, report, document)
            check(d["result"] == result and "BLOCK-NO-VERDICT" in (
                rule_ids(d) + [e["reason"].split(":")[0] for e in d["escalations"]]),
                f"outcome {outcome!r} with on_unknown_evidence={action}: {result}", f"    {d}")
    esc = accept_all_policy()
    esc["on_unknown_evidence"] = "escalate"
    d = decide(esc, build_report(esc, document, lambda r: entry(r, "OBL-0009").update(
        outcome="not-run")), document)
    check(d["escalations"][0]["requested_of"] == esc["approved_by"],
          "an escalation without policy.escalation goes to approved_by", f"    {d}")

    only_post = {**accept_all_policy(), "rules": [{
        "rule_id": "RULE-ONLY-POST", "applies_to": {"obligation_kinds": ["postcondition"]},
        "requires": {"min_evidence_kind": "kernel-proven"}, "action": "accept"}]}
    d = decide(only_post, build_report(only_post, document), document)
    check(d["result"] == "rejected" and rule_ids(d) == ["DEFAULT-NO-RULE-DECIDES"] * 2
          and [v["obligation_id"] for v in d["violations"]] == ["OBL-0008", "OBL-0009"],
          "no rule applies to an obligation (roadmap 1.3): rejected, naming exactly those obligations",
          f"    {d}")
    unmet = accept_all_policy("kernel-proven")
    report = build_report(unmet, document, lambda r: entry(r, "OBL-0009")["evidence"].update(
        kind="machine-checked-model", soundness="sound-for-model",
        semantic_gap="model omits overflow"))
    d = decide(unmet, report, document)
    check(d["result"] == "rejected" and [v["obligation_id"] for v in d["violations"]] == ["OBL-0009"],
          "an accept rule whose threshold is not met does not accept (falls to the default)",
          f"    {d}")

    heading("Evidence requirements")
    strict = copy.deepcopy(document)
    strict["obligations"][2]["required_evidence_kind"] = "kernel-proven"
    report = build_report(policy, strict, lambda r: entry(r, "OBL-0009")["evidence"].update(
        kind="machine-checked-model", soundness="sound-for-model", semantic_gap="gap"))
    scenario("supplied evidence weaker than the set requires", policy, report, strict,
             "rejected", ["BLOCK-EVIDENCE-BELOW-REQUIRED"], obligation="OBL-0009")
    relaxed = build_report(policy, document, lambda r: entry(r, "OBL-0009")["evidence"].update(
        kind="machine-checked-model", soundness="sound-for-model", semantic_gap="gap"))
    scenario("machine-checked evidence meets a machine-checked requirement", policy, relaxed,
             document, "accepted")
    scenario("a forged echo of the set is an integrity failure, not a judged obligation", policy,
             build_report(policy, strict, lambda r: (
                 entry(r, "OBL-0009").update(required_evidence_kind="human-asserted"),
                 entry(r, "OBL-0009")["evidence"].update(
                     kind="machine-checked-model", soundness="sound-for-model",
                     semantic_gap="gap"))), strict, "rejected",
             ["INTEGRITY-COVERAGE"])
    forged_echo = build_report(policy, strict, lambda r: entry(r, "OBL-0009").update(
        required_evidence_kind="human-asserted"))
    d = decide(policy, forged_echo, strict)
    check(d["rules_evaluated"] == 0 and any("required_evidence_kind" in v["message"]
                                           for v in d["violations"]),
          "the forged echo names the field and judges no obligation", f"    {d}")
    forged = build_report(policy, document, lambda r: entry(r, "OBL-0007")["evidence"].update(
        kind="llm-corroborated", soundness="probabilistic",
        model_identity={"name": "m", "version": "1"}))
    check(bool(validate.validate(forged, REPORT_SCHEMA)),
          "(setup) 'proven' by an LLM is invalid for JSON Schema")
    scenario("...and the engine refuses it too, without relying on the schema", policy, forged,
             document, "rejected", ["BLOCK-PROVEN-WITHOUT-MACHINE-EVIDENCE"],
             obligation="OBL-0007")
    no_assumptions = accept_all_policy()
    no_assumptions["rules"][0]["requires"]["non_empty_assumptions"] = True
    d = decide(no_assumptions, build_report(no_assumptions, document, lambda r: entry(
        r, "OBL-0008")["evidence"].update(assumptions=[])), document)
    check(d["result"] == "rejected" and [v["obligation_id"] for v in d["violations"]] == ["OBL-0008"],
          "non_empty_assumptions: only the obligation with an empty list lacks it", f"    {d}")

    heading("Empirical evidence: the lattice below machine-checked-model is live")

    def violations_of(decision):
        return [(v["rule_id"], v["obligation_id"]) for v in decision["violations"]]

    def empirical_run(kind, required, threshold, outcome="passed", suppression="forbidden", **evidence):
        doc = the_set()
        doc["obligations"][0]["required_evidence_kind"] = required
        pol = accept_all_policy(threshold)
        pol["suppression"] = {"mode": suppression, "requires_human_approval": True, "recorded_in_report": True}
        report = build_report(pol, doc, lambda r: entry(r, "OBL-0007").update(outcome=outcome) or entry(
            r, "OBL-0007")["evidence"].update({"kind": kind, "soundness": CEILING[kind], **evidence}))
        return decide(pol, report, doc), report

    for kind, required, threshold, want, why in (
        ("property-tested", "property-tested", "property-tested", "accepted", "a passing property test meets its requirement and the policy's threshold"),
        ("property-tested", "property-tested", "machine-checked-model", "rejected", "...but not a policy that demands machine-checked evidence"),
        ("property-tested", "kernel-proven", "property-tested", "rejected", "...nor a set that requires a proof"),
        ("runtime-asserted", "runtime-asserted", "runtime-asserted", "accepted", "a runtime assertion at its own level"),
        ("runtime-asserted", "runtime-asserted", "property-tested", "rejected", "runtime-asserted ranks below property-tested"),
    ):
        d, _ = empirical_run(kind, required, threshold)
        rule = {"BLOCK-EVIDENCE-BELOW-REQUIRED", "DEFAULT-NO-RULE-DECIDES"}
        check(d["result"] == want and (want == "accepted" or {v["rule_id"] for v in d["violations"]} <= rule)
              and (want == "accepted" or [v["obligation_id"] for v in d["violations"]] == ["OBL-0007"]),
              f"{kind} passed, required {required}, threshold {threshold}: {want} ({why})", f"    {d}")
    d, _ = empirical_run("property-tested", "kernel-proven", "property-tested")
    check(rule_ids(d) == ["BLOCK-EVIDENCE-BELOW-REQUIRED"], "a set requiring a proof blocks empirical evidence by name")
    d, _ = empirical_run("property-tested", "property-tested", "machine-checked-model")
    check(rule_ids(d) == ["DEFAULT-NO-RULE-DECIDES"], "a policy demanding a proof leaves empirical evidence undecided: rejected by default")
    d, _ = empirical_run("llm-corroborated", "llm-corroborated", "human-asserted", model_identity={"name": "m", "version": "1"})
    check(rule_ids(d) == ["BLOCK-LLM-NOT-SUFFICIENT"], "a model's corroboration never accepts an obligation, whatever the policy says", f"    {d}")
    d, _ = empirical_run("human-asserted", "human-asserted", "human-asserted", waiver_id="WAIVER-7")
    check(rule_ids(d) == ["BLOCK-WAIVER-FORBIDDEN"],
          "a waiver is a suppression: a policy that forbids suppression refuses it, recorded or not", f"    {d}")
    d, _ = empirical_run("human-asserted", "human-asserted", "human-asserted", suppression="narrow-only")
    check(rule_ids(d) == ["BLOCK-WAIVER-MISSING"], "where suppression is allowed, a human assertion without a recorded waiver is blocked")
    d, _ = empirical_run("human-asserted", "human-asserted", "human-asserted", suppression="narrow-only", waiver_id="WAIVER-7")
    check(d["result"] == "accepted", "...and accepted with one, when the set and the policy allow it")
    doc = the_set()
    for o in doc["obligations"]:
        o["required_evidence_kind"] = "human-asserted"
    pol = accept_all_policy("human-asserted")
    pol["suppression"] = {"mode": "narrow-only", "requires_human_approval": True, "recorded_in_report": True, "max_active": 2}
    report = build_report(pol, doc, lambda r: [
        (e.update(outcome="passed"), e["evidence"].update(kind="human-asserted", soundness="none", waiver_id="W-" + e["obligation_id"]))
        for e in r["obligations"]])
    d = decide(pol, report, doc)
    check(d["result"] == "rejected" and violations_of(d) == [("BLOCK-WAIVER-LIMIT", "OBL-0009")],
          "max_active caps the waivers: the first two (by obligation_id) stand, the third is blocked", f"    {d}")
    pol["suppression"]["max_active"] = 0
    d = decide(pol, build_report(pol, doc, lambda r: [
        (e.update(outcome="passed"), e["evidence"].update(kind="human-asserted", soundness="none", waiver_id="W")) for e in r["obligations"]]), doc)
    check({v["rule_id"] for v in d["violations"]} == {"BLOCK-WAIVER-LIMIT"} and len(d["violations"]) == 3, "max_active 0 allows none")
    d, _ = empirical_run("kernel-proven", "kernel-proven", "kernel-proven", soundness="sound-for-fragment")
    check(rule_ids(d) == ["BLOCK-PASSED-WITHOUT-EMPIRICAL-EVIDENCE"], "'passed' with machine-checked evidence is blocked: it should say proven")
    d, report = empirical_run("property-tested", "property-tested", "property-tested")
    check(valid_report(report), "(setup) the accepted empirical report is valid for JSON Schema")

    heading("Rules: selection, order, actions")
    guarded = copy.deepcopy(legacy.good_policy)  # accept core kinds, escalate everything else
    d = decide(guarded, build_report(guarded, document), document)
    check(d["result"] == "needs-human" and d["violations"] == []
          and [(e["obligation_id"], e["requested_of"]) for e in d["escalations"]]
          == [("OBL-0009", "project-owner")] and d["rules_evaluated"] == 2,
          "escalate guard: the termination obligation is escalated by name to its target",
          f"    {d}")
    reject_first = copy.deepcopy(guarded)
    reject_first["rules"].insert(0, {"rule_id": "RULE-NO-TERMINATION",
                                     "applies_to": {"obligation_kinds": ["termination"]},
                                     "requires": {"min_evidence_kind": "kernel-proven"},
                                     "action": "reject"})
    scenario("reject guard fires when its threshold is not met", reject_first,
             build_report(reject_first, document, lambda r: entry(r, "OBL-0009")["evidence"].update(
                 kind="machine-checked-model", soundness="sound-for-model", semantic_gap="gap")),
             document, "rejected", ["RULE-NO-TERMINATION"], obligation="OBL-0009")
    scenario("reject guard passes when its threshold is met", reject_first,
             build_report(reject_first, document), document, "needs-human", [], ["RULE-INTENT-CORROBORATED"])
    swapped = copy.deepcopy(guarded)
    swapped["rules"].reverse()
    d = decide(swapped, build_report(swapped, document), document)
    check(d["result"] == "needs-human" and len(d["escalations"]) == 3,
          "rule order is meaning: the same rules reversed escalate everything", f"    {d}")
    path_policy = accept_all_policy()
    path_policy["rules"][0]["applies_to"] = {"paths": ["drain"], "obligation_kinds": ["postcondition"]}
    d = decide(path_policy, build_report(path_policy, document), document)
    check([v["obligation_id"] for v in d["violations"]] == ["OBL-0007", "OBL-0008", "OBL-0009"],
          "selectors are conjunctive: OBL-0007 has the kind but not the path, OBL-0009 the path "
          "but not the kind, so neither is selected", f"    {d}")
    only_paths = accept_all_policy()
    only_paths["rules"][0]["applies_to"] = {"paths": ["drain"]}
    d = decide(only_paths, build_report(only_paths, document), document)
    check([v["obligation_id"] for v in d["violations"]] == ["OBL-0007", "OBL-0008"],
          "paths match component prefixes of the set: only OBL-0009 touches drain.ts", f"    {d}")
    composed, decomposed = "caf\u00e9/", "cafe\u0301/"
    uni_set = the_set()
    uni_set["obligations"][0]["affected_components"] = [decomposed + "q.ts"]
    uni_policy = accept_all_policy()
    uni_policy["rules"][0]["applies_to"] = {"paths": [composed]}
    uni_policy2 = copy.deepcopy(uni_policy)
    uni_policy2["rules"][0]["applies_to"] = {"paths": [decomposed]}
    check(engine.policy_digest(uni_policy) == engine.policy_digest(uni_policy2),
          "(setup) the two spellings have the same policy digest")
    d1 = decide(uni_policy, build_report(uni_policy, uni_set), uni_set)
    d2 = decide(uni_policy2, build_report(uni_policy2, uni_set), uni_set)
    check(d1 == d2 and [v["obligation_id"] for v in d1["violations"]] == ["OBL-0008", "OBL-0009"],
          "composed and decomposed spellings of a path select the same obligations (same digest, "
          "same decision)", f"    {d1}\n    {d2}")
    nfc_dup = accept_all_policy()
    nfc_dup["rules"] = [{**nfc_dup["rules"][0], "rule_id": "R-caf\u00e9"},
                        {**nfc_dup["rules"][0], "rule_id": "R-cafe\u0301"}]
    scenario("rule ids equal only after NFC are duplicates", nfc_dup,
             build_report(nfc_dup, document), document, "rejected", ["INTEGRITY-POLICY-INVALID"])
    nothing = accept_all_policy()
    nothing["rules"][0]["applies_to"] = {"all_obligations": False}
    d = decide(nothing, build_report(nothing, document), document)
    check(d["result"] == "rejected" and len(d["violations"]) == 3,
          "all_obligations: false selects nothing", f"    {d}")
    mixed = copy.deepcopy(guarded)
    d = decide(mixed, build_report(mixed, document, lambda r: entry(r, "OBL-0007").update(
        outcome="failed", diagnostics=["x"])), document)
    check(d["result"] == "rejected" and len(d["violations"]) == 1 and len(d["escalations"]) == 1,
          "a violation outranks an escalation; both are reported", f"    {d}")

    heading("Trust")
    untrusted = accept_all_policy()
    untrusted["approval"] = "manual-attestation"
    d = decide(untrusted, build_report(untrusted, document, lambda r: r["trust"].update(
        policy_approval_trusted=False)), document)
    check(d["result"] == "needs-human" and d["escalations"][0]["reason"].startswith(
        "TRUST-POLICY-APPROVAL") and d["escalations"][0]["requested_of"] == untrusted["approved_by"],
        "an untrusted policy approval caps the result at needs-human", f"    {d}")


# ------------------------------------------------------------------ lattice, digest

def check_lattice_and_digest():
    heading("Evidence lattice")
    report_kinds = REPORT_SCHEMA["$defs"]["evidence"]["properties"]["kind"]["enum"]
    policy_kinds = POLICY_SCHEMA["$defs"]["rule"]["properties"]["requires"]["properties"][
        "min_evidence_kind"]["enum"]
    check(set(engine.EVIDENCE_LATTICE) == set(report_kinds) == set(policy_kinds)
          and len(engine.EVIDENCE_LATTICE) == len(engine.RANK),
          "the lattice names exactly the kinds of both schemas")
    check(report_kinds != policy_kinds, "(setup) the two schemas order the kinds differently")
    check([engine.RANK[k] for k in policy_kinds] == sorted(engine.RANK[k] for k in policy_kinds)
          and [engine.RANK[k] for k in report_kinds][::-1] == [engine.RANK[k] for k in policy_kinds],
          "ranking follows strength whichever way a schema's enum is ordered")
    check(engine.EVIDENCE_LATTICE == ("human-asserted", "llm-corroborated", "runtime-asserted",
                                      "property-tested", "machine-checked-model", "kernel-proven"),
          "the lattice is the one in docs/architecture/verification-engine.md")

    heading("Policy digest")
    policy = legacy.good_policy
    base = engine.policy_digest(policy)
    check(not validate.validate(policy, POLICY_SCHEMA), "(setup) good policy is valid")
    check(base == "sha256:21e706171e4f868db512630d87d1da26526bcce98a4251fec12abacd68b39846",
          "the example policy has its pinned digest (recomputed independently with hashlib)", f"    {base}")
    reordered = json.loads(json.dumps(policy, sort_keys=True), object_pairs_hook=lambda p: dict(reversed(p)))
    check(engine.policy_digest(reordered) == base, "digest ignores key order and formatting")
    floaty = copy.deepcopy(policy)
    floaty["cost_budget"]["fast_wall_ms"] = 2000.0
    check(engine.policy_digest(floaty) == base, "digest treats 2000.0 as 2000")
    for label, fn in (("an edit", lambda p: p.update(approved_by="someone-else")),
                      ("a rule threshold", lambda p: p["rules"][0]["requires"].update(
                          min_evidence_kind="machine-checked-model")),
                      ("rule order", lambda p: p["rules"].reverse())):
        changed = copy.deepcopy(policy)
        fn(changed)
        check(engine.policy_digest(changed) != base, f"digest changes with {label}")


# ------------------------------------------------------------------ safety, exhaustive

def safe_acceptance(outcome, kind, waiver, required, mode):
    """The only ways an obligation may be accepted, written down independently of the engine."""
    if engine.RANK[kind] < engine.RANK[required]:
        return False
    if outcome == "proven":
        return kind in ("kernel-proven", "machine-checked-model")
    if outcome == "passed":
        return kind in ("runtime-asserted", "property-tested") or (kind == "human-asserted" and bool(waiver) and mode == "narrow-only")
    return False


def check_exhaustive_safety():
    heading("Safety, exhaustively: acceptance never outruns the evidence")
    outcomes = ("proven", "passed", "failed", "timeout", "not-run", "unknown", "novel")
    kinds = engine.EVIDENCE_LATTICE
    actions = ("accept", "reject", "escalate")
    thresholds = (None, *engine.EVIDENCE_LATTICE)
    runs = accepted = unsafe = invalid = nondeterministic = 0
    for required in kinds:
        document = the_set()
        document["obligations"] = document["obligations"][:1]
        document["obligations"][0]["required_evidence_kind"] = required
        for action, threshold, on_unknown, on_timeout, mode in itertools.product(
                actions, thresholds, ("reject", "escalate"), ("reject", "escalate"), ("forbidden", "narrow-only")):
            policy = accept_all_policy()
            policy["suppression"] = {"mode": mode, "requires_human_approval": True, "recorded_in_report": True}
            rule = policy["rules"][0]
            rule["action"] = action
            rule["requires"] = {"min_evidence_kind": threshold} if threshold else {
                "non_empty_assumptions": False}
            if action == "escalate":
                rule["escalate_to"] = "owner"
            policy["on_unknown_evidence"], policy["on_timeout"] = on_unknown, on_timeout
            base = build_report(policy, document)
            variants = [(o, k, None) for o in outcomes for k in kinds]
            variants += [(o, "human-asserted", "WAIVER-1") for o in outcomes]
            for outcome, kind, waiver in variants:
                report = copy.deepcopy(base)
                report["obligations"][0]["outcome"] = outcome
                report["obligations"][0]["evidence"]["kind"] = kind
                if waiver:
                    report["obligations"][0]["evidence"]["waiver_id"] = waiver
                d = decide(policy, report, document)
                runs += 1
                nondeterministic += d != decide(policy, report, document)
                invalid += bool(validate.validate(d, DECISION_SCHEMA))
                if d["result"] == "accepted":
                    accepted += 1
                    unsafe += not safe_acceptance(outcome, kind, waiver, required, mode)
                if outcome == "failed" and d["result"] != "rejected":
                    unsafe += 1
    check(runs == 6 * 3 * 7 * 2 * 2 * 2 * 7 * 7, f"enumerated {runs} decisions")
    check(unsafe == 0, "no unsafe acceptance and no failed obligation ever escapes rejection",
          f"    {unsafe} unsafe")
    check(invalid == 0, "every decision is valid for the decision schema", f"    {invalid} invalid")
    check(nondeterministic == 0, "every decision is identical when recomputed")
    check(0 < accepted < runs, f"acceptance is reachable ({accepted} of {runs}), so the check is not vacuous")


# ------------------------------------------------------------------ determinism, purity

def shuffle_keys(value, rng):
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {k: shuffle_keys(v, rng) for k, v in items}
    if isinstance(value, list):
        return [shuffle_keys(v, rng) for v in value]
    return value


def check_determinism():
    heading("Determinism, replayability, purity")
    document, policy = the_set(), copy.deepcopy(legacy.good_policy)
    report = build_report(policy, document)
    inputs = (policy, report, document)
    frozen = json.dumps(inputs, sort_keys=True)
    first = engine.canonical_decision(decide(*inputs))
    check(engine.canonical_decision(decide(*inputs)) == first, "two runs give identical bytes")
    check(json.dumps(inputs, sort_keys=True) == frozen, "inputs are not mutated")

    rng = random.Random(7)
    for i in range(20):
        shuffled_policy = shuffle_keys(policy, rng)  # arrays untouched: rule order is meaning
        shuffled_report = shuffle_keys(report, rng)
        shuffled_report["obligations"].reverse() if i % 2 else None
        shuffled_set = shuffle_keys(document, rng)
        shuffled_set["obligations"].reverse() if i % 3 else None
        for o in shuffled_set["obligations"]:
            o["assumptions"].reverse()
        if engine.canonical_decision(decide(shuffled_policy, shuffled_report, shuffled_set)) != first:
            check(False, "decision independent of key and array order of the inputs",
                  f"    differs on shuffle {i}")
            break
    else:
        check(True, "decision independent of key order, report order and set array order (20 shuffles)")

    def forbidden(*args, **kwargs):
        raise AssertionError("decide() touched the outside world")
    with mock.patch("builtins.open", forbidden), mock.patch.object(socket, "socket", forbidden), \
            mock.patch.object(subprocess, "Popen", forbidden), mock.patch.object(time, "time", forbidden), \
            mock.patch.object(time, "monotonic", forbidden), mock.patch.object(random, "random", forbidden), \
            mock.patch.object(os, "urandom", forbidden), mock.patch.object(os, "listdir", forbidden), \
            mock.patch.object(Path, "read_text", forbidden), mock.patch.object(os, "stat", forbidden):
        try:
            same = engine.canonical_decision(engine.decide(*inputs)) == first  # validating path
            check(same, "decide() uses no file, socket, process, clock or randomness")
        except AssertionError as exc:
            check(False, "decide() uses no file, socket, process, clock or randomness", f"    {exc}")

    with tempfile.TemporaryDirectory(prefix="selyth-engine-") as tmp:
        paths = []
        for name, doc in (("policy", policy), ("report", report), ("set", document)):
            path = Path(tmp) / f"{name}.json"
            path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            paths.append(str(path))
        outputs = set()
        for seed in ("0", "1", "4242", "random"):
            run = subprocess.run([sys.executable, str(TOOLS / "policy_engine.py"), "decide", *paths],
                                 capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed})
            outputs.add((run.returncode, run.stdout))
        check(outputs == {(3, first + "\n")}, "CLI output is byte-identical across processes and hash seeds",
              f"    {outputs}")

    heading("Replay")
    for label, policy_, edit, result in (
            ("accepted", accept_all_policy(), None, "accepted"),
            ("needs-human", policy, None, "needs-human"),
            ("rejected", policy, lambda r: entry(r, "OBL-0008").update(
                outcome="failed", diagnostics=["x"]), "rejected")):
        rep = build_report(policy_, document, edit)
        rep["decision"] = decide(policy_, rep, document)
        check(rep["decision"]["result"] == result and not validate.validate(rep, REPORT_SCHEMA),
              f"a {result} report embedding the engine's decision is valid for JSON Schema",
              f"    {validate.validate(rep, REPORT_SCHEMA)}")
        check(engine.replay(policy_, rep, document), f"the {result} decision is reproduced")
        forged = copy.deepcopy(rep)
        forged["decision"]["violations"] = [{"rule_id": "X", "message": "forged"}]
        forged["decision"]["result"] = "rejected"
        check(not engine.replay(policy_, forged, document),
              f"a forged decision on the {result} report is not reproduced")
    claimed = build_report(policy, document)
    claimed["decision"].update(result="accepted")
    check(decide(policy, claimed, document)["result"] == "needs-human",
          "a report claiming 'accepted' does not make the engine accept")

    with tempfile.TemporaryDirectory(prefix="selyth-engine-") as tmp:
        def write(name, doc):
            path = Path(tmp) / name
            path.write_text(json.dumps(doc), encoding="utf-8")
            return str(path)

        def cli(*args):
            return subprocess.run([sys.executable, str(TOOLS / "policy_engine.py"), *args],
                                  capture_output=True, text=True).returncode
        ok_policy = accept_all_policy()
        pol, rep, doc = (write("p.json", ok_policy), write("r.json", build_report(ok_policy, document)),
                         write("s.json", document))
        guard = write("g.json", policy)
        check(cli("decide", pol, rep, doc) == 0, "CLI: accepted exits 0")
        check(cli("decide", guard, write("rg.json", build_report(policy, document)), doc) == 3,
              "CLI: needs-human exits 3")
        check(cli("decide", pol, write("ro.json", build_report(ok_policy, document,
              lambda r: r["obligations"].pop())), doc) == 1, "CLI: rejected exits 1")
        check(cli("decide", pol, write("bad.json", {"not": "a report"}), doc) == 2,
              "CLI: an invalid report exits 2")
        embedded = build_report(ok_policy, document)
        embedded["decision"] = decide(ok_policy, embedded, document)
        check(cli("replay", pol, write("re.json", embedded), doc) == 0,
              "CLI: replay exits 0 when the recorded decision is reproduced")
        embedded["decision"]["rules_evaluated"] += 1
        check(cli("replay", pol, write("rf.json", embedded), doc) == 1,
              "CLI: replay exits 1 when it is not")
        check(cli("policy-digest", pol) == 0, "CLI: policy-digest exits 0")


def check_lock():
    heading("The lock: policy and obligations approved outside the agent's reach")
    document, policy = the_set(), accept_all_policy()
    lock = engine.make_lock(policy, document, "project-owner")
    lock_schema = validate.load_schema(ROOT / "docs" / "specifications" / "lock-schema.json")
    check(not validate.validate(lock, lock_schema), "a generated lock is valid for lock-schema.json")
    report = build_report(policy, document)
    check(decide(policy, report, document, lock)["result"] == "accepted", "a coherent run under its lock is accepted")
    check(decide(policy, report, document) == decide(policy, report, document, None),
          "without a lock nothing changes (backward compatible)")

    # An agent that edits the policy regenerates a report pinned to its own edit.
    weaker = copy.deepcopy(policy)
    weaker["on_timeout"] = "escalate"
    forged = build_report(weaker, document, lambda r: entry(r, "OBL-0008").update(outcome="timeout"))
    check(decide(weaker, forged, document)["result"] == "needs-human",
          "(setup) without a lock, the weakened policy and its own report are accepted as coherent: "
          "a timeout becomes an escalation instead of a rejection")
    d = decide(weaker, forged, document, lock)
    check(d["result"] == "rejected" and rule_ids(d) == ["INTEGRITY-POLICY-NOT-APPROVED"]
          and engine.policy_digest(weaker) in d["violations"][0]["message"] and lock["policy_digest"] in d["violations"][0]["message"],
          "with the lock, the same pair is rejected by digest mismatch, naming both digests", f"    {d}")

    smaller = copy.deepcopy(document)
    smaller["obligations"].pop()
    d = decide(policy, build_report(policy, smaller), smaller, lock)
    check(d["result"] == "rejected" and rule_ids(d) == ["INTEGRITY-OBLIGATION-SET-NOT-FROZEN"],
          "dropping an obligation, with a report regenerated for the smaller set, is rejected", f"    {d}")
    edited = copy.deepcopy(document)
    edited["obligations"][1]["statement"] = "invariant true"
    d = decide(policy, build_report(policy, edited), edited, lock)
    check(d["result"] == "rejected" and rule_ids(d) == ["INTEGRITY-OBLIGATION-SET-NOT-FROZEN"],
          "weakening an obligation to `invariant true` is rejected", f"    {d}")
    both = decide(weaker, build_report(weaker, edited), edited, lock)
    check(rule_ids(both) == ["INTEGRITY-POLICY-NOT-APPROVED", "INTEGRITY-OBLIGATION-SET-NOT-FROZEN"],
          "both edits are reported", f"    {both}")

    reformatted = json.loads(json.dumps(policy, indent=7, sort_keys=True))
    reformatted["cost_budget"]["fast_wall_ms"] = 2000.0
    check(decide(reformatted, build_report(reformatted, document), document, lock)["result"] == "accepted",
          "reformatting or reordering keys is not an edit: same digest, still accepted")
    reordered = copy.deepcopy(document)
    reordered["obligations"].reverse()
    check(decide(policy, build_report(policy, reordered), reordered, lock)["result"] == "accepted",
          "reordering the Obligation Set is not an edit either")

    forged_lock = engine.make_lock(weaker, edited, "agent")
    check(decide(weaker, build_report(weaker, edited), edited, forged_lock)["result"] == "accepted",
          "KNOWN LIMIT: an agent that also rewrites the lock is not detected; the lock is protected "
          "procedurally (signed commit, protected path), not by Core")
    check(replay_with_lock(policy, document, lock), "replay with the lock reproduces the recorded decision")

    with tempfile.TemporaryDirectory(prefix="selyth-lock-") as tmp:
        def write(name, doc):
            path = Path(tmp) / name
            path.write_text(json.dumps(doc), encoding="utf-8")
            return str(path)

        def cli(*args):
            return subprocess.run([sys.executable, str(TOOLS / "policy_engine.py"), *args],
                                  capture_output=True, text=True)
        pol, rep, doc = write("p.json", policy), write("r.json", build_report(policy, document)), write("s.json", document)
        made = cli("lock", pol, doc, "--approved-by", "project-owner")
        check(made.returncode == 0 and json.loads(made.stdout) == lock, "CLI: `lock` prints the lock for a policy and a set")
        lk = write("lock.json", lock)
        check(cli("decide", pol, rep, doc, "--lock", lk).returncode == 0, "CLI: decide --lock accepts the approved run")
        wp = write("wp.json", weaker)
        wr = write("wr.json", build_report(weaker, document))
        run = cli("decide", wp, wr, doc, "--lock", lk)
        check(run.returncode == 1 and "INTEGRITY-POLICY-NOT-APPROVED" in run.stdout,
              "CLI: decide --lock rejects the weakened policy (exit 1)", run.stdout)
        check(cli("decide", wp, wr, doc).returncode == 0, "CLI: without --lock the same weakened pair is accepted")
        check(cli("decide", pol, rep, doc, "--lock", write("bad.json", {"policy_digest": "x"})).returncode == 2,
              "CLI: an invalid lock exits 2")
        check(cli("lock", pol, doc).returncode == 2, "CLI: `lock` needs --approved-by")
        broken = copy.deepcopy(policy)
        del broken["title"]
        run = cli("lock", write("broken-policy.json", broken), doc, "--approved-by", "project-owner")
        check(run.returncode == 2 and "policy_digest" not in run.stdout and "not valid" in run.stdout + run.stderr,
              "CLI: `lock` refuses a policy that violates policy-schema.json and prints no lock",
              f"    exit {run.returncode}: {run.stdout!r} {run.stderr!r}")
        invalid_set = copy.deepcopy(document)
        invalid_set["obligations"] = []
        check(cli("lock", pol, write("empty-set.json", invalid_set), "--approved-by", "project-owner").returncode == 2,
              "CLI: `lock` refuses an invalid Obligation Set too")


def replay_with_lock(policy, document, lock):
    """Replay is lock-aware: a decision recorded under a lock is only reproduced with that lock."""
    weaker = copy.deepcopy(policy)
    weaker["on_timeout"] = "escalate"
    report = build_report(weaker, document)
    report["decision"] = decide(weaker, report, document, lock)  # rejected: policy not approved
    return (report["decision"]["result"] == "rejected" and engine.replay(weaker, report, document, lock)
            and not engine.replay(weaker, report, document))


def check_input_validation():
    heading("The engine is total: what the schemas reject, the engine rejects (core-v0.1.md, M4)")
    tv = _load("test_validation", "test-validation.py")
    document, policy = the_set(), copy.deepcopy(legacy.good_policy)
    coherent = build_report(policy, document)
    replayed = 0
    for name, schema_path, instance, must_pass, _why in legacy.CASES:
        is_report = schema_path == legacy.REPORT
        d = engine.decide(policy, instance, document) if is_report else engine.decide(instance, coherent, document)
        invalid = [v["message"] for v in d["violations"] if v["rule_id"] == "INTEGRITY-INPUT-INVALID"]
        if must_pass:
            check(not invalid, f"{name}: the valid fixture is not flagged by the engine", f"    {invalid}")
            continue
        path, keyword, *_ = tv.LEGACY_REASONS[name]
        what = "report" if is_report else "policy"
        hit = [m for m in invalid if m.startswith(what + ":") and f"{path}:" in m and f"({keyword})" in m]
        replayed += bool(hit)
        check(d["result"] == "rejected" and bool(hit) and d["rules_evaluated"] == 0 and not validate.validate(d, DECISION_SCHEMA),
              f"{name}: rejected by the engine itself, naming {path} ({keyword})", f"    {d}")
    check(replayed == 21, "all 21 legacy rejections are replayed through the engine and rejected for the intended rule",
          f"    {replayed} of 21")

    heading("Totality: any JSON gives a decision, never an exception, never an acceptance")
    garbage = [None, True, 0, 7, 1.5, -1e308, "text", "", [], [None], {}, {"a": {"b": [1, 2, {"c": None}]}},
               {"rules": "x"}, [[[[[[[[[[1]]]]]]]]]], {"policy_digest": 1}, 10 ** 400]
    bad = tried = 0
    for position in range(4):
        for g in garbage:
            if position == 3 and g is None:
                continue  # no lock is a legitimate input
            tried += 1
            args = [copy.deepcopy(policy), copy.deepcopy(coherent), copy.deepcopy(document), None]
            if position == 3:
                args[3] = g
            else:
                args[position] = g
            try:
                d = engine.decide(*args)
            except Exception as exc:  # noqa: BLE001 - the point is that none escapes
                check(False, f"decide raised on garbage at argument {position}: {g!r}", f"    {exc!r}")
                bad += 1
                continue
            bad += not (d["result"] == "rejected" and not validate.validate(d, DECISION_SCHEMA))
    check(bad == 0 and tried == 63, f"{tried} garbage inputs, each rejected with a valid decision", f"    {bad} failures")
    for label, mutate_set in (("two obligations share an obligation_id", lambda d: d["obligations"][1].update(obligation_id="OBL-0007")),
                              ("assumptions equal only after NFC", lambda d: d["obligations"][0].update(assumptions=["caf\u00e9", "cafe\u0301"]))):
        broken_set = copy.deepcopy(document)
        mutate_set(broken_set)
        try:
            d = engine.decide(policy, coherent, broken_set)
            check(d["result"] == "rejected" and rule_ids(d) == ["INTEGRITY-INPUT-INVALID"] and "obligation set" in d["violations"][0]["message"],
                  f"an Obligation Set the schema accepts but the canonical form refuses ({label}) is rejected, not a crash", f"    {d}")
        except Exception as exc:  # noqa: BLE001
            check(False, f"decide raised on an Obligation Set where {label}", f"    {exc!r}")
    fractional = copy.deepcopy(policy)
    fractional["cost_budget"]["fast_wall_ms"] = 1.5
    d = engine.decide(fractional, build_report(policy, document), document)
    check(d["result"] == "rejected" and rule_ids(d)[0] == "INTEGRITY-INPUT-INVALID" and d["policy_digest"].startswith("sha256:"),
          "a policy whose digest cannot be computed is rejected, with a well-formed digest field", f"    {d}")
    empty_lock = engine.decide(policy, coherent, document, {})
    check(empty_lock["result"] == "rejected" and "lock:" in empty_lock["violations"][0]["message"],
          "an empty lock is invalid, not 'no lock' (it must not silently disable the check)", f"    {empty_lock}")
    check(engine.replay(policy, "not a report", document) is False and engine.replay(policy, [], document) is False,
          "replay of a non-report is False, not an exception")

    heading("For valid documents the validating path decides exactly as the semantic core")
    other = copy.deepcopy(legacy.good_policy)
    other["rules"].reverse()
    for label, pol in (("accept-all policy", accept_all_policy()), ("guarded policy", copy.deepcopy(legacy.good_policy)), ("reversed rules", other)):
        rep = build_report(pol, document)
        check(engine.decide(pol, rep, document) == decide(pol, rep, document) and valid_report(rep),
              f"{label}: same decision with and without the schema check")


def main():
    check_scenarios()
    check_lattice_and_digest()
    check_exhaustive_safety()
    check_determinism()
    check_lock()
    check_input_validation()
    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
