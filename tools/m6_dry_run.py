#!/usr/bin/env python3
"""M6-Lite launch rehearsal ("dry run"): the whole evaluator chain, end to end, on a synthetic
corpus that meets the ADR-004 minimums, with fake agents and no real model.

Stages (each also runnable alone):

  corpus  build a synthetic corpus (6 tasks / 3 projects / 24 obligations / 10+10 controls,
          hashed artefacts, manifest + digest, sources inside the widened subset) and check
          that the launch gate accepts it
  gate    launch_gate.py check accepts the manifest and refuses a wrong digest (exit 2)
  runs    12 fake agent runs (6 tasks x 2 arms) through agent_runner.py behind the loopback
          counting proxy, plus one over-budget run and one unmeasured run; check-launch
          accepts the measured set and refuses the unmeasured one
  score   evaluate.py run --manifest/--manifest-digest --runs over a 2-task slice of the
          scripted corpus (needs Dafny and Node; skipped without them); a wrong digest is
          refused with exit 2 before any scoring
  review  review.py classify/sample/package over a synthetic evidence report and one task of
          the synthetic corpus; the evidence package carries no scope and no oracle
  journal audit_journal.py append/verify/anchor/verify-anchor over the rehearsal events; a
          tampered entry breaks the chain

Usage:  python3 tools/m6_dry_run.py all --work DIR [--skip-scoring]

This rehearses the launch. It is not the experiment: no real model is called, and the corpus
is synthetic, so nothing here is a result of M6.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import agent_runner
import audit_journal
import evaluate
import launch_gate
import model_proxy
import review

WRONG_DIGEST = "sha256:" + "0" * 64
TASK_IDS = ["N00", "C01", "C02", "C03", "C04", "C05"]     # scripted-corpus ids used for the runs
ARMS = ["A1", "B"]                                          # the arm names the gate accepts (protocol A1 / B)

SOURCES = [
    "export function f(x: number): number { return x + 1; }\n",
    "export function f(x: number): number { const t: number[] = [3, 1, 2]; return t[0] + x; }\n",
    "export function g(x: number): number { return x * 2; }\n"
    "export function f(x: number): number { return g(x) + Math.min(x, 10); }\n",
]


def sha_text(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_artefact(root, path, content):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": path, "digest": sha_text(content)}


def obligation_set_doc(n):
    return {"selyth_obligation_set_version": "0.1.0", "set_id": "set", "specification_version": "1.0.0",
            "obligations": [{"obligation_id": f"O{i:02d}", "kind": "postcondition",
                             "statement": f"ensures result >= {i}", "required_evidence_kind": "kernel-proven",
                             "assumptions": [], "affected_components": ["src/a.ts"]} for i in range(n)]}


def build_corpus(root):
    """A synthetic corpus meeting the ADR-004 minimums; returns (manifest_path, corpus, digest)."""
    tasks = []
    for i in range(6):
        tid = f"TSK{i:05d}A"
        src = write_artefact(root, f"{tid}/src/a.ts", SOURCES[i % len(SOURCES)])
        obl = write_artefact(root, f"{tid}/obligations.json", json.dumps(obligation_set_doc(4)))
        orc = write_artefact(root, f"{tid}/oracle.ts", f"// oracle {tid}\n")
        tasks.append({"task_id": tid, "title": f"synthetic task {i}",
                      "prompt": f"Implement the synthetic task number {i} exactly as specified.",
                      "source": {"project": ("proj-a", "proj-b", "proj-c")[i % 3], "licence": "MIT",
                                 "revision": "dryrun0"},
                      "files": [src], "modifiable": ["src/a.ts"], "obligations": obl, "oracle": orc,
                      "budget": {"max_minutes": 20, "max_model_calls": 15}})
    controls = []
    for i in range(20):
        controls.append({"control_id": f"CTL{i:04d}A", "type": "null-agent" if i < 10 else "sabotage",
                         "task_id": tasks[i % 6]["task_id"], "arm": ARMS[i % 2],
                         "expected_scenario": "scripted", "expected_result": "rejected" if i >= 10 else "floor"})
    corpus = {"corpus_id": "dry-run-1", "tasks": tasks, "control_plan": {"controls": controls}}
    corpus["manifest_digest"] = launch_gate.manifest_digest(corpus)
    manifest = root / "corpus.json"
    manifest.write_text(json.dumps(corpus, indent=2) + "\n", encoding="utf-8")
    return manifest, corpus, corpus["manifest_digest"]


def cli(args, env=None):
    return subprocess.run([sys.executable, str(TOOLS / args[0])] + args[1:],
                          capture_output=True, text=True, env=env)


def stage_corpus(work):
    corpus_dir = work / "corpus"
    manifest, corpus, digest = build_corpus(corpus_dir)
    problems = launch_gate.verify(manifest)
    return problems == [], f"launch_gate.verify reports {problems or 'no problems'}", (manifest, corpus, digest)


def stage_gate(work, manifest, digest):
    ok_cli = cli(["launch_gate.py", "check", "--manifest", str(manifest)])
    wrong = launch_gate.verify(manifest, expected_digest=WRONG_DIGEST)
    wrong_cli = cli(["launch_gate.py", "check", "--manifest", str(manifest), "--digest", WRONG_DIGEST])
    ok = (ok_cli.returncode == 0 and wrong and wrong_cli.returncode == 2)
    detail = (f"cli check rc={ok_cli.returncode}; wrong digest: {len(wrong)} problem(s), "
              f"cli rc={wrong_cli.returncode}")
    return ok, detail, None


class _StubUpstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def write_fake_agent(path, n_calls):
    path.write_text(
        "import os, urllib.request\n"
        "base = os.environ['ANTHROPIC_BASE_URL']\n"
        f"for _ in range({n_calls}):\n"
        "    try:\n"
        "        urllib.request.urlopen(urllib.request.Request(base + '/v1/messages', data=b'{}', method='POST'), timeout=10)\n"
        "    except Exception:\n"
        "        pass\n", encoding="utf-8")
    return f"{sys.executable} {path}"


def stage_runs(work):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubUpstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{server.server_address[1]}"
    runs_dir = work / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    try:
        for i, tid in enumerate(TASK_IDS):
            for arm in ARMS:
                agent = write_fake_agent(work / f"agent_{i}_{arm}.py", 3)
                rec, rc = agent_runner.run(agent, work / f"wd_{i}_{arm}", task_id=tid, arm=arm,
                                           max_calls=15, max_seconds=120,
                                           record_path=runs_dir / f"run_{i}_{arm}.json", upstream=upstream)

                if not (rec["measured"] and rec["model_calls"] == 3 and rc == 0):
                    return False, f"run {tid}/{arm}: measured={rec['measured']} calls={rec['model_calls']} rc={rc}", None
        over = write_fake_agent(work / "agent_over.py", 10)
        rec, rc = agent_runner.run(over, work / "wd_over", task_id="CTL0000A", arm="A1",
                                   max_calls=2, max_seconds=120,
                                   record_path=runs_dir / "run_over.json", upstream=upstream)
        if not (rec["measured"] and rec["model_calls"] == 2 and rec["refused_calls"] == 8):
            return False, f"over-budget run: calls={rec['model_calls']} refused={rec['refused_calls']}", None
        bad_dir = work / "runs-bad"
        bad_dir.mkdir(exist_ok=True)
        shutil.copy(runs_dir / "run_0_A1.json", bad_dir / "run_0_A1.json")
        silent = write_fake_agent(work / "agent_silent.py", 0)
        rec, rc = agent_runner.run(silent, work / "wd_silent", task_id="N00", arm="B",
                                   max_calls=15, max_seconds=120,
                                   record_path=bad_dir / "run_silent.json", upstream=upstream)
        if not (rec["measured"] and rec["model_calls"] == 0 and rc == 0):
            return False, f"a zero-call run is still measured: measured={rec['measured']} calls={rec['model_calls']} rc={rc}", None
        unmeasured = dict(rec, run_id="bad1", measured=False, model_calls=None, refused_calls=None)
        (bad_dir / "run_unmeasured.json").write_text(json.dumps(unmeasured, indent=2) + "\n", encoding="utf-8")
        if agent_runner.check_launch(runs_dir) != 0:
            return False, "check-launch refused the 13 measured runs", None
        if agent_runner.check_launch(bad_dir) != 2:
            return False, "check-launch accepted an unmeasured run", None
        return True, ("12 runs x 3 calls measured, 1 over-budget (2 calls + 8 refused), "
                      "1 zero-call run still measured; check-launch accepts the 13-run set and "
                      "refuses a set holding an unmeasured record"), None
    finally:
        server.shutdown()


def find_dafny():
    explicit = os.environ.get("SELYTH_DAFNY")
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("dafny")
    if found:
        return found
    default = Path.home() / ".local" / "opt" / "dafny" / "dafny"
    return str(default) if default.is_file() else None


def find_node():
    return os.environ.get("NODE") or shutil.which("node")


def stage_score(work, manifest, digest):
    dafny, node = find_dafny(), find_node()
    if not (dafny and node):
        return "skip", f"no Dafny ({dafny!r}) or no Node ({node!r}): scoring stage skipped", None
    corpus = json.loads(evaluate.DEFAULT_TASKS.read_text(encoding="utf-8"))
    corpus["tasks"] = corpus["tasks"][:2]
    small = work / "tasks-small.json"
    small.write_text(json.dumps(corpus, indent=2) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env["SELYTH_DAFNY"] = dafny
    env["NODE"] = node
    results = work / "results.json"
    ok_cli = cli(["evaluate.py", "run", "--tasks", str(small), "--manifest", str(manifest),
                  "--manifest-digest", digest, "--runs", str(work / "runs"), "--jobs", "2",
                  "-o", str(results)], env=env)
    if ok_cli.returncode != 0:
        return False, f"evaluate.py run rc={ok_cli.returncode}: {ok_cli.stderr[-400:]}", None
    out = json.loads(results.read_text(encoding="utf-8"))
    mc = out["summary"]["model_calls"]
    if not (mc and len(mc["runs"]) == 4 and mc["total_calls"] == 12 and not mc["unmeasured"]):
        return False, f"model_calls summary unexpected: {mc}", None
    bad = work / "results-bad.json"
    bad_cli = cli(["evaluate.py", "run", "--tasks", str(small), "--manifest", str(manifest),
                   "--manifest-digest", WRONG_DIGEST, "-o", str(bad)], env=env)
    if bad_cli.returncode != 2 or bad.exists():
        return False, f"wrong digest: rc={bad_cli.returncode}, output exists={bad.exists()}", None
    return True, ("evaluate.py run scored the 2-task slice with 4 merged agent runs (12 calls); "
                  "a wrong manifest digest is refused with exit 2 before any scoring"), None


def stage_review(work, corpus):
    task = corpus["tasks"][0]
    task_file = work / "task.json"
    task_file.write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    set_path = work / "corpus" / task["obligations"]["path"]
    report = {"obligations": [
        {"obligation_id": "O00", "outcome": "proven",
         "evidence": {"kind": "kernel-proven", "soundness": "sound-for-fragment",
                      "scope": "free text, never read", "assumptions": []}},
        {"obligation_id": "O01", "outcome": "failed", "diagnostics": ["the postcondition could not be proved"]},
        {"obligation_id": "O02", "outcome": "failed", "diagnostics": ["solver gave up"]},
        {"obligation_id": "O03", "outcome": "timeout", "diagnostics": ["time limit"]},
    ]}
    report_file = work / "report.json"
    report_file.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    cls = cli(["review.py", "classify", "--report", str(report_file), "--set", str(set_path)])
    if cls.returncode != 0 or cls.stdout.split() != ["O02", "error", "O03", "timeout"]:
        return False, f"classify rc={cls.returncode} stdout={cls.stdout!r}", None
    s1 = cli(["review.py", "sample", "--cases", "O02,O03", "--manifest-digest", corpus["manifest_digest"],
              "--run-id", "run-0001"])
    s2 = cli(["review.py", "sample", "--cases", "O03,O02", "--manifest-digest", corpus["manifest_digest"],
              "--run-id", "run-0001"])
    if s1.returncode != 0 or not s1.stdout.strip() or s1.stdout != s2.stdout:
        return False, f"sample rc={s1.returncode} out={s1.stdout!r} vs {s2.stdout!r}", None
    pkg_file = work / "package.json"
    pkg = cli(["review.py", "package", "--task", str(task_file), "--set", str(set_path),
               "--report", str(report_file), "-o", str(pkg_file)])
    if pkg.returncode != 0:
        return False, f"package rc={pkg.returncode}: {pkg.stderr[-300:]}", None
    body = pkg_file.read_text(encoding="utf-8")
    if "scope" in body or "oracle" in body.lower() or '"set_digest"' not in body:
        return False, "the evidence package leaks scope/oracle or misses the set digest", None
    return True, ("classify: O02=error, O03=timeout (O00 clearly-proven, O01 clearly-violated); "
                  "sample deterministic over ceil(20%)=1 of 2 cases; package has no scope/oracle"), None


def stage_journal(work, digest):
    journal = work / "journal.jsonl"
    events = [{"event": "gate", "manifest_digest": digest, "decision": "allowed"}]
    for i, tid in enumerate(TASK_IDS):
        for arm in ARMS:
            events.append({"event": "run", "run_id": f"run{i}{arm}", "task_id": tid, "arm": arm,
                           "model_calls": 3})
    events.append({"event": "review", "cases": "O02,O03", "sample": "1"})
    for ev in events:
        args = ["audit_journal.py", "append", "--journal", str(journal)]
        for k, v in ev.items():
            args += ["--set", f"{k}={json.dumps(v)}"]
        if cli(args).returncode != 0:
            return False, f"append failed for {ev}", None
    if cli(["audit_journal.py", "verify", "--journal", str(journal)]).returncode != 0:
        return False, "verify failed on a fresh chain", None
    anchor = work / "anchor.json"
    if cli(["audit_journal.py", "anchor", "--journal", str(journal), "--out", str(anchor),
            "--operator", "dry-run"]).returncode != 0:
        return False, "anchor failed", None
    if cli(["audit_journal.py", "verify-anchor", "--journal", str(journal), "--anchor", str(anchor)]).returncode != 0:
        return False, "verify-anchor failed", None
    lines = journal.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["model_calls"] = 99
    lines[1] = json.dumps(tampered, sort_keys=True)
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if cli(["audit_journal.py", "verify", "--journal", str(journal)]).returncode != 2:
        return False, "a tampered entry did not break the chain", None
    return True, (f"{len(events)} events appended, chain verified, head anchored and re-verified, "
                  "tampered entry detected (exit 2)"), None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("stage", choices=["all", "corpus", "gate", "runs", "score", "review", "journal"])
    ap.add_argument("--work", help="working directory (default: a fresh temp dir)")
    ap.add_argument("--skip-scoring", action="store_true", help="skip the score stage in `all`")
    args = ap.parse_args(argv)

    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="selyth-m6-dry-"))
    work.mkdir(parents=True, exist_ok=True)
    failures = []

    def run_stage(name, fn, *extra):
        result = fn(work, *extra)
        if isinstance(result, tuple) and len(result) == 3 and result[0] == "skip":
            print(f"[SKIP] {name}: {result[1]}")
            return None
        ok, detail, out = result
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            failures.append(name)
        return out

    ctx = {}
    if args.stage in ("all", "corpus"):
        ctx["corpus"] = run_stage("corpus", stage_corpus)
    if ctx.get("corpus") is None and args.stage == "corpus":
        return 1 if failures else 0
    manifest, corpus, digest = ctx["corpus"] if ctx.get("corpus") else build_corpus(work / "corpus")
    if args.stage in ("all", "gate"):
        run_stage("gate", stage_gate, manifest, digest)
    if args.stage in ("all", "runs"):
        run_stage("runs", stage_runs)
    if args.stage in ("all", "score") and not (args.stage == "all" and args.skip_scoring):
        run_stage("score", stage_score, manifest, digest)
    if args.stage in ("all", "review"):
        run_stage("review", stage_review, corpus)
    if args.stage in ("all", "journal"):
        run_stage("journal", stage_journal, digest)

    if args.stage == "all":
        print(f"\n{'DRY RUN OK' if not failures else 'DRY RUN FAILED: ' + ', '.join(failures)} "
              f"(work dir: {work})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
