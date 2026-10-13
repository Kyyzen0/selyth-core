# Selyth Language — Long-Term Vision and Design Options

**Status:** Design phase. Nothing in this document is authorised for implementation.
**Read first:** `docs/architecture/overview.md` for why the language is Stage 3 and not Stage 1.

## Design principles

Five principles, in priority order. When two conflict, the earlier one wins.

1. **Agent-writability is a correctness property, not a convenience.** Code an agent produces must be mechanically checkable by a gate the agent cannot influence. A feature that makes code pleasant to write but impossible to verify is a net loss in this ecosystem.
2. **Every effect is declared.** The verification report's value depends on knowing what a unit of code touched. A language that hides its effects produces reports with blind spots.
3. **The language never decides.** It produces obligations and admits to verification. Acceptance belongs to the policy engine, in Core, permanently.
4. **Honest failure beats optimistic success.** An unrepresentable state, a rejected change, or an unavailable backend are all correct outcomes. A wrong answer that passes the gate is the only unacceptable one.
5. **Boring where boring works.** Novelty in Core is where the project needs it. Novelty in the language is a cost paid for nothing.

## Dual representation

Humans should write readable source; agents should manipulate a structured form with stable identifiers and explicit semantics. Both must describe the same program.

The resolution is two projections over one obligation set, not two representations that must be kept in sync:

- **L1, the Obligation Set**, is the source of truth for verification. It is what the gate reads and what a report names by digest.
- **The human source** is the source of truth for humans. It is what gets read, reviewed and refactored.

A round trip between them is desirable for tooling but is **not** on the acceptance path. The gate never requires a round trip to be lossless, because a lossy round trip that still produces the same L1 digest is not a failure — the obligation set is what was verified.

Stable identifiers matter more than they first appear. A report says "OBL-0007 was proven". If identifiers are positional or content-derived, every refactor invalidates every report and the evidence trail becomes useless. Selyth therefore requires **explicit, persistent obligation identifiers** that survive refactoring. This is a real constraint on the source syntax: obligations must be nameable, not inferred from position.

## Type system

Phase C's finding constrains this section more than taste does. The most advanced verification approaches score worst with language models — 44.2% for Verus and 26.8% for Lean, against 82.2% for Dafny. And the ecosystem's own language designers have repeatedly declined the same features on cost grounds. A type system chosen for elegance rather than for verifiability would produce a language whose proofs nobody, human or agent, can discharge.

### Recommended for V1

**Algebraic data types with exhaustive pattern matching.** The strongest candidate in the set, and the only one with a three-way justification. Structurally, it makes illegal states unrepresentable, which is the cheapest class of bug removal available. Practically, exhaustiveness checking gives the gate a check that needs no solver and no time budget. For agents, it converts "did I handle every case" from a review question into a compile error, which is the single most valuable thing a gate can hand an agent that has to work without a human reading the output.

**Immutable-by-default data with explicit mutation.** Immutability keeps proof contexts small, which keeps resource-unit cost down, which is what keeps the deep tier inside its budget. Mutation remains available and is declared.

**Static typing with inference.** Inference reduces annotation burden, and annotation burden is a measured cost: refinement-type systems in the literature carry roughly 10% annotation overhead on total source lines. Selyth's obligations are explicit by design, but the surrounding types should be inferred.

**Declared effects, recorded rather than deeply inferred.** An effect row on each function, propagated and checked, with the result written into the verification report. The point is not to build a full effect system; the point is that the report can then state which capabilities a verified unit touched. Deep effect polymorphism — row types, effect handlers, effect subtyping — is deferred.

### Explicitly rejected for V1

**Refinement types.** Rejected on measured cost, not on principle. Roc's language FAQ states the plan is never to have linear, dependent, refinement or uniqueness types, giving the reason directly: fast compile times are a primary goal and a major downside of refinement types is an exponential increase in compile times. The measured record agrees: on DafnyBench, 66 of 782 programs failed on a 20-second verification limit, and liquid-type systems carry around 10% annotation overhead. Against a €0 budget, one machine, and a gate that must return inside human attention span, this is the wrong side of the trade. It may return in a later stage if the cost profile changes.

**Dependent types.** Rejected on the same evidence from the other direction: Lean's 26.8% LLM success rate is the clearest available signal that the proof burden exceeds what an agent-driven workflow can discharge. A language whose proofs must be written by a human expert is a language that fails Principle 1.

**Linear types and ownership in V1.** Deferred, not rejected — see memory management. The reasoning is in the next section and the trade is genuinely close.

**Implicit coercion, operator overloading, and untyped escape hatches.** Each makes "does this code still do what the obligation says" harder to answer mechanically. Each is rejected.

### Deferred to V2 and beyond

Row-polymorphic effect inference, effect handlers, higher-rank polymorphism, type-level programming, gradual typing for interop.

### Where capabilities live

Capability-based security is **not** a type-system feature in Selyth. Capabilities in this project are policy-governed and change at runtime — a waiver narrows one, a policy update changes what is required — and a static type cannot express a decision that depends on which policy document is pinned. Capabilities therefore live in the evidence and policy layers, and the language's contribution is to *declare* what a unit touches so the policy layer has something to reason about. A language-level `Effect` row is the interface; the judgement is not.

## Memory management

This is the hardest genuine trade in the language design, and it should be presented as one rather than resolved by assertion.

| Approach | Performance | Safety | Agent ergonomics | Blocks the systems goal? |
|---|---|---|---|---|
| Tracing GC | Lowest peak-memory pressure; pauses | Safe by default | Easiest — no ownership rules to violate | No |
| Reference counting | Predictable, immediate reclaim | Cycles leak unless handled | Easy in the common case | No |
| Ownership and borrowing | Best; no runtime | Safe; compile-time | **Hard** — borrow errors are hard to act on | No |
| Region-based | Good; needs annotations | Safe with lifetime inference | Middling; annotation-driven | No |
| Hybrid (ARC + optional GC) | Good | Safe | Middling | No |

The forcing function is the vision: the language is meant to cover systems programming as well as desktop, web and mobile. That range is not served by one memory strategy, and a language that tries to serve it with one will serve none of it well.

The argument for ownership is that it composes with everything else already recommended: effects, immutability and proof obligations all assume a static, analysable ownership discipline, and a garbage collector reintroduces precisely the nondeterminism the pure execution profile is meant to exclude. The argument against is empirical and specific to this project: borrow-checker error messages are a known weak point for language models, and a gate that an agent cannot pass without a human reading compiler output contradicts Principle 1.

**Recommendation: DEFERRED, leaning ownership and borrowing.** Concretely, the decision is deferred to Stage 3, and the precondition for making it is a measurement — an experiment comparing an agent's success rate against borrow errors versus a reference-counted baseline on the same tasks. Making this decision now, on taste, would be exactly the failure Phase C warned about.

## Execution and compilation

**Interpreter or bytecode VM first, WebAssembly second, native third.** WASM is the natural first serious target: it is deterministic by specification, sandboxed by construction, has a single mature toolchain, costs nothing to run, and reaches the browser, the edge and every mobile platform from one artefact. It is also the target where a bug is least costly, which matters for a language with no production history.

Native compilation comes later, through Cranelift before LLVM: Cranelift builds faster and is easier to debug, and a project with one maintainer and a two-minute deep-verification budget should prefer the tool that iterates faster. LLVM arrives when there is a measured need for its optimisation.

**No backend provides universal platform compatibility, and the language will not pretend otherwise.** Desktop, mobile, web and embedded differ in memory model, floating-point semantics, threading and system-call surface. A backend target declares which of those it pins and which it leaves unspecified, and the language guarantees nothing across an unspecified boundary. The portable core is the `PURE` execution profile; everything else is a target with a documented trust boundary.

## Determinism profiles

Three named profiles, each with a guarantee and a non-guarantee, stated together.

| Profile | Guarantees | Explicitly outside the guarantee |
|---|---|---|
| `PURE` | Total functions, no ambient state, reproducibility from a content hash | Solver nondeterminism; verification is not a soundness input |
| `IO` | Decision determinism: identical inputs and identical environment observations produce reproducible control flow | Floating-point semantics; wall-clock; any undeclared effect |
| `CONCURRENT` | Declared ordering, or a proof obligation that ordering is irrelevant | Scheduling, and therefore reproducibility, of any given run |

The language states the non-guarantees in the same place as the guarantees, and writes both into the verification report's `trust.limitations`. A determinism claim without an adjacent list of what it excludes is a marketing claim, and the report schema requires a non-empty limitations list on every report precisely so that this cannot be omitted.

## What the language deliberately does not inherit

It does not reimplement a solver, a proof language, a type-inference engine for advanced features, a package manager with a central registry, or a language server protocol implementation. Each exists, each is MIT-licensed or has a free implementation, and each is a Stage 4 concern at the earliest.
