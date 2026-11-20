#!/usr/bin/env python3
"""The whole gate on one example, in one command.

For docs/specifications/examples/<example>/ this runs, in order: validate the Obligation Set
and its components; regenerate the Dafny workspace from the TypeScript and compare it byte
for byte with the committed one; run the gate (tools/gate.py: Dafny backend over the contract
clauses, property-test backend over the acceptance criteria when the example has a property
sidecar, one merged report); check the report against the schema; require accept under the
lock; replay the decision with the policy engine. Every step prints one line; the exit status
says whether all passed.

Usage:
  check_example.py --example ledger [--dafny PATH] [--node PATH]
Exit status: 0 every step passed, 1 a step failed (including an unknown example),
2 a tool or input could not be read.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import dafny_backend
import obligation_set as oset
import validate

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
EXAMPLES = ROOT / "docs" / "specifications" / "examples"


class StepError(RuntimeError):
    pass


def run_tool(name, *args, expect=0):
    """Run a sibling tool; return (returncode, stdout, stderr)."""
    run = subprocess.run([sys.executable, str(TOOLS / name), *args],
                         capture_output=True, text=True)
    if expect is not None and run.returncode != expect:
        raise StepError(f"{name} {' '.join(args)} exited {run.returncode} (expected {expect}):\n"
                        f"{(run.stderr or run.stdout).strip()[-600:]}")
    return run


def check_example(example, dafny_arg=None, node_arg=None):
    exdir = EXAMPLES / example
    base = exdir if exdir.is_dir() else EXAMPLES  # flat layout (queue) or subdirectory (ledger, gcd)
    if not (base / f"{example}.obligations.json").is_file():
        raise StepError(f"no such example: {EXAMPLES}/{example}[/] ({base / (example + '.obligations.json')} missing)")
    set_path = base / f"{example}.obligations.json"
    policy_path = base / f"{example}.policy.json"
    lock_path = base / f"{example}.lock.json"
    workspace_path = base / f"{example}.workspace.dfy"
    ts_path = base / f"{example}.ts"
    translation_path = base / f"{example}.translation.json"
    props_spec = base / f"{example}.properties.json"
    for path in (set_path, policy_path, lock_path, workspace_path):
        if not path.is_file():
            raise StepError(f"missing {path}")
    has_ts = ts_path.is_file() and translation_path.is_file()
    # A property sidecar means the example has acceptance-criterion obligations: the gate runs
    # both backends (Dafny + property tests) and decides once over the whole set.
    has_props = has_ts and props_spec.is_file()

    dafny = dafny_backend.resolve_dafny(dafny_arg)
    version, _solver = dafny_backend.tool_versions(dafny)

    steps = []

    def step(label, fn):
        fn()
        steps.append(label)
        print(f"[ok  ] {label}")

    def s_validate():
        run_tool("obligation_set.py", "check", str(set_path))
        if has_ts:
            run_tool("obligation_set.py", "check-components", str(set_path))

    def s_workspace():
        if not has_ts:
            return
        with tempfile.TemporaryDirectory(prefix="selyth-check-") as tmp:
            out = Path(tmp) / "workspace.dfy"
            run_tool("ts_to_dafny.py", str(ts_path), "--spec", str(translation_path),
                     "-o", str(out))
            if out.read_bytes() != workspace_path.read_bytes():
                raise StepError(f"the committed {workspace_path.name} is not the translation of "
                                f"{ts_path.name}; regenerate with `make {example}-workspace`")

    def s_verify():
        nonlocal report
        with tempfile.TemporaryDirectory(prefix="selyth-check-") as tmp:
            report_path = Path(tmp) / "report.json"
            cmd = [sys.executable, str(TOOLS / "gate.py"), "run", "--obligation-set", str(set_path),
                   "--workspace", str(workspace_path), "--policy", str(policy_path), "--lock",
                   str(lock_path), "--dafny", str(dafny)]
            if has_props:
                cmd += ["--source", str(ts_path), "--spec", str(props_spec)]
                if node_arg:
                    cmd += ["--node", node_arg]
            verify = subprocess.run(cmd, capture_output=True, text=True)
            if verify.returncode != 0:
                raise StepError(f"gate.py run exited {verify.returncode} (expected 0, accepted):\n"
                                f"{(verify.stderr or verify.stdout)[-600:]}")
            report_path.write_text(verify.stdout, encoding="utf-8")
            report = json.loads(verify.stdout)
            problems = validate.validate(report, validate.load_schema(oset.REPORT_SCHEMA))
            if problems:
                raise StepError("the report is not valid for report-schema.json: "
                                + "; ".join(f"{p.path} {p.message}" for p in problems))
            if report["decision"]["result"] != "accepted":
                raise StepError(f"the decision is {report['decision']['result']}, not accepted: "
                                f"{report['decision']['violations']}")
            run_tool("policy_engine.py", "replay", str(policy_path), str(report_path),
                     str(set_path), "--lock", str(lock_path))

    report = None
    pipeline = [(f"validate {set_path.name}" + (" + check-components" if has_ts else ""),
                 s_validate),
                (f"committed {workspace_path.name} is the translation of {ts_path.name}"
                 if has_ts else "workspace (no TypeScript to regenerate)", s_workspace),
                (f"gate.py run (Dafny {version}"
                 + (" + property tests" if has_props else "")
                 + ") + schema + accept under the lock + replay", s_verify)]
    for label, fn in pipeline:
        step(label, fn)
    print(f"\n{example}: all {len(steps)} steps passed "
          f"(Dafny {version}, report {report['report_id'] if report else 'n/a'})")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(prog="check_example.py", description=__doc__.split("Usage:")[0].strip())
    ap.add_argument("--example", required=True, help="the example directory name (ledger, gcd, ...)")
    ap.add_argument("--dafny", help="path to the dafny executable (or set SELYTH_DAFNY)")
    ap.add_argument("--node", help="path to node (or set SELYTH_NODE)")
    args = ap.parse_args(argv[1:])
    try:
        return check_example(args.example, args.dafny, args.node)
    except (StepError, dafny_backend.BackendError, OSError, validate.SchemaError,
            json.JSONDecodeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1 if isinstance(exc, StepError) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
