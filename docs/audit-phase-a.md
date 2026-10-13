# Audit pédagogique — livrables de phase A

**Date :** 13 octobre 2026  
**Périmètre :** audit documentaire et exécution de la suite de schémas, sans modification des livrables d'origine.

## Conclusion

Selyth est aujourd'hui un **pré-prototype d'architecture**. Le dépôt ne contient pas encore Selyth Core comme application exécutable : il contient une vision, des ADR, des schémas de rapport et de politique, ainsi qu'un validateur minimal et ses tests.

La décision de commencer par le moteur de vérification est cohérente avec l'ambition de langage IA/humain. Core ne possède volontairement ni IR d'exécution ni système de types : le futur langage deviendra un producteur d'obligations et d'IR de vérification, sans rendre Core dépendant de sa syntaxe.

## Inventaire vérifié

- Documentation d'architecture, de sécurité, de recherche et de feuille de route dans `docs/`.
- Trois ADR : démarrer par Core, rendre la frontière de confiance explicite, et séparer intention informelle et obligation formelle.
- Deux schémas JSON : `report-schema.json` et `policy-schema.json`.
- Deux outils Python : un validateur JSON Schema volontairement limité et une suite de fixtures.
- La suite fournie compte **23 cas** : 2 documents valides et 21 cas qui doivent être rejetés. Elle passe avec le validateur minimal inclus.

## Ce qui est solide

1. La séparation des rôles est nette : l'humain définit les obligations, l'agent propose, le moteur de vérification produit des éléments de preuve et le moteur de politique décide.
2. Les forces de preuve, résultats de vérification et provenances sont séparés. Une corroboration par modèle de langage ne peut pas devenir une preuve formelle.
3. Le modèle local est honnête : un sous-processus sur la même machine améliore la fiabilité et la traçabilité, mais n'isole pas un agent ayant les mêmes permissions.
4. Les conditions d'arrêt explicites empêchent que la future ambition de langage remplace une évaluation mesurable de Core.

## Incohérences et lacunes à traiter avant l'adaptateur TypeScript

### P0 — Contrat de politique incomplet

Les documents décrivent la politique comme une fonction totale `(policy, report) -> decision`. Or le schéma autorise une politique sans règle applicable à une obligation donnée et sans décision par défaut. Cette fonction n'est donc pas encore définie pour tous les couples valides.

Le futur moteur doit définir un repli sûr, préférablement `needs-human` ou `rejected`, et tester explicitement les obligations sans règle applicable.

### P0 — Liaison insuffisante entre décision et politique

Le rapport contient un digest de politique dans `policy` et un autre dans `decision`, mais le schéma ne vérifie pas leur égalité. De même, un rapport peut déclarer une acceptation avec `rules_evaluated: 0`.

Cette cohérence inter-objets relève du moteur de politique, pas du seul JSON Schema. Elle doit être validée avant d'émettre un rapport accepté.

### P0 — Obligation Set non spécifié

La feuille de route demande un Obligation Set validé, canonisé et haché, mais aucun schéma correspondant n'est présent. Sans cette définition, ni le digest ni la couverture complète des obligations ne peuvent être implémentés proprement.

### P1 — Couverture intégrale non démontrable par le rapport seul

Le schéma de rapport exige une liste non vide d'obligations, mais ne peut pas vérifier que cette liste correspond à toutes celles de l'Obligation Set figé. Le moteur devra comparer les identifiants et digests des deux artefacts.

### P1 — Promesse de confiance à reformuler

Le manifeste présente le vérificateur comme hors de portée de l'agent, tandis que l'ADR-002 et le modèle de sécurité disent correctement que ce n'est pas possible en V0.1 locale. Le README et les rapports devraient qualifier le verdict : `accepted` signifie une acceptation par politique, avec un niveau de confiance explicitement local-structurel ; ce n'est pas une attestation indépendante.

### P1 — Terminologie de preuve

`kernel-proven` est un nom trop fort pour un résultat issu de Dafny/Boogie/Z3 : cette chaîne ne correspond pas à un petit noyau de preuve vérifié au sens strict. Choisir `solver-verified` ou définir précisément l'usage de `kernel-proven` éviterait de surpromettre.

### P2 — Documentation à harmoniser

- Les comptes de tests négatifs divergent selon les documents (16, 17, 18 ou 21) ; le chiffre constaté est 21.
- Le périmètre de l'évaluation varie entre un projet d'environ 20 obligations et trois projets pour 20+ obligations.
- Le test de déterminisme de l'IR doit dépendre du digest des obligations **et** de la version de l'adaptateur, pas seulement du digest des obligations.

## Décision recommandée

Ne pas débuter l'adaptateur TypeScript avant les trois tâches de `docs/roadmap/next-tasks-for-claude.md`. Elles rendent les données d'entrée, la décision et la confiance auditables ; l'adaptateur pourra ensuite produire des preuves qui ont un contrat précis.
