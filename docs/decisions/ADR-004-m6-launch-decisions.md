# ADR-004 — Launch decisions for the M6 evaluation

**Status:** Accepted
**Date:** 2026-10-13
**Deciders:** Project owner
**Related:** ADR-002, `docs/roadmap/core-v0.1.md` (M6), `docs/roadmap/validation-plan.md` (Evaluation protocol)

## Context

M6 compares arm A (agent + Dafny) with arm B (agent + Dafny + Selyth layer). `core-v0.1.md` marked it "not started: needs decisions on the agent, the tasks and the budget". This ADR fixes those decisions before launch, so that they cannot be adjusted after seeing results. The general protocol (frozen obligations, verifier outside the agent's environment, null-agent floor, adversarially tested evaluator) remains that of `validation-plan.md`.

## Decision

### 1. Agent, model and information provided

- Agent: Claude Code. Model: `claude-sonnet-4-6`, frozen for all trials.
- Recorded for every run: Claude Code version, exact model identifier, date, parameters, duration, tokens and cost.
- Condition **A1**: both arms receive the same code, the same frozen read-only obligations and the same Dafny access.
  - Arm A: agent + obligations + Dafny, without the Selyth engine.
  - Arm B: agent + same obligations + same Dafny + Selyth layer (evidence report and policy engine).
- **A0** (the agent writes the specification itself) is excluded from M6. It will be a separate experiment.

### 2. Task set

- 10 tasks, kept secret until launch, totalling about 20 obligations.
- Code from external projects under a permissive licence, within the pure TypeScript subset supported by the prototype (`ts-to-dafny.md`).
- 6 ordinary tasks (fix or implementation) and 4 adversarial tasks, where a plausible solution may violate a precondition, an invariant or an edge case.
- Obligations, oracle and reference solution are written and frozen by an independent human curator. Neither the Selyth project, nor the agent, nor its operator modifies them.
- Each task ships with a hashed manifest: starting code, obligations, allowed modifiable files and expected oracle.

### 3. Repetitions and budget

- 3 fresh executions per task and per arm: 10 × 2 × 3 = **60 main runs**.
- Plus 10 "null agent" runs and 10 controlled-sabotage runs against the evaluator.
- No rerun after a failure, except for a documented infrastructure outage.
- Limit per main run: 20 minutes and €4.
- Maximum study budget: **€350**, of which €240 for the 60 main runs (60 × €4) and €110 for controls and technical contingencies. Stop immediately if the budget is reached.

### 4. Measuring human review

- An reviewer external to the project examines only the cases that genuinely require a human decision.
- They receive the code, the frozen obligations and a standardised evidence package; they never access the hidden oracle.
- The measurement starts when the review file is opened and stops when a structured verdict is submitted: accept, reject or request information.
- The form automatically records start, end, verdict and reason; interruptions are reported separately.
- A random 20% sample is re-reviewed by a second person to check consistency.
- False positives and false negatives are determined by the hidden oracle, never by the reviewer.

### 5. Isolation

- The agent works in a disposable directory containing only the starting code, the read-only obligations and the explicitly modifiable files.
- The lock, the hashed manifest, the oracle, the harness and the hidden tests live in a separate private evaluation repository, to which the agent has neither access nor a GitHub token.
- The agent submits only a patch of the allowed files. A controller applies it to a clean copy in the evaluation environment; the harness refuses any modification of the obligations, the manifest or the infrastructure. The evaluator then runs the hidden tests and produces the result.
- On a single machine this separation remains **procedural**. To claim that the agent "cannot" touch the oracle, the evaluator must run in a separate private repository and a separate runner.

## Consequences

- With 3 repetitions and 10 tasks, the study detects large differences between arms, not small ones: results will be reported with raw counts, without claiming fine statistical significance.
- Without reruns, an agent failure counts as a failure. This is deliberate: we measure behaviour, not the best of several attempts.
- The budget is a hard cap. The control runs (null agent, sabotage) are mandatory; if they exhaust their €110 envelope, we stop rather than eat into the €240 for the main runs.
- As long as the evaluator and the agent share a machine, any isolation claim must be worded as procedural.
- The task set does not exist yet in this repository: by construction it lives in the private evaluation repository.

## Alternatives considered

- **A0** (specification written by the agent): conflates two questions; deferred to a later experiment.
- **Reruns after failure**: artificially inflates the success rate and distorts the cost comparison.
- **Reviewer given the oracle**: would make the reviewer measure what the oracle already knows.
