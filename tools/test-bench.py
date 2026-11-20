#!/usr/bin/env python3
"""Tests for the bench report (docs/specifications/bench-schema.json, tools/bench.py).

  * hygiene: the schema is a valid Draft 2020-12 schema (tools/validate.py load_schema);
  * summarise() turns a verification report into a valid bench report, is pure and
    deterministic, and computes the totals;
  * mutated bench reports are rejected for the intended rule.

The full pipeline run (with a real Dafny) is checked by tools/test-dafny-backend.py.

Run with `make test` (see README.md), or `python tools/test-bench.py` in the pinned environment.
"""
import copy
import importlib.util
import json
import sys
from pathlib import Path

import bench
import validate

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "docs" / "specifications" / "bench-schema.json"

FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


spec = importlib.util.spec_from_file_location("legacy_test_schemas", ROOT / "tools" / "test-schemas.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


def report_fixture():
    report = copy.deepcopy(legacy.good_report)
    report["report_id"] = "rpt-" + "0" * 16  # the backend's id shape
    return report


def main():
    schema = validate.load_schema(SCHEMA)
    check(True, "the bench schema passes the Draft 2020-12 hygiene checks (load_schema)")

    heading("summarise: verification report -> bench report")
    bench_doc = bench.summarise(report_fixture())
    check(not validate.validate(bench_doc, schema), "summarise() of a real-shaped report is valid")
    check(bench.summarise(report_fixture()) == bench_doc, "summarise is deterministic (pure)")
    check(bench_doc["totals"]["resource_units"] == 63182
          and bench_doc["totals"]["wall_ms"] == 134
          and bench_doc["totals"]["peak_rss_bytes"] == 188743680
          and bench_doc["totals"]["obligations"] == 1
          and bench_doc["totals"]["timed_out"] == 0,
          "totals are the per-obligation sums (max for peak_rss_bytes, an upper bound)",
          f"    {bench_doc['totals']}")
    two = copy.deepcopy(report_fixture())
    second = copy.deepcopy(two["obligations"][0])
    second["obligation_id"] = "OBL-2"
    second["evidence"]["cost"]["peak_rss_bytes"] = 999
    two["obligations"].append(second)
    b3 = bench.summarise(two)
    check(b3["totals"]["peak_rss_bytes"] == 188743680 and b3["totals"]["resource_units"] == 2 * 63182,
          "with two obligations, peak_rss_bytes total is the max and resource_units the sum",
          f"    {b3['totals']}")
    check(bench_doc["decision"] == "accepted" and bench_doc["policy_digest"] == "sha256:" + "c" * 64,
          "decision and digests are carried over for context")

    not_run = copy.deepcopy(report_fixture())
    not_run["obligations"][0]["outcome"] = "not-run"
    not_run["obligations"][0]["evidence"].pop("cost")
    b2 = bench.summarise(not_run)
    check(not validate.validate(b2, schema) and b2["totals"]["obligations"] == 1
          and "resource_units" not in b2["obligations"][0] and "resource_units" not in b2["totals"],
          "an obligation with no cost recorded still appears, without invented numbers", f"    {b2['totals']}")

    heading("Mutated bench reports are rejected for the intended rule")

    def rejects(label, mutate, fragment):
        doc = copy.deepcopy(bench_doc)
        mutate(doc)
        problems = validate.validate(doc, schema)
        check(any(fragment in (p.path + " " + p.keyword) for p in problems), f"refused: {label}",
              "    " + "; ".join(f"{p.path} {p.keyword}" for p in problems) or "    (accepted)")

    rejects("a negative resource count", lambda d: d["obligations"][0].update(resource_units=-1),
            "resource_units")
    rejects("an unknown decision", lambda d: d.update(decision="maybe"), "decision")
    rejects("a malformed report id", lambda d: d.update(report_id="rpt-xyz"), "report_id")
    rejects("a digest without the sha256 prefix", lambda d: d.update(policy_digest="abc"), "policy_digest")
    rejects("an unknown top-level key", lambda d: d.update(verdict="great"), "additionalProperties")
    rejects("an empty obligations list", lambda d: d.update(obligations=[]), "obligations")

    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
