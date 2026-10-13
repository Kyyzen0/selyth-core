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


def entry(report, oid):
    return next(e for e in report["obligations"] if e["obligation_id"] == oid)


def decide(policy, report, document):
    return engine.decide(policy, report, document)


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
             ["INTEGRITY-COVERAGE"] * 2)

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
    scenario("the requirement comes from the set, not from the report's echo", policy,
             build_report(policy, strict, lambda r: (
                 entry(r, "OBL-0009").update(required_evidence_kind="human-asserted"),
                 entry(r, "OBL-0009")["evidence"].update(
                     kind="machine-checked-model", soundness="sound-for-model",
                     semantic_gap="gap"))), strict, "rejected",
             ["BLOCK-EVIDENCE-BELOW-REQUIRED"])
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

def check_exhaustive_safety():
    heading("Safety, exhaustively: acceptance never outruns the evidence")
    outcomes = ("proven", "failed", "timeout", "not-run", "unknown", "novel")
    kinds = engine.EVIDENCE_LATTICE
    actions = ("accept", "reject", "escalate")
    thresholds = (None, *engine.EVIDENCE_LATTICE)
    runs = accepted = unsafe = invalid = nondeterministic = 0
    for required in kinds:
        document = the_set()
        document["obligations"] = document["obligations"][:1]
        document["obligations"][0]["required_evidence_kind"] = required
        for action, threshold, on_unknown, on_timeout in itertools.product(
                actions, thresholds, ("reject", "escalate"), ("reject", "escalate")):
            policy = accept_all_policy()
            rule = policy["rules"][0]
            rule["action"] = action
            rule["requires"] = {"min_evidence_kind": threshold} if threshold else {
                "non_empty_assumptions": False}
            if action == "escalate":
                rule["escalate_to"] = "owner"
            policy["on_unknown_evidence"], policy["on_timeout"] = on_unknown, on_timeout
            base = build_report(policy, document)
            for outcome, kind in itertools.product(outcomes, kinds):
                report = copy.deepcopy(base)
                report["obligations"][0]["outcome"] = outcome
                report["obligations"][0]["evidence"]["kind"] = kind
                d = decide(policy, report, document)
                runs += 1
                nondeterministic += d != decide(policy, report, document)
                invalid += bool(validate.validate(d, DECISION_SCHEMA))
                if d["result"] == "accepted":
                    accepted += 1
                    unsafe += not (outcome == "proven" and kind in engine.MACHINE_CHECKED
                                   and engine.RANK[kind] >= engine.RANK[required])
                if outcome == "failed" and d["result"] != "rejected":
                    unsafe += 1
    check(runs == 6 * 3 * 7 * 2 * 2 * 6 * 6, f"enumerated {runs} decisions")
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
            same = engine.canonical_decision(decide(*inputs)) == first
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


def main():
    check_scenarios()
    check_lattice_and_digest()
    check_exhaustive_safety()
    check_determinism()
    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
