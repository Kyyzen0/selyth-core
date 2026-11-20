# Selecting the M6-Lite task set

ADR-004 section 2 already fixes the constraints; this document turns them into
a checklist for the independent curator who produces the corpus. Nothing here
relaxes the ADR. Each task is shipped as a manifest validated against
[`task-schema.json`](task-schema.json) (see `tools/test-task-schema.py`); an
example manifest is [`task.example.json`](task.example.json).

## What the corpus must contain

- **6 real tasks**, from **at least 3 distinct external projects**, totalling **at least 20 obligations**. Below any of these minimums the launch is refused, not started.
- Code under a **permissive licence** (the enum in `task-schema.json`) and **inside the pure TypeScript subset** of [`../specifications/ts-to-dafny.md`](../specifications/ts-to-dafny.md); code outside the subset is refused by the translator, so a task built on it would measure the translator, not the agent.
- Every task ships a **hashed manifest**: starting code (path + `sha256:` digest per file), the frozen Obligation Set (path + digest), the allowed modifiable files, and the expected oracle (digest only — the oracle itself stays in the private evaluation repository). Task identifiers are opaque (`task-schema.json` pattern) and the set is **secret until launch**.
- Obligations, oracle and reference solution are written and frozen by the **independent human curator**. Neither the Selyth project, nor the agent, nor its operator modifies them.
- A **frozen, hashed control plan** with exactly **10 `null-agent` and 10 `sabotage`** controls: opaque identifier, type, targeted task, arm if applicable, expected scenario, expected result. The evaluator refuses an official launch if the plan is absent, miscounted, aimed at an unknown task, or inconsistent with the frozen manifests.
- Per-run budgets recorded in every manifest: **20 minutes, at most 15 model calls** (retries, automatic summaries and relaunches count). The runner must measure the call count and refuse the official launch if it cannot.

## How to choose the 6 tasks

1. **Real, not synthetic.** The ledger module was written for this project and cannot estimate an effect size; the corpus uses third-party code. Pick tasks a working developer would actually be given: bug fix, refactor, small feature, edge-case handling.
2. **Variety across projects and difficulty.** At least 3 projects; mix sizes (a few obligations to several) so the ≥20-obligation floor is not met by one large task alone.
3. **Ordinary and adversarial tasks.** Some tasks should have a plausible solution that violates a precondition, an invariant or an edge case. The split is not fixed by the ADR: the curator states it before the corpus is frozen and it is recorded with the manifests.
4. **Verifiable by the frozen obligations alone.** Each task must be judgeable by its Obligation Set plus the oracle — no hidden test may be needed to decide a run. If a task needs a hidden test to be scored, the obligations are incomplete; fix the obligations, not the harness.
5. **No ambiguity the obligations do not resolve.** The intent gap is real but out of scope for M6-Lite; a task whose statement is ambiguous produces noise, not signal. Rephrase or drop it.
6. **Deterministic environment.** Same code, same frozen read-only obligations, same Dafny access for both arms; the agent gets a disposable directory, a patch-only submission, and no token or identifier reaching the private repository (procedural isolation, ADR-004 section 5).

## Launch gate (checked mechanically where possible)

- [ ] 6 manifests, each valid against `task-schema.json`, digests all `sha256:` and full length.
- [ ] At least 3 distinct `source.project` values; licences all in the schema enum; all task code inside the TS subset.
- [ ] At least 20 obligations in total across the frozen Obligation Sets.
- [ ] Control plan present and frozen: exactly 10 `null-agent` + 10 `sabotage`, each targeting a known `task_id`.
- [ ] Runner counts every model call and enforces 20 min / 15 calls per run; 12 agent runs total (6 tasks × 2 arms × 1 run).
- [ ] Corpus, control plan and manifests hashed and frozen before any run; results reported task by task, no general conclusion from 12 runs.
- [ ] Marginal monetary cost €0.
