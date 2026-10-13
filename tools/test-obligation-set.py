#!/usr/bin/env python3
"""Tests for the Obligation Set: schema, canonical digest, and report coverage.

Four questions, each answered by tests that can fail:
  * Is the schema itself sound, and does it reject malformed sets (with the intended rule)?
  * Does the digest stay put under anything that is not a change of meaning
    (indentation, line endings, key order, array order, \\u escapes, Unicode normalisation)?
  * Does it move under every change of meaning, however small?
  * Does semantic validation refuse a report that omits, duplicates, invents or
    mis-digests an obligation of the frozen set?

Run with `make test`, or `python tools/test-obligation-set.py` inside the pinned environment.
"""
import copy
import importlib.util
import json
import sys
import tempfile
import unicodedata
from pathlib import Path

import obligation_set as oset
import validate

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "docs" / "specifications" / "examples" / "queue.obligations.json"
# Pinned on purpose: if canonicalisation changes by accident this fails, and if it changes
# on purpose the maintainer must bump selyth_obligation_set_version and update this value.
DEMO_DIGEST = "sha256:1ee6ae78adc7350b90066fa64c778715a50cbc1eed2d319678cd54d0134eea2a"

spec = importlib.util.spec_from_file_location("legacy_test_schemas",
                                              ROOT / "tools" / "test-schemas.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)

FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


def demo():
    return json.loads(DEMO.read_text(encoding="utf-8"))


def digest_of_text(text):
    return oset.set_digest(oset.parse_obligation_set(text))


def digest_of(document):
    return oset.set_digest(document)


# ---------------------------------------------------------------- schema

def check_schema():
    heading("Schema")
    try:
        schema = validate.load_schema(oset.SET_SCHEMA)
        check(True, "obligation-set-schema.json: valid Draft 2020-12, no duplicate key, "
                    "unknown keyword or dangling $ref")
    except validate.SchemaError as exc:
        check(False, "obligation-set-schema.json unusable", f"    {exc}")
        return None
    check(not validate.validate(demo(), schema), "the demonstration Obligation Set is valid")

    def mutated(fn):
        d = demo()
        fn(d)
        return d

    first = lambda d: d["obligations"][0]
    cases = [
        ("missing set_id", lambda d: d.pop("set_id"), ("$", "required", "set_id")),
        ("missing specification_version", lambda d: d.pop("specification_version"),
         ("$", "required", "specification_version")),
        ("unpinned schema version", lambda d: d.update(selyth_obligation_set_version="1"),
         ("$.selyth_obligation_set_version", "pattern")),
        ("empty obligation list", lambda d: d.update(obligations=[]),
         ("$.obligations", "minItems")),
        ("unknown top-level field", lambda d: d.update(note="x"),
         ("$", "additionalProperties")),
        ("unknown obligation field", lambda d: first(d).update(owner="x"),
         ("$.obligations[0]", "additionalProperties")),
        ("identifier with a space", lambda d: first(d).update(obligation_id="OBL 1"),
         ("$.obligations[0].obligation_id", "pattern")),
        ("non-ASCII identifier", lambda d: first(d).update(obligation_id="OBL-é"),
         ("$.obligations[0].obligation_id", "pattern")),
        ("unknown kind", lambda d: first(d).update(kind="wish"),
         ("$.obligations[0].kind", "enum")),
        ("empty statement", lambda d: first(d).update(statement=""),
         ("$.obligations[0].statement", "minLength")),
        ("unknown required evidence kind",
         lambda d: first(d).update(required_evidence_kind="trust-me"),
         ("$.obligations[0].required_evidence_kind", "enum")),
        ("null intent (absent is the only 'none')",
         lambda d: first(d).update(intent_statement=None),
         ("$.obligations[0].intent_statement", "type")),
        ("empty intent", lambda d: first(d).update(intent_statement=""),
         ("$.obligations[0].intent_statement", "minLength")),
        ("missing assumptions (empty must be explicit)",
         lambda d: first(d).pop("assumptions"), ("$.obligations[0]", "required", "assumptions")),
        ("assumption that is not a string", lambda d: first(d).update(assumptions=[1]),
         ("$.obligations[0].assumptions[0]", "type")),
        ("repeated assumption",
         lambda d: first(d).update(assumptions=["a", "a"]),
         ("$.obligations[0].assumptions", "uniqueItems")),
        ("no affected component", lambda d: first(d).update(affected_components=[]),
         ("$.obligations[0].affected_components", "minItems")),
        ("number where a string is required", lambda d: first(d).update(statement=3),
         ("$.obligations[0].statement", "type")),
    ]
    for name, fn, reason in cases:
        problems = validate.validate(mutated(fn), schema)
        path, keyword, *fragment = reason
        hit = [p for p in problems if p.path == path and p.keyword == keyword
               and (not fragment or fragment[0] in p.message)]
        check(bool(hit), f"rejected: {name} <- {path} ({keyword})",
              "    reported: " + "; ".join(f"{p.path} {p.keyword}" for p in problems))
    return schema


# ---------------------------------------------------------------- canonical form

def reorder_keys(value):
    if isinstance(value, dict):
        return {k: reorder_keys(value[k]) for k in reversed(list(value))}
    if isinstance(value, list):
        return [reorder_keys(v) for v in value]
    return value


def reverse_arrays(document):
    d = copy.deepcopy(document)
    d["obligations"].reverse()
    for o in d["obligations"]:
        o["assumptions"].reverse()
        o["affected_components"].reverse()
    return d


def check_vector():
    heading("Canonical form (hand-written test vector from the specification)")
    document = {
        "obligations": [{"affected_components": ["b.ts", "a.ts"], "assumptions": [],
                         "statement": "ensures x > 0", "kind": "postcondition",
                         "required_evidence_kind": "kernel-proven", "obligation_id": "OBL-1"}],
        "specification_version": "1.0.0", "set_id": "demo",
        "selyth_obligation_set_version": "0.1.0"}
    expected = ('{"obligations":[{"affected_components":["a.ts","b.ts"],"assumptions":[],'
                '"kind":"postcondition","obligation_id":"OBL-1",'
                '"required_evidence_kind":"kernel-proven","statement":"ensures x > 0"}],'
                '"selyth_obligation_set_version":"0.1.0","set_id":"demo",'
                '"specification_version":"1.0.0"}')
    got = oset.canonical_bytes(document).decode("utf-8")
    check(got == expected, "canonical form equals the vector", f"    got {got}")
    # Escaping rules: only the listed escapes, everything else raw UTF-8.
    doc = demo()
    doc["obligations"][0]["statement"] = 'a "q" \\ \t \u001f \u007f   é 😀'
    raw = oset.canonical_bytes(doc).decode("utf-8")
    want = '"statement":"a \\"q\\" \\\\ \\t \\u001f \u007f   é 😀"'
    check(want in raw, "escapes: \\\" \\\\ \\t and \\u001f escaped; DEL, U+2028 and non-ASCII raw",
          f"    got {raw}")
    check(not oset.canonical_bytes(demo()).endswith(b"\n"), "no trailing newline")


def check_invariance():
    heading("Digest is invariant under anything that is not a change of meaning")
    base_doc = demo()
    base = digest_of(base_doc)
    check(base == DEMO_DIGEST, "demonstration set has its pinned digest", f"    got {base}")
    check(base == oset.set_digest(oset.load_obligation_set(DEMO)), "same digest via the file loader")

    texts = {
        "indent 0 (newlines only)": json.dumps(base_doc, indent=0),
        "indent 8": json.dumps(base_doc, indent=8),
        "tab indent": json.dumps(base_doc, indent="\t"),
        "compact": json.dumps(base_doc, separators=(",", ":")),
        "CRLF line endings": json.dumps(base_doc, indent=2).replace("\n", "\r\n"),
        "surrounding whitespace": "\n\n  " + json.dumps(base_doc) + "  \n\n",
        "keys reversed at every level": json.dumps(reorder_keys(base_doc), indent=3),
        "arrays reversed at every level": json.dumps(reverse_arrays(base_doc)),
        "keys and arrays reversed": json.dumps(reorder_keys(reverse_arrays(base_doc))),
        "ASCII-escaped non-ASCII (\\u00e9)": json.dumps(base_doc, ensure_ascii=True),
        "raw non-ASCII": json.dumps(base_doc, ensure_ascii=False),
    }
    for label, text in texts.items():
        got = digest_of_text(text)
        check(got == base, f"same digest: {label}", f"    got {got}")

    nfd = demo()
    for o in nfd["obligations"]:
        if "intent_statement" in o:
            o["intent_statement"] = unicodedata.normalize("NFD", o["intent_statement"])
    check(nfd["obligations"][1]["intent_statement"]
          != demo()["obligations"][1]["intent_statement"], "(setup) NFD text really differs in code points")
    check(digest_of(nfd) == base, "same digest: Unicode NFD input normalised to NFC")

    check(oset.canonical_bytes(demo()) == oset.canonical_bytes(
        json.loads(json.dumps(reverse_arrays(base_doc)))), "canonical bytes are identical, not only the digest")
    before = json.dumps(base_doc, sort_keys=True)
    oset.set_digest(base_doc)
    check(json.dumps(base_doc, sort_keys=True) == before, "digesting does not mutate its input")


def check_sensitivity():
    heading("Digest changes under every change of meaning")
    base = digest_of(demo())

    def ob(d, i=0):
        return d["obligations"][i]

    def swap_assumptions(d):
        a, b = ob(d, 0)["assumptions"], ob(d, 2)["assumptions"]
        ob(d, 0)["assumptions"], ob(d, 2)["assumptions"] = b, a

    def swap_ids(d):
        ob(d, 0)["obligation_id"], ob(d, 1)["obligation_id"] = \
            ob(d, 1)["obligation_id"], ob(d, 0)["obligation_id"]

    def one_char(d):
        s = ob(d)["statement"]
        ob(d)["statement"] = s[:-1] + chr(ord(s[-1]) + 1)

    changes = [
        ("one character of one statement", one_char),
        ("comparison > becomes >=", lambda d: ob(d).update(statement="ensures result.length >= 0")),
        ("case of a statement", lambda d: ob(d).update(statement=ob(d)["statement"].upper())),
        ("extra space inside a statement",
         lambda d: ob(d).update(statement="ensures  result.length > 0")),
        ("trailing space in a statement", lambda d: ob(d).update(statement=ob(d)["statement"] + " ")),
        ("kind", lambda d: ob(d).update(kind="invariant")),
        ("required evidence kind", lambda d: ob(d).update(required_evidence_kind="property-tested")),
        ("intent edited", lambda d: ob(d).update(intent_statement="The queue is never empty.")),
        ("intent removed", lambda d: ob(d).pop("intent_statement")),
        ("intent added", lambda d: ob(d, 2).update(intent_statement="It terminates.")),
        ("assumption added", lambda d: ob(d).update(assumptions=["x", *ob(d)["assumptions"]])),
        ("assumption removed", lambda d: ob(d).update(assumptions=[])),
        ("assumption edited", lambda d: ob(d).update(assumptions=["list append is total"])),
        ("assumption moved to another obligation", swap_assumptions),
        ("affected component added", lambda d: ob(d).update(affected_components=["queue.ts", "x.ts"])),
        ("affected component renamed", lambda d: ob(d).update(affected_components=["Queue.ts"])),
        ("obligation_id changed", lambda d: ob(d).update(obligation_id="OBL-0099")),
        ("two obligation ids swapped (statements stay)", swap_ids),
        ("obligation removed", lambda d: d["obligations"].pop()),
        ("obligation added", lambda d: d["obligations"].append({**copy.deepcopy(ob(d)),
                                                                "obligation_id": "OBL-0100"})),
        ("specification_version", lambda d: d.update(specification_version="1.0.1")),
        ("set_id", lambda d: d.update(set_id="queue2")),
        ("title edited", lambda d: d.update(title="Bounded queues")),
        ("title removed", lambda d: d.pop("title")),
        ("schema version", lambda d: d.update(selyth_obligation_set_version="0.2.0")),
    ]
    seen = {base: "unchanged"}
    for label, fn in changes:
        d = demo()
        fn(d)
        got = digest_of(d)
        clash = seen.get(got)
        check(clash is None, f"digest changes: {label}",
              f"    same digest as {clash!r}: {got}")
        seen[got] = label

    # Locality: editing one obligation moves its digest and no other.
    before = oset.obligation_digests(demo())
    d = demo()
    one_char(d)
    after = oset.obligation_digests(d)
    moved = sorted(k for k in before if before[k] != after[k])
    check(moved == ["OBL-0007"], "editing one obligation moves only its own digest", f"    moved {moved}")
    d = demo()
    d["specification_version"] = "1.0.1"
    check(oset.obligation_digests(d) == before,
          "re-versioning the set keeps obligation digests (the set digest still moves)")


def check_refusals():
    heading("Semantic refusals while canonicalising")
    base = demo()

    def refused(label, text):
        try:
            oset.parse_obligation_set(text)
            check(False, f"refused: {label}", "    accepted")
        except oset.ObligationSetError:
            check(True, f"refused: {label}")

    dup_id = copy.deepcopy(base)
    dup_id["obligations"][1]["obligation_id"] = "OBL-0007"
    refused("two obligations with one obligation_id", json.dumps(dup_id))
    nfc_dup = copy.deepcopy(base)
    nfc_dup["obligations"][0]["assumptions"] = ["café", "café"]
    refused("assumptions equal only after NFC", json.dumps(nfc_dup))
    comp_dup = copy.deepcopy(base)
    comp_dup["obligations"][0]["affected_components"] = ["é.ts", "é.ts"]
    refused("components equal only after NFC", json.dumps(comp_dup))
    text = json.dumps(base)
    refused("duplicate JSON key", text.replace('"set_id": "queue"', '"set_id": "queue", "set_id": "x"'))
    refused("NaN literal", text.replace('"queue"', "NaN", 1))
    refused("lone surrogate", text.replace("Bounded queue", "Bounded \\ud800 queue"))
    refused("byte-order mark", "﻿" + text)
    refused("not JSON", "{not json")
    with tempfile.TemporaryDirectory(prefix="selyth-oset-") as tmp:
        path = Path(tmp) / "bad.json"
        original = DEMO.read_bytes()
        at = original.index(b"Bounded queue") + 7
        path.write_bytes(original[:at] + b"\xff\xfe" + original[at:])
        try:
            oset.load_obligation_set(path)
            check(False, "refused: malformed UTF-8 in a file", "    accepted")
        except oset.ObligationSetError as exc:
            check("UTF-8" in str(exc), "refused: malformed UTF-8 in a file", f"    {exc}")


# ---------------------------------------------------------------- coverage

def report_for(document):
    """A structurally valid report covering `document`, built from the legacy fixtures."""
    report = copy.deepcopy(legacy.good_report)
    template = report["obligations"][0]
    digests = oset.obligation_digests(document)
    report["obligations"] = []
    for o in oset.canonical_form(document)["obligations"]:
        entry = copy.deepcopy(template)
        entry.update({"obligation_id": o["obligation_id"],
                      "obligation_digest": digests[o["obligation_id"]],
                      "specification_version": document["specification_version"],
                      "kind": o["kind"], "statement": o["statement"],
                      "intent_statement": o.get("intent_statement"),
                      "affected_components": o["affected_components"]})
        report["obligations"].append(entry)
    for s in report["subject"]:
        if s["role"] == "obligation-set":
            s["digest"] = digest_of(document)
    return report


def check_coverage():
    heading("Coverage: a report must account for exactly the frozen obligations")
    schema = validate.load_schema(oset.REPORT_SCHEMA)
    document = oset.load_obligation_set(DEMO)
    good = report_for(document)
    check(not validate.validate(good, schema), "(setup) the covering report is structurally valid")
    check(oset.check_coverage(document, good) == [], "a report listing every obligation is covered")

    def case(label, fn, fragment):
        report = copy.deepcopy(good)
        fn(report)
        structural = validate.validate(report, schema)
        problems = oset.check_coverage(document, report)
        check(not structural, f"(setup) {label}: still valid for JSON Schema, the gap is semantic",
              "    " + "; ".join(p.message for p in structural))
        check(any(fragment in p for p in problems), f"refused: {label}",
              "    reported: " + (" | ".join(problems) or "(covered)"))

    def drop(report, oid):
        report["obligations"] = [e for e in report["obligations"] if e["obligation_id"] != oid]

    case("omits the first obligation", lambda r: drop(r, "OBL-0007"),
         "obligation OBL-0007 is in the set but omitted")
    case("omits the last obligation", lambda r: drop(r, "OBL-0009"),
         "obligation OBL-0009 is in the set but omitted")
    case("omits all but one", lambda r: r.update(obligations=r["obligations"][:1]),
         "OBL-0008 is in the set but omitted")
    case("lists one obligation twice", lambda r: r["obligations"].append(
         copy.deepcopy(r["obligations"][0])), "OBL-0007 appears 2 times")

    def swap_in_stranger(r):
        r["obligations"][0]["obligation_id"] = "OBL-0777"
    case("replaces an obligation by one not in the set (same count)", swap_in_stranger,
         "OBL-0007 is in the set but omitted")
    case("adds an obligation not in the set",
         lambda r: r["obligations"].append({**copy.deepcopy(r["obligations"][0]),
                                            "obligation_id": "OBL-0777"}),
         "report contains obligation OBL-0777")
    case("carries a stale obligation digest",
         lambda r: r["obligations"][1].update(obligation_digest="sha256:" + "0" * 64),
         "obligation OBL-0008: report digest")
    case("carries another specification_version",
         lambda r: r["obligations"][2].update(specification_version="1.0.1"),
         "specification_version 1.0.1")

    def other_set_digest(r):
        for s in r["subject"]:
            if s["role"] == "obligation-set":
                s["digest"] = "sha256:" + "b" * 64
    case("names a different obligation-set digest", other_set_digest,
         "obligation-set subject digest")
    case("names no obligation-set subject",
         lambda r: r.update(subject=[s for s in r["subject"] if s["role"] != "obligation-set"]),
         "exactly one subject with role 'obligation-set', found 0")
    case("names two obligation-set subjects", lambda r: r["subject"].append(
         copy.deepcopy(next(s for s in r["subject"] if s["role"] == "obligation-set"))),
         "found 2")

    # Freeze: editing the set after the report was produced makes the report stale.
    edited = copy.deepcopy(demo())
    edited["obligations"][0]["statement"] += " && true"
    problems = oset.check_coverage(edited, good)
    check(any("obligation-set subject digest" in p for p in problems)
          and any("OBL-0007: report digest" in p for p in problems),
          "a report is stale once the set is edited (set and obligation digests both mismatch)",
          "    reported: " + " | ".join(problems))
    reordered = reverse_arrays(demo())
    check(oset.check_coverage(reordered, good) == [],
          "reordering the set's arrays does not stale the report")


def check_cli():
    heading("Command line")
    import subprocess
    py, tool = sys.executable, str(ROOT / "tools" / "obligation_set.py")

    def run(*args):
        return subprocess.run([py, tool, *args], capture_output=True, text=True)

    r = run("digest", str(DEMO))
    check(r.returncode == 0 and r.stdout.strip() == DEMO_DIGEST, "digest prints the pinned digest")
    r = run("check", str(DEMO))
    check(r.returncode == 0, "check exits 0 on the demonstration set")
    r = run("cover", str(DEMO))
    check(r.returncode == 2, "cover without a report is a usage error (exit 2)")
    r = run("check", str(ROOT / "docs" / "specifications" / "report-schema.json"))
    check(r.returncode == 1, "check exits 1 on a document that is not an Obligation Set")
    document = oset.load_obligation_set(DEMO)
    with tempfile.TemporaryDirectory(prefix="selyth-oset-") as tmp:
        good = report_for(document)
        bad = copy.deepcopy(good)
        bad["obligations"].pop()
        for name, report in (("good", good), ("bad", bad)):
            (Path(tmp) / f"{name}.json").write_text(json.dumps(report), encoding="utf-8")
        r = run("cover", str(DEMO), str(Path(tmp) / "good.json"))
        check(r.returncode == 0, "cover exits 0 on a covering report", r.stdout)
        r = run("cover", str(DEMO), str(Path(tmp) / "bad.json"))
        check(r.returncode == 1 and "OBL-0009 is in the set but omitted" in r.stdout,
              "cover exits 1 and names the omitted obligation", r.stdout)


def main():
    schema = check_schema()
    if schema is not None:
        check_vector()
        check_invariance()
        check_sensitivity()
        check_refusals()
        check_coverage()
        check_cli()
    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
