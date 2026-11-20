# Self-hosted CI runner (zero GitHub-hosted minutes)

The repository is private, so GitHub-hosted runners draw on the account's included
Actions minutes (2,000/month on the Free plan). When that quota is exhausted, every
hosted job is refused before it starts ("spending limit needs to be increased") and no
job log exists. A **self-hosted runner executes jobs on your own machine and consumes
no GitHub-hosted minutes**, so CI stays free.

`.github/workflows/ci.yml` therefore targets the runner labels
`[self-hosted, linux, x64, selyth]`. To go back to hosted runners once minutes are
available again, replace those `runs-on` lines with `ubuntu-24.04` (not `latest`:
26.04 no longer ships Python 3.9, the oldest interpreter the project supports).

## Security rules (read once)

- A self-hosted runner executes the workflow's shell commands as the user running it.
  Only ever attach one to a **private** repository whose workflows you trust (this one:
  the workflows are in this repo and reviewed like any other code). Never attach a
  self-hosted runner to a public repository.
- Run it under a dedicated, unprivileged OS user, on a machine you control.
- Prefer an **ephemeral** runner (`--ephemeral`): it registers for exactly one job and
  unregisters itself, so a stale token cannot be reused. Restart it per job (see below).
- The registration token shown by GitHub expires after 1 hour and must not be committed
  anywhere; it is only used once, locally, by `config.sh`.

## Setup (once, on the runner machine)

Requirements: Linux x64, `git`, `curl`, `unzip`, `make`, Python 3.9+ and Node on PATH
(the workflow's `setup-python`/`setup-node` actions install the exact versions they
need, but the runner itself needs a system Python to bootstrap).

In the repository's GitHub page: **Settings → Actions → Runners → New self-hosted
runner → Linux → x64**. GitHub shows the download and configure commands; they look
like this (run them in an empty directory, as the dedicated user):

```sh
mkdir actions-runner && cd actions-runner
curl -fsSL -o actions-runner.tar.gz \
  https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-linux-x64-2.338.2.tar.gz
tar xzf actions-runner.tar.gz
./config.sh --url https://github.com/Kyyzen0/selyth-core \
  --labels self-hosted,linux,x64,selyth --ephemeral --unattended
```

`config.sh` consumes the one-time registration token from the GitHub page
(paste it when prompted, or set `ACTIONS_RUNNER_INPUT_TOKEN` for the unattended run).
Nothing secret is stored in the repository.

## Running it

Interactive (foreground):

```sh
./run.sh
```

As a service (survives logout), or per-job with the ephemeral flag:

```sh
sudo ./svc.sh install <runner-user>   # once
sudo ./svc.sh start
# ephemeral mode: a supervisor loop restarts ./run.sh until the machine stops;
# a one-shot job exits and unregisters itself, then the loop re-registers via a
# fresh token only if you script it — simplest is the service mode above.
```

With `--ephemeral`, each `./run.sh` invocation takes exactly one job and then exits;
a small `while true; do ./run.sh; done` loop (or the service mode without
`--ephemeral`) keeps the runner available continuously.

## What the jobs need on the runner machine

- `actions/checkout`, `actions/setup-python` (matrix 3.9–3.13) and `actions/setup-node`
  (Node 24) download their toolchains from GitHub at job start: outbound HTTPS to
  github.com is required.
- `make` builds a local `venv` with `pip install --require-hashes -r requirements.txt`:
  outbound HTTPS to pypi.org is required (first run only; the venv persists in the
  workspace between jobs).
- The `test-dafny` job downloads the pinned Dafny 4.11.0 from github.com itself.
- While the runner is offline, jobs stay **queued** (not failed): the PR page shows
  them waiting. `concurrency: cancel-in-progress` in the workflow cancels superseded
  runs so the runner never works on stale commits.

## Verifying

Push any commit (or use **Actions → test → Run workflow**): the run's jobs should show
the runner name `selyth` and complete without any GitHub-hosted minutes being consumed
(check **Settings → Billing → Plans & usage → Actions**: self-hosted minutes are not
metered).
