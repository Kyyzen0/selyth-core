# Self-hosted CI runner (zero GitHub-hosted minutes)

The repository is private, so GitHub-hosted runners draw on the account's included
Actions minutes (2,000/month on the Free plan). When that quota is exhausted, every
hosted job is refused before it starts ("spending limit needs to be increased") and no
job log exists. A **self-hosted runner executes jobs on your own machine and consumes
no GitHub-hosted minutes**, so CI stays free.

`.github/workflows/ci.yml` therefore targets `runs-on: [self-hosted, linux]` (any
architecture: x64 or arm64). To go back to hosted runners once minutes are available
again, replace the `runs-on` lines with `ubuntu-24.04` (not `latest`: 26.04 no longer
ships Python 3.9, the oldest interpreter the project supports).

## Quickstart: Docker on an always-on Mac (or any always-on Linux box)

Keep the runner directory **outside the repository** so the token never lands in git:

```sh
mkdir ~/selyth-runner && cd ~/selyth-runner
cp <repo>/docs/operations/runner/Dockerfile <repo>/docs/operations/runner/docker-compose.yml .
# GitHub page of the repo: Settings -> Actions -> Runners -> New self-hosted runner -> Linux.
# Copy the registration token it shows (valid 1 hour, used once):
echo 'RUNNER_TOKEN=<token>' > .env && chmod 600 .env
docker compose up -d --build
```

That's it: the container registers itself (default labels `self-hosted`, `Linux`,
`X64`/`ARM64`), then `./run.sh` polls for jobs. The named volume `runner-home` keeps
the registration and the job workspace (including the project `venv`) across restarts;
on restart, `config.sh` is skipped because the runner is already registered.

Verify: the runner shows **Idle** in Settings -> Actions -> Runners, and a push (or
Actions -> test -> Run workflow) makes the jobs run on it. In Settings -> Billing ->
Plans & usage -> Actions, self-hosted minutes are not metered: 0 hosted minutes spent.

Re-register from scratch (new token needed): `docker compose down -v`, update `.env`,
`docker compose up -d --build`.

The image (`docs/operations/runner/Dockerfile`) is the official
`ghcr.io/actions/actions-runner:2.338.2` plus `make`/`python3` (the project's Makefile
builds a venv; `actions/setup-python` provides the tested interpreters 3.9-3.13, and the
`test-dafny` job downloads the pinned Dafny 4.11.0 itself; the base image already carries
the .NET 8 runtime Dafny needs). Do **not** mount `/var/run/docker.sock`: no job of this
repo runs docker.

## Security rules (read once)

- A self-hosted runner executes the workflow's shell commands inside the container.
  Only attach one to a **private** repository whose workflows you trust (this one: the
  workflows live in this repo and are reviewed like any other code). Never attach a
  self-hosted runner to a public repository.
- The registration token lives only in `~/selyth-runner/.env` (mode 600, outside the
  repo, never committed).
- Run Docker Desktop with default settings; the container runs as the unprivileged
  `runner` user built into the official image.

## What the jobs need

- Outbound HTTPS to github.com (checkout, setup-python/setup-node toolchains, the Dafny
  release) and to pypi.org (first `pip install --require-hashes -r requirements.txt`;
  the venv persists in the volume afterwards).
- While the runner container is down, jobs stay **queued** (not failed). The workflow's
  `concurrency: cancel-in-progress` cancels superseded runs so the runner never works
  on stale commits.

## Bare-metal fallback (no Docker)

On any Linux box, as a dedicated unprivileged user:

```sh
mkdir actions-runner && cd actions-runner
curl -fsSL -o actions-runner.tar.gz \
  https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-linux-x64-2.338.2.tar.gz
tar xzf actions-runner.tar.gz
./config.sh --url https://github.com/Kyyzen0/selyth-core --labels self-hosted,linux --ephemeral --unattended
# paste the token from Settings -> Actions -> Runners -> New self-hosted runner
./run.sh            # foreground; or: sudo ./svc.sh install <user> && sudo ./svc.sh start
```

(`--ephemeral` = one job then auto-unregister; wrap `./run.sh` in a `while true` loop
or use the service mode for continuous availability.)
