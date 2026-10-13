# Verification IR and the TypeScript adapter

The Verification IR is layer L2 of `docs/architecture/intent-and-ir.md`: the only thing a backend sees, derived from an Obligation Set (L1), disposable, and regenerated every run. Its schema is [`verification-ir-schema.json`](verification-ir-schema.json). `tools/ts_adapter.py` produces it. This document is normative.

## Reproducibility

Regenerating the IR from the same inputs must give a **byte-identical** file (rule 2 of the layer boundaries). The inputs are exactly these, and the IR depends on nothing else:

| Input | Where it appears |
|---|---|
| Obligation Set digest ([canonicalisation](obligation-set-canonicalization.md)) | `obligation_set.digest` |
| Adapter identity and version | `adapter` |
| Verification tool identity and version (the backend the IR targets) | `backend` |
| IR format version | `selyth_ir_version` |

`reproducibility_key` = `sha256:` + SHA-256 of the canonical JSON (sorted keys, no whitespace, UTF-8) of `{"adapter": …, "backend": …, "ir_version": …, "obligation_set_digest": …}`. Two files with the same key are byte-identical; any change of an input changes the key. Output is `json.dumps(indent=2, sort_keys=True, ensure_ascii=False)` plus a final newline. No timestamp, path, host name or random value may appear.

**The adapter version is part of the contract.** Changing what the adapter emits for any input without changing `ADAPTER_VERSION` makes two IR files with the same key differ, which is the non-determinism this rule forbids. The test pins a golden IR for the demonstration set and fails with that instruction if the output moves while the version does not.

The backend version is **declared** by the caller (`--backend-version`), never read from the machine, so the IR is the same wherever it is generated. Whether the installed tool matches the declaration is checked where the tool is run (the backend, not the adapter).

## Lowering

Each obligation is lowered to one clause of the backend, chosen by its `kind`:

| Obligation kind | Clause |
|---|---|
| `precondition` | `requires` |
| `postcondition` | `ensures` |
| `invariant` | `invariant` |
| `termination` | `decreases` |
| `permission`, `acceptance-criterion`, `resource-bound` | not lowerable by this adapter |

The statement must start with its clause keyword followed by an expression in the supported subset (`ensures result.length > 0`). The expression is parsed and printed back in a canonical form (single spaces around binary operators, parentheses only where precedence needs them), so spelling does not change the IR.

**Supported expression subset** (pure, no calls, no indexing, no assignment): identifiers; member access `a.b.c`; non-negative integer literals; `true`, `false`; unary `!` and `-`; binary `* / %`, `+ -`, comparisons `< <= > >= == !=` (chains of `<` and `<=`, as in `0 <= size <= capacity`), `&&`, `||`, `==>` (right associative); parentheses.

An obligation that is not lowerable (kind, missing keyword, expression outside the subset) goes to `unsupported` with a reason. **Nothing is dropped:** every obligation of the set is in `units` or in `unsupported`, exactly once, with the digest the set gives it. A backend reports an unsupported obligation as `not-run`, which the policy engine turns into its `on_unknown_evidence` action.

Every unit and every unsupported entry also carries `kind`, `statement`, `required_evidence_kind` and `intent_statement` (absent when none), because a report entry needs them and layer rule 4 forbids L3 from carrying what L2 did not produce: the verification engine never rereads the Obligation Set. Unsupported entries also carry `affected_components`. Integer literals are normalised textually (leading zeros dropped), never through a bounded integer type.

Assumptions travel in `context` for the report. They are never lowered to `assume`.

## Implementations

Two implementations exist and must agree byte for byte: `tools/ts_adapter.py` (Python, the reference) and `ts/` (TypeScript on Node). The TypeScript tests run both on the same inputs and compare their output, so a divergence in canonicalisation, ordering (code point, not UTF-16 code unit), Unicode normalisation or integer handling fails the build. Changing the output of either requires changing `ADAPTER_VERSION` in both.

## Not covered here

Translating a TypeScript implementation to the backend's language (the LemmaScript step) is not part of this milestone: the determinism of the lowering is, and it is the property everything after it relies on.
