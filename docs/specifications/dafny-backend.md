# Dafny backend and the evidence report

`tools/dafny_backend.py` turns a Verification IR ([`verification-ir.md`](verification-ir.md)) and a proof workspace into a report that the policy engine ([`policy-engine.md`](policy-engine.md)) decides. It runs Dafny as a subprocess. It is the only part of Core that starts a process or reads a clock; the adapter and the engine stay pure. This document is normative.

```
Obligation Set --ts_adapter--> IR --+
                                    +--dafny_backend--> report --policy_engine--> decision (embedded)
proof workspace (.dfy) -------------+
```

## The proof workspace

A Dafny file written by a human (or proposed by an agent), containing the program under verification or a model of it. Translating TypeScript to Dafny is not part of this milestone, so the workspace is what binds an obligation to code. The binding is a **marker comment** placed where Dafny expects contract clauses (after a method signature, or between a loop header and its body):

```dafny
method Enqueue(l: Queue) returns (result: Queue)
  ensures result.length == l.length + 1      // hand-written clause: stays
  // selyth:obligation OBL-0007
{ ... }
```

The expression of an obligation must be well-formed in its context (`result.length` must resolve there). If it is not, Dafny rejects the file and the obligation is `failed`.

## One obligation at a time

For each unit of the IR the backend writes a copy of the workspace in which that obligation's marker is replaced by its clause (`ensures …`, `invariant …`, `decreases …`) and every other marker is replaced by an empty line, so line numbers still point into the workspace. It runs

`dafny verify --solver-path z3-4.12.1 --resource-limit N --verification-time-limit T`

The consequence is deliberate and conservative: an obligation never benefits from another obligation's clause, so a clause cannot be proved with another one as a hidden hypothesis. It may fail where the pair would verify.

## Outcomes

| Dafny says | Outcome | Evidence soundness |
|---|---|---|
| ≥ 1 verified, 0 errors, exit 0 | `proven` | `sound-for-fragment` |
| any verification error | `failed`, with Dafny's diagnostics | `none` |
| out of resource, or time out, or the process was killed | `timeout` | `none` |
| 0 verified, 0 errors (the clause is attached to nothing Dafny checks) | `unknown` | `none` |
| no summary (parse or resolution error) | `failed`, with the error lines | `none` |
| the workspace has no marker, or more than one, for the obligation | `not-run` | `none` |
| the adapter put the obligation in `unsupported` | `not-run`, with the adapter's reason | `none` |

The evidence kind is always `kernel-proven`: it names the kind of check, and the soundness says whether it reached a proof. `proven` is the only outcome with a soundness claim (the report schema enforces this).

## Trust escapes

Policy blocking rule 5 forbids `assume`, `{:axiom}` and their equivalents. The backend scans the workspace (comments and string contents removed) for `assume`, `expect` (which the verifier treats as an assumption; it is only checked at run time), `{:axiom}`, `{:verify false}`, `{:extern}` and `include`. An `include` is refused because Dafny does not verify included files unless asked, and an included file would be neither scanned nor bound by the report's digests. If any is present, **every** obligation is `failed` with the construct and its line, and Dafny is not trusted for that workspace: a proof that stands on an `assume` is not a proof of the obligation. The scan is lexical; a construct the list does not know is not caught. Body-less lemmas and functions are not scanned for: Dafny warns about them, and the backend runs without `--allow-warnings`, so a clean verification summary with a non-zero exit status is `failed`, with the warnings quoted.

## Tool identity

The IR must be exactly what the adapter lowers from the Obligation Set: the backend recomputes the lowering and refuses (exit 2, no report) an IR that differs in any field, so an edited clause or another adapter version cannot be verified under the set's digests. The IR declares the verification tool it targets. The backend refuses to run if the installed Dafny's version differs (exit 2, no report): a report must name the tool that really produced it. Z3 is pinned by the backend (`4.12.1`, passed with `--solver-path`) and recorded in the toolchain. Dafny's and Z3's own correctness is trusted, not checked, and the report's `limitations` say so.

## The report

- `subject`: the workspace (role `proof-workspace`, digest of its bytes) and the Obligation Set (role `obligation-set`, its digest), so the report covers the set exactly ([coverage](obligation-set-canonicalization.md)).
- `producer`: `local-subprocess`, `outside_agent_boundary: false`; `trust.model: local-structural`. A same-machine run is an assertion, not isolation.
- `cost`: `resource_units` is the sum of Dafny's per-batch resource counts (deterministic for a fixed build and input); `wall_ms` is for humans only.
- `scope` states what was checked, with the resource and time limits, because they decide the `timeout` outcome.
- `decision`: computed by the policy engine on the finished report, never by the backend.
- `report_id` is derived from the digests of the inputs and tool versions, so equal runs have equal ids; `produced_at` is informational.

## Not covered here

Translating TypeScript source to Dafny; running obligations together; a second backend; checking that the workspace is a faithful model of the implementation (the report says it is not checked).
