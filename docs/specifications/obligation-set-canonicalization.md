# Obligation Set canonicalisation and digest

An Obligation Set is frozen by a digest that every report names. The digest must not move when a file is reformatted or reordered, and must move when the meaning changes. This document defines the single function that gives that property. The reference implementation is `tools/obligation_set.py`; the rules below are normative and the implementation follows them, not the other way round.

The schema is [`obligation-set-schema.json`](obligation-set-schema.json). The rules are versioned with it by `selyth_obligation_set_version`: changing either is changing that value, so a digest is never compared across rule sets.

## Why the format is restricted

The schema allows only objects, arrays and strings. There is no number, boolean or null, so no number needs a canonical spelling (the hardest part of JSON canonicalisation, RFC 8785 §3.2.2.3), and "absent" is the only way to say "none". The rules below are otherwise those of RFC 8785 (JCS), with two additions: Unicode normalisation, and set semantics for arrays.

## Procedure

Input is UTF-8 text, without byte-order mark.

1. **Parse strictly.** Reject invalid JSON, duplicate object keys (a parser would silently keep one), `NaN`/`Infinity`, and any string containing a lone surrogate (it has no UTF-8 encoding).
2. **Validate** against the schema. Nothing is canonicalised before it is known to be well formed.
3. **Normalise every string, keys included, to Unicode NFC.** A statement typed with a composed `é` and one with `e` + combining accent are the same text and must hash the same. Nothing else in a string is changed: **whitespace and case inside `statement`, `intent_statement`, assumptions and components are significant**, because they may change meaning.
4. **Sort every array.** Every array in an Obligation Set is a *set*; its order is not meaning.

   | Array | Sorted by |
   |---|---|
   | `obligations` | `obligation_id` |
   | `assumptions` | the string itself |
   | `affected_components` | the string itself |

   Strings are compared by Unicode code point, which is also UTF-8 byte order. After normalisation and sorting, a duplicate is an error (two obligations sharing an `obligation_id`; two equal assumptions or components, including ones equal only after NFC). JSON Schema can check neither the key uniqueness nor the post-NFC uniqueness, so the tool does.
5. **Sort object keys** by code point. Keys are fixed ASCII names by the schema, so this coincides with JCS's UTF-16 ordering.
6. **Serialise** with no whitespace, `,` and `:` as separators, and these escapes only: `\"`, `\\`, `\b`, `\f`, `\n`, `\r`, `\t`, and `\u00xx` (lowercase hex) for the other control characters below U+0020. Every other character, including U+007F and U+2028, is written as raw UTF-8. No trailing newline.

The result is the **canonical form**.

## Semantic checks at load

`parse_obligation_set` refuses, after schema validation and canonicalisation, what the format rules above cannot express: duplicate `obligation_id`s after NFC, duplicate assumptions or components after NFC, lone surrogates, and **vacuous statements** — an expression that is the literal `true` or compares a name with itself (`x == x`, `x <= x`, `x >= x`, optionally parenthesised) proves nothing and is rejected with the obligation id (risk R10). The lint is deliberately syntactic and conservative: anything else is left to the verifier, and it must never refuse a meaningful statement. In-memory documents that skip `parse_obligation_set` (the tests that weaken an obligation on purpose) are still caught by the lock at decision time.

## Digests

- **Set digest** = `sha256:` + lowercase hex SHA-256 of the canonical form of the whole document. This is the digest a report lists under `subject` with role `obligation-set`.
- **Obligation digest** = the same function applied to one obligation object alone (steps 3 to 6). This is the `obligation_digest` of a report entry. It deliberately does not include set-level fields, so an unchanged obligation keeps its digest when the set is re-versioned; the set digest still changes.

Both cover the whole object, `intent_statement` included. Editing intent therefore invalidates reports of that obligation. This is conservative: it errs towards stale rather than towards silently reusing a result. Whether intent should be excluded from the obligation digest is an open question for the Obligation Set's next revision.

## Test vector

Input (key order, indentation and array order are deliberately arbitrary):

```json
{
  "obligations": [
    { "affected_components": ["b.ts", "a.ts"], "assumptions": [],
      "statement": "ensures x > 0", "kind": "postcondition",
      "required_evidence_kind": "kernel-proven", "obligation_id": "OBL-1" }
  ],
  "specification_version": "1.0.0",
  "set_id": "demo",
  "selyth_obligation_set_version": "0.1.0"
}
```

Canonical form (one line, shown wrapped here only for reading):

```
{"obligations":[{"affected_components":["a.ts","b.ts"],"assumptions":[],"kind":"postcondition",
"obligation_id":"OBL-1","required_evidence_kind":"kernel-proven","statement":"ensures x > 0"}],
"selyth_obligation_set_version":"0.1.0","set_id":"demo","specification_version":"1.0.0"}
```

`tools/test-obligation-set.py` contains this vector and fails if the implementation disagrees with it.

## Coverage: report against Obligation Set

Coverage compares two documents, so it is semantic validation (see [`validation-layers.md`](validation-layers.md)), implemented by `tools/obligation_set.py cover`. A report covers an Obligation Set only if all hold:

1. The report has exactly one `subject` with role `obligation-set`, and its digest is the set digest.
2. Every obligation of the set appears in the report **exactly once**, matched by `obligation_id`.
3. The report contains no obligation absent from the set.
4. For each matched obligation, `obligation_digest` equals the obligation digest and `specification_version` equals the set's.
5. For each matched obligation, every field the report echoes from the set — `kind`, `statement`, `intent_statement`, `required_evidence_kind`, `affected_components` — equals the set's (strings compared after NFC; `affected_components` compared as a set, order-free). The obligation digest already binds these fields, so this rule cannot catch anything the digest misses; it exists so that a forged echo is named field by field instead of only as a digest mismatch.

An obligation that was not run still appears in the report, with outcome `not-run`: omission is never a way to skip.

## Binding to code: components and markers

`affected_components` are paths **relative to the Obligation Set file's directory**. `obligation_set.py check-components <set.json> [base-dir]` fails (exit 1) naming every component that does not exist under the base directory (default: the set file's own): a component that is gone means the obligation is detached from the code it claims to govern (failure mode F2), and that must be visible, not silent. The Dafny backend enforces the other direction: it refuses (exit 2, no report) a proof workspace that carries a marker for an obligation id the set does not declare, and reports an obligation with no marker (or several) as `not-run` ([`dafny-backend.md`](dafny-backend.md)). Together: every obligation is bound to exactly one place in the code, and every marked place in the code belongs to a declared obligation.

## Not covered here

Ambiguity records and structured assumption records (`docs/architecture/core-components.md`) are not part of this version; assumptions are strings on the obligation. Adding them changes `selyth_obligation_set_version`.
