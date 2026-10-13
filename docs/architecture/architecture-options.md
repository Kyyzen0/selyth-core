# Architectural options

## New language construction

A compiler, type system, and verifier built from scratch. This is the status quo plan.

The cost is not primarily the code. It is the absence of training data, ecosystem, or credibility. A new language has no verified corpus, no industrial users, and no verifiers built for mainstream targets, while Dafny already has 82.2% LLM success and 96% pure-verification improvement in a year [9][10][26]. The risk is that the language works and nobody uses it — the exact outcome observed across eighteen prior projects [38][39][41][42][43][44][45][46].

The one genuine advantage is total control over determinism, portability, and the provenance model. That control is real, and it is the reason the option cannot be dismissed on principle. It is simply not worth the price when the same control is available at the specification layer.

## Specification extensions to an existing language

Annotate an existing language and translate to an existing prover. This is LemmaScript's architecture, and it is the best-evidenced option in the field: 114 stars, brownfield adoption, four CVEs covered, and a production deployment [2][3][4].

Its strengths are decisive for this project. Contracts carry zero runtime cost because annotations are comments. Adoption is incremental and per-function. The prover's automation is free. The agent writes proofs rather than implementations, which keeps the human out of formal notation entirely — LemmaScript's stated design rule is that "the human should not need to touch anything formal, ever" [6].

Its limitation is that it offers no distinctive artifact. If Selyth becomes a TypeScript annotation layer, it competes with LemmaScript on LemmaScript's terms, with fewer resources.

## Standalone specification and evidence framework

A language-agnostic layer: structured intent, typed specification, evidence records, and a policy engine, lowering to Dafny for checking. This is the recommended option.

The evidence converges on this shape from four independent directions. Djinnlang is specification-only with an LLM in the compiler [1]. IntentLang treats orphan code — implementation without specification justification — as a first-class error [42]. SPOQ applies dual validation gates with a quantitative threshold plus a human-as-agent lane [50]. Conductor's premise is that orchestration should be deterministic and inspectable, not model-routed [49]. These are not the same project, and they are not the same field, but they are the same conclusion.

## Option comparison

The three options are scored below against the constraints that actually bind this project: a €0 budget, one M4 Mac mini, and a solo maintainer assisted by coding agents.

| Criterion | New language | Specification extension | Standalone framework |
|---|---|---|---|
| Cost to first vertical slice | Highest — months | Low — weeks | Low — weeks |
| LLM competence on day one | ~0% [9] | High — TypeScript is the best-documented language in existence | High — inherits Dafny's 82% [9] |
| Verification soundness | Fully under control | Borrowed from Dafny | Borrowed from Dafny |
| Differentiated artifact | The language itself | Annotations | Intent and evidence records |
| Adoption ceiling observed in field | 0–98 stars across 18 projects [38]–[46] | 114 stars with production use [2] | Not yet bounded |
| Risk that nothing gets built | High | Medium | Low |
| Alignment with Principles 2 and 7 | Poor — contradicts model independence | Poor — no model-specific design | Good |

The scoring favours the standalone framework on every axis that reflects a real constraint, and it is the only option that leaves the intent gap — the genuinely unsolved part — as the surface of differentiation.

---

## References


