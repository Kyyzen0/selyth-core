# Selyth — Architecture Overview

**Status:** Design phase. No implementation exists.
**Depends on:** Phase C research (see `docs/research/prior-art.md`, `docs/research/technical-risks.md`).

## The four stages

Selyth is built in four stages. The stage order is the architectural decision that everything else follows from.

| Stage | Deliverable | Language-dependent? |
|---|---|---|
| 1 — Selyth Core | Verification framework: obligations, evidence, reports, acceptance policy | No |
| 2 — Selyth Intent | Structured intention and specification system | No |
| 3 — Selyth Language | Original language with its own syntax, type system, compiler | Yes |
| 4 — Selyth Universal | Native and cross-platform targets, libraries, tooling | Yes |

The fundamental invariant — *Selyth Core must work independently of Selyth Language* — is not a slogan. It is enforced by one structural choice: **Core contains no execution intermediate representation and no type system of its own.** Core reasons about obligations and evidence. It never needs to know what a program does, only what was claimed about it and what was shown. A future Selyth Language becomes one more producer of obligations and one more verification target; nothing in Core changes when it arrives.

## Resolving the "not a wrapper" constraint

The vision requires that Selyth "must not become merely a wrapper around existing languages", and Phase C concluded that Dafny, Boogie and Z3 must be reused. These look contradictory. They are not, but only because "wrapper" needs a testable definition.

Selyth Core is a wrapper if — and only if — running the underlying tools directly would produce the same acceptance decision. If that is true for any given obligation set, the layer is decoration and should be deleted.

The test, applied per change:

> Remove the Selyth layer. Run `dafny verify` yourself, read the output, and decide accept or reject. Would your decision, and your confidence in it, differ from Selyth's?

If the answer is "no", the layer has not earned its place for that obligation. If the answer is "yes" — because the report binds the result to a content hash, records the tool identity and cost, distinguishes proven from tested from asserted, and applies a human-approved policy as a deterministic function — then the layer is doing work that running the tool does not do.

Selyth is not a wrapper around a language. It is a governance and evidence layer over a prover. Those are different products, and the distinction is only worth defending if the evaluation in `docs/roadmap/core-v0.1.md` shows the layer changes decisions. It may not. The design is built to be deleted if it does not.

## Layer model

Core is a pipeline of five artifacts, each with one job. The boundaries between them are the design.

```
  Intent Document  ──human-authored, natural language──┐
                                                     │  both are hashed
  Obligation Set    ──human-authored, formal────────┤  into the report
                                                     │  subject
        │
        ▼
  Verification IR   ──per-backend lowering target──► Verification backend
        │                                              (Dafny, type checker,
        │                                               property tests)
        ▼
  Evidence Report   ◄──backend results, normalised──┘
        │
        ▼
  Policy Decision   ◄──policy evaluation, total function──► accepted
                                                          rejected
                                                          needs-human
```

Each arrow is a versioned, content-addressed artifact. Nothing crosses an arrow in memory; everything crosses it as data with a digest. This is what makes a report falsifiable: you can re-derive every input from the digests it carries.

**There is no arrow from the Obligation Set to an execution IR.** That arrow is what Stage 3 adds, and Core is built so that its absence is architectural rather than merely unfinished.

## Component responsibilities

| Component | Owns | Never does |
|---|---|---|
| Intent & Specification | Intent text, obligations, assumptions, ambiguity records | Interpret code |
| Verification IR | Per-backend lowering, backend capability declaration | Decide acceptance |
| Verification Engine | Running backends, normalising results, costing runs | Judge adequacy |
| Evidence Report | The record; schema-enforced claim ceilings | Carry anything not evidenced |
| Policy Engine | Deterministic evaluation over policy and report | Modify either input |

The single most important separation is the last row. The verification engine produces evidence and the policy engine decides. An engine that both verified a change and decided whether that change was acceptable would be grading its own homework — the failure NIST documented when agents removed pre-existing checks to make a task pass.

## Data flow

1. A human authors an Intent Document and an Obligation Set, and commits them.
2. The Obligation Set is frozen by digest. Every later report names the digest it was produced against.
3. A backend adapter lowers the obligations into its own input format and declares what evidence kind it is entitled to produce.
4. The engine runs the backend, records the outcome, the tool identity and the cost, and normalises everything into an Evidence Report.
5. The policy engine evaluates the report against the pinned policy. The evaluation is a total function: same inputs, same decision, every time.
6. The decision and the report are committed. The next change diffs against them.

## Dependencies and cost

Everything in Stage 1 runs on one ordinary machine with no paid service.

| Dependency | Licence | Role | Why it is acceptable |
|---|---|---|---|
| Dafny | MIT | Primary verification backend, bundling Boogie and Z3 | Single self-contained binary, no separate runtime to install |
| Z3 | MIT | SMT solver, reached through Dafny | |
| Boogie | MIT | IVL, reached through Dafny | See below: not a direct dependency |

Boogie is licensed permissively but requires .NET Core and a separately installed SMT solver. Depending on it directly would add a managed runtime and a second solver installation to a €0, single-machine, minimal-footprint project for no benefit, because Dafny already ships the Boogie-to-Z3 pipeline. **Selyth therefore invokes the Dafny command-line tool as a subprocess and does not depend on Boogie at all.** This is a packaging decision with a licensing reason attached, and it is recorded in ADR-002.

## What this architecture does not establish

- It does not establish that the obligations match human intent. Nothing in this pipeline can. The intent gap is real and is tracked in `docs/architecture/intent-and-ir.md` as an open research question, not a solved component.
- It does not establish security on a single machine. `docs/architecture/security-model.md` states the limitation precisely: V0.1 provides structural integrity, not isolation.
- It does not claim repository-scale verification. Vero measured the strongest frontier agent at 27 of 43 multi-module instances, with 10 unsolved by any configuration.
