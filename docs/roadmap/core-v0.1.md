# Selyth Core V0.1 — Prototype Scope and Milestones

**Status:** Milestones M0 to M5 are implemented (see *Status of the milestones*); M6, the evaluation, is not. The scope and the milestone definitions below are kept as written.
**Depends on:** `docs/architecture/overview.md`, `docs/architecture/decision-register.md`.

## Scope boundary

V0.1 answers exactly one question:

> Does an evidence-and-policy layer change the accept/reject decision that a competent developer or agent would reach by running the same verification tools directly?

Everything in V0.1 exists to make that question answerable. Anything that does not serve it is out of scope, and the exclusion list is as binding as the scope.

## The nine required elements, made concrete

| # | Required | V0.1 realisation | Excluded |
|---|---|---|---|
| 1 | One structured specification format | Obligation Set in JSON, schema-validated, frozen by SHA-256 | A parser or a DSL compiler |
| 2 | One small typed program representation **or existing-language adapter** | A TypeScript adapter producing a Verification IR | A Selyth parser, a type system, any code generation |
| 3 | One verification backend | Dafny via subprocess, with a capability declaration | Boogie as a direct dependency; any second backend |
| 4 | One evidence-report generator | Emits `report-schema.json` instances | Any UI |
| 5 | One deterministic policy engine | A total function over (policy, report), no I/O | Learning, scoring, heuristics |
| 6 | One successful verification scenario | ~20 obligations from one real project, all `kernel-proven` | Repository-scale coverage |
| 7 | One intentionally incorrect implementation | A change violating a stated invariant while passing every pre-existing check | Mutation testing at scale |
| 8 | One attempt to weaken a verification policy | An agent-style edit to the policy or an obligation | Automated red-team tooling |
| 9 | One test that the policy engine rejects the weakening | A test asserting `rejected` with a named rule | Coverage of all attack paths |

## Milestones

Each milestone ends in something runnable and checkable. A milestone that cannot be demonstrated is not complete.

**M0 — Schemas and their tests.** The two JSON schemas exist, a minimal validator covers the keywords used, and the negative test suite passes. This milestone is already complete in the design phase: `docs/specifications/*.json`, `tools/minischema.py`, `tools/test-schemas.py`, 23 cases, 21 of which must fail validation.

**M1 — Obligation Set and its digest.** Read an Obligation Set, validate it, compute its digest, and refuse to proceed if the digest changes mid-run. Exit code is the contract. **Done when:** running the tool twice on the same input yields the same digest, and editing one character yields a different one.

**M2 — TypeScript adapter.** Lower obligations for a small, pure subset of TypeScript into a Verification IR. **Done when:** regenerating from the same obligation digest produces a byte-identical IR. That determinism test is the milestone, not the translation.

**M3 — Dafny backend.** Invoke Dafny as a subprocess, capture the result, and emit a report entry. **Done when:** a known-correct obligation yields `kernel-proven`, and the report validates against the schema.

**M4 — Policy engine.** Evaluate (policy, report) into a decision, with every rejection naming the rule that produced it. **Done when:** the schema test fixtures, which include five policy-level and sixteen report-level negative cases, are replayed as unit tests against the real engine rather than the schema alone.

**M5 — The negative scenarios.** The intentionally incorrect implementation, the policy-weakening attempt, and the rejection test. **Done when:** the incorrect change is rejected with a named obligation; the weakened policy is detected by digest mismatch; the test asserting rejection passes.

**M6 — The evaluation.** Run Baseline A and Experimental B on a held-out task set and report the metrics below.

M0 through M5 are the prototype. M6 is the experiment that justifies continuing.

### Status of the milestones

| Milestone | State | Where |
|---|---|---|
| M0 Schemas and their tests | done | `docs/specifications/`, `tools/test-schemas.py`, `tools/test-validation.py` |
| M1 Obligation Set and its digest | done | `tools/obligation_set.py`, `obligation-set-canonicalization.md` |
| M2 TypeScript adapter | done, in Python and in TypeScript, byte-identical | `tools/ts_adapter.py`, `ts/`, `verification-ir.md` |
| M3 Dafny backend | done, against Dafny 4.11.0 | `tools/dafny_backend.py`, `dafny-backend.md` |
| M4 Policy engine | done; the 21 negative fixtures are replayed through the engine, which rejects each as invalid input | `tools/policy_engine.py`, `policy-engine.md`, `tools/test-policy-engine.py` |
| M5 Negative scenarios | done | `tools/test-scenarios.py`, `negative-scenarios.md` |
| Element 6 (about twenty obligations) | done on a purpose-written module, not on an existing third-party project | `docs/specifications/examples/ledger/`, `tools/test-ledger.py` |
| M6 Evaluation | prepared, not run: protocol, harness, oracle and a scripted corpus; no agent has been run. Launch decisions taken for M6-Lite, a zero-marginal-cost pilot (6 tasks, 12 agent runs, 20 minutes and 15 model calls per run, procedural isolation); the full 60-run evaluation is a distinct future one, not started | `docs/evaluation/protocol.md`, `tools/evaluate.py`, `tools/test-evaluate.py`; [`ADR-004`](../decisions/ADR-004-m6-launch-decisions.md) |

Nothing is claimed beyond what the tests check: the proof workspace is generated from the TypeScript by a translator for a pure subset ([`ts-to-dafny.md`](../specifications/ts-to-dafny.md)), but the invariants, the lemmas and the callers that prove preconditions are still written by hand, and the twenty-obligation scenario ran on a module written for the purpose, so how Core fares on someone else's code is still unknown.


## Evaluation

### Design

Baseline A is the same agent, the same model, the same tasks, with the same Dafny checking available, and nothing else. Experimental B is the same agent with the Selyth intent, evidence and policy layer added.

Holding the prover constant is the point. The experiment tests the *governance layer*, not the verifier. If the prover differed between arms, any measured difference would be attributable to the wrong component.

### Metrics

| Metric | Definition | Why it is primary |
|---|---|---|
| False acceptance | A change accepted that violates a stated obligation | The cost the system exists to remove |
| False rejection | A change rejected that violates no obligation | The cost of adding a gate; a high rate makes the gate get turned off |
| Verification latency | Resource units, with wall-clock recorded alongside | Wall-clock is not reproducible; resource units are |
| Human intervention | Review-minutes per accepted change | The quantity the project is actually trying to reduce |
| CPU and memory | Peak RSS and CPU time on the declared hardware | Keeps the €0 constraint a measurement rather than a claim |
| Maintenance overhead | Obligations added or deleted per accepted change | Detects F2, the obligation-set decay failure mode |
| Unauthorized spec change detection | Rate at which a changed obligation invalidates a prior report | Tests the content-addressing design directly |

### Contamination controls

Four, all borrowed from work that has already documented the failure mode being avoided.

**Held-out obligations.** The evaluated set is frozen and authored by someone other than the evaluating agent. Following Vero, the curator supplies frozen layers and the agent discharges obligations against them.

**Verifier outside the agent's environment.** Berkeley RDI found all eight audited agent benchmarks exploitable precisely because the submission shared the evaluation environment. The rule is absolute: the evaluated system must not read, write or influence the evaluation environment. In V0.1 this is documented as a procedural control, because a single machine cannot provide a security boundary — see `security-model.md`.

**Null-agent floor.** Run an agent that takes no action. Its score is the floor. If it is not zero, the evaluation is broken and the results are discarded.

**Adversarial evaluation of the evaluator.** Run a state-tampering agent that modifies the environment instead of solving the task, and mutation-test the obligation set to catch obligations that pass while being gutted.

The reason this is not optional: OpenAI stopped reporting SWE-bench Verified after finding that at least 59.4% of an audited subset of unsolved instances had test suites that rejected functionally correct submissions, and Berkeley RDI subsequently identified 16 distinct attack types across 13 benchmarks. An evaluation of a verification gate that uses an agent-writable test suite as its oracle reproduces a design already known to be invalid.

## Stopping conditions

Stated in advance so that they are not renegotiated after the result is known.

**They apply to the full evaluation, not to M6-Lite.** M6-Lite (12 agent runs, one per arm and task, see ADR-004) is a feasibility and directional pilot and cannot trigger any of them; its results are reported task by task and support no general conclusion.

**Remove the intent layer** if it does not measurably reduce false acceptance against Baseline A. The available benchmark evidence predicts this outcome, and the correct response to being right about it is to cut the feature, not to lower the bar. The intent records stay; the corroboration engine does not ship.

**Stop the project** if the fast tier cannot meet 2 seconds on real obligations, or if false rejection exceeds 10% against Baseline A. Either result means the gate would be bypassed or switched off in practice, which is worse than having no gate because the failure would be invisible.

**Add a second backend** if the adapter contract turns out to require backend-specific knowledge inside the policy engine. That would mean the boundary in `verification-engine.md` was drawn in the wrong place, and it is cheaper to discover with two adapters than with one.

**Narrow the claims** if the evaluation cannot demonstrate a difference between A and B. The honest outcome is then a well-documented negative result about a governance layer over existing tools, which is still worth having, and the correct response is to record it rather than to re-run until it succeeds.

## What V0.1 explicitly does not build

No compiler. No Selyth Language. No type system. No effect system. No parser. No user interface. No database. No network service. No multi-agent orchestration. No report signing. No container isolation. No second language. No property-based test generation beyond what the backend does itself.

## First three development tasks

Ordered so that each is independently verifiable and none can be "done" by a plausible-looking artefact.

**Task 1 — Replace the schema validator with a real one, and keep the negative tests.**
`tools/minischema.py` covers only the JSON Schema keywords these two schemas use, and it was written alongside them, so it cannot be wrong in a way that hides a bug in the schema. Before any implementation work begins, the fixtures in `tools/test-schemas.py` should be replayed against a production validator, and the 21 negative cases must still fail. *Done when:* the fixtures run under an independent validator with the same 21 rejections.

**Task 2 — Implement the Obligation Set digest and its invariance test.**
Read an Obligation Set, validate it against a schema, canonicalise it, and compute a SHA-256 digest that is stable under reordering and formatting but changes under any semantic edit. *Done when:* reordering keys and reindenting produce an identical digest, and changing one character of one obligation produces a different one.

**Task 3 — Implement the TypeScript adapter's determinism test before the adapter.**
Write the test that regenerates the Verification IR twice from the same obligation digest and compares byte-for-byte. The test should fail until the adapter exists. *Done when:* the test is committed, fails for the right reason, and passes once the adapter is implemented. Writing the test first is deliberate: a non-deterministic adapter produces a cache that is unsound in a way indistinguishable from a stale one, and that is much cheaper to catch before the cache exists.
