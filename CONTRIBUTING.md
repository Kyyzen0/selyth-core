# Contributing to Selyth Core

Selyth Core is MIT-licensed (`LICENSE`); by contributing you agree that your
contribution is licensed under the same terms.

## Before you start

Read `MANIFESTO.md` and `README.md`. The design rule that matters most for
changes: **the specification is normative, the code follows it**
(`docs/specifications/policy-engine.md` says so explicitly, and the same holds
for every file under `docs/specifications/`). Change the document first, then
the implementation, then the tests. Every behaviour the engine exhibits is
pinned by a scenario in `tools/test-policy-engine.py`; a change that alters a
decision must alter the document, the scenario and the message together.

Everything fails closed: an input the engine does not understand is rejected or
escalated, never accepted. Keep it that way — a new `accepted` path needs a
reason in the decision register, not just a rule.

## Running the tests

```
make test-python     # all Python suites; needs only Python >= 3.9 (builds .venv from the hash-locked requirements.txt)
make test-ts         # the TypeScript port, byte-compared with the Python adapter; needs Node >= 24
make test-properties # the property-test backend against a real Node (>= 22.6); needs Node
make test-dafny      # the Dafny backend against a real Dafny 4.11.0 (SELYTH_DAFNY=<path> if not at ~/.local/opt/dafny/dafny)
make check           # the whole gate on one example in one command (EXAMPLE=ledger|gcd|queue): validate,
                     # workspace regeneration check, lower, Dafny verify, accept under the lock, replay,
                     # property backend when the example has one; needs Dafny (Node too for EXAMPLE=gcd)
make test            # everything
```

CI (`.github/workflows/ci.yml`) runs all of these on every pull request; a PR
with a red job is not mergeable.

## Making a change

1. Branch from `main`: `git checkout -b vibe/<short-slug> <base>`.
2. One focused change per pull request. Add or update tests in the same
   commit — the suites are the specification's executable form.
3. Keep `decide`, `lower` and the canonicalisation functions pure: no file,
   network, clock, process or randomness inside them. There are tests that
   mock exactly those and fail if you touch them.
4. If your change touches a schema, the fixtures that must be rejected for the
   intended reason live in `tools/test-schemas.py` / `tools/test-validation.py`;
   a conditional schema rule with no fixture fails the suite.
5. Update the normative document in the same PR, and the repository layout in
   `README.md` if you add a file.

## Architecture decisions

Open questions never silently become decisions: add a row to
`docs/architecture/decision-register.md` (PROPOSED / EXPERIMENTAL / DEFERRED /
REJECTED) and, for a decision that binds the project, an ADR under
`docs/decisions/` in the format of ADR-001..ADR-005. The most anticipated
contributions are backend adapters (the adapter contract is designed to be
additive), but note the README's caveat: the contract has not yet survived
contact with a second implementation.

## Reporting a problem

Open an issue with a minimal reproduction: the three JSON documents (policy,
report, Obligation Set) and the command you ran. Do not post secrets, tokens
or private evaluation material in issues or PRs — the M6 oracle and control
plan live in a separate private repository and must stay there.
