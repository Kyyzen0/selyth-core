#!/usr/bin/env python3
"""Independent JSON Schema validation for the Selyth specifications.

Validates instances under the JSON Schema Draft 2020-12 dialect with the
`jsonschema` library (pinned in requirements.txt). tools/minischema.py was
written alongside the schemas and implements only the keywords they use; this
module delegates every keyword to a general-purpose implementation of the
specification, so a mistake shared by the schema author and the author's own
validator can no longer pass unnoticed.

Scope: *structural* validation only. A document is checked against its own
schema. Rules that compare values across fields or across documents (digest
equality between policy and decision, obligation coverage, rule applicability)
cannot be expressed in JSON Schema and belong to the policy engine; see
docs/specifications/validation-layers.md.

Beyond validating, this module refuses schemas that rely on constructs a
validator would silently ignore: duplicate object keys, keywords outside the
Draft 2020-12 vocabularies (typos), `format` (annotation-only by default) and
unresolvable local `$ref`s.

Usage: validate.py <schema.json> <instance.json> [<instance.json> ...]
Exit status: 0 all valid, 1 some invalid, 2 usage error or unusable schema.
"""
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError as MetaSchemaError
from jsonschema_specifications import REGISTRY

DIALECT = "https://json-schema.org/draft/2020-12/schema"

# Where sub-schemas live inside a schema object. Everything else (const, enum,
# default, examples, ...) is data and must not be scanned for keywords.
_SCHEMA_VALUED = ("additionalProperties", "items", "contains", "if", "then", "else",
                  "not", "propertyNames", "unevaluatedItems", "unevaluatedProperties",
                  "contentSchema")
_SCHEMA_ARRAYS = ("allOf", "anyOf", "oneOf", "prefixItems")
_SCHEMA_MAPS = ("properties", "patternProperties", "$defs", "dependentSchemas")


class SchemaError(ValueError):
    """The schema file cannot be trusted to mean what it appears to say."""


@dataclass(frozen=True)
class Problem:
    path: str      # location in the instance, e.g. $.obligations[0].evidence.kind
    keyword: str   # schema keyword that failed, e.g. enum
    message: str


def _object_without_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise SchemaError(f"duplicate object key {key!r} (object also has keys "
                              f"{sorted(k for k in out if k != key)}); a JSON parser "
                              "silently keeps only the last one")
        out[key] = value
    return out


def _reject_constant(name):
    raise SchemaError(f"{name} is not valid JSON")


def loads(text):
    """Parse JSON text, rejecting duplicate keys and NaN/Infinity."""
    return json.loads(text, object_pairs_hook=_object_without_duplicates,
                      parse_constant=_reject_constant)


def load_json(path):
    return loads(Path(path).read_text(encoding="utf-8"))


def draft_2020_12_keywords():
    """Keywords defined by the official Draft 2020-12 vocabulary meta-schemas."""
    root = Draft202012Validator.META_SCHEMA
    keywords = set()
    for part in root["allOf"]:
        vocabulary = REGISTRY.get_or_retrieve(urljoin(root["$id"], part["$ref"]))
        keywords.update(vocabulary.value.contents.get("properties", {}))
    return keywords


def _subschemas(schema, path="$"):
    yield path, schema
    if not isinstance(schema, dict):
        return
    for key in _SCHEMA_VALUED:
        if key in schema:
            yield from _subschemas(schema[key], f"{path}.{key}")
    for key in _SCHEMA_ARRAYS:
        for i, sub in enumerate(schema.get(key, [])):
            yield from _subschemas(sub, f"{path}.{key}[{i}]")
    for key in _SCHEMA_MAPS:
        for name, sub in schema.get(key, {}).items():
            yield from _subschemas(sub, f"{path}.{key}.{name}")


def _resolves(root, ref):
    if not ref.startswith("#"):
        return False
    node = root
    for part in filter(None, unquote(ref[1:]).split("/")):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    return True


def lint_schema(schema):
    """Return a list of strings describing constructs a validator would ignore."""
    known = draft_2020_12_keywords()
    problems = []
    if schema.get("$schema") != DIALECT:
        problems.append(f"$: $schema must be {DIALECT}, got {schema.get('$schema')!r}")
    for path, sub in _subschemas(schema):
        if not isinstance(sub, dict):
            continue
        for key in sub:
            if key not in known:
                problems.append(f"{path}: unknown keyword {key!r}")
        if "format" in sub:
            problems.append(f"{path}: 'format' is annotation-only under Draft 2020-12 "
                            "and is not asserted by this validator")
        if "$ref" in sub and not _resolves(schema, sub["$ref"]):
            problems.append(f"{path}: $ref {sub['$ref']!r} does not resolve inside the schema")
    return problems


def load_schema(path):
    """Load a schema, insisting that it is a valid, unambiguous Draft 2020-12 schema."""
    schema = load_json(path)
    try:
        Draft202012Validator.check_schema(schema)
    except MetaSchemaError as exc:
        raise SchemaError(f"{path}: not a valid Draft 2020-12 schema: {exc.message}") from exc
    problems = lint_schema(schema)
    if problems:
        raise SchemaError(f"{path}: " + "; ".join(problems))
    return schema


def _render_path(parts):
    out = "$"
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def validate(instance, schema):
    """Return the Problems that make `instance` invalid under `schema` (empty if valid)."""
    errors = Draft202012Validator(schema).iter_errors(instance)
    problems = [Problem(_render_path(e.absolute_path), e.validator, e.message) for e in errors]
    return sorted(problems, key=lambda p: (p.path, p.keyword, p.message))


def main(argv):
    if len(argv) < 3:
        print("usage: validate.py <schema.json> <instance.json> [<instance.json> ...]")
        return 2
    try:
        schema = load_schema(argv[1])
    except (SchemaError, OSError, json.JSONDecodeError) as exc:
        print(f"[SCHEMA] {exc}")
        return 2
    rc = 0
    for name in argv[2:]:
        try:
            problems = validate(load_json(name), schema)
        except (SchemaError, OSError, json.JSONDecodeError) as exc:
            print(f"[ERROR] {name}: {exc}")
            return 2
        print(f"[{'FAIL' if problems else 'PASS'}] {name}")
        for p in problems:
            print(f"        {p.path}: {p.message} ({p.keyword})")
        rc |= 1 if problems else 0
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
