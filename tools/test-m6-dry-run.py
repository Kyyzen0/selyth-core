#!/usr/bin/env python3
"""End-to-end test of the M6-Lite launch rehearsal (tools/m6_dry_run.py).

The dry run wires the whole evaluator chain together on a synthetic ADR-004-conforming corpus:
corpus -> launch gate -> 12 measured fake-agent runs (+ over-budget and unmeasured refusals) ->
scoring through evaluate.py run --manifest/--manifest-digest --runs -> review trigger, sample
and evidence package -> anchored audit journal. Each stage also exercises its refusal path
(wrong digest, unmeasured run, tampered journal, package without scope/oracle).

Run with `make test-python` (the scoring stage skips itself when no Dafny/Node is available) and
with `make test-eval` (full chain, real Dafny and Node), or `python tools/test-m6-dry-run.py`.
"""
import json
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import m6_dry_run

FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def main():
    with tempfile.TemporaryDirectory(prefix="selyth-m6-dry-") as tmp:
        work = Path(tmp)
        rc = m6_dry_run.main(["all", "--work", str(work)])
        check(rc == 0, "the dry run passes end to end (all stages)", f"    rc={rc}")
        check((work / "corpus" / "corpus.json").is_file(), "the synthetic corpus manifest is written")
        check((work / "journal.jsonl").is_file() and (work / "anchor.json").is_file(),
              "the audit journal and its anchor are written")
        dafny, node = m6_dry_run.find_dafny(), m6_dry_run.find_node()
        results = work / "results.json"
        if dafny and node:
            check(results.is_file(), "the scoring stage ran (Dafny and Node available)")
            if results.is_file():
                mc = json.loads(results.read_text(encoding="utf-8"))["summary"]["model_calls"]
                check(mc and len(mc["runs"]) == 4 and mc["total_calls"] == 12 and not mc["unmeasured"],
                      "the scored report merges the 4 agent runs of the 2-task slice (12 model calls)",
                      f"    {mc}")
        else:
            check(not results.exists(), "no scoring without Dafny/Node (stage skipped, nothing written)")

    print(f"\n{'all checks passed' if not FAILURES else 'FAILED'}: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
