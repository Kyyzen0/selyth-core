# Security Model: Threat Model, Trust Boundaries, Isolation Requirements

**Status:** Design phase.

## The honest starting point

On a single macOS machine, with one human user and one agent process, **there is no security boundary between the agent and the verifier.** Both run as the same user, on the same filesystem, with the same permissions.

This must be stated first because everything else in this document is a consequence of it. A separate process is not a security boundary. A subprocess is a reliability boundary — it isolates crashes, gives a clean serialised interface, and can be moved elsewhere later — but it does not isolate privilege, because the agent can read the verifier's files, write to its working directory, and observe its arguments.

Berkeley RDI's finding is the reference case: all eight audited agent benchmarks were exploitable, and the mechanism was that the submission shared the evaluation environment. A `conftest.py` hook inside the agent-controlled container forced every test to report as passing. The lesson generalises directly. Selyth running locally inherits the same exposure, and no amount of careful process management removes it.

So V0.1 provides **structural integrity, not isolation.** The distinction is precise and the report schema enforces it: a `local-subprocess` producer may not set `outside_agent_boundary` to true, and a report in the `local-structural` trust model may not claim an outside boundary. The system is built so that it cannot lie about what it is.

## Threat model

The adversary is an AI agent that may be confused, incentivised, or actively adversarial, operating with the ambient authority of a local development process.

| # | Threat | V0.1 defence | Residual risk |
|---|---|---|---|
| T1 | Modify the acceptance policy | Policy is content-addressed and digest-pinned in every report; a change alters the hash and makes prior reports visibly stale | Agent edits the policy *and* the expected digest in the same commit; detected only by a human reading the diff |
| T2 | Delete or weaken tests | Additions-only invariant on proof artifacts, enforced mechanically by diff, not by convention | Tests outside the proof workspace are not covered by the invariant |
| T3 | Change the specification | Obligations are frozen by digest; a report names the digest it was produced against, so an edited spec invalidates the report | Agent edits spec and report together in one commit |
| T4 | Forge a verification report | Report schema rejects structurally impossible claims; report must be produced by a named producer outside the agent's declared boundary | **Not defended in V0.1.** See below |
| T5 | Reuse a stale result | Reports bind to content digests, not branches; cache keys include backend identity and version | Agent replays a valid report for a semantically different change that happens to share digests |
| T6 | Manipulate tool output | Backend invoked by subprocess with captured stdout; results normalised into a typed structure | Agent writes directly into the captured output path if it can predict it |
| T7 | Introduce hidden side effects | Not in V0.1 scope; belongs to the language's effect system and to a future sandboxed execution tier | Entirely undefended |
| T8 | Exploit the verification environment | Path-scoped rules exist but are advisory; see below | Entirely undefended |

Two of these deserve to be called out rather than tabulated.

**T4 is not defended in V0.1, and pretending otherwise would be the worst error in this document.** A report produced by a subprocess on the same machine, by a user the agent shares, is an assertion. The schema can require that the report *claims* a boundary it does not have — which is why the schema requires the opposite, forcing `outside_agent_boundary: false` — but a schema constrains the shape of a document, not the honesty of the process that wrote it. What the schema buys is that a reader is never misled about the strength of the evidence, which is necessary and not sufficient.

**T1 and T3 have a real gap.** Content addressing makes tampering visible in a diff, which assumes a human reads the diff. Phase C is unambiguous that this is a procedural control, not a cryptographic one. It is the correct control for a €0, one-person project, and it should be labelled as procedural everywhere it appears rather than described as tamper-proof.

## Trust boundaries

```
   ┌──────────────────────────────────────────────────────────────┐
   │  TRUSTED: human owner                                        │
   │    authors obligations · approves policies · waives          │
   └───────────────────────────────┬──────────────────────────────┘
                                   │ approval, never automated
   ┌───────────────────────────────▼──────────────────────────────┐
   │  SEMI-TRUSTED: Selyth Core                                   │
   │    policy engine (pure function) · report schema              │
   │    Can be relied on for determinism. Not for honesty about   │
   │    the outside world.                                         │
   └───────────────────────────────┬──────────────────────────────┘
                                   │ reads frozen artefacts
   ┌───────────────────────────────▼──────────────────────────────┐
   │  ASSUMED-HONEST-UNTIL-DEMONSTRATED: verification environment │
   │    V0.1: local subprocess, same user, same filesystem        │
   │    Not a security boundary. Reports it emits are assertions  │
   │    with a recorded, stated scope.                            │
   └───────────────────────────────┬──────────────────────────────┘
                                   │ receives changes
   ┌───────────────────────────────▼──────────────────────────────┐
   │  UNTRUSTED: AI agent                                         │
   │    proposes everything · may weaken, forge, replay, bypass   │
   │    No authority over obligations, policy, or acceptance       │
   └──────────────────────────────────────────────────────────────┘
```

The two boundaries that actually matter are the first and the last. The human's approval is the only root of authority, and the agent's proposals carry no authority at all. Everything in between is engineering.

## What V0.1 structurally enforces

These hold regardless of what the agent does, because they are properties of the data rather than of the process:

- **Content addressing.** Obligations, specifications, policies and subjects are bound by SHA-256 digests. A report states exactly which bytes it was produced from.
- **The additions-only invariant.** The diff between a generated proof artefact and its proof file must be additions only, checked mechanically in CI, as LemmaScript does. A modified proof either fails to check, becomes valid but irrelevant, or remains valid and safe — the three-outcome tamperproofness property from proof-carrying code. This gives tamper *detection* without a trusted third party, which is exactly the property a €0 project can have.
- **Schema-enforced claim ceilings.** The report schema makes it impossible to record a proof without a machine-checked evidence kind, a soundness claim without an evidence kind that supports it, or an accepted decision on an unapproved policy. Sixteen of the seventeen report test cases assert exactly this.
- **Total, replayable policy evaluation.** `(policy, report) → decision` is a pure function. Re-running it on the same inputs yields the same answer, which means a disputed decision can be replayed rather than argued about.
- **Hash-pinned policy.** The policy digest travels inside the report, so a decision cannot be re-read against a policy that changed after the fact.
- **No silent omission.** Every obligation in the frozen set appears in the report, including those not run. The schema enforces a non-empty obligations array, so an empty report cannot masquerade as a satisfied one.

## What V0.1 does not enforce

- **Against an agent with arbitrary local code execution.** The machine is assumed compromised for the purposes of forging reports. This is not a bug in the design; it is the honest consequence of the €0, single-machine constraint.
- **Path-scoped policy rules.** Rules can select obligations by path prefix, and this looks like containment. It is not: an agent that can move code can move it across a path boundary, and the scope check would follow the code and still pass. Path scoping is a routing convenience for which rules apply, and the policy schema says so in the field description rather than leaving a reader to assume otherwise.
- **Runtime behaviour.** V0.1 verifies declarations about code. It does not observe, sandbox or constrain what code does when it runs. Hidden side effects are entirely out of scope.
- **Report authenticity.** There are no signatures in V0.1. Every report is locally forgeable in principle; the report schema's job is to make the forgery harder to pass off, not impossible.

## The path to real isolation

The architecture is built so that the change required for genuine isolation is small and does not touch Core's semantics.

1. **Artifact handoff.** Today the verifier reads the workspace directly. The first step is an immutable snapshot: the workspace is frozen by digest, the verifier reads only the snapshot, and results leave through a write-once channel.
2. **Execution relocation.** The subprocess invocation becomes a container or remote invocation. Because the adapter contract is already a serialised interface with captured output, this is a transport substitution, not a redesign.
3. **Attested producer.** A remote verifier runs in a controlled environment with a declared identity, and the report's `producer.kind` becomes `remote-service` with `outside_agent_boundary: true`. The schema already models this state; nothing needs to change except the value.
4. **Policy approval.** Git-signed commits and a revocation file replace `manual-attestation`, and `trust.policy_approval_trusted` becomes meaningful rather than procedural.

Step 1 is worth doing in V0.1 because it is cheap and it improves forensics even without isolation. Steps 2 to 4 are a deployment decision that depends on whether the threat model ever includes a genuinely adversarial local agent — and for a €0 research project, it does not yet.

## Security properties summary

| Property | V0.1 | Isolated deployment |
|---|---|---|
| Reports cannot overclaim their evidence | Enforced by schema | Enforced by schema |
| Policy changes are visible in diffs | Enforced by content addressing | Enforced by content addressing |
| Proof weakening is detected | Enforced by additions-only invariant | Enforced by additions-only invariant |
| Acceptance decisions are replayable | Enforced | Enforced |
| Reports are unforgeable | **Not enforced** | Enforced by producer identity |
| Agent is isolated from the verifier | **Not enforced** | Enforced by relocation |
| Runtime behaviour is constrained | **Not enforced** | Out of scope in both |
