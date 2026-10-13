#!/usr/bin/env python3
"""Tests for the Dafny backend (docs/specifications/dafny-backend.md), against a real Dafny.

Needs Dafny 4.11.0: set SELYTH_DAFNY or install it at ~/.local/opt/dafny/dafny.

Run with `make test`, or `python tools/test-dafny-backend.py` in the pinned environment.
"""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import dafny_backend as backend
import obligation_set as oset
import policy_engine as engine
import ts_adapter as adapter
import validate

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
EXAMPLES = ROOT / "docs" / "specifications" / "examples"
WORKSPACE = EXAMPLES / "queue.workspace.dfy"
FAILURES = []
PRODUCED_AT = "2026-10-13T12:00:00Z"


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


spec = importlib.util.spec_from_file_location("test_obligation_set", TOOLS / "test-obligation-set.py")
osets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(osets)

REPORT_SCHEMA = validate.load_schema(oset.REPORT_SCHEMA)
POLICY = validate.load_json(EXAMPLES / "queue.policy.json")


def locate_dafny():
    default = Path.home() / ".local" / "opt" / "dafny" / "dafny"
    return backend.resolve_dafny(os.environ.get("SELYTH_DAFNY") or str(default))


def pipeline(dafny, solver, version, document=None, workspace=None, resource_limit=1_000_000,
             time_limit=60, policy=None, ir_edit=None, lock=None):
    document = document or osets.demo()
    ir = adapter.lower(document, version)
    if ir_edit:
        ir_edit(ir)
    data = (workspace if workspace is not None else WORKSPACE.read_text(encoding="utf-8")).encode("utf-8")
    return backend.build_report(ir, document, policy or POLICY, data, "queue.workspace.dfy",
                                PRODUCED_AT, dafny, solver, version, resource_limit, time_limit, lock)


def outcomes(report):
    return {o["obligation_id"]: o["outcome"] for o in report["obligations"]}


def violations(report):
    return [(v["rule_id"], v["obligation_id"]) for v in report["decision"]["violations"]]


def valid(report):
    return not validate.validate(report, REPORT_SCHEMA)


def check_units():
    heading("Parsing Dafny's output")
    cases = [
        ("Dafny program verifier finished with 3 verified, 0 errors", 0, "proven"),
        ("Dafny program verifier finished with 0 verified, 0 errors", 0, "unknown"),
        ("x.dfy(3,24): Related location: this is the postcondition that could not be proved\n"
         "Dafny program verifier finished with 0 verified, 1 error", 4, "failed"),
        ("Error: Verification out of resource (M)\nDafny program verifier finished with "
         "0 verified, 0 errors, 1 out of resource", 4, "timeout"),
        ("Dafny program verifier finished with 0 verified, 0 errors, 2 time outs", 4, "timeout"),
        ("Dafny program verifier finished with 1 verified, 1 error, 1 out of resource", 4, "failed"),
        ("x.dfy(1,0): Error: unresolved identifier 'q'\n1 resolution/type errors detected in x.dfy", 2, "failed"),
        ("", 137, "failed"),
        ("Dafny program verifier finished with 2 verified, 0 errors", 1, "failed"),
    ]
    for text, code, want in cases:
        outcome, diagnostics = backend.parse_dafny(text, code)
        check(outcome == want and (outcome in ("proven",) or diagnostics),
              f"exit {code}: {text.splitlines()[-1][:60] if text else '(empty)'!r} -> {want}",
              f"    got {outcome} {diagnostics}")

    heading("Workspace handling")
    source = WORKSPACE.read_text(encoding="utf-8")
    out = backend.instantiate(source, "OBL-0008", "invariant 0 <= size <= capacity")
    check(out.count("\n") == source.count("\n"), "instantiate keeps the number of lines")
    check("invariant 0 <= size <= capacity" in out
          and not any(backend.MARKER.match(l) for l in out.split("\n"))
          and "decreases queue.length" not in out, "only the chosen obligation's clause is put in")
    line = next(l for l in out.split("\n") if "invariant 0 <=" in l)
    check(line.startswith("    invariant"), "the marker's indentation is kept")
    check(backend.marker_count(source, "OBL-0007") == 1 and backend.marker_count(source, "OBL-9999") == 0,
          "markers are counted per obligation")

    heading("Several obligations at one marker: postconditions are verified under the preconditions")
    shared = "method M()\n  // selyth:obligation A B C\n{ }"
    unit = lambda oid, kw: {"obligation_id": oid, "clause": {"keyword": kw, "expression": "e"}}
    req = {"A": "requires a", "C": "requires c"}
    check(backend.marker_count(shared, "B") == 1 and backend.marker_count(shared, "D") == 0,
          "an identifier is found among those sharing a marker")
    check(backend.hypotheses_for(shared, unit("B", "ensures"), req) == [("A", "requires a"), ("C", "requires c")],
          "a postcondition takes the other obligations' requires clauses, in marker order, as hypotheses")
    check(backend.hypotheses_for(shared, unit("A", "requires"), req) == [],
          "a precondition is verified alone: only its call sites can make it pass")
    check(backend.hypotheses_for(shared, unit("B", "ensures"), {}) == [] and backend.hypotheses_for(shared, unit("Z", "ensures"), req) == [],
          "no preconditions, or no marker, means no hypotheses (nothing else is ever assumed)")
    inst = backend.instantiate(shared, "B", "ensures b", ["requires a", "requires c"])
    check(inst.split("\n")[1] == "  requires a requires c ensures b" and inst.count("\n") == shared.count("\n"),
          "the clauses share the marker's line, so line numbers are preserved")
    check(backend.instantiate(shared, "A", "requires a").split("\n")[1] == "  requires a",
          "a precondition is instantiated alone")

    heading("Call sites of a precondition")
    for text, want in (
            ("method M(x: int)\n  // selyth:obligation A\n{ }", "M"),
            ("lemma L() { }\nmethod {:timeLimit 5} Slow(x: int)\n  // selyth:obligation A\n{ }", "Slow"),
            ("function F(x: int): int\n  // selyth:obligation A\n{ x }", "F"),
            ("// method Hidden()\nmethod Real()\n  // selyth:obligation A\n{ }", "Real"),
            ("datatype T = T(x: int)\n// selyth:obligation A", None)):
        check(backend.enclosing_declaration(text, "A") == want, f"enclosing declaration of a marker: {want}",
              f"    got {backend.enclosing_declaration(text, 'A')}")
    check(backend.enclosing_declaration("method M()\n// selyth:obligation A", "B") is None, "no marker, no declaration")
    code = "method M(x: int)\n{ }\nmethod C() { M(1); var y := M (2); }\n// M(3) in a comment\n"
    check(backend.call_sites(code, "M") == 2, "call sites: the declaration and comments are not calls, spacing before ( is fine",
          f"    got {backend.call_sites(code, 'M')}")
    check(backend.call_sites("method M()\n{ }", "M") == 0 and backend.call_sites("method Mx()\n{ Mx(); }", "M") == 0,
          "an uncalled method has none; a longer name is not a call of a shorter one")
    check(backend.call_sites("function {:opaque} F(): int { 1 }\nmethod C() { var z := F(); }", "F") == 1,
          "a declaration with an attribute is still a declaration")

    heading("Trust-escape scan")
    for text, expected in (
        ("method M() { assume true; }", [("assume")]),
        ("method M() { expect false; }", ["expect"]),
        ('include "lib.dfy"', ["include"]),
        ("var expected := 1; var inclusion := 2;", []),
        ("lemma {:axiom} L() ensures false", ["{:axiom}"]),
        ("lemma { :axiom } L()", ["{:axiom}"]),
        ("method {:verify false} M() {}", ["{:verify false}"]),
        ("method {:extern} M()", ["{:extern}"]),
        ("// assume true;\nmethod M() {}", []),
        ("/* assume\n true */ method M() {}", []),
        ('method M() { print "assume {:axiom}"; }', []),
        ("var assumed := 1; var reassume := 2;", []),
    ):
        found = [name for _, name in backend.trust_escapes(text)]
        check(found == expected, f"trust escapes in {text[:44]!r}: {expected}", f"    got {found}")
    check(backend.trust_escapes("/* a\nb */\nmethod M() { assume true; }") == [(3, "assume")],
          "line numbers survive multi-line comments")


def check_pipeline(dafny, solver, version):
    heading("Known-correct obligations are kernel-proven (the M3 criterion)")
    report = pipeline(dafny, solver, version)
    check(valid(report), "the report is valid for report-schema.json",
          "    " + "; ".join(f"{p.path} {p.message}" for p in validate.validate(report, REPORT_SCHEMA)))
    check(outcomes(report) == {"OBL-0007": "proven", "OBL-0008": "proven", "OBL-0009": "proven"},
          "all three obligations are proven by real Dafny", f"    {outcomes(report)}")
    check(all(o["evidence"]["kind"] == "kernel-proven" and o["evidence"]["soundness"] == "sound-for-fragment"
              for o in report["obligations"]), "as kernel-proven, sound-for-fragment")
    check(oset.check_coverage(osets.demo(), report) == [], "the report covers the Obligation Set exactly")
    check(report["decision"]["result"] == "accepted" and engine.replay(POLICY, report, osets.demo()),
          "the policy engine accepts it and replays the embedded decision")
    check(report["producer"]["kind"] == "local-subprocess" and not report["producer"]["outside_agent_boundary"]
          and report["trust"]["model"] == "local-structural", "it claims local-structural trust only")
    check({t["name"]: t["version"] for t in report["producer"]["toolchain"]}["dafny"] == version
          and {t["name"]: t["version"] for t in report["producer"]["toolchain"]}["z3"] == "4.12.1",
          "the toolchain names the Dafny and Z3 that ran")
    check(all(o["evidence"]["cost"]["resource_units"] > 0 for o in report["obligations"]),
          "each proof records its resource units")

    again = pipeline(dafny, solver, version)
    check(outcomes(again) == outcomes(report) and again["report_id"] == report["report_id"],
          "a second run gives the same outcomes and the same report_id")
    check([o["evidence"]["cost"]["resource_units"] for o in again["obligations"]]
          == [o["evidence"]["cost"]["resource_units"] for o in report["obligations"]],
          "resource units are deterministic across runs (wall time is not, and is not compared)")

    heading("A false obligation is failed, and named")
    wrong = osets.demo()
    wrong["obligations"][0]["statement"] = "ensures result.length > 5"
    report = pipeline(dafny, solver, version, document=wrong)
    check(valid(report) and outcomes(report) == {"OBL-0007": "failed", "OBL-0008": "proven", "OBL-0009": "proven"},
          "only the false obligation fails", f"    {outcomes(report)}")
    failed = next(o for o in report["obligations"] if o["obligation_id"] == "OBL-0007")
    check(any("postcondition" in d for d in failed["diagnostics"])
          and all(str(ROOT) not in d and "selyth-dafny-" not in d for d in failed["diagnostics"]),
          "with Dafny's diagnostics, free of temporary paths", f"    {failed['diagnostics']}")
    check(report["decision"]["result"] == "rejected" and violations(report) == [("BLOCK-FAILED", "OBL-0007")],
          "the policy engine rejects it, naming BLOCK-FAILED and the obligation", f"    {violations(report)}")
    check(oset.check_coverage(wrong, report) == [], "a failed obligation is still covered")

    heading("A trust escape cannot launder a false obligation")
    cheat = WORKSPACE.read_text(encoding="utf-8").replace(
        "  result := Queue(l.length + 1, l.size, l.capacity);",
        "  assume {:axiom} l.length > 99;\n  result := Queue(l.length + 1, l.size, l.capacity);")
    source = backend.instantiate(cheat, "OBL-0007", "ensures result.length > 5")
    with tempfile.TemporaryDirectory(prefix="selyth-dafny-") as tmp:
        path = Path(tmp) / "cheat.dfy"
        path.write_text(source, encoding="utf-8")
        alone = subprocess.run([str(dafny), "verify", "--solver-path", str(solver), str(path)],
                               capture_output=True, text=True)
    check(alone.returncode == 0 and "0 errors" in alone.stdout,
          "(setup) Dafny alone, exit 0, accepts the false obligation once the workspace assumes it",
          f"    exit {alone.returncode}: {alone.stdout[-200:]}")
    cheat_set = osets.demo()
    cheat_set["obligations"][0]["statement"] = "ensures result.length > 5"
    report = pipeline(dafny, solver, version, document=cheat_set, workspace=cheat)
    check(valid(report) and set(outcomes(report).values()) == {"failed"},
          "the backend fails every obligation of the workspace", f"    {outcomes(report)}")
    check(all("forbidden construct assume at line" in o["diagnostics"][0] for o in report["obligations"])
          and "wall_ms" not in report["obligations"][0]["evidence"]["cost"],
          "naming the construct and the line, without running Dafny")
    check(report["decision"]["result"] == "rejected", "so the policy engine rejects")
    quiet = WORKSPACE.read_text(encoding="utf-8").replace(
        "datatype Queue", "// we never assume anything: {:axiom} is forbidden\ndatatype Queue", 1)
    check(set(outcomes(pipeline(dafny, solver, version, workspace=quiet)).values()) == {"proven"},
          "the same words in a comment do not trigger the scan")

    for construct, injected in (("expect", "  expect false;\n"), ("include", None)):
        body = "  result := Queue(l.length + 1, l.size, l.capacity);"
        text = WORKSPACE.read_text(encoding="utf-8")
        text = text.replace(body, injected + body) if injected else 'include "/nonexistent/lib.dfy"\n' + text
        report = pipeline(dafny, solver, version, workspace=text)
        check(valid(report) and set(outcomes(report).values()) == {"failed"}
              and all(f"forbidden construct {construct} at line" in o["diagnostics"][0] for o in report["obligations"])
              and "wall_ms" not in report["obligations"][0]["evidence"]["cost"],
              f"`{construct}` fails every obligation without running Dafny", f"    {outcomes(report)}")
    expect_source = backend.instantiate(WORKSPACE.read_text(encoding="utf-8").replace(
        "  result := Queue(l.length + 1, l.size, l.capacity);",
        "  expect false;\n  result := Queue(l.length + 1, l.size, l.capacity);"),
        "OBL-0007", "ensures result.length > 5")
    with tempfile.TemporaryDirectory(prefix="selyth-dafny-") as tmp:
        path = Path(tmp) / "expect.dfy"
        path.write_text(expect_source, encoding="utf-8")
        alone = subprocess.run([str(dafny), "verify", "--solver-path", str(solver), str(path)],
                               capture_output=True, text=True)
    check(alone.returncode == 0 and "0 errors" in alone.stdout,
          "(setup) Dafny alone, exit 0, accepts a false obligation after `expect false`",
          f"    exit {alone.returncode}: {alone.stdout[-200:]}")
    bodyless = WORKSPACE.read_text(encoding="utf-8") + "\nlemma Free() ensures false\n"
    report = pipeline(dafny, solver, version, workspace=bodyless)
    check(set(outcomes(report).values()) == {"failed"} and any("Warning" in d for d in report["obligations"][0]["diagnostics"]),
          "a body-less lemma is caught through Dafny's warning exit status, and the warning is reported",
          f"    {report['obligations'][0].get('diagnostics')}")

    heading("The IR must be the lowering of the Obligation Set")

    def tamper(ir):
        ir["units"][0]["clause"]["expression"] = "true"
    try:
        pipeline(dafny, solver, version, ir_edit=tamper)
        check(False, "an IR whose clause was replaced by `true` is refused", "    a report was produced")
    except backend.BackendError as exc:
        check("fresh lowering" in str(exc), "an IR whose clause was replaced by `true` is refused", f"    {exc}")

    def other_adapter(ir):
        ir["adapter"]["version"] = "9.9.9"
    try:
        pipeline(dafny, solver, version, ir_edit=other_adapter)
        check(False, "an IR from another adapter version is refused", "    a report was produced")
    except backend.BackendError:
        check(True, "an IR from another adapter version is refused (regenerate it)")

    heading("No verdict")
    report = pipeline(dafny, solver, version, resource_limit=1)
    check(valid(report) and set(outcomes(report).values()) == {"timeout"},
          "an exhausted resource limit is a timeout", f"    {outcomes(report)}")
    timed = report["obligations"][0]["evidence"]
    check(timed["soundness"] == "none" and timed["cost"]["timed_out"] is True and "resource limit 1" in timed["scope"],
          "with no soundness claim, timed_out set, and the limit recorded in the scope")
    check(report["decision"]["result"] == "rejected" and {v[0] for v in violations(report)} == {"BLOCK-TIMEOUT"},
          "the policy engine rejects with BLOCK-TIMEOUT")

    missing = WORKSPACE.read_text(encoding="utf-8").replace("  // selyth:obligation OBL-0007\n", "")
    report = pipeline(dafny, solver, version, workspace=missing)
    check(valid(report) and outcomes(report)["OBL-0007"] == "not-run" and outcomes(report)["OBL-0008"] == "proven",
          "an obligation with no marker is not-run; the others are unaffected", f"    {outcomes(report)}")
    check(violations(report) == [("BLOCK-NO-VERDICT", "OBL-0007")],
          "the policy engine rejects it as having no verdict")
    twice = WORKSPACE.read_text(encoding="utf-8").replace(
        "  // selyth:obligation OBL-0007\n", "  // selyth:obligation OBL-0007\n  // selyth:obligation OBL-0007\n")
    check(outcomes(pipeline(dafny, solver, version, workspace=twice))["OBL-0007"] == "not-run",
          "two markers for one obligation are ambiguous: not-run")

    permission = osets.demo()
    permission["obligations"].append({**copy.deepcopy(permission["obligations"][0]), "obligation_id": "OBL-0010",
                                      "kind": "permission", "statement": "ensures true"})
    report = pipeline(dafny, solver, version, document=permission)
    entry = next(o for o in report["obligations"] if o["obligation_id"] == "OBL-0010")
    check(valid(report) and entry["outcome"] == "not-run" and "cannot be lowered" in entry["diagnostics"][0]
          and oset.check_coverage(permission, report) == [],
          "an obligation the adapter could not lower is reported not-run, with its reason, and still covered")

    heading("Tool identity and inputs")
    other = adapter.lower(osets.demo(), "4.10.0")
    with tempfile.TemporaryDirectory(prefix="selyth-dafny-") as tmp:
        files = {}
        for name, doc in (("ir", other), ("set", osets.demo()), ("policy", POLICY)):
            files[name] = Path(tmp) / f"{name}.json"
            files[name].write_text(json.dumps(doc), encoding="utf-8")
        run = subprocess.run([sys.executable, str(TOOLS / "dafny_backend.py"), "verify", "--ir", str(files["ir"]),
                              "--workspace", str(WORKSPACE), "--obligation-set", str(files["set"]),
                              "--policy", str(files["policy"]), "--dafny", str(dafny)],
                             capture_output=True, text=True)
        check(run.returncode == 2 and "4.10.0" in run.stderr and version in run.stderr and not run.stdout,
              "an IR targeting another Dafny version is refused: exit 2, no report", f"    {run.returncode} {run.stderr}")

        good = Path(tmp) / "good-ir.json"
        good.write_text(json.dumps(adapter.lower(osets.demo(), version)), encoding="utf-8")
        edited_set = osets.demo()
        edited_set["obligations"][0]["statement"] += " && true"
        files["set"].write_text(json.dumps(edited_set), encoding="utf-8")
        run = subprocess.run([sys.executable, str(TOOLS / "dafny_backend.py"), "verify", "--ir", str(good),
                              "--workspace", str(WORKSPACE), "--obligation-set", str(files["set"]),
                              "--policy", str(files["policy"]), "--dafny", str(dafny)],
                             capture_output=True, text=True)
        check(run.returncode == 2 and "different Obligation Set" in run.stderr,
              "an IR lowered from another Obligation Set is refused", f"    {run.stderr}")

        files["set"].write_text(json.dumps(osets.demo()), encoding="utf-8")
        report_path = Path(tmp) / "report.json"
        args = [sys.executable, str(TOOLS / "dafny_backend.py"), "verify", "--ir", str(good), "--workspace",
                str(WORKSPACE), "--obligation-set", str(files["set"]), "--policy", str(files["policy"]),
                "--dafny", str(dafny), "--produced-at", PRODUCED_AT]
        run = subprocess.run(args, capture_output=True, text=True)
        check(run.returncode == 0 and valid(json.loads(run.stdout)) and json.loads(run.stdout)["produced_at"] == PRODUCED_AT,
              "CLI: an accepted run exits 0 and prints a valid report")
        report_path.write_text(run.stdout, encoding="utf-8")
        replay = subprocess.run([sys.executable, str(TOOLS / "policy_engine.py"), "replay", str(files["policy"]),
                                 str(report_path), str(files["set"])], capture_output=True, text=True)
        check(replay.returncode == 0, "the policy engine CLI replays the printed report", replay.stdout)
        bad = Path(tmp) / "bad.dfy"
        bad.write_text(WORKSPACE.read_text(encoding="utf-8").replace("result.length == l.length + 1", "result.length == 0"),
                       encoding="utf-8")
        args[args.index(str(WORKSPACE))] = str(bad)
        check(subprocess.run(args, capture_output=True, text=True).returncode == 1,
              "CLI: a rejected run exits 1 (the hand-written clause makes the workspace fail)")
        args[args.index("--dafny") + 1] = str(Path(tmp) / "no-such-dafny")
        check(subprocess.run(args, capture_output=True, text=True).returncode == 2, "CLI: a missing Dafny exits 2")


def main():
    check_units()
    try:
        dafny = locate_dafny()
        version, solver = backend.tool_versions(dafny)
    except (backend.BackendError, OSError) as exc:
        check(False, "Dafny is available", f"    {exc}\n    Install Dafny 4.11.0 and set SELYTH_DAFNY, or put it at ~/.local/opt/dafny/dafny.")
        version = None
    if version is not None:
        check(version == "4.11.0", f"Dafny {version} is the version the fixtures and docs name (4.11.0)",
              "    other versions may change diagnostics or resource counts")
        check_pipeline(dafny, solver, version)
    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
