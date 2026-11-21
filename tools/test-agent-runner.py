#!/usr/bin/env python3
"""Tests for the M6-Lite model-call-counting runner (tools/agent_runner.py, tools/model_proxy.py).

ADR-004: every request to the model counts (retries, summaries, relaunches); at most 15
calls and 20 minutes per run; if the runner cannot measure the count, it refuses the
official launch. These tests prove the instrument: the proxy counts and enforces the
budget, the runner measures every call, the timeout fires, the launch gate refuses an
unmeasured run. A fake agent makes the HTTP calls; no real model is involved.

Run with `make test-python` (no Dafny, no Node needed), or `python tools/test-agent-runner.py`.
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agent_runner
import evaluate as ev
import model_proxy

FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


class StubUpstream(BaseHTTPRequestHandler):
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


def post_messages(base, n):
    codes = []
    for _ in range(n):
        req = urllib.request.Request(base + "/v1/messages", data=b"{}", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                codes.append(resp.status)
        except urllib.error.HTTPError as exc:
            codes.append(exc.code)
    return codes


def write_fake_agent(path, n_calls, sleep=0):
    """A fake agent: POSTs n_calls times to the model base URL, then sleeps. Written to a
    file so the command line stays trivially parseable."""
    path.write_text(
        "import os, time, urllib.request\n"
        "base = os.environ['ANTHROPIC_BASE_URL']\n"
        f"for _ in range({n_calls}):\n"
        "    try:\n"
        "        urllib.request.urlopen(urllib.request.Request(base + '/v1/messages', data=b'{}', method='POST'), timeout=10)\n"
        "    except Exception:\n"
        "        pass\n"
        f"time.sleep({sleep})\n", encoding="utf-8")
    return f"{sys.executable} {path}"


def synthetic_record(rid, group, violates, accepted, agent_runs=None):
    rec = {"id": rid, "group": group, "description": rid, "label_violates": violates,
           "oracle": {"violated": violates, "violates": violates}, "label_confirmed": True,
           "translation": {"ok": True}, "wall_seconds": 1.0}
    for arm in ev.ARMS:
        rec[arm] = {"accepted": accepted, "why": "dafny" if accepted else "BLOCK", "seconds": 1.0}
    rec["B"]["resource_units"] = 10
    if agent_runs:
        rec["agent_runs"] = agent_runs
    return rec


def main():
    with tempfile.TemporaryDirectory(prefix="selyth-runner-") as tmp:
        tmp = Path(tmp)
        stub = ThreadingHTTPServer(("127.0.0.1", 0), StubUpstream)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        upstream = f"http://127.0.0.1:{stub.server_address[1]}"

        heading("The proxy counts every POST to /v1/messages and enforces the budget with 429")
        counter_file = tmp / "counter.json"
        proxy = model_proxy.Proxy(max_calls=3, counter_file=counter_file, upstream=upstream)
        port = proxy.serve()
        base = f"http://127.0.0.1:{port}"
        codes = post_messages(base, 5)
        snap = model_proxy.read_counter(counter_file)
        check(codes == [200, 200, 200, 429, 429], "3 calls forwarded, 2 refused with 429", f"    {codes}")
        check(snap == {"calls": 3, "refused": 2, "budget": 3}, "counter file: calls=3 refused=2", f"    {snap}")
        with urllib.request.urlopen(base + "/health", timeout=5) as resp:
            health = json.loads(resp.read())
        check(health["calls"] == 3, "GET /health does not count as a model call", f"    {health}")
        proxy.stop()

        heading("The proxy binds loopback only, and a missing counter means 'not measured'")
        try:
            model_proxy.Proxy(max_calls=1, counter_file=tmp / "c.json", host="0.0.0.0")
            check(False, "Proxy refuses a non-loopback host")
        except model_proxy.ProxyError:
            check(True, "Proxy refuses a non-loopback host")
        check(model_proxy.read_counter(tmp / "nope.json") is None, "missing counter file reads as None")

        heading("agent_runner.run measures the calls of a real agent process through the proxy")
        workdir = tmp / "agent-work"
        record_path = tmp / "run1.json"
        agent_cmd = write_fake_agent(tmp / "fake_agent.py", 5)
        rec, code = agent_runner.run(agent_cmd, workdir, task_id="t1", arm="A1",
                                     max_calls=3, max_seconds=60, record_path=record_path,
                                     upstream=upstream)
        check(code == 0 and rec["measured"] is True, "run is measured, exit 0")
        check(rec["model_calls"] == 3 and rec["refused_calls"] == 2,
              "model_calls=3 (budget), refused_calls=2", f"    calls={rec['model_calls']} refused={rec['refused_calls']}")
        check(rec["timed_out"] is False and rec["wall_seconds"] > 0, "no timeout, wall time recorded")
        check(json.loads(record_path.read_text())["run_id"] == rec["run_id"], "record written to --record")

        heading("The wall-clock limit fires and is recorded")
        rec2, code2 = agent_runner.run(write_fake_agent(tmp / "sleepy_agent.py", 0, sleep=10),
                                       tmp / "agent-work2", max_calls=15, max_seconds=1,
                                       record_path=tmp / "run2.json", upstream=upstream)
        check(rec2["timed_out"] is True and rec2["wall_seconds"] < 9,
              "agent killed at max-seconds, timed_out=true", f"    wall={rec2['wall_seconds']}")

        heading("The launch gate refuses unmeasured runs (ADR-004)")
        records_dir = tmp / "records"
        records_dir.mkdir()
        (records_dir / "run1.json").write_text(json.dumps(rec))
        check(agent_runner.check_launch(records_dir) == 0, "check-launch accepts a measured run")
        (records_dir / "run2.json").write_text(json.dumps(rec2))
        check(agent_runner.check_launch(records_dir) == 0, "a timed-out but measured run is not a refusal")
        bad = dict(rec2, run_id="bad1", measured=False, model_calls=None, refused_calls=None)
        (records_dir / "bad.json").write_text(json.dumps(bad))
        check(agent_runner.check_launch(records_dir) == 2, "check-launch REFUSES an unmeasured run (exit 2)")
        empty = tmp / "empty"
        empty.mkdir()
        check(agent_runner.check_launch(empty) == 2, "check-launch REFUSES with no records at all")

        heading("evaluate.py merges the run records: summarize() and render() report the calls")
        runs_dir = tmp / "runs"
        runs_dir.mkdir()
        (runs_dir / "t1-a.json").write_text(json.dumps({"task_id": "t1", "arm": "A1", "run_id": rec["run_id"],
                                                         "model_calls": 3, "refused_calls": 2,
                                                         "wall_seconds": rec["wall_seconds"],
                                                         "timed_out": False, "measured": True}))
        (runs_dir / "t1-b.json").write_text(json.dumps({"task_id": "t1", "arm": "B", "run_id": "run-b1",
                                                         "model_calls": 5, "refused_calls": 0,
                                                         "wall_seconds": 9.0,
                                                         "timed_out": False, "measured": True}))
        runs = ev.load_agent_runs(runs_dir)
        check(set(runs) == {"t1"} and len(runs["t1"]) == 2
              and {r["arm"] for r in runs["t1"]} == {"A1", "B"},
              "load_agent_runs groups records by task_id as a list: both arms of one task are kept")
        records = [synthetic_record("t1", "ordinary", True, False,
                                    agent_runs=[{"run_id": rec["run_id"], "task_id": "t1", "arm": "A1",
                                                 "model_calls": 3, "refused_calls": 2,
                                                 "wall_seconds": 12.0, "timed_out": False, "measured": True},
                                                {"run_id": "run-b1", "task_id": "t1", "arm": "B",
                                                 "model_calls": 5, "refused_calls": 0,
                                                 "wall_seconds": 9.0, "timed_out": False, "measured": True}]),
                   synthetic_record("ctl", "control", False, True)]
        summary = ev.summarize(records)
        mc = summary["model_calls"]
        check(mc is not None and mc["total_calls"] == 8 and mc["over_budget"] == ["t1/A1"]
              and mc["unmeasured"] == [] and len(mc["runs"]) == 2,
              "summarize: total calls over both arms, over-budget and unmeasured lists",
              f"    {mc}")
        out = ev.render(records, summary)
        check("Model calls" in out and "15-call budget" in out, "render prints the model-call line")
        no_runs = ev.summarize([synthetic_record("ctl", "control", False, True)])
        check(no_runs["model_calls"] is None, "scripted corpus without agent runs: model_calls=None (no claim)")
        stub.shutdown()

    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
