# The ledger: twenty obligations on a small money module

Element 6 of `docs/roadmap/core-v0.1.md`: a module with about twenty obligations, every one `kernel-proven` by a real Dafny. The module is **written for this purpose**, small and realistic (money in cents, no I/O), not an existing third-party project.

| File | Role |
|---|---|
| `ledger.ts` | The TypeScript the obligations are about |
| `ledger.obligations.json` | The Obligation Set: 5 preconditions, 12 postconditions, 2 loop invariants, 1 termination |
| `ledger.workspace.dfy` | The Dafny model of `ledger.ts`, with the markers that bind each obligation to its contract |
| `ledger.policy.json`, `ledger.lock.json` | The acceptance policy and the human-approved digests |

```
python tools/ts_adapter.py lower ledger.obligations.json --backend-version 4.11.0 > ir.json
python tools/dafny_backend.py verify --ir ir.json --workspace ledger.workspace.dfy \
    --obligation-set ledger.obligations.json --policy ledger.policy.json --lock ledger.lock.json
```

`tools/test-ledger.py` runs it all and `ts/test/ledger.test.ts` runs the same obligations on the TypeScript.

## What was measured

- **All twenty are proven**, in 17 s wall and 1.28 million resource units, the report accepted under the lock.
- **Each obligation catches a bug.** For every obligation the test injects a bug into the workspace that it must fail on, and all twenty do. This is the check against vacuous obligations, which would pass for any implementation.
- **It is the obligation that catches it.** Seventeen of the twenty bugs pass Dafny with no obligation attached (what a developer would run today) and are caught only because the obligation names the property. The three loop obligations overlap the invariant written by hand in the workspace and Dafny's own termination check, so Dafny alone also refuses those bugs.
- **A precondition no one calls is refused.** Without `Credit`, `LED-01` would verify vacuously; the backend reports it `unknown`, and the engine rejects the report.

## What it cost, and what it does not show

- **Cost is obligations times file size.** Each obligation re-verifies the whole workspace: about 63,000 resource units each for 118 lines. Twenty obligations are 17 s; two hundred on a file ten times longer would be a different tool. Restricting each run to the method of its marker is the obvious fix and is not done.
- **The workspace shapes the code.** With every marker blank the file must still verify, so the Dafny model is total where the TypeScript need not be: `result` is an `int` rather than a `nat`, `share` has an explicit zero branch, `Fee` guards its lemma call. It also needs proof scaffolding the obligations do not cover: one lemma (`MulMono`) and one loop invariant.
- **A precondition is proven against the workspace's callers.** `Credit`, `Spend`, `Charge`, `Limit` and `SplitBill` exist only to call the methods under verification. That `LED-04` holds says those five satisfy it, not that every caller of `withdraw` in a real code base does.
- **The model is written by hand.** `ledger.ts` and `ledger.workspace.dfy` can drift. `ts/test/ledger.test.ts` runs each obligation as a predicate on pseudo-random inputs of the real TypeScript, with a check that every obligation has one and that the predicates fail on wrong implementations. That is sampling, not evidence, and it is there only to bound the gap.
- **Integers.** Dafny's are unbounded; TypeScript's are doubles, exact below 2^53. Every arithmetic obligation lists that as an assumption, and nothing checks that callers stay below it: `fee(amount, bps)` multiplies before dividing and leaves the safe range for amounts above about 9e11 cents. The tests stay below 10^6.
- **Some obligations follow from others.** `LED-03` (`result <= cap`) follows from `LED-01` and `LED-02`; it is a restatement a reader may want, not an independent check.
