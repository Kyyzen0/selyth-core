"""Human-review trigger, second-review sampler and evidence package for M6-Lite
(docs/decisions/ADR-004-m6-launch-decisions.md section 4; docs/evaluation/protocol.md).

ADR-004 section 4 fixes the trigger as exact predicates over the evidence report, computed
by the controller, identical for both arms, never an operator's reading of free text:

  | class              | predicate                                                                    |
  |--------------------|------------------------------------------------------------------------------|
  | timeout            | outcome == timeout                                                            |
  | unknown            | outcome == unknown                                                            |
  | insufficient-scope | outcome == not-run, or proven/passed with evidence.kind weaker than required |
  | error              | outcome == failed and no diagnostic contains a refutation phrase             |
  | conditional-proof  | proven/passed, kind not weaker, evidence.assumptions not empty               |
  | clearly-violated   | outcome == failed and some diagnostic contains a refutation phrase           |
  | clearly-proven     | proven/passed, kind not weaker, evidence.assumptions empty                   |

"Weaker" follows the enum order of evidence.kind in report-schema.json (kernel-proven is
the strongest). The free-text `scope` field is NEVER read. The refutation phrases are the
closed list REFUTATION_PHRASES. The second review takes ceil(20%) of the reviewed cases,
those with the smallest SHA-256("selyth-m6-review-v1" + manifest_digest + run_id + case),
deterministic, no seed.

  review.py classify --report results.json --set obligations.json   per-obligation classes
  review.py sample --cases id1,id2,... --manifest-digest D --run-id R
  review.py package --task task.json --set obligations.json --report results.json -o pkg.json
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import obligation_set as oset
import validate

ROOT = Path(__file__).resolve().parent.parent
REPORT_SCHEMA = ROOT / "docs" / "specifications" / "report-schema.json"

# Strongest first; mirrors the enum order of evidence.kind in report-schema.json.
# test-review.py asserts the two stay equal.
EVIDENCE_KIND_ORDER = ["kernel-proven", "machine-checked-model", "property-tested",
                       "runtime-asserted", "llm-corroborated", "human-asserted"]

# The closed list of ADR-004 section 4. A `failed` with none of these is an error, not a
# refutation, and goes to review.
REFUTATION_PHRASES = ("could not be proved", "might not hold", "might not decrease", "might not be")

SECOND_REVIEW_LABEL = "selyth-m6-review-v1"
SECOND_REVIEW_FRACTION = 0.20

CLEAR = ("clearly-proven", "clearly-violated")


class ReviewError(Exception):
    pass


def kind_rank(kind):
    try:
        return EVIDENCE_KIND_ORDER.index(kind)
    except ValueError:
        raise ReviewError(f"unknown evidence kind {kind!r}")


def is_weaker(kind, required):
    return kind_rank(kind) > kind_rank(required)


def has_refutation_phrase(diagnostics):
    return any(phrase in diagnostic
               for diagnostic in diagnostics or [] for phrase in REFUTATION_PHRASES)


def classify(result, required_evidence_kind):
    """Exactly one ADR-004 class for one obligation_result of the report."""
    outcome = result.get("outcome")
    if outcome == "timeout":
        return "timeout"
    if outcome == "unknown":
        return "unknown"
    if outcome == "not-run":
        return "insufficient-scope"
    if outcome in ("proven", "passed"):
        evidence = result.get("evidence") or {}
        kind = evidence.get("kind")
        if kind is None or is_weaker(kind, required_evidence_kind):
            return "insufficient-scope"
        if evidence.get("assumptions"):
            return "conditional-proof"
        return "clearly-proven"
    if outcome == "failed":
        if has_refutation_phrase(result.get("diagnostics")):
            return "clearly-violated"
        return "error"
    raise ReviewError(f"unknown outcome {outcome!r}")


def needs_review(report, obligation_set):
    """The obligations whose result is neither clearly proven nor clearly violated.
    Every obligation of the frozen set is required; the rule is arm-independent."""
    required = {o["obligation_id"]: o.get("required_evidence_kind", "kernel-proven")
                for o in obligation_set["obligations"]}
    cases = []
    for result in report["obligations"]:
        oid = result["obligation_id"]
        if oid not in required:
            raise ReviewError(f"report obligation {oid!r} is not in the frozen set")
        cls = classify(result, required[oid])
        if cls not in CLEAR:
            cases.append({"obligation_id": oid, "class": cls})
    missing = sorted(set(required) - {r["obligation_id"] for r in report["obligations"]})
    if missing:
        raise ReviewError(f"report misses frozen obligations: {missing}")
    return cases


def second_review_sample(case_ids, manifest_digest, run_id):
    """The ceil(20%) of the reviewed cases with the smallest
    SHA-256(label + manifest_digest + run_id + case). Deterministic, no seed; every party
    can recompute it. The case id is part of the hashed value so the ordering is total."""
    cases = list(case_ids)
    if not cases:
        return []
    k = math.ceil(len(cases) * SECOND_REVIEW_FRACTION)
    ranked = sorted(cases, key=lambda c: hashlib.sha256(
        (SECOND_REVIEW_LABEL + manifest_digest + run_id + c).encode("utf-8")).hexdigest())
    return ranked[:k]


def build_evidence_package(task_manifest, obligation_set, report):
    """The standardized package handed to the external reviewer: the task, the frozen
    obligations, the structured evidence. It never contains the free-text `scope` field
    (ADR-004: it is never read) and never the oracle (the reviewer never accesses it)."""
    required = {o["obligation_id"]: o for o in obligation_set["obligations"]}
    results = []
    for result in report["obligations"]:
        evidence = result.get("evidence") or {}
        results.append({
            "obligation_id": result["obligation_id"],
            "outcome": result["outcome"],
            "evidence": {k: evidence[k] for k in ("kind", "soundness", "producer_tool", "assumptions")
                         if k in evidence},
            "diagnostics": result.get("diagnostics", []),
            "class": classify(result, required[result["obligation_id"]].get(
                "required_evidence_kind", "kernel-proven")),
        })
    return {
        "task_id": task_manifest.get("task_id"),
        "prompt": task_manifest.get("prompt"),
        "source": task_manifest.get("source"),
        "obligations": [{k: o[k] for k in ("obligation_id", "kind", "statement", "required_evidence_kind")}
                        for o in obligation_set["obligations"]],
        "results": results,
        "set_digest": oset.set_digest(obligation_set),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("classify")
    c.add_argument("--report", required=True)
    c.add_argument("--set", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--cases", required=True, help="comma-separated reviewed case ids")
    s.add_argument("--manifest-digest", required=True)
    s.add_argument("--run-id", required=True)
    p = sub.add_parser("package")
    p.add_argument("--task", required=True)
    p.add_argument("--set", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("-o", "--output")
    args = ap.parse_args(argv)
    try:
        if args.command == "classify":
            report = validate.load_json(args.report)
            obset = oset.load_obligation_set(args.set)
            for case in needs_review(report, obset):
                print(f"{case['obligation_id']}\t{case['class']}")
            return 0
        if args.command == "sample":
            cases = [c for c in args.cases.split(",") if c]
            for case in second_review_sample(cases, args.manifest_digest, args.run_id):
                print(case)
            return 0
        task = validate.load_json(args.task)
        obset = oset.load_obligation_set(args.set)
        report = validate.load_json(args.report)
        pkg = build_evidence_package(task, obset, report)
        text = json.dumps(pkg, indent=2, sort_keys=True) + "\n"
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        else:
            print(text)
        return 0
    except (ReviewError, validate.SchemaError, oset.ObligationSetError, OSError) as exc:
        print(f"review: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
