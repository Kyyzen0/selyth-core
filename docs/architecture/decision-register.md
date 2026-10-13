# Decision Register and Adversarial Design Review

**Status:** Design phase, reviewed 2026-10-13.
**Purpose:** every architectural question in Selyth carries an explicit status, so that no open question silently becomes a decision.

## Status vocabulary

| Status | Meaning | Reversible? |
|---|---|---|
| **DECIDED** | Accepted for the prototype. Build against it. | Only by a new ADR |
| **PROPOSED** | Recommended, awaiting a named validation | Yes, cheaply |
| **EXPERIMENTAL** | Requires measurement or research before it is adopted | Yes |
| **DEFERRED** | Outside the current scope, deliberately | Yes |
| **REJECTED** | Explicitly excluded, with a reason that can be revisited if the reason changes | Yes |

## Decision register

### DECIDED

| ID | Decision | Rationale | Recorded in |
|---|---|---|---|
| D-01 | Build the verification framework before the language | Phase C: the substrate already exists and is stronger; a new language starts at 0% LLM competence against Dafny's 82.2% | ADR-001 |
| D-02 | Invoke Dafny as a subprocess; do not depend on Boogie | Boogie's .NET requirement adds a managed runtime to a €0 single-machine project for no benefit, since Dafny already bundles the Boogie-to-Z3 pipeline | ADR-002 |
| D-03 | Core has no execution IR and no type system of its own | Makes stage-1/stage-3 independence structural rather than aspirational | ADR-001, `overview.md` |
| D-04 | Intent is a separately-graded object, never folded into the proof chain | The intent gap is unsolved; a 96.3% probabilistic check must not be able to produce a "verified" label | ADR-003 |
| D-05 | Evidence strength, outcome and provenance are three orthogonal axes | Collapsing them is the most common error in this design space; the report schema enforces the separation | `verification-engine.md` |
| D-06 | Acceptance is a total, deterministic function of (policy, report) | Replayability and diffability; a disputed decision can be re-derived rather than argued | `core-components.md` |
| D-07 | Schema-enforced claim ceilings | every conditional rule of the report schema is exercised by a fixture rejected for the intended reason, and the suite fails if one is not | `tools/test-validation.py` |
| D-08 | The additions-only invariant on proof artifacts | Tamper detection with no trusted third party — the only such mechanism available at €0 | `security-model.md` |
| D-09 | Bind to content digests, never to branches | A result applied to changed code is not a result | in-toto / SLSA convention |
| D-10 | Suppression is `forbidden` in V0.1 | Suppression is the practical path to policy weakening; nothing is lost by starting without it | `policy-schema.json` |
| D-11 | M6 launch parameters fixed before running: Claude Code, `claude-sonnet-4-6`, condition A1, 60 main runs, €0 marginal budget, evaluator in a separate private repo | Decisions taken before seeing results cannot be tuned to them | ADR-004 |

### PROPOSED — recommended, awaiting named validation

| ID | Proposal | Validated by |
|---|---|---|
| P-01 | Two verification tiers, fast under 2 s and deep under 2 min | Benchmark in `core-v0.1.md`; both are experimental targets, not guarantees |
| P-02 | V0.1 targets TypeScript via an adapter | First vertical slice; revisited once one adapter has run end to end |
| P-03 | Obligations are authored in a constrained DSL | Open question Q2 in `intent-and-ir.md`: whether a permissive format ages better |
| P-04 | Ownership and borrowing for the future language | An agent-success-rate experiment against a reference-counted baseline |
| P-05 | WASM as the first serious compilation target | Stage 3; no measurement possible before the language exists |
| P-06 | The ambiguity set is bounded, not open-ended | Open question Q3 in `intent-and-ir.md`; a bound is necessary and its location is unresolved |

### EXPERIMENTAL — requires measurement before adoption

| ID | Question | How it gets answered |
|---|---|---|
| E-01 | Does the intent layer reduce false acceptance? | The controlled experiment in `core-v0.1.md`. The vericoding benchmark reports that natural-language descriptions do not significantly improve outcomes, so the default expectation is that it will not |
| E-02 | Is semantic corroboration worth its cost at all? | Same experiment. Default is that the feature stays off |
| E-03 | Is ownership better than reference counting for agent-written code? | Borrow-error success-rate comparison at Stage 3 |
| E-04 | Does differential ambiguity detection reduce human review time? | Measured during the vertical slice, or the feature is cut |
| E-05 | Can the fast tier meet 2 s on real obligations? | Benchmark. If not, the gate will be bypassed regardless of its soundness |

### DEFERRED

| ID | Deferred | Revisit when |
|---|---|---|
| F-01 | Containerised or remote verification | The threat model includes a genuinely adversarial local agent |
| F-02 | Report signing and producer attestation | F-01 lands |
| F-03 | Suppression in narrowing-only form | The evaluation shows a real need for it |
| F-04 | Property-based test generation beyond the backend's own | A second backend needs it |
| F-05 | Multi-agent orchestration | Stage 3, and only if a single-agent loop is demonstrably insufficient |
| F-06 | Any user interface | Never, at this project's scale |
| F-07 | A second target language | One adapter has run end to end and the adapter contract has survived contact with reality |

### REJECTED

| ID | Rejected | Reason | Revisit if |
|---|---|---|---|
| R-01 | Building a Selyth Language now | Phase C: occupied space, no adoption for any comparable project, 0–98 stars across eighteen | The Core evaluation succeeds and a maintainer appears |
| R-02 | Owning a solver or proof language | Dafny, Boogie and Z3 are MIT and stronger | Never, realistically |
| R-03 | Refinement types in the language V1 | Measured cost: Roc's explicit refusal over compile time, ~10% annotation overhead, 20-second timeout failures on 66 of 782 DafnyBench programs | Compile-time budgets change by an order of magnitude |
| R-04 | Dependent types in the language V1 | 26.8% LLM success on Lean — the proof burden exceeds an agent-driven workflow | Agent proof competence improves substantially |
| R-05 | A universal IR representing all program semantics | Would force either language lock-in or a content-free document format | Never |
| R-06 | LLM-based evidence satisfying a required obligation | A probability is not a proof; this is the failure the project exists to prevent | Never |
| R-07 | Path-scoped policy rules as a containment mechanism | An agent that moves code moves it across the path boundary | Never, as a containment claim |
| R-08 | Claiming the gate is deterministic | Dafny is seed-dependent; 30% failure over 100 runs in one measured project | Never; the claim is about policy evaluation, not verification |

## Adversarial review

### Five architectural failure modes

**F1 — The gate is bypassed because it is slow.** The most likely way this project fails is not that the verification is unsound, but that it takes long enough that someone turns it off, and the bypass never appears in a report. Phase C measured the numbers: 66 of 782 DafnyBench programs fail on a 20-second limit, and real case studies run at 180 to 600-second limits. Mitigation is the tier split, and the honest countermeasure if the fast tier misses 2 s is to reduce what the gate claims to check — not to relax the target.

**F2 — The obligation set decays into documentation.** Obligations that nobody reads, that a refactor silently detaches from the code, that accumulate until nobody dares touch them. The additions-only invariant and the obligation digest make detachment visible; nothing makes the set *maintainable*. This is the most likely long-term failure and it is a social problem, not a technical one. The only known mitigations are small obligation sets and deleting obligations that no longer earn their place.

**F3 — The evidence chain becomes unfalsifiable in practice.** The schemas are correct and the tests pass, but a determined agent edits the obligation set, the policy, and the expected digests in one commit, and the diff is large enough that nobody reads it. Content addressing makes tampering *visible*; it does not make it *absent*. V0.1 accepts this and labels it procedural everywhere it appears.

**F4 — Dafny's ecosystem moves and Core does not.** The adapter contract pins tool identity and version in every report, so results remain interpretable across an upgrade, but an adapter written against one Dafny version may not survive the next. Mitigation is a pinned version in V0.1 and a re-verification step on any version change. This is a maintenance cost, not a correctness risk, provided the cache key includes the backend version — which it does.

**F5 — The language vision becomes a way to avoid measuring.** Stage 3 is a real, attractive destination, and building a language is more satisfying than running an experiment. The risk is that Stage 1 produces a framework nobody has needed twice and Stage 3 begins anyway, with the evaluation never run. The countermeasure is structural: the evaluation is defined in `core-v0.1.md` with stopping conditions, and the language roadmap states that Stage 3 opens only after the evaluation completes.

**F6 — Intent corroboration quietly becomes load-bearing.** The most seductive failure. A policy author sets `requires_intent_corroboration: true`, the feature reports high confidence, and it quietly becomes the thing reviewers trust. It is probabilistic, it is model-dependent, and it will be wrong sometimes in a way that is invisible. The schema keeps it below the blocking floor and the policy can require it only as an additional condition — but a human can still write a policy that treats it as sufficient, and the design cannot prevent that. It can only make the mistake visible in the schema's field description.

### Components that are more complex than they need to be

- **The evidence lattice with six rungs.** Four would cover V0.1. The two extra rungs (`machine-checked-model`, `runtime-asserted`) exist to stop a specific and likely overclaim, and that is a real reason, but the honest assessment is that the lattice is one rung wider than V0.1 requires.
- **The schema's cross-object rule between `decision` and `trust`.** Valuable, and the only rule that prevents an unapproved policy from producing an acceptance. Cheap. Keep.
- **The assumptions array.** Nearly always empty in practice, and the `non_empty_assumptions` policy flag is modelled but disabled. It stays because unstated assumptions are how a proof becomes false later, and because making them cheap to record now is cheaper than retrofitting.
- **The ambiguity record model.** Three detection mechanisms for a component whose value is experimental. If E-04 fails, this is the largest piece of removable complexity in the design.

### Lock-in exposure

| Dependency | Exposure | Assessment |
|---|---|---|
| Dafny | The whole verification path | **Highest.** Mitigated by the adapter contract, which is a serialised interface, so a second backend is additive rather than a rewrite |
| Z3 | Indirect, through Dafny | Low. Reachable through any other SMT solver |
| TypeScript as the V0.1 target | The first adapter | Low. The adapter contract does not mention TypeScript |
| The report schema | Selyth's own format | Self-owned, no external dependency |
| Git | Approval mechanism | Acceptable, and replaceable |

The genuine lock-in risk is not a vendor; it is **Dafny's interface plus one adapter**. If Dafny's CLI changed shape and no second backend existed, Core would have one path. Building the adapter contract as data rather than code is the mitigation, and it is why the contract requires capability declarations up front.

### Assumptions that lack evidence

Stated as assumptions rather than findings, because that is what they are.

- That an agent can work through borrow errors at an acceptable rate. No measurement exists. Underlies P-04.
- That a 2-second fast tier is achievable on real obligations. No measurement exists. Underlies P-01.
- That the intent layer reduces false acceptance at all. The available benchmark evidence points the other way. Underlies E-01, and it is the assumption the whole differentiation strategy rests on.
- That the additions-only invariant transfers to languages other than the one modelled. LemmaScript demonstrates it for one workflow; transfer is assumed.
- That a human will read the diff containing a policy change. Assumed throughout the content-addressing design, and unverifiable from inside the system.
- That content addressing survives as the dominant artefact identity mechanism. Assumed, not depended on structurally; the report binds to a digest field, not to a specific hash function beyond `sha256`.

### Choices that would make the future language harder to build

- **Requiring explicit, persistent obligation identifiers.** This constrains the source syntax from day one and is expensive to retrofit, because every existing fragment would need naming. It is nonetheless required, because reports reference obligations by identifier and a positional scheme makes the evidence trail worthless after the first refactor.
- **Putting obligations in a separate artefact from code rather than inline.** Convenient for versioning, but it means every function the gate understands lives in two files, and the association between them is maintained by hand.
- **Making the obligation set the verification source of truth rather than the source code.** This is what enables language independence, and it is the single decision that most shapes Stage 3.
- **Fixing the evidence lattice in the schema rather than making it configurable.** Cheaper and more auditable; also means a future evidence kind is a schema version bump rather than a policy change.
