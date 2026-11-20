#!/usr/bin/env python3
"""Benchmark the verification gate's cost on a real example module.

Answers the open experimental question E-05 / P-01 / failure mode F1 of
docs/architecture/decision-register.md: can the fast tier really finish on a
single unit in under two seconds, and the deep tier under two minutes? The
run is the full pipeline (lower -> Dafny backend -> policy engine) on an
example; the per-obligation cost recorded in the verification report is
collected into a bench report valid for docs/specifications/bench-schema.json.

`summarise` is pure: verification report -> bench report, no I/O, and
deterministic in everything except the measured fields it copies
(produced_at, wall_ms, cpu_ms, peak_rss_bytes).

Usage:
  bench.py --obligation-set S.json --policy P.json --workspace W.dfy [--lock L.json]
           [--dafny PATH] [--resource-limit N] [--time-limit SECONDS]
           [--produced-at RFC3339] [-o BENCH.json]
Prints a table, and the bench report JSON to stdout (or to -o). Exit status:
0 the run completed (whatever the decision), 2 usage, unreadable input or tool error.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import dafny_backend as backend
import obligation_set as oset
import policy_engine as engine
import ts_adapter
import validate

ROOT = Path(__file__).resolve().parent.parent
BENCH_SCHEMA = ROOT / "docs" / "specifications" / "bench-schema.json"
BENCH_VERSION = "0.1.0"

_COST_FIELDS = ("resource_units", "wall_ms", "cpu_ms", "peak_rss_bytes")


def summarise(report):
    """The bench report for one verification report: per-obligation cost plus totals."""
    entries = []
    for o in sorted(report["obligations"], key=lambda e: e["obligation_id"]):
        cost = o["evidence"].get("cost") or {}
        entry = {"obligation_id": o["obligation_id"], "outcome": o["outcome"]}
        for field in _COST_FIELDS:
            if field in cost:
                entry[field] = cost[field]
        if "timed_out" in cost:
            entry["timed_out"] = cost["timed_out"]
        entries.append(entry)
    totals = {"obligations": len(entries)}
    for field in _COST_FIELDS:
        values = [e[field] for e in entries if field in e]
        if values:
            # peak_rss_bytes is an upper bound per run, so the module total is the max, not the sum
            totals[field] = max(values) if field == "peak_rss_bytes" else sum(values)
    totals["timed_out"] = sum(1 for e in entries if e.get("timed_out"))
    set_subject = next(s for s in report["subject"] if s["role"] == "obligation-set")
    return {"selyth_bench_version": BENCH_VERSION,
            "produced_at": report["produced_at"],
            "report_id": report["report_id"],
            "decision": report["decision"]["result"],
            "policy_digest": report["policy"]["policy_digest"],
            "obligation_set_digest": set_subject["digest"],
            "toolchain": report["producer"]["toolchain"],
            "obligations": entries,
            "totals": totals}


def render_table(bench):
    """A human-readable table of the bench report (the JSON is the artefact)."""
    rows = [(e["obligation_id"], e["outcome"],
             e.get("resource_units", "-"), e.get("wall_ms", "-"),
             e.get("cpu_ms", "-"), e.get("peak_rss_bytes", "-"))
            for e in bench["obligations"]]
    t = bench["totals"]
    rows.append(("TOTAL", bench["decision"], t.get("resource_units", "-"), t.get("wall_ms", "-"),
                 t.get("cpu_ms", "-"), t.get("peak_rss_bytes", "-")))
    widths = [max(len(str(r[i])) for r in rows) for i in range(6)]
    header = ("obligation", "outcome", "resource_units", "wall_ms", "cpu_ms", "peak_rss_bytes")
    lines = ["  ".join(h.ljust(w) for h, w in zip(header, widths)),
             "  ".join("-" * w for w in widths)]
    lines += ["  ".join(str(c).ljust(w) for c, w in zip(r, widths)) for r in rows]
    lines.append(f"decision: {bench['decision']}; toolchain: "
                 + ", ".join(f"{t['name']} {t['version']}" for t in bench["toolchain"]))
    return "\n".join(lines)


def build_parser():
    ap = argparse.ArgumentParser(prog="bench.py", description=__doc__.split("Usage:")[0].strip())
    ap.add_argument("--obligation-set", required=True, dest="obligation_set")
    ap.add_argument("--policy", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--lock")
    ap.add_argument("--dafny", help="path to the dafny executable (or set SELYTH_DAFNY)")
    ap.add_argument("--resource-limit", type=backend._positive_int, default=backend.DEFAULT_RESOURCE_LIMIT)
    ap.add_argument("--time-limit", type=backend._positive_int, default=backend.DEFAULT_TIME_LIMIT)
    ap.add_argument("--produced-at", help="RFC3339 timestamp recorded in the report (default: now)")
    ap.add_argument("-o", dest="out", help="write the bench report JSON here instead of stdout")
    ap.add_argument("--source", help="TypeScript source: with --spec, the report is the merged gate "
                                     "report (Dafny + property tests), not the Dafny-only one")
    ap.add_argument("--spec", help="property-test sidecar (with --source)")
    ap.add_argument("--node", help="path to node (or set SELYTH_NODE)")
    return ap


def main(argv):
    args = build_parser().parse_args(argv[1:])
    try:
        import gate
        import property_backend
        document = oset.load_obligation_set(args.obligation_set)
        policy = validate.load_json(args.policy)
        if validate.validate(policy, validate.load_schema(
                oset.ROOT / "docs" / "specifications" / "policy-schema.json")):
            raise backend.BackendError("the policy is not valid against policy-schema.json")
        lock = engine.load_lock(args.lock) if args.lock else None
        dafny = backend.resolve_dafny(args.dafny)
        version, solver = backend.tool_versions(dafny)
        workspace = Path(args.workspace)
        produced_at = args.produced_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if args.source and args.spec:
            source = Path(args.source)
            node = property_backend.resolve_node(args.node)
            node_version = property_backend.node_version(node)
            spec = property_backend.load_spec(args.spec)
            report = gate.run_gate(document, Path(args.obligation_set).name, workspace.read_bytes(),
                                   workspace.name, policy, produced_at, dafny, solver, version,
                                   args.resource_limit, args.time_limit, lock,
                                   source_bytes=source.read_bytes(), source_name=str(source),
                                   spec=spec, node=node, node_version=node_version)
        else:
            ir = ts_adapter.lower(document, version)
            report = backend.build_report(
                ir, document, policy, workspace.read_bytes(), workspace.name,
                produced_at, dafny, solver, version, args.resource_limit, args.time_limit, lock)
    except (backend.BackendError, gate.GateError, property_backend.BackendError, oset.ObligationSetError,
            validate.SchemaError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    bench = summarise(report)
    problems = validate.validate(bench, validate.load_schema(BENCH_SCHEMA))
    if problems:
        print("[ERROR] the bench report is not valid: "
              + "; ".join(f"{p.path}: {p.message}" for p in problems), file=sys.stderr)
        return 2
    print(render_table(bench))
    text = json.dumps(bench, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
