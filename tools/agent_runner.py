"""The M6-Lite agent runner: runs one agent attempt and MEASURES the model calls
(docs/evaluation/protocol.md section 3; docs/decisions/ADR-004-m6-launch-decisions.md).

ADR-004: at most 20 minutes and 15 model calls per run, every request counting (retries,
summaries, relaunches); "if the runner cannot measure this number, it refuses the official
launch". This runner starts a loopback-only counting proxy (tools/model_proxy.py), points
the agent at it through its base-URL variable, enforces the wall-clock limit, and writes a
per-run JSON record. `check-launch` refuses an official launch if any run is unmeasured.

  agent_runner.py run --agent-cmd "claude -p ..." --workdir DIR [--task-id T] [--arm A1]
                      [--max-calls 15] [--max-seconds 1200] [--record FILE]
  agent_runner.py check-launch --records-dir DIR

Exit codes: run -> 0 measured, 2 unmeasured (record still written); check-launch -> 0 ok,
2 refusal. Nothing here is ever converted into a monetary cost (ADR-004).
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import model_proxy

DEFAULT_MAX_CALLS = 15          # ADR-004
DEFAULT_MAX_SECONDS = 20 * 60   # ADR-004
BASE_URL_ENV = "ANTHROPIC_BASE_URL"


def run(agent_cmd, workdir, task_id=None, arm=None, max_calls=DEFAULT_MAX_CALLS,
        max_seconds=DEFAULT_MAX_SECONDS, record_path=None, upstream=None):
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    counter_file = workdir / ".model-counter.json"
    counter_file.unlink(missing_ok=True)
    upstream = upstream or os.environ.get("MODEL_UPSTREAM", model_proxy.DEFAULT_UPSTREAM)
    proxy = model_proxy.Proxy(max_calls, counter_file, upstream=upstream)
    port = proxy.serve()
    env = dict(os.environ)
    env[BASE_URL_ENV] = f"http://{model_proxy.LOOPBACK}:{port}"
    env["SELYTH_MODEL_COUNTER"] = str(counter_file)
    env["SELYTH_MODEL_BUDGET"] = str(max_calls)

    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    timed_out = False
    exit_code = None
    stdout_tail = stderr_tail = ""
    try:
        proc = subprocess.run(shlex.split(agent_cmd), cwd=workdir, env=env,
                              capture_output=True, text=True, timeout=max_seconds)
        exit_code = proc.returncode
        stdout_tail, stderr_tail = proc.stdout[-2000:], proc.stderr[-2000:]
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        stdout_tail = (exc.stdout or b"").decode("utf-8", "replace")[-2000:] if isinstance(exc.stdout, bytes) else (exc.stdout or "")[-2000:]
        stderr_tail = (exc.stderr or b"").decode("utf-8", "replace")[-2000:] if isinstance(exc.stderr, bytes) else (exc.stderr or "")[-2000:]
    finally:
        wall = round(time.monotonic() - t0, 2)
        proxy.stop()

    counter = model_proxy.read_counter(counter_file)
    measured = counter is not None
    record = {
        "run_id": uuid.uuid4().hex[:12],
        "task_id": task_id,
        "arm": arm,
        "agent_cmd": agent_cmd,
        "started_at": started_at,
        "wall_seconds": wall,
        "max_seconds": max_seconds,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "measured": measured,
        "model_calls": counter["calls"] if measured else None,
        "refused_calls": counter["refused"] if measured else None,
        "budget": max_calls,
        "submission_dir": str(workdir),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
    }
    out = Path(record_path) if record_path else workdir / "run-record.json"
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"agent_runner: run {record['run_id']} measured={measured} "
          f"calls={record['model_calls']} refused={record['refused_calls']} "
          f"wall={wall}s timed_out={timed_out} record={out}")
    return record, (0 if measured else 2)


def check_launch(records_dir):
    """The launch gate: refuse (exit 2) if any run could not be measured (ADR-004)."""
    records = []
    for path in sorted(Path(records_dir).glob("*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            records.append({"run_id": path.name, "measured": False})
    if not records:
        print("agent_runner: check-launch REFUSED: no run records", file=sys.stderr)
        return 2
    unmeasured = [r for r in records if r.get("measured") is not True]
    over = [r for r in records if (r.get("refused_calls") or 0) > 0]
    for r in records:
        print(f"  {r.get('run_id')} task={r.get('task_id')} arm={r.get('arm')} "
              f"calls={r.get('model_calls')} refused={r.get('refused_calls')} "
              f"wall={r.get('wall_seconds')}s timed_out={r.get('timed_out')} measured={r.get('measured')}")
    if unmeasured:
        print(f"agent_runner: check-launch REFUSED: {len(unmeasured)} unmeasured run(s) "
              f"({', '.join(str(r.get('run_id')) for r in unmeasured)})", file=sys.stderr)
        return 2
    total = sum(r["model_calls"] for r in records)
    print(f"agent_runner: check-launch OK: {len(records)} runs measured, {total} model calls total; "
          f"runs that hit the call budget (reported as findings, not refusals): "
          f"{[r.get('run_id') for r in over] or 'none'}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--agent-cmd", required=True)
    r.add_argument("--workdir", required=True)
    r.add_argument("--task-id")
    r.add_argument("--arm")
    r.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    r.add_argument("--max-seconds", type=int, default=DEFAULT_MAX_SECONDS)
    r.add_argument("--record")
    c = sub.add_parser("check-launch")
    c.add_argument("--records-dir", required=True)
    args = ap.parse_args(argv)
    if args.command == "run":
        _, code = run(args.agent_cmd, args.workdir, task_id=args.task_id, arm=args.arm,
                      max_calls=args.max_calls, max_seconds=args.max_seconds, record_path=args.record)
        return code
    return check_launch(args.records_dir)


if __name__ == "__main__":
    sys.exit(main())
