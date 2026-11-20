#!/usr/bin/env python3
"""The third-party example (roadmap item 1): gcd.ts from pallosp/congruence_solver (MIT).

  * the committed Dafny workspace is exactly the translation of gcd.ts (regeneration check);
  * all four obligations are kernel-proven by a real Dafny and the run is accepted under the
    human-approved lock, and replays;
  * a bug injected for each obligation, in the workspace, makes that obligation fail.

Run with `make test-dafny`, or `python tools/test-gcd.py` in the pinned environment with
Dafny 4.11.0 available (SELYTH_DAFNY=<path> or ~/.local/opt/dafny/dafny).
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
import ts_to_dafny as t2d
import validate

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PROJECT = ROOT / "docs" / "specifications" / "examples" / "gcd"
FAILURES = []
PRODUCED_AT = "2026-11-20T12:00:00Z"
RESOURCE_LIMIT, TIME_LIMIT = 1_000_000, 60

SET = oset.load_obligation_set(PROJECT / "gcd.obligations.json")
POLICY = validate.load_json(PROJECT / "gcd.policy.json")
LOCK = validate.load_json(PROJECT / "gcd.lock.json")
WORKSPACE = (PROJECT / "gcd.workspace.dfy").read_text(encoding="utf-8")
NAME = "gcd.workspace.dfy"

# One bug per obligation, in the workspace, that the obligation must catch.
MUTANTS = [
    ("GCD-01", "  requires a >= 1 || b >= 1\n{\n  var g := Gcd(a, b);", "  requires true\n{\n  var g := Gcd(a, b);"),
    ("GCD-02", "  result := a;", "  result := a * 0;"),
    ("GCD-03", "    b := a % b;", "    b := a;"),
    ("GCD-04", "    a := t;", "    a := 0;"),
]  # GCD-04 is a loop invariant verified under the enclosing method's contract (its
   # `requires` and `decreases`): the backend gives a loop clause those clauses as
   # hypotheses (hypotheses_for), so the invariant is a standalone obligation here


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


def build(dafny, solver, version, workspace=None, lock=LOCK):
    ir = adapter.lower(SET, version)
    data = (workspace if workspace is not None else WORKSPACE).encode("utf-8")
    return backend.build_report(ir, SET, POLICY, data, NAME, PRODUCED_AT, dafny, solver, version,
                                RESOURCE_LIMIT, TIME_LIMIT, lock)


def main():
    heading("The third-party module and its Obligation Set")
    check(len(SET["obligations"]) == 4, "four obligations: precondition, postcondition, termination, loop invariant",
          f"    {[o['obligation_id'] for o in SET['obligations']]}")
    check(LOCK == engine.make_lock(POLICY, SET, LOCK["approved_by"]),
          "the committed lock is the lock of this policy and this set")
    spec_json = validate.load_json(PROJECT / "gcd.translation.json")
    regenerated = t2d.translate((PROJECT / "gcd.ts").read_text(encoding="utf-8"), spec_json)
    check(regenerated == WORKSPACE, "the committed workspace is exactly the translation of gcd.ts",
          "    regenerate with: make gcd-workspace")

    try:
        dafny = tdb.locate_dafny()
        version, solver = backend.tool_versions(dafny)
    except (backend.BackendError, OSError) as exc:
        check(False, "Dafny is available", f"    {exc}")
        print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
        return 1
    check(version == "4.11.0", "Dafny 4.11.0 is the pinned version", f"    got {version}")

    heading("All obligations are kernel-proven by a real Dafny")
    report = build(dafny, solver, version)
    check(tdb.valid(report), "the report is valid for report-schema.json",
          "    " + "; ".join(f"{p.path} {p.message}" for p in validate.validate(report, tdb.REPORT_SCHEMA)))
    outcomes = {o["obligation_id"]: o["outcome"] for o in report["obligations"]}
    check(set(outcomes.values()) == {"proven"} and len(outcomes) == 4, "four obligations, all proven",
          f"    {outcomes}")
    check(report["decision"]["result"] == "accepted" and engine.replay(POLICY, report, SET, LOCK),
          "the policy engine accepts the report under the lock, and replays its decision",
          f"    {report['decision']}")
    check(oset.check_coverage(SET, report) == [], "the report covers the Obligation Set exactly")

    heading("Mutation study: each obligation catches its bug")
    for oid, old, new in MUTANTS:
        check(WORKSPACE.count(old) == 1, f"(setup) the text to mutate for {oid} occurs once")
        mutant = build(dafny, solver, version, workspace=WORKSPACE.replace(old, new))
        entry = next(o for o in mutant["obligations"] if o["obligation_id"] == oid)
        check(entry["outcome"] == "failed" and mutant["decision"]["result"] == "rejected",
              f"{oid} fails on its bug ({old.strip()[:40]!r} -> {new.strip()[:40]!r})",
              f"    outcome {entry['outcome']}, decision {mutant['decision']['result']}")

    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
