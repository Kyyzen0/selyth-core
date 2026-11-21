#!/usr/bin/env python3
"""Translate a pure subset of TypeScript into Dafny, so a proof workspace is generated from the
code under verification instead of being written by hand beside it.

The subset (anything else is refused with a located error, never guessed):

  * `export function f(a: number, ...): number | [number, ...] { ... }`; a trailing optional
    parameter of function type (a test hook such as `observe?: (i: number, t: number) => void`)
    is dropped together with the statements that call it;
  * statements: `let|const x = e;`, `x = e;`, `x += e;`, `x -= e;`, `x++;`, `return e;`,
    `return [e, e];`, `if (e) s [else s]` (chains of `else if` included), `while (e) { ... }`,
    `while (true) { ...; if (e) break; ... }`,
    `do { ... } while (e);` (a post-test loop, emitted as `while true { ...; if !e { break } }`),
    `for (let i = K; i < e; i++) { ... }` (K a non-negative literal, `<` or `<=`, only `i++` as the
    update — desugared to a `while` with an explicit counter),
    `switch (e) { case K: s...; case L: s...; [default: s...] }` with integer-literal labels only,
    each case ending in `break;` or `return ...;` (no fallthrough), `default` last — desugared to
    a chain of `if (e === K) ... else ...`, blocks;
  * read-only fixed-size arrays: `const a: number[] = [e, ...];` (a `seq<int>`), reads `a[i]`
    (literal indices are bounds-checked at translation time, others are left for Dafny to prove)
    and `a.length` (`|a|`); element assignment, array parameters and reassignment are refused;
  * calls to pure functions of the same module (`g(e, ...)` → a Dafny `function` call): a pure
    function is a single `return e;`, no loop, no hook, marked `"pure": true` in the sidecar;
    calls to impure functions are refused;
  * `// selyth:lemma <Dafny lemma declaration>` comments, emitted as lemmas in the preamble —
    proven by Dafny (fail-closed); trust escapes (`assume`, `{:axiom}`, ...) are refused;
  * `// selyth:invariant <Dafny invariant expression>` inside a function that has the (single)
    loop: becomes the `invariant` clause of the `while`, in place of the sidecar `loop.invariant`;
  * `// selyth:caller <Dafny method declaration>` after a function: emitted verbatim after the
    method as a caller that proves preconditions, in place of the sidecar `callers`; each
    comment is one full `method ...` declaration, verified like everything else (fail-closed),
    trust escapes refused;
  * `// selyth:obligation <ID ...>` inside a function: the `// selyth:obligation` marker of the
    contract, or of the (single) loop when the comment sits inside the loop body — in place of
    the sidecar `obligations` / `loop.obligations`;
  * `// selyth:hint <Dafny statement>` inside a function: emitted at the top of the method body,
    in place of the sidecar `body_prefix`;
  * `// selyth:preamble <Dafny text>` at the top level: prepended to the workspace preamble, in
    place of the sidecar `preamble` (consecutive lines join into one block);
  * expressions: integer literals, identifiers, `+ - * % / === !== < <= > >= && || !`, unary `-`,
    parentheses, `c ? a : b`, `Math.floor(a / b)` as the only division, `Math.abs(e)` (emitted as
    an `Abs` helper on `int`; exact because `nat` is a subtype of `int`), and `Math.min(a, b)` /
    `Math.max(a, b)` (emitted as prepended `Min` / `Max` helpers on `int`, exact on integers).

Numbers are Dafny integers. That is a stated gap, not a translation: a TypeScript number is a
double, exact only below 2^53, and the Dafny proof holds for unbounded integers (see
verification-ir.md). `/` and `%` are refused unless both operands are provably non-negative
(literals, `nat`-typed names, sums and products of such), because JavaScript truncates and
Dafny floors toward the Euclidean quotient; where they agree the translation is exact, where they
differ the translator stops. Division by zero is not the translator's concern: Dafny proves the
divisor non-zero or the proof fails.

What cannot be read from the code comes from a sidecar specification, a JSON document:

  {"preamble": "<Dafny text>",
   "functions": {"clamp": {"dafny_name": "Clamp", "nat": ["balance"],
                           "returns": [["result", "int"]],
                           "obligations": ["LED-10", ...],
                           "heading": "Clamp: LED-10 .. LED-12",
                           "body_prefix": "<Dafny proof hint>", "loop": {"invariant": "...",
                           "obligations": [...]}, "callers": "<Dafny text>"}}}

`nat` lists the parameters that are non-negative, `returns` names and types the results, and
`dafny_name` / `heading` name the emitted declaration. The whole proof contract may live in the
code as `// selyth:` comments (lemma/preamble at the top level, obligation/invariant/hint inside a
function, caller after a function); the same value may also sit in the sidecar (`obligations`,
`loop`, `body_prefix`, `preamble`, `callers`) — one source only, giving both differently is
refused, and both sources produce the exact same workspace. The translation itself is the method
signature and body.

Usage: ts_to_dafny.py FILE.ts --spec SPEC.json [-o OUT.dfy]
"""
import argparse
import json
import re
import sys
from pathlib import Path


class TranslationError(Exception):
    pass


# ---- lexing ----------------------------------------------------------------------------

_TOKEN = re.compile(r"""
    \s+ | (?P<num>\d+(?:\.\d+)?) | (?P<id>[A-Za-z_$][A-Za-z0-9_$]*)
    | (?P<op>===|!==|\?\.|=>|<=|>=|&&|\|\||\+\+|--|\+=|-=|==|!=|[-+*/%<>=!?:;,.(){}\[\]])
    | (?P<bad>.)
""", re.X | re.S)


def strip_comments(text):
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise TranslationError("unterminated block comment")
            out.append(" ")
            i = j + 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def tokenize(text):
    text = strip_comments(text)
    tokens = []
    for m in _TOKEN.finditer(text):
        if m.group("bad"):
            raise TranslationError(f"unsupported character {m.group('bad')!r} (strings, templates and "
                                   f"regular expressions are not in the subset)")
        for kind in ("num", "id", "op"):
            if m.group(kind) is not None:
                tokens.append((kind, m.group(kind)))
                break
    return tokens


# ---- parsing ---------------------------------------------------------------------------

class Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k][1] if self.i + k < len(self.t) else None

    def kind(self):
        return self.t[self.i][0] if self.i < len(self.t) else None

    def next(self):
        if self.i >= len(self.t):
            raise TranslationError("unexpected end of input")
        tok = self.t[self.i]
        self.i += 1
        return tok[1]

    def eat(self, value):
        if self.peek() != value:
            raise TranslationError(f"expected {value!r}, found {self.peek()!r}")
        self.i += 1

    def accept(self, value):
        if self.peek() == value:
            self.i += 1
            return True
        return False

    def ident(self):
        if self.kind() != "id":
            raise TranslationError(f"expected a name, found {self.peek()!r}")
        return self.next()

    # program
    def program(self):
        functions = []
        while self.i < len(self.t):
            if self.peek() != "export":
                raise TranslationError(f"only exported functions are in the subset, found {self.peek()!r}")
            functions.append(self.function())
        return functions

    def number_type(self):
        if self.peek() != "number":
            raise TranslationError(f"only the type `number` is in the subset, found {self.peek()!r}")
        self.i += 1

    def function(self):
        self.eat("export")
        self.eat("function")
        name = self.ident()
        self.eat("(")
        params, hooks = [], []
        while self.peek() != ")":
            pname = self.ident()
            optional = self.accept("?")
            self.eat(":")
            if self.peek() == "(":                        # a function-typed parameter: a test hook
                depth = 0
                while True:
                    tok = self.next()
                    depth += tok == "("
                    depth -= tok == ")"
                    if depth == 0:
                        break
                self.eat("=>")
                self.eat("void")
                if not optional:
                    raise TranslationError(f"{name}: the function-typed parameter {pname} must be optional "
                                           f"(a test hook the proof model has no counterpart for)")
                hooks.append(pname)
            else:
                if optional:
                    raise TranslationError(f"{name}: optional number parameter {pname} is not in the subset")
                self.number_type()
                params.append(pname)
            if not self.accept(","):
                break
        self.eat(")")
        self.eat(":")
        if self.accept("["):
            arity = 1
            self.number_type()
            while self.accept(","):
                self.number_type()
                arity += 1
            self.eat("]")
        else:
            self.number_type()
            arity = 1
        body = self.block(hooks)
        return {"name": name, "params": params, "arity": arity, "body": body, "hooks": hooks}

    # statements
    def block(self, hooks):
        self.eat("{")
        out = []
        while self.peek() != "}":
            stmt = self.statement(hooks)
            if stmt is not None:
                out.append(stmt)
        self.eat("}")
        return out

    def body_of(self, hooks):
        if self.peek() == "{":
            return self.block(hooks)
        stmt = self.statement(hooks)
        return [] if stmt is None else [stmt]

    def statement(self, hooks):
        tok = self.peek()
        if tok in ("let", "const"):
            is_const = tok == "const"
            self.next()
            name = self.ident()
            is_array = False
            if self.peek() == ":":
                self.next()
                if self.peek() == "number" and self.peek(1) == "[":
                    self.next()
                    self.eat("[")
                    self.eat("]")
                    is_array = True
                else:
                    self.number_type()
            self.eat("=")
            if is_array:
                if not is_const:
                    raise TranslationError(f"{name}: an array must be declared `const` (arrays are read-only)")
                self.eat("[")
                values = [self.expr()]
                while self.accept(","):
                    values.append(self.expr())
                self.eat("]")
                self.eat(";")
                if not values:
                    raise TranslationError(f"{name}: an empty array literal is refused")
                return ("arrdecl", name, values)
            value = self.expr()
            self.eat(";")
            return ("let", name, value)
        if tok == "return":
            self.next()
            if self.accept("["):
                values = [self.expr()]
                while self.accept(","):
                    values.append(self.expr())
                self.eat("]")
            else:
                values = [self.expr()]
            self.eat(";")
            return ("return", values)
        if tok == "if":
            self.next()
            self.eat("(")
            cond = self.expr()
            self.eat(")")
            then = self.body_of(hooks)
            other = None
            if self.accept("else"):
                other = self.body_of(hooks)
            return ("if", cond, then, other)
        if tok == "while":
            self.next()
            self.eat("(")
            cond = self.expr()
            self.eat(")")
            return ("while", cond, self.block(hooks))
        if tok == "do":
            # do { ... } while (c); — a post-test loop, emitted as while true { body; if !c { break } }
            # so the existing loop machinery (one loop per function, invariant, marker) applies.
            self.next()
            body = self.block(hooks)
            self.eat("while")
            self.eat("(")
            cond = self.expr()
            self.eat(")")
            self.eat(";")
            return ("dowhile", cond, body)
        if tok == "for":
            # A counted loop is the one `for` the subset accepts, desugared at parse time into
            # `var i := K; while i < e { ...; i := i + 1; }` so the existing loop machinery
            # (sidecar invariant, marker, one loop per function) applies unchanged.
            self.next()
            self.eat("(")
            if self.peek() not in ("let", "const"):
                raise TranslationError("only `for (let i = K; i < e; i++)` is in the subset")
            self.next()
            var = self.ident()
            self.eat("=")
            init = self.expr()
            if init[0] != "num" or init[1] < 0:
                raise TranslationError("the for-loop initialiser must be a non-negative integer literal")
            self.eat(";")
            cond = self.expr()
            if not (cond[0] == "bin" and cond[1] in ("<", "<=") and cond[2] == ("var", var)):
                raise TranslationError(f"the for-loop condition must be `{var} < e` or `{var} <= e`")
            self.eat(";")
            if self.peek() != var or self.peek(1) != "++":
                raise TranslationError("only `i++` is in the subset as a for-loop update")
            self.next()
            self.next()
            self.eat(")")
            body = self.body_of(hooks)
            return ("block", [("let", var, init),
                              ("while", cond, [*body, ("assign", var, "+=", ("num", 1))])])
        if tok == "switch":
            # Desugared at parse time into a chain of `if (subj === K) ... else ...`, so the
            # emitter and the one-loop-per-function rule apply unchanged. Conservative by
            # construction: integer-literal labels only, no duplicate, no fallthrough (a case
            # must end with `break;` or `return ...;`), `default` last.
            self.next()
            self.eat("(")
            subj = self.expr()
            self.eat(")")
            self.eat("{")
            cases = []
            seen = set()
            default_seen = False
            while self.peek() != "}":
                if default_seen:
                    raise TranslationError("`default` must be the last clause of a switch")
                if self.peek() == "case":
                    self.next()
                    label = self.expr()
                    if label[0] == "num":
                        n = label[1]
                    elif label[0] == "un" and label[1] == "-" and label[2][0] == "num":
                        n = -label[2][1]
                    else:
                        raise TranslationError("a case label must be an integer literal")
                    if n in seen:
                        raise TranslationError(f"duplicate case label {n}")
                    seen.add(n)
                    self.eat(":")
                elif self.peek() == "default":
                    self.next()
                    self.eat(":")
                    n = None
                    default_seen = True
                else:
                    raise TranslationError("a switch holds only `case <int>:` and `default:` clauses")
                body = []
                while self.peek() not in ("case", "default", "}"):
                    s = self.statement(hooks)
                    if s is not None:
                        body.append(s)
                if not body:
                    raise TranslationError("an empty case is refused (write `break;`)")
                if body[-1] == ("break",):
                    body = body[:-1]
                elif body[-1][0] != "return":
                    raise TranslationError("a case must end with `break;` or `return ...;` "
                                           "(fallthrough is not translated)")
                cases.append((n, body))
            self.eat("}")
            if not cases:
                raise TranslationError("an empty switch is refused")
            else_stmts = None
            for n, body in reversed(cases):
                if n is None:
                    else_stmts = body
                else:
                    node = ("if", ("bin", "===", subj, ("num", n)), body, else_stmts)
                    else_stmts = [node]
            return ("block", else_stmts)
        if tok == "break":
            self.next()
            self.eat(";")
            return ("break",)
        if tok == "{":
            return ("block", self.block(hooks))
        if tok in hooks:                                   # observe?.(...);  /  observe(...);
            self.next()
            self.accept("?.")
            self.eat("(")
            # The call is dropped, so its arguments must have no effect to lose: they are parsed
            # as expressions, a grammar with no assignment, increment or call.
            if self.peek() != ")":
                self.expr()
                while self.accept(","):
                    self.expr()
            self.eat(")")
            self.eat(";")
            return None
        if self.kind() == "id":
            name = self.next()
            if self.peek() == "[":
                raise TranslationError(f"{name}[...]: array elements are read-only in the subset")
            op = self.peek()
            if op in ("=", "+=", "-="):
                self.next()
                value = self.expr()
                self.eat(";")
                return ("assign", name, op, value)
            if op in ("++", "--"):
                self.next()
                self.eat(";")
                return ("assign", name, "+=" if op == "++" else "-=", ("num", 1))
            raise TranslationError(f"unsupported statement starting with {name!r} {op!r}")
        raise TranslationError(f"unsupported statement starting with {tok!r}")

    # expressions
    _LEVELS = [("||",), ("&&",), ("===", "!=="), ("<", "<=", ">", ">="), ("+", "-"), ("*", "/", "%")]

    def expr(self, level=0):
        if level == 0:
            return self.ternary()
        if level > len(self._LEVELS):
            return self.unary()
        ops = self._LEVELS[level - 1]
        left = self.expr(level + 1)
        while self.peek() in ops:
            op = self.next()
            left = ("bin", op, left, self.expr(level + 1))
        return left

    def ternary(self):
        cond = self.expr(1)                            # the `||` level and everything below
        if self.accept("?"):
            then = self.expr()
            self.eat(":")
            return ("if", cond, then, self.ternary())   # right associative
        return cond

    def unary(self):
        tok = self.peek()
        if tok in ("-", "!"):
            self.next()
            return ("un", tok, self.unary())
        return self.primary()

    def primary(self):
        kind, tok = self.t[self.i] if self.i < len(self.t) else (None, None)
        if kind == "num":
            self.next()
            if "." in tok:
                raise TranslationError(f"non-integer literal {tok}")
            return ("num", int(tok))
        if tok == "true" or tok == "false":
            self.next()
            return ("bool", tok == "true")
        if tok == "Math":
            self.next()
            self.eat(".")
            name = self.next()
            if name == "floor":
                self.eat("(")
                inner = self.expr()
                self.eat(")")
                if inner[0] != "bin" or inner[1] != "/":
                    raise TranslationError("Math.floor is only accepted around a division a / b")
                return ("floordiv", inner[2], inner[3])
            if name == "abs":
                self.eat("(")
                inner = self.expr()
                self.eat(")")
                return ("abs", inner)
            if name in ("min", "max"):
                self.eat("(")
                a = self.expr()
                if not self.accept(","):
                    raise TranslationError(f"Math.{name} takes exactly two arguments")
                b = self.expr()
                self.eat(")")
                return ("minmax", "Min" if name == "min" else "Max", a, b)
            raise TranslationError("only Math.floor, Math.abs, Math.min and Math.max are in the subset")
        if kind == "id":
            self.next()
            if self.peek() == "[":
                self.next()
                index = self.expr()
                self.eat("]")
                return ("index", tok, index)
            if self.peek() == "." and self.peek(1) == "length":
                self.next()
                self.next()
                return ("arrlen", tok)
            if self.peek() == "(":
                self.next()
                args = []
                if self.peek() != ")":
                    args.append(self.expr())
                    while self.accept(","):
                        args.append(self.expr())
                self.eat(")")
                return ("call", tok, args)
            return ("var", tok)
        if tok == "(":
            self.next()
            inner = self.expr()
            self.eat(")")
            return inner
        raise TranslationError(f"unsupported expression starting with {tok!r}")


# ---- translation -----------------------------------------------------------------------

_DAFNY_OP = {"===": "==", "!==": "!=", "&&": "&&", "||": "||", "<": "<", "<=": "<=", ">": ">", ">=": ">=",
             "+": "+", "-": "-", "*": "*", "%": "%"}
_PREC = {"||": 2, "&&": 2, "==": 3, "!=": 3, "<": 3, "<=": 3, ">": 3, ">=": 3, "+": 5, "-": 5,
         "*": 6, "/": 6, "%": 6, "if": 1}

ABS_DEF = "function Abs(x: int): int\n{\n  if x < 0 then -x else x\n}"
MIN_DEF = "function Min(x: int, y: int): int\n{\n  if x < y then x else y\n}"
MAX_DEF = "function Max(x: int, y: int): int\n{\n  if x > y then x else y\n}"
_NEGATED = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "===": "!==", "!==": "==="}


def dafny_name(name):
    """A TypeScript name as a Dafny one: Dafny reserves a leading underscore."""
    return name.lstrip("_") or "x"


class Context:
    def __init__(self, fn, spec):
        self.fn = fn
        self.spec = spec
        self.names = {}                                    # TypeScript name -> Dafny name
        for p in fn["params"]:
            d = dafny_name(p)
            if d in self.names.values():
                raise TranslationError(f"{fn['name']}: the parameters collide once renamed: {d}")
            self.names[p] = d
        self.results = [r[0] for r in spec["returns"]]
        if len(self.results) != fn["arity"]:
            raise TranslationError(f"{fn['name']}: returns {fn['arity']} value(s), the specification names "
                                   f"{len(self.results)}")
        if set(self.results) & set(self.names.values()):
            raise TranslationError(f"{fn['name']}: a result shares its name with a parameter")
        unknown = set(spec.get("nat", [])) - set(fn["params"])
        if unknown:
            raise TranslationError(f"{fn['name']}: `nat` lists unknown parameters {sorted(unknown)}")
        self.nonneg = {self.names[p] for p in spec.get("nat", [])}
        self.locals_declared = set()
        self.arrays = {}                                   # TS name -> (Dafny name, length)
        self.pure_calls = fn.get("pure_calls", {})         # TS name -> Dafny name of a pure function
        self.pure_nat = fn.get("pure_nat", set())          # Dafny names of pure functions returning nat
        self._prepare(fn["body"])
        for ts_name, (dname, _) in self.arrays.items():
            if ts_name in fn["params"]:
                raise TranslationError(f"{fn['name']}: {ts_name} is both a parameter and an array")
            if dname in self.names.values() or dname in [r for r in self.results]:
                raise TranslationError(f"{fn['name']}: the array {ts_name} collides once renamed: {dname}")
        # Dafny parameters are immutable: a parameter that the TypeScript reassigns is shadowed
        # by a mutable local of the same name at the top of the body (`var b := b;`), so the
        # assignments below are legal and the `ensures` clauses still see the original values.
        self.reassigned_params = [p for p in fn["params"] if p in self._updates]

    def _prepare(self, body):
        """Which locals are non-negative: a local is, if every value it takes is."""
        inits, updates = {}, {}

        self._updates = updates

        def walk(stmts):
            for s in stmts:
                if s[0] == "let":
                    inits.setdefault(s[1], []).append(s[2])
                elif s[0] == "arrdecl":
                    if s[1] in self.arrays:
                        raise TranslationError(f"the array {s[1]} is declared twice")
                    self.arrays[s[1]] = (dafny_name(s[1]), len(s[2]))
                elif s[0] == "assign":
                    if s[1] in self.arrays:
                        raise TranslationError(f"{s[1]}: arrays are read-only in the subset")
                    updates.setdefault(s[1], []).append((s[2], s[3]))
                elif s[0] == "if":
                    walk(s[2])
                    walk(s[3] or [])
                elif s[0] in ("while", "dowhile"):
                    walk(s[2])
                elif s[0] == "block":
                    walk(s[1])
        walk(body)
        nonneg = set(self.nonneg) | {r for r, t in self.spec["returns"] if t == "nat"}
        for _ in range(len(inits) + 1):                    # fixed point, from the optimistic side
            changed = False
            for name in list(inits):
                dname = dafny_name(name)
                if dname in nonneg:
                    continue
                ok = all(self.is_nonneg(e, nonneg) for e in inits[name])
                for op, e in updates.get(name, []):
                    ok = ok and op != "-=" and self.is_nonneg(e, nonneg | {dname})
                if ok:
                    nonneg.add(dname)
                    changed = True
            if not changed:
                break
        self.nonneg = nonneg

    def is_nonneg(self, e, extra=None):
        known = self.nonneg if extra is None else extra
        kind = e[0]
        if kind == "num":
            return e[1] >= 0
        if kind == "var":
            return self.names.get(e[1], dafny_name(e[1])) in known
        if kind == "bin" and e[1] in ("+", "*", "%"):
            return self.is_nonneg(e[2], known) and self.is_nonneg(e[3], known)
        if kind == "floordiv":
            return self.is_nonneg(e[1], known) and self.is_nonneg(e[2], known)
        if kind == "abs":
            return True
        if kind == "minmax":
            return self.is_nonneg(e[2], known) and self.is_nonneg(e[3], known)
        if kind == "if":
            return self.is_nonneg(e[2], known) and self.is_nonneg(e[3], known)
        if kind == "arrlen":
            return True
        if kind == "call":
            return dafny_name(e[1]) in self.pure_nat
        return False

    # Expression emission, with the fewest parentheses that keep the meaning in Dafny. Dafny has
    # its own rules: comparisons do not chain and `&&` / `||` do not mix without parentheses, so
    # those are always parenthesised; only integer arithmetic relies on precedence.
    @staticmethod
    def _wrap(text, child_op, parent_op, right):
        if parent_op is None:
            return text
        pc, pp = _PREC[child_op], _PREC[parent_op]
        if pc < pp:
            need = True
        elif pc > pp:
            need = False
        elif pp == 3:
            need = True
        elif pp <= 2:
            need = child_op != parent_op
        elif not right:
            need = False
        else:
            # only integer addition is associative whatever the operands contain: `a * (b / c) * d`
            # is not `a * b / c * d`, so a right operand of `*`, `/`, `%` or `-` keeps its parentheses
            need = not (parent_op == "+" and child_op in "+-")
        return f"({text})" if need else text

    def emit(self, e, parent_op=None, right=False):
        kind = e[0]
        if kind == "num":
            return str(e[1])
        if kind == "bool":
            return "true" if e[1] else "false"
        if kind == "var":
            name = e[1]
            if name not in self.names and name not in self.locals_declared and name not in self.results:
                raise TranslationError(f"unknown name {name!r}")
            return self.names.get(name, dafny_name(name))
        if kind == "un":
            inner = e[2]
            text = self.emit(inner)
            if e[1] == "-":
                # Dafny types `-x` with the type of x, so `-amount` on a nat must be shown to be an
                # int: `-amount + balance` fails to verify otherwise (found by the evaluation harness)
                return f"-({text} as int)"
            if inner[0] in ("bin", "floordiv", "un"):
                text = f"({text})"
            return f"!{text}"
        if kind == "floordiv":
            return self._arith(e[1], e[2], "/", parent_op, right)
        if kind == "abs":
            return f"Abs({self.emit(e[1])})"
        if kind == "minmax":
            return f"{e[1]}({self.emit(e[2])}, {self.emit(e[3])})"
        if kind == "index":
            if e[1] not in self.arrays:
                if e[1] in self.names or e[1] in self.locals_declared or e[1] in self.results:
                    raise TranslationError(f"{e[1]} is not an array: indexing needs a `const x: number[] = [...]`")
                raise TranslationError(f"unknown name {e[1]!r}")
            dname, length = self.arrays[e[1]]
            idx = e[2]
            literal = None
            if idx[0] == "num":
                literal = idx[1]
            elif idx[0] == "un" and idx[1] == "-" and idx[2][0] == "num":
                literal = -idx[2][1]
            if literal is not None and not (0 <= literal < length):
                raise TranslationError(f"{e[1]}[{literal}]: the array has {length} element(s) (bounds are checked, not assumed)")
            return f"{dname}[{self.emit(idx)}]"
        if kind == "arrlen":
            if e[1] not in self.arrays:
                raise TranslationError(f"{e[1]}.length: {e[1]} is not an array")
            return f"|{self.arrays[e[1]][0]}|"
        if kind == "call":
            if e[1] not in self.pure_calls:
                raise TranslationError(f"call to {e[1]!r}: only calls to pure functions (single `return`, "
                                       f"no loop, spec entry \"pure\": true) are in the subset")
            args = ", ".join(self.emit(a) for a in e[2])
            return f"{self.pure_calls[e[1]]}({args})"
        if kind == "if":
            def sub(x):
                t = self.emit(x)
                return f"({t})" if x[0] == "if" else t   # nested if-expressions are parenthesised
            text = f"if {self.emit(e[1])} then {sub(e[2])} else {sub(e[3])}"
            return self._wrap(text, "if", parent_op, right)
        if kind == "bin":
            op = e[1]
            if op == "/":
                raise TranslationError("a bare `/` is real division in TypeScript; write Math.floor(a / b)")
            if op == "%":
                return self._arith(e[2], e[3], "%", parent_op, right)
            dop = _DAFNY_OP[op]
            text = f"{self.emit(e[2], dop)} {dop} {self.emit(e[3], dop, True)}"
            return self._wrap(text, dop, parent_op, right)
        raise TranslationError(f"cannot emit {kind}")

    def _arith(self, a, b, op, parent_op, right):
        if not (self.is_nonneg(a) and self.is_nonneg(b)):
            raise TranslationError(f"`{op}` on operands that are not provably non-negative: JavaScript and "
                                   f"Dafny disagree on negative operands (declare the parameters `nat`)")
        text = f"{self.emit(a, op)} {op} {self.emit(b, op, True)}"
        return self._wrap(text, op, parent_op, right)

    def negate(self, e):
        if e[0] == "bin" and e[1] in _NEGATED:
            return ("bin", _NEGATED[e[1]], e[2], e[3])
        if e[0] == "un" and e[1] == "!":
            return e[2]
        return ("un", "!", e)


def translate_function(fn, spec, indent="  "):
    ctx = Context(fn, spec)
    results = ctx.results
    out = []
    params = ", ".join(f"{ctx.names[p]}: {'nat' if ctx.names[p] in {ctx.names[q] for q in spec.get('nat', [])} else 'int'}"
                       for p in fn["params"])
    returns = ", ".join(f"{n}: {t}" for n, t in spec["returns"])
    out.append(f"method {spec['dafny_name']}({params}) returns ({returns})")
    if fn.get("resolved_obligations"):
        out.append(f"{indent}// selyth:obligation {' '.join(fn['resolved_obligations'])}")
    out.append("{")
    for p in ctx.reassigned_params:
        out.append(f"{indent}var {ctx.names[p]} := {ctx.names[p]};")
    for hint in fn.get("resolved_hints") or []:
        out.append(f"{indent}{hint}")
    loop_used = [False]

    def stmts(body, depth, last_of_function=False):
        lines = []
        pad = indent * depth
        for k, s in enumerate(body):
            final = last_of_function and k == len(body) - 1
            kind = s[0]
            if kind == "let":
                name = s[1]
                if name in ctx.names:
                    raise TranslationError(f"{fn['name']}: {name} shadows a parameter")
                if name in results:
                    lines.append(f"{pad}{name} := {ctx.emit(s[2])};")
                else:
                    ctx.locals_declared.add(name)
                    lines.append(f"{pad}var {dafny_name(name)} := {ctx.emit(s[2])};")
            elif kind == "arrdecl":
                name = s[1]
                if name in ctx.names or name in results:
                    raise TranslationError(f"{fn['name']}: the array {name} collides with a parameter or result")
                dname, _ = ctx.arrays[name]
                values = ", ".join(ctx.emit(v) for v in s[2])
                lines.append(f"{pad}var {dname}: seq<int> := [{values}];")
            elif kind == "assign":
                _, name, op, value = s
                target = dafny_name(name)
                if name not in ctx.names and name not in ctx.locals_declared and name not in results:
                    raise TranslationError(f"{fn['name']}: assignment to undeclared {name!r}")
                if op == "=":
                    lines.append(f"{pad}{target} := {ctx.emit(value)};")
                elif op == "+=":
                    lines.append(f"{pad}{target} := {target} + {ctx.emit(value, '+', True)};")
                else:
                    lines.append(f"{pad}{target} := {target} - {ctx.emit(value, '-', True)};")
            elif kind == "return":
                values = s[1]
                if len(values) != len(results):
                    raise TranslationError(f"{fn['name']}: returns {len(values)} value(s), declared {len(results)}")
                pairs = [(r, v) for r, v in zip(results, values) if not (v == ("var", r))]
                if pairs:
                    lhs = ", ".join(r for r, _ in pairs)
                    rhs = ", ".join(ctx.emit(v) for _, v in pairs)
                    lines.append(f"{pad}{lhs} := {rhs};")
                if not final:
                    lines.append(f"{pad}return;")
            elif kind == "if":
                lines.append(f"{pad}if {ctx.emit(s[1])} {{")
                lines += stmts(s[2], depth + 1)
                if s[3] is not None:
                    lines.append(f"{pad}}} else {{")
                    lines += stmts(s[3], depth + 1)
                lines.append(f"{pad}}}")
            elif kind in ("while", "dowhile"):
                lines += loop(s, depth)
            elif kind == "block":
                lines += stmts(s[1], depth)
            elif kind == "break":
                raise TranslationError(f"{fn['name']}: `break` outside the `if (e) break;` loop-exit form")
        return lines

    def loop(s, depth):
        pad = indent * depth
        if loop_used[0]:
            raise TranslationError(f"{fn['name']}: one loop per function is the limit of this translator")
        loop_used[0] = True
        if s[0] == "dowhile":                              # do { B } while (c); — post-test: B runs first
            cond, body = s[1], s[2]
            spec_loop = spec.get("loop") or {}
            invariant = fn.get("resolved_invariant") or spec_loop.get("invariant")
            if not invariant:
                raise TranslationError(f"{fn['name']}: a loop needs an invariant (a selyth:invariant comment or "
                                       f"the sidecar)")
            lines = [f"{pad}while true", f"{pad}{indent}invariant {invariant}"]
            loop_ids = fn.get("resolved_loop_obligations") or spec_loop.get("obligations")
            if loop_ids:
                lines.append(f"{pad}{indent}// selyth:obligation {' '.join(loop_ids)}")
            lines.append(f"{pad}{{")
            lines += stmts(body, depth + 1)
            lines.append(f"{pad}{indent}if {ctx.emit(ctx.negate(cond))} {{ break }}")
            lines.append(f"{pad}}}")
            return lines
        cond, body = s[1], s[2]
        if cond == ("bool", True):                         # while (true) { ...; if (c) break; rest }
            at = next((k for k, b in enumerate(body) if b[0] == "if" and b[2] == [("break",)] and b[3] is None), None)
            if at is None or at != 0:
                raise TranslationError(f"{fn['name']}: `while (true)` must begin with `if (e) break;`")
            exit_cond = body[0][1]
            head = ctx.emit(ctx.negate(exit_cond))
            body = body[1:]
        else:
            head = ctx.emit(cond)
        spec_loop = spec.get("loop") or {}
        invariant = fn.get("resolved_invariant") or spec_loop.get("invariant")
        if not invariant:
            raise TranslationError(f"{fn['name']}: a loop needs an invariant (a selyth:invariant comment or "
                                   f"the sidecar)")
        lines = [f"{pad}while {head}", f"{pad}{indent}invariant {invariant}"]
        loop_ids = fn.get("resolved_loop_obligations") or spec_loop.get("obligations")
        if loop_ids:
            lines.append(f"{pad}{indent}// selyth:obligation {' '.join(loop_ids)}")
        lines.append(f"{pad}{{")
        lines += stmts(body, depth + 1)
        lines.append(f"{pad}}}")
        return lines

    out += stmts(fn["body"], 1, last_of_function=True)
    out.append("}")
    return "\n".join(out)


def _uses_helper_in_expr(e, kinds):
    if not isinstance(e, tuple):
        return False
    if e[0] in kinds:
        return True
    return any(_uses_helper_in_expr(x, kinds) for x in e[1:] if isinstance(x, tuple))


def _uses_helper_in_stmts(stmts, kinds):
    for s in stmts:
        if s[0] == "let" and _uses_helper_in_expr(s[2], kinds):
            return True
        if s[0] == "arrdecl" and any(_uses_helper_in_expr(v, kinds) for v in s[2]):
            return True
        if s[0] == "return" and any(_uses_helper_in_expr(v, kinds) for v in s[1]):
            return True
        if s[0] == "assign" and _uses_helper_in_expr(s[3], kinds):
            return True
        if s[0] == "if" and (_uses_helper_in_expr(s[1], kinds) or _uses_helper_in_stmts(s[2], kinds)
                             or _uses_helper_in_stmts(s[3] or [], kinds)):
            return True
        if s[0] in ("while", "dowhile") and (_uses_helper_in_expr(s[1], kinds) or _uses_helper_in_stmts(s[2], kinds)):
            return True
        if s[0] == "block" and _uses_helper_in_stmts(s[1], kinds):
            return True
    return False


def _uses_minmax_name(stmts, helper):
    """Whether a min/max node with the given Dafny helper name occurs in the statements."""
    def in_expr(e):
        if not isinstance(e, tuple):
            return False
        if e[0] == "minmax" and e[1] == helper:
            return True
        return any(in_expr(x) for x in e[1:] if isinstance(x, tuple))
    for s in stmts:
        if s[0] == "let" and in_expr(s[2]):
            return True
        if s[0] == "return" and any(in_expr(v) for v in s[1]):
            return True
        if s[0] == "assign" and in_expr(s[3]):
            return True
        if s[0] == "if" and (in_expr(s[1]) or _uses_minmax_name(s[2], helper)
                             or _uses_minmax_name(s[3] or [], helper)):
            return True
        if s[0] in ("while", "dowhile") and (in_expr(s[1]) or _uses_minmax_name(s[2], helper)):
            return True
        if s[0] == "block" and _uses_minmax_name(s[1], helper):
            return True
    return False


_LEMMA_RE = re.compile(r"//[ \t]*selyth:lemma[ \t]+(.*)")
_TRUST_WORDS = ("assume", "{:axiom}", "{:extern}", "expect ")


def extract_lemmas(ts_source):
    """`// selyth:lemma <Dafny lemma declaration>` comments become proven Dafny lemmas in the
    preamble. They are PROVEN by Dafny (a false lemma fails verification, fail-closed); trust
    escapes (`assume`, `{:axiom}`, `{:extern}`, `expect`) are refused here, as the backend's
    trust scan refuses them in the workspace."""
    lemmas = []
    for lineno, line in enumerate(ts_source.splitlines(), 1):
        m = _LEMMA_RE.search(line)
        if not m:
            continue
        text = m.group(1).strip()
        if not text.startswith("lemma "):
            raise TranslationError(f"line {lineno}: a selyth:lemma comment holds a full Dafny lemma declaration")
        if any(w in text for w in _TRUST_WORDS):
            raise TranslationError(f"line {lineno}: trust escapes are refused in code (the workspace scan "
                                   f"refuses them too)")
        lemmas.append((lineno, text))
    names = [text.split("(", 1)[0].split()[-1] for _, text in lemmas if "(" in text]
    if len(names) != len(set(names)):
        raise TranslationError("duplicate lemma name in selyth:lemma comments")
    return [text for _, text in lemmas]


_INVARIANT_RE = re.compile(r"//[ \t]*selyth:invariant[ \t]+(.*)")
_CALLER_RE = re.compile(r"//[ \t]*selyth:caller(?:[ \t](.*))?")
_OBLIGATION_RE = re.compile(r"//[ \t]*selyth:obligation[ \t]+(.*)")
_HINT_RE = re.compile(r"//[ \t]*selyth:hint[ \t]+(.*)")
_PREAMBLE_RE = re.compile(r"//[ \t]*selyth:preamble(?:[ \t](.*))?")
_FN_RE = re.compile(r"^\s*export\s+function\s+([A-Za-z_$][A-Za-z0-9_$]*)")
_LOOP_RE = re.compile(r"\b(?:while|for)\s*\(")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _empty_contract():
    return {"invariant": None, "obligations": [], "loop_obligations": [], "hints": [], "callers": []}


def extract_source_contracts(ts_source):
    """The `// selyth:...` comments carry the proof contract in the code itself, so the sidecar
    keeps only the naming and typing: `selyth:lemma` and `selyth:preamble` at the top level,
    `selyth:obligation` / `selyth:invariant` / `selyth:hint` inside a function, `selyth:caller`
    after a function. One loop per function makes the contract/loop classification of an
    obligation comment unambiguous (brace depth). Everything emitted from a comment is verified
    by Dafny like everything else (fail-closed); trust escapes are refused here, as the
    workspace scan refuses them too."""
    text = _BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), ts_source)  # keep line numbers
    contracts, preambles = {}, []
    current = last_fn = None
    depth = 0
    loop_body_depth, pending_loop = None, False
    prev_was_caller = False
    for lineno, line in enumerate(text.splitlines(), 1):
        cur_was_caller = prev_was_caller
        prev_was_caller = _CALLER_RE.search(line) is not None
        code = line.split("//", 1)[0]
        depth_before = depth
        fn = _FN_RE.match(line)
        was_top = depth_before == 0 and fn is None
        if fn:
            current = last_fn = fn.group(1)
            contracts.setdefault(current, _empty_contract())
            depth, loop_body_depth, pending_loop = 0, None, False
        if current is not None and loop_body_depth is None and _LOOP_RE.search(code):
            pending_loop = True
        for ch in code:
            if ch == "{":
                depth += 1
                if pending_loop:
                    loop_body_depth, pending_loop = depth, False
            elif ch == "}":
                depth -= 1
                if loop_body_depth is not None and depth < loop_body_depth:
                    loop_body_depth = None
        if depth == 0 and fn is None:
            current = None
        m = _PREAMBLE_RE.search(line)
        if m:
            if not was_top:
                raise TranslationError(f"line {lineno}: a selyth:preamble comment must sit at the top level")
            pre = (m.group(1) or "").rstrip()
            if not pre.strip():
                raise TranslationError(f"line {lineno}: a selyth:preamble comment holds Dafny text")
            if any(w in pre for w in _TRUST_WORDS):
                raise TranslationError(f"line {lineno}: trust escapes are refused in code (the workspace scan "
                                       f"refuses them too)")
            preambles.append(pre)
            continue
        m = _INVARIANT_RE.search(line)
        if m:
            if current is None:
                raise TranslationError(f"line {lineno}: a selyth:invariant comment must sit inside a function")
            inv = m.group(1).strip()
            if not inv:
                raise TranslationError(f"line {lineno}: a selyth:invariant comment holds a Dafny invariant expression")
            if contracts[current]["invariant"] is not None:
                raise TranslationError(f"line {lineno}: function {current} has two selyth:invariant comments "
                                       f"(one loop per function)")
            contracts[current]["invariant"] = inv
            continue
        m = _OBLIGATION_RE.search(line)
        if m:
            if current is None:
                raise TranslationError(f"line {lineno}: a selyth:obligation comment must sit inside a function")
            ids = m.group(1).split()
            if not ids or any(not re.fullmatch(r"[A-Za-z0-9_-]+", i) for i in ids):
                raise TranslationError(f"line {lineno}: a selyth:obligation comment holds obligation ids")
            c = contracts[current]
            if any(i in c["obligations"] + c["loop_obligations"] for i in ids):
                raise TranslationError(f"line {lineno}: function {current} names an obligation id twice")
            if loop_body_depth is not None and depth_before >= loop_body_depth:
                c["loop_obligations"].extend(ids)
            else:
                c["obligations"].extend(ids)
            continue
        m = _HINT_RE.search(line)
        if m:
            if current is None:
                raise TranslationError(f"line {lineno}: a selyth:hint comment must sit inside a function")
            hint = m.group(1).strip()
            if not hint:
                raise TranslationError(f"line {lineno}: a selyth:hint comment holds a Dafny statement")
            if any(w in hint for w in _TRUST_WORDS):
                raise TranslationError(f"line {lineno}: trust escapes are refused in code (the workspace scan "
                                       f"refuses them too)")
            contracts[current]["hints"].append(hint)
            continue
        m = _CALLER_RE.search(line)
        if m:
            if last_fn is None:
                raise TranslationError(f"line {lineno}: a selyth:caller comment must follow a function")
            caller = (m.group(1) or "").rstrip()
            if not cur_was_caller and not caller.strip().startswith("method "):
                raise TranslationError(f"line {lineno}: a selyth:caller comment holds a full Dafny method declaration")
            if any(w in caller for w in _TRUST_WORDS):
                raise TranslationError(f"line {lineno}: trust escapes are refused in code (the workspace scan "
                                       f"refuses them too)")
            contracts[last_fn]["callers"].append(caller)
    return contracts, preambles


_OBLIGATION_COMMENT = re.compile(r"^(\s*)//[ \t]*selyth:obligation[ \t]+(.*)$")


def strip_obligation_ids(ts_source, ids):
    """Remove `ids` from the `// selyth:obligation` comments, dropping emptied comment lines. The
    workspace is regenerated from the code, so an obligation deleted from the set must also lose
    its marker there, keeping the workspace consistent with the set (a stale marker is refused by
    the backend itself)."""
    out = []
    for line in ts_source.split("\n"):
        m = _OBLIGATION_COMMENT.match(line)
        if m:
            kept = [i for i in m.group(2).split() if i not in ids]
            if kept:
                out.append(f"{m.group(1)}// selyth:obligation {' '.join(kept)}")
            continue
        out.append(line)
    return "\n".join(out)


def _has_loop(stmts):
    for s in stmts:
        if s[0] in ("while", "dowhile"):
            return True
        for part in s[1:]:
            if isinstance(part, list) and _has_loop(part):
                return True
    return False


def _is_pure(fn, entry):
    """A pure function is emitted as a Dafny `function` (callable in expressions): a single
    `return e;`, no loop, no hook, no obligations/callers in the sidecar."""
    if not entry.get("pure"):
        return False
    if fn["hooks"]:
        raise TranslationError(f"{fn['name']}: a pure function cannot have a test hook")
    if (entry.get("loop") or entry.get("obligations") or entry.get("callers") or entry.get("body_prefix")
            or fn.get("resolved_invariant") or fn.get("resolved_callers")
            or fn.get("resolved_obligations") or fn.get("resolved_loop_obligations") or fn.get("resolved_hints")):
        raise TranslationError(f"{fn['name']}: a pure function cannot carry a loop, obligations, hints or callers")
    if fn["arity"] != 1 or len(fn["body"]) != 1 or fn["body"][0][0] != "return" or len(fn["body"][0][1]) != 1:
        raise TranslationError(f"{fn['name']}: a pure function is exactly one `return e;`")
    return True


def translate_pure(fn, entry):
    ctx = Context(fn, entry)
    params = ", ".join(f"{ctx.names[p]}: {'nat' if p in entry.get('nat', []) else 'int'}" for p in fn["params"])
    rtype = entry["returns"][0][1]
    body = ctx.emit(fn["body"][0][1][0])
    return f"function {entry['dafny_name']}({params}): {rtype}\n{{\n  {body}\n}}"


def translate(ts_source, spec):
    """The Dafny workspace for `ts_source`: the preamble (helpers, selyth:lemma lemmas), then per
    function its translated method — or Dafny function, for pure ones — and the hand-written
    callers, in the order the functions appear in the code."""
    lemmas = extract_lemmas(ts_source)
    contracts, preamble_lines = extract_source_contracts(ts_source)
    functions = Parser(tokenize(ts_source)).program()
    seen = {f["name"] for f in functions}
    declared = spec.get("functions", {})
    if len(seen) != len(functions):
        raise TranslationError("a function is declared twice")
    for name in sorted(seen - set(declared)):
        raise TranslationError(f"function {name} has no entry in the specification")
    for name in sorted(set(declared) - seen):
        raise TranslationError(f"the specification names {name}, which the code does not declare")
    for fn in functions:
        name = fn["name"]
        entry = declared[name]
        src = contracts.get(name, _empty_contract())
        side_loop = entry.get("loop") or {}
        if (side_loop.get("invariant") or side_loop.get("obligations")) and not _has_loop(fn["body"]):
            raise TranslationError(f"{name}: the sidecar declares a loop but the function has none")
        inv = src["invariant"]
        if inv is not None and not _has_loop(fn["body"]):
            raise TranslationError(f"{name}: a selyth:invariant comment needs the function's loop")
        side_inv = side_loop.get("invariant")
        if inv is not None and side_inv and inv != side_inv:
            raise TranslationError(f"{name}: the invariant is given both in a selyth:invariant comment and in "
                                   f"the sidecar, differently")
        fn["resolved_invariant"] = inv or side_inv
        c_ids, side_ids = src["obligations"], entry.get("obligations") or []
        if c_ids and side_ids and c_ids != side_ids:
            raise TranslationError(f"{name}: the contract obligations are given both in selyth:obligation "
                                   f"comments and in the sidecar, differently")
        fn["resolved_obligations"] = c_ids or side_ids
        l_ids, side_l = src["loop_obligations"], side_loop.get("obligations") or []
        if l_ids and not _has_loop(fn["body"]):
            raise TranslationError(f"{name}: a loop obligation marker needs the function's loop")
        if l_ids and side_l and l_ids != side_l:
            raise TranslationError(f"{name}: the loop obligations are given both in selyth:obligation comments "
                                   f"and in the sidecar, differently")
        fn["resolved_loop_obligations"] = l_ids or side_l
        hints, bp = src["hints"], entry.get("body_prefix")
        if hints and bp and "\n".join(hints) != bp.strip():
            raise TranslationError(f"{name}: the proof hints are given both in selyth:hint comments and in the "
                                   f"sidecar body_prefix, differently")
        fn["resolved_hints"] = hints or ([bp] if bp else [])
        cl, side_cl = src["callers"], entry.get("callers")
        if cl and side_cl and "\n".join(cl) != side_cl.strip():
            raise TranslationError(f"{name}: callers are given both in selyth:caller comments and in the "
                                   f"sidecar, differently")
        fn["resolved_callers"] = "\n".join(cl) if cl else side_cl
    side_preamble = spec.get("preamble")
    if preamble_lines and side_preamble and "\n".join(preamble_lines) != side_preamble.strip():
        raise TranslationError("the preamble is given both in selyth:preamble comments and in the sidecar, "
                               "differently")
    resolved_preamble = "\n".join(preamble_lines) if preamble_lines else side_preamble
    pure = {fn["name"]: declared[fn["name"]] for fn in functions if _is_pure(fn, declared[fn["name"]])}
    pure_calls = {name: entry["dafny_name"] for name, entry in pure.items()}
    pure_nat = {entry["dafny_name"] for entry in pure.values() if entry["returns"][0][1] == "nat"}
    for fn in functions:
        fn["pure_calls"] = pure_calls
        fn["pure_nat"] = pure_nat
    all_dafny_names = [declared[f["name"]].get("dafny_name", dafny_name(f["name"])) for f in functions]
    if len(all_dafny_names) != len(set(all_dafny_names)):
        raise TranslationError("two functions share a Dafny name: rename one in the specification")
    uses_abs = any(_uses_helper_in_stmts(fn["body"], ("abs",)) for fn in functions)
    uses_min = any(_uses_helper_in_stmts(fn["body"], ("minmax",)) and
                   _uses_minmax_name(fn["body"], "Min") for fn in functions)
    uses_max = any(_uses_helper_in_stmts(fn["body"], ("minmax",)) and
                   _uses_minmax_name(fn["body"], "Max") for fn in functions)
    if uses_abs and any(entry.get("dafny_name") == "Abs" for entry in declared.values()):
        raise TranslationError("Math.abs is used and a function is already translated as `Abs`: rename one")
    clash = {"Abs", "Min", "Max"} & set(all_dafny_names)
    if (uses_abs or uses_min or uses_max) and clash:
        raise TranslationError(f"a helper name is already used by a function: {sorted(clash)[0]}: rename one")
    preamble = (resolved_preamble or "").rstrip("\n")
    for used, definition in ((uses_abs, ABS_DEF), (uses_min, MIN_DEF), (uses_max, MAX_DEF)):
        if used:
            preamble = f"{preamble}\n\n{definition}" if preamble else definition
    for lemma in lemmas:
        preamble = f"{preamble}\n\n{lemma}" if preamble else lemma
    parts = [spec.get("header", "").rstrip("\n"), preamble]
    for fn in functions:
        entry = declared[fn["name"]]
        block = []
        if entry.get("heading"):
            block.append(f"// ---- {entry['heading']}")
        if fn["name"] in pure:
            block.append(translate_pure(fn, entry))
        else:
            block.append(translate_function(fn, entry))
            callers_text = fn.get("resolved_callers")
            if callers_text:
                block.append("")
                block.append(callers_text.rstrip("\n"))
        parts.append("\n".join(block))
    return "\n\n".join(p for p in parts if p) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source")
    ap.add_argument("--spec", required=True)
    ap.add_argument("-o", "--output")
    args = ap.parse_args(argv)
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        text = translate(Path(args.source).read_text(encoding="utf-8"), spec)
    except TranslationError as e:
        print(f"{args.source}: not translatable: {e}", file=sys.stderr)
        return 2
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="\n") if sys.version_info >= (3, 10) \
            else Path(args.output).write_bytes(text.encode("utf-8"))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
