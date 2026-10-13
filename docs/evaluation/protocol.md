# M6 evaluation protocol

This document turns the design of [`core-v0.1.md`](../roadmap/core-v0.1.md#evaluation) into a procedure and says what is ready, what the harness has already shown, and which decisions only the project owner can make. It is written **before** any agent has been run, so that the stopping conditions are not renegotiated after the result.

## What is ready

| Piece | Where |
|---|---|
| The gates under comparison (three arms, one prover) | `tools/evaluate.py` |
| An oracle that labels submissions without trusting the tools under evaluation | `ts/eval/oracle.ts`, over `ts/test/ledger-properties.ts` |
| A scripted corpus of 23 submissions (control, correct refactors, bugs, a specification gap, tampering) | `docs/evaluation/tasks.json` |
| A test of the instrument | `tools/test-evaluate.py` |
| Null-agent control, tampering submissions | in the corpus (`N00`, `X1`..`X4`) |
| Mutation test of the obligations | `tools/test-ledger.py` (one bug per obligation, plus bugs written in the TypeScript) |

## The arms

All three share the same Dafny, the same translator, the same code. They differ only in what decides.

| Arm | Gate |
|---|---|
| **A0, Dafny alone** | The model translated from the code, verified with no obligation attached (`--allow-warnings`, the most favourable flags). What a developer gets with no specification. |
| **A1, obligations without governance** | The same prover on the obligations and the policy of the working tree, which the agent can edit. Accepts when every obligation is proven. No human-approved lock. |
| **B, Core** | A1 plus the lock: the digests of the policy and of the Obligation Set as a human approved them. |

A1 and B are **one verification run decided twice**, so a difference between them is the governance layer and nothing else. The baseline of the design document ("same Dafny checking available, and nothing else") is A1 when the agent has been given the obligations, A0 when it has not; the real experiment has to say which, and this protocol proposes to report both.

## Ground truth

A submission *violates* when it breaks a stated obligation. The label is never Dafny's verdict:

1. the curator labels each submission in `tasks.json`, before running any arm;
2. the oracle runs the obligations as predicates on the submitted code (pseudo-random inputs, a fixed seed) and must agree; a task it contradicts is excluded from the rates and listed as unreliable.

The oracle is sampling, not proof. It sees fewer violations than Dafny (for a withdrawal that takes one cent too much it names `LED-05`; Dafny also fails `LED-06`, which needs `amount == balance`). That is why the label is one boolean per submission, "breaks at least one obligation", and why a violation the oracle misses would surface as a *false acceptance the oracle cannot see*: the number reported is a floor. The preconditions are not violated by a change, only no longer needed, so the oracle reports those apart.

What the metric does **not** reward: a submission that is wrong but within the obligations (`G01`, clamp returning `lo` above the range). Correct acceptance by this definition; a reminder that the obligations bound what the gate can notice.

## Metrics

| Metric | Measured by the harness | Measured how |
|---|---|---|
| False acceptance | yes | accepted and the oracle says it breaks an obligation |
| False rejection | yes | rejected and the oracle says it breaks none; refusals of code outside the translated subset are counted separately |
| Verification latency | yes | resource units of the A1/B run, wall-clock beside it |
| CPU | yes | CPU time of child processes, read once around the whole run (not per submission) |
| Unauthorized spec change detection | yes | rate at which the tampering submissions are rejected |
| Peak memory | **no** | to add on the declared hardware |
| Human intervention (review-minutes per accepted change) | **no** | needs people; see decisions |
| Maintenance overhead | **no** | needs a sequence of changes by an agent |

## Contamination controls

| Control | Status |
|---|---|
| Null-agent floor | in the corpus (`N00`): accepted, breaks nothing. For a real run the floor is the score of an agent that submits nothing on the real tasks; if it is not zero the run is void. |
| Adversarial evaluation of the evaluator | `X1`..`X4`: a submission that edits the Obligation Set, the proof model or unmarks a method instead of fixing the code. |
| Mutation testing of the obligation set | done for the ledger (`test-ledger.py`). |
| Verifier outside the agent's environment | **procedural only.** The lock and the policy are files in the same tree; a real run must keep the lock, the oracle and the harness out of the agent's reach (separate user, separate checkout). One machine is not a security boundary (`security-model.md`). |
| Held-out obligations authored by someone else | **not met.** The ledger's obligations were written by the same author as the tooling and the corpus. A valid M6 needs a curator who is neither. |

## What the scripted corpus has shown

Run `python tools/evaluate.py run --jobs 4` (needs Dafny 4.11.0 and Node 24). On the current corpus (the table is a snapshot of scripted submissions, not a claim about agents):

| arm | false acceptance (of 13 that break an obligation) | false rejection (of 10 that break none) | tampering detected |
|---|---|---|---|
| A0, Dafny alone | 11 of 13 (0.846) | 1 (`C08`, untranslatable) | 0 of 4 |
| A1, obligations, no lock | 2 of 13 (0.154): `X1`, `X2` | 1 (`C08`) | 2 of 4 |
| B, Core | 0 of 13 | 1 (`C08`) | 4 of 4 |

Verification of the A1/B run: about 1.27 million resource units and 23 s per submission (Dafny alone: 1.1 s). The null agent is accepted by every gate and breaks nothing. No label is contradicted by the oracle.

Reading it:

- **B accepts nothing that breaks an obligation; A1 does** when the submission also edits the Obligation Set (`X1`, `X2`): the same prover proves the weakened set. That difference is the lock, and it is the only difference the design says to look for.
- **On plain bugs A1 and B are identical**, because they share the prover. Anyone expecting the governance layer to catch more *bugs* than the prover will be disappointed; what it adds is that the prover cannot be told what to prove.
- **A0 accepts most bugs**: with no specification, Dafny verifies a model that does what the code does. It also accepts `X3` (`assume false;` in the model), which only warns; the backend refuses it.
- The two bugs in the loop (`V05`, `V09`) are rejected even by A0, through the hand-written loop invariant: the overlap recorded in the ledger README.
- **The false-rejection column is the cost.** `C08` (a correct `Math.trunc`) is refused because the translator reads a small subset; that is a cost of the translation, paid by every arm. The first run also exposed a translator fault the unit tests had missed (`-amount + balance` failed to verify under Dafny's `nat` typing), now fixed: the corpus is already doing its job.
- Nothing here says how often a real agent produces such submissions. That is the experiment.

## Decisions only the project owner can make

The harness runs whatever submissions it is given. These choices decide whether the result means anything:

1. **The agent and its model.** Which coding agent, which model, what instructions. The same for both arms; whether the arm A agent is *shown* the obligations (A1) or must write its own specification (A0) is the main design question.
2. **The tasks.** The ledger is a small, purpose-written module: a real task set needs code the project did not write, with obligations written by someone who is not the harness author, and enough tasks that a rate means something (the corpus has 23 scripted submissions and 13 that break an obligation: it can show an effect exists, not estimate its size).
3. **Repetitions and budget.** Agents are stochastic: how many runs per task and per arm, token or quota and wall-clock ceiling per run. There is no monetary spend: the marginal budget is €0 by design (`core-v0.1.md`), which constrains the agent as well.
4. **Human review time.** The primary metric ("review-minutes per accepted change") needs people reading changes. Who, how many, and how the minutes are recorded.
5. **Isolation.** Where the lock, the oracle and the harness live so the agent cannot read or write them.

## Stopping conditions (unchanged, restated)

Fixed in [`core-v0.1.md`](../roadmap/core-v0.1.md#stopping-conditions) before any run and not to be revised after:

- remove the intent layer if it does not reduce false acceptance against A;
- stop the project if the fast tier cannot meet 2 seconds on real obligations, or if false rejection exceeds 10% against A;
- add a second backend if the policy engine turns out to need backend-specific knowledge;
- narrow the claims if no difference between A and B can be shown.

Two observations from the scripted run bear on them and are recorded now rather than later. Verification of the twenty obligations takes about 24 s per submission, nowhere near the 2-second fast tier (the cost is obligations times file size, `dafny-backend.md`); a real run on this module will fail that condition unless the backend verifies only the method of each marker. And the intent layer is not built, so its condition cannot be tested yet.

## Procedure for the real run

1. Fix the five decisions above and commit them (this file) before running.
2. Freeze the task set, the obligations, the policy and the lock; record their digests.
3. Run the null agent; stop if its score is not the floor.
4. Run the tampering agent; stop if the gate accepts it.
5. For each task and repetition, give the agent the task in a fresh checkout of each arm; collect its submission (a patch to the code and to anything it can write).
6. Score the submissions with this harness (`evaluate.py` with the corpus replaced by the collected patches), the oracle labelling them.
7. Report every metric above, including the ones that go against the project, and the unreliable labels.
