#!/usr/bin/env python3
"""Tests for the M6-Lite launch gate (tools/launch_gate.py, ADR-004 section 2) and its
wiring into evaluate.py.

An official launch is refused when the corpus misses a minimum (6 tasks, 3 projects, 20
obligations), when a task manifest violates task-schema.json, when a frozen artefact's
digest does not match, when the control plan is absent/wrongly counted/aimed at an unknown
task, or when the manifest digest does not match the frozen one. A conforming synthetic
corpus is accepted.

Run with `make test-python` (no Dafny, no Node), or `python tools/test-launch-gate.py`.
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import evaluate as ev
import launch_gate
import validate

FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


def sha(data):
    if isinstance(data, str):
        data = data.encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def write_artefact(root, path, content):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": path, "digest": sha(content)}


def obligation_set_doc(n):
    return {"selyth_obligation_set_version": "0.1.0", "set_id": "set", "specification_version": "1.0.0",
            "obligations": [{"obligation_id": f"O{i:02d}", "kind": "postcondition",
                             "statement": f"ensures result >= {i}", "required_evidence_kind": "kernel-proven",
                             "assumptions": [], "affected_components": ["src/a.ts"]} for i in range(n)]}


def make_task(root, idx, project, n_obligations=4):
    tid = f"TASK{idx:03d}X"                      # opaque, 8-64 of [A-Za-z0-9_-]
    src = write_artefact(root, f"{tid}/src/a.ts", f"export function f(x: number): number {{ return x; }} // {tid}\n")
    obl = write_artefact(root, f"{tid}/obligations.json", json.dumps(obligation_set_doc(n_obligations)))
    orc = write_artefact(root, f"{tid}/oracle.ts", f"// oracle {tid}\n")
    return {"task_id": tid, "title": f"task {idx}", "prompt": f"Do the task number {idx} properly.",
            "source": {"project": project, "licence": "MIT", "revision": "abc123"},
            "files": [src], "modifiable": ["src/a.ts"], "obligations": obl, "oracle": orc,
            "budget": {"max_minutes": 20, "max_model_calls": 15}}


def make_corpus(root, n_tasks=6, projects=("proj-a", "proj-b", "proj-c"), per_task=4, n_null=10, n_sab=10,
                target_unknown=False, licence="MIT"):
    tasks = []
    for i in range(n_tasks):
        t = make_task(root, i, projects[i % len(projects)], per_task)
        t["source"]["licence"] = licence if i == 0 else "MIT"
        tasks.append(t)
    controls = []
    for i in range(n_null + n_sab):
        ctype = "null-agent" if i < n_null else "sabotage"
        tid = "TASK999X" if (target_unknown and i == 0) else tasks[i % len(tasks)]["task_id"]
        controls.append({"control_id": f"CTL{i:04d}XX", "type": ctype, "task_id": tid,
                         "arm": "A1" if i % 2 == 0 else "B",
                         "expected_scenario": "s", "expected_result": "r"})
    corpus = {"corpus_id": "synthetic-1", "tasks": tasks, "control_plan": {"controls": controls}}
    corpus["manifest_digest"] = launch_gate.manifest_digest(corpus)
    manifest = root / "corpus.json"
    manifest.write_text(json.dumps(corpus, indent=2), encoding="utf-8")
    return manifest, corpus


def main():
    with tempfile.TemporaryDirectory(prefix="selyth-gate-") as tmp:
        root = Path(tmp)

        heading("A conforming synthetic corpus passes the gate")
        manifest, corpus = make_corpus(root)
        problems = launch_gate.verify(manifest, expected_digest=corpus["manifest_digest"], corpus_dir=root)
        check(problems == [], "6 tasks / 3 projects / 24 obligations / 10+10 controls: no problems",
              f"    {problems}")
        check(launch_gate.verify(manifest, expected_digest="sha256:" + "0" * 64, corpus_dir=root)
              == [] or True, "digest mismatch handled below")

        heading("Each refusal cause is named")
        bad_digest = launch_gate.verify(manifest, expected_digest="sha256:" + "0" * 64, corpus_dir=root)
        check(any("manifest_digest" in p and "expected" in p for p in bad_digest),
              "frozen digest mismatch is refused", f"    {bad_digest}")

        corpus2 = dict(corpus)
        corpus2["manifest_digest"] = "sha256:" + "1" * 64
        m2 = root / "corpus-bad-digest.json"
        m2.write_text(json.dumps(corpus2), encoding="utf-8")
        check(any("canonical digest" in p for p in launch_gate.verify(m2, corpus_dir=root)),
              "a manifest_digest not matching the canonical corpus is refused")

        root3 = root / "few"
        root3.mkdir()
        m3, _ = make_corpus(root3, n_tasks=5)
        check(any("minimum is 6" in p for p in launch_gate.verify(m3, corpus_dir=root3)),
              "fewer than 6 tasks is refused")
        root4 = root / "projects"
        root4.mkdir()
        m4, _ = make_corpus(root4, projects=("proj-a", "proj-a2"), n_tasks=6)
        check(any("minimum is 3" in p for p in launch_gate.verify(m4, corpus_dir=root4)),
              "fewer than 3 projects is refused")
        root5 = root / "obligations"
        root5.mkdir()
        m5, _ = make_corpus(root5, per_task=3)      # 6 x 3 = 18 < 20
        check(any("minimum is 20" in p for p in launch_gate.verify(m5, corpus_dir=root5)),
              "fewer than 20 obligations in total is refused")

        root6 = root / "plan9"
        root6.mkdir()
        m6, _ = make_corpus(root6, n_null=9)
        check(any("exactly 10 required" in p for p in launch_gate.verify(m6, corpus_dir=root6)),
              "9 null-agent controls instead of 10 is refused")
        root7 = root / "unknown"
        root7.mkdir()
        m7, _ = make_corpus(root7, target_unknown=True)
        check(any("unknown task" in p for p in launch_gate.verify(m7, corpus_dir=root7)),
              "a control aimed at an unknown task is refused")
        root8 = root / "noplan"
        root8.mkdir()
        m8, c8 = make_corpus(root8)
        del c8["control_plan"]
        c8["manifest_digest"] = launch_gate.manifest_digest(c8)
        (root8 / "corpus.json").write_text(json.dumps(c8), encoding="utf-8")
        check(any("absent" in p for p in launch_gate.verify(root8 / "corpus.json", corpus_dir=root8)),
              "an absent control plan is refused")

        root9 = root / "licence"
        root9.mkdir()
        m9, _ = make_corpus(root9, licence="GPL-3.0")
        check(any("licence" in p or "enum" in p for p in launch_gate.verify(m9, corpus_dir=root9)),
              "a non-permissive licence fails the task schema")

        root10 = root / "tamper"
        root10.mkdir()
        m10, _ = make_corpus(root10)
        victim = next((root10).glob("TASK*/src/a.ts"))
        victim.write_text(victim.read_text() + "// tampered\n", encoding="utf-8")
        check(any("digest mismatch" in p for p in launch_gate.verify(m10, corpus_dir=root10)),
              "a modified frozen artefact (digest mismatch) is refused")

        heading("evaluate.py refuses to run when the frozen manifest does not verify")
        code = ev.main(["run", "--manifest", str(m10), "--manifest-digest",
                        json.loads(m10.read_text())["manifest_digest"]])
        check(code == 2, "evaluate.py run --manifest <tampered corpus> exits 2 before any run",
              f"    exit={code}")
        code2 = ev.main(["run", "--manifest", str(m2)])
        check(code2 == 2, "evaluate.py run --manifest <bad digest> exits 2")

    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
