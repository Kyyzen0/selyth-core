#!/usr/bin/env python3
"""Translate a pure subset of TypeScript into Dafny, so a proof workspace is generated from the
code under verification instead of being written by hand beside it.

The subset (anything else is refused with a located error, never guessed):

  * `export function f(a: number, ...): number | [number, ...] { ... }`; a trailing optional
    parameter of function type (a test hook such as `observe?: (i: number, t: number) => void`)
    is dropped together with the statements that call it;
  * statements: `let|const x = e;`, `x = e;`, `x += e;`, `x -= e;`, `x++;`, `return e;`,
    `return [e, e];`, `if (e) s [else s]`, `while (e) { ... }`, `while (true) { ...; if (e) break; ... }`,
    `for (let i = K; i < e; i++) { ... }` (K a non-negative literal, `<` or `<=`, only `i++` as the
    update — desugared to a `while` with an explicit counter), blocks;
  * expressions: integer literals, identifiers, `+ - * % / === !== < <= > >= && || !`, unary `-`,
    parentheses, `c ? a : b`, `Math.floor(a / b)` as the only division, and `Math.abs(e)` (emitted as
    an `Abs` helper on `int`; exact because `nat` is a subtype of `int`).

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

`nat` lists the parameters that are non-negative, `returns` names and types the results,
`obligations` become the `// selyth:obligation` marker, and `callers`, `body_prefix`, `preamble`
are hand-written scaffolding that the translator copies. The translation itself is the method
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
            self.next()
            name = self.ident()
            if self.peek() == ":":
                self.next()
                self.number_type()
            self.eat("=")
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
            raise TranslationError("only Math.floor and Math.abs are in the subset")
        if kind == "id":
            self.next()
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
        self._prepare(fn["body"])
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
                elif s[0] == "assign":
                    updates.setdefault(s[1], []).append((s[2], s[3]))
                elif s[0] == "if":
                    walk(s[2])
                    walk(s[3] or [])
                elif s[0] in ("while",):
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
        if kind == "if":
            return self.is_nonneg(e[2], known) and self.is_nonneg(e[3], known)
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
    if spec.get("obligations"):
        out.append(f"{indent}// selyth:obligation {' '.join(spec['obligations'])}")
    out.append("{")
    for p in ctx.reassigned_params:
        out.append(f"{indent}var {ctx.names[p]} := {ctx.names[p]};")
    if spec.get("body_prefix"):
        out.append(f"{indent}{spec['body_prefix']}")
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
            elif kind == "while":
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
        spec_loop = spec.get("loop")
        if spec_loop is None or not spec_loop.get("invariant"):
            raise TranslationError(f"{fn['name']}: a loop needs an invariant in the specification")
        lines = [f"{pad}while {head}", f"{pad}{indent}invariant {spec_loop['invariant']}"]
        if spec_loop.get("obligations"):
            lines.append(f"{pad}{indent}// selyth:obligation {' '.join(spec_loop['obligations'])}")
        lines.append(f"{pad}{{")
        lines += stmts(body, depth + 1)
        lines.append(f"{pad}}}")
        return lines

    out += stmts(fn["body"], 1, last_of_function=True)
    out.append("}")
    return "\n".join(out)


def _uses_abs_in_expr(e):
    if not isinstance(e, tuple):
        return False
    if e[0] == "abs":
        return True
    return any(_uses_abs_in_expr(x) for x in e[1:] if isinstance(x, tuple))


def _uses_abs_in_stmts(stmts):
    for s in stmts:
        if s[0] == "let" and _uses_abs_in_expr(s[2]):
            return True
        if s[0] == "return" and any(_uses_abs_in_expr(v) for v in s[1]):
            return True
        if s[0] == "assign" and _uses_abs_in_expr(s[3]):
            return True
        if s[0] == "if" and (_uses_abs_in_expr(s[1]) or _uses_abs_in_stmts(s[2])
                             or _uses_abs_in_stmts(s[3] or [])):
            return True
        if s[0] == "while" and (_uses_abs_in_expr(s[1]) or _uses_abs_in_stmts(s[2])):
            return True
        if s[0] == "block" and _uses_abs_in_stmts(s[1]):
            return True
    return False


def translate(ts_source, spec):
    """The Dafny workspace for `ts_source`: the preamble, then per function its translated method
    and the hand-written callers, in the order the functions appear in the code."""
    functions = Parser(tokenize(ts_source)).program()
    seen = {f["name"] for f in functions}
    declared = spec.get("functions", {})
    if len(seen) != len(functions):
        raise TranslationError("a function is declared twice")
    for name in sorted(seen - set(declared)):
        raise TranslationError(f"function {name} has no entry in the specification")
    for name in sorted(set(declared) - seen):
        raise TranslationError(f"the specification names {name}, which the code does not declare")
    uses_abs = any(_uses_abs_in_stmts(fn["body"]) for fn in functions)
    if uses_abs and any(entry.get("dafny_name") == "Abs" for entry in declared.values()):
        raise TranslationError("Math.abs is used and a function is already translated as `Abs`: rename one")
    preamble = spec.get("preamble", "").rstrip("\n")
    if uses_abs:
        preamble = f"{preamble}\n\n{ABS_DEF}" if preamble else ABS_DEF
    parts = [spec.get("header", "").rstrip("\n"), preamble]
    for fn in functions:
        entry = declared[fn["name"]]
        block = []
        if entry.get("heading"):
            block.append(f"// ---- {entry['heading']}")
        block.append(translate_function(fn, entry))
        if entry.get("callers"):
            block.append("")
            block.append(entry["callers"].rstrip("\n"))
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
