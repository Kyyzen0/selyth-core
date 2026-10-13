#!/usr/bin/env python3
"""TypeScript adapter: lower an Obligation Set to the Verification IR (L2).

Specified in docs/specifications/verification-ir.md. `lower` is a pure function of
the Obligation Set and the declared verification-tool version: it reads no file,
clock, host or randomness, so regenerating the IR gives a byte-identical file. Its
whole input is what the reproducibility key digests, and nothing else may enter.

"TypeScript" names the source language whose expression syntax the adapter accepts
(a pure subset); the adapter itself is a Python tool like the rest of tools/.

Usage:
  ts_adapter.py lower <obligation-set.json> --backend-version <version>
Exit status: 0 ok, 1 invalid Obligation Set, 2 usage error or unreadable input.
"""
import hashlib
import json
import re
import sys

import obligation_set as oset

ADAPTER_NAME = "selyth-ts-adapter"
# Changing what `lower` emits for any input requires changing this value.
ADAPTER_VERSION = "0.2.0"
IR_VERSION = "0.2.0"
BACKEND_NAME = "dafny"

CLAUSES = {"precondition": "requires", "postcondition": "ensures",
           "invariant": "invariant", "termination": "decreases"}

_TOKEN = re.compile(r"\s*(==>|<=|>=|==|!=|&&|\|\||[-+*/%<>!().]|[A-Za-z_][A-Za-z0-9_]*|[0-9]+)")


class ExpressionError(ValueError):
    """The expression is outside the supported subset."""


def _tokenize(text):
    tokens, pos = [], 0
    text = text.rstrip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise ExpressionError(f"unsupported character {text[pos:].lstrip()[:1]!r}")
        tokens.append(match.group(1))
        pos = match.end()
    return tokens


class _Parser:
    COMPARE = ("<", "<=", ">", ">=", "==", "!=")

    def __init__(self, tokens):
        self.tokens, self.i = tokens, 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self):
        token = self.peek()
        self.i += 1
        return token

    def parse(self):
        if not self.tokens:
            raise ExpressionError("empty expression")
        node = self.implies()
        if self.peek() is not None:
            raise ExpressionError(f"unexpected {self.peek()!r}")
        return node

    def implies(self):  # right associative
        left = self.disjunction()
        if self.peek() == "==>":
            self.take()
            return ("bin", "==>", left, self.implies())
        return left

    def _left(self, operators, operand):
        node = operand()
        while self.peek() in operators:
            op = self.take()
            node = ("bin", op, node, operand())
        return node

    def disjunction(self):
        return self._left(("||",), self.conjunction)

    def conjunction(self):
        return self._left(("&&",), self.comparison)

    def comparison(self):
        operands, ops = [self.additive()], []
        while self.peek() in self.COMPARE:
            ops.append(self.take())
            operands.append(self.additive())
        if len(ops) > 1 and not all(op in ("<", "<=") for op in ops):
            raise ExpressionError("only chains of < and <= are supported")
        if not ops:
            return operands[0]
        return ("cmp", ops, operands)

    def additive(self):
        return self._left(("+", "-"), self.multiplicative)

    def multiplicative(self):
        return self._left(("*", "/", "%"), self.unary)

    def unary(self):
        if self.peek() in ("!", "-"):
            op = self.take()
            return ("un", op, self.unary())
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.peek() == ".":
            self.take()
            name = self.take()
            if name is None or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ExpressionError("member access needs an identifier")
            node = ("member", node, name)
        return node

    def primary(self):
        token = self.take()
        if token == "(":
            node = self.implies()
            if self.take() != ")":
                raise ExpressionError("missing ')'")
            return node
        if token is None:
            raise ExpressionError("unexpected end of expression")
        if token in ("true", "false"):
            return ("bool", token)
        if re.fullmatch(r"[0-9]+", token):
            return ("int", token.lstrip("0") or "0")  # textual: int() is length-limited
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            return ("id", token)
        raise ExpressionError(f"unexpected {token!r}")


_BINARY_PRECEDENCE = {"==>": 1, "||": 2, "&&": 3, "+": 5, "-": 5, "*": 6, "/": 6, "%": 6}


def _precedence(node):
    kind = node[0]
    if kind == "bin":
        return _BINARY_PRECEDENCE[node[1]]
    return {"cmp": 4, "un": 7}.get(kind, 8)


def _print(node):
    kind = node[0]
    if kind in ("id", "int", "bool"):
        return node[1]
    if kind == "member":
        base = _print(node[1]) if _precedence(node[1]) >= 8 else f"({_print(node[1])})"
        return f"{base}.{node[2]}"
    if kind == "un":
        operand = node[2]
        text = _print(operand)
        if _precedence(operand) < 7 or (node[1] == "-" and operand[:2] == ("un", "-")):
            text = f"({text})"
        return node[1] + text
    if kind == "cmp":
        parts = [_print(o) if _precedence(o) >= 5 else f"({_print(o)})" for o in node[2]]
        out = parts[0]
        for op, part in zip(node[1], parts[1:]):
            out += f" {op} {part}"
        return out
    _, op, left, right = node
    prec = _BINARY_PRECEDENCE[op]
    right_assoc = op == "==>"
    lt, rt = _print(left), _print(right)
    if _precedence(left) < prec or (right_assoc and _precedence(left) == prec):
        lt = f"({lt})"
    if _precedence(right) < prec or (not right_assoc and _precedence(right) == prec):
        rt = f"({rt})"
    return f"{lt} {op} {rt}"


def canonical_expression(text):
    """Parse an expression of the supported subset and print it in canonical form."""
    return _print(_Parser(_tokenize(text)).parse())


def _lower_obligation(obligation):
    """Return ('unit', clause) or ('unsupported', reason)."""
    kind = obligation["kind"]
    keyword = CLAUSES.get(kind)
    if keyword is None:
        return "unsupported", f"kind {kind!r} cannot be lowered to a {BACKEND_NAME} clause"
    parts = obligation["statement"].split(None, 1)
    if not parts or parts[0] != keyword:
        return "unsupported", f"a {kind} statement must start with {keyword!r}"
    if len(parts) == 1:
        return "unsupported", f"no expression after {keyword!r}"
    try:
        return "unit", {"keyword": keyword, "expression": canonical_expression(parts[1])}
    except ExpressionError as exc:
        return "unsupported", f"expression outside the supported subset: {exc}"


def reproducibility_key(set_digest, backend_version):
    inputs = {"adapter": {"name": ADAPTER_NAME, "version": ADAPTER_VERSION},
              "backend": {"name": BACKEND_NAME, "version": backend_version},
              "ir_version": IR_VERSION, "obligation_set_digest": set_digest}
    text = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def lower(document, backend_version):
    """Lower a (schema-valid) Obligation Set to the Verification IR; return it as a dict."""
    canonical = oset.canonical_form(document)
    digest = oset.set_digest(document)
    digests = oset.obligation_digests(document)
    units, unsupported = [], []
    for obligation in canonical["obligations"]:  # already sorted by obligation_id
        oid = obligation["obligation_id"]
        outcome, value = _lower_obligation(obligation)
        # Everything a report entry needs travels in the IR, so L3 never rereads L1.
        entry = {"obligation_id": oid, "obligation_digest": digests[oid], "kind": obligation["kind"],
                 "statement": obligation["statement"],
                 "required_evidence_kind": obligation["required_evidence_kind"]}
        if "intent_statement" in obligation:
            entry["intent_statement"] = obligation["intent_statement"]
        if outcome == "unit":
            units.append({**entry, "clause": value,
                          "context": {"assumptions": obligation["assumptions"],
                                      "affected_components": obligation["affected_components"]}})
        else:
            unsupported.append({**entry, "affected_components": obligation["affected_components"],
                                "reason": value})
    return {"selyth_ir_version": IR_VERSION,
            "reproducibility_key": reproducibility_key(digest, backend_version),
            "obligation_set": {"digest": digest, "set_id": canonical["set_id"],
                               "specification_version": canonical["specification_version"]},
            "adapter": {"name": ADAPTER_NAME, "version": ADAPTER_VERSION},
            "backend": {"name": BACKEND_NAME, "version": backend_version},
            "units": units, "unsupported": unsupported}


def render(ir):
    """The only serialisation of an IR: indented, sorted keys, one final newline."""
    return json.dumps(ir, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv):
    if len(argv) != 5 or argv[1] != "lower" or argv[3] != "--backend-version" or not argv[4]:
        print("usage: ts_adapter.py lower <obligation-set.json> --backend-version <version>")
        return 2
    try:
        document = oset.load_obligation_set(argv[2])
    except oset.ObligationSetError as exc:
        print(f"[FAIL] {argv[2]}: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(render(lower(document, argv[4])).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
