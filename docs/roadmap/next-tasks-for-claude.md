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

1. Un scénario réel d'une vingtaine d'obligations `kernel-proven` sur un petit projet (élément 6 de `core-v0.1.md`).
2. La traduction d'un sous-ensemble pur de TypeScript vers Dafny, qui remplacerait l'atelier de preuve écrit à la main.
3. L'évaluation M6 (A contre B), qui demande de choisir l'agent, les tâches et le budget.
4. Points de conception ouverts : `requires_intent_corroboration` ne peut pas être satisfait en même temps qu'une preuve (une seule preuve par obligation dans le rapport) ; `non_empty_assumptions` est lu littéralement ; `cost_budget` n'est pas appliqué ; le contenu des champs que le rapport recopie de l'Obligation Set n'est pas comparé.
