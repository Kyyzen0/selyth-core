# M6-Lite launch runbook

The exact operator commands for the official M6-Lite launch, each mapped to the ADR-004
requirement it enforces. Everything here is rehearsed end to end, without any real model, by
[`tools/m6_dry_run.py`](../../tools/m6_dry_run.py) (`python3 tools/m6_dry_run.py all --work DIR`;
tested by `tools/test-m6-dry-run.py` in `make test-python` and, with real Dafny and Node, in
`make test-eval`). The corpus, the oracle, the lock and the control plan live in the private
evaluation repository (`Kyyzen0/selyth-eval-vault`); this runbook names commands and their
refusals, never the vault's content.

Notation: `VAULT` is the private repository checkout, `CORPUS=$VAULT/corpus/corpus.json`,
`DIGEST` is the frozen `manifest_digest`, `RUNS` is a fresh empty directory for run records,
`JOURNAL` is the audit journal file in the vault.

## 0. Rehearse first (no model, no corpus content needed)

```
python3 tools/m6_dry_run.py all --work /tmp/m6-rehearsal
```

Must print `DRY RUN OK`. If any stage fails, do not launch: the evaluator is broken, not the corpus.

## 1. Gate the frozen corpus before anything runs (ADR-004 §2)

```
python3 tools/launch_gate.py check --manifest "$CORPUS" --digest "$DIGEST" --corpus-dir "$VAULT/corpus"
```

Exit 0 = launch allowed. Exit 2 with named reasons = refused: minimums (6 tasks, ≥ 3 projects,
≥ 20 obligations), a task manifest violating `task-schema.json`, a missing/mismatched artefact
sha256, a control plan that is not exactly 10 `null-agent` + 10 `sabotage` aimed at known tasks,
or a `manifest_digest` that does not match the canonical digest of the corpus.

## 2. Run the 12 agent runs (ADR-004 §3: 6 tasks × 2 arms, ≤ 20 min, ≤ 15 model calls each)

For each task `T` in the corpus and each arm `A1` / `B`, in a fresh disposable directory holding
only the starting code, the read-only obligations and the modifiable files (procedural isolation,
ADR-004 §5 — the agent gets no GitHub token and no vault access):

```
python3 tools/agent_runner.py run --agent-cmd "<the agent command>" --workdir "$RUN/$T-$ARM" \
    --task-id "$T" --arm "$ARM" --max-calls 15 --max-seconds 1200 --record "$RUNS/$T-$ARM.json"
```

Each run goes through the loopback-only counting proxy (`tools/model_proxy.py`, binds 127.0.0.1
only): every `POST /v1/messages` counts — retries, automatic summaries and relaunches included —
and the proxy answers **429 past the 15-call budget**. Each run writes a JSON record
(`run_id`, `task_id`, `arm`, `model_calls`, `refused_calls`, `wall_seconds`, `timed_out`,
`measured`).

Then the launch gate on the measurements (ADR-004 §3: a runner that cannot measure the count
refuses the launch):

```
python3 tools/agent_runner.py check-launch --records-dir "$RUNS"
```

Exit 0 = all 12 runs measured. Exit 2 = refused (an unmeasured run is named). Runs that hit the
call budget are reported as findings (429 refusals), not refusals.

The 20 scripted controls (10 `null-agent` + 10 `sabotage`, per the frozen control plan) call no
model; run them against the real tasks as the plan lists them, with the same runner and records
directory, and keep their records apart from the agent runs.

## 3. Score (gate re-verified before any scoring)

```
python3 tools/evaluate.py run --tasks "$VAULT/corpus/tasks.json" \
    --manifest "$CORPUS" --manifest-digest "$DIGEST" --runs "$RUNS" -o "$VAULT/results/m6-lite.json"
```

`--manifest` is verified by the launch gate **before any scoring**: exit 2 with the named problems
means refused, no report is produced. On success the report merges the run records per
`(task_id, arm)` into `summary.model_calls` (`runs`, `total_calls`, `over_budget`, `unmeasured`).

## 4. Human review trigger, second-review sample, evidence packages (ADR-004 §4)

For each task's evidence report `R` (one per arm and task), against its frozen Obligation Set `S`
and task manifest `M`:

```
python3 tools/review.py classify --report "$R" --set "$S"          # the obligations needing review, with their class
python3 tools/review.py sample --cases O1,O2,... --manifest-digest "$DIGEST" --run-id "$RUN_ID"   # ceil(20%), smallest hashes, no seed
python3 tools/review.py package --task "$M" --set "$S" --report "$R" -o package.json             # what the reviewer gets
```

The classes are the exact ADR-004 predicates (`timeout`, `unknown`, `insufficient-scope`,
`error`, `conditional-proof`, `clearly-violated`, `clearly-proven`); the free-text `scope` is never
read, and the package never contains the oracle. The external reviewer gets only the package; the
verdict is timed by the form (start, end, verdict, reason; interruptions reported separately), and
false positives/negatives are set by the hidden oracle, never by the reviewer.

## 5. Audit journal (procedural integrity, ADR-004 §5)

```
python3 tools/audit_journal.py append --journal "$JOURNAL" --set event=gate --set manifest_digest="$DIGEST" --set decision=allowed
python3 tools/audit_journal.py append --journal "$JOURNAL" --set event=run --set run_id=... --set task_id=... --set arm=... --set model_calls=...
python3 tools/audit_journal.py verify --journal "$JOURNAL"                                   # exit 0 = chain holds
python3 tools/audit_journal.py anchor --journal "$JOURNAL" --out "$VAULT/anchor.json" --operator "<operator name>"
python3 tools/audit_journal.py verify-anchor --journal "$JOURNAL" --anchor "$VAULT/anchor.json"
```

The journal is a hash-chained append-only JSONL; `verify` detects any tampered entry (exit 2).
The anchor writes the head (hash + seq + operator attestation) to an operator-chosen path outside
the agent's working directory. This stays **procedural** integrity: no report may claim the agent
was technically unable to reach the oracle while evaluator and agent share a machine.

## 6. Report (ADR-004 §6)

Report **task by task, with raw counts** (per arm: obligations proven/failed, model calls, refused
calls, wall time; the controls' outcomes against their expected results; the review cases and the
second-review sample). No general conclusion about performance, significance or security is drawn
from 12 runs; the stopping conditions of `core-v0.1.md` do not apply to M6-Lite.

## Refusal summary (all exit 2, nothing runs)

| Refusal | Command |
|---|---|
| corpus below the ADR-004 minimums / bad schema / bad artefact digest / bad control plan / bad manifest digest | `launch_gate.py check` |
| any agent run unmeasured | `agent_runner.py check-launch` |
| manifest or digest wrong at scoring time | `evaluate.py run --manifest ... --manifest-digest ...` (before any scoring) |
| tampered journal entry | `audit_journal.py verify` |
| anchor not matching the journal head | `audit_journal.py verify-anchor` |
| trust escape in the workspace (`assume`, `{:axiom}`, `{:extern}`, `expect`) | `dafny_backend.py verify` (no report) |
| detached marker (obligation not in the frozen set) | `dafny_backend.py verify` (exit 2, no report) |
