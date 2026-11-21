"""Hash-chained audit journal for M6 runs, with a head anchor (docs/evaluation/protocol.md
section 5: "the journal only provides local, procedural integrity until its head is anchored
in a location outside the tested agent's working directory and credentials, which the
operator attests").

The journal is an append-only JSONL file. Each entry carries its sequence number, a
timestamp, the event, the previous entry's hash, and its own hash over the canonical JSON
(sorted keys, compact separators) of everything but the hash itself. `verify` recomputes
the whole chain; any edit, deletion or reordering breaks it.

  audit_journal.py append --journal J --set key=value [--set ...]     add an event
  audit_journal.py verify --journal J                                 exit 0 if the chain holds
  audit_journal.py anchor --journal J --out ANCHOR --operator NAME    write the head to an
                                                                      operator-chosen location
                                                                      OUTSIDE the agent's working
                                                                      directory; the operator attests it
  audit_journal.py verify-anchor --journal J --anchor ANCHOR         exit 0 if the anchor
                                                                      matches the journal head

This is a traceability anchor, not a security boundary: the isolation level of M6-Lite stays
`procedural` (ADR-004 section 5), and no report may claim otherwise.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _canonical(document):
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def entry_hash(entry):
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def load(journal_path):
    path = Path(journal_path)
    if not path.exists():
        return []
    entries = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entries.append((lineno, json.loads(line)))
        except ValueError as exc:
            raise ValueError(f"line {lineno}: not JSON: {exc}")
    return entries


def append(journal_path, event):
    path = Path(journal_path)
    entries = load(path)
    prev = entries[-1][1]["hash"] if entries else GENESIS
    entry = {"seq": len(entries), "ts": datetime.now(timezone.utc).isoformat(),
             "event": event, "prev": prev}
    entry["hash"] = entry_hash(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_canonical(entry) + "\n")
    return entry


def verify(journal_path):
    """All chain problems (empty list = the journal is intact)."""
    problems = []
    prev = GENESIS
    for i, (lineno, entry) in enumerate(load(journal_path)):
        if entry.get("seq") != i:
            problems.append(f"line {lineno}: seq {entry.get('seq')!r} is out of order")
        if entry.get("prev") != prev:
            problems.append(f"line {lineno}: prev does not chain to the previous entry")
        if entry_hash(entry) != entry.get("hash"):
            problems.append(f"line {lineno}: hash mismatch (entry modified)")
        prev = entry.get("hash", "")
    return problems


def anchor(journal_path, out_path, operator):
    entries = load(journal_path)
    if not entries:
        raise ValueError("refusing to anchor an empty journal")
    head = entries[-1][1]
    record = {"head": head["hash"], "seq": head["seq"], "corpus_or_run": head["event"].get("run_id"),
              "anchored_at": datetime.now(timezone.utc).isoformat(), "operator": operator,
              "note": "procedural anchor only (ADR-004 section 5): traceability, not a security boundary"}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def verify_anchor(journal_path, anchor_path):
    entries = load(journal_path)
    if not entries:
        return ["the journal is empty"]
    record = json.loads(Path(anchor_path).read_text(encoding="utf-8"))
    head = entries[-1][1]
    problems = verify(journal_path)
    if record.get("head") != head["hash"]:
        problems.append(f"anchor head {record.get('head')!r} != journal head {head['hash']!r}")
    if record.get("seq") != head["seq"]:
        problems.append(f"anchor seq {record.get('seq')!r} != journal seq {head['seq']!r}")
    if not record.get("operator"):
        problems.append("the anchor carries no operator attestation")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    a = sub.add_parser("append")
    a.add_argument("--journal", required=True)
    a.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    v = sub.add_parser("verify")
    v.add_argument("--journal", required=True)
    an = sub.add_parser("anchor")
    an.add_argument("--journal", required=True)
    an.add_argument("--out", required=True)
    an.add_argument("--operator", required=True)
    va = sub.add_parser("verify-anchor")
    va.add_argument("--journal", required=True)
    va.add_argument("--anchor", required=True)
    args = ap.parse_args(argv)
    try:
        if args.command == "append":
            event = {}
            for item in args.set:
                if "=" not in item:
                    raise ValueError(f"--set expects KEY=VALUE, got {item!r}")
                k, val = item.split("=", 1)
                try:
                    val = json.loads(val)
                except ValueError:
                    pass
                event[k] = val
            entry = append(args.journal, event)
            print(f"appended seq={entry['seq']} hash={entry['hash'][:16]}...")
            return 0
        if args.command == "verify":
            problems = verify(args.journal)
            for p in problems:
                print(f"audit_journal: BROKEN: {p}", file=sys.stderr)
            print("audit_journal: OK" if not problems else "audit_journal: the chain does NOT hold")
            return 0 if not problems else 2
        if args.command == "anchor":
            record = anchor(args.journal, args.out, args.operator)
            print(f"anchored head={record['head'][:16]}... seq={record['seq']} at {args.out}")
            return 0
        problems = verify_anchor(args.journal, args.anchor)
        for p in problems:
            print(f"audit_journal: ANCHOR MISMATCH: {p}", file=sys.stderr)
        print("audit_journal: anchor OK" if not problems else "audit_journal: anchor does NOT verify")
        return 0 if not problems else 2
    except (ValueError, OSError) as exc:
        print(f"audit_journal: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
