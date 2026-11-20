#!/usr/bin/env python3
"""Tests for the property-test backend (docs/specifications/property-backend.md), against a real Node.

The second backend: same report schema, same policy engine, same lock as the Dafny backend.
Run with `make test-properties` (needs Node >= 22.6 on PATH, or SELYTH_NODE=<path>).
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import obligation_set as oset
import policy_engine as engine
import property_backend as backend
import validate

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PROJECT = ROOT / "docs" / "specifications" / "examples" / "gcd"
FAILURES = []
PRODUCED_AT = "2026-11-20T12:00:00Z"
REPORT_SCHEMA = validate.load_schema(oset.REPORT_SCHEMA)

SET_PATH = PROJECT / "gcd.properties.obligations.json"
SPEC_PATH = PROJECT / "gcd.properties.json"
POLICY_PATH = PROJECT / "gcd.properties.policy.json"
LOCK_PATH = PROJECT / "gcd.properties.lock.json"
SOURCE_PATH = PROJECT / "gcd.ts"

SET = oset.load_obligation_set(SET_PATH)
SPEC = backend.load_spec(SPEC_PATH)
POLICY = validate.load_json(POLICY_PATH)
LOCK = validate.load_json(LOCK_PATH)
SOURCE = SOURCE_PATH.read_bytes()


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


def build(node, version, source_bytes=SOURCE, source_name=str(SOURCE_PATH), lock=LOCK):
    return backend.run_and_build(SET, SET_PATH.name, source_bytes, source_name, SPEC, POLICY,
                                 node, version, PRODUCED_AT, 60, lock)


def main():
    heading("The sample is deterministic and inside the declared domain")
    cases = backend.generate_cases(SPEC)
    check(len(cases) == SPEC["samples"], f"{SPEC['samples']} cases generated")
    check(cases == backend.generate_cases(SPEC), "the same seed gives the same sample, bit for bit")
    other = copy.deepcopy(SPEC)
    other["seed"] = SPEC["seed"] + 1
    check(cases != backend.generate_cases(other), "a different seed gives a different sample")
    check(all(1 <= c["a"] <= 40 and 1 <= c["b"] <= 40 for c in cases), "every case is inside 1..40")

    heading("The sidecar and the property expressions are validated (fail-closed)")
    for label, spec in (
            ("an unknown key", {**SPEC, "extra": 1}),
            ("a missing key", {k: v for k, v in SPEC.items() if k != "seed"}),
            ("a non-nat domain", {**SPEC, "params": {"a": {"domain": "int", "min": 0, "max": 9},
                                                       "b": {"domain": "nat", "min": 1, "max": 40}}}),
            ("min > max", {**SPEC, "params": {"a": {"domain": "nat", "min": 5, "max": 4},
                                               "b": {"domain": "nat", "min": 1, "max": 40}}}),
            ("zero samples", {**SPEC, "samples": 0}),
            ("too many samples", {**SPEC, "samples": backend.MAX_SAMPLES + 1}),
            ("a non-integer seed", {**SPEC, "seed": "x"}),
    ):
        with tempfile.TemporaryDirectory(prefix="selyth-prop-") as tmp:
            path = Path(tmp) / "spec.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            try:
                backend.load_spec(path)
                check(False, f"refused: {label}", "    accepted")
            except backend.BackendError:
                check(True, f"refused: {label}")
    for label, statement in (
            ("a statement not starting with ensures", "check a % result == 0"),
            ("an implication (not executable)", "ensures a > 0 ==> result > 0"),
            ("a bare division (real division in TS)", "ensures a / result == 0"),
            ("an expression outside the subset", "ensures a[0] > 0"),
    ):
        obligation = {"obligation_id": "X-1", "kind": "acceptance-criterion", "statement": statement,
                      "required_evidence_kind": "property-tested", "assumptions": [],
                      "affected_components": ["gcd.ts"]}
        try:
            backend.property_expression(obligation)
            check(False, f"refused: {label}", "    accepted")
        except backend.BackendError:
            check(True, f"refused: {label}")
    check(backend.property_expression({"statement": "ensures a % result == 0 && b % result == 0"})
          == "a % result == 0 && b % result == 0", "a subset property expression is accepted, canonical")
    check(backend.declared_function(SOURCE.decode("utf-8"), "gcd")["params"] == ["a", "b"]
          and backend.declared_function(SOURCE.decode("utf-8"), "nope") is None,
          "the declared function and its parameters are read from the source")

    try:
        node = backend.resolve_node()
        version = backend.node_version(node)
    except (backend.BackendError, OSError) as exc:
        check(False, "Node is available", f"    {exc}")
        print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
        return 1
    check(version.split(".")[0] >= "23" or version.startswith("22.") and
          tuple(int(p) for p in version.split(".")[:2]) >= (22, 6),
          f"Node {version} can strip types (>= 22.6)", f"    got v{version}")

    heading("The gcd acceptance criteria pass on the sample, and the report is decided under the lock")
    report = build(node, version)
    check(not validate.validate(report, REPORT_SCHEMA), "the report is valid for report-schema.json",
          "    " + "; ".join(f"{p.path} {p.message}" for p in validate.validate(report, REPORT_SCHEMA)))
    outcomes = {o["obligation_id"]: o["outcome"] for o in report["obligations"]}
    check(outcomes == {"GCD-P1": "passed", "GCD-P2": "passed"}, "both criteria passed on 200 inputs",
          f"    {outcomes}")
    check(all(o["evidence"]["kind"] == "property-tested" and o["evidence"]["soundness"] == "empirical"
              for o in report["obligations"]),
          "property-tested evidence is recorded as empirical, never as a proof")
    check(report["producer"]["identity"].startswith("local://selyth-property-backend/")
          and report["producer"]["toolchain"] == [{"name": "node", "version": version}],
          "the producer names the property backend and the Node that ran")
    check(all(o["evidence"]["cost"]["wall_ms"] >= 0 and o["evidence"]["cost"]["cpu_ms"] > 0
              and o["evidence"]["cost"]["peak_rss_bytes"] > 0 for o in report["obligations"]),
          "the run records wall_ms, cpu_ms and peak_rss_bytes")
    check(report["decision"]["result"] == "accepted" and engine.replay(POLICY, report, SET, LOCK),
          "the policy engine accepts under the lock, and replays its decision",
          f"    {report['decision']}")
    check(oset.check_coverage(SET, report) == [], "the report covers the Obligation Set exactly")
    check(LOCK == engine.make_lock(POLICY, SET, LOCK["approved_by"]),
          "the committed lock is the lock of this policy and this set")
    again = build(node, version)
    check(again["report_id"] == report["report_id"]
          and {o["obligation_id"]: o["outcome"] for o in again["obligations"]} == outcomes,
          "a second run gives the same report_id and the same outcomes (the sample is seeded)")

    heading("A bug in the source fails the criteria it violates, and is named")
    mutant = SOURCE.decode("utf-8").replace("return a;", "return a + 1;")
    check(mutant != SOURCE.decode("utf-8"), "(setup) the mutant source differs")
    bad = build(node, version, source_bytes=mutant.encode("utf-8"), source_name="/tmp/gcd-mutant.ts")
    check(not validate.validate(bad, REPORT_SCHEMA), "the failing report is still valid for the schema")
    outcomes = {o["obligation_id"]: o["outcome"] for o in bad["obligations"]}
    check(set(outcomes.values()) == {"failed"} and bad["decision"]["result"] == "rejected"
          and {(v["rule_id"], v["obligation_id"]) for v in bad["decision"]["violations"]}
          == {("BLOCK-FAILED", "GCD-P1"), ("BLOCK-FAILED", "GCD-P2")},
          "the mutant is rejected, BLOCK-FAILED per criterion", f"    {outcomes} {bad['decision']}")
    check(all(o.get("diagnostics") and "property" in o["diagnostics"][0] for o in bad["obligations"]),
          "each failure names a violating input", f"    {bad['obligations'][0].get('diagnostics')}")

    heading("Refusals and exit codes through the CLI")
    def run_cli(*args):
        return subprocess.run([sys.executable, str(TOOLS / "property_backend.py"), "test", *args],
                              capture_output=True, text=True)

    base = ("--obligation-set", str(SET_PATH), "--source", str(SOURCE_PATH), "--spec", str(SPEC_PATH),
            "--policy", str(POLICY_PATH), "--produced-at", PRODUCED_AT)
    r = run_cli(*base, "--lock", str(LOCK_PATH))
    check(r.returncode == 0 and json.loads(r.stdout)["decision"]["result"] == "accepted",
          "CLI: the gcd criteria exit 0 (accepted) under the lock")
    with tempfile.TemporaryDirectory(prefix="selyth-prop-") as tmp:
        mutant_path = Path(tmp) / "gcd-mutant.ts"
        mutant_path.write_text(mutant, encoding="utf-8")
        r = run_cli("--obligation-set", str(SET_PATH), "--source", str(mutant_path), "--spec",
                    str(SPEC_PATH), "--policy", str(POLICY_PATH), "--produced-at", PRODUCED_AT)
        check(r.returncode == 1 and json.loads(r.stdout)["decision"]["result"] == "rejected",
              "CLI: the mutant exits 1 (rejected)")
        wrong_set = copy.deepcopy(SET)
        wrong_set["obligations"][0]["statement"] = "ensures a % result == 0"
        wrong_path = Path(tmp) / "wrong.obligations.json"
        wrong_path.write_text(json.dumps(wrong_set), encoding="utf-8")
        r = run_cli("--obligation-set", str(wrong_path), "--source", str(SOURCE_PATH), "--spec",
                    str(SPEC_PATH), "--policy", str(POLICY_PATH), "--lock", str(LOCK_PATH),
                    "--produced-at", PRODUCED_AT)
        out = json.loads(r.stdout) if r.stdout else {}
        check(r.returncode == 1 and any(v["rule_id"] == "INTEGRITY-OBLIGATION-SET-NOT-FROZEN"
                                         for v in out.get("decision", {}).get("violations", [])),
              "CLI: a set the lock does not freeze is rejected at decide (exit 1, named rule)",
              f"    exit {r.returncode}: {out.get('decision', {}).get('violations')}")
        contract_set = copy.deepcopy(SET)
        contract_set["obligations"][0]["kind"] = "postcondition"
        contract_path = Path(tmp) / "contract.obligations.json"
        contract_path.write_text(json.dumps(contract_set), encoding="utf-8")
        r = run_cli("--obligation-set", str(contract_path), "--source", str(SOURCE_PATH), "--spec",
                    str(SPEC_PATH), "--policy", str(POLICY_PATH), "--produced-at", PRODUCED_AT)
        check(r.returncode == 2 and "acceptance-criterion" in r.stderr,
              "CLI: a contract clause in the property set is refused (exit 2)", f"    {r.stderr.strip()[:120]}")
        bad_spec = Path(tmp) / "bad-spec.json"
        bad_spec.write_text(json.dumps({**SPEC, "samples": 0}), encoding="utf-8")
        r = run_cli("--obligation-set", str(SET_PATH), "--source", str(SOURCE_PATH), "--spec",
                    str(bad_spec), "--policy", str(POLICY_PATH), "--produced-at", PRODUCED_AT)
        check(r.returncode == 2 and "samples" in r.stderr, "CLI: a bad sidecar exits 2", r.stderr.strip()[:120])
        r = run_cli(*base)
        check(r.returncode == 0 and json.loads(r.stdout)["decision"]["result"] == "accepted",
              "CLI: without the lock the engine still decides on its own (nothing is anchored)",
              f"    exit {r.returncode}")

    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
