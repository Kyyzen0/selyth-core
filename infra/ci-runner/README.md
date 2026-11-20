# CI runner auto-hébergé (Kut CI) — un seul conteneur, toujours allumé

Runner GitHub Actions auto-hébergé pour ce dépôt **privé**, dans UN conteneur Docker
persistant géré par `docker compose` (`restart: unless-stopped`). Référence : `infra/ci-runner/`
de `Kyyzen0/Kut-Studio`, adaptée ici (pas de FFmpeg/PySide6 ; `make`/`python3` ajoutés ;
le runtime .NET 8 de l'image de base suffit pour Dafny 4.11.0).

Pourquoi : le dépôt est privé, donc les runners hébergés consomment le quota inclus
(2 000 min/mois, plan gratuit). Quota épuisé = chaque job hébergé est refusé avant de
démarrer (« spending limit needs to be increased »). Un runner auto-hébergé ne consomme
**aucune** minute hébergée : la CI reste gratuite.

## Principe

- Un conteneur Linux contient le binaire `actions/runner` (version figée **et** SHA-256
  vérifié au build, idem pour `gh`), les outils de la CI (python3, make, git, gh, curl,
  unzip, jq) et un utilisateur non-root `runner`.
- Il s'enregistre **une seule fois** auprès de GitHub avec un jeton d'enregistrement,
  puis tourne en continu et attend les jobs (« Listening for Jobs »). Ce n'est PAS un
  `docker run` lancé par job.
- Géré par `docker compose` (`restart: unless-stopped`) ; sa configuration
  (`.runner`, `.credentials`, `_work`) est dans le **volume nommé** `runner-home`
  (jamais un dossier de la machine hôte) : un redémarrage ne demande aucun nouveau jeton.
- Le workflow vise le runner par étiquettes : `runs-on: [self-hosted, linux, kut-ci]`.

## Démarrage (le jeton ne s'écrit jamais dans un fichier ni dans le chat)

```sh
cd infra/ci-runner
RUNNER_TOKEN=$(gh api -X POST repos/Kyyzen0/selyth-core/actions/runners/register-token --jq .token) \
  docker compose up -d --build
docker compose logs -f runner    # attendre « Listening for Jobs »
```

Le jeton vaut 1 h et sert une seule fois ; il n'est nécessaire qu'au premier démarrage.
Ensuite : `docker compose up -d` (sans jeton) suffit, y compris après un redémarrage de
la machine — la compose n'exige `RUNNER_TOKEN` que s'il est vide ET que le runner
n'est pas encore enregistré.

Vérifications : `docker compose ps` (conteneur `running`) ; GitHub → Settings → Actions →
Runners → le runner est **Idle** ; les jobs en attente (`queued`) partent seuls ;
Settings → Billing → Actions → 0 minute hébergée consommée.

## Sécurité (dépôt privé uniquement, jamais pour des PR de forks)

- Conteneur non privilégié : `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`,
  aucun montage de dossier personnel, du trousseau, ni du socket Docker, aucun port publié.
- Le workflow a un garde : `if: github.event.repository.private && (github.event_name !=
  'pull_request' || !github.event.pull_request.head.repo.fork)` — dépôt privé ET PR non
  issue d'un fork.
- Le jeton d'enregistrement n'est jamais commité ni collé : il ne transite que par la
  variable d'environnement de la commande de démarrage. Dans le conteneur, `entrypoint.sh`
  le retire de l'environnement du process avec `exec env -u RUNNER_TOKEN ./run.sh`
  (un simple `unset` ne suffirait pas : le secret resterait lisible dans `/proc/1/environ`).

## Pièges déjà rencontrés (ne pas les recréer)

- **Valider dans les mêmes conditions que la CI** : tester avec exactement les options du
  compose (`docker compose up -d --build`), sinon la validation est un faux vert.
- **Pas de tmpfs sur /tmp** : monté `noexec`, les tests qui lancent des scripts temporaires
  échouent avec « Permission denied ». Le compose ne monte donc aucun tmpfs.
- **Crochet de nettoyage** (`job-reset.sh`, hook `ACTIONS_RUNNER_HOOK_JOB_COMPLETED`) :
  il ne doit jamais supprimer le dossier de travail du job (`GITHUB_WORKSPACE`), sinon chaque
  job échoue en ~8 s avec « No such file or directory » — il est recréé après le nettoyage.
- **Ne jamais recréer le conteneur pendant qu'un job tourne** : GitHub garde l'ancienne
  session et le job est perdu au bout de ~10 min (« lost communication »). Vérifier d'abord
  que le runner n'est pas occupé :
  `gh api repos/Kyyzen0/selyth-core/actions/runners -q '.runners[] | {name, status, busy}'`
  Après une recréation, le runner reste hors ligne 4 à 5 minutes avant de se reconnecter
  tout seul.
- **Cache de dépendances** : volume nommé `pip-cache` pour pip, jamais un montage de l'hôte.
- **Apple Silicon** : l'image est épinglée `platform: linux/amd64` parce que Dafny 4.11.0
  n'existe qu'en x64 pour Linux ; sur Mac arm64 le conteneur tourne via l'émulation qemu de
  Docker Desktop (un peu plus lent, mais le binaire Dafny x64 fonctionne).

## Ré-enregistrement from scratch

```sh
cd infra/ci-runner
docker compose down -v        # supprime aussi les volumes (registration comprise)
RUNNER_TOKEN=$(gh api -X POST repos/Kyyzen0/selyth-core/actions/runners/register-token --jq .token) \
  docker compose up -d --build
```

## Preuve que ça marche

Lancer un vrai job et le regarder passer vert sur le runner :
`gh pr checks` sur la PR concernée + `docker compose logs -f runner` (le log du job
apparaît dans la console du conteneur).
