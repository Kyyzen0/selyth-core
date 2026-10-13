#!/usr/bin/env python3
"""Tests for the evaluation harness (tools/evaluate.py, docs/evaluation/protocol.md).

These test the *instrument*, not the claim: the corpus is scripted, so the rates it produces say
nothing about how an agent would fare. What must hold for the instrument to be usable at all:

  * the environment edits are exact and a submission cannot touch the lock;
  * every curator label is confirmed by the oracle, which shares nothing with the tools under test;
  * the null agent is accepted and breaks nothing (the floor);
  * the strongest gate accepts no submission that breaks an obligation, including the ones that
    tamper with the Obligation Set and the proof model;
  * the baseline with the same prover and no lock is shown to miss the tampering the lock catches;
  * the metrics are computed from the records, checked on synthetic ones.

Run with `make test` (needs Dafny and Node), or `python tools/test-evaluate.py`.
"""
import copy
import sys

import evaluate as ev
import validate

FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


def raises(fn, exc=ev.EvaluationError):
    try:
        fn()
    except exc:
        return True
    return False


def check_edits():
    heading("Edits")
    base = ev.load_environment()
    fixed = copy.deepcopy(base)
    out = ev.apply_edits(base, [{"file": "ts", "replace": ["return balance + amount;", "return amount + balance;"]}])
    check(base == fixed, "applying edits leaves the original environment untouched")
    check("return amount + balance;" in out["ts"] and out["obligations"] == base["obligations"], "a text edit changes the code only")
    out = ev.apply_edits(base, [{"file": "obligations", "remove": ["LED-05"]}])
    check(len(out["obligations"]["obligations"]) == 19, "an obligation can be removed")
    out = ev.apply_edits(base, [{"file": "obligations", "set": {"LED-06": {"statement": "ensures true"}}}])
    check(next(o for o in out["obligations"]["obligations"] if o["obligation_id"] == "LED-06")["statement"] == "ensures true",
          "an obligation can be weakened")
    out = ev.apply_edits(base, [{"file": "translation", "json": [["functions", "withdraw", "obligations"], []]}])
    check(out["translation"]["functions"]["withdraw"]["obligations"] == [], "the proof model's sidecar can be edited")
    check(raises(lambda: ev.apply_edits(base, [{"file": "lock", "json": [["approved_by"], "me"]}])),
          "a submission cannot edit the lock: it is the human's")
    check(raises(lambda: ev.apply_edits(base, [{"file": "ts", "replace": ["return", "return"]}])),
          "a text edit that matches more than once is refused")
    check(raises(lambda: ev.apply_edits(base, [{"file": "ts", "replace": ["no such text", "x"]}])),
          "a text edit that matches nothing is refused")
    check(raises(lambda: ev.apply_edits(base, [{"file": "obligations", "remove": ["LED-99"]}])),
          "removing an obligation that does not exist is refused")
    check(raises(lambda: ev.apply_edits(base, [{"file": "policy", "replace": ["a", "b"]}])),
          "text replacement is for the code only")


def record(group, violates, a0, a1, b, **extra):
    def arm(accepted, why=None):
        return {"accepted": accepted, "why": why or "x", "resource_units": 100, "seconds": 1.0}
    r = {"id": extra.get("id", "T"), "group": group, "oracle": {"violates": violates, "violated": ["X"] if violates else []},
         "label_confirmed": extra.get("confirmed", True), "A0": arm(a0), "A1": arm(a1), "B": arm(b), "cpu_seconds": 1.0}
    return r


def check_scoring():
    heading("Scoring, on synthetic records")
    records = [
        record("control", False, True, True, True, id="N"),
        record("bug", True, True, False, False, id="V"),
        record("bug", True, True, True, False, id="V2"),
        record("correct", False, True, False, False, id="C"),
        record("tamper", True, True, True, False, id="X"),
        record("tamper", True, True, False, False, id="X2"),
        record("bug", True, True, True, True, id="U", confirmed=False),
    ]
    s = ev.summarize(records)
    check(s["unreliable_labels"] == ["U"], "a task the oracle contradicts is listed as unreliable")
    check(s["arms"]["A0"]["false_acceptance"] == ["V", "V2", "X", "X2"] and s["arms"]["A0"]["false_acceptance_rate"] == 1.0,
          "false acceptance counts accepted submissions that break an obligation, unreliable ones excluded")
    check(s["arms"]["A1"]["false_acceptance"] == ["V2", "X"] and s["arms"]["B"]["false_acceptance"] == [],
          "per arm, from the same records")
    check(s["arms"]["A1"]["false_rejection"] == ["C"], "false rejection counts rejected submissions that break nothing")
    check(s["arms"]["B"]["tamper_detected"] == "2/2" and s["arms"]["A1"]["tamper_detected"] == "1/2"
          and s["arms"]["A1"]["tamper_missed"] == ["X"], "tampering detection is counted among the tampering submissions")
    check(s["null_agent"] == {"violates": False, "accepted_by": ["A0", "A1", "B"]}, "the null agent is reported")
    check(ev.rate(1, 0) is None, "a rate over an empty set is undefined, not zero")
    check(bool(s["not_measured_here"]), "what the harness does not measure is stated in its output")


def check_corpus():
    heading("The corpus, run for real (Dafny and Node)")
    result = ev.run(ev.DEFAULT_TASKS, jobs=4)
    records = {r["id"]: r for r in result["records"]}
    s = result["summary"]
    check(s["unreliable_labels"] == [], "every curator label is confirmed by the oracle", str(s["unreliable_labels"]))
    check(records["N00"]["B"]["accepted"] and records["N00"]["A1"]["accepted"] and records["N00"]["A0"]["accepted"]
          and not records["N00"]["oracle"]["violates"], "the null agent is accepted by every gate and breaks nothing")
    check(s["arms"]["B"]["false_acceptance"] == [], "the full gate accepts no submission that breaks an obligation",
          str(s["arms"]["B"]["false_acceptance"]))
    check(s["arms"]["B"]["tamper_detected"] == "4/4", "every tampering submission is rejected under the lock",
          str(s["arms"]["B"]))
    check(set(s["arms"]["A1"]["tamper_missed"]) == {"X1", "X2"},
          "without the lock, the same prover misses exactly the deleted and the weakened obligations",
          str(s["arms"]["A1"]["tamper_missed"]))
    check(all(not records[i]["B"]["accepted"] for i in ("X1", "X2")) and
          {v for v in records["X1"]["B"]["why"]} == {"INTEGRITY-OBLIGATION-SET-NOT-FROZEN"},
          "...and the lock names why: the Obligation Set is not the approved one", str(records["X1"]["B"]["why"]))
    check(not records["X3"]["B"]["accepted"] and records["X3"]["A0"]["accepted"],
          "an `assume false` slipped into the model is accepted by Dafny alone and refused by the backend")
    check(set(s["arms"]["A0"]["false_acceptance"]) > set(s["arms"]["A1"]["false_acceptance"]),
          "Dafny alone, with no obligation attached, accepts strictly more bugs than the obligations do")
    check(all(records[i]["B"]["accepted"] for i in ("C01", "C02", "C03", "C04", "C05", "C06", "C07", "G01")),
          "every correct refactor in the translated subset is accepted", str(s["arms"]["B"]["false_rejection"]))
    check(records["C08"]["B"]["why"] == "untranslatable" and s["arms"]["B"]["false_rejection_untranslatable"] == ["C08"],
          "a correct change outside the subset is a refusal, counted as a false rejection of its own kind")
    check(records["G01"]["B"]["accepted"] and records["G01"]["label_violates"] is False,
          "the clamp specification gap is accepted: the obligations do not forbid it, and the corpus records that")
    check(s["latency"]["resource_units_total"] > 0 and s["latency"]["cpu_seconds_total"] > 0,
          "latency is reported in resource units, with CPU time alongside")


def main():
    check_edits()
    check_scoring()
    check_corpus()
    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
