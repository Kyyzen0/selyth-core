# Validation plan

## Vertical slice scope

The smallest credible prototype is not a language. It is four components and one negative test, sized to build in weeks rather than months.

**The specification layer** is a small declarative language — on the order of fifty lines of grammar — carrying function contracts (preconditions, postconditions, invariants, termination) and an optional natural-language intent string per obligation. Contracts are the load-bearing part; the intent string is the experimental part, and it must be separable so the baseline can be measured without it.

**The lowering** translates specifications into Dafny, reusing LemmaScript's shallow-embedding approach: construct for construct, so the output is a Dafny program a person could have written, and Dafny's own machinery through Boogie and Z3 does the proving [6][23][24].

**The proof workspace** maintains the additions-only invariant between the generated file and the proof file, enforced mechanically and checked in CI, exactly as LemmaScript does [4][7]. This is the single most important structural element in the prototype, because it is what prevents policy weakening without requiring a trusted third party.

**The evidence emitter** produces an in-toto-shaped report: subject hash, obligation list, evidence kind, outcome, verifier identity, Resource Unit cost, and policy decision [31][32].

The negative test is deliberate and non-negotiable: introduce a change that breaks a stated invariant while leaving every pre-existing check technically passing, and confirm the system reports it and the policy rejects. TheBotCompany's finding makes the design target explicit — the gate is only worth building where criteria are *not* exactly evaluable, and the continuous-criteria projects in that study rejected at 5.3% and 18.6% [14]. A prototype that only catches obviously broken code has demonstrated nothing.

Scale: roughly twenty functions drawn from one real project, in one language, verified through Dafny. Vero's 43 repository-scale instances are out of scope by a wide margin — 10 were unsolved by every configuration, and 74% of successful proof lines were shared helper lemmas that agents rarely build [12][13]. The shared-lemma bottleneck is a finding about model capability, not a scope choice Selyth can engineer around at v1.

## Evaluation protocol

Comparing a Selyth-gated agent against an ungated agent is the wrong experiment, because the obvious confound is that the Selyth arm simply runs more tools. The protocol must control for that, and the field has recently documented exactly how badly such comparisons usually fail.

The **baseline** is the same agent, the same model, the same task set, with the identical Dafny checking harness available to both arms. Baseline A is the agent plus Dafny with no intent layer and no policy engine. This is the only honest control: it tests the *claim ledger and policy layer*, not the prover, which both arms share.

The **metrics** are false acceptance (a change accepted that violates a stated obligation) and false rejection (a change rejected that violates none). Both are counted, neither is optional. Verification latency is reported in Resource Units with a coefficient of variation, not in wall-clock seconds [24]. Human intervention is counted as review-minutes per accepted change. Computational cost is reported as peak RSS and CPU time on the declared hardware, so the €0 constraint stays a measured claim rather than an assertion.

Four contamination controls are mandatory, all borrowed from work that has already identified the failure.

Specs and obligations are held out and frozen, following Vero, where the curator supplies three frozen layers and the agent discharges obligations against fixed API signatures [12][13]. Nothing in the evaluated set was authored by the evaluating agent.

The verifier runs outside the agent's environment. Berkeley RDI's central finding is that all eight audited benchmarks were exploitable precisely because the submission shared the evaluation container [17]. The rule is that the evaluated system must not read, write, or influence the evaluation environment.

A null-agent floor is established. Run a null agent that takes no action. Its score is the floor; if it is not zero, the evaluation is broken [17][18].

The evaluator is itself tested adversarially, with a state-tampering agent that modifies the environment instead of solving the task [17], and with mutation testing to catch tests that pass while being gutted [21].

This protocol is not optional conservatism. OpenAI stopped reporting SWE-bench Verified after finding that at least 59.4% of an audited subset of unsolved instances had flawed tests rejecting functionally correct submissions — 35.5% narrow, 18.8% wide — and that frontier models reproduced gold patches verbatim [15]. Berkeley RDI subsequently found 16 distinct attack types across 13 benchmarks and 45 confirmed exploits [17][18]. Any evaluation of a verification gate that uses a test suite as its oracle is reproducing a design already known to be invalid.

The final question the protocol must answer: does Selyth provide benefit beyond adding existing verification tools to a conventional language? The honest answer is that it might not, and the experiment is designed to find that out. If the intent layer does not reduce false acceptance relative to the Dafny-only arm, that is a publishable result and it should be acted on.

## Acceptance criteria and stopping conditions

The prototype is validated when, on a held-out set of twenty-plus obligations across at least three projects:

1. False acceptance on deliberately incorrect modifications is zero, with the detection attributable to a named obligation rather than to incidental test failure.
2. False rejection on correct modifications is under 10%, measured against the same baseline arm.
3. The fast tier completes per unit in under two seconds and the slow tier under two minutes on the declared hardware, with a Resource Unit coefficient of variation under 20% [24].
4. The additions-only invariant demonstrably blocks a modification that weakens a proof, with the block traceable to a specific rule.
5. A policy file modified by the agent causes acceptance to fail rather than succeed.

The project stops, or the intent layer is removed, if either of two conditions holds: the intent layer shows no measurable reduction in false acceptance against the Dafny-only baseline, consistent with the vericoding finding that natural-language descriptions do not significantly improve performance [9][10]; or the fast tier cannot meet the two-second target, in which case the gate will be bypassed regardless of its soundness.

---

## References


