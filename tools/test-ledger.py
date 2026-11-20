#!/usr/bin/env python3
"""The real-project scenario of core-v0.1.md (element 6): twenty obligations on a small
money module (docs/specifications/examples/ledger/), every one verified by a real Dafny.

Beyond "they all prove", the tests ask whether the obligations are worth having:

  * mutation study: for each obligation, a bug in the workspace that it must catch. An
    obligation no bug can fail would pass for any implementation (the vacuity failure mode);
  * a bug written in the TypeScript itself, translated to Dafny by tools/ts_to_dafny.py, is rejected
    by the obligation it breaks (the proof model is generated from the code, not written beside it);
  * a precondition no caller exercises is not accepted as proven;
  * the whole run is accepted under the human-approved lock, and a regenerated, weakened
    set is not.

Run with `make test`, or `python tools/test-ledger.py` in the pinned environment.
"""
import copy
import importlib.util
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import dafny_backend as backend
import obligation_set as oset
import policy_engine as engine
import ts_adapter as adapter
import ts_to_dafny as t2d
import validate

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PROJECT = ROOT / "docs" / "specifications" / "examples" / "ledger"
FAILURES = []
PRODUCED_AT = "2026-10-13T12:00:00Z"
RESOURCE_LIMIT, TIME_LIMIT = 1_000_000, 60


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

SET = oset.load_obligation_set(PROJECT / "ledger.obligations.json")
POLICY = validate.load_json(PROJECT / "ledger.policy.json")
LOCK = validate.load_json(PROJECT / "ledger.lock.json")
WORKSPACE = (PROJECT / "ledger.workspace.dfy").read_text(encoding="utf-8")
NAME = "ledger.workspace.dfy"

# One bug per obligation, in the workspace, that the obligation must catch.
# (obligation, text in the workspace, replacement)
MUTANTS = [
    ("LED-01", "method Credit(balance: nat, amount: nat, cap: nat)\n  requires balance + amount <= cap",
     "method Credit(balance: nat, amount: nat, cap: nat)\n  requires true"),
    ("LED-02", "result := balance + amount;", "result := balance + amount - 1;"),
    ("LED-03", "result := balance + amount;", "result := cap + 1;"),
    ("LED-04", "method Spend(balance: nat, price: nat)\n  requires price <= balance",
     "method Spend(balance: nat, price: nat)\n  requires true"),
    ("LED-05", "result := balance - amount;", "result := balance - amount - 1;"),
    ("LED-06", "result := balance - amount;", "result := balance + amount;"),
    ("LED-07", "method Charge(amount: nat, bps: nat)\n  requires bps <= 10000",
     "method Charge(amount: nat, bps: nat)\n  requires true"),
    ("LED-08", "fee := amount * bps / 10000;", "fee := amount * bps / 100;"),
    ("LED-09", "fee := amount * bps / 10000;", "fee := amount * bps / 10000 + 1;"),
    ("LED-10", "method Limit(x: int, lo: int, hi: int)\n  requires lo <= hi",
     "method Limit(x: int, lo: int, hi: int)\n  requires true"),
    ("LED-11", "  if x < lo {\n    result := lo;\n    return;\n  }\n  if x > hi {\n    result := hi;\n    return;\n  }\n  result := x;",
     "  result := x;"),
    ("LED-12", "\n  result := x;\n}\n\nmethod Limit", "\n  result := lo;\n}\n\nmethod Limit"),
    ("LED-13", "method SplitBill(total: nat, people: nat)\n  requires people > 0",
     "method SplitBill(total: nat, people: nat)\n  requires true"),
    ("LED-14", "share, remainder := total / parts, total % parts;",
     "share, remainder := total / parts + 1, total % parts;"),
    ("LED-15", "share, remainder := total / parts, total % parts;",
     "share, remainder := total / parts, total % (parts + 1);"),
    ("LED-16", "while i < periods", "while i <= periods"),
    ("LED-17", "    i := i + 1;", "    i := i + 0;"),
    ("LED-18", "    total := total + step;", "    total := total + step + 1;"),
    ("LED-19", "    i := i + 1;\n  }\n}", "    i := i + 1;\n  }\n  total := total + 1;\n}"),
    ("LED-20", "fromAfter, toAfter := from - amount, to + amount;",
     "fromAfter, toAfter := from - amount, to + amount + 1;"),
]


def setup():
    dafny = tdb.locate_dafny()
    version, solver = backend.tool_versions(dafny)
    return dafny, solver, version


def build(dafny, solver, version, document=None, workspace=None, lock=LOCK):
    document = document or SET
    ir = adapter.lower(document, version)
    data = (workspace if workspace is not None else WORKSPACE).encode("utf-8")
    return backend.build_report(ir, document, POLICY, data, NAME, PRODUCED_AT, dafny, solver, version,
                                RESOURCE_LIMIT, TIME_LIMIT, lock)


def check_project(dafny, solver, version):
    heading("The module and its Obligation Set")
    kinds = Counter(o["kind"] for o in SET["obligations"])
    check(len(SET["obligations"]) == 20 and kinds == {"precondition": 5, "postcondition": 12, "invariant": 2, "termination": 1},
          "twenty obligations: 5 preconditions, 12 postconditions, 2 invariants, 1 termination", f"    {dict(kinds)}")
    check(all(o["required_evidence_kind"] == "kernel-proven" for o in SET["obligations"]), "every one requires kernel-proven evidence")
    check(LOCK == engine.make_lock(POLICY, SET, LOCK["approved_by"]),
          "the committed lock is the lock of this policy and this set (regenerate it when either is approved to change)")
    ir = adapter.lower(SET, version)
    check(len(ir["units"]) == 20 and not ir["unsupported"], "the adapter lowers all twenty; none is unsupported")

    heading("All twenty obligations are kernel-proven by a real Dafny")
    started = time.monotonic()
    report = build(dafny, solver, version)
    elapsed = time.monotonic() - started
    check(tdb.valid(report), "the report is valid for report-schema.json",
          "    " + "; ".join(f"{p.path} {p.message}" for p in validate.validate(report, tdb.REPORT_SCHEMA)))
    outcomes = {o["obligation_id"]: o["outcome"] for o in report["obligations"]}
    check(set(outcomes.values()) == {"proven"} and len(outcomes) == 20, "twenty obligations, all proven", f"    {outcomes}")
    check(all(o["evidence"]["kind"] == "kernel-proven" and o["evidence"]["soundness"] == "sound-for-fragment"
              for o in report["obligations"]), "as kernel-proven, sound-for-fragment")
    check(report["decision"]["result"] == "accepted" and engine.replay(POLICY, report, SET, LOCK),
          "the policy engine accepts the report under the lock, and replays its decision")
    check(oset.check_coverage(SET, report) == [], "the report covers the Obligation Set exactly")
    units = sum(o["evidence"]["cost"]["resource_units"] for o in report["obligations"])
    lines = WORKSPACE.count("\n") + 1
    print(f"[info] workspace {lines} lines, {len(SET['obligations'])} obligations; {units} resource units in "
          f"total, {elapsed:.0f} s wall: every obligation re-verifies the whole file, so the cost is "
          "obligations x file size")
    by_id = {o["obligation_id"]: o for o in report["obligations"]}
    check("LED-01 holds at every call site" in " ".join(by_id["LED-03"]["evidence"]["assumptions"])
          and "under the clauses LED-01 of its contract" in by_id["LED-03"]["evidence"]["scope"]
          and not any("holds at every call site" in a for a in by_id["LED-01"]["evidence"]["assumptions"]),
          "a postcondition's evidence lists the preconditions it was verified under; a precondition has none")
    return report


def kill(args):
    dafny, solver, version, ir_units, requires_by_id, oid, old, new = args
    if WORKSPACE.count(old) != 1:
        return oid, None, f"the text to mutate occurs {WORKSPACE.count(old)} times"
    source = WORKSPACE.replace(old, new)
    unit = ir_units[oid]
    outcome, diagnostics, _ = backend.run_obligation(dafny, solver, NAME, source, unit, RESOURCE_LIMIT, TIME_LIMIT,
                                                    requires_by_id)
    return oid, outcome, diagnostics


def plain_verifies(dafny, solver, source):
    """What a developer gets from Dafny alone: the workspace with every marker blank."""
    with tempfile.TemporaryDirectory(prefix="selyth-plain-") as tmp:
        path = Path(tmp) / NAME
        path.write_text(backend.instantiate(source, "none", ""), encoding="utf-8")
        run = subprocess.run([str(dafny), "verify", "--solver-path", str(solver), str(path)], capture_output=True, text=True)
    return run.returncode == 0 and " 0 errors" in run.stdout


# Dafny alone already refuses these bugs: the loop invariant written by hand in the workspace is
# proof scaffolding that overlaps LED-16 and LED-18, and Dafny demands termination of every loop
# whether or not LED-17 names the measure. The other seventeen bugs are accepted by Dafny alone.
OVERLAPS_SCAFFOLDING = {"LED-16", "LED-17", "LED-18"}


def check_mutation(dafny, solver, version):
    heading("Mutation study: each obligation catches a bug")
    ir = adapter.lower(SET, version)
    units = {u["obligation_id"]: u for u in ir["units"]}
    requires_by_id = {i: f"requires {u['clause']['expression']}" for i, u in units.items() if u["clause"]["keyword"] == "requires"}
    check(sorted(m[0] for m in MUTANTS) == sorted(units), "there is exactly one mutant per obligation")
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(kill, [(dafny, solver, version, units, requires_by_id, *m) for m in MUTANTS]))
    survivors = []
    for oid, outcome, diagnostics in results:
        ok = outcome == "failed"
        if not ok:
            survivors.append(oid)
        check(ok, f"{oid} fails on its bug (`{units[oid]['clause']['keyword']} {units[oid]['clause']['expression']}`)",
              f"    {outcome}: {diagnostics}")
    check(not survivors, "no obligation passes on a buggy implementation of what it specifies", f"    survivors: {survivors}")

    heading("...and it is the obligation that catches the bug, not the proof scaffolding")
    with ThreadPoolExecutor(4) as pool:
        plain = dict(zip((m[0] for m in MUTANTS), pool.map(
            lambda m: plain_verifies(dafny, solver, WORKSPACE.replace(m[1], m[2])), MUTANTS)))
    check(plain_verifies(dafny, solver, WORKSPACE), "(setup) the correct workspace verifies with every marker blank")
    escaped = sorted(i for i, ok in plain.items() if ok)
    caught_anyway = sorted(i for i, ok in plain.items() if not ok)
    check(caught_anyway == sorted(OVERLAPS_SCAFFOLDING),
          "seventeen of the twenty bugs pass Dafny alone and are caught only by their obligation; "
          "the three loop obligations overlap the hand-written invariant and Dafny's own termination check",
          f"    caught by Dafny alone: {caught_anyway}; escaped: {len(escaped)}")

    heading("End to end: one bug rejects the report and names the obligation")
    broken = WORKSPACE.replace("result := balance - amount;", "result := balance - amount - 1;")
    report = build(dafny, solver, version, workspace=broken)
    failed = sorted(o["obligation_id"] for o in report["obligations"] if o["outcome"] == "failed")
    check(tdb.valid(report) and report["decision"]["result"] == "rejected" and "LED-05" in failed
          and {v["rule_id"] for v in report["decision"]["violations"]} == {"BLOCK-FAILED"}
          and all(o["outcome"] == "proven" for o in report["obligations"] if o["obligation_id"] not in failed),
          f"a wrong withdrawal is rejected with BLOCK-FAILED on {failed}; every other obligation is still proven",
          f"    {report['decision']}")


def check_translated_bugs(dafny, solver, version):
    heading("A bug in the TypeScript itself is caught: the Dafny model is generated from the code")
    ts = (PROJECT / "ledger.ts").read_text(encoding="utf-8")
    translation = validate.load_json(PROJECT / "ledger.translation.json")
    check(t2d.translate(ts, translation) == WORKSPACE, "(setup) the committed workspace is the translation of ledger.ts")
    bugs = [
        ("a withdrawal that takes one cent too much", "return balance - amount;", "return balance - amount - 1;", {"LED-05"}),
        ("a fee divided by 100 instead of 10000", "Math.floor((amount * bps) / 10000)", "Math.floor((amount * bps) / 100)", {"LED-08"}),
        ("a clamp that lets a too-large value through", "if (x > hi) return hi;", "if (x > hi) return x;", {"LED-11"}),
        ("a remainder taken modulo parts + 1", "total % parts]", "total % (parts + 1)]", {"LED-15"}),
        ("an accrual that adds one cent too many (the hand-written loop invariant fails too, so every obligation fails)", "total += step;", "total += step + 1;", {"LED-18"}),
    ]
    for label, old, new, expected in bugs:
        assert old in ts, old
        report = build(dafny, solver, version, workspace=t2d.translate(ts.replace(old, new, 1), translation))
        failed = {o["obligation_id"] for o in report["obligations"] if o["outcome"] == "failed"}
        check(tdb.valid(report) and report["decision"]["result"] == "rejected" and expected <= failed
              and {v["rule_id"] for v in report["decision"]["violations"]} == {"BLOCK-FAILED"},
              f"{label}: rejected, failing {sorted(failed)}", f"    {report['decision']}")
    try:
        t2d.translate(ts.replace("return balance - amount;", "return Math.abs(balance - amount);", 1), translation)
        refused = False
    except t2d.TranslationError:
        refused = True
    check(refused, "a change the translator cannot read (Math.abs) is refused, not approximated")


def check_vacuity(dafny, solver, version):
    heading("A precondition nothing calls is not proven")
    without_caller = WORKSPACE.replace(
        "method Credit(balance: nat, amount: nat, cap: nat)\n  requires balance + amount <= cap\n{\n  var credited := Deposit(balance, amount, cap);\n}\n\n", "")
    check(without_caller != WORKSPACE, "(setup) the workspace without Credit")
    report = build(dafny, solver, version, workspace=without_caller)
    entry = next(o for o in report["obligations"] if o["obligation_id"] == "LED-01")
    check(entry["outcome"] == "unknown" and "no call site" in entry["diagnostics"][0],
          "LED-01 becomes unknown: attached to a method no one calls, it would verify vacuously", f"    {entry['outcome']} {entry.get('diagnostics')}")
    check(tdb.valid(report) and report["decision"]["result"] == "rejected"
          and ("BLOCK-NO-VERDICT", "LED-01") in [(v["rule_id"], v["obligation_id"]) for v in report["decision"]["violations"]],
          "the policy engine rejects the report (BLOCK-NO-VERDICT on LED-01)")
    check(entry["outcome"] != "proven" and all(o["outcome"] == "proven" for o in report["obligations"] if o["obligation_id"] != "LED-01"),
          "the postconditions at the same marker are unaffected")


def check_lock(dafny, solver, version):
    heading("Weakening an obligation is caught by the lock")
    weakened = copy.deepcopy(SET)
    next(o for o in weakened["obligations"] if o["obligation_id"] == "LED-06")["statement"] = "ensures true"
    report = build(dafny, solver, version, document=weakened, lock=None)
    check(report["decision"]["result"] == "accepted",
          "(setup) regenerated for the weakened set, the buggy-tolerant report is accepted when nothing is anchored")
    d = engine.decide(POLICY, report, weakened, LOCK)
    check(d["result"] == "rejected" and [v["rule_id"] for v in d["violations"]] == ["INTEGRITY-OBLIGATION-SET-NOT-FROZEN"],
          "with the lock it is rejected: the Obligation Set is not the approved one", f"    {d}")


def main():
    try:
        dafny, solver, version = setup()
    except (backend.BackendError, OSError) as exc:
        check(False, "Dafny is available", f"    {exc}")
        version = None
    if version is not None:
        check_project(dafny, solver, version)
        check_mutation(dafny, solver, version)
        check_translated_bugs(dafny, solver, version)
        check_vacuity(dafny, solver, version)
        check_lock(dafny, solver, version)
    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
