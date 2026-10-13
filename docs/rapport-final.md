# Selyth Phase C — Rapport final

## Les concurrents de référence

Le projet Selyth, tel qu'il est formulé, se heurte à un fait qu'il faut regarder en face : **au moins dix-huit projets occupent déjà la position qu'il vise**, et aucun n'a été adopté.

Le concurrent le plus sérieux n'est pas un langage. C'est **Djinnlang**, un article de l'ETH Zürich (Simon Henniger, Stephen Chong, Nada Amin), soumis le 21 septembre 2026 [1]. Il ne s'agit pas d'un voisin thématique : c'est la tranche verticale que Selyth propose de construire, déjà réalisée et publiée. Le programmeur y écrit uniquement des spécifications ; un traducteur symbolique produit des stubs Dafny et des obligations de preuve ; un pilote orchestre un LLM qui remplit à la fois les implémentations et les preuves ; le vérificateur Dafny contrôle le résultat ; et l'article démontre l'auto-hébergement. Sa contribution supplémentaire est une contrainte d'ambiguïté qui oblige le modèle à prouver que la spécification détermine une relation de sortie unique. Sa formulation répond point par point à celle de Selyth : *« un modèle non fiable écrit le code, mais son travail est étroitement contrôlé par un vérificateur »* — c'est du code porteur de preuve appliqué aux agents.

Vient ensuite **LemmaScript**, 114 étoiles, qui traduit du TypeScript annoté vers Dafny ou Lean 4 [2][3][4]. Son document de conception énonce la conclusion que Selyth est en train de redécouvrir : *« Il n'y a pas de nouveau langage. Il y a TypeScript, et il y a un prouveur (Lean ou Dafny). L'outillage fait le pont. »* Son équipe a evalué le même point de départ et a pris la même décision. Elle a livré en plus des cas réels : 753 conditions de vérification sans erreur, et quatre CVE couvertes en code existant [4]. Un témoignage public fait état d'un service de production à environ 150 millions d'utilisateurs quotidiens avec un cœur de domaine vérifié par Dafny [2].

Le contraste est instructif. Les dix-huit langages « AI-native » affichent entre 0 et 98 étoiles, la plupart autour de quelques étoiles, pour un auteur unique ; l'un est archivé, un autre lance un jeton communautaire sur pump.fun. L'outillage de vérification et de politique, lui, affiche de 32 à 454 étoiles, tourne en production et est intégré par des équipes qui n'ont construit aucun langage pour y accéder — Sponsio, avec 35 contrôles déterministes, zéro appel LLM et moins de 0,01 ms par contrôle [22]. Personne n'a gagné le langage. Les gens ont gagné la barrière.

## La direction architecturale retenue

Le changement de direction ne consiste pas à abandonner la thèse mais à changer d'artefact. Le socle de vérification n'est pas un manque : **Dafny atteint 82,2 % de succès LLM sur le plus grand banc de vericoding** — 12 504 spécifications formelles, dont 6 174 inédites, et 55 397 expériences enregistrées — et son taux de vérification pure est passé de 68 % à 96 % en un an environ [9][10]. Un langage neuf part de zéro : l'écosystème Dafny renvoie environ 779 résultats sur GitHub, contre plus de cinq millions pour Java ou Python [27]. Choisir Dafny comme socle n'est pas un compromis, c'est retenir le meilleur actif du domaine.

Le second chiffre est plus dérangeant pour Selyth : sur ce même banc, **ajouter des descriptions en langage naturel n'améliore pas significativement la performance** [9][10]. C'est la preuve de premier ordre contre la valeur des blocs d'intention, et c'est le nombre le plus lourd de tout ce dossier.

Reste un écart réel et non résolu : aucun outil disponible ne vérifie qu'une spécification formelle correspond à ce qu'un humain voulait réellement dire. Tout vérificateur en production prend une spécification déjà écrite et contrôle le code par rapport à elle. C'est de la vérification, pas de la validation — et l'écart entre les deux est ce que la littérature appelle l'« intent gap » [36][37]. C'est là, et seulement là, que Selyth peut se différencier. Un problème d'élicitation de spécification, pas un problème de langage.

La direction recommandée est donc un cadre de spécification et de preuve indépendant du langage : obligations structurées, enregistrement de preuves, moteur de politique, avec abaissement vers Dafny pour le contrôle. Les annotations sont des commentaires, donc sans coût à l'exécution ; l'adoption est incrémentale, fonction par fonction ; l'automatisation du prouveur est gratuite ; et l'agent écrit les preuves, ce qui tient l'humain à distance des notations formelles [4][6].

## Les trois risques majeurs

**Le premier est la non-déterminisme de la validation de l'intention.** Claimcheck, le meilleur outil disponible, travaille par informalisation croisée à l'aveugle et revendique 96,3 % de précision — c'est-à-dire une probabilité, pas une garantie [8]. Il repose sur un modèle de langage. Il viole donc simultanément le principe 3 (exécution déterministe) et le principe 7 (indépendance au modèle). Aucun mécanisme déterministe n'existe pour le remplacer. C'est un problème de recherche, pas d'ingénierie.

**Le deuxième est le coût de vérification, mesuré et rédhibitoire.** Sur DafnyBench, 782 programmes et 230 seulement se vérifient ; 66 échouent sur un délai de 20 secondes [25]. Les cas d'usage en production de LemmaScript tournent avec des limites de 180 à 600 secondes [4]. Roc refuse explicitement les types de raffinement en invoquant une « augmentation exponentielle des temps de compilation » [28]. Sur un Mac mini, une barrière qui prend des minutes sera contournée par l'agent, et la contournement sera invisible. Aggravant : la vérification n'est pas déterministe d'une exécution à l'autre — les unités de ressources sont déterministes, la durée ne l'est pas, et un projet a mesuré un taux d'échec de 30 % sur 100 exécutions [24].

**Le troisième est la frontière de confiance.** Un rapport de vérification ne vaut rien si c'est l'agent qui exécute le vérificateur. SLSA est explicite : il faut faire confiance au producteur, et cette confiance représente la fermeture transitive des entités jugées fiables [31]. Berkeley RDI a démontré que **les huit bancs d'agents audités sont exploitables**, précisément parce que la soumission partage l'environnement d'évaluation — un crochet `conftest.py` suffit à faire passer tous les tests [17]. L'architecture impose donc une contrainte dure, non négociable : le vérificateur s'exécute hors du système de fichiers de l'agent, et ses sorties sont extraites par un canal contrôlé.

## Le prototype de validation

Le prototype minimal n'est pas un langage. C'est quatre composants et un test négatif, dimensionnés pour des semaines et non des mois.

Le premier est une **couche de spécification** d'une cinquantaine de lignes de grammaire, portant contrats et invariant de terminaison, plus une chaîne d'intention en langage naturel par obligation — chaîne qui doit être séparable, afin de pouvoir mesurer la ligne de base sans elle. Le deuxième est l'**abaissement** vers Dafny par intégration superficielle, chaque construction correspondant à une construction, de sorte que la sortie soit un programme Dafny qu'une personne aurait pu écrire [6][23][24]. Le troisième est l'**espace de preuve**, qui maintient l'invariant « ajouts seulement » entre le fichier généré et le fichier de preuve, vérifié mécaniquement et en intégration continue [4][7] : c'est l'élément structurel le plus important du prototype, car il empêche l'affaiblissement de la politique sans exiger de tiers de confiance. Le quatrième est l'**émetteur de preuves**, qui produit un rapport de forme in-toto : hash du sujet, liste des obligations, type de preuve, résultat, identité du vérificateur, coût en unités de ressources, décision [31][32].

Le test négatif est obligatoire : introduire une modification qui viole un invariant énoncé tout en laissant tous les contrôles existants techniquement satisfaits. Une barrière qui ne détecte que du code manifestement cassé n'a rien démontré.

L'échelle visée est d'une vingtaine de fonctions tirées d'un projet réel. Les 43 instances multi-modules du banc Vero sont hors de portée : 10 restent non résolues par toutes les configurations testées, et 74 % des lignes de preuve des réussite sont des lemmes partagés que les agents construisent rarement [12][13].

Le protocole d'évaluation doit contrôler la confusion centrale : si le bras Selyth se contente de lancer plus d'outils, la comparaison ne prouve rien. La ligne de base est donc le même agent, le même modèle, le même banc, avec le harnais Dafny identique disponible des deux côtés. Quatre protections contre la contamination sont obligatoires, toutes empruntées à des travaux qui ont déjà identifié la panne : spécifications tenues et gelées hors du périmètre évalué [12][13] ; vérificateur hors de l'environnement de l'agent [17] ; agent nul dont le score définit le plancher [17][18] ; et évaluation de l'évaluateur lui-même par un agent de sabotage et par mutation testing [17][21]. Ce protocole n'est pas une précaution de style. OpenAI a cessé de publier SWE-bench Verified après avoir constaté qu'au moins 59,4 % d'un sous-ensemble audité contenaient des tests fautifs rejetant des solutions correctes, et que les modèles de frontière reproduisaient les correctifs de référence mot pour mot [15].

## Les constructions à différer

Il ne faut pas construire, à ce stade, le compilateur, le système de types, le prouveur, le langage d'effets, le runtime, l'orchestration multi-agents, ni l'auto-hébergement de la chaîne d'outils. Chacun de ces éléments existe déjà sous licence MIT et est plus fort que ce qu'un effort solo produirait. Les retarder n'est pas un renoncement : c'est la condition pour que l'effort se concentre sur l'écart qui, lui, n'est pas résolu.

Le principe 11, « sustainability économique de la vérification », est le plus important du manifeste révisé et il figurait en dernier dans la version initiale. Il devrait être le premier, parce que c'est le seul principe qui élimine d'emblée la plupart des directions possibles.

## Verdict

**PIVOT.**

L'idée vaut d'être conservée ; le langage est le mauvais conteneur. Construire le compilateur serait consentir plusieurs années de travail à reproducing un résultat que l'ETH et une équipe de production ont déjà obtenu, en moins bien, et en partant de zéro sur le plan de l'outillage.

Ce qui doit être construit à la place est plus modeste, plus honnête et beaucoup moins cher : une couche de spécification et de preuve, adossée à Dafny, qui produit des rapports lisibles par machine et applique une politique d'acceptation outside the reach de l'agent.

Reste une question ouverte, et c'est celle qui mérite l'effort : la validation déterministe de l'intention. Tout l'outillage disponible est formel — et vérifie donc une spécification que l'humain a déjà écrite — ou fondé sur un modèle de langage, et donc probabiliste. Ce goulot d'étranglement est une élicitation de spécification. C'est un problème de recherche, il n'est pas résolu, et c'est le seul endroit où Selyth peut honnêtement se distinguer.

Ce que le présent rapport ne démontre pas : qu'un agent pourra faire passer une modification non triviale sous la barrière sans intervention humaine. C'est le critère d'acceptation central, et il reste à tester.

---


