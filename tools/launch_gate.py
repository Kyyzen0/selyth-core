"""The M6-Lite official-launch gate (docs/decisions/ADR-004-m6-launch-decisions.md section 2;
docs/evaluation/protocol.md section 6).

An official launch is REFUSED (exit 2) if:
  * the corpus holds fewer than 6 tasks, from fewer than 3 distinct projects, or fewer than
    20 obligations in total (ADR-004 section 2 minimums);
  * a task manifest does not validate against docs/evaluation/task-schema.json;
  * a frozen artefact (starting code, obligations, oracle) is missing or its sha256 does
    not match the digest frozen in the manifest;
  * the control plan is absent, does not hold exactly 10 null-agent and 10 sabotage
    controls, targets an unknown task, or carries a malformed control;
  * the corpus manifest digest does not match --digest (the frozen manifests).

Corpus manifest shape (the manifest is frozen and hashed; the digest covers everything
except the "manifest_digest" field itself, canonical JSON, same rules as Obligation Sets):

  {"corpus_id": "...", "manifest_digest": "sha256:...",
   "tasks": [ <task manifest, task-schema.json> ... ],
   "control_plan": {"controls": [ {"control_id": "...", "type": "null-agent"|"sabotage",
                                   "task_id": "...", "arm": "A1"|"B" (optional),
                                   "expected_scenario": "...", "expected_result": "..."} ]}}

  launch_gate.py check --manifest corpus.json [--digest sha256:...]
                       [--corpus-dir DIR]     DIR defaults to the manifest's directory;
                                              artefact paths are resolved against it
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import obligation_set as oset
import validate

ROOT = Path(__file__).resolve().parent.parent
TASK_SCHEMA = ROOT / "docs" / "evaluation" / "task-schema.json"

MIN_TASKS = 6
MIN_PROJECTS = 3
MIN_OBLIGATIONS = 20
CONTROL_COUNTS = {"null-agent": 10, "sabotage": 10}
CONTROL_ARMS = ("A1", "B")
OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class GateError(Exception):
    pass


def file_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest_digest(corpus):
    """The frozen digest: canonical JSON of the corpus without the digest field itself
    (sorted keys, compact separators, UTF-8 — the same serialisation rules the Obligation
    Set canonicalisation uses, applied to any JSON document)."""
    body = {k: v for k, v in corpus.items() if k != "manifest_digest"}
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_task_manifest(task, schema):
    problems = validate.validate(task, schema)
    return [f"task {task.get('task_id', '?')!r}: {p.path} {p.message}" for p in problems]


def check_artefacts(task, corpus_dir):
    """Every frozen artefact of the task exists under corpus_dir and matches its digest."""
    problems = []
    artefacts = list(task.get("files", [])) + [task.get("obligations"), task.get("oracle")]
    for art in artefacts:
        if not isinstance(art, dict):
            continue
        path = Path(corpus_dir) / art["path"]
        if not path.is_file():
            problems.append(f"task {task.get('task_id')!r}: missing artefact {art['path']}")
        elif file_digest(path) != art["digest"]:
            problems.append(f"task {task.get('task_id')!r}: digest mismatch for {art['path']}")
    return problems


def obligation_count(task, corpus_dir):
    art = task.get("obligations") or {}
    path = Path(corpus_dir) / art.get("path", "")
    if not path.is_file():
        raise GateError(f"task {task.get('task_id')!r}: obligations artefact missing")
    return len(oset.load_obligation_set(path)["obligations"])


def check_corpus_minimums(corpus, total_obligations):
    problems = []
    tasks = corpus.get("tasks") or []
    if len(tasks) < MIN_TASKS:
        problems.append(f"corpus holds {len(tasks)} tasks, minimum is {MIN_TASKS}")
    projects = {t.get("source", {}).get("project") for t in tasks}
    if len(projects) < MIN_PROJECTS:
        problems.append(f"corpus draws on {len(projects)} project(s), minimum is {MIN_PROJECTS}")
    if total_obligations < MIN_OBLIGATIONS:
        problems.append(f"corpus totals {total_obligations} obligations, minimum is {MIN_OBLIGATIONS}")
    return problems


def check_control_plan(corpus, task_ids):
    plan = corpus.get("control_plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("controls"), list):
        return ["control plan is absent or has no controls list"]
    controls = plan["controls"]
    problems = []
    counts = {}
    for ctl in controls:
        cid = ctl.get("control_id", "?")
        ctype = ctl.get("type")
        if ctype not in CONTROL_COUNTS:
            problems.append(f"control {cid!r}: unknown type {ctype!r}")
            continue
        counts[ctype] = counts.get(ctype, 0) + 1
        if not isinstance(cid, str) or not OPAQUE_ID.match(cid):
            problems.append(f"control {cid!r}: identifier is not opaque (8-64 of [A-Za-z0-9_-])")
        if ctl.get("task_id") not in task_ids:
            problems.append(f"control {cid!r}: targets unknown task {ctl.get('task_id')!r}")
        if "arm" in ctl and ctl["arm"] not in CONTROL_ARMS:
            problems.append(f"control {cid!r}: unknown arm {ctl['arm']!r}")
        if not ctl.get("expected_scenario") or not ctl.get("expected_result"):
            problems.append(f"control {cid!r}: missing expected scenario or result")
    for ctype, n in CONTROL_COUNTS.items():
        if counts.get(ctype, 0) != n:
            problems.append(f"control plan holds {counts.get(ctype, 0)} {ctype} controls, exactly {n} required")
    return problems


def verify(manifest_path, expected_digest=None, corpus_dir=None):
    """All launch-gate problems for a corpus manifest (empty list = launch allowed)."""
    manifest_path = Path(manifest_path)
    corpus_dir = Path(corpus_dir) if corpus_dir else manifest_path.parent
    corpus = validate.load_json(manifest_path)
    schema = validate.load_schema(TASK_SCHEMA)
    problems = []
    tasks = corpus.get("tasks") or []
    if not isinstance(tasks, list) or not tasks:
        return ["corpus manifest has no tasks list"]
    task_ids = set()
    total_obligations = 0
    for task in tasks:
        tid = task.get("task_id")
        task_ids.add(tid)
        problems += check_task_manifest(task, schema)
        problems += check_artefacts(task, corpus_dir)
        try:
            total_obligations += obligation_count(task, corpus_dir)
        except (GateError, oset.ObligationSetError, OSError) as exc:
            problems.append(f"task {tid!r}: cannot load its Obligation Set: {exc}")
    problems += check_corpus_minimums(corpus, total_obligations)
    problems += check_control_plan(corpus, task_ids)
    frozen = corpus.get("manifest_digest")
    if expected_digest is not None and frozen != expected_digest:
        problems.append(f"manifest_digest {frozen!r} != expected {expected_digest!r}")
    if frozen != manifest_digest(corpus):
        problems.append("manifest_digest does not match the canonical digest of the corpus")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    c.add_argument("--manifest", required=True)
    c.add_argument("--digest", help="the frozen digest the manifest must carry")
    c.add_argument("--corpus-dir")
    args = ap.parse_args(argv)
    try:
        problems = verify(args.manifest, expected_digest=args.digest, corpus_dir=args.corpus_dir)
    except (GateError, validate.SchemaError, OSError, ValueError) as exc:
        print(f"launch_gate: REFUSED: {exc}", file=sys.stderr)
        return 2
    if problems:
        for p in problems:
            print(f"launch_gate: REFUSED: {p}", file=sys.stderr)
        return 2
    print("launch_gate: OK: corpus manifest, artefact digests, minimums and control plan all check out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
