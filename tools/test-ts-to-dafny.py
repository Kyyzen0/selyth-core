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
    out = translated("export function f(a: number, b: number): number { while (b !== 0) { const t = b; b = a % b; a = t; } return a; }",
                     {"functions": {"f": {"dafny_name": "F", "nat": ["a", "b"], "returns": [["result", "nat"]],
                                          "loop": {"invariant": "true"}}}})
    check("var a := a;" in out and "var b := b;" in out and "b := a % b;" in out and "a := t;" in out,
          "a reassigned parameter is shadowed by a mutable local (Dafny parameters are immutable); "
          "the ensures clauses still see the original parameter values", out)
    out = translated("export function f(a: number, b: number): number { return -a + b; }")
    check("result := -(a as int) + b;" in out, "unary minus converts a nat to int first (Dafny would type -a as a nat)", out)
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


def check_constructs():
    heading("Widened subset: ternary, Math.abs, counted for-loop")
    spec = {"functions": {"f": {"dafny_name": "F", "nat": ["a", "b"], "returns": [["result", "int"]]}}}
    out = translated("export function f(a: number, b: number): number { return a > b ? a : b; }", spec)
    check("result := if a > b then a else b;" in out, "a ternary becomes a Dafny if-expression", out)
    out = translated("export function f(a: number, b: number): number { return (a > b ? a : b) * 2 - 1; }", spec)
    check("result := (if a > b then a else b) * 2 - 1;" in out,
          "a ternary under arithmetic is parenthesised", out)
    out = translated("export function f(a: number, b: number): number { return Math.abs(a - b); }", spec)
    check("function Abs(x: int): int" in out and "result := Abs(a - b);" in out,
          "Math.abs becomes the Abs helper (prepended once) on int", out)
    out = translated("export function f(a: number, b: number): number { return Math.abs(a - b) + Math.abs(b - a); }", spec)
    check(out.count("function Abs") == 1, "the Abs helper is emitted once however often it is used", out)
    fspec = {"functions": {"f": {"dafny_name": "F", "nat": ["n"], "returns": [["t", "nat"]],
                                 "loop": {"invariant": "0 <= i <= n", "obligations": ["X-1"]}}}}
    out = translated("export function f(n: number): number { let t = 0; for (let i = 0; i < n; i++) { t += i; } return t; }",
                     fspec)
    check("var i := 0;\n  while i < n\n    invariant 0 <= i <= n\n    // selyth:obligation X-1\n  {\n    t := t + i;\n    i := i + 1;\n  }"
          in out, "a counted for-loop desugars to while + explicit counter, with the sidecar's invariant and marker", out)
    out = translated("export function f(n: number): number { let t = 0; for (let i = 0; i <= n; i++) { t += 1; } return t; }",
                     fspec)
    check("while i <= n" in out, "`i <= e` is accepted as the for-loop condition", out)
    out = translated("export function f(a: number, b: number): number { return Math.min(a, b); }", spec)
    check("function Min(x: int, y: int): int" in out and "result := Min(a, b);" in out,
          "Math.min becomes the prepended Min helper on int", out)
    out = translated("export function f(a: number, b: number): number { return Math.max(a, b) - Math.min(a, b); }", spec)
    check("function Max" in out and "function Min" in out and "result := Max(a, b) - Min(a, b);" in out,
          "Math.max/Math.min map to Max/Min", out)
    out = translated("export function f(a: number, b: number): number { return Math.max(Math.min(a, b), 0) + Math.min(a, 5); }", spec)
    check(out.count("function Min") == 1 and out.count("function Max") == 1
          and "result := Max(Min(a, b), 0) + Min(a, 5);" in out,
          "nested min/max: each helper is prepended once", out)
    out = translated("export function f(a: number, b: number): number { let m = Math.min(a, b); return Math.floor(m / 2); }", spec)
    check("result := m / 2;" in out, "Min(a, b) counts as provably non-negative for a following division", out)


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
        "a ternary without else": "export function f(a: number, b: number): number { return a ? 1; }",
        "Math.min with one argument": "export function f(a: number, b: number): number { return Math.min(a); }",
        "Math.min with three arguments": "export function f(a: number, b: number): number { return Math.min(a, b, 0); }",
        "a for loop without a counter declaration": "export function f(n: number): number { for (i = 0; i < n; i++) {} return 0; }",
        "a for loop with a decrementing counter": "export function f(n: number): number { for (let i = 0; i < n; i--) {} return 0; }",
        "a for loop with a non-literal initialiser": "export function f(n: number): number { for (let i = n; i < 10; i++) {} return 0; }",
        "a for loop with a downward condition": "export function f(n: number): number { for (let i = 0; i > n; i++) {} return 0; }",
        "an array literal result of the wrong arity": "export function f(a: number, b: number): number { return [a, b]; }",
        "a signed division": "export function f(a: number, b: number): number { return Math.floor(a / b) - 1 + (a - b) % 3; }",
        "a subtraction under a division": "export function f(a: number, b: number): number { return Math.floor((a - b) / 2); }",
        "a shadowed parameter": "export function f(a: number, b: number): number { let a = 1; return a; }",
        "an assignment to an undeclared name": "export function f(a: number, b: number): number { z = 1; return a; }",
        "a hook argument with a side effect": "export function f(a: number, observe?: (x: number) => void): number { let x = a; observe?.(x++); return x; }",
        "a hook argument that calls": "export function f(a: number, observe?: (x: number) => void): number { observe?.(g(a)); return a; }",
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
    text = text.replace("Math.min(", "min(").replace("Math.max(", "max(")
    text = text.replace("===", "==").replace("!==", "!=").replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    return eval(text, {"__builtins__": {}, "min": min, "max": max}, dict(env))


def dafny_eval(src, env):
    text = src.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    text = re.sub(r"(?<![/])/(?![/])", "//", text)
    return eval(text, {"__builtins__": {}, "Min": min, "Max": max}, dict(env))


def random_arith(rng, depth, vars_):
    if depth == 0 or rng.random() < 0.25:
        return rng.choice(vars_ + [str(rng.randint(0, 9))])
    if rng.random() < 0.15:
        a, b = random_arith(rng, depth - 1, vars_), random_arith(rng, depth - 1, vars_)
        m = rng.choice(["Math.min", "Math.max"])
        return f"{m}(({a}), ({b}))"               # Min/Max are exact on integers
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


def _split_ternary_ts(src):
    depth, q, c = 0, None, None
    for i, ch in enumerate(src):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0:
            if ch == "?" and q is None:
                q = i
            elif ch == ":" and q is not None and c is None:
                c = i
    if q is None or c is None:
        return None
    return src[:q], src[q + 1:c], src[c + 1:]


def _ternary_ts_to_python(src):
    s = src.strip()
    while s.startswith("(") and s.endswith(")") and _wraps_whole(s):
        s = s[1:-1].strip()
    parts = _split_ternary_ts(s)
    if parts is None:
        return s
    c, a, b = parts
    return f"({_ternary_ts_to_python(a)}) if ({_ternary_ts_to_python(c)}) else ({_ternary_ts_to_python(b)})"


def _wraps_whole(s):
    depth = 0
    for i, ch in enumerate(s):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
            if depth == 0 and i < len(s) - 1:
                return False
    return True


def _split_dafny_if(src):
    s = src.strip()
    while s.startswith("(") and s.endswith(")") and _wraps_whole(s):
        s = s[1:-1].strip()
    if not s.startswith("if "):
        return None
    depth, then, els = 0, None, None
    i = 3
    while i < len(s):
        if s[i] in "([":
            depth += 1
        elif s[i] in ")]":
            depth -= 1
        elif depth == 0:
            if then is None and s.startswith(" then ", i):
                then = i
                i += 6
                continue
            if els is None and s.startswith(" else ", i):
                els = i
                i += 6
                continue
        i += 1
    if then is None or els is None:
        return None
    return s[3:then].strip(), s[then + 6:els], s[els + 6:]


def _ternary_dafny_to_python(src):
    parts = _split_dafny_if(src)
    if parts is None:
        return src
    c, a, b = parts
    return f"({_ternary_dafny_to_python(a)}) if ({_ternary_dafny_to_python(c)}) else ({_ternary_dafny_to_python(b)})"


def random_construct(rng, vars_):
    e = random_arith(rng, 2, vars_)
    kind = rng.choice(["plain", "abs", "abs", "ternary", "ternary", "nested", "minmax", "minmax"])
    if kind == "abs":
        return f"Math.abs(({e}) - ({random_arith(rng, 2, vars_)}))"
    if kind == "minmax":
        m = rng.choice(["min", "max"])
        return f"Math.{m}(({e}), ({random_arith(rng, 2, vars_)}))"
    if kind == "ternary":
        return f"({random_bool(rng, 2, vars_)}) ? ({e}) : ({random_arith(rng, 2, vars_)})"
    if kind == "nested":
        c = random_bool(rng, 2, vars_)
        return f"({c}) ? Math.abs(({e}) - 3) : (({c}) ? 0 : ({random_arith(rng, 1, vars_)}))"
    return e


def check_construct_differential():
    heading("Differential: ternary and Math.abs agree between the TypeScript and Dafny readings")
    rng = random.Random(20261121)
    spec = {"functions": {"f": {"dafny_name": "F", "nat": ["a", "b"], "returns": [["result", "int"]]}}}
    wrong, translated_ok, tried, refusals = [], 0, 0, 0
    for _ in range(400):
        e = random_construct(rng, ["a", "b"])
        try:
            out = t2d.translate(f"export function f(a: number, b: number): number {{ return {e}; }}", spec)
        except t2d.TranslationError as err:
            if "non-negative" in str(err):                  # the sign inference is conservative: fine
                refusals += 1
            else:
                wrong.append((e, f"refused: {err}"))
            continue
        translated_ok += 1
        text = re.search(r"result := (.*);", out).group(1)
        for a, b in [(0, 0), (1, 2), (7, 3), (12, 5), (0, 9), (31, 4)]:
            tried += 1
            ts_text = _ternary_ts_to_python(floor_to_python(e).replace("Math.abs(", "abs(")
                                            .replace("Math.min(", "min(").replace("Math.max(", "max("))
            ts_text = ts_text.replace("===", "==").replace("!==", "!=").replace("&&", " and ").replace("||", " or ")
            ts_text = re.sub(r"!(?!=)", " not ", ts_text)
            dafny_text = text.replace("Abs(", "abs(").replace("Min(", "min(").replace("Max(", "max(")
            dafny_text = dafny_text.replace("&&", " and ").replace("||", " or ")
            dafny_text = re.sub(r"!(?!=)", " not ", dafny_text)
            dafny_text = re.sub(r"(?<![/])/(?![/])", "//", _ternary_dafny_to_python(dafny_text))
            env = {"a": a, "b": b, "abs": abs, "min": min, "max": max}
            if eval(ts_text, {"__builtins__": {}}, env) != eval(dafny_text, {"__builtins__": {}}, dict(env)):
                wrong.append((e, text, a, b))
                break
    check(not wrong, f"random ternary/abs expressions agree at 6 points each ({translated_ok} translated, "
                     f"{tried} evaluations, {refusals} conservative refusals)", str(wrong[:3]))


def check_switch():
    heading("Widened subset: switch on integer literals, desugared to if/else chains")
    spec = {"functions": {"f": {"dafny_name": "F", "nat": ["a", "b"], "returns": [["result", "int"]]}}}
    out = translated("export function f(a: number, b: number): number { switch (a) { case 0: return 10; case -1: result = 5; break; default: return 30; } }", spec)
    check("if a == 0 {" in out and "if a == -1 {" in out and "} else {" in out and "result := 30;" in out,
          "switch desugars to a nested if/else chain on `===` labels (negative label, break dropped)", out)
    out = translated("export function f(a: number, b: number): number { let r = 0; switch (a + b) { case 2: r = 1; break; } return r; }", spec)
    check("if a + b == 2 {" in out and "r := 1;" in out and "result := r;" in out,
          "the subject may be an expression; a case ending in break; falls out of the chain", out)
    out = translated("export function f(a: number, b: number): number { switch (a) { case 1: return 7; } return 0; }", spec)
    check("if a == 1 {" in out and "result := 0;" in out,
          "a switch without default leaves the following statements in place", out)
    out = translated("export function f(a: number, b: number): number { if (a < b) return 1; else if (a === b) return 2; else return 3; }", spec)
    check(out.count("} else {") == 2 and "if a == b {" in out,
          "an else-if chain nests as expected", out)
    cases = {
        "fallthrough": "export function f(a: number, b: number): number { switch (a) { case 0: result = 1; case 1: return 2; } }",
        "duplicate label": "export function f(a: number, b: number): number { switch (a) { case 0: break; case 0: break; } }",
        "non-literal label": "export function f(a: number, b: number): number { switch (a) { case a: break; } }",
        "default not last": "export function f(a: number, b: number): number { switch (a) { default: break; case 0: break; } }",
        "empty case": "export function f(a: number, b: number): number { switch (a) { case 0: } }",
        "empty switch": "export function f(a: number, b: number): number { switch (a) {} return 0; }",
        "case with a call in the label": "export function f(a: number, b: number): number { switch (a) { case g(1): break; } }",
    }
    for label, ts in cases.items():
        check(refused(ts) is not None, f"switch: {label} is refused", ts)


def check_arrays_calls_lemmas():
    heading("Widened subset: read-only arrays, pure internal calls, selyth:lemma comments")
    spec = {"functions": {
        "double": {"dafny_name": "Double", "nat": ["x"], "returns": [["result", "int"]], "pure": True},
        "f": {"dafny_name": "F", "nat": ["a"], "returns": [["result", "int"]]}}}
    out = translated("export function double(x: number): number { return x + x; }\n"
                     "export function f(a: number): number { const t: number[] = [10, 20, 30];"
                     " return t[a] + double(1) + t.length; }", spec)
    check("function Double(x: nat): int" in out and "x + x" in out,
          "a pure function (single return, \"pure\": true) becomes a Dafny function", out)
    check("var t: seq<int> := [10, 20, 30];" in out, "a const array literal becomes a read-only seq<int> (a `var`: Dafny has no local const)", out)
    check("result := t[a] + Double(1) + |t|;" in out,
          "a[i] indexes the seq, a.length is |a|, a pure call is a function call", out)
    out = translated("export function double(x: number): number { return x + x; }\n"
                     "export function f(a: number): number { const t: number[] = [1, 2]; return t[1]; }", spec)
    check("result := t[1];" in out and "t[1]" in out, "a literal in-range index translates", out)
    out = translated("// selyth:lemma lemma DoubleNonneg(x: nat) ensures Double(x) >= 0 {}\n"
                     "export function double(x: number): number { return x + x; }\n"
                     "export function f(a: number): number { return double(a); }", spec)
    check(out.startswith("lemma DoubleNonneg(x: nat) ensures Double(x) >= 0 {}"),
          "a selyth:lemma comment lands in the preamble as a (Dafny-proven) lemma", out)
    cases = {
        "index out of range (literal)": "export function f(a: number): number { const t: number[] = [1, 2]; return t[5]; }",
        "negative literal index": "export function f(a: number): number { const t: number[] = [1, 2]; return t[-1]; }",
        "element assignment": "export function f(a: number): number { const t: number[] = [1, 2]; t[0] = 9; return t[0]; }",
        "let array": "export function f(a: number): number { let t: number[] = [1]; return t[0]; }",
        "array reassignment": "export function f(a: number): number { const t: number[] = [1]; t = [2]; return t[0]; }",
        "empty array": "export function f(a: number): number { const t: number[] = []; return 0; }",
        "array parameter": "export function f(t: number[]): number { return 0; }",
        "index of a non-array": "export function f(a: number): number { return a[0]; }",
        "call to an impure function": "export function g(a: number): number { let t = a; while (t > 0) { t -= 1; } return t; }\nexport function f(a: number): number { return g(a); }",
        "call to an unknown name": "export function f(a: number): number { return nope(a); }",
        "pure function with a loop": None,   # built below (needs spec)
        "lemma with assume": "// selyth:lemma lemma L() ensures true { assume true; }\nexport function f(a: number): number { return a; }",
        "lemma not starting with lemma": "// selyth:lemma ensures true {}\nexport function f(a: number): number { return a; }",
        "duplicate lemma": "// selyth:lemma lemma L() ensures true {}\n// selyth:lemma lemma L() ensures true {}\nexport function f(a: number): number { return a; }",
    }
    for label, ts in cases.items():
        if ts is None:
            continue
        sp = {"functions": {"f": {"dafny_name": "F", "nat": ["a"], "returns": [["result", "int"]]}}}
        if "g(a)" in ts or "nope(a)" in ts:
            sp["functions"]["g"] = {"dafny_name": "G", "nat": ["a"], "returns": [["t", "int"]],
                                     "loop": {"invariant": "true"}}
        check(refused(ts, sp) is not None, f"{label} is refused", ts)
    # a "pure" spec entry on a looping function is refused
    sp = {"functions": {"g": {"dafny_name": "G", "nat": ["a"], "returns": [["t", "int"]], "pure": True,
                              "loop": {"invariant": "true"}}}}
    check(refused("export function g(a: number): number { let t = a; while (t > 0) { t -= 1; } return t; }", sp)
          is not None, "a pure entry on a looping function is refused")


def check_contract_comments():
    heading("Widened subset: selyth:invariant and selyth:caller comments carry the loop contract in the code")
    fn = {"dafny_name": "Sum", "nat": ["n"], "returns": [["t", "nat"]]}
    other = {"dafny_name": "F", "nat": ["a"], "returns": [["result", "int"]]}
    src_comment = ("export function sum(n: number): number {\n"
                   "  let t = 0;\n"
                   "  for (let i = 0; i < n; i++) {\n"
                   "    // selyth:invariant 0 <= i <= n\n"
                   "    t += i;\n"
                   "  }\n"
                   "  return t;\n"
                   "}\n"
                   "// selyth:caller method UseSum() { var t := Sum(5); }\n"
                   "export function f(a: number): number { return a; }\n")
    spec_comment = {"functions": {"sum": {**fn, "loop": {"obligations": ["T-2"]}}, "f": other}}
    out = translated(src_comment, spec_comment)
    check("invariant 0 <= i <= n" in out and "method UseSum() { var t := Sum(5); }" in out
          and "// selyth:obligation T-2" in out,
          "a selyth:invariant comment becomes the invariant clause, a selyth:caller comment the caller method",
          out)
    src_sidecar = ("export function sum(n: number): number {\n"
                   "  let t = 0;\n"
                   "  for (let i = 0; i < n; i++) { t += i; }\n"
                   "  return t;\n"
                   "}\n"
                   "export function f(a: number): number { return a; }\n")
    spec_sidecar = {"functions": {"sum": {**fn, "loop": {"invariant": "0 <= i <= n", "obligations": ["T-2"]},
                                          "callers": "method UseSum() { var t := Sum(5); }"}, "f": other}}
    check(translated(src_comment, spec_comment) == translated(src_sidecar, spec_sidecar),
          "comment-sourced invariant and callers produce the exact same workspace as the sidecar")
    cases = {
        "invariant without a loop": ("// selyth:invariant true\nexport function f(a: number): number { return a; }",
                                      {"functions": {"f": other}}),
        "two invariants in one function": ("export function g(a: number): number { let t = a; "
                                           "// selyth:invariant true\n while (t > 0) {\n // selyth:invariant t >= 0\n t -= 1; } return t; }",
                                           {"functions": {"g": {"dafny_name": "G", "nat": ["a"],
                                                                "returns": [["t", "int"]]}}}),
        "invariant before any function": ("// selyth:invariant true\nexport function f(a: number): number { return a; }",
                                           {"functions": {"f": other}}),
        "invariant conflicting with the sidecar": ("export function g(a: number): number { let t = a;\n"
                                                    "// selyth:invariant t >= 0\nwhile (t > 0) { t -= 1; } return t; }",
                                                    {"functions": {"g": {"dafny_name": "G", "nat": ["a"],
                                                                         "returns": [["t", "int"]],
                                                                         "loop": {"invariant": "true"}}}}),
        "caller before any function": ("// selyth:caller method U() { }\nexport function f(a: number): number { return a; }",
                                        {"functions": {"f": other}}),
        "caller not a method": ("export function f(a: number): number { return a; }\n// selyth:caller lemma L() {}",
                                {"functions": {"f": other}}),
        "caller with assume": ("export function f(a: number): number { return a; }\n// selyth:caller method U() { assume true; }",
                               {"functions": {"f": other}}),
        "caller conflicting with the sidecar": ("export function f(a: number): number { return a; }\n// selyth:caller method U() { }",
                                                 {"functions": {"f": {**other, "callers": "method V() { }"}}}),
        "caller on a pure function": ("export function d(x: number): number { return x + x; }\n"
                                      "// selyth:caller method U() { var r := D(1); }\n"
                                      "export function f(a: number): number { return d(a); }",
                                      {"functions": {"d": {"dafny_name": "D", "nat": ["x"],
                                                           "returns": [["result", "int"]], "pure": True},
                                                     "f": other}}),
    }
    for label, (ts, sp) in cases.items():
        check(refused(ts, sp) is not None, f"contract comments: {label} is refused", ts)

    heading("Widened subset: selyth:obligation / selyth:hint / selyth:preamble comments")
    loop_src = ("export function sum(n: number): number {\n"
                "  // selyth:obligation T-1\n"
                "  let t = 0;\n"
                "  for (let i = 0; i < n; i++) {\n"
                "    // selyth:obligation T-2 T-3\n"
                "    // selyth:invariant 0 <= i <= n\n"
                "    // selyth:hint assert t >= 0;\n"
                "    t += i;\n"
                "  }\n"
                "  return t;\n"
                "}\n"
                "// selyth:caller method UseSum() { var t := Sum(5); }\n"
                "export function f(a: number): number {\n"
                "  // selyth:obligation F-1\n"
                "  return a;\n"
                "}\n")
    pre = "// selyth:preamble lemma Helper(x: nat) ensures x >= 0 {}\n"
    spec_c = {"preamble": "lemma Helper(x: nat) ensures x >= 0 {}",
              "functions": {"sum": {"dafny_name": "Sum", "nat": ["n"], "returns": [["t", "nat"]]},
                            "f": {"dafny_name": "F", "nat": ["a"], "returns": [["result", "int"]]}}}
    out_comment = translated(pre + loop_src, {"functions": spec_c["functions"]})
    out_sidecar = translated(loop_src.replace("  // selyth:obligation T-1\n", "")
                             .replace("    // selyth:obligation T-2 T-3\n", "")
                             .replace("    // selyth:invariant 0 <= i <= n\n", "")
                             .replace("    // selyth:hint assert t >= 0;\n", "")
                             .replace("// selyth:caller method UseSum() { var t := Sum(5); }\n", ""),
                             {"preamble": "lemma Helper(x: nat) ensures x >= 0 {}",
                              "functions": {"sum": {"dafny_name": "Sum", "nat": ["n"], "returns": [["t", "nat"]],
                                                   "obligations": ["T-1"], "body_prefix": "assert t >= 0;",
                                                   "loop": {"invariant": "0 <= i <= n", "obligations": ["T-2", "T-3"]},
                                                   "callers": "method UseSum() { var t := Sum(5); }"},
                                          "f": {"dafny_name": "F", "nat": ["a"], "returns": [["result", "int"]],
                                                "obligations": ["F-1"]}}})
    check("// selyth:obligation T-1" in out_comment and "// selyth:obligation T-2 T-3" in out_comment
          and "// selyth:obligation F-1" in out_comment and "invariant 0 <= i <= n" in out_comment
          and "assert t >= 0;" in out_comment and "lemma Helper(x: nat) ensures x >= 0 {}" in out_comment
          and "method UseSum() { var t := Sum(5); }" in out_comment,
          "obligation markers (contract and loop), hint, preamble and caller all come from the comments",
          out_comment)
    check(out_comment == out_sidecar,
          "comment-sourced contract produces the exact same workspace as the sidecar")
    bad = {
        "obligation before any function": ("// selyth:obligation X-1\nexport function f(a: number): number { return a; }",
                                            {"functions": {"f": other}}),
        "obligation id twice": ("export function f(a: number): number {\n// selyth:obligation F-1\n"
                                "// selyth:obligation F-1\n return a; }",
                                {"functions": {"f": other}}),
        "obligation conflicting with sidecar": ("export function f(a: number): number {\n// selyth:obligation F-1\n return a; }",
                                                 {"functions": {"f": {**other, "obligations": ["F-2"]}}}),
        "loop obligation without a loop": ("export function f(a: number): number {\n// selyth:obligation F-1\n return a; }",
                                            {"functions": {"f": {**other, "loop": {"invariant": "true", "obligations": ["F-9"]}}}}),
        "hint before any function": ("// selyth:hint assert true;\nexport function f(a: number): number { return a; }",
                                     {"functions": {"f": other}}),
        "hint with assume": ("export function f(a: number): number {\n// selyth:hint assume true;\n return a; }",
                             {"functions": {"f": other}}),
        "hint conflicting with body_prefix": ("export function f(a: number): number {\n// selyth:hint assert true;\n return a; }",
                                              {"functions": {"f": {**other, "body_prefix": "assert false;"}}}),
        "preamble inside a function": ("export function f(a: number): number {\n// selyth:preamble lemma L() {}\n return a; }",
                                       {"functions": {"f": other}}),
        "preamble conflicting with sidecar": ("// selyth:preamble lemma L() {}\nexport function f(a: number): number { return a; }",
                                               {"preamble": "lemma M() {}", "functions": {"f": other}}),
        "obligation on a pure function": ("export function d(x: number): number {\n// selyth:obligation D-1\n return x + x; }\n"
                                          "export function f(a: number): number { return d(a); }",
                                          {"functions": {"d": {"dafny_name": "D", "nat": ["x"],
                                                               "returns": [["result", "int"]], "pure": True},
                                                         "f": other}}),
    }
    for label, (ts, sp) in bad.items():
        check(refused(ts, sp) is not None, f"contract comments: {label} is refused", ts)


def main():
    check_ledger()
    check_shapes()
    check_loops()
    check_constructs()
    check_switch()
    check_arrays_calls_lemmas()
    check_contract_comments()
    check_refusals()
    check_differential()
    check_construct_differential()
    print(f"\n{'FAILED' if FAILURES else 'all checks passed'}: {len(FAILURES)} failure(s)")
    for label, detail in FAILURES:
        print(f"\n--- {label}\n{detail}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
