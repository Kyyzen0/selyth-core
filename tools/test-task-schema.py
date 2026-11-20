#!/usr/bin/env python3
"""Tests for the M6-Lite task manifest schema (docs/evaluation/task-schema.json).

  * hygiene: the schema is a valid Draft 2020-12 schema with no duplicate key,
    no unknown keyword and no dangling $ref (tools/validate.py load_schema);
  * the shipped example manifest is accepted;
  * mutated manifests are rejected, and for the intended rule each time.

Run with `make test` (see README.md), or `python tools/test-task-schema.py`
inside the pinned environment.
"""
import copy
import json
import sys
from pathlib import Path

import validate

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "docs" / "evaluation" / "task-schema.json"
EXAMPLE = ROOT / "docs" / "evaluation" / "task.example.json"

FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def main():
    schema = validate.load_schema(SCHEMA)  # raises if the schema itself is unsound
    check(True, "the task schema passes the Draft 2020-12 hygiene checks (load_schema)")

    example = validate.load_json(EXAMPLE)
    check(not validate.validate(example, schema), "the shipped example manifest is valid")

    def rejects(label, mutate, fragment):
        doc = copy.deepcopy(example)
        mutate(doc)
        problems = validate.validate(doc, schema)
        check(any(fragment in (p.path + " " + p.message + " " + p.keyword) for p in problems),
              f"refused: {label}", "    " + "; ".join(f"{p.path} {p.keyword}" for p in problems)
              or "    (accepted)")

    rejects("a task_id that is too short / not opaque-shaped",
            lambda d: d.update(task_id="abc"), "task_id")
    rejects("a non-permissive licence",
            lambda d: d["source"].update(licence="GPL-3.0"), "licence")
    rejects("a budget above the ADR-004 per-run limit",
            lambda d: d["budget"].update(max_minutes=30), "budget")
    rejects("a call budget above 15",
            lambda d: d["budget"].update(max_model_calls=16), "max_model_calls")
    rejects("no modifiable file", lambda d: d.update(modifiable=[]), "modifiable")
    rejects("a modifiable path escaping the working directory",
            lambda d: d.update(modifiable=["../outside.ts"]), "modifiable")
    rejects("a file without its digest",
            lambda d: d["files"][0].pop("digest"), "digest")
    rejects("a digest that is not a full sha256",
            lambda d: d["files"][0].update(digest="sha256:abc"), "digest")
    rejects("an unknown top-level key", lambda d: d.update(cheat=True), "additionalProperties")
    rejects("a prompt that is empty", lambda d: d.update(prompt=""), "prompt")

    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
