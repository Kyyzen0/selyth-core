#!/usr/bin/env python3
"""Adversarial suite: can an agent get a bad change accepted? (the rapport-final.md criterion)

Simulates, at the CLI level, an agent whose submission tries to cross the trust boundary.
A fake Dafny (a shell script that always reports "finished with 1 verified, 0 errors", plus
the pinned z3 path so tool_versions() accepts it) stands in for the prover: these tests are
about what the gate REFUSES, not about what the prover proves. The real-prover negative
scenarios live in tools/test-scenarios.py (make test-dafny).

Scenarios, each must be refused with the right exit status:
  1. baseline: the untouched ledger example is accepted under its lock with the fake prover
  2. a trust escape (`assume`) added to the workspace      -> gate rejects (exit 1)
  3. an orphan obligation marker added to the workspace    -> gate exit 2, no report
  4. the Obligation Set edited after the lock              -> gate exit 2 / decide exit 1 (INTEGRITY)
  5. the policy weakened after the lock                    -> decide exit 1 (INTEGRITY-POLICY-NOT-APPROVED)
  6. the accepted report edited afterwards                 -> replay exit 1, decide exit 1 (INTEGRITY-COVERAGE)
  7. a hand-edited workspace vs regeneration               -> byte-compare mismatch (check_example's control)
  8. KNOWN LIMIT, asserted on purpose: a forged lock is accepted by the engine alone; the lock
     is protected procedurally (protected path, signed commit), not by Core

Run with `make test-python`, or `python tools/test-adversarial.py` in the pinned environment.
"""
import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import obligation_set as oset
import policy_engine as engine
import validate

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PROJECT = ROOT / "docs" / "specifications" / "examples" / "ledger"
FAILURES = []
PRODUCED_AT = "2026-11-21T12:00:00Z"

FAKE_DAFNY = """#!/bin/sh
for a in "$@"; do
  case "$a" in
    --version) echo "4.11.0+fake"; exit 0 ;;
  esac
done
log=""
for a in "$@"; do
  case "$a" in
    *LogFileName=*) log="${a#*LogFileName=}" ;;
  esac
done
if [ -n "$log" ]; then printf 'TestResult.ResourceCount\\n0\\n' > "$log"; fi
echo "Dafny 4.11.0 (fake prover: everything verifies)"
echo "finished with 1 verified, 0 errors"
exit 0
"""


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


def cli(tool, *args):
    return subprocess.run([sys.executable, str(TOOLS / tool), *args],
                          capture_output=True, text=True)


def make_fake_dafny(tmp):
    home = Path(tmp) / "fake-dafny"
    (home / "z3" / "bin").mkdir(parents=True)
    dafny = home / "dafny"
    dafny.write_text(FAKE_DAFNY, encoding="utf-8")
    dafny.chmod(dafny.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (home / "z3" / "bin" / "z3-4.12.1").write_text("#!/bin/sh\n", encoding="utf-8")
    return dafny


def gate_run(dafny, set_path, workspace_path, policy_path, lock_path, tmp):
    return subprocess.run(
        [sys.executable, str(TOOLS / "gate.py"), "run",
         "--obligation-set", str(set_path), "--workspace", str(workspace_path),
         "--policy", str(policy_path), "--lock", str(lock_path),
         "--dafny", str(dafny), "--produced-at", PRODUCED_AT],
        capture_output=True, text=True)


def main():
    set_path = PROJECT / "ledger.obligations.json"
    policy_path = PROJECT / "ledger.policy.json"
    lock_path = PROJECT / "ledger.lock.json"
    workspace_path = PROJECT / "ledger.workspace.dfy"
    ts_path = PROJECT / "ledger.ts"
    translation_path = PROJECT / "ledger.translation.json"
    workspace_text = workspace_path.read_text(encoding="utf-8")
    document = oset.load_obligation_set(set_path)
    policy = validate.load_json(policy_path)
    lock = engine.load_lock(lock_path)

    with tempfile.TemporaryDirectory(prefix="selyth-adversarial-") as tmp:
        tmp = Path(tmp)
        fake = make_fake_dafny(tmp)

        heading("1. Baseline: the untouched example is accepted under its lock (fake prover)")
        base = gate_run(fake, set_path, workspace_path, policy_path, lock_path, tmp)
        check(base.returncode == 0, "gate.py run exits 0 (accepted) on the untouched example",
              f"exit {base.returncode}: {(base.stderr or base.stdout)[-400:]}")
        report = json.loads(base.stdout) if base.returncode == 0 else {}
        check(report.get("decision", {}).get("result") == "accepted",
              "the decision is accepted", base.stdout[-400:])
        report_path = tmp / "report.json"
        report_path.write_text(base.stdout, encoding="utf-8")

        heading("2. Trust escape smuggled into the workspace (`assume`)")
        evil_ws = tmp / "evil-assume.workspace.dfy"
        evil_ws.write_text(workspace_text + "\nassume false;\n", encoding="utf-8")
        run = gate_run(fake, set_path, evil_ws, policy_path, lock_path, tmp)
        out = run.stdout + run.stderr
        check(run.returncode == 1 and "forbidden construct" in out and "assume" in out,
              "gate rejects a workspace containing `assume` (exit 1, 'forbidden construct')",
              f"exit {run.returncode}: {out[-400:]}")

        heading("3. Orphan obligation marker in the workspace")
        orphan_ws = tmp / "orphan.workspace.dfy"
        orphan_ws.write_text(workspace_text + "\n  // selyth:obligation LED-99\n", encoding="utf-8")
        run = gate_run(fake, set_path, orphan_ws, policy_path, lock_path, tmp)
        out = run.stdout + run.stderr
        check(run.returncode == 2 and "LED-99" in out,
              "gate exits 2 (no report) on a marker the Obligation Set does not declare",
              f"exit {run.returncode}: {out[-400:]}")

        heading("4. Obligation Set edited after the lock")
        edited_set = copy.deepcopy(document)
        edited_set["obligations"][0]["statement"] = "true"  # weakened after approval
        edited_path = tmp / "edited.obligations.json"
        edited_path.write_text(json.dumps(edited_set), encoding="utf-8")
        run = gate_run(fake, edited_path, workspace_path, policy_path, lock_path, tmp)
        check(run.returncode == 1 and "INTEGRITY" in run.stdout,
              "gate refuses the edited set under the lock (exit 1, INTEGRITY digest mismatch)",
              f"exit {run.returncode}: {(run.stderr or run.stdout)[-400:]}")
        run = cli("policy_engine.py", "decide", str(policy_path), str(report_path),
                  str(edited_path), "--lock", str(lock_path))
        check(run.returncode == 1 and "INTEGRITY" in run.stdout,
              "decide --lock rejects the edited set (exit 1, INTEGRITY digest mismatch)",
              f"exit {run.returncode}: {run.stdout[-400:]}")

        heading("5. Policy weakened after the lock")
        weaker = copy.deepcopy(policy)
        # machine-checked-model weakened to human-asserted: any change alters the policy digest
        weaker["rules"][0]["requires"]["min_evidence_kind"] = "human-asserted"
        weaker_path = tmp / "weaker.policy.json"
        weaker_path.write_text(json.dumps(weaker), encoding="utf-8")
        run = cli("policy_engine.py", "decide", str(weaker_path), str(report_path),
                  str(set_path), "--lock", str(lock_path))
        check(run.returncode == 1 and "INTEGRITY-POLICY-NOT-APPROVED" in run.stdout,
              "decide --lock rejects the weakened policy (exit 1)", f"exit {run.returncode}: {run.stdout[-400:]}")

        heading("6. Accepted report edited afterwards")
        forged = copy.deepcopy(report)
        forged["obligations"][0]["statement"] = "true"
        forged_path = tmp / "forged-report.json"
        forged_path.write_text(json.dumps(forged), encoding="utf-8")
        run = cli("policy_engine.py", "decide", str(policy_path), str(forged_path),
                  str(set_path), "--lock", str(lock_path))
        check(run.returncode == 1 and "INTEGRITY-COVERAGE" in run.stdout,
              "decide rejects a report whose entries no longer match the set (INTEGRITY-COVERAGE)",
              f"exit {run.returncode}: {run.stdout[-400:]}")
        replay = copy.deepcopy(report)
        replay["decision"]["result"] = "accepted"
        replay["decision"]["rules_evaluated"] = 999
        replay_path = tmp / "replay-report.json"
        replay_path.write_text(json.dumps(replay), encoding="utf-8")
        run = cli("policy_engine.py", "replay", str(policy_path), str(replay_path),
                  str(set_path), "--lock", str(lock_path))
        check(run.returncode == 1, "replay exits 1 when the recorded decision is not reproduced",
              f"exit {run.returncode}: {run.stdout[-200:]}")

        heading("7. Hand-edited workspace vs regeneration (check_example's control)")
        regen = tmp / "regen.workspace.dfy"
        run = cli("ts_to_dafny.py", str(ts_path), "--spec", str(translation_path), "-o", str(regen))
        check(run.returncode == 0 and regen.read_bytes() == workspace_path.read_bytes(),
              "regenerating from ledger.ts reproduces the committed workspace byte for byte",
              f"exit {run.returncode}")
        tampered = workspace_text.replace("requires ", "requires true && ", 1)
        check(tampered != workspace_text and regen.read_text(encoding="utf-8") != tampered,
              "a hand-weakened workspace differs from the regeneration (the compare would fail)")

        heading("8. KNOWN LIMIT, asserted on purpose")
        # A report produced for the weakened policy, then a lock the "agent" forged for it:
        # the engine alone cannot tell; the lock is protected procedurally (protected path /
        # signed commit), not by Core. Asserted so a silent change of this behaviour is visible.
        weak_run = subprocess.run(
            [sys.executable, str(TOOLS / "gate.py"), "run",
             "--obligation-set", str(set_path), "--workspace", str(workspace_path),
             "--policy", str(weaker_path), "--dafny", str(fake), "--produced-at", PRODUCED_AT],
            capture_output=True, text=True)
        weak_report_path = tmp / "weak-report.json"
        weak_report_path.write_text(weak_run.stdout, encoding="utf-8")
        forged_lock = engine.make_lock(weaker, document, "agent")
        forged_lock_path = tmp / "forged.lock.json"
        forged_lock_path.write_text(json.dumps(forged_lock), encoding="utf-8")
        run = cli("policy_engine.py", "decide", str(weaker_path), str(weak_report_path),
                  str(set_path), "--lock", str(forged_lock_path))
        check(run.returncode in (0, 3) and "INTEGRITY" not in run.stdout,
              "KNOWN LIMIT: the engine alone accepts a forged lock (no INTEGRITY violation); the lock "
              "is protected procedurally (protected path / signed commit), not by Core",
              f"exit {run.returncode}: {run.stdout[-300:]}")

    total_failed = len(FAILURES)
    print(f"\n{'FAILED' if total_failed else 'all checks passed'}: {total_failed} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
