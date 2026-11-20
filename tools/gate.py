#!/usr/bin/env python3
"""The gate: one Obligation Set, one policy, one lock, ONE report.

Runs the Dafny backend over the obligations the adapter can lower (contract clauses) and the
property-test backend over the acceptance-criterion obligations, merges the per-obligation
entries into a single report, and lets the policy engine decide once over the whole set.
The per-obligation evidence still names the tool that produced it. Specified in
docs/specifications/gate.md.

Usage:
  gate.py run --obligation-set S.json --workspace W.dfy --policy P.json [--lock L.json]
      [--source SRC.ts --spec PROPS.json] [--dafny PATH] [--node PATH]
      [--resource-limit N] [--time-limit SECONDS] [--produced-at RFC3339]
Prints the report. Exit status: 0 accepted, 1 rejected, 3 needs-human, 2 usage error,
unreadable input, or a tool that is not the declared one.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import dafny_backend
import obligation_set as oset
import policy_engine as engine
import property_backend
import ts_adapter
import validate

GATE_IDENTITY_VERSION = "0.1.0"


class GateError(RuntimeError):
    """The gate cannot honestly produce a report."""


def sha256_bytes(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def run_gate(obligation_set, set_name, workspace_bytes, workspace_name, policy, produced_at,
             dafny, solver, dafny_version, resource_limit, time_limit, lock=None,
             source_bytes=None, source_name=None, spec=None, node=None, node_version=None):
    """Run both backends over one Obligation Set and decide once. Does the I/O."""
    ir = ts_adapter.lower(obligation_set, dafny_version)
    source = dafny_backend.check_workspace(ir, obligation_set, workspace_bytes)
    entries = dafny_backend.obligation_results(ir, source, workspace_name, dafny, solver, dafny_version,
                                               resource_limit, time_limit, include_unsupported=False)
    canonical = oset.canonical_form(obligation_set)
    criteria = [o for o in canonical["obligations"] if o["kind"] == "acceptance-criterion"]
    node_tool = None
    if criteria:
        if spec is None or source_bytes is None or node is None:
            raise GateError("the set has acceptance-criterion obligations; pass --source, --spec and a "
                            "Node so the property-test backend can run them (otherwise they would be "
                            "not-run and the engine would reject them)")
        view = {**obligation_set, "obligations": criteria}
        entries += property_backend.run_criteria(view, spec, source_bytes, source_name, node,
                                                 node_version, time_limit)
        node_tool = {"name": "node", "version": node_version}
    digests = oset.obligation_digests(obligation_set)
    dafny_tool = {"name": dafny_backend.BACKEND_NAME, "version": dafny_version}
    for item in ir["unsupported"]:
        if item["kind"] == "acceptance-criterion":
            continue  # covered by the property backend above
        entries.append({"obligation_id": item["obligation_id"], "obligation_digest": digests[item["obligation_id"]],
                        "specification_version": obligation_set["specification_version"],
                        "kind": item["kind"], "statement": item["statement"],
                        "intent_statement": item.get("intent_statement"),
                        "required_evidence_kind": item["required_evidence_kind"],
                        "affected_components": item["affected_components"],
                        "outcome": "not-run",
                        "evidence": {"kind": "kernel-proven", "soundness": "none", "producer_tool": dafny_tool,
                                     "scope": "not run: the adapter could not lower this obligation to a Dafny "
                                              "clause, and no other backend handles this kind",
                                     "assumptions": [], "cost": {"timed_out": False}},
                        "diagnostics": [item["reason"]]})
    entries = sorted(entries, key=lambda e: e["obligation_id"])
    expected = sorted(o["obligation_id"] for o in canonical["obligations"])
    if [e["obligation_id"] for e in entries] != expected:
        raise GateError("internal: the merged entries do not cover the Obligation Set exactly once: "
                        f"{[e['obligation_id'] for e in entries]} vs {expected}")

    set_digest = oset.set_digest(obligation_set)
    policy_digest = engine.policy_digest(policy)
    toolchain = [dafny_tool, {"name": "boogie", "version": "bundled-with-dafny"},
                 {"name": "z3", "version": dafny_backend.Z3_VERSION}]
    if node_tool:
        toolchain.append(node_tool)
    identity_inputs = [set_digest, sha256_bytes(workspace_bytes), policy_digest, dafny_version,
                       dafny_backend.Z3_VERSION, ir["reproducibility_key"]]
    if source_bytes is not None:
        identity_inputs.append(sha256_bytes(source_bytes))
    if spec is not None:
        identity_inputs.append(sha256_bytes(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")))
    if node_version is not None:
        identity_inputs.append(node_version)
    identity = hashlib.sha256(json.dumps(identity_inputs, sort_keys=True).encode()).hexdigest()[:16]

    subjects = [{"name": workspace_name, "digest": sha256_bytes(workspace_bytes), "role": "proof-workspace"},
                {"name": set_name, "digest": set_digest, "role": "obligation-set"}]
    if source_bytes is not None:
        subjects.insert(1, {"name": Path(source_name).name, "digest": sha256_bytes(source_bytes),
                            "role": "implementation"})
    trusted = policy["approval"] != "unverified"
    limitations = [
        "Single-machine structural integrity only; no isolation from an agent with local code "
        "execution.",
        "Dafny, Boogie, Z3 and Node are trusted as installed; none is a verified kernel.",
        "Each contract obligation is verified alone by Dafny with the workspace's hand-written "
        "clauses; that the workspace models the implementation is not checked.",
    ]
    if criteria:
        limitations.append(f"The acceptance criteria rest on {spec['samples']} generated inputs (seed "
                           f"{spec['seed']}): a sample, not a proof (soundness 'empirical').")
    report = {
        "selyth_report_version": "0.1.0",
        "report_id": f"rpt-{identity}",
        "produced_at": produced_at,
        "subject": subjects,
        "producer": {
            "identity": f"local://selyth-gate/{GATE_IDENTITY_VERSION}",
            "kind": "local-subprocess", "outside_agent_boundary": False,
            "toolchain": toolchain},
        "policy": {"policy_id": policy["policy_id"], "policy_version": policy["policy_version"],
                   "policy_digest": policy_digest, "approved_by": policy["approved_by"],
                   "approval": policy["approval"]},
        "obligations": entries,
        "decision": {"result": "rejected", "policy_digest": policy_digest, "rules_evaluated": 0,
                     "violations": [{"rule_id": "PENDING", "obligation_id": None,
                                     "message": "the policy engine has not decided yet"}],
                     "escalations": []},
        "trust": {"model": "local-structural", "verifier_outside_agent_boundary": False,
                  "policy_approval_trusted": trusted,
                  "limitations": limitations},
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
    ap = argparse.ArgumentParser(prog="gate.py", description=__doc__.split("Usage:")[0].strip())
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="one Obligation Set -> one merged report -> one decision")
    run.add_argument("--obligation-set", required=True, dest="obligation_set")
    run.add_argument("--workspace", required=True, help="the Dafny proof workspace (.dfy)")
    run.add_argument("--policy", required=True)
    run.add_argument("--lock", help="the human-approved lock")
    run.add_argument("--source", help="the TypeScript source (needed when the set has acceptance-criteria)")
    run.add_argument("--spec", help="the property-test sidecar (needed with --source)")
    run.add_argument("--dafny", help="path to the dafny executable (or set SELYTH_DAFNY)")
    run.add_argument("--node", help="path to node (or set SELYTH_NODE)")
    run.add_argument("--resource-limit", type=_positive_int, default=dafny_backend.DEFAULT_RESOURCE_LIMIT,
                     help="solver resource limit per obligation (Dafny pass)")
    run.add_argument("--time-limit", type=_positive_int, default=dafny_backend.DEFAULT_TIME_LIMIT,
                     help="time limit per obligation / for the Node run, seconds")
    run.add_argument("--produced-at", help="RFC3339 timestamp recorded in the report (default: now)")
    return ap


def main(argv):
    args = build_parser().parse_args(argv[1:])
    try:
        document = oset.load_obligation_set(args.obligation_set)
        policy = validate.load_json(args.policy)
        if validate.validate(policy, validate.load_schema(
                oset.ROOT / "docs" / "specifications" / "policy-schema.json")):
            raise GateError("the policy is not valid against policy-schema.json")
        lock = engine.load_lock(args.lock) if args.lock else None
        dafny = dafny_backend.resolve_dafny(args.dafny)
        version, solver = dafny_backend.tool_versions(dafny)
        workspace = Path(args.workspace)
        spec = property_backend.load_spec(args.spec) if args.spec else None
        node, node_version, source_bytes, source_name = None, None, None, None
        if args.source:
            source = Path(args.source)
            source_bytes, source_name = source.read_bytes(), str(source)
            if spec is None:
                raise GateError("--source needs --spec (the property-test sidecar)")
            node = property_backend.resolve_node(args.node)
            node_version = property_backend.node_version(node)
        elif spec is not None:
            raise GateError("--spec without --source")
        report = run_gate(document, Path(args.obligation_set).name, workspace.read_bytes(),
                          workspace.name, policy,
                          args.produced_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                          dafny, solver, version, args.resource_limit, args.time_limit, lock,
                          source_bytes=source_bytes, source_name=source_name, spec=spec,
                          node=node, node_version=node_version)
    except (GateError, dafny_backend.BackendError, property_backend.BackendError, oset.ObligationSetError,
            validate.SchemaError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return {"accepted": 0, "rejected": 1, "needs-human": 3}[report["decision"]["result"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
