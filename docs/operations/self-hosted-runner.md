# Self-hosted CI runner (zero GitHub-hosted minutes)

The repository is private, so GitHub-hosted runners draw on the account's included
Actions minutes (2,000/month on the Free plan). When that quota is exhausted, every
hosted job is refused before it starts ("spending limit needs to be increased") and no
job log exists. A **self-hosted runner executes jobs on your own machine and consumes
no GitHub-hosted minutes**, so CI stays free.

`.github/workflows/ci.yml` targets `runs-on: [self-hosted]` — one label, matched by any
self-hosted runner (native macOS, native Linux, or a Docker container on Linux). To go
back to hosted runners once minutes are available again, replace the `runs-on` lines with
`ubuntu-24.04` (not `latest`: 26.04 no longer ships Python 3.9, the oldest interpreter
the project supports).

## Quickstart: native runner on an always-on Mac (simplest — no Docker)

Download the **macOS** tarball (NOT the linux one — a Linux binary cannot execute on
macOS), extract, register, run:

```sh
mkdir ~/actions-runner && cd ~/actions-runner
os=$( [ "$(uname -m)" = arm64 ] && echo osx-arm64 || echo osx-x64 )
curl -fsSL -o runner.tgz \
  "https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-${os}-2.338.2.tar.gz"
tar xzf runner.tgz
# GitHub page of the repo: Settings -> Actions -> Runners -> New self-hosted runner -> MACOS tab.
# Paste the registration token it shows when config.sh asks (valid 1 hour, used once,
# never commit it):
./config.sh --url https://github.com/Kyyzen0/selyth-core
./run.sh
```

`./run.sh` stays in the foreground and prints `Listening for Jobs` when ready.

**Automatic start (no manual step ever again):** install the runner as a service once —
on macOS `svc.sh` registers a LaunchAgent that starts the runner at every boot/login:

```sh
sudo ./svc.sh install "$USER"
sudo ./svc.sh start
# check: sudo ./svc.sh status ; logs: ~/actions-runner/daemon/*.log or `./run.sh` output
```

From then on the runner is up whenever the Mac is awake, with zero interaction. While
the Mac sleeps, jobs simply stay **queued** (not failed) and run automatically at the
next wake-up — nothing is lost, and `concurrency: cancel-in-progress` keeps only the
latest commit's run. The runner shows **Idle** in Settings -> Actions -> Runners; queued
PR runs are picked up immediately, and Settings -> Billing -> Actions shows 0 hosted
minutes spent (self-hosted minutes are not metered).

If you would rather have CI with **no machine of yours at all**, switch the workflow back
to hosted runners (`runs-on: ubuntu-24.04` in `.github/workflows/ci.yml`): jobs are
refused while the monthly quota is exhausted, then run automatically at the next monthly
reset — zero setup, zero cost, fully automatic.

Requirements on the Mac: `git`, `curl`, `unzip`, `make` (all present via Xcode Command
Line Tools), plus network access to github.com and pypi.org (the `setup-python` /
`setup-node` actions install the tested interpreters 3.9-3.13 and Node 24; the
`test-dafny` job downloads the pinned Dafny 4.11.0 itself; the project `venv` is built
with `pip install --require-hashes -r requirements.txt`).

To re-register with a fresh token later: `./config.sh remove --token <remove-token>` then
re-run `config.sh` with a new token.

## Alternative: Docker on an always-on machine (Linux box, or Mac via Docker Desktop)

Keep the runner directory **outside the repository** so the token never lands in git:

```sh
mkdir -p ~/selyth-runner && cd ~/selyth-runner
cp <repo>/docs/operations/runner/Dockerfile <repo>/docs/operations/runner/docker-compose.yml .
# Settings -> Actions -> Runners -> New self-hosted runner -> LINUX tab (the container
# is Linux even on a Mac):
echo 'RUNNER_TOKEN=<token>' > .env && chmod 600 .env
docker compose up -d --build
docker compose ps -a          # container must be "running"
docker compose logs -f        # wait for "Listening for Jobs" (no container name needed)
```

The image (`docs/operations/runner/Dockerfile`) is the official
`ghcr.io/actions/actions-runner:2.338.2` plus `make`/`python3` (the base image already
carries the .NET 8 runtime Dafny needs). The named volume `runner-home` keeps the
registration and the job workspace (incl. the project `venv`) across restarts; on restart
`config.sh` is skipped if the runner is already registered. Do **not** mount
`/var/run/docker.sock`: no job of this repo runs docker. To re-register from scratch:
`docker compose down -v`, fresh token in `.env`, `docker compose up -d --build`.

## Security rules (read once)

- A self-hosted runner executes the workflow's shell commands as the user running it
  (or inside the container). Only attach one to a **private** repository whose workflows
  you trust (this one: the workflows live in this repo and are reviewed like any other
  code). Never attach a self-hosted runner to a public repository.
- Registration tokens are short-lived (1 hour), shown once by GitHub, and must never be
  committed or pasted into chats/issues. Store them only in the local `.env` (mode 600,
  outside the repo) or type them interactively.
- While the runner is down, jobs stay **queued** (not failed). The workflow's
  `concurrency: cancel-in-progress` cancels superseded runs so the runner never works on
  stale commits.
