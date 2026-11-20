#!/usr/bin/env python3
"""Property-test backend: acceptance-criterion obligations as deterministic property tests.

Specified in docs/specifications/property-backend.md. This is the second backend: it shares
the report schema, the policy engine and the lock with the Dafny backend, which is exactly
what makes it a validation of the adapter contract — a second producer must be able to emit
a report the same engine decides. It runs Node (type stripping) on the TypeScript source,
executes the declared function on a deterministic sample of inputs, and checks each
obligation's property expression on every sample.

The evidence it produces is `property-tested` with soundness `empirical`: a sample is not a
proof, and the report schema's ceilings enforce that. Only obligations of kind
`acceptance-criterion` are handled; contract clauses stay with the Dafny backend.

Usage:
  property_backend.py test --obligation-set S.json --source SRC.ts --spec SPEC.json \\
      --policy P.json [--lock LOCK.json] [--node PATH] [--time-limit SECONDS] \\
      [--produced-at RFC3339]
Prints the report. Exit status: 0 accepted, 1 rejected, 3 needs-human, 2 usage error,
unreadable input, or a tool that is not the declared one.
"""
import argparse
import hashlib
import json
import os
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
import ts_to_dafny
import validate

BACKEND_NAME = "property-test"
BACKEND_IDENTITY_VERSION = "0.1.0"
DEFAULT_TIME_LIMIT = 60
MAX_SAMPLES = 100_000
SPEC_KEYS = ("function", "params", "samples", "seed")


class BackendError(RuntimeError):
    """The backend cannot honestly produce a report (bad input, wrong tool, unsupported shape)."""


def sha256_bytes(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def resolve_node(path=None):
    candidate = path or os.environ.get("SELYTH_NODE") or "node"
    from shutil import which
    found = which(candidate)
    if not found:
        raise BackendError(f"Node not found ({candidate!r}); pass --node or set SELYTH_NODE")
    return Path(found).resolve()


def node_version(node):
    run = subprocess.run([str(node), "--version"], capture_output=True, text=True, timeout=60)
    if run.returncode != 0 or not run.stdout.strip().startswith("v"):
        raise BackendError(f"`{node} --version` failed: {run.stderr.strip()}")
    return run.stdout.strip()[1:]


def _mulberry32(seed):
    """A fixed, documented PRNG: the sample must be reproducible bit for bit."""
    state = seed & 0xFFFFFFFF
    while True:
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = state
        t = (t ^ (t >> 15)) * (t | 1) & 0xFFFFFFFF
        t ^= t + ((t ^ (t >> 7)) * (t | 61) & 0xFFFFFFFF)
        yield (t ^ (t >> 14)) & 0xFFFFFFFF


def load_spec(path):
    """Load and validate the property-test specification (the sidecar)."""
    try:
        spec = validate.load_json(path)
    except (OSError, validate.SchemaError, json.JSONDecodeError) as exc:
        raise BackendError(f"cannot read the spec {path}: {exc}") from exc
    if not isinstance(spec, dict) or set(spec) - set(SPEC_KEYS) or not set(SPEC_KEYS) <= set(spec):
        raise BackendError(f"the spec must have exactly the keys {list(SPEC_KEYS)}, got "
                           f"{sorted(spec) if isinstance(spec, dict) else spec!r}")
    if not isinstance(spec["function"], str) or not spec["function"].isidentifier():
        raise BackendError(f"spec.function must be an identifier, got {spec['function']!r}")
    params = spec["params"]
    if not isinstance(params, dict) or not params:
        raise BackendError("spec.params must be a non-empty object of parameter domains")
    for name, domain in params.items():
        if not name.isidentifier():
            raise BackendError(f"parameter name {name!r} is not an identifier")
        if (not isinstance(domain, dict) or set(domain) != {"domain", "min", "max"}
                or domain["domain"] != "nat"
                or not all(isinstance(domain[k], int) and not isinstance(domain[k], bool)
                           for k in ("min", "max"))
                or not 0 <= domain["min"] <= domain["max"]):
            raise BackendError(f"parameter {name!r}: the only supported domain is "
                               f'{{"domain": "nat", "min": m, "max": n}} with 0 <= m <= n, '
                               f"got {domain!r}")
    if (not isinstance(spec["samples"], int) or isinstance(spec["samples"], bool)
            or not 1 <= spec["samples"] <= MAX_SAMPLES):
        raise BackendError(f"spec.samples must be an integer in 1..{MAX_SAMPLES}, got {spec['samples']!r}")
    if not isinstance(spec["seed"], int) or isinstance(spec["seed"], bool):
        raise BackendError(f"spec.seed must be an integer, got {spec['seed']!r}")
    return spec


def declared_function(source, name):
    """The parsed declaration of `name` in the TypeScript source, or None."""
    try:
        functions = ts_to_dafny.Parser(ts_to_dafny.tokenize(source)).program()
    except ts_to_dafny.TranslationError as exc:
        raise BackendError(f"the source is outside the pure subset: {exc}") from exc
    for fn in functions:
        if fn["name"] == name:
            return fn
    return None


def generate_cases(spec):
    """The deterministic sample: [{param: value, ...}, ...], len == spec['samples']."""
    rng = _mulberry32(spec["seed"])
    names = sorted(spec["params"])
    cases = []
    for _ in range(spec["samples"]):
        case = {}
        for name in names:
            domain = spec["params"][name]
            span = domain["max"] - domain["min"] + 1
            case[name] = domain["min"] + (next(rng) % span)
        cases.append(case)
    return cases


def property_expression(obligation):
    """The TS expression checked on every sample, validated against the shared subset.

    The statement of an acceptance-criterion is `ensures <expr>`; the expression may only
    use the subset the adapter also parses, minus `==>` (not executable) and bare `/`
    (real division in TypeScript), both refused rather than guessed.
    """
    parts = obligation["statement"].split(None, 1)
    if len(parts) != 2 or parts[0] != "ensures":
        raise BackendError(f"obligation {obligation['obligation_id']}: an acceptance-criterion "
                           f"statement must start with 'ensures ', got {obligation['statement']!r}")
    expression = parts[1]
    try:
        tokens = ts_adapter._tokenize(expression)
    except ts_adapter.ExpressionError as exc:
        raise BackendError(f"obligation {obligation['obligation_id']}: expression outside the "
                           f"supported subset: {exc}") from None
    if "==>" in tokens:
        raise BackendError(f"obligation {obligation['obligation_id']}: `==>` is not executable; "
                           f"write `!(a) || (b)` instead")
    if "/" in tokens:
        raise BackendError(f"obligation {obligation['obligation_id']}: a bare `/` is real division "
                           f"in TypeScript; write `Math.floor(a / b)`")
    try:
        return ts_adapter.canonical_expression(expression)
    except ts_adapter.ExpressionError as exc:
        raise BackendError(f"obligation {obligation['obligation_id']}: expression outside the "
                           f"supported subset: {exc}") from None


RUNNER_TEMPLATE = """// Generated by tools/property_backend.py from @SOURCE_NAME@; do not edit.
import { @FN@ } from "@SOURCE_URI@";
const cases = @CASES@;
const properties = @PROPERTIES@;
const failures = [];
for (const c of cases) {
  const { @PARAMS@ } = c;
  const result = @FN@(@PARAMS@);
  for (const p of properties) {
    if (!p.check(@PARAMS@, result)) {
      failures.push({ id: p.id, ...c, result });
    }
  }
}
console.log(JSON.stringify({ samples: cases.length, failures: failures.slice(0, 50) }));
"""


def render_runner(spec, cases, expressions, source_uri, source_name):
    """The Node runner: one check per obligation, so each gets its own verdict."""
    names = sorted(spec["params"])
    params = ", ".join(names)
    # The checks are real arrow functions, not JSON strings: the expressions are validated
    # against the shared subset, which contains no string literals, so embedding is exact.
    properties_js = "[\n" + ",\n".join(
        f"  {{ id: {json.dumps(oid)}, check: ({params}, result) => ({expr}) }}"
        for oid, expr in expressions) + "\n]"
    out = RUNNER_TEMPLATE
    for placeholder, value in (
            ("@SOURCE_NAME@", source_name),
            ("@FN@", spec["function"]),
            ("@SOURCE_URI@", source_uri),
            ("@CASES@", json.dumps(cases, separators=(",", ":"))),
            ("@PROPERTIES@", properties_js),
            ("@PARAMS@", params)):
        out = out.replace(placeholder, value)
    return out


def run_property(node, runner_text, time_limit):
    """Execute the runner; return (outcome, parsed output, diagnostics, cost)."""
    cost = {"timed_out": False}
    with tempfile.TemporaryDirectory(prefix="selyth-prop-") as tmp:
        runner = Path(tmp) / "runner.ts"
        runner.write_text(runner_text, encoding="utf-8")
        command = [str(node), "--experimental-strip-types", str(runner)]
        started = time.monotonic()
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=time_limit * 4 + 60)
        except subprocess.TimeoutExpired:
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            cost.update(wall_ms=int((time.monotonic() - started) * 1000), timed_out=True,
                        **_child_cost(before, after))
            return "timeout", None, ["the Node process was killed after its wall-clock allowance"], cost
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        cost["wall_ms"] = int((time.monotonic() - started) * 1000)
        cost.update(_child_cost(before, after))
        output = run.stdout.strip().splitlines()
        if run.returncode != 0 or not output:
            diagnostics = [line for line in (run.stdout + run.stderr).splitlines()
                           if "Error" in line or "error" in line][:10]
            return "failed", None, diagnostics or [f"Node exited with status {run.returncode}"], cost
        try:
            result = json.loads(output[-1])
        except json.JSONDecodeError:
            return "failed", None, ["the runner printed no JSON verdict: " + output[-1][:200]], cost
    if not isinstance(result, dict) or not isinstance(result.get("samples"), int) \
            or not isinstance(result.get("failures"), list):
        return "failed", None, ["the runner's verdict has the wrong shape"], cost
    if result["failures"]:
        diagnostics = [f"input {json.dumps(f)}: property violated" for f in result["failures"][:10]]
        return "failed", result, diagnostics, cost
    return "passed", result, [], cost


def _child_cost(before, after):
    cpu_s = (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
    rss_unit = 1024 if sys.platform.startswith("linux") else 1
    return {"cpu_ms": max(0, int(cpu_s * 1000)), "peak_rss_bytes": after.ru_maxrss * rss_unit}


def run_and_build(obligation_set, set_name, source_bytes, source_name, spec, policy, node, node_ver,
                  produced_at, time_limit, lock=None):
    """Run the property tests and build the report. Does the I/O (one Node process)."""
    source = source_bytes.decode("utf-8")
    canonical = oset.canonical_form(obligation_set)
    for obligation in canonical["obligations"]:
        if obligation["kind"] != "acceptance-criterion":
            raise BackendError(f"obligation {obligation['obligation_id']}: kind {obligation['kind']!r} "
                               f"is not an acceptance-criterion; this backend only runs those, contract "
                               f"clauses belong to the Dafny backend")
    fn = declared_function(source, spec["function"])
    if fn is None:
        raise BackendError(f"the source does not declare function {spec['function']!r}")
    if sorted(fn["params"]) != sorted(spec["params"]):
        raise BackendError(f"the spec's params {sorted(spec['params'])} do not match "
                           f"{spec['function']}'s parameters {sorted(fn['params'])}")
    ordered = sorted(canonical["obligations"], key=lambda o: o["obligation_id"])
    expressions = [(o["obligation_id"], property_expression(o)) for o in ordered]
    cases = generate_cases(spec)
    runner = render_runner(spec, cases, expressions, Path(source_name).resolve().as_uri(),
                           Path(source_name).name)
    run_outcome, result, run_diagnostics, cost = run_property(node, runner, time_limit)

    set_digest = oset.set_digest(obligation_set)
    digests = oset.obligation_digests(obligation_set)
    spec_digest = sha256_bytes(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    policy_digest = engine.policy_digest(policy)
    identity = hashlib.sha256(json.dumps(
        [set_digest, sha256_bytes(source_bytes), spec_digest, policy_digest, node_ver],
        sort_keys=True).encode()).hexdigest()[:16]
    tool = {"name": "node", "version": node_ver}
    domain_desc = ", ".join(f"{n} in {spec['params'][n]['min']}..{spec['params'][n]['max']}"
                            for n in sorted(spec["params"]))
    base_scope = (f"node {node_ver} executed `{spec['function']}` on {spec['samples']} inputs "
                  f"generated from seed {spec['seed']} over {domain_desc}")
    failures_by_id = {}
    if result:
        for failure in result["failures"]:
            failures_by_id.setdefault(failure.get("id"), []).append(failure)
    results = []
    for obligation in ordered:
        oid = obligation["obligation_id"]
        if run_outcome == "timeout":
            outcome, diagnostics = "timeout", run_diagnostics
        elif run_outcome == "failed" and result is None:
            # The run itself failed (Node error, bad output): no obligation gets a verdict.
            outcome, diagnostics = "failed", run_diagnostics
        elif oid in failures_by_id:
            outcome = "failed"
            diagnostics = [f"input {json.dumps({k: v for k, v in f.items() if k != 'id'})}: "
                           f"property {oid} violated" for f in failures_by_id[oid][:10]]
        else:
            outcome, diagnostics = "passed", []
        entry = {"obligation_id": oid, "obligation_digest": digests[oid],
                 "specification_version": obligation_set["specification_version"],
                 "kind": obligation["kind"], "statement": obligation["statement"],
                 "intent_statement": obligation.get("intent_statement"),
                 "required_evidence_kind": obligation["required_evidence_kind"],
                 "affected_components": obligation["affected_components"],
                 "outcome": outcome,
                 "evidence": {"kind": "property-tested", "soundness": "empirical",
                              "producer_tool": tool,
                              "scope": (f"{base_scope}, checking `{dict(expressions)[oid]}` on every "
                                        f"input ({len(failures_by_id.get(oid, []))} of "
                                        f"{spec['samples']} failed)"),
                              "assumptions": obligation["assumptions"] + [
                                  f"inputs are sampled inside the declared domain ({domain_desc}), "
                                  f"which respects the contract preconditions"],
                              "cost": cost}}
        if diagnostics:
            entry["diagnostics"] = diagnostics
        results.append(entry)
    trusted = policy["approval"] != "unverified"
    report = {
        "selyth_report_version": "0.1.0",
        "report_id": f"rpt-{identity}",
        "produced_at": produced_at,
        "subject": [
            {"name": Path(source_name).name, "digest": sha256_bytes(source_bytes), "role": "implementation"},
            {"name": set_name, "digest": set_digest, "role": "obligation-set"}],
        "producer": {
            "identity": f"local://selyth-property-backend/{BACKEND_IDENTITY_VERSION}",
            "kind": "local-subprocess", "outside_agent_boundary": False,
            "toolchain": [tool]},
        "policy": {"policy_id": policy["policy_id"], "policy_version": policy["policy_version"],
                   "policy_digest": policy_digest, "approved_by": policy["approved_by"],
                   "approval": policy["approval"]},
        "obligations": results,
        "decision": {"result": "rejected", "policy_digest": policy_digest, "rules_evaluated": 0,
                     "violations": [{"rule_id": "PENDING", "obligation_id": None,
                                     "message": "the policy engine has not decided yet"}],
                     "escalations": []},
        "trust": {"model": "local-structural", "verifier_outside_agent_boundary": False,
                  "policy_approval_trusted": trusted,
                  "limitations": [
                      f"{spec['samples']} generated inputs (seed {spec['seed']}) are a sample, not a "
                      f"proof: the property is checked on the sample only, and soundness is 'empirical'.",
                      "Single-machine structural integrity only; no isolation from an agent with "
                      "local code execution.",
                      "Node is trusted as installed; the executed code is the TypeScript source itself, "
                      "run with type stripping, so what ran is what the digest binds."]},
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
    ap = argparse.ArgumentParser(prog="property_backend.py",
                                 description=__doc__.split("Usage:")[0].strip())
    sub = ap.add_subparsers(dest="command", required=True)
    test = sub.add_parser("test", help="run the acceptance-criteria of an Obligation Set as property tests")
    test.add_argument("--obligation-set", required=True, dest="obligation_set")
    test.add_argument("--source", required=True, help="the TypeScript source (pure subset)")
    test.add_argument("--spec", required=True, help="the property-test sidecar (JSON)")
    test.add_argument("--policy", required=True)
    test.add_argument("--lock", help="the human-approved lock")
    test.add_argument("--node", help="path to node (or set SELYTH_NODE)")
    test.add_argument("--time-limit", type=_positive_int, default=DEFAULT_TIME_LIMIT,
                      help=f"wall-clock allowance for the whole run, seconds (default {DEFAULT_TIME_LIMIT})")
    test.add_argument("--produced-at", help="RFC3339 timestamp recorded in the report (default: now)")
    return ap


def main(argv):
    args = build_parser().parse_args(argv[1:])
    try:
        document = oset.load_obligation_set(args.obligation_set)
        policy = validate.load_json(args.policy)
        if validate.validate(policy, validate.load_schema(
                oset.ROOT / "docs" / "specifications" / "policy-schema.json")):
            raise BackendError("the policy is not valid against policy-schema.json")
        spec = load_spec(args.spec)
        lock = engine.load_lock(args.lock) if args.lock else None
        node = resolve_node(args.node)
        version = node_version(node)
        source = Path(args.source)
        report = run_and_build(document, Path(args.obligation_set).name, source.read_bytes(),
                               str(source), spec, policy, node, version,
                               args.produced_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               args.time_limit, lock)
    except (BackendError, oset.ObligationSetError, validate.SchemaError, OSError,
            json.JSONDecodeError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return {"accepted": 0, "rejected": 1, "needs-human": 3}[report["decision"]["result"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
