# ADR-005 — MIT licence for Selyth Core, with a completed dependency licence audit

**Status:** Accepted
**Date:** 2026-11-19
**Deciders:** Project owner
**Related:** ADR-001 to ADR-004, `MANIFESTO.md`, `docs/architecture/decision-register.md` (D-12), `README.md`

## Context

The README left the licence "not yet chosen" and called licence verification of every dependency a blocking pre-implementation task. The manifesto commits to a €0 marginal-cost, single-maintainer project (principles 8 and 11: no paid API, no external service in the decision function), and the architecture assumes permissive licensing throughout, because a project of this scale cannot absorb a proprietary or paid component.

## Decision

Licence Selyth Core under the MIT License (`LICENSE`), and record the dependency audit the decision requires.

Licences of the pinned dependencies, as shipped by the lockfiles:

| Component | Pinned by | Licence |
|---|---|---|
| jsonschema, jsonschema-specifications, referencing, attrs, rpds-py | `requirements.txt` (hash-locked) | MIT |
| typing-extensions | `requirements.txt` (hash-locked) | PSF-2.0 |
| ajv, @types/node | `ts/package-lock.json` | MIT |
| TypeScript | `ts/package-lock.json` | Apache-2.0 |
| Dafny, Boogie, Z3 | subprocess toolchain, Dafny 4.11.0 with bundled z3 4.12.1 | MIT (see decision R-02) |

All permissive: no copyleft, no proprietary, no paid component anywhere in the execution, verification or acceptance path.

## Consequences

**Positive.** MIT is the lowest-friction licence for the contributions the project actually anticipates (backend adapters, per the README), it is compatible with every audited dependency, and it matches the €0, single-maintainer constraint: it asks nothing of contributors and encumbers nothing. The audit being recorded here closes the "blocking pre-implementation task" the README named, for the pinned set; any future dependency must be checked against this table before it enters a lockfile.

**Negative.** MIT lets a fork keep its improvements proprietary. Accepted: the project's value is the method and the published evidence, not secrecy — the manifesto already commits to publishing measurements that reduce the apparent value of its own thesis.

**Neutral.** The licence of Core says nothing about the licence of artefacts Core verifies (obligation sets, proof workspaces, reports); those remain the property of their authors.

## Alternatives considered

**Apache-2.0.** Adds an explicit patent grant and contribution terms. Heavier than a single-maintainer project needs today; revisit if corporate contributors appear and ask for patent protection.

**A copyleft licence (GPL/LGPL).** Rejected: nothing in the project requires it, and copyleft creates licence friction for the agents and downstream tools the gate is meant to be embedded in. The subprocess toolchain is MIT, so no licence of Core can or should be stricter than the ecosystem it invokes.

**No licence (all rights reserved).** Rejected: an unlicensed public repository is unusable by contributors in practice, and the README had already made the choice a blocking task.

## References

- `MANIFESTO.md`, principles 8 and 11
- `docs/architecture/decision-register.md`, D-12 and R-02
- `requirements.txt`, `ts/package-lock.json`
- Dafny licence: MIT (`dafny-lang/dafny`)
