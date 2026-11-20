# Selyth

**The human defines. The agent proposes. A verifier the agent cannot influence decides.**

Selyth is an experimental open-source project on trustworthy AI-assisted software development. Its long-term ambition is a universal programming language designed jointly for human developers and AI agents. Its first deliverable is not that language — it is the verification foundation such a language would need, built now, against an existing prover, on an ordinary machine, at zero marginal cost.

**Status:** The Core prototype (milestones M0 to M5 of [`docs/roadmap/core-v0.1.md`](docs/roadmap/core-v0.1.md)) is implemented and tested; the language is not, and the evaluation (M6) is prepared (a protocol and a tested harness, [`docs/evaluation/protocol.md`](docs/evaluation/protocol.md)) but not run. Its launch parameters are fixed ([`ADR-004`](docs/decisions/ADR-004-m6-launch-decisions.md)) for **M6-Lite**, a zero-marginal-cost pilot: Claude Code with `claude-sonnet-4-6`, 6 real tasks, 2 arms, 1 fresh run per arm and task (12 agent runs), at most 20 minutes and 15 model calls per run, procedural isolation. It is a feasibility and directional study, not a statistical demonstration; the full evaluation (10 tasks, 3 repetitions, 60 runs) is a distinct future one, not started. It still needs the real task set, which does not exist. A pure subset of TypeScript is translated to Dafny ([`docs/specifications/ts-to-dafny.md`](docs/specifications/ts-to-dafny.md)); code outside it is refused. The architecture is still a design: every architectural choice is recorded with an explicit status in [`docs/architecture/decision-register.md`](docs/architecture/decision-register.md), so that no open question silently becomes a decision.

> **Where things stand:** run `make test` (see below). What is done and what remains is in [`docs/roadmap/next-tasks-for-claude.md`](docs/roadmap/next-tasks-for-claude.md); the audit that preceded the implementation is [`docs/audit-phase-a.md`](docs/audit-phase-a.md), kept as written.

## The four stages

| Stage | Deliverable | Depends on the language? |
|---|---|---|
| **1 — Selyth Core** | Verification framework: obligations, evidence reports, acceptance policy, one backend adapter | No |
| **2 — Selyth Intent** | Structured intention and specification system, with ambiguity detection | No |
| **3 — Selyth Language** | An original language with its own syntax, type system, semantics and compiler | Yes |
| **4 — Selyth Universal** | Cross-platform targets, libraries, tooling, workflows | Yes |

The independence of Stage 1 from Stage 3 is structural, not aspirational: **Core contains no execution intermediate representation and no type system of its own.** Core reasons about obligations and evidence. It never needs to know what a program does, only what was claimed about it and what was shown. The language arrives later as one more producer of obligations, and nothing in Core changes when it does.

## What the verification foundation does

A human writes down obligations — preconditions, postconditions, invariants, termination conditions, permissions, acceptance criteria — and commits them. An agent proposes an implementation. A backend checks the obligations. The result is a machine-readable report that records, per obligation, what was checked, by which tool version, over what scope, at what cost, and under what assumptions. A deterministic policy engine — a total function from (policy, report) to a decision — turns that report into `accepted`, `rejected`, or `needs-human`.

Three properties do the work:

**Reports cannot overclaim their evidence.** The JSON Schema encodes the ceilings structurally. An obligation with outcome `proven` requires a machine-checked evidence kind, so a language model cannot produce a proof. A timed-out check may carry no soundness claim at all. A local subprocess may not claim it ran outside the agent's boundary. Every conditional rule of the schemas is exercised by a fixture that must be rejected for the intended reason, and the suite fails if a rule has none. Overclaiming, stale-policy reuse and self-attestation all fail validation.

**Evidence strength, outcome and provenance are three separate axes.** Whether something was proven, how well it was proven, and who proved it are recorded independently. Collapsing them into a single status is the most common error in this design space and the one that turns a gate into a label.

**The agent cannot silently weaken the policy.** Policies are content-addressed and pinned by digest in every report they decide, so a change makes prior reports visibly stale. The additions-only invariant on proof artefacts detects tampering with no trusted third party. Suppression is forbidden in V0.1.

## Why the language is not being built yet

Phase C investigated this directly and the answer was not reassuring for the original plan. At least eighteen projects already claim the "AI-native language with first-class intent" position, and every one is unadopted — between zero and ninety-eight stars, almost all single-author, one archived. The two with real institutional weight, from ETH Zürich and from a team shipping into a service at roughly 150 million daily users, independently concluded that they should not build a language at all, and said so in their own design documents.

The verification substrate is also not a gap. Dafny reaches 82.2% LLM success on the largest vericoding benchmark and improved from 68% to 96% in about a year. A new language begins at zero, with roughly 779 GitHub results against more than five million for Java or Python.

The reasoning is in [`docs/research/prior-art.md`](docs/research/prior-art.md), and the decision in [`docs/decisions/ADR-001-core-first.md`](docs/decisions/ADR-001-core-first.md).

## What this project does not do

It does not claim to solve the intent gap. No tool in existence checks that a formal specification matches what a human actually meant, and Selyth does not close that gap in V0.1 — it records the distance honestly and escalates the cases where it matters.

It does not claim security on a single machine. [`docs/architecture/security-model.md`](docs/architecture/security-model.md) states the limitation precisely: V0.1 provides **structural integrity, not isolation**. A local subprocess is a reliability boundary, not a security boundary, and a report produced on the same machine by the same user is an assertion. The report schema forces that admission rather than hiding it.

It does not claim deterministic verification. The solver is seed-dependent; one measured project saw a 30% failure rate over 100 runs. What is deterministic is the policy evaluation over a fixed report, which is a total function over data.

It does not claim repository-scale verification. The strongest frontier agent fully solves 27 of 43 multi-module instances on the most demanding public benchmark, with 10 unsolved by any configuration.

## Repository layout

```
MANIFESTO.md                          Founding principles, revised
README.md                             This file
CONTRIBUTING.md                       How to run the tests and propose a change
LICENSE                               MIT (ADR-005)
.github/workflows/ci.yml              CI: Python suites on 3.9-3.13, TypeScript port, Dafny backend
docs/
  architecture/
    overview.md                       Ecosystem architecture, Core → Language
    core-components.md                Component responsibilities and boundaries
    intent-and-ir.md                  Intent representation, layers, IR boundary
    verification-engine.md            Backends, adapter contract, evidence semantics
    verification-model.md             Claim taxonomy, policy semantics, trust boundary
    security-model.md                 Threat model, trust boundaries, isolation path
    language-vision.md                Long-term language design options
    architecture-options.md           New language vs extension vs framework
    decision-register.md              DECIDED / PROPOSED / EXPERIMENTAL / DEFERRED / REJECTED
  decisions/
    ADR-001-core-first.md             Build the framework before the language
    ADR-002-verification-trust-boundary.md
    ADR-003-intent-representation.md
  specifications/
    report-schema.json                Verification report schema
    policy-schema.json                Acceptance policy schema
    examples/ledger/                  A 20-obligation money module: TypeScript, Dafny model, obligations, policy, lock
    examples/gcd/                     Third-party code (pallosp/congruence_solver, MIT) through the gate: 4 proven + 2 property-tested obligations in one merged report
    negative-scenarios.md             The M5 scenarios: incorrect code, weakened obligations and policy, and the lock
    lock-schema.json                  The lock: human-approved digests of the policy and the Obligation Set
    dafny-backend.md                  Dafny backend: workspace markers, outcomes, trust escapes, the report
    property-backend.md               Property-test backend (the second backend): acceptance criteria on a seeded sample
    gate.md                           The gate: one set, one policy, one lock, ONE merged report (Dafny + property tests)
    verification-ir.md                Verification IR (L2), reproducibility key, lowering rules
    verification-ir-schema.json       Verification IR schema
    policy-engine.md                  Normative semantics of (policy, report, obligation set) -> decision
    bench-schema.json                 Bench report schema (what `make bench` emits)
    obligation-set-schema.json        Obligation Set schema (the frozen verification input)
    obligation-set-canonicalization.md  Canonical form, digest, and report coverage rules
    examples/queue.obligations.json   Demonstration Obligation Set
    validation-layers.md              What JSON Schema enforces vs. what the policy engine must
  roadmap/
    core-v0.1.md                      Minimal prototype scope and milestones
    language-evolution.md             Core to the universal language
    validation-plan.md                Evaluation protocol (Phase C)
  research/
    prior-art.md                      Comparative analysis of prior art
    technical-risks.md                Prioritised risk register
    performance.md                    First measurements of the gate's cost (make bench), E-05/P-01/F1
  evaluation/
    protocol.md                       M6 evaluation protocol (harness reference)
    tasks.json                        scripted corpus (23 submissions) for harness self-tests
    task-schema.json                  M6-Lite task manifest schema (ADR-004); test-task-schema.py
    task-selection.md                 the curator's checklist for selecting the 6 real tasks
    task.example.json                 a valid example manifest
  rapport-final.md                    Phase C report, in French
Makefile                              `make test`: builds the pinned venv, runs all schema tests
requirements.in / requirements.txt    Direct dependencies / hash-locked environment
tools/
  validate.py                         Independent Draft 2020-12 validator (jsonschema)
  bench.py                            Benchmark: full pipeline run on an example, per-obligation cost report (make bench)
  check_example.py                    Whole gate on one example in one command (make check)
  test-bench.py                       Bench schema + summarise() tests (no Dafny needed)
ts/                                   TypeScript port of the adapter (src/), tests against the Python adapter (test/)
  test-ledger.py                      The ledger: 20 obligations proven, one bug per obligation, vacuity
  test-gcd.py                         The third-party gcd example: translation, 4 obligations proven, mutants
  test-scenarios.py                   M5 negative scenarios, end to end with Dafny
  dafny_backend.py                    Dafny backend: IR + proof workspace -> report (decided by the engine)
  test-dafny-backend.py               Backend tests against a real Dafny
  gate.py                             The gate: one Obligation Set -> one merged report -> one decision (Dafny + property tests)
  property_backend.py                 Property-test backend: acceptance criteria -> report entries (second backend, needs Node)
  test-properties.py                  Property backend tests against a real Node (make test-properties)
  ts_to_dafny.py                      TypeScript subset -> Dafny methods (proof model generated from the code)
  test-ts-to-dafny.py                 Translator tests: regeneration, refusals, differential, mutants
  evaluate.py                         M6 harness: three gates, one prover, scored against an independent oracle
  test-evaluate.py                    Harness tests (needs Dafny and Node)
  ts_adapter.py                       TypeScript adapter: Obligation Set -> Verification IR (deterministic)
  test-ts-adapter.py                  Adapter tests: byte-identical IR, reproducibility key, lowering
  policy_engine.py                    Deterministic policy engine: decide, replay, policy digest
  test-policy-engine.py               Engine tests: scenarios, exhaustive safety, determinism, replay
  obligation_set.py                   Validate, canonicalise, hash an Obligation Set; check report coverage
  test-obligation-set.py              Obligation Set tests: digest invariance and sensitivity, coverage
  test-validation.py                  Primary schema suite: legacy fixtures + hardening
  minischema.py                       Scoped JSON Schema validator (legacy)
  test-schemas.py                     Schema fixtures: 23 cases, 21 must fail
```

## Running the schema tests

```
make test
```

This builds a virtualenv (`.venv`, Python 3.9 or newer) from the hash-locked `requirements.txt`, runs the Python suites below, the Dafny backend's tests, then the TypeScript port's tests. `make test-python` needs neither Node nor Dafny; `make test-dafny` needs Dafny 4.11.0 (`SELYTH_DAFNY=/path/to/dafny`, default `~/.local/opt/dafny/dafny`); `make test-ts` needs Node 24 or newer (`make test NODE_BIN=/path/to/node/bin` if it is not on `PATH`). `make bench` also needs Dafny: it runs the full pipeline on an example (`EXAMPLE=ledger` by default, `EXAMPLE=gcd` for the third-party one) once and prints the per-obligation cost (resource units, wall-clock, memory) as a table plus a bench report valid for [`docs/specifications/bench-schema.json`](docs/specifications/bench-schema.json); the measurements are published in [`docs/research/performance.md`](docs/research/performance.md). `make check [EXAMPLE=...]` runs the whole gate on an example in one command — validate the set and its components, regenerate and byte-compare the workspace, lower, verify with Dafny, accept under the lock, replay the decision, and run the property backend when the example has one (`EXAMPLE=gcd`). The suites:

1. **`tools/test-validation.py`, the primary suite**, under the independent [`jsonschema`](https://python-jsonschema.readthedocs.io/) library, pinned, in its Draft 2020-12 mode. It replays the 23 fixtures of `tools/test-schemas.py` unchanged: two valid documents that must pass and twenty-one that must fail. For every rejection it also requires that the *intended* rule is among the reported errors, because a fixture that fails for an unrelated reason proves nothing. It further checks that each schema is a valid Draft 2020-12 schema with no duplicate key, no unknown keyword and no dangling `$ref`, so a schema cannot quietly rely on a rule that is never checked.
2. **`tools/test-schemas.py`, the original suite**, under `tools/minischema.py`, kept as a secondary check.
3. **`tools/test-obligation-set.py`**, the Obligation Set suite: the schema rejects malformed sets; the digest is unchanged by formatting, key order, array order, `\u` escapes and Unicode normalisation, and changes under every change of meaning; a report that omits, duplicates, invents or mis-digests an obligation is refused.

Some rules cannot be expressed in JSON Schema at all, such as a policy digest that must equal the digest recorded in the decision. Those are enforced by the policy engine; the split is documented in [`docs/specifications/validation-layers.md`](docs/specifications/validation-layers.md).
4. **`tools/test-policy-engine.py`**, the policy engine suite: every integrity failure, block, default and escalation yields the decision the specification gives; over every combination of outcome, evidence kind and rule, `accepted` is never produced for evidence weaker than required; decisions are byte-identical across runs, hash seeds and input orderings, and `decide` touches no file, socket, process, clock or randomness.
5. **`tools/test-ts-adapter.py`**, the adapter suite: regenerating the Verification IR gives a byte-identical file across runs, processes, hash seeds, time zones, locales and working directories, and for any formatting or ordering of the Obligation Set; the reproducibility key changes with each of its inputs (Obligation Set digest, adapter name and version, verification tool name and version, IR version); no obligation is ever dropped.
6. **`ts/` (`npm test`)**, the TypeScript port of the adapter: typechecked (`tsc --strict`), and compared **byte for byte** with the Python adapter on the demonstration set and on a rich set (Unicode normalisation, code point ordering, many assumptions, unsupported obligations, a 5,000-digit literal), plus the same vectors and golden as the Python suite.
7. **`tools/test-dafny-backend.py`**, the Dafny backend suite, against a **real Dafny**: known-correct obligations are `kernel-proven` and the report validates; a false obligation is `failed` and named by the policy engine; an `assume {:axiom}` that Dafny itself would accept cannot launder a false obligation; resource exhaustion is a `timeout`; a missing marker or an unlowerable obligation is `not-run`; an IR for another Dafny version is refused.
8. **`tools/test-scenarios.py`**, the negative scenarios of milestone M5, end to end with a real Dafny: an off-by-one that passes plain Dafny is `rejected` naming `OBL-0008`; weakening or deleting the obligation it violates, or editing the policy, is `rejected` by digest mismatch against the human-approved lock ([`negative-scenarios.md`](docs/specifications/negative-scenarios.md)).
9. **`tools/test-ledger.py`** (and `ts/test/ledger.test.ts`), the twenty-obligation money module of [`docs/specifications/examples/ledger/`](docs/specifications/examples/ledger/README.md) against a real Dafny: all twenty `kernel-proven` and accepted under the lock; a bug injected for each obligation, which it must fail on, and which Dafny alone accepts in seventeen cases; a precondition nobody calls is refused as vacuous; bugs written in the TypeScript itself are rejected through the generated model. **`tools/test-gcd.py`** repeats the core of this on third-party code ([`docs/specifications/examples/gcd/`](docs/specifications/examples/gcd/README.md), from `pallosp/congruence_solver`, MIT): the committed workspace is the exact translation of the TypeScript, four obligations are `kernel-proven` and accepted under the lock, and one bug per obligation is caught.
10. **`tools/test-ts-to-dafny.py`**, the translator ([`ts-to-dafny.md`](docs/specifications/ts-to-dafny.md)): the ledger's committed Dafny workspace is exactly the translation of `ledger.ts`; everything outside the subset (strings, calls, ternaries, bare `/`, signed division, two loops, a name the specification lacks) is refused; random expressions and loop conditions give the same value read as TypeScript and as the emitted Dafny text; twelve deliberately broken translators are caught.
11. **`tools/test-evaluate.py`**, the instrument of milestone M6 ([`docs/evaluation/protocol.md`](docs/evaluation/protocol.md)): a scripted corpus of 23 submissions (a null agent, correct refactors, bugs, a specification gap, agents that tamper with the Obligation Set or the proof model) is run through three gates that share one prover (Dafny alone, the obligations without a lock, Core) and scored against an oracle that shares nothing with them. It asserts the properties of the instrument (labels confirmed, null-agent floor, the full gate accepts no violating submission, the unlocked gate misses what the lock catches), not rates: nothing here says how a real agent would fare.

Without `make`:

```
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
.venv/bin/python tools/test-validation.py
```

To verify with Dafny and decide: `.venv/bin/python tools/dafny_backend.py verify --ir ir.json --workspace docs/specifications/examples/queue.workspace.dfy --obligation-set docs/specifications/examples/queue.obligations.json --policy docs/specifications/examples/queue.policy.json` prints the report (exit 0 accepted, 1 rejected, 3 needs-human, 2 error); see [`docs/specifications/dafny-backend.md`](docs/specifications/dafny-backend.md).

To lower an Obligation Set: `.venv/bin/python tools/ts_adapter.py lower <obligation-set.json> --backend-version 4.11.0`. The format and the key are in [`docs/specifications/verification-ir.md`](docs/specifications/verification-ir.md).

To decide: `.venv/bin/python tools/policy_engine.py decide <policy.json> <report.json> <obligation-set.json>` (exit 0 accepted, 1 rejected, 3 needs-human, 2 invalid input), or `replay` to check that a report's recorded decision is the engine's. The semantics are in [`docs/specifications/policy-engine.md`](docs/specifications/policy-engine.md).

To hash an Obligation Set: `.venv/bin/python tools/obligation_set.py digest docs/specifications/examples/queue.obligations.json`; to check that a report covers it: `... obligation_set.py cover <obligation-set.json> <report.json>`. The rules are in [`docs/specifications/obligation-set-canonicalization.md`](docs/specifications/obligation-set-canonicalization.md).

To validate one document against one schema: `.venv/bin/python tools/validate.py docs/specifications/report-schema.json my-report.json`. To change a dependency, edit `requirements.in` and regenerate the lock with the command written at the top of that file.

## Requirements

The full test suite needs Python 3.9 or newer, Node 24 or newer and Dafny 4.11.0 (`make test`; see *Running the schema tests*). The design targets a €0 marginal-cost stack: MIT-licensed Dafny, Boogie and Z3, invoked as a subprocess; one ordinary machine; no paid API; no cloud infrastructure; no model required for execution, verification or acceptance. Models propose. They are never part of the decision function.

## Licence and contributing

MIT — see [`LICENSE`](LICENSE), [ADR-005](docs/decisions/ADR-005-mit-licence.md) and D-12 in the [decision register](docs/architecture/decision-register.md). The architecture assumes permissive licensing throughout and no dependency on a proprietary or paid component, because a €0, single-maintainer project cannot absorb one. The dependency licence audit ADR-005 records is complete for the pinned set: the hash-locked Python requirements (MIT, plus PSF-2.0 for typing-extensions), the npm lockfile (ajv and @types/node MIT, TypeScript Apache-2.0) and the Dafny/Boogie/Z3 subprocess toolchain (MIT).

Community contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). The most likely useful contributions are backend adapters, because the adapter contract is designed to make them additive — but that contract has not yet survived contact with a second implementation, which is exactly why V0.1 ships one.
