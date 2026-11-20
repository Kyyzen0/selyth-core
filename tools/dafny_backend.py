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
import argparse
import bisect
import csv
import hashlib
import json
import os
import re
import resource
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

# `// selyth:obligation OBL-1 OBL-2 ...` marks where the clauses of one contract go. Several
# identifiers may share a marker: the postconditions of a method are verified under the
# preconditions written at the same place, and a clause inside the body (a loop marker) under
# the whole contract of the enclosing declaration (see hypotheses_for).
MARKER = re.compile(r"^([ \t]*)// selyth:obligation((?:[ \t]+\S+)+)[ \t]*$")
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


def marker_ids(line):
    """(indentation, [obligation ids]) for a marker line, else None."""
    m = MARKER.match(line)
    return (m.group(1), m.group(2).split()) if m else None


def marker_count(source, obligation_id):
    return sum(1 for line in source.split("\n")
               if (found := marker_ids(line)) and obligation_id in found[1])


def workspace_marker_ids(source):
    """Every obligation id named by any marker in the workspace, in order of appearance."""
    ids = []
    for line in source.split("\n"):
        found = marker_ids(line)
        if found:
            ids.extend(found[1])
    return ids


_DECLARATION = re.compile(r"\b(?:method|function|lemma|predicate|constructor)\b(?:\s*\{[^}]*\})*\s+([A-Za-z_][A-Za-z0-9_']*)")


def enclosing_declaration(source, obligation_id):
    """Name of the method, function or lemma whose contract holds the obligation's marker."""
    lines = source.split("\n")
    for number, line in enumerate(lines):
        found = marker_ids(line)
        if found and obligation_id in found[1]:
            names = _DECLARATION.findall(strip_comments("\n".join(lines[:number])))
            return names[-1] if names else None
    return None


def declaration_count(source, name):
    """How many declarations of `name` the workspace has (methods in different classes may share one)."""
    return sum(1 for n in _DECLARATION.findall(strip_comments(source)) if n == name)


def call_sites(source, name):
    """How many times `name(` is used in the workspace other than at its own declaration.

    Lexical, on comment-free text: it counts a use that is a call. It cannot tell which of
    several same-named declarations a call resolves to, so the caller of this function refuses
    a name declared more than once (declaration_count); and a precondition with no call site
    found is reported as unknown rather than proven.
    """
    text, count = strip_comments(source), 0
    for m in re.finditer(r"\b" + re.escape(name) + r"\s*\(", text):
        before = text[:m.start()]
        if not re.search(r"\b(?:method|function|lemma|predicate|constructor)\b(?:\s*\{[^}]*\})*\s*$", before):
            count += 1
    return count


def _declaration_lines(source):
    """[(line number, name)] of every declaration in the workspace (comments stripped)."""
    text = strip_comments(source)
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    return [(bisect.bisect_right(starts, m.start()) - 1, m.group(1))
            for m in _DECLARATION.finditer(text)]


def contract_marker_line(source, obligation_id):
    """Line of the contract marker (the first marker after the declaration line) of the
    declaration enclosing the obligation's marker; None when the obligation's own marker is
    that contract marker, or when the enclosing declaration has no marker of its own."""
    lines = source.split("\n")
    marker_line = None
    for number, line in enumerate(lines):
        found = marker_ids(line)
        if found and obligation_id in found[1]:
            marker_line = number
            break
    if marker_line is None:
        return None
    decls = [n for n, _ in _declaration_lines(source) if n < marker_line]
    if not decls:
        return None
    for number in range(max(decls) + 1, marker_line):
        if marker_ids(lines[number]):
            return number
    return None


def hypotheses_for(source, unit, contract_clauses):
    """The contract clauses an obligation is verified under: [(id, clause)].

    A clause at a declaration's contract marker is verified with the `requires` clauses of
    the other obligations at its own marker, in marker order: a contract is proved under its
    preconditions, and each precondition is itself verified at the call sites in the
    workspace. A clause inside the body (a loop marker) is verified with the whole contract
    of the enclosing declaration — its `requires`, and its `decreases`, because a loop
    terminates by the method's measure — since that is the context in which Dafny checks a
    loop. A `requires` obligation is verified alone, so that those call sites are the only
    thing that can make it pass. Nothing else is ever assumed.
    """
    if unit["clause"]["keyword"] == "requires":
        return []
    lines = source.split("\n")
    for number, line in enumerate(lines):
        found = marker_ids(line)
        if found and unit["obligation_id"] in found[1]:
            own = [i for i in found[1]
                   if i != unit["obligation_id"] and i in contract_clauses
                   and contract_clauses[i].startswith("requires ")]
            contract = contract_marker_line(source, unit["obligation_id"])
            if contract is not None and contract != number:
                found_contract = marker_ids(lines[contract])
                own += [i for i in found_contract[1]
                        if i != unit["obligation_id"] and i in contract_clauses and i not in own]
            return [(i, contract_clauses[i]) for i in own]
    return []


def instantiate(source, obligation_id, clause, hypotheses=()):
    """Put `clause` at the obligation's marker and the `hypotheses` (contract clauses) where
    Dafny expects them: at the obligation's own marker when that is a contract marker, at the
    enclosing contract's marker when the clause sits inside the body (a loop marker accepts
    only loop clauses). Blank every other marker.

    The clauses share the marker's line, so line numbers are preserved and Dafny's
    diagnostics point into the workspace.
    """
    lines = source.split("\n")
    clause_line = None
    for number, line in enumerate(lines):
        found = marker_ids(line)
        if found and obligation_id in found[1]:
            clause_line = number
            break
    hyp_line = clause_line
    if clause_line is not None and hypotheses:
        contract = contract_marker_line(source, obligation_id)
        if contract is not None and contract != clause_line:
            hyp_line = contract
    out = []
    for number, line in enumerate(lines):
        found = marker_ids(line)
        if not found:
            out.append(line)
        elif number == clause_line:
            own = hypotheses if hyp_line == clause_line else ()
            out.append(f"{found[0]}{' '.join([*own, clause])}")
        elif number == hyp_line:
            out.append(f"{found[0]}{' '.join(hypotheses)}")
        else:
            out.append("")
    return "\n".join(out)


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


def _child_cost(before, after):
    """cpu_ms and peak_rss_bytes of the verifier subprocess, from getrusage(RUSAGE_CHILDREN).

    cpu_ms is the exact delta of user+system time over all children (this backend runs one
    prover at a time). peak_rss_bytes is after.ru_maxrss (KiB on Linux, bytes on macOS): a
    cumulative *maximum* over all children so far, hence an upper bound for this run, not an
    exact per-run value — recorded as such in the report schema.
    """
    cpu_s = (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
    rss_unit = 1024 if sys.platform.startswith("linux") else 1
    return {"cpu_ms": max(0, int(cpu_s * 1000)), "peak_rss_bytes": after.ru_maxrss * rss_unit}


def run_obligation(dafny, solver, workspace_name, source, unit, resource_limit, time_limit,
                   contract_clauses=None):
    """Verify one obligation; return (outcome, diagnostics, cost)."""
    oid, clause = unit["obligation_id"], unit["clause"]
    cost = {"timed_out": False}
    count = marker_count(source, oid)
    if count != 1:
        return "not-run", [f"the workspace has {count} markers for {oid}; exactly one is needed"], cost
    if clause["keyword"] == "requires":
        # Attached to a method nobody calls, a precondition makes the body easier and checks
        # nothing: the proof would be vacuous.
        name = enclosing_declaration(source, oid)
        if name is not None and declaration_count(source, name) > 1:
            return "unknown", [f"{oid} is a precondition of {name}, but the workspace declares {name} more "
                               "than once: a call site cannot be attributed to this one, so none is assumed"], cost
        if name is None or call_sites(source, name) == 0:
            return "unknown", [f"{oid} is a precondition, but the workspace has no call site of "
                               f"{name or 'its method'}: verifying it would prove nothing"], cost
    hypotheses = [c for _, c in hypotheses_for(source, unit, contract_clauses or {})]
    text = instantiate(source, oid, f"{clause['keyword']} {clause['expression']}", hypotheses)
    with tempfile.TemporaryDirectory(prefix="selyth-dafny-") as tmp:
        path = Path(tmp) / workspace_name
        path.write_text(text, encoding="utf-8")
        log = Path(tmp) / "log.csv"
        command = [str(dafny), "verify", "--solver-path", str(solver),
                   "--resource-limit", str(resource_limit),
                   "--verification-time-limit", str(time_limit),
                   "--log-format", f"csv;LogFileName={log}", str(path)]
        started = time.monotonic()
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=time_limit * 4 + 60)
        except subprocess.TimeoutExpired:
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            cost.update(wall_ms=int((time.monotonic() - started) * 1000), timed_out=True,
                        **_child_cost(before, after))
            return "timeout", ["the Dafny process was killed after its wall-clock allowance"], cost
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        cost["wall_ms"] = int((time.monotonic() - started) * 1000)
        cost.update(_child_cost(before, after))
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
    contract_clauses = {u["obligation_id"]: f"{u['clause']['keyword']} {u['clause']['expression']}"
                        for u in ir["units"] if u["clause"]["keyword"] in ("requires", "decreases")}
    for unit in ir["units"]:
        oid, clause = unit["obligation_id"], unit["clause"]
        under = hypotheses_for(source, unit, contract_clauses)
        scope = (f"Dafny {version} verification of `{clause['keyword']} {clause['expression']}` "
                 f"with the clauses written by hand in {workspace_name}, one obligation at a time"
                 + (f", under the clauses {', '.join(i for i, _ in under)} of its contract" if under else "")
                 + f" (resource limit {resource_limit}, time limit {time_limit}s, z3 {Z3_VERSION})")
        premises = unit["context"]["assumptions"] + [
            (f"{i} holds at every call site: {c}" if c.startswith("requires ") else f"{i}: {c}")
            for i, c in under]
        if escapes:
            outcome, cost = "failed", {"timed_out": False}
            diagnostics = [f"forbidden construct {name} at line {line} of {workspace_name}: the proof "
                           "would depend on something Dafny did not check or the report does not bind"
                           for line, name in escapes]
        else:
            outcome, diagnostics, cost = run_obligation(dafny, solver, workspace_name, source, unit,
                                                        resource_limit, time_limit, contract_clauses)
        entry = {**base(unit, unit["context"]["affected_components"]), "outcome": outcome,
                 "evidence": evidence(outcome, tool, scope, premises, cost)}
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
    known = {o["obligation_id"] for o in oset.canonical_form(obligation_set)["obligations"]}
    orphans = sorted(set(workspace_marker_ids(source)) - known)
    if orphans:
        raise BackendError("the workspace has markers for " + ", ".join(orphans) + " but the Obligation "
                           "Set does not declare them: a detached obligation must stay visible, so the "
                           "workspace and the set must be regenerated together (exit 2, no report)")
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


def _positive_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value}")
    return value


def build_parser():
    ap = argparse.ArgumentParser(prog="dafny_backend.py",
                                 description=__doc__.split("Usage:")[0].strip())
    sub = ap.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="IR + proof workspace + Obligation Set + policy -> report")
    verify.add_argument("--ir", required=True, help="the Verification IR (JSON)")
    verify.add_argument("--workspace", required=True, help="the Dafny proof workspace (.dfy)")
    verify.add_argument("--obligation-set", required=True, dest="obligation_set")
    verify.add_argument("--policy", required=True)
    verify.add_argument("--lock", help="the human-approved lock")
    verify.add_argument("--dafny", help="path to the dafny executable (or set SELYTH_DAFNY)")
    verify.add_argument("--resource-limit", type=_positive_int, default=DEFAULT_RESOURCE_LIMIT,
                        help=f"solver resource limit per obligation (default {DEFAULT_RESOURCE_LIMIT})")
    verify.add_argument("--time-limit", type=_positive_int, default=DEFAULT_TIME_LIMIT,
                        help=f"verification time limit per obligation, seconds (default {DEFAULT_TIME_LIMIT})")
    verify.add_argument("--produced-at", help="RFC3339 timestamp recorded in the report (default: now)")
    return ap


def main(argv):
    args = build_parser().parse_args(argv[1:])
    try:
        ir = validate.load_json(args.ir)
        problems = validate.validate(ir, validate.load_schema(
            oset.ROOT / "docs" / "specifications" / "verification-ir-schema.json"))
        if problems:
            raise BackendError("the IR is not valid: " + "; ".join(f"{p.path}: {p.message}" for p in problems))
        document = oset.load_obligation_set(args.obligation_set)
        policy = validate.load_json(args.policy)
        if validate.validate(policy, validate.load_schema(oset.ROOT / "docs" / "specifications" / "policy-schema.json")):
            raise BackendError("the policy is not valid against policy-schema.json")
        lock = engine.load_lock(args.lock) if args.lock else None
        dafny = resolve_dafny(args.dafny)
        version, solver = tool_versions(dafny)
        if ir["backend"] != {"name": BACKEND_NAME, "version": version}:
            raise BackendError(f"the IR targets {ir['backend']['name']} {ir['backend']['version']} "
                               f"but the installed tool is {BACKEND_NAME} {version}")
        workspace = Path(args.workspace)
        report = build_report(
            ir, document, policy, workspace.read_bytes(), workspace.name,
            args.produced_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            dafny, solver, version, args.resource_limit, args.time_limit, lock)
    except (BackendError, oset.ObligationSetError, validate.SchemaError, OSError,
            json.JSONDecodeError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return {"accepted": 0, "rejected": 1, "needs-human": 3}[report["decision"]["result"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
