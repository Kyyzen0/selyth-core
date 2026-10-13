#!/usr/bin/env python3
"""Dafny backend: Verification IR + proof workspace -> evidence report.

Specified in docs/specifications/dafny-backend.md. The backend runs Dafny as a
subprocess, one obligation at a time, maps what Dafny says onto an outcome
(proven / failed / timeout / unknown / not-run), and assembles a report that the
policy engine then decides. It is the only module that runs a process or reads
a clock; the adapter and the policy engine stay pure.

Usage:
  dafny_backend.py verify --ir IR.json --workspace W.dfy --obligation-set S.json \\
      --policy P.json [--lock LOCK.json] [--dafny PATH] [--resource-limit N] \\
      [--time-limit SECONDS] [--produced-at RFC3339]
Prints the report. Exit status: 0 accepted, 1 rejected, 3 needs-human,
2 usage error, unreadable input, or a tool that is not the declared one.
"""
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import obligation_set as oset
import policy_engine as engine
import ts_adapter
import validate

BACKEND_NAME = "dafny"
BACKEND_IDENTITY_VERSION = "0.1.0"
Z3_VERSION = "4.12.1"  # pinned by the backend and passed with --solver-path
DEFAULT_RESOURCE_LIMIT = 1_000_000
DEFAULT_TIME_LIMIT = 60

MARKER = re.compile(r"^([ \t]*)// selyth:obligation[ \t]+(\S+)[ \t]*$")
# Constructs that make a Dafny proof depend on something Dafny did not check, or that the
# report does not bind. `expect` is verified as an assumption (it is checked only at run
# time); `include` pulls in files that are neither verified, scanned nor in the report digest.
TRUST_ESCAPES = (
    (re.compile(r"\bassume\b"), "assume"),
    (re.compile(r"\bexpect\b"), "expect"),
    (re.compile(r"\binclude\b"), "include"),
    (re.compile(r"\{\s*:\s*axiom\b"), "{:axiom}"),
    (re.compile(r"\{\s*:\s*verify\s+false\b"), "{:verify false}"),
    (re.compile(r"\{\s*:\s*extern\b"), "{:extern}"),
)


class BackendError(RuntimeError):
    """The backend cannot honestly produce a report (wrong tool, missing tool, bad input)."""


def strip_comments(source):
    """Source with comments and string contents removed, line structure kept."""
    out, i, n = [], 0, len(source)
    while i < n:
        two = source[i:i + 2]
        if two == "//":
            while i < n and source[i] != "\n":
                i += 1
        elif two == "/*":
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append("\n" * source.count("\n", i, end))
            i = end
        elif source[i] == '"':
            i += 1
            while i < n and source[i] != '"' and source[i] != "\n":
                i += 2 if source[i] == "\\" else 1
            i += 1
            out.append('""')
        else:
            out.append(source[i])
            i += 1
    return "".join(out)


def trust_escapes(source):
    """[(line number, construct)] of every trust-escape construct in the workspace."""
    found = []
    for number, line in enumerate(strip_comments(source).split("\n"), 1):
        for pattern, name in TRUST_ESCAPES:
            if pattern.search(line):
                found.append((number, name))
    return found


def marker_count(source, obligation_id):
    return sum(1 for line in source.split("\n")
               if (m := MARKER.match(line)) and m.group(2) == obligation_id)


def instantiate(source, obligation_id, clause):
    """Put `clause` where the obligation's marker is; blank the other markers.

    Line numbers are preserved, so Dafny's diagnostics point into the workspace.
    """
    lines = []
    for line in source.split("\n"):
        m = MARKER.match(line)
        if m:
            lines.append(f"{m.group(1)}{clause}" if m.group(2) == obligation_id else "")
        else:
            lines.append(line)
    return "\n".join(lines)


def resolve_dafny(path=None):
    candidate = path or os.environ.get("SELYTH_DAFNY") or "dafny"
    from shutil import which
    found = which(candidate)
    if not found:
        raise BackendError(f"Dafny not found ({candidate!r}); pass --dafny or set SELYTH_DAFNY")
    return Path(found).resolve()


def tool_versions(dafny):
    run = subprocess.run([str(dafny), "--version"], capture_output=True, text=True, timeout=120)
    if run.returncode != 0 or not run.stdout.strip():
        raise BackendError(f"`{dafny} --version` failed: {run.stderr.strip()}")
    version = run.stdout.strip().split("+")[0]
    solver = dafny.parent / "z3" / "bin" / f"z3-{Z3_VERSION}"
    if not solver.exists():
        raise BackendError(f"the pinned solver z3-{Z3_VERSION} is not in {solver.parent}")
    return version, solver


_SUMMARY = re.compile(r"finished with (\d+) verified, (\d+) errors?")


def parse_dafny(text, returncode):
    """Map Dafny's output to (outcome, diagnostics)."""
    match = _SUMMARY.search(text)
    if not match:
        lines = [line for line in text.splitlines() if "Error" in line or "error" in line][:10]
        return "failed", lines or [f"Dafny exited with status {returncode} and no verification summary"]
    verified, errors = int(match.group(1)), int(match.group(2))
    out_of_resource = sum(int(n) for n in re.findall(r"(\d+) out of resource", text))
    timeouts = sum(int(n) for n in re.findall(r"(\d+) time ?outs?", text))
    if errors > 0:
        lines = [line.strip() for line in text.splitlines()
                 if "Error" in line or "Related location" in line or "could not be proved" in line]
        return "failed", lines[:10] or [f"{errors} verification error(s)"]
    if out_of_resource or timeouts:
        return "timeout", [f"Dafny stopped before reaching a verdict ({out_of_resource} out of "
                           f"resource, {timeouts} time out)"]
    if verified == 0:
        return "unknown", ["Dafny verified nothing: the clause was not attached to anything it checks"]
    if returncode != 0:
        warnings = [line.strip() for line in text.splitlines() if "Warning" in line][:5]
        return "failed", [f"Dafny reported no error but exited with status {returncode}"] + warnings
    return "proven", []


def run_obligation(dafny, solver, workspace_name, source, unit, resource_limit, time_limit):
    """Verify one obligation; return (outcome, diagnostics, cost)."""
    oid, clause = unit["obligation_id"], unit["clause"]
    cost = {"timed_out": False}
    count = marker_count(source, oid)
    if count != 1:
        return "not-run", [f"the workspace has {count} markers for {oid}; exactly one is needed"], cost
    text = instantiate(source, oid, f"{clause['keyword']} {clause['expression']}")
    with tempfile.TemporaryDirectory(prefix="selyth-dafny-") as tmp:
        path = Path(tmp) / workspace_name
        path.write_text(text, encoding="utf-8")
        log = Path(tmp) / "log.csv"
        command = [str(dafny), "verify", "--solver-path", str(solver),
                   "--resource-limit", str(resource_limit),
                   "--verification-time-limit", str(time_limit),
                   "--log-format", f"csv;LogFileName={log}", str(path)]
        started = time.monotonic()
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=time_limit * 4 + 60)
        except subprocess.TimeoutExpired:
            cost.update(wall_ms=int((time.monotonic() - started) * 1000), timed_out=True)
            return "timeout", ["the Dafny process was killed after its wall-clock allowance"], cost
        cost["wall_ms"] = int((time.monotonic() - started) * 1000)
        output = (run.stdout + run.stderr).replace(str(path), workspace_name)
        if log.exists():
            with log.open(newline="", encoding="utf-8") as handle:
                cost["resource_units"] = sum(int(row["TestResult.ResourceCount"] or 0)
                                             for row in csv.DictReader(handle))
    outcome, diagnostics = parse_dafny(output, run.returncode)
    cost["timed_out"] = outcome == "timeout"
    return outcome, diagnostics, cost


def evidence(outcome, tool, scope, assumptions, cost):
    return {"kind": "kernel-proven",
            "soundness": "sound-for-fragment" if outcome == "proven" else "none",
            "producer_tool": tool, "scope": scope, "assumptions": assumptions, "cost": cost}


def obligation_results(ir, source, workspace_name, dafny, solver, version, resource_limit, time_limit):
    tool = {"name": BACKEND_NAME, "version": version}
    spec_version = ir["obligation_set"]["specification_version"]
    results = []

    def base(entry, components):
        return {"obligation_id": entry["obligation_id"], "obligation_digest": entry["obligation_digest"],
                "specification_version": spec_version, "kind": entry["kind"],
                "statement": entry["statement"], "intent_statement": entry.get("intent_statement"),
                "required_evidence_kind": entry["required_evidence_kind"],
                "affected_components": components}

    escapes = trust_escapes(source)
    for unit in ir["units"]:
        oid, clause = unit["obligation_id"], unit["clause"]
        scope = (f"Dafny {version} verification of `{clause['keyword']} {clause['expression']}` "
                 f"with the clauses written by hand in {workspace_name}, one obligation at a time "
                 f"(resource limit {resource_limit}, time limit {time_limit}s, z3 {Z3_VERSION})")
        if escapes:
            outcome, cost = "failed", {"timed_out": False}
            diagnostics = [f"forbidden construct {name} at line {line} of {workspace_name}: the proof "
                           "would depend on something Dafny did not check or the report does not bind"
                           for line, name in escapes]
        else:
            outcome, diagnostics, cost = run_obligation(dafny, solver, workspace_name, source, unit,
                                                        resource_limit, time_limit)
        entry = {**base(unit, unit["context"]["affected_components"]), "outcome": outcome,
                 "evidence": evidence(outcome, tool, scope, unit["context"]["assumptions"], cost)}
        if diagnostics:
            entry["diagnostics"] = diagnostics
        results.append(entry)
    for item in ir["unsupported"]:
        scope = f"not run: the adapter could not lower this obligation to a {BACKEND_NAME} clause"
        results.append({**base(item, item["affected_components"]), "outcome": "not-run",
                        "evidence": evidence("not-run", tool, scope, [], {"timed_out": False}),
                        "diagnostics": [item["reason"]]})
    return sorted(results, key=lambda r: r["obligation_id"])


def sha256_bytes(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def build_report(ir, obligation_set, policy, workspace_bytes, workspace_name, produced_at,
                 dafny, solver, version, resource_limit, time_limit, lock=None):
    source = workspace_bytes.decode("utf-8")
    set_digest = oset.set_digest(obligation_set)
    if ir["obligation_set"]["digest"] != set_digest:
        raise BackendError("the IR was lowered from a different Obligation Set than the one given")
    if ir != ts_adapter.lower(obligation_set, ir["backend"]["version"]):
        raise BackendError("the IR differs from a fresh lowering of the Obligation Set (it was edited, "
                           "or made by another adapter version); regenerate it with ts_adapter.py")
    policy_digest = engine.policy_digest(policy)
    results = obligation_results(ir, source, workspace_name, dafny, solver, version,
                                 resource_limit, time_limit)
    identity = hashlib.sha256(json.dumps(
        [set_digest, sha256_bytes(workspace_bytes), policy_digest, version, Z3_VERSION,
         ir["reproducibility_key"]], sort_keys=True).encode()).hexdigest()[:16]
    trusted = policy["approval"] != "unverified"
    report = {
        "selyth_report_version": "0.1.0",
        "report_id": f"rpt-{identity}",
        "produced_at": produced_at,
        "subject": [
            {"name": workspace_name, "digest": sha256_bytes(workspace_bytes), "role": "proof-workspace"},
            {"name": f"{obligation_set['set_id']}.obligations.json", "digest": set_digest,
             "role": "obligation-set"}],
        "producer": {
            "identity": f"local://selyth-dafny-backend/{BACKEND_IDENTITY_VERSION}",
            "kind": "local-subprocess", "outside_agent_boundary": False,
            "toolchain": [{"name": BACKEND_NAME, "version": version},
                          {"name": "boogie", "version": "bundled-with-dafny"},
                          {"name": "z3", "version": Z3_VERSION}]},
        "policy": {"policy_id": policy["policy_id"], "policy_version": policy["policy_version"],
                   "policy_digest": policy_digest, "approved_by": policy["approved_by"],
                   "approval": policy["approval"]},
        "obligations": results,
        # Replaced below by the engine's decision. The engine validates its inputs, so even this
        # placeholder must be a valid decision: a rejection names a violation.
        "decision": {"result": "rejected", "policy_digest": policy_digest, "rules_evaluated": 0,
                     "violations": [{"rule_id": "PENDING", "obligation_id": None,
                                     "message": "the policy engine has not decided yet"}],
                     "escalations": []},
        "trust": {"model": "local-structural", "verifier_outside_agent_boundary": False,
                  "policy_approval_trusted": trusted,
                  "limitations": [
                      "Single-machine structural integrity only; no isolation from an agent with "
                      "local code execution.",
                      "Dafny, Boogie and Z3 are trusted as installed; none is a verified kernel.",
                      "Each obligation is verified alone with the workspace's hand-written clauses; "
                      "that the workspace models the implementation is not checked."]},
    }
    report["decision"] = engine.decide(policy, report, obligation_set, lock)
    return report


def parse_args(argv):
    options = {"--dafny": None, "--resource-limit": str(DEFAULT_RESOURCE_LIMIT),
               "--time-limit": str(DEFAULT_TIME_LIMIT), "--produced-at": None, "--lock": None}
    required = {"--ir": None, "--workspace": None, "--obligation-set": None, "--policy": None}
    if len(argv) < 2 or argv[1] != "verify":
        raise ValueError("usage")
    args = argv[2:]
    if len(args) % 2:
        raise ValueError("usage")
    for flag, value in zip(args[::2], args[1::2]):
        if flag in required:
            required[flag] = value
        elif flag in options:
            options[flag] = value
        else:
            raise ValueError("usage")
    if any(v is None for v in required.values()):
        raise ValueError("usage")
    return required, options


def main(argv):
    try:
        required, options = parse_args(argv)
        resource_limit, time_limit = int(options["--resource-limit"]), int(options["--time-limit"])
    except ValueError:
        print(__doc__.strip().split("Usage:")[1].split("Prints")[0].rstrip())
        return 2
    try:
        ir = validate.load_json(required["--ir"])
        problems = validate.validate(ir, validate.load_schema(
            oset.ROOT / "docs" / "specifications" / "verification-ir-schema.json"))
        if problems:
            raise BackendError("the IR is not valid: " + "; ".join(f"{p.path}: {p.message}" for p in problems))
        document = oset.load_obligation_set(required["--obligation-set"])
        policy = validate.load_json(required["--policy"])
        if validate.validate(policy, validate.load_schema(oset.ROOT / "docs" / "specifications" / "policy-schema.json")):
            raise BackendError("the policy is not valid against policy-schema.json")
        lock = engine.load_lock(options["--lock"]) if options["--lock"] else None
        dafny = resolve_dafny(options["--dafny"])
        version, solver = tool_versions(dafny)
        if ir["backend"] != {"name": BACKEND_NAME, "version": version}:
            raise BackendError(f"the IR targets {ir['backend']['name']} {ir['backend']['version']} "
                               f"but the installed tool is {BACKEND_NAME} {version}")
        workspace = Path(required["--workspace"])
        report = build_report(
            ir, document, policy, workspace.read_bytes(), workspace.name,
            options["--produced-at"] or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            dafny, solver, version, resource_limit, time_limit, lock)
    except (BackendError, oset.ObligationSetError, validate.SchemaError, OSError,
            json.JSONDecodeError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return {"accepted": 0, "rejected": 1, "needs-human": 3}[report["decision"]["result"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
