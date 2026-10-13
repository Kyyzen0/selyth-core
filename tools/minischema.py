#!/usr/bin/env python3
"""Minimal JSON Schema validator covering exactly the draft 2020-12 subset used
by the Selyth schemas: type, required, properties, additionalProperties, items,
enum, const, pattern, minItems, minimum, allOf, if/then/else, $ref->#/$defs.

Deliberately scoped: it is a verification tool for these two schemas, not a
general-purpose validator. Unknown keywords are ignored rather than silently
treated as satisfied-by-default; `--strict` reports them so a schema can never
quietly rely on a construct this file does not implement.
"""
import json
import re
import sys
from pathlib import Path

SUPPORTED = {
    "$schema", "$id", "$defs", "$ref", "title", "description", "type", "required",
    "properties", "additionalProperties", "items", "enum", "const", "pattern",
    "minItems", "minLength", "minProperties", "minimum", "allOf", "if", "then", "else", "examples", "default",
    "propertyNames", "maxItems",
}

TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "number": (int, float), "null": type(None),
}


def _is_type(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean":
        return isinstance(v, bool)
    return isinstance(v, TYPES[t])


def validate(inst, schema, root=None, path="$", errs=None, strict=False, unknown=None):
    root = root or schema
    errs = errs if errs is not None else []
    if unknown is None:
        unknown = set()

    for k in schema:
        if k not in SUPPORTED:
            unknown.add(f"{path}:{k}")

    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            errs.append(f"{path}: unsupported $ref {ref}")
        else:
            name = ref.split("/")[-1]
            target = root.get("$defs", {}).get(name)
            if target is None:
                errs.append(f"{path}: unresolvable $ref {ref}")
            else:
                validate(inst, target, root, path, errs, strict, unknown)
        return errs

    if "type" in schema:
        ts = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(inst, t) for t in ts):
            errs.append(f"{path}: expected type {'/'.join(ts)}, got {type(inst).__name__}")
            return errs

    if "const" in schema and inst != schema["const"]:
        errs.append(f"{path}: expected const {schema['const']!r}, got {inst!r}")
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} not in enum {schema['enum']}")
    if "pattern" in schema and isinstance(inst, str):
        if not re.search(schema["pattern"], inst):
            errs.append(f"{path}: {inst!r} does not match /{schema['pattern']}/")
    if "minimum" in schema and isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if inst < schema["minimum"]:
            errs.append(f"{path}: {inst} < minimum {schema['minimum']}")
    if "minLength" in schema and isinstance(inst, str) and len(inst) < schema["minLength"]:
        errs.append(f"{path}: length {len(inst)} < minLength {schema['minLength']}")
    if "minItems" in schema and isinstance(inst, list) and len(inst) < schema["minItems"]:
        errs.append(f"{path}: {len(inst)} items < minItems {schema['minItems']}")
    if "maxItems" in schema and isinstance(inst, list) and len(inst) > schema["maxItems"]:
        errs.append(f"{path}: {len(inst)} items > maxItems {schema['maxItems']}")

    if isinstance(inst, dict):
        if "minProperties" in schema and len(inst) < schema["minProperties"]:
            errs.append(f"{path}: {len(inst)} properties < minProperties {schema['minProperties']}")
        for r in schema.get("required", []):
            if r not in inst:
                errs.append(f"{path}: missing required property {r!r}")
        props = schema.get("properties", {})
        for k, v in inst.items():
            if k in props:
                validate(v, props[k], root, f"{path}.{k}", errs, strict, unknown)
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: additional property {k!r} not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(v, schema["additionalProperties"], root, f"{path}.{k}",
                         errs, strict, unknown)
        if "propertyNames" in schema:
            for k in inst:
                validate(k, schema["propertyNames"], root, f"{path}.{k}(name)",
                         errs, strict, unknown)

    if isinstance(inst, list) and "items" in schema:
        for i, v in enumerate(inst):
            validate(v, schema["items"], root, f"{path}[{i}]", errs, strict, unknown)

    for sub in schema.get("allOf", []):
        validate(inst, sub, root, path, errs, strict, unknown)

    if "if" in schema:
        cond = []
        validate(inst, schema["if"], root, path, cond, strict, unknown)
        branch = "then" if not cond else "else"
        if branch in schema:
            validate(inst, schema[branch], root, path, errs, strict, unknown)

    return errs


def main():
    if len(sys.argv) < 3:
        print("usage: minischema.py <schema.json> <instance.json> [...]")
        return 2
    schema = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    rc = 0
    for f in sys.argv[2:]:
        inst = json.loads(Path(f).read_text(encoding="utf-8"))
        unknown = set()
        errs = validate(inst, schema, unknown=unknown)
        status = "PASS" if not errs else "FAIL"
        print(f"[{status}] {f}")
        for e in errs:
            print(f"        {e}")
        rc |= 0 if not errs else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
