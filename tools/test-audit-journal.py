#!/usr/bin/env python3
"""Tests for the M6 audit journal and its head anchor (tools/audit_journal.py,
docs/evaluation/protocol.md section 5).

The journal is a hash-chained append-only JSONL; the anchor writes the head to a location
chosen by the operator (outside the agent's working directory) with the operator's
attestation. What must hold:

  * appends chain correctly and verify() accepts an intact journal;
  * any edit, deletion or reordering breaks the chain and verify() names the line;
  * the anchor records head+seq+operator, and verify-anchor accepts it only if it matches the
    current head and carries an operator;
  * anchoring an empty journal is refused.

This is a traceability anchor, not a security boundary: the M6-Lite isolation level stays
`procedural` (ADR-004 section 5).

Run with `make test-python` (no Dafny, no Node), or `python tools/test-audit-journal.py`.
"""
import json
import sys
import tempfile
from pathlib import Path

import audit_journal as aj

FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


def main():
    with tempfile.TemporaryDirectory(prefix="selyth-journal-") as tmp:
        root = Path(tmp)
        journal = root / "journal.jsonl"

        heading("Appends chain; verify accepts an intact journal")
        e0 = aj.append(journal, {"run_id": "r1", "event": "start", "model_calls": 0})
        e1 = aj.append(journal, {"run_id": "r1", "event": "model_call", "n": 1})
        e2 = aj.append(journal, {"run_id": "r1", "event": "end", "model_calls": 3})
        check([e["seq"] for e in (e0, e1, e2)] == [0, 1, 2], "sequence numbers are 0,1,2")
        check(e0["prev"] == aj.GENESIS and e1["prev"] == e0["hash"] and e2["prev"] == e1["hash"],
              "each entry chains to the previous hash")
        check(aj.verify(journal) == [], "verify() accepts the intact chain")

        heading("Any tampering breaks the chain and is located")
        orig = journal.read_text().splitlines()
        lines = list(orig)
        tampered = json.loads(lines[1])
        tampered["event"]["n"] = 99
        lines[1] = aj._canonical(tampered)
        broken = root / "broken.jsonl"
        broken.write_text("\n".join(lines) + "\n")
        problems = aj.verify(broken)
        check(any("hash mismatch" in p for p in problems), "an edited entry is caught by its hash",
              f"    {problems}")
        truncated = root / "truncated.jsonl"
        truncated.write_text("\n".join(orig[:2]) + "\n")
        check(aj.verify(truncated) == [], "a truncated tail is not a break (append-only: the head moved)")
        reordered = root / "reordered.jsonl"
        reordered.write_text("\n".join([orig[0], orig[2], orig[1]]) + "\n")
        check(aj.verify(reordered) != [], "a reordered journal does not verify")

        heading("The anchor records head+seq+operator; verify-anchor checks it against the head")
        anchor_path = root / "outside" / "anchor.json"       # a location chosen by the operator
        rec = aj.anchor(journal, anchor_path, operator="audry")
        check(rec["head"] == e2["hash"] and rec["seq"] == 2 and rec["operator"] == "audry",
              "the anchor carries the journal head, seq and the operator attestation")
        check(aj.verify_anchor(journal, anchor_path) == [], "verify-anchor accepts a matching anchor")
        aj.append(journal, {"run_id": "r1", "event": "extra"})
        check(any("!=" in p for p in aj.verify_anchor(journal, anchor_path)),
              "after a new append the old anchor no longer matches")
        other = root / "other.jsonl"
        aj.append(other, {"run_id": "r2"})
        check(aj.verify_anchor(other, anchor_path) != [], "an anchor from another journal is refused")
        no_op = root / "no-op.json"
        rec2 = dict(rec)
        del rec2["operator"]
        no_op.write_text(json.dumps(rec2))
        check(any("operator" in p for p in aj.verify_anchor(journal, no_op)),
              "an anchor without the operator attestation is refused")

        heading("An empty journal is never anchored")
        empty = root / "empty.jsonl"
        empty.write_text("")
        try:
            aj.anchor(empty, root / "a.json", operator="audry")
            check(False, "anchoring an empty journal is refused")
        except ValueError:
            check(True, "anchoring an empty journal is refused")
        check(aj.verify(empty) == [], "an empty journal verifies (vacuously)")

    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
