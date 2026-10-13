#!/usr/bin/env python3
"""Fixture generator + assertion runner for the Selyth schemas.

The negative fixtures matter more than the positive one: they are the tests that
prove the anti-overclaim rules actually bite rather than merely being present in
the schema text.
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "minischema.py"
REPORT = ROOT / "docs" / "specifications" / "report-schema.json"
POLICY = ROOT / "docs" / "specifications" / "policy-schema.json"
D = "sha256:" + "a" * 64
T = {"name": "dafny", "version": "4.11.0"}

good_obligation = {
    "obligation_id": "OBL-0007",
    "obligation_digest": D,
    "specification_version": "1.0.0",
    "kind": "postcondition",
    "statement": "ensures result.length > 0",
    "intent_statement": "The queue is never empty after a successful enqueue.",
    "required_evidence_kind": "kernel-proven",
    "evidence": {
        "kind": "kernel-proven",
        "soundness": "sound-for-fragment",
        "producer_tool": T,
        "scope": "enqueue function body, pure integer and list fragment",
        "assumptions": ["list append is total for finite inputs"],
        "cost": {"resource_units": 63182, "wall_ms": 134, "cpu_ms": 128,
                 "peak_rss_bytes": 188743680, "timed_out": False}
    },
    "outcome": "proven",
    "affected_components": ["queue.ts"]
}

good_report = {
    "selyth_report_version": "0.1.0",
    "report_id": "rpt-2026-10-13-0001",
    "produced_at": "2026-10-13T09:12:44Z",
    "subject": [
        {"name": "src/queue.ts", "digest": D, "role": "implementation"},
        {"name": "spec/queue.selyth.json", "digest": "sha256:" + "b" * 64,
         "role": "obligation-set"}
    ],
    "producer": {
        "identity": "local://selyth-verify/0.1.0",
        "kind": "local-subprocess",
        "outside_agent_boundary": False,
        "toolchain": [T, {"name": "boogie", "version": "bundled-with-dafny"},
                      {"name": "z3", "version": "bundled-with-dafny"}]
    },
    "policy": {
        "policy_id": "policy-default",
        "policy_version": "0.1.0",
        "policy_digest": "sha256:" + "c" * 64,
        "approved_by": "project-owner",
        "approval": "git-signed-commit"
    },
    "obligations": [good_obligation],
    "decision": {
        "result": "accepted",
        "policy_digest": "sha256:" + "c" * 64,
        "rules_evaluated": 3,
        "violations": [],
        "escalations": []
    },
    "trust": {
        "model": "local-structural",
        "verifier_outside_agent_boundary": False,
        "policy_approval_trusted": True,
        "limitations": [
            "Single-machine structural integrity only; no isolation from an agent with local code execution.",
            "Floating-point behaviour is not modelled; JavaScript number is verified as a mathematical integer."
        ]
    }
}

good_policy = {
    "policy_version": "0.1.0",
    "policy_id": "policy-default",
    "title": "Default acceptance policy",
    "description": "Obligations in the core domain require kernel evidence; everything else escalates.",
    "approved_by": "project-owner",
    "approval": "git-signed-commit",
    "rules": [
        {"rule_id": "RULE-CORE-PROVEN", "rationale": "Core domain logic is money and inventory adjacent.",
         "applies_to": {"obligation_kinds": ["postcondition", "invariant"]},
         "requires": {"min_evidence_kind": "kernel-proven"},
         "action": "accept"},
        {"rule_id": "RULE-INTENT-CORROBORATED",
         "applies_to": {"all_obligations": True},
         "requires": {"requires_intent_corroboration": True},
         "action": "escalate", "escalate_to": "project-owner"}
    ],
    "suppression": {"mode": "forbidden", "requires_human_approval": True,
                    "recorded_in_report": True},
    "on_unknown_evidence": "reject",
    "on_timeout": "reject",
    "cost_budget": {"fast_wall_ms": 2000, "deep_wall_ms": 120000,
                    "max_resource_units": 50_000_000, "max_peak_rss_bytes": 1_073_741_824},
    "escalation": {"requested_of": "project-owner",
                   "note": "Escalations pause the change, never auto-accept it."}
}


def mutate(fn, base):
    c = copy.deepcopy(base)
    fn(c)
    return c


CASES = []  # (name, schema, instance, must_pass, why)


def add(name, schema, inst, must_pass, why):
    CASES.append((name, schema, inst, must_pass, why))


add("report/valid", REPORT, good_report, True,
    "a well-formed report must validate")

add("report/overclaim-proven-by-llm", REPORT,
    mutate(lambda c: c["obligations"][0]["evidence"].update(
        {"kind": "llm-corroborated", "soundness": "probabilistic",
         "producer_tool": T,
         "model_identity": {"name": "some-model", "version": "x"}}),
        good_report),
    False, "a language model cannot produce a formal proof")

add("report/llm-claiming-soundness", REPORT,
    mutate(lambda c: c["obligations"][0]["evidence"].update(
        {"kind": "llm-corroborated", "soundness": "sound-for-fragment",
         "producer_tool": T,
         "model_identity": {"name": "m", "version": "v"}}), good_report),
    False, "an LLM check may not claim fragment soundness")

add("report/kernel-claiming-model-soundness", REPORT,
    mutate(lambda c: c["obligations"][0]["evidence"].update(
        {"soundness": "sound-for-model"}), good_report),
    False, "a kernel proof is sound for its fragment, not for a model")

add("report/timeout-claiming-soundness", REPORT,
    mutate(lambda c: (c["obligations"][0].update(
        {"outcome": "timeout", "diagnostics": ["solver budget exhausted"]}),
        c["obligations"][0]["evidence"]["cost"].update({"timed_out": True})),
        good_report),
    False, "a timed-out check produced no conclusion and no soundness claim")

add("report/failed-without-diagnostic", REPORT,
    mutate(lambda c: c["obligations"][0].update(
        {"outcome": "failed", "diagnostics": []}), good_report),
    False, "a failure with no diagnostic is not actionable")

add("report/accepted-with-violation", REPORT,
    mutate(lambda c: c["decision"].update(
        {"violations": [{"rule_id": "RULE-X", "message": "m"}]}), good_report),
    False, "an accepted decision may not carry violations")

add("report/rejected-without-violation", REPORT,
    mutate(lambda c: c["decision"].update(
        {"result": "rejected", "violations": []}), good_report),
    False, "a rejection must name the rule that caused it")

add("report/needs-human-without-escalation", REPORT,
    mutate(lambda c: c["decision"].update(
        {"result": "needs-human", "escalations": []}), good_report),
    False, "an escalation that names nobody is a rejection with worse ergonomics")

add("report/accepted-on-untrusted-policy", REPORT,
    mutate(lambda c: c["trust"].update({"policy_approval_trusted": False}),
        good_report),
    False, "an unapproved policy may not produce an accepted decision")

add("report/local-subprocess-claiming-isolation", REPORT,
    mutate(lambda c: c["producer"].update({"outside_agent_boundary": True}),
        good_report),
    False, "a local subprocess is not a security boundary")

add("report/isolated-trust-model-must-deny-boundary", REPORT,
    mutate(lambda c: c["trust"].update({"verifier_outside_agent_boundary": True}),
        good_report),
    False, "local-structural trust model cannot claim an outside boundary")

add("report/model-without-semantic-gap", REPORT,
    mutate(lambda c: c["obligations"][0]["evidence"].update(
        {"kind": "machine-checked-model", "semantic_gap": ""}), good_report),
    False, "a model check must state its gap to production")

add("report/human-asserted-without-waiver", REPORT,
    mutate(lambda c: c["obligations"][0]["evidence"].update(
        {"kind": "human-asserted", "soundness": "none", "producer_tool": T}),
        good_report),
    False, "a human assertion must name which human waived what")

add("report/empty-trust-limitations", REPORT,
    mutate(lambda c: c["trust"].update({"limitations": []}), good_report),
    False, "a report may not claim it established everything it checked")

add("report/empty-obligations", REPORT,
    mutate(lambda c: c.update({"obligations": []}), good_report),
    False, "a silent omission must be distinguishable from no obligation")

add("report/unpinned-policy-digest", REPORT,
    mutate(lambda c: c["policy"].update({"policy_digest": "abc123"}), good_report),
    False, "digests must be algorithm-prefixed and full length")

add("policy/valid", POLICY, good_policy, True,
    "a well-formed policy must validate")

add("policy/escalate-without-target", POLICY,
    mutate(lambda c: c["rules"][1].pop("escalate_to"), good_policy),
    False, "an escalation must name a principal")

add("policy/empty-rules", POLICY,
    mutate(lambda c: c.update({"rules": []}), good_policy),
    False, "a policy with no rules accepts everything")

add("policy/rule-without-requires", POLICY,
    mutate(lambda c: c["rules"][0].update({"requires": {}}), good_policy),
    False, "a rule that requires nothing is a rule that does nothing")

add("policy/unknown-approver", POLICY,
    mutate(lambda c: c.update({"approved_by": ""}), good_policy),
    False, "'agents may not weaken policy' is unenforceable with no named human")

add("policy/loose-suppression", POLICY,
    mutate(lambda c: c["suppression"].update(
        {"mode": "anything-goes", "requires_human_approval": False,
         "recorded_in_report": False}), good_policy),
    False, "suppression is the practical path to policy weakening")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="selyth-schema-"))
    failures, unknown_kw = [], set()
    for name, schema_path, inst, must_pass, why in CASES:
        f = tmp / (name.replace("/", "_") + ".json")
        f.write_text(json.dumps(inst, ensure_ascii=False, indent=2), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(TOOL), str(schema_path), str(f)],
            capture_output=True, text=True)
        got_pass = r.returncode == 0
        for line in r.stdout.splitlines():
            if "not in SUPPORTED" in line or "unsupported" in line.lower():
                unknown_kw.add(line)
        ok = got_pass == must_pass
        if not ok:
            failures.append((name, why, r.stdout.strip()))
        mark = "ok  " if ok else "FAIL"
        print(f"[{mark}] {name:44s} expected={'PASS' if must_pass else 'FAIL':4s} "
              f"got={'PASS' if got_pass else 'FAIL'}")

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} as expected")
    for n, why, out in failures:
        print(f"\n--- {n} ({why})\n{out}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
