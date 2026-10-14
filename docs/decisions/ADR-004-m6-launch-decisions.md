# ADR-004 — Launch decisions for the M6 evaluation (M6-Lite)

**Status:** Accepted, amended on 2026-10-14 before any run
**Date:** 2026-10-13 (amended 2026-10-14)
**Deciders:** Project owner
**Related:** ADR-002, `docs/roadmap/core-v0.1.md` (M6), `docs/roadmap/validation-plan.md` (Evaluation protocol), `docs/evaluation/protocol.md`

## Context

M6 compares arm A (agent + Dafny) with arm B (agent + Dafny + Selyth layer). `core-v0.1.md` marked it "not started: needs decisions on the agent, the tasks and the budget". This ADR fixes those decisions before launch, so that they cannot be adjusted after seeing results. The general protocol (frozen obligations, verifier outside the agent's environment, null-agent floor, adversarially tested evaluator) remains that of `validation-plan.md`.

**Amendment of 2026-10-14.** The first version of this ADR planned a full study: 10 tasks, 3 repetitions per arm, 60 agent runs. The first study actually launched is **M6-Lite**, a pilot at zero marginal cost. No run of any kind had taken place when the plan was replaced, so the replacement is not informed by any result. The full study is kept as a distinct future evaluation, not started (section 6).

## Decision

### 1. Agent, model and information provided

- Agent: Claude Code. Model: `claude-sonnet-4-6`, frozen for all trials.
- Recorded for every run: Claude Code version, exact model identifier, date, parameters, duration, number of model calls, tokens or quota consumed when available, CPU, memory and commands executed. These are resource measurements; they are never converted into a monetary cost. A measurement that cannot be recovered is recorded as `unavailable`, never guessed.
- Condition **A1**: both arms receive the same code, the same frozen read-only obligations and the same Dafny access.
  - Arm A: agent + obligations + Dafny, without the Selyth engine.
  - Arm B: agent + same obligations + same Dafny + Selyth layer (evidence report and policy engine).
- **A0** (the agent writes the specification itself) is excluded from M6-Lite. It will be a separate experiment.

> **Terminology.** `docs/evaluation/protocol.md` uses "A0" for *Dafny alone, no obligation attached*, and "A1" for *obligations without the lock*. Here, "A1" names the information condition (same code, obligations and Dafny for both arms), "arm A" corresponds to the protocol's A1 and "arm B" to its B. "A0" in this ADR means *the agent writes the specification*, a different thing from the protocol's A0. Where the two documents disagree on a definition, the protocol governs the harness and this ADR governs the launch parameters.

### 2. Task set

- **6 real tasks** from **at least 3 distinct projects**, totalling **at least 20 obligations**. A set below any of these minimums is not launched. The tasks are kept secret until launch.
- Code from external projects under a permissive licence, within the pure TypeScript subset supported by the prototype (`ts-to-dafny.md`).
- Obligations, oracle and reference solution are written and frozen by an independent human curator. Neither the Selyth project, nor the agent, nor its operator modifies them.
- Each task ships with a hashed manifest: starting code, obligations, allowed modifiable files and expected oracle. Task identifiers are opaque.
- The corpus also ships a **frozen, hashed control plan** with exactly 10 `null-agent` and 10 `sabotage` controls. For each: an opaque control identifier, its type, the targeted task, the arm concerned if applicable, the expected scenario and the expected result. The independent curator chooses the identifiers and the real distribution of the controls when creating the corpus, not this ADR. An official launch is refused if the plan is absent, does not hold exactly 10 controls of each type, targets an unknown task, or does not match the frozen manifests.
- The split between ordinary tasks and adversarial ones (where a plausible solution may violate a precondition, an invariant or an edge case) is not fixed by this ADR. The curator states it before the corpus is frozen, and it is recorded with the manifests.

### 3. Runs and resource limits

- **Two arms on every task, one fresh run per arm and task: 6 × 2 × 1 = 12 agent runs.**
- Limit per run: **20 minutes and at most 15 model calls.** Every request to the model counts, including retries, automatic summaries and relaunches. If the runner cannot measure this number, it refuses the official launch.
- Hence at most **180 model calls** and **4 hours** of theoretical run time for the whole study.
- Scripted **null-agent** and **sabotage** controls are kept (10 of each, run against the real tasks, as listed in the frozen control plan of section 2). They call no model. Controls run against the harmless example task are harness self-tests and do not count among them.
- No rerun after a failure, except for a documented infrastructure outage.
- **No reduction** of the corpus, automatic or manual. If quotas are insufficient, the study waits; it removes neither a task nor a control.
- **Marginal monetary budget: €0**, in line with the project's zero-marginal-cost principle (`MANIFESTO.md`, principles 8 and 11). M6-Lite uses only subscriptions and hardware already owned, free local tools and open-source components. If usage limits do not allow the runs, they are spread over time; the study is never accelerated by buying API access, credits or a cloud service.
- **No paid run has been launched and no money has been spent.** This is stated as a fact at the date of this ADR, not as a forecast.

### 4. Measuring human review

- A reviewer external to the project examines only the cases that require a human decision.
  - **Trigger.** After verification and before any Selyth policy, a required obligation is neither clearly proven nor clearly violated. The rule is identical for both arms, is applied to the same evidence report, and does not depend on arm B's policy outcome. It is not left to an operator's reading: it is a set of predicates over the fields of each obligation result of the evidence report (`report-schema.json`), computed by the controller. Every obligation of the frozen Obligation Set is required. Exactly one class applies to a result:

    | Class | Predicate | Review |
    |---|---|---|
    | `timeout` | `outcome == timeout` | yes |
    | `unknown` | `outcome == unknown` | yes |
    | `insufficient-scope` | `outcome == not-run`, or `outcome` is `proven` or `passed` and `evidence.kind` is weaker than `required_evidence_kind` | yes |
    | `error` | `outcome == failed` and no diagnostic contains a refutation phrase | yes |
    | `conditional-proof` | `outcome` is `proven` or `passed`, `evidence.kind` is not weaker than `required_evidence_kind`, and `evidence.assumptions` is not empty | yes |
    | `clearly-violated` | `outcome == failed` and some diagnostic contains a refutation phrase | no |
    | `clearly-proven` | `outcome` is `proven` or `passed`, `evidence.kind` is not weaker than `required_evidence_kind`, and `evidence.assumptions` is empty | no |

    "Weaker" follows the order of the evidence kinds in the report schema (`kernel-proven` is the strongest). The free-text `scope` field is never read. The refutation phrases are the closed list `could not be proved`, `might not hold`, `might not decrease`, `might not be`: the report schema does not distinguish a refuted obligation from a tool failure (both are `failed`), so a `failed` result carrying none of these phrases is treated as an `error` and goes to review.
- They receive the code, the frozen obligations and a standardised evidence package; they never access the hidden oracle.
- The measurement starts when the review file is opened and stops when a structured verdict is submitted: accept, reject or request information.
- The form automatically records start, end, verdict and reason; interruptions are reported separately.
- **Second review.** Among the cases that required a review, `ceil(20%)` of them are re-reviewed by a second person: those whose `SHA-256("selyth-m6-review-v1" + manifest_digest + run_id)` is smallest. There is no seed.
- False positives and false negatives are determined by the hidden oracle, never by the reviewer.

### 5. Isolation

- The agent works in a disposable directory containing only the starting code, the read-only obligations and the explicitly modifiable files.
- The lock, the hashed manifest, the oracle, the harness, the hidden tests and the control plan live in a separate private evaluation repository. That repository, the oracle and the hidden tests are not provided in the agent's working directory, and the agent receives no GitHub token and no identifier giving access to the repository.
- The agent submits only a patch of the allowed files. A controller applies it to a clean copy in the evaluation environment; the harness refuses any modification of the obligations, the manifest or the infrastructure. The evaluator then runs the hidden tests and produces the result.
- **The isolation level of this first M6 is `procedural`.** On a shared machine the separation described above remains procedural: it does not allow stating that the agent is technically unable to access the oracle, and no report may state it. An anchor of the audit journal improves traceability; it does not turn this level into a security sandbox. Such a statement would require the evaluator to run in a separate private repository on a separate runner, which is not part of M6-Lite.
- The run records and the audit journal are described in the private repository. The journal only provides local, procedural integrity until its head is anchored in a location outside the tested agent's working directory and credentials, which the operator attests.

### 6. Interpretation of the results

M6-Lite is a feasibility study and a directional comparison, not a robust statistical demonstration.

- Results are reported **task by task, with raw counts**.
- **No general conclusion** about performance, statistical significance or security is drawn from 12 runs.
- M6-Lite does not by itself establish the acceptance criteria of `validation-plan.md`. It informs whether the full evaluation is worth running.
- **The stopping conditions of `core-v0.1.md` do not apply to M6-Lite.** Removing the intent layer, stopping the project, adding a second backend or narrowing the claims are decisions for the full evaluation, whose data a 12-run pilot cannot supply. An M6-Lite outcome such as no observed difference between the arms, or a false rejection above 10% on six tasks, is reported as it is and triggers none of them.
- The **full M6** (10 tasks, 3 repetitions per arm, 60 agent runs) is a distinct future evaluation. It is **not started**, it is **not** the launch plan, and its parameters are not decided by this ADR.

## Consequences

- With one run per arm and task, a single stochastic outcome can decide a task. That is why nothing beyond a task-by-task description is claimed.
- Without reruns, an agent failure counts as a failure. This is deliberate: we measure behaviour, not the best of several attempts.
- The limits of the subscriptions already held are the real constraint. Controls are mandatory and are never dropped to make room; the study waits instead.
- The 15-call limit is a constraint on what the agent can attempt. A task that needs more is a finding about the setup, to be reported as such.
- The corpus does not exist yet: by construction it lives in the private evaluation repository.
- As long as the evaluator and the agent share a machine, any isolation claim must be worded as procedural.

## Alternatives considered

- **The full M6 (10 tasks, 3 repetitions, 60 runs) as the first study:** replaced by M6-Lite, which fits within the usage limits of the subscriptions already held and is enough to test feasibility; kept as a future distinct evaluation.
- **A0** (specification written by the agent): conflates two questions; deferred to a later experiment.
- **Reruns after failure:** artificially inflates the success rate and distorts the comparison of resource use.
- **A reduction rule for the corpus:** removed; a reduction chosen under quota pressure invites choosing what to drop after seeing outputs. The study waits instead.
- **A random second-review sample drawn from a seed:** replaced by a deterministic hash of values every party can recompute.
- **Reviewer given the oracle:** would make the reviewer measure what the oracle already knows.
