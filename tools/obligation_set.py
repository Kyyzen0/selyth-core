#!/usr/bin/env python3
"""Validate, canonicalise and hash a Selyth Obligation Set; check report coverage.

The rules are defined in docs/specifications/obligation-set-canonicalization.md and
this module follows them. It makes no decision about acceptance: that is the policy
engine's job. `cover` only answers whether a report accounts for exactly the
obligations of a frozen set, a question about two documents that JSON Schema cannot
ask (docs/specifications/validation-layers.md).

Usage:
  obligation_set.py check     <obligation-set.json>   validate and report problems
  obligation_set.py canonical <obligation-set.json>   write the canonical form (no newline)
  obligation_set.py digest    <obligation-set.json>   print the set digest
  obligation_set.py digests   <obligation-set.json>   print each obligation digest
  obligation_set.py cover     <obligation-set.json> <report.json>
Exit status: 0 ok, 1 invalid or not covered, 2 usage error or unreadable input.
"""
import hashlib
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import validate

ROOT = Path(__file__).resolve().parent.parent
SET_SCHEMA = ROOT / "docs" / "specifications" / "obligation-set-schema.json"
REPORT_SCHEMA = ROOT / "docs" / "specifications" / "report-schema.json"


class ObligationSetError(ValueError):
    """The Obligation Set is not well formed, structurally or semantically."""


def _nfc(value):
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_nfc(v) for v in value]
    if isinstance(value, dict):
        return {_nfc(k): _nfc(v) for k, v in value.items()}
    return value


def _sorted_set(values, what):
    out = sorted(values)
    dupes = sorted(v for v, n in Counter(out).items() if n > 1)
    if dupes:
        raise ObligationSetError(f"duplicate {what} after normalisation: {dupes}")
    return out


def _canonical_obligation(obligation):
    obligation = _nfc(obligation)
    obligation["assumptions"] = _sorted_set(obligation["assumptions"], "assumption")
    obligation["affected_components"] = _sorted_set(obligation["affected_components"],
                                                    "affected component")
    return obligation


def canonical_form(document):
    """Return the normalised document: NFC strings, arrays sorted, duplicates refused."""
    document = _nfc(document)
    obligations = [_canonical_obligation(o) for o in document["obligations"]]
    ids = [o["obligation_id"] for o in obligations]
    _sorted_set(ids, "obligation_id")
    document["obligations"] = sorted(obligations, key=lambda o: o["obligation_id"])
    return document


def _serialise(value):
    # json.dumps escapes exactly what the canonical form requires (", \, \b \f \n \r \t,
    # other controls as lowercase \u00xx) and, with ensure_ascii off, leaves the rest raw.
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ObligationSetError("a string contains a lone surrogate, which has no UTF-8 "
                                 "encoding") from exc


def canonical_bytes(document):
    return _serialise(canonical_form(document))


def _digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def set_digest(document):
    return _digest(canonical_bytes(document))


def obligation_digests(document):
    """Map obligation_id to its digest (the object alone, without set-level fields)."""
    return {o["obligation_id"]: _digest(_serialise(o))
            for o in canonical_form(document)["obligations"]}


def parse_obligation_set(text):
    """Parse and validate an Obligation Set from JSON text; return the parsed document."""
    try:
        document = validate.loads(text)
    except (validate.SchemaError, json.JSONDecodeError) as exc:
        raise ObligationSetError(f"not valid JSON: {exc}") from exc
    problems = validate.validate(document, validate.load_schema(SET_SCHEMA))
    if problems:
        raise ObligationSetError("schema violation: " + "; ".join(
            f"{p.path}: {p.message}" for p in problems))
    canonical_bytes(document)  # semantic checks: duplicates, lone surrogates
    return document


def load_obligation_set(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ObligationSetError(f"not valid UTF-8: {exc.reason}") from exc
    return parse_obligation_set(text)


def check_coverage(obligation_set, report):
    """Return the reasons `report` does not account for exactly `obligation_set`.

    Both documents must already be structurally valid. An empty result means covered.
    """
    problems = []
    expected_set = set_digest(obligation_set)
    subjects = [s for s in report["subject"] if s["role"] == "obligation-set"]
    if len(subjects) != 1:
        problems.append("report must name exactly one subject with role 'obligation-set', "
                        f"found {len(subjects)}")
    elif subjects[0]["digest"] != expected_set:
        problems.append(f"obligation-set subject digest {subjects[0]['digest']} is not the "
                        f"set digest {expected_set}")

    expected = obligation_digests(obligation_set)
    frozen = {o["obligation_id"]: o for o in canonical_form(obligation_set)["obligations"]}
    entries = report["obligations"]
    seen = Counter(e["obligation_id"] for e in entries)
    for oid in sorted(expected):
        if seen[oid] == 0:
            problems.append(f"obligation {oid} is in the set but omitted from the report")
        elif seen[oid] > 1:
            problems.append(f"obligation {oid} appears {seen[oid]} times in the report")
    for oid in sorted(set(seen) - set(expected)):
        problems.append(f"report contains obligation {oid}, which is not in the set")
    for entry in entries:
        oid = entry["obligation_id"]
        if oid not in expected:
            continue
        if entry["obligation_digest"] != expected[oid]:
            problems.append(f"obligation {oid}: report digest {entry['obligation_digest']} "
                            f"is not the frozen digest {expected[oid]}")
        if entry["specification_version"] != obligation_set["specification_version"]:
            problems.append(f"obligation {oid}: specification_version "
                            f"{entry['specification_version']} is not "
                            f"{obligation_set['specification_version']}")
        # The fields a report echoes from the set. The obligation digest already binds them,
        # so this cannot catch anything the digest misses; it names a forged echo field by
        # field instead of only as a digest mismatch.
        set_obligation = frozen[oid]
        for field in ("kind", "statement", "required_evidence_kind"):
            if _nfc(entry[field]) != _nfc(set_obligation[field]):
                problems.append(f"obligation {oid}: report {field} {entry[field]!r} is not "
                                f"the frozen {set_obligation[field]!r}")
        if _nfc(entry.get("intent_statement")) != _nfc(set_obligation.get("intent_statement")):
            problems.append(f"obligation {oid}: report intent_statement "
                            f"{entry.get('intent_statement')!r} is not the frozen "
                            f"{set_obligation.get('intent_statement')!r}")
        if sorted(_nfc(entry["affected_components"])) != sorted(_nfc(set_obligation["affected_components"])):
            problems.append(f"obligation {oid}: report affected_components "
                            f"{entry['affected_components']} are not the frozen "
                            f"{set_obligation['affected_components']}")
    return problems


def _usage():
    print(__doc__.strip().split("Usage:")[1].split("Exit status")[0].rstrip())
    return 2


def main(argv):
    commands = {"check": 1, "canonical": 1, "digest": 1, "digests": 1, "cover": 2}
    if len(argv) < 2 or commands.get(argv[1]) != len(argv) - 2:
        return _usage()
    command = argv[1]
    try:
        document = load_obligation_set(argv[2])
    except ObligationSetError as exc:
        print(f"[FAIL] {argv[2]}: {exc}")
        return 1
    except (OSError, validate.SchemaError) as exc:
        print(f"[ERROR] {exc}")
        return 2
    if command == "check":
        print(f"[PASS] {argv[2]}")
    elif command == "canonical":
        sys.stdout.buffer.write(canonical_bytes(document))
    elif command == "digest":
        print(set_digest(document))
    elif command == "digests":
        for oid, digest in obligation_digests(document).items():
            print(f"{oid} {digest}")
    else:
        try:
            report = validate.load_json(argv[3])
            report_problems = validate.validate(report, validate.load_schema(REPORT_SCHEMA))
        except (OSError, json.JSONDecodeError, validate.SchemaError) as exc:
            print(f"[ERROR] {exc}")
            return 2
        if report_problems:
            print(f"[FAIL] {argv[3]} is not a valid report:")
            for p in report_problems:
                print(f"        {p.path}: {p.message} ({p.keyword})")
            return 1
        problems = check_coverage(document, report)
        print(f"[{'FAIL' if problems else 'PASS'}] {argv[3]} covers {argv[2]}")
        for problem in problems:
            print(f"        {problem}")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
