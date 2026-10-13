# Prior art landscape

## Four families of prior art

The material divides cleanly into four families that are routinely conflated. Family one is verification-aware languages an agent must satisfy: Dafny, Verus, F*, Lean, Rocq, Whiley [23][54]. Family two is contract-and-intent languages: Eiffel, and the recent cluster of projects adding contracts plus natural-language intent blocks [52][53][38]. Family three is agent-ergonomic languages optimised for token density, machine-readable references, or AI reasoning as a language primitive [41][42][46]. Family four is specification and evidence formats for agent workflows: spec IRs, intent packages, task briefs, and the IETF agent-security benchmark [20][40].

The relevant question is not which family Selyth resembles. It is which family contains an *adopted* system. The answer is unambiguous, and it is family one and the tooling layered on top of it.

## Direct competitors in the AI-native language space

The table below lists the projects that publicly claim Selyth's exact positioning — a language where human intent is a first-class, machine-consumable construct and verification gates agent output.

| Project | Positioning | Evidence of activity | Star count | Verified adoption |
|---|---|---|---|---|
| Djinnlang [1] | Spec-only language; LLM writes implementations and proofs; Dafny verifies; self-hosting | ETH Zürich (Henniger, Chong, Amin), submitted 21 Sep 2026 | n/a (paper) | Research; no ecosystem |
| Faby [43] | Intent blocks as first-class syntax; `ensure`/`expect` contracts; flows with explicit effects | Marketing site; community token launch on pump.fun | Unverified | None; token-funded |
| lhaig/intent [38] | Contracts + intent blocks resolving to verification points; Z3; multi-target | 339 commits, self-hosting stage1→stage3 bootstrap, 33+ ADRs | 7 | None |
| l3yx/IntentLang [42] | XML Intent IR guiding code generation; Python host | pip package; security notice warning it executes AI-generated code in-process | 98 | None |
| Fai [44] | ML-family; `example`/`forall` as checked declarations run as property tests | Rust compiler; samples verified by test suite | 4 | None |
| RIINA [39] | Formally verified, AI-native; 12,819 Coq `Qed`, 0 admits | 868 commits; llms.txt / llms-full.txt | 1 | None |
| KARN [41] | Token-minimal language for agents; multi-target codegen | v1.0.0, MIT, 128 tests; "76% fewer tokens" is a self-declared author estimate | 5 | None |
| Spec IR [40] | Language-agnostic IR separating WHAT from HOW | 3 commits, 1 contributor | 10 | None |
| namel3ss [45] | "Deterministic, inspectable applications"; deterministic manifests | PyPI, archived by maintainers | n/a | Abandoned |
| Mohio [46] | `ai.decide`/`ai.audit` as compiler-enforced primitives | Soft release | 0 | None |

Two patterns carry the whole argument. First, the upper bound of adoption across eighteen projects is 98 stars, and the median is under ten; the two largest are hobby-scale single-author efforts, and one has been archived. Second, and more telling, the two projects with genuine institutional backing both refused to build a verifier. Djinnlang lowers specifications to Dafny stubs and lets an LLM fill the proofs [1]. LemmaScript states it flatly in its design rationale: *"There is no new language. There is TypeScript, and there is a prover (Lean or Dafny). The toolchain bridges them."* [3] — the same decision reached independently, from the same starting position, by a different team.

Djinnlang deserves particular attention because it is not merely adjacent; it is the Selyth vertical slice, already built and published. A programmer writes specifications and never writes executable code; a symbolic translator emits Dafny stubs and proof obligations; a driver harness orchestrates an LLM to fill both implementations and proofs; the Dafny verifier checks the result; and the paper demonstrates self-hosting, with an LLM implementing the Djinnlang translator from its own specification [1]. Its additional contribution is an unambiguity constraint requiring the LLM to prove that the specification determines a unique output relation, leaving the model no latitude over program semantics. The paper's framing is worth quoting because it is also the answer to Selyth's framing: *"The arrangement doubles as a strong form of AI control: an untrusted model writes the code, yet its work is tightly checked by a verifier."* [1] That is proof-carrying code applied to agents, and it was published in September 2026.

## Infrastructure that already performs the verification role

The comparison that matters is not with other AI-native languages. It is with what an agent actually has available today.

- **Dafny** — MIT-licensed, verification-aware, roughly 18 years old, maintained by Amazon's Automated Reasoning group; Dafny compiles to Boogie, which produces verification conditions discharged by Z3 [23][24][26]. Used by Amazon to verify AWS authorisation logic and by Intel for hardware cryptographic libraries [27]. Recognised in the PL tooling community [47].
- **LemmaScript** — 114 stars, Tech Preview since April 2026, translates annotated TypeScript shallowly into Dafny or Lean 4. Its published case studies report 753 verification conditions with zero errors and zero assumptions, and four CVEs covered in brownfield code including CVE-2026-39409 and CVE-2026-39410 [2][4].
- **Claimcheck** — 32 stars, explicitly aimed at the intent gap: *"Dafny verifies proofs; claimcheck confirms intent."* It performs blinded round-trip informalisation and returns JUSTIFIED / PARTIALLY_JUSTIFIED / NOT_JUSTIFIED / VACUOUS verdicts, at a reported 96.3% accuracy in two-pass mode and 86.1% single-prompt [8].
- **Sponsio** — 454 stars, Apache 2.0, self-hosted. A deterministic policy engine gating agent tool calls using a fuzzy LTL monitor: 35 checks, zero LLM calls, under 0.01 ms per check, with verdicts of pass, block, warn, escalate, or redirect [22].

The asymmetry is the finding. Every language in the table above is between 0 and 98 stars. The verification and policy tooling runs from 32 to 454 stars, has a production deployment behind it, and is integrated by teams that did not build a language to get it [2][22]. Nobody has won the language. People have won the gate.

## Differentiation assessment

Against the four questions the Phase C brief poses, the evidence answers as follows.

**What is genuinely new.** Two things. First, the idea that a verification result should be a first-class, content-addressed, machine-readable artifact that a policy engine consumes — rather than a CI pass/fail — is not yet standardised for code, although the pattern is mature in supply-chain attestation [31][32]. Second, closing the intent gap *deterministically*, without an LLM in the loop, remains unsolved; Claimcheck is the state of the art and it is an LLM at 96.3% [8].

**What already exists.** Everything else. Intent blocks [43][38], compiler-checked intent [44], intent IRs [40][42], LLM-in-the-compiler with formal verification [1], agent policy gates [22], intent contracts on conventional languages [2], and intent-as-artifact in agentic SDLC proposals [46][37].

**What could be achieved with existing technology.** The entire acceptance-gate mechanism, without a new language: annotations plus a translator to Dafny [2][3], an SMT checker [23][24], a deterministic policy engine [22], and in-toto/SLSA-shaped attestations for provenance [31][32].

**What would require original research.** Deterministic validation of natural-language intent. Everything else on the roadmap is engineering.

**What Selyth should deliberately avoid reinventing.** The parser, the type checker, the solver, the proof language, the effect system, and the compiler backend. All five exist, are MIT-licensed, and are stronger than anything a solo effort will produce.

**Is a new language justified.** No, on current evidence.

**Would an IR and verification framework be a better starting point.** Yes, and this is the PIVOT.

---

## References


