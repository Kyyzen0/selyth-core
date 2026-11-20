# Prochaines tâches à confier à Claude Code

Ces tâches sont ordonnées. Chacune doit être livrée avec des tests automatisés, sans démarrer l'adaptateur TypeScript avant leur achèvement.

## 1. Mettre en place une validation indépendante et des tests adversariaux

**But :** ne plus dépendre uniquement de `tools/minischema.py`, écrit en même temps que les schémas qu'il valide.

**À faire :**

1. Ajouter un environnement Python reproductible et un validateur JSON Schema complet, compatible Draft 2020-12.
2. Rejouer les 23 fixtures existantes contre ce validateur ; conserver 2 succès et 21 rejets attendus.
3. Ajouter des cas négatifs pour :
   - un digest différent entre `policy.policy_digest` et `decision.policy_digest` ;
   - un rapport `accepted` avec zéro règle évaluée ;
   - un rapport déclarant une confiance distante avec un producteur local ;
   - une politique dont aucune règle ne s'applique à une obligation.
4. Distinguer les contraintes que JSON Schema peut imposer de celles qui nécessitent une validation sémantique dans le moteur de politique.

**Fini quand :** la commande de test documentée utilise le validateur indépendant, échoue pour chaque scénario adverse, et ne remplace pas la suite existante par des tests plus faibles.

## 2. Définir l'Obligation Set, sa canonisation et son digest

**But :** disposer de l'entrée figée dont dépendent le rapport, la politique et la future vérification.

**À faire :**

1. Créer `docs/specifications/obligation-set-schema.json` avec identifiants stables, versions, déclarations formelles, intentions facultatives, hypothèses et composants affectés.
2. Définir une canonisation sans ambiguïté : ordre des clés d'objet indifférent, formatage indifférent, ordre des tableaux explicitement sémantique ou explicitement normalisé.
3. Implémenter un outil qui valide, canonise et produit un digest `sha256:`.
4. Tester l'invariance au formatage et à l'ordre des clés ; tester qu'une modification de sens modifie le digest.
5. Définir la vérification de couverture : chaque obligation de l'Obligation Set doit apparaître une fois dans le rapport, avec le même identifiant et digest.

**Fini quand :** un Obligation Set de démonstration peut être validé et haché de façon reproductible, et qu'un rapport omettant une obligation est refusé par la validation sémantique.

## 3. Implémenter le moteur de politique déterministe

**But :** rendre réelle la promesse `(policy, report, obligation_set) -> decision`.

**À faire :**

1. Définir une décision par défaut sûre pour chaque cas non couvert, timeout et preuve inconnue.
2. Implémenter le classement des forces de preuve sans dépendre de l'ordre textuel des énumérations JSON.
3. Vérifier l'égalité des digests de politique, la couverture complète des obligations, les exigences de preuve, les escalades et la cohérence du niveau de confiance.
4. Produire une décision explicable : chaque rejet ou escalade nomme une règle et, lorsque pertinent, une obligation.
5. Ajouter des tests de déterminisme et de rejouabilité ; aucun accès au réseau, au système de fichiers ou à une IA dans la fonction de décision.

**Fini quand :** un rapport est accepté, rejeté ou escaladé uniquement par le moteur ; le moteur rejette les incohérences laissées ouvertes par le JSON Schema ; deux exécutions avec les mêmes entrées produisent exactement la même sortie.

## Suite prévue

Une fois ces trois tâches terminées, commencer l'adaptateur TypeScript avec une clé de reproductibilité incluant le digest de l'Obligation Set, l'identité et la version de l'adaptateur, puis l'identité de l'outil de vérification.

## État au 13 octobre 2026

Les trois tâches ci-dessus et la « Suite prévue » sont faites et mergées :

| Travail | Pull request |
|---|---|
| Tâche 1 — validation indépendante et cas adverses | #1 |
| Tâche 2 — Obligation Set, canonisation, digest, couverture | #2 |
| Tâche 3 — moteur de politique déterministe | #3 |
| Adaptateur TypeScript et IR de vérification (Python, puis portage TypeScript) | #4, #5, #6 |
| Backend Dafny et rapport de preuve | #7 |
| Scénarios négatifs (M5) et verrou (lock) | #8 |
| Preuve empirique réussie (`passed`) dans le schéma et le moteur | #9 |

Reste à faire, par ordre de décision à prendre :

1. Fait sur un module écrit pour l'occasion : vingt obligations `kernel-proven`, étude de mutation, précondition vacuité (`docs/specifications/examples/ledger/`, PR #11). Fait aussi sur du code d'un tiers : `docs/specifications/examples/gcd/` (fonction `gcd` de `pallosp/congruence_solver`, MIT, commit 150bfb57, portée au sous-ensemble pur en deux lignes documentées), 4 obligations `kernel-proven` sous lock, étude de mutation, `tools/test-gcd.py` ; le coût est mesuré par `make bench EXAMPLE=gcd` et comparé dans `docs/research/performance.md`.
2. Fait pour un sous-ensemble pur (`tools/ts_to_dafny.py`, `docs/specifications/ts-to-dafny.md`) : le modèle Dafny du ledger est généré depuis `ledger.ts`. Restent à la main les invariants, lemmes et appelants ; les entiers sont non bornés ; rien hors du sous-ensemble n'est traduit.
3. L'évaluation M6-Lite (A contre B), étude pilote à coût marginal nul : le protocole, le harnais et l'oracle sont prêts et testés sur un corpus scripté (`docs/evaluation/protocol.md`). Les paramètres sont décidés (`docs/decisions/ADR-004-m6-launch-decisions.md`) : 6 tâches réelles, 2 bras, 1 exécution par bras et par tâche (12 runs avec agent), 20 minutes et 15 appels au modèle par run, isolation procédurale, résultats rapportés tâche par tâche sans conclusion générale. L'évaluation complète (10 tâches, 3 répétitions, 60 runs) est une étude future distincte, non commencée. Reste à constituer le jeu de tâches (curateur indépendant) et un runner qui compte chaque appel au modèle ; le dépôt privé d'évaluation existe (structure seulement).
4. Points de conception documentés comme des choix délibérés dans `docs/specifications/policy-engine.md` : `requires_intent_corroboration` ne peut pas être satisfait en même temps qu'une preuve (une seule entrée de preuve par obligation dans le rapport) — c'est une garde, jamais une condition d'acceptation ; `non_empty_assumptions` est lu littéralement, fail-closed (les policies V0.1 gardent le flag à faux). Faits : le contenu des champs que le rapport recopie de l'Obligation Set (`kind`, `statement`, `intent_statement`, `required_evidence_kind`, `affected_components`) est comparé champ par champ par `check_coverage` (règle 5 de `docs/specifications/obligation-set-canonicalization.md`), et `cost_budget` est appliqué par le moteur (règle 6 de `docs/specifications/policy-engine.md`, `BLOCK-COST-BUDGET`).

## État au 20 novembre 2026 (PR #20 à #27 et la suite)

- Boucle `cost_budget` bouclée : le backend Dafny enregistre `cpu_ms` et `peak_rss_bytes` du sous-processus vérifieur (PR #27), `make bench` agrège le pic en `max`, et la recommandation de `docs/research/performance.md` inclut `max_peak_rss_bytes: 1073741824`.
- Une clause de boucle (marqueur entre l'en-tête et le corps) est désormais vérifiée sous le **contrat entier de la déclaration englobante** (`requires` + `decreases`), écrit au marqueur de contrat : l'invariant de boucle du gcd tiers est une obligation autonome `GCD-04`, prouvée et testée par mutation. Les marqueurs orphelins (code qui marque une obligation que l'ensemble ne déclare plus) sont refusés (exit 2, aucun rapport), et `obligation_set.py check-components` échoue sur tout `affected_components` absent du disque (F2 rendu visible).
- Lint de vacuité fail-closed au chargement de l'Obligation Set : `true`, `x == x`, `x <= x`, `x >= x` (parenthèses admises) sont refusés avec l'id de l'obligation (R10) ; le lock attrape toujours les affaiblissements non vides.
- **Deuxième backend** (`tools/property_backend.py`, spec `docs/specifications/property-backend.md`) : les `acceptance-criterion` sont exécutés comme property tests déterministes (Node, sample semé) et produisent de l'évidence `property-tested` / `empirical` — le contrat (schéma de rapport, moteur, lock) a tenu sans modification, ce qui valide la frontière. Le gcd a 2 critères (`GCD-P1`, `GCD-P2`) acceptés sous lock ; `make test-properties` les teste de bout en bout, mutant compris.

- **Rapports fusionnés** : `tools/gate.py` (spec `docs/specifications/gate.md`) produit UN rapport pour UN set / une policy / un lock : le backend Dafny couvre les clauses de contrat, le backend property couvre les `acceptance-criterion`, le moteur décide une seule fois. L'exemple gcd est migré en set fusionné de 6 obligations (GCD-01..04 + GCD-P1/P2) ; `make check EXAMPLE=gcd` et `make bench EXAMPLE=gcd` passent par la gate.

- **Sous-ensemble élargi** (tools/ts_to_dafny.py, spec docs/specifications/ts-to-dafny.md) : boucle `for (let i = K; i < e; i++)` (désucrée en `while` + compteur explicite), `c ? a : b` (if-expression Dafny), `Math.abs(e)` (helper `Abs` sur `int`, prépendu une fois). Tests de forme, refusals et différentiel TS/Dafny (2160 évaluations) dans test-ts-to-dafny.py ; vérification réelle sous Dafny 4.11.0 dans test-dafny-backend.py (4 obligations prouvées). Le gcd tiers revient à son `return Math.abs(a);` original, traduit tel quel. Côté backend, une clause `decreases` de boucle reste au marqueur de boucle (les locales y sont visibles) et sert d'hypothèse aux invariants de la même boucle.
