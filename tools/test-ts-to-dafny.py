#!/usr/bin/env python3
"""Tests for the TypeScript -> Dafny translator (tools/ts_to_dafny.py).

  * the committed proof workspace of the ledger is exactly what the translator produces from
    ledger.ts and ledger.translation.json (so editing the code without regenerating is caught);
  * the translation is deterministic and the test hook never reaches the proof model;
  * everything outside the subset is refused with an error, never translated approximately;
  * differential: random integer expressions give the same value read as TypeScript and as the
    Dafny text the translator emits, so precedence, parentheses and negated loop conditions hold.

That the translated code satisfies its obligations is the job of tools/test-ledger.py, which
runs Dafny on the generated workspace.

Run with `make test`, or `python tools/test-ts-to-dafny.py`.
"""
import copy
import json
import random
import re
import sys
from pathlib import Path

import ts_to_dafny as t2d

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "specifications" / "examples" / "ledger"
FAILURES = []


def check(ok, label, detail=""):
    print(f"[{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append((label, detail))
    return ok


def heading(title):
    print(f"\n== {title}")


SPEC_ONE = {"functions": {"f": {"dafny_name": "F", "nat": ["a", "b"], "returns": [["result", "int"]]}}}


def translated(ts, spec=SPEC_ONE):
    return t2d.translate(ts, spec)


def refused(ts, spec=SPEC_ONE):
    try:
        t2d.translate(ts, spec)
    except t2d.TranslationError as e:
        return str(e)
    return None


def check_ledger():
    heading("The ledger workspace is generated from the code")
    ts = (LEDGER / "ledger.ts").read_text(encoding="utf-8")
    spec = json.loads((LEDGER / "ledger.translation.json").read_text(encoding="utf-8"))
    generated = t2d.translate(ts, spec)
    committed = (LEDGER / "ledger.workspace.dfy").read_text(encoding="utf-8")
    check(generated == committed, "ledger.workspace.dfy equals the translation of ledger.ts",
          "    regenerate with: python tools/ts_to_dafny.py docs/specifications/examples/ledger/ledger.ts "
          "--spec docs/specifications/examples/ledger/ledger.translation.json "
          "-o docs/specifications/examples/ledger/ledger.workspace.dfy")
    check(t2d.translate(ts, spec) == generated, "translating twice gives the same bytes")
    check("observe" not in generated, "the test hook of accrue has no counterpart in the proof model")
    for fn in re.findall(r"export function (\w+)", ts):
        check(f"// ---- " in generated and spec["functions"][fn]["dafny_name"] in generated,
              f"{fn} is translated as {spec['functions'][fn]['dafny_name']}")
    edited = ts.replace("return balance + amount;", "return balance + amount + 1;")
    check(t2d.translate(edited, spec) != committed, "editing the TypeScript changes the generated workspace")
    # a marker for every obligation of the set survives the translation
    ids = {o["obligation_id"] for o in json.loads((LEDGER / "ledger.obligations.json").read_text(encoding="utf-8"))["obligations"]}
    found = {i for line in re.findall(r"selyth:obligation ([^\n]*)", generated) for i in line.split()}
    check(found == ids, "the generated markers carry exactly the obligations of the set",
          f"    missing {sorted(ids - found)}, extra {sorted(found - ids)}")


def check_shapes():
    heading("Translation shapes")
    out = translated("export function f(a: number, b: number): number { if (a < b) return a; return b; }")
    check("method F(a: nat, b: nat) returns (result: int)" in out and "result := a;\n    return;" in out
          and out.rstrip().endswith("result := b;\n}"), "early return, then fall-through result", out)
    out = translated("export function f(a: number, b: number): number { return a - b; }")
    check("result := a - b;" in out and "return;" not in out, "a final return needs no `return;`")
    out = translated("export function f(a: number, b: number): number { return a - (b - a); }")
    check("a - (b - a)" in out, "right operand of `-` keeps its parentheses")
    out = translated("export function f(a: number, b: number): number { return (a + b) * a; }")
    check("(a + b) * a" in out, "a lower-precedence operand of `*` keeps its parentheses")
    out = translated("export function f(a: number, b: number): number { return Math.floor((a * b) / 3); }")
    check("result := a * b / 3;" in out, "Math.floor((a * b) / 3) is `a * b / 3` on non-negative operands", out)
    out = translated("export function f(a: number, b: number): number { return a % b + 1; }")
    check("a % b + 1" in out, "% on non-negative operands")
    out = translated("export function f(_a: number, b: number): number { return _a + b; }",
                     {"functions": {"f": {"dafny_name": "F", "nat": ["_a"], "returns": [["result", "int"]]}}})
    check("method F(a: nat, b: int)" in out and "a + b" in out, "a leading underscore is dropped for Dafny")
    out = translated("export function f(a: number, b: number): number { if (a === b) { return 1; } else { return 2; } }")
    check("if a == b {" in out and "} else {" in out, "=== is ==, else is kept")
    out = translated("export function f(a: number, b: number): number { let t = a; t -= b; t += 2; t++; return t; }",
                     {"functions": {"f": {"dafny_name": "F", "returns": [["result", "int"]]}}})
    check("var t := a;" in out and "t := t - b;" in out and "t := t + 2;" in out and "t := t + 1;" in out,
          "let, -=, +=, ++", out)
    out = translated("export function f(a: number, b: number): number { let t = 0; t -= (a - b); return t; }",
                     {"functions": {"f": {"dafny_name": "F", "returns": [["result", "int"]]}}})
    check("t := t - (a - b);" in out, "the right operand of -= keeps its parentheses", out)
    out = translated("export function f(a: number, b: number): number { if ((a < b) === (b < a)) return 1; return 0; }")
    check("if (a < b) == (b < a) {" in out, "a comparison inside a comparison is parenthesised (Dafny does not chain them)", out)
    out = translated("export function f(a: number, b: number): number { if (a < b && b < a || a === b) return 1; return 0; }")
    check("if (a < b && b < a) || a == b {" in out, "&& and || are never mixed without parentheses (Dafny refuses it)", out)
    two = {"functions": {"f": {"dafny_name": "F", "nat": ["a", "b"], "returns": [["x", "nat"], ["y", "nat"]]}}}
    out = translated("export function f(a: number, b: number): [number, number] { return [b, a]; }", two)
    check("x, y := b, a;" in out, "tuple results are a parallel assignment", out)
    out = translated("export function f(a: number, b: number): [number, number] { let x = a; return [x, b]; }", two)
    check("x := a;" in out and "y := b;" in out and "x, y" not in out,
          "a local named like a result is that result, returned as itself", out)


def check_loops():
    heading("Loops")
    spec = {"functions": {"f": {"dafny_name": "F", "nat": ["n"], "returns": [["t", "nat"]],
                                "loop": {"invariant": "0 <= i <= n", "obligations": ["X-1"]}}}}
    base = ("export function f(n: number, observe?: (i: number) => void): number {{ let t = 0; let i = 0; "
            "while (true) {{ observe?.(i); if ({cond}) break; t += 2; i++; }} return t; }}")
    out = translated(base.format(cond="i >= n"), spec)
    check("while i < n\n    invariant 0 <= i <= n\n    // selyth:obligation X-1\n  {" in out,
          "while (true) { if (i >= n) break; ... } becomes `while i < n` with its invariant and marker", out)
    check("observe" not in out, "the optional hook parameter and its calls are dropped")
    out = translated(base.format(cond="i >= n && t > 3"), spec)
    check("while !(i >= n && t > 3)" in out, "a compound exit condition is negated as a whole", out)
    out = translated(base.format(cond="!(i < n)"), spec)
    check("while i < n" in out, "a negated exit condition is not double negated", out)
    out = translated("export function f(n: number): number { let t = 0; let i = 0; while (i < n) { t += 2; i++; } return t; }", spec)
    check("while i < n\n" in out, "a plain while loop is accepted")
    check(refused("export function f(n: number): number { let t = 0; while (t < n) { t += 1; } return t; }",
                  {"functions": {"f": {"dafny_name": "F", "nat": ["n"], "returns": [["t", "nat"]]}}}) is not None,
          "a loop without an invariant in the specification is refused")
    check(refused("export function f(n: number): number { let t = 0; while (true) { t += 1; if (t > n) break; } return t; }",
                  spec) is not None, "`while (true)` that does not begin with its exit test is refused")
    check(refused("export function f(n: number): number { let t = 0; while (t < n) { t += 1; } "
                  "while (t < 9) { t += 1; } return t; }", spec) is not None, "two loops in one function are refused")
    check(refused("export function f(n: number): number { break; return 0; }", spec) is not None,
          "a `break` outside the loop-exit form is refused")


def check_refusals():
    heading("Everything outside the subset is refused")
    cases = {
        "a string": "export function f(a: number, b: number): number { return \"x\"; }",
        "a template": "export function f(a: number, b: number): number { return `x`; }",
        "a non-integer literal": "export function f(a: number, b: number): number { return 1.5; }",
        "a non-exported function": "function f(a: number, b: number): number { return a; }",
        "a class": "export class C {}",
        "a string parameter": "export function f(a: string, b: number): number { return b; }",
        "a boolean result": "export function f(a: number, b: number): boolean { return true; }",
        "an optional number parameter": "export function f(a: number, b?: number): number { return a; }",
        "a required function parameter": "export function f(a: number, cb: (x: number) => void): number { return a; }",
        "a bare division": "export function f(a: number, b: number): number { return a / b; }",
        "Math.round": "export function f(a: number, b: number): number { return Math.round(a); }",
        "Math.floor of a non-division": "export function f(a: number, b: number): number { return Math.floor(a); }",
        "loose equality": "export function f(a: number, b: number): number { if (a == b) return 1; return 0; }",
        "an unknown name": "export function f(a: number, b: number): number { return c; }",
        "a call": "export function f(a: number, b: number): number { return g(a); }",
        "a ternary": "export function f(a: number, b: number): number { return a ? 1 : 2; }",
        "an array literal result of the wrong arity": "export function f(a: number, b: number): number { return [a, b]; }",
        "a signed division": "export function f(a: number, b: number): number { return Math.floor(a / b) - 1 + (a - b) % 3; }",
        "a subtraction under a division": "export function f(a: number, b: number): number { return Math.floor((a - b) / 2); }",
        "a shadowed parameter": "export function f(a: number, b: number): number { let a = 1; return a; }",
        "an assignment to an undeclared name": "export function f(a: number, b: number): number { z = 1; return a; }",
        "a for loop": "export function f(a: number, b: number): number { for (;;) {} }",
        "a switch": "export function f(a: number, b: number): number { switch (a) {} }",
    }
    for label, ts in cases.items():
        message = refused(ts)
        check(message is not None, f"{label} is refused", ts)
    check("non-negative" in (refused(cases["a subtraction under a division"]) or ""),
          "the signed-division refusal says why")
    # a divisor or dividend that is not declared `nat` is refused, even though it is a plain name
    ts = "export function f(a: number, b: number): number { return Math.floor(a / b); }"
    check(refused(ts, {"functions": {"f": {"dafny_name": "F", "nat": ["a"], "returns": [["result", "int"]]}}}) is not None,
          "a division whose divisor is not declared nat is refused")
    check(refused(ts) is None, "the same division is accepted once both operands are declared nat")
    # a local that goes negative is not non-negative
    ts = "export function f(a: number, b: number): number { let t = a; t -= b; return Math.floor(t / 2); }"
    check(refused(ts) is not None, "a local decremented by a name is not assumed non-negative")
    ts = "export function f(a: number, b: number): number { let t = a; t += b; return Math.floor(t / 2); }"
    check(refused(ts) is None, "a local only ever increased by non-negative values is")

    heading("The specification and the code must agree")
    ts = "export function f(a: number, b: number): number { return a; }"
    check(refused(ts, {"functions": {}}) is not None, "a function missing from the specification is refused")
    check(refused(ts, {"functions": {**SPEC_ONE["functions"], "g": {"dafny_name": "G", "returns": [["r", "int"]]}}})
          is not None, "a specification entry without a function is refused")
    check(refused(ts, {"functions": {"f": {"dafny_name": "F", "returns": [["r", "int"], ["s", "int"]]}}}) is not None,
          "a different number of results is refused")
    check(refused(ts, {"functions": {"f": {"dafny_name": "F", "nat": ["zz"], "returns": [["r", "int"]]}}}) is not None,
          "`nat` naming an unknown parameter is refused")
    check(refused(ts, {"functions": {"f": {"dafny_name": "F", "returns": [["a", "int"]]}}}) is not None,
          "a result named like a parameter is refused")
    check(refused(ts + ts) is not None, "a function declared twice is refused")


# ---- differential: the expression read as TypeScript and as emitted Dafny ---------------

def floor_to_python(src):
    """Math.floor(a / b) -> ((a) // (b)), matching parentheses, innermost first."""
    while "Math.floor(" in src:
        start = src.rindex("Math.floor(")
        i, depth, slash = start + len("Math.floor("), 1, None
        while depth:
            c = src[i]
            depth += c == "("
            depth -= c == ")"
            if c == "/" and depth == 1:
                slash = i
            i += 1
        inner_a, inner_b = src[start + len("Math.floor("):slash], src[slash + 1:i - 1]
        src = f"{src[:start]}(({inner_a}) // ({inner_b})){src[i:]}"
    return src


def ts_eval(src, env):
    """TypeScript semantics for the non-negative integer fragment, by a separate route: the source
    text rewritten to Python, whose operators agree with JavaScript's here."""
    text = floor_to_python(src)
    text = text.replace("===", "==").replace("!==", "!=").replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    return eval(text, {"__builtins__": {}}, dict(env))


def dafny_eval(src, env):
    text = src.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    text = re.sub(r"(?<![/])/(?![/])", "//", text)
    return eval(text, {"__builtins__": {}}, dict(env))


def random_arith(rng, depth, vars_):
    if depth == 0 or rng.random() < 0.25:
        return rng.choice(vars_ + [str(rng.randint(0, 9))])
    op = rng.choice(["+", "*", "-", "%", "/", "+", "*"])
    a, b = random_arith(rng, depth - 1, vars_), random_arith(rng, depth - 1, vars_)
    if op == "/":
        return f"Math.floor(({a}) / ({b} + 1))"
    if op == "%":
        return f"({a}) % ({b} + 1)"
    if op == "-":
        return f"(({a}) + 100 - ({b}))"          # stays non-negative by construction, still a '-'
    return f"({a}) {op} ({b})" if rng.random() < 0.5 else f"{a} {op} {b}".replace(" ", " ")


def random_bool(rng, depth, vars_):
    if depth == 0 or rng.random() < 0.3:
        op = rng.choice(["<", "<=", ">", ">=", "===", "!=="])
        return f"({random_arith(rng, 2, vars_)}) {op} ({random_arith(rng, 2, vars_)})"
    kind = rng.choice(["&&", "||", "!"])
    if kind == "!":
        return f"!({random_bool(rng, depth - 1, vars_)})"
    return f"({random_bool(rng, depth - 1, vars_)}) {kind} ({random_bool(rng, depth - 1, vars_)})"


def check_differential():
    heading("Differential: TypeScript reading against the emitted Dafny text")
    rng = random.Random(20261013)
    spec = {"functions": {"f": {"dafny_name": "F", "nat": ["a", "b"], "returns": [["result", "int"]]}}}
    wrong, refusals, translated_ok, tried = [], 0, 0, 0
    for _ in range(600):
        e = random_arith(rng, 3, ["a", "b"])
        try:
            out = t2d.translate(f"export function f(a: number, b: number): number {{ return {e}; }}", spec)
        except t2d.TranslationError as err:
            if "-" in e and "non-negative" in str(err):          # the sign inference is conservative: fine
                refusals += 1
            else:
                wrong.append((e, f"refused: {err}"))
            continue
        translated_ok += 1
        text = re.search(r"result := (.*);", out).group(1)
        for a, b in [(0, 0), (1, 2), (7, 3), (12, 5), (0, 9), (31, 31)]:
            tried += 1
            if ts_eval(e, {"a": a, "b": b}) != dafny_eval(text, {"a": a, "b": b}):
                wrong.append((e, text, a, b))
                break
    check(not wrong, f"random arithmetic expressions agree at 6 points each ({translated_ok} translated, "
                     f"{tried} evaluations)", str(wrong[:3]))
    check(translated_ok >= 400 and refusals > 0,
          f"most expressions are translated ({translated_ok}) and a signed division is refused, not guessed "
          f"({refusals} refusals)")
    wrong, translated_ok = [], 0
    for _ in range(600):
        e = random_bool(rng, 3, ["a", "b"])
        ts = (f"export function f(a: number, b: number): number {{ let t = 0; let i = 0; "
              f"while (true) {{ if ({e}) break; t += 1; i++; }} return t; }}")
        sp = {"functions": {"f": {"dafny_name": "F", "nat": ["a", "b"], "returns": [["t", "nat"]],
                                  "loop": {"invariant": "true"}}}}
        try:
            out = t2d.translate(ts, sp)
        except t2d.TranslationError as err:
            if not ("-" in e and "non-negative" in str(err)):
                wrong.append((e, f"refused: {err}"))
            continue
        translated_ok += 1
        head = re.search(r"while (.*)\n", out).group(1)
        for a, b in [(0, 0), (1, 2), (7, 3), (12, 5), (0, 9), (31, 31)]:
            if bool(ts_eval(e, {"a": a, "b": b})) == bool(dafny_eval(head, {"a": a, "b": b})):
                wrong.append((e, head, a, b))                    # the loop head must be the exit test negated
                break
    check(not wrong, f"random exit conditions: the emitted loop head is always the exact negation "
                     f"({translated_ok} translated)", str(wrong[:3]))


def main():
    check_ledger()
    check_shapes()
    check_loops()
    check_refusals()
    check_differential()
    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
