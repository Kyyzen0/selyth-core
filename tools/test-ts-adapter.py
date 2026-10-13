#!/usr/bin/env python3
"""Tests for the TypeScript adapter (docs/specifications/verification-ir.md).

The milestone is determinism: regenerating the Verification IR from the same
inputs gives a byte-identical file. This file was written before the adapter.
Until tools/ts_adapter.py exists it fails with "adapter not implemented", which is
the right reason.

Run with `make test`, or `python tools/test-ts-adapter.py` in the pinned environment.
"""
import copy
import hashlib
import importlib.util
import json
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import obligation_set as oset
import validate

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


try:
    import ts_adapter as adapter
except ImportError:
    adapter = None

spec = importlib.util.spec_from_file_location("test_obligation_set", TOOLS / "test-obligation-set.py")
osets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(osets)

DEMO = osets.DEMO
BACKEND = "4.11.0"
# Pinned: if the adapter's output for the demonstration set changes, ADAPTER_VERSION must
# change with it, and this value be updated in the same commit.
GOLDEN = {"adapter_version": "0.2.1",
          "sha256": "sha256:4f20d0d13af2656c2f3c6b15c83f0c9260d0385799ecc4b53fd2c1f1952499eb"}


def lowered(document=None, backend=BACKEND):
    return adapter.render(adapter.lower(document or osets.demo(), backend))


def run_cli(*args, env=None, cwd=None):
    return subprocess.run([sys.executable, str(TOOLS / "ts_adapter.py"), *args],
                          capture_output=True, text=True, env=env, cwd=cwd)


def check_determinism():
    heading("Determinism: the same inputs give a byte-identical IR")
    first = lowered()
    check(lowered() == first, "two runs in one process")
    check(not first.startswith(" ") and first.endswith("}\n"), "rendered as indented JSON with one final newline")
    document = osets.demo()
    frozen = json.dumps(document, sort_keys=True)
    lowered(document)
    check(json.dumps(document, sort_keys=True) == frozen, "the Obligation Set is not mutated")

    outputs = set()
    with tempfile.TemporaryDirectory(prefix="selyth-ir-") as tmp:
        for seed, tz, lang in (("0", "UTC", "C"), ("1", "Asia/Tokyo", "fr_FR.UTF-8"),
                               ("999", "America/Sao_Paulo", "en_US.UTF-8"), ("random", "UTC", "C")):
            env = {**os.environ, "PYTHONHASHSEED": seed, "TZ": tz, "LANG": lang, "LC_ALL": lang}
            run = run_cli("lower", str(DEMO), "--backend-version", BACKEND, env=env, cwd=tmp)
            outputs.add((run.returncode, run.stdout))
        check(outputs == {(0, first)}, "byte-identical across processes, hash seeds, time zones, "
              "locales and working directories", f"    {len(outputs)} distinct outputs")
        out = Path(tmp) / "ir.json"
        out.write_text(first, encoding="utf-8")
        out.unlink()
        regenerated = run_cli("lower", str(DEMO), "--backend-version", BACKEND).stdout
        check(regenerated == first, "delete the IR and regenerate it: same bytes")

    base = osets.demo()
    variants = {
        "keys reversed": osets.reorder_keys(base),
        "arrays reversed": osets.reverse_arrays(base),
        "keys and arrays reversed": osets.reorder_keys(osets.reverse_arrays(base)),
    }
    for label, variant in variants.items():
        check(lowered(variant) == first, f"Obligation Set with {label}: same IR")
    for label, text in (("indent 8", json.dumps(base, indent=8)),
                        ("compact", json.dumps(base, separators=(",", ":"))),
                        ("CRLF", json.dumps(base, indent=2).replace("\n", "\r\n"))):
        with tempfile.TemporaryDirectory(prefix="selyth-ir-") as tmp:
            path = Path(tmp) / "set.json"
            path.write_bytes(text.encode("utf-8"))
            same = run_cli("lower", str(path), "--backend-version", BACKEND).stdout == first
        check(same, f"Obligation Set file formatted as {label}: same IR")

    rich = osets.demo()
    for i, o in enumerate(rich["obligations"]):
        o["assumptions"] = [f"assumption {c} of {i}" for c in "fbdaec"]
        o["affected_components"] = [f"{c}/mod{i}.ts" for c in "zyxwvu"]
    rich["obligations"].append({**copy.deepcopy(rich["obligations"][0]), "obligation_id": "U-1",
                                "kind": "permission", "statement": "ensures true"})
    with tempfile.TemporaryDirectory(prefix="selyth-ir-") as tmp:
        path = Path(tmp) / "rich.json"
        path.write_text(json.dumps(rich), encoding="utf-8")
        rich_outputs = {run_cli("lower", str(path), "--backend-version", BACKEND,
                                env={**os.environ, "PYTHONHASHSEED": seed}).stdout
                        for seed in ("0", "1", "2", "3", "77", "random")}
    check(len(rich_outputs) == 1 and "assumption a of 0" in next(iter(rich_outputs)),
          "a set with many assumptions, components and an unsupported obligation: identical bytes "
          "under six hash seeds (no set or dict ordering leaks)", f"    {len(rich_outputs)} distinct outputs")
    ctx = adapter.lower(rich, BACKEND)["units"][0]["context"]
    check(ctx["assumptions"] == sorted(ctx["assumptions"]) and ctx["affected_components"]
          == sorted(ctx["affected_components"]), "assumptions and components are emitted sorted")

    heading("The IR holds nothing volatile")
    host = socket.gethostname()
    leaks = [name for name, pattern in (
        ("working directory", str(ROOT)), ("home directory", str(Path.home())),
        ("host name", host)) if pattern and pattern in first]
    leaks += [name for name, pattern in (
        ("date", r"\d{4}-\d{2}-\d{2}"), ("clock time", r"\d{2}:\d{2}:\d{2}"),
        ("uuid", r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))
        if re.search(pattern, first)]
    check(not leaks, "no path, host name, date, time or uuid", f"    found {leaks}")

    document = osets.demo()  # read before the patches: only lower() must stay pure

    def forbidden(*args, **kwargs):
        raise AssertionError("lower() touched the outside world")
    with mock.patch("builtins.open", forbidden), mock.patch.object(socket, "socket", forbidden), \
            mock.patch.object(subprocess, "Popen", forbidden), mock.patch.object(time, "time", forbidden), \
            mock.patch.object(random, "random", forbidden), mock.patch.object(os, "urandom", forbidden), \
            mock.patch.object(os, "stat", forbidden), mock.patch.object(Path, "read_text", forbidden):
        try:
            check(lowered(document) == first, "lower() uses no file, socket, process, clock or randomness")
        except AssertionError as exc:
            check(False, "lower() uses no file, socket, process, clock or randomness", f"    {exc}")


def check_key():
    heading("Reproducibility key: obligation set digest, adapter, backend, IR version")
    base = adapter.lower(osets.demo(), BACKEND)
    schema = validate.load_schema(ROOT / "docs" / "specifications" / "verification-ir-schema.json")
    check(not validate.validate(base, schema), "the IR is valid for verification-ir-schema.json",
          "    " + "; ".join(p.path + " " + p.message for p in validate.validate(base, schema)))
    expected = "sha256:" + hashlib.sha256(json.dumps({
        "adapter": base["adapter"], "backend": base["backend"],
        "ir_version": base["selyth_ir_version"], "obligation_set_digest": base["obligation_set"]["digest"]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    check(base["reproducibility_key"] == expected, "key = digest of the four inputs (recomputed independently)")
    check(base["obligation_set"]["digest"] == oset.set_digest(osets.demo()), "carries the Obligation Set digest")
    check(base["backend"] == {"name": "dafny", "version": BACKEND}, "names the verification tool and its declared version")

    seen = {base["reproducibility_key"]: "baseline"}

    def changed(label, ir):
        key, other = ir["reproducibility_key"], seen.get(ir["reproducibility_key"])
        check(other is None and adapter.render(ir) != adapter.render(base),
              f"key and IR change: {label}", f"    same key as {other!r}")
        seen[key] = label

    edited = osets.demo()
    edited["obligations"][0]["statement"] += " && true"
    changed("one obligation edited", adapter.lower(edited, BACKEND))
    changed("backend version", adapter.lower(osets.demo(), "4.11.1"))
    with mock.patch.object(adapter, "ADAPTER_VERSION", "9.9.9"):
        ir = adapter.lower(osets.demo(), BACKEND)
        check(ir["adapter"]["version"] == "9.9.9", "the adapter version comes from the adapter")
        changed("adapter version", ir)
    with mock.patch.object(adapter, "ADAPTER_NAME", "another-adapter"):
        changed("adapter name", adapter.lower(osets.demo(), BACKEND))
    with mock.patch.object(adapter, "IR_VERSION", "9.9.9"):
        changed("IR version", adapter.lower(osets.demo(), BACKEND))
    renamed = osets.demo()
    renamed["title"] = "Another title"
    changed("a field of the set that is not an obligation (title)", adapter.lower(renamed, BACKEND))

    heading("Golden IR: output moves only with the adapter version")
    digest = "sha256:" + hashlib.sha256(lowered().encode()).hexdigest()
    if adapter.ADAPTER_VERSION != GOLDEN["adapter_version"]:
        check(False, "ADAPTER_VERSION changed: update GOLDEN in this file",
              f"    adapter is {adapter.ADAPTER_VERSION}, golden is for {GOLDEN['adapter_version']}")
    else:
        check(digest == GOLDEN["sha256"], "the demonstration IR equals its pinned golden",
              f"    got {digest}. If the output changed on purpose, bump ADAPTER_VERSION and update "
              "GOLDEN in this file in the same commit; if not, the lowering is not deterministic.")


def check_lowering():
    heading("Lowering (expected clauses written by hand)")
    ir = adapter.lower(osets.demo(), BACKEND)
    got = {u["obligation_id"]: (u["clause"]["keyword"], u["clause"]["expression"]) for u in ir["units"]}
    check(got == {"OBL-0007": ("ensures", "result.length > 0"),
                  "OBL-0008": ("invariant", "0 <= size <= capacity"),
                  "OBL-0009": ("decreases", "queue.length")}, "demonstration obligations", f"    {got}")
    check([u["obligation_id"] for u in ir["units"]] == sorted(got) and ir["unsupported"] == [],
          "units sorted by obligation_id; nothing unsupported in the demonstration set")
    unit = next(u for u in ir["units"] if u["obligation_id"] == "OBL-0009")
    check(unit["context"] == {"assumptions": ["the scheduler is fair"],
                              "affected_components": ["drain.ts", "queue.ts"]},
          "assumptions and components travel as context (sorted)")
    check(not re.search(r"\bassume\b", json.dumps([u["clause"] for u in ir["units"]])),
          "assumptions are never lowered to `assume`")
    canonical = {o["obligation_id"]: o for o in oset.canonical_form(osets.demo())["obligations"]}
    check(all(u[f] == canonical[u["obligation_id"]][f] for u in ir["units"]
              for f in ("kind", "statement", "required_evidence_kind"))
          and [u.get("intent_statement") for u in ir["units"]]
          == [canonical[u["obligation_id"]].get("intent_statement") for u in ir["units"]]
          and "intent_statement" not in ir["units"][2],
          "each unit carries kind, statement, required evidence kind and intent (absent when none): "
          "a report entry can be built from the IR alone")
    digests = oset.obligation_digests(osets.demo())
    check(all(u["obligation_digest"] == digests[u["obligation_id"]] for u in ir["units"]),
          "each unit carries the obligation digest the set gives it")

    canon = adapter.canonical_expression
    for source, want in (("a+b*c", "a + b * c"), ("(a+b)*c", "(a + b) * c"), ("((a))", "a"),
                         ("a - (b - c)", "a - (b - c)"), ("a-b-c", "a - b - c"),
                         ("!(a&&b)", "!(a && b)"), ("!a&&b", "!a && b"), ("-x + 1", "-x + 1"),
                         ("a ==> b ==> c", "a ==> b ==> c"), ("(a ==> b) ==> c", "(a ==> b) ==> c"),
                         ("a||b&&c", "a || b && c"), ("(a||b)&&c", "(a || b) && c"),
                         ("0<=i  <  n", "0 <= i < n"), ("x.y .z>=0", "x.y.z >= 0"),
                         ("a == b", "a == b"), ("x%2==0", "x % 2 == 0"), ("true&&false", "true && false")):
        got = canon(source)
        check(got == want, f"canonical expression: {source!r} -> {want!r}", f"    got {got!r}")
    check(canon("a+b*c") == canon(" a +  b*c ") == canon("a + (b * c)"),
          "spelling, spacing and redundant parentheses do not change the canonical form")
    check(canon("a - (b - c)") != canon("a - b - c") and canon("(a+b)*c") != canon("a+b*c"),
          "parentheses that matter are kept")

    heading("Nothing is dropped: unsupported obligations are recorded")
    document = osets.demo()
    base = document["obligations"][0]

    def extra(oid, **fields):
        document["obligations"].append({**copy.deepcopy(base), "obligation_id": oid, **fields})
    extra("U-1", kind="permission", statement="ensures true")
    extra("U-2", kind="resource-bound", statement="ensures true")
    extra("U-3", kind="acceptance-criterion", statement="ensures true")
    extra("U-4", kind="postcondition", statement="result.length > 0")
    extra("U-5", kind="postcondition", statement="requires x > 0")
    extra("U-6", kind="postcondition", statement="ensures f(x) > 0")
    extra("U-7", kind="postcondition", statement="ensures a[0] > 0")
    extra("U-8", kind="postcondition", statement='ensures s == "x"')
    extra("U-9", kind="postcondition", statement="ensures")
    extra("U-10", kind="postcondition", statement="ensures x >")
    extra("U-11", kind="postcondition", statement="ensures x > 0 )")
    extra("U-12", kind="postcondition", statement="ensures a ? b : c")
    extra("U-13", kind="postcondition", statement="ensures x = 1")
    extra("U-14", kind="postcondition", statement="ensures 1.5 > x")
    extra("U-15", kind="postcondition", statement="ensureszero > 0")
    ir = adapter.lower(document, BACKEND)
    unsupported = {u["obligation_id"]: u for u in ir["unsupported"]}
    check(set(unsupported) == {f"U-{i}" for i in range(1, 16)}, "every unlowerable obligation is recorded",
          f"    {sorted(unsupported)}")
    check(all(u["reason"] for u in unsupported.values()), "each with a reason")
    ids = [u["obligation_id"] for u in ir["units"]] + [u["obligation_id"] for u in ir["unsupported"]]
    expected_ids = sorted(o["obligation_id"] for o in document["obligations"])
    check(sorted(ids) == expected_ids and len(ids) == len(set(ids)),
          "every obligation is in units or unsupported, exactly once")
    check(not validate.validate(ir, validate.load_schema(ROOT / "docs" / "specifications" / "verification-ir-schema.json")),
          "the IR with unsupported obligations is valid")
    check(all(u["obligation_digest"] == oset.obligation_digests(document)[u["obligation_id"]]
              for u in ir["unsupported"]), "unsupported entries carry the digest too")
    check(adapter.lower(document, BACKEND) == ir, "lowering with unsupported obligations is deterministic")
    check(all(u[f] == {o["obligation_id"]: o for o in oset.canonical_form(document)["obligations"]}[u["obligation_id"]][f]
              for u in ir["unsupported"] for f in ("kind", "statement", "required_evidence_kind", "affected_components")),
          "unsupported entries carry the same metadata, so they too can become 'not-run' report entries")
    big = copy.deepcopy(osets.demo())
    big["obligations"][0]["statement"] = "ensures x > " + "0" * 10 + "9" * 5000
    huge = adapter.lower(big, BACKEND)
    check(huge["units"][0]["clause"]["expression"] == "x > " + "9" * 5000 and not huge["unsupported"],
          "a 5000-digit literal is normalised textually (no int() length limit); leading zeros dropped")
    check(adapter.canonical_expression("x == 0000") == "x == 0", "000 normalises to 0")


def check_cli():
    heading("Command line")
    check(run_cli("lower", str(DEMO)).returncode == 2, "--backend-version is required (exit 2)")
    check(run_cli("lower", str(ROOT / "docs" / "specifications" / "report-schema.json"),
                  "--backend-version", BACKEND).returncode == 1, "an invalid Obligation Set exits 1")
    run = run_cli("lower", str(DEMO), "--backend-version", BACKEND)
    check(run.returncode == 0 and json.loads(run.stdout)["backend"]["version"] == BACKEND, "lower exits 0 and prints the IR")


def main():
    if adapter is None:
        check(False, "adapter not implemented: tools/ts_adapter.py does not exist yet",
              "    This failure is expected before the adapter is written (core-v0.1.md, task 3).")
    else:
        check_determinism()
        check_key()
        check_lowering()
        check_cli()
    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
