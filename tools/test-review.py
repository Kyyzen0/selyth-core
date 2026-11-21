#!/usr/bin/env python3
"""Tests for the M6-Lite review trigger (tools/review.py, ADR-004 section 4).

The trigger is exact predicates over the evidence report, identical for both arms, and the
free-text `scope` field is never read. What must hold:

  * each of the seven ADR-004 classes is produced by its exact predicate, and only by it;
  * "weaker" follows the enum order of evidence.kind in report-schema.json (tested against
    the schema file itself, so the two cannot drift);
  * the refutation phrases are the closed list, matched inside diagnostics;
  * changing `scope` never changes the class;
  * needs_review reports exactly the not-clearly-decided obligations and refuses a report
    that misses a frozen obligation;
  * the second-review sample is ceil(20%), deterministic, and recomputable by hash.

Run with `make test-python` (no Dafny, no Node), or `python tools/test-review.py`.
"""
import hashlib
import json
import sys
from pathlib import Path

import review
import validate

FAILURES = []
ROOT = Path(__file__).resolve().parent.parent


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


def result(outcome, kind=None, assumptions=None, diagnostics=None):
    r = {"obligation_id": "O1", "outcome": outcome}
    if kind is not None or outcome in ("proven", "passed"):
        r["evidence"] = {"kind": kind, "soundness": "sound-for-model", "scope": "free text, never read",
                         "assumptions": assumptions or []}
    if diagnostics is not None:
        r["diagnostics"] = diagnostics
    return r


def main():
    heading("The evidence-kind order mirrors report-schema.json exactly")
    schema = validate.load_json(ROOT / "docs" / "specifications" / "report-schema.json")
    enum_order = schema["$defs"]["evidence"]["properties"]["kind"]["enum"]
    check(review.EVIDENCE_KIND_ORDER == enum_order,
          "EVIDENCE_KIND_ORDER == schema enum order", f"    {review.EVIDENCE_KIND_ORDER} vs {enum_order}")
    check(review.is_weaker("property-tested", "kernel-proven"), "property-tested is weaker than kernel-proven")
    check(not review.is_weaker("kernel-proven", "property-tested"), "kernel-proven is not weaker than property-tested")
    check(not review.is_weaker("property-tested", "property-tested"), "a kind is not weaker than itself")

    heading("The seven ADR-004 classes, produced by their exact predicates")
    req = "kernel-proven"
    check(review.classify(result("timeout"), req) == "timeout", "timeout -> timeout")
    check(review.classify(result("unknown"), req) == "unknown", "unknown -> unknown")
    check(review.classify(result("not-run"), req) == "insufficient-scope", "not-run -> insufficient-scope")
    check(review.classify(result("proven", kind="property-tested"), req) == "insufficient-scope",
          "proven with weaker kind -> insufficient-scope")
    check(review.classify(result("proven", kind=None), req) == "insufficient-scope",
          "proven without evidence kind -> insufficient-scope (fail-closed)")
    check(review.classify(result("failed", diagnostics=["solver gave up"]), req) == "error",
          "failed without refutation phrase -> error")
    check(review.classify(result("failed", diagnostics=["could not be proved"]), req) == "clearly-violated",
          "failed with 'could not be proved' -> clearly-violated")
    check(review.classify(result("failed", diagnostics=["the invariant might not hold on entry"]), req)
          == "clearly-violated", "refutation phrase matched inside a longer diagnostic")
    check(review.classify(result("passed", kind="kernel-proven", assumptions=["x > 0"]), req) == "conditional-proof",
          "passed, sufficient kind, non-empty assumptions -> conditional-proof")
    check(review.classify(result("proven", kind="kernel-proven", assumptions=[]), req) == "clearly-proven",
          "proven, sufficient kind, no assumptions -> clearly-proven")
    check(review.classify(result("passed", kind="property-tested"), "property-tested") == "clearly-proven",
          "equal kind is not weaker -> clearly-proven")

    heading("The free-text scope field is never read")
    base = result("failed", diagnostics=["might not be"])
    other = json.loads(json.dumps(base))
    other["evidence"] = {"kind": "kernel-proven", "scope": "COMPLETELY DIFFERENT FREE TEXT", "assumptions": []}
    check(review.classify(base, req) == review.classify(other, req) == "clearly-violated",
          "changing scope does not change the class")
    r = result("proven", kind="kernel-proven", assumptions=[])
    r["evidence"]["scope"] = "anything"
    check(review.classify(r, req) == "clearly-proven", "scope present on a clear case: still clearly-proven")

    heading("needs_review over a report and a frozen set")
    obset = {"selyth_obligation_set_version": "0.1.0", "set_id": "s", "specification_version": "1.0.0",
             "obligations": [
                 {"obligation_id": "O1", "kind": "postcondition", "statement": "ensures result >= 0",
                  "required_evidence_kind": "kernel-proven", "assumptions": [], "affected_components": ["a.ts"]},
                 {"obligation_id": "O2", "kind": "postcondition", "statement": "ensures result <= 100",
                  "required_evidence_kind": "kernel-proven", "assumptions": [], "affected_components": ["a.ts"]}]}
    rep = {"obligations": [dict(result("proven", kind="kernel-proven"), obligation_id="O1"),
                          dict(result("timeout"), obligation_id="O2")]}
    cases = review.needs_review(rep, obset)
    check(cases == [{"obligation_id": "O2", "class": "timeout"}], "only the undecided obligation is reviewed",
          f"    {cases}")
    rep_missing = {"obligations": [dict(result("proven", kind="kernel-proven"), obligation_id="O1")]}
    try:
        review.needs_review(rep_missing, obset)
        check(False, "a report missing a frozen obligation is refused")
    except review.ReviewError:
        check(True, "a report missing a frozen obligation is refused")

    heading("The second-review sample: ceil(20%), smallest hashes, deterministic, no seed")
    cases = [f"case-{i:02d}" for i in range(10)]
    md, rid = "sha256:" + "ab" * 32, "run-0001"
    s1 = review.second_review_sample(cases, md, rid)
    s2 = review.second_review_sample(list(reversed(cases)), md, rid)
    check(len(s1) == 2, "10 cases -> ceil(20%) = 2 sampled", f"    {s1}")
    check(s1 == s2, "order of input does not matter (hash-ordered)")
    manual = sorted(cases, key=lambda c: hashlib.sha256(
        (review.SECOND_REVIEW_LABEL + md + rid + c).encode()).hexdigest())[:2]
    check(s1 == manual, "sampled cases are exactly the smallest SHA-256 values")
    check(review.second_review_sample([], md, rid) == [], "no cases -> no sample")
    check(review.second_review_sample(["only"], md, rid) == ["only"], "1 case -> ceil(0.2) = 1")

    heading("The evidence package carries structure only: no scope, no oracle")
    task = {"task_id": "T000000A", "prompt": "do the thing", "source": {"project": "p", "licence": "MIT",
             "revision": "r"}}
    pkg = review.build_evidence_package(task, obset, rep)
    check("scope" not in json.dumps(pkg), "no free-text scope anywhere in the package")
    check(pkg["results"][0]["class"] == "clearly-proven" and pkg["results"][1]["class"] == "timeout",
          "each result carries its ADR class")
    check(pkg["set_digest"].startswith("sha256:"), "package anchors the frozen set by digest")
    check("oracle" not in json.dumps(pkg).lower(), "the oracle never appears in the package")

    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
