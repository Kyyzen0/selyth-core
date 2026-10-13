#!/usr/bin/env python3
"""The negative scenarios of core-v0.1.md (milestone M5), end to end with a real Dafny.

  7. an intentionally incorrect implementation that passes every pre-existing check;
  8. attempts to weaken the verification: edit the policy, edit or drop an obligation;
  9. tests asserting that the gate rejects them, naming the rule and the obligation.

The question behind them is the one the whole project exists for: does the evidence
and policy layer change the decision a competent person would reach by running the
prover directly? Each scenario states what plain Dafny says and what Core decides.

Run with `make test`, or `python tools/test-scenarios.py` in the pinned environment.
"""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import dafny_backend as backend
import obligation_set as oset
import policy_engine as engine
import ts_adapter as adapter
import validate

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


spec = importlib.util.spec_from_file_location("test_dafny_backend", TOOLS / "test-dafny-backend.py")
tdb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tdb)
osets = tdb.osets
POLICY = tdb.POLICY
GOOD_WORKSPACE = tdb.WORKSPACE.read_text(encoding="utf-8")
# The incorrect change: an off-by-one in the loop guard. It compiles, it terminates, and the
# hand-written contract still verifies; only the stated invariant 0 <= size <= capacity breaks.
BUGGY_WORKSPACE = GOOD_WORKSPACE.replace("while size < capacity", "while size <= capacity")
assert BUGGY_WORKSPACE != GOOD_WORKSPACE


def plain_dafny(dafny, solver, workspace):
    """What a developer would run with no Selyth: Dafny on the workspace, no obligation attached."""
    source = backend.instantiate(workspace, "no-obligation", "")
    with tempfile.TemporaryDirectory(prefix="selyth-plain-") as tmp:
        path = Path(tmp) / "workspace.dfy"
        path.write_text(source, encoding="utf-8")
        run = subprocess.run([str(dafny), "verify", "--solver-path", str(solver), str(path)],
                             capture_output=True, text=True)
    return run.returncode, run.stdout


def gate(report, policy, document, lock):
    """The decision a CI gate would reach on a report: the engine, with the approved lock."""
    return engine.decide(policy, report, document, lock)


def rules(decision):
    return [(v["rule_id"], v["obligation_id"]) for v in decision["violations"]]


def agent_report(base, policy, document):
    """The report an agent can produce for a policy it edited: coherent with its own edit."""
    report = copy.deepcopy(base)
    digest = engine.policy_digest(policy)
    report["policy"].update(policy_id=policy["policy_id"], policy_version=policy["policy_version"],
                            policy_digest=digest, approved_by=policy["approved_by"], approval=policy["approval"])
    report["decision"]["policy_digest"] = digest
    report["decision"] = engine.decide(policy, report, document)
    return report


def scenarios(dafny, solver, version):
    document = osets.demo()
    lock = engine.make_lock(POLICY, document, "project-owner")

    heading("Scenario 0 — the correct implementation is accepted")
    base = tdb.pipeline(dafny, solver, version, lock=lock)
    check(base["decision"]["result"] == "accepted" and set(tdb.outcomes(base).values()) == {"proven"},
          "all obligations proven, accepted under the lock", f"    {base['decision']}")

    heading("Scenario 7 — an incorrect implementation that passes every pre-existing check")
    code, output = plain_dafny(dafny, solver, BUGGY_WORKSPACE)
    check(code == 0 and "0 errors" in output,
          "(setup) plain Dafny on the buggy workspace: exit 0, 0 errors; the pre-existing checks pass",
          f"    exit {code}: {output[-200:]}")
    report = tdb.pipeline(dafny, solver, version, workspace=BUGGY_WORKSPACE, lock=lock)
    check(tdb.valid(report), "the report for the buggy implementation is valid")
    check(tdb.outcomes(report) == {"OBL-0007": "proven", "OBL-0008": "failed", "OBL-0009": "proven"},
          "only the violated obligation fails; the others are still proven", f"    {tdb.outcomes(report)}")
    failed = next(o for o in report["obligations"] if o["obligation_id"] == "OBL-0008")
    check(any("invariant" in d for d in failed["diagnostics"]) and any("workspace" in d for d in failed["diagnostics"]),
          "Dafny's diagnostic names the invariant and points into the workspace", f"    {failed['diagnostics']}")
    d = report["decision"]
    check(d["result"] == "rejected" and rules(d) == [("BLOCK-FAILED", "OBL-0008")],
          "REJECTED, naming the rule BLOCK-FAILED and the obligation OBL-0008 (the milestone's criterion)", f"    {d}")
    check(engine.replay(POLICY, report, document, lock), "the recorded decision is reproduced by replay")

    heading("Scenario 8a — the agent weakens the obligation it violates")
    weakened = copy.deepcopy(document)
    weakened["obligations"][1]["statement"] = "invariant true"
    report = tdb.pipeline(dafny, solver, version, document=weakened, workspace=BUGGY_WORKSPACE)
    check(set(tdb.outcomes(report).values()) == {"proven"} and report["decision"]["result"] == "accepted",
          "(setup) without the lock the buggy implementation is accepted under the weakened obligation: "
          "Dafny proves `invariant true`, and every digest in the report is the weakened set's own",
          f"    {tdb.outcomes(report)} {report['decision']['result']}")
    d = gate(report, POLICY, weakened, lock)
    check(d["result"] == "rejected" and rules(d) == [("INTEGRITY-OBLIGATION-SET-NOT-FROZEN", None)],
          "with the lock it is REJECTED: the Obligation Set no longer has the approved digest", f"    {d}")
    check(lock["obligation_set_digest"] in d["violations"][0]["message"]
          and oset.set_digest(weakened) in d["violations"][0]["message"], "naming the approved and the actual digest")

    heading("Scenario 8b — the agent deletes the obligation it violates")
    narrowed = copy.deepcopy(document)
    narrowed["obligations"] = [o for o in narrowed["obligations"] if o["obligation_id"] != "OBL-0008"]
    report = tdb.pipeline(dafny, solver, version, document=narrowed, workspace=BUGGY_WORKSPACE)
    check(report["decision"]["result"] == "accepted" and "OBL-0008" not in tdb.outcomes(report),
          "(setup) without the lock the buggy implementation is accepted once its obligation is gone")
    d = gate(report, POLICY, narrowed, lock)
    check(d["result"] == "rejected" and rules(d) == [("INTEGRITY-OBLIGATION-SET-NOT-FROZEN", None)],
          "with the lock it is REJECTED by digest mismatch", f"    {d}")
    stale = tdb.pipeline(dafny, solver, version, workspace=BUGGY_WORKSPACE)
    d = gate(stale, POLICY, narrowed, None)
    check(d["result"] == "rejected" and {r for r, _ in rules(d)} == {"INTEGRITY-COVERAGE"},
          "and even without the lock, a report that still lists the deleted obligation fails coverage", f"    {rules(d)}")

    heading("Scenario 8c — the agent weakens the policy")
    timed_out = tdb.pipeline(dafny, solver, version, resource_limit=1, lock=lock)
    check(timed_out["decision"]["result"] == "rejected"
          and {r for r, _ in rules(timed_out["decision"])} == {"BLOCK-TIMEOUT"},
          "(setup) under the approved policy, proofs that run out of resource are rejected (BLOCK-TIMEOUT)")

    def softer_timeout(p):
        p["on_timeout"] = "escalate"
        p["escalation"] = {"requested_of": "agent-bot"}

    def permissive_rule(p):
        p["rules"].insert(0, {"rule_id": "RULE-ANYTHING", "applies_to": {"all_obligations": True},
                              "requires": {"min_evidence_kind": "human-asserted"}, "action": "accept"})

    edits = [("on_timeout reject -> escalate", softer_timeout),
             ("a permissive accept rule inserted first", permissive_rule),
             ("the threshold lowered to human-asserted",
              lambda p: p["rules"][0]["requires"].update(min_evidence_kind="human-asserted")),
             ("approved_by changed to the agent", lambda p: p.update(approved_by="agent-bot")),
             ("an extra accept rule appended",
              lambda p: p["rules"].append({"rule_id": "RULE-EXTRA", "applies_to": {"all_obligations": True},
                                           "requires": {"min_evidence_kind": "kernel-proven"}, "action": "accept"}))]
    for label, edit in edits:
        weaker = copy.deepcopy(POLICY)
        edit(weaker)
        forged = agent_report(timed_out, weaker, document)
        coherent = gate(forged, weaker, document, None)
        d = gate(forged, weaker, document, lock)
        check(d["result"] == "rejected" and rules(d) == [("INTEGRITY-POLICY-NOT-APPROVED", None)]
              and engine.policy_digest(weaker) != lock["policy_digest"],
              f"policy edit `{label}`: REJECTED by digest mismatch with the lock "
              f"(without it the pair would be decided {coherent['result']})", f"    {d}")
        unregenerated = gate(timed_out, weaker, document, None)
        check({r for r, _ in rules(unregenerated)} >= {"INTEGRITY-POLICY-NOT-PINNED"},
              f"policy edit `{label}`: and an edit after the report was produced is caught by the report's own pin",
              f"    {rules(unregenerated)}")

    reformatted = json.loads(json.dumps(POLICY, indent=6, sort_keys=True))
    check(gate(tdb.pipeline(dafny, solver, version, policy=reformatted, lock=lock), reformatted, document, lock)["result"] == "accepted",
          "a reformatted, key-reordered policy is the same policy: still accepted")

    heading("Scenario 9 — the rejections hold end to end through the command line")
    with tempfile.TemporaryDirectory(prefix="selyth-scn-") as tmp:
        def write(name, doc=None, text=None):
            path = Path(tmp) / name
            path.write_text(text if text is not None else json.dumps(doc), encoding="utf-8")
            return str(path)
        lk, pol = write("lock.json", lock), write("policy.json", POLICY)
        bug = write("buggy.dfy", text=BUGGY_WORKSPACE)
        good_set, weak_set = write("set.json", document), write("weak.json", weakened)

        def verify(set_path, workspace):
            document_ = json.loads(Path(set_path).read_text())
            ir = write("ir.json", adapter.lower(document_, version))
            return subprocess.run([sys.executable, str(TOOLS / "dafny_backend.py"), "verify", "--ir", ir,
                                   "--workspace", workspace, "--obligation-set", set_path, "--policy", pol,
                                   "--lock", lk, "--dafny", str(dafny), "--produced-at", tdb.PRODUCED_AT],
                                  capture_output=True, text=True)
        run = verify(good_set, bug)
        out = json.loads(run.stdout)
        check(run.returncode == 1 and rules(out["decision"]) == [("BLOCK-FAILED", "OBL-0008")],
              "the buggy implementation: exit 1, BLOCK-FAILED on OBL-0008")
        run = verify(weak_set, bug)
        out = json.loads(run.stdout)
        check(run.returncode == 1 and rules(out["decision"]) == [("INTEGRITY-OBLIGATION-SET-NOT-FROZEN", None)],
              "the weakened obligation: exit 1, INTEGRITY-OBLIGATION-SET-NOT-FROZEN")
        good = verify(good_set, write("good.dfy", text=GOOD_WORKSPACE))
        check(good.returncode == 0, "and the correct implementation under the same lock: exit 0")

    heading("What the lock does not do")
    lock_forged = engine.make_lock(POLICY, weakened, "agent-bot")
    report = tdb.pipeline(dafny, solver, version, document=weakened, workspace=BUGGY_WORKSPACE, lock=lock_forged)
    check(report["decision"]["result"] == "accepted",
          "KNOWN LIMIT (failure mode F3): an agent that also rewrites the lock gets the weakened run accepted; "
          "the lock is protected by a signed commit or a protected path, which Core records but cannot enforce")


def main():
    try:
        dafny = tdb.locate_dafny()
        version, solver = backend.tool_versions(dafny)
    except (backend.BackendError, OSError) as exc:
        check(False, "Dafny is available", f"    {exc}")
        version = None
    if version is not None:
        scenarios(dafny, solver, version)
    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
