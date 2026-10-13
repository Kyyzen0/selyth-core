# Negative scenarios (core-v0.1.md, milestone M5)

`tools/test-scenarios.py` runs these end to end with a real Dafny: Obligation Set, adapter, Dafny, report, policy engine. Each scenario states what plain Dafny says and what Core decides, because the question V0.1 exists to answer is whether the layer changes the decision a competent person would reach by running the prover directly.

The setting is `examples/`: the queue Obligation Set, its proof workspace, its policy, and a **lock** ([`lock-schema.json`](lock-schema.json)) holding the digests of the Obligation Set and the policy as a human approved them.

| # | Scenario | Plain Dafny | Core | Detected by |
|---|---|---|---|---|
| 0 | The correct implementation | verifies | `accepted` | |
| 7 | An off-by-one in a loop guard (`<` becomes `<=`), which still compiles, terminates and verifies with its own contract | verifies, exit 0 | `rejected`, `BLOCK-FAILED` on `OBL-0008` | the stated invariant `0 <= size <= capacity` |
| 8a | The agent weakens the obligation it violates to `invariant true` and regenerates everything | verifies | `rejected`, `INTEGRITY-OBLIGATION-SET-NOT-FROZEN` | digest of the set differs from the lock |
| 8b | The agent deletes that obligation | verifies | `rejected`, same rule; with a stale report, `INTEGRITY-COVERAGE` | lock; coverage |
| 8c | The agent edits the policy (softer `on_timeout`, a permissive rule first, a lower threshold, a new approver, an extra rule) and regenerates the report | not applicable | `rejected`, `INTEGRITY-POLICY-NOT-APPROVED`; if the report was not regenerated, `INTEGRITY-POLICY-NOT-PINNED` | digest of the policy differs from the lock, or from the report's pin |
| 9 | The same, through the command line | | exit 1, the rule named in the printed decision | |

## What these scenarios show, and what they do not

**The lock is what makes "detected by digest mismatch" true.** A report pins the policy it was produced under, but an agent that edits the policy produces a report pinned to its own edit; checked against itself, it is coherent, and without the lock the engine accepts the pair (the tests show this first, in each `(setup)` line). Only a digest approved outside the agent's reach exposes the edit. Scenario 8a is the sharp case: with a weakened obligation, Dafny proves the code and every digest in the report is internally consistent, and plain verification would call the change correct.

**The lock cannot protect itself.** An agent that also rewrites the lock gets the weakened run accepted (the last test says so). This is failure mode F3 of the decision register: Core makes tampering visible, not absent. The lock's integrity is procedural in V0.1: a signed commit, a protected path, a human approving the diff. A change to the lock must be reviewed like a change to the policy.

**The engine is, by design, hard to weaken through the policy alone.** Blocks (`failed`, `timeout`, no verdict, evidence below the set's requirement) come before any rule, so no rule edit turns a failed obligation into an accepted one. In scenario 8c only the softer `on_timeout` changes the decision at all (`rejected` becomes `needs-human`); the other edits would leave this report rejected, and the lock still refuses them, because a policy that differs from the approved one is not the policy. The edits that would matter most, lowering the evidence an obligation accepts, are not yet reachable: the report schema cannot yet express passing empirical evidence, so only machine-checked evidence can be accepted.

**Scope.** The incorrect implementation is a Dafny model, because translating TypeScript to Dafny is not part of this milestone; the report says the model's fidelity is not checked. Formatting or key order of the policy or the set is not an edit: the digests do not move.

## Using the lock

```
python tools/policy_engine.py lock policy.json obligation-set.json --approved-by project-owner > selyth.lock.json
python tools/dafny_backend.py verify ... --lock selyth.lock.json
python tools/policy_engine.py decide policy.json report.json obligation-set.json --lock selyth.lock.json
```

Commit the lock under the same protection as the policy. Regenerate it whenever a human approves a change to either.
