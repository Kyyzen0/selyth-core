# Self-hosted CI runner (zero GitHub-hosted minutes)

The repository is private, so GitHub-hosted runners draw on the account's included
Actions minutes (2,000/month on the Free plan). When that quota is exhausted, every
hosted job is refused before it starts ("spending limit needs to be increased") and no
job log exists. A **self-hosted runner executes jobs on your own machine and consumes
no GitHub-hosted minutes**, so CI stays free.

`.github/workflows/ci.yml` targets `runs-on: [self-hosted]` — one label, matched by any
self-hosted runner. To go back to hosted runners once minutes are available again,
replace the `runs-on` lines with `ubuntu-24.04` (not `latest`: 26.04 no longer ships
Python 3.9, the oldest interpreter the project supports); jobs are then refused while
the quota is exhausted and run automatically at the next monthly reset.

## Quickstart: one docker run on an always-on Mac (or Linux box)

Prerequisites: Docker Desktop installed and set to start at login (default). The token
comes from the repo's GitHub page: **Settings -> Actions -> Runners -> New self-hosted
runner -> LINUX tab** (the container is Linux, even on a Mac). The token is valid 1 hour,
used once, and must never be committed or pasted into chats/issues.

```sh
docker rm -f selyth-runner 2>/dev/null; true
read -s RUNNER_TOKEN          # paste the token, Enter — read -s keeps it out of shell history
docker run -d --name selyth-runner --restart unless-stopped \
  -v selyth-runner-home:/home/runner \
  -e REPO_URL=https://github.com/Kyyzen0/selyth-core \
  -e RUNNER_TOKEN="$RUNNER_TOKEN" \
  -e RUNNER_NAME=selyth-mac \
  ghcr.io/actions/actions-runner:2.338.2 \
  sh -c 'if [ ! -f .runner ]; then ./config.sh --unattended --url "$REPO_URL" --token "$RUNNER_TOKEN" --name "$RUNNER_NAME" --disableupdate; fi; exec ./run.sh'
docker logs -f selyth-runner  # wait for "Listening for Jobs"
```

That is the whole setup: the official image (git, curl, unzip, jq, .NET 8 runtime —
enough for the pinned Dafny 4.11.0; the workflow installs `make` itself if missing),
a named volume so the registration survives restarts/reboots (on restart `config.sh`
is skipped), and `--restart unless-stopped` so Docker relaunches it. No build, no
compose file, no token in any file.

Verify: `docker ps` shows the container running; the runner shows **Idle** in Settings ->
Actions -> Runners; queued PR runs are picked up immediately. While the machine is off,
jobs stay **queued** (not failed) and run at the next start. Settings -> Billing ->
Actions shows 0 hosted minutes spent (self-hosted minutes are not metered).

Job requirements: outbound HTTPS to github.com (checkout, setup-python/setup-node
toolchains, the Dafny release) and pypi.org (first `pip install --require-hashes -r
requirements.txt`; the venv persists in the volume).

Re-register from scratch (fresh token): `docker rm -fv selyth-runner selyth-runner-home`,
then the block above with a new token.

Do **not** mount `/var/run/docker.sock`: no job of this repo runs docker.

## Alternative: native runner on macOS (no Docker)

Download the **macOS** tarball (NOT the linux one — a Linux binary cannot execute on
macOS), extract, register, and install as a service (LaunchAgent, starts at every boot):

```sh
mkdir ~/actions-runner && cd ~/actions-runner
os=$( [ "$(uname -m)" = arm64 ] && echo osx-arm64 || echo osx-x64 )
curl -fsSL -o runner.tgz \
  "https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-${os}-2.338.2.tar.gz"
tar xzf runner.tgz
./config.sh --url https://github.com/Kyyzen0/selyth-core   # token from the MACOS tab
sudo ./svc.sh install "$USER" && sudo ./svc.sh start        # run from this directory!
sudo ./svc.sh status
```

## Security rules (read once)

- A self-hosted runner executes the workflow's shell commands as the runner user
  (inside the container, or as your user on a native runner). Only attach one to a
  **private** repository whose workflows you trust (this one: the workflows live in this
  repo and are reviewed like any other code). Never attach a self-hosted runner to a
  public repository.
- Registration tokens are short-lived (1 hour), shown once by GitHub, never committed,
  never pasted anywhere except the interactive `config.sh` prompt or the `read -s` above.
- The workflow's `concurrency: cancel-in-progress` cancels superseded runs so the
  runner never works on stale commits.
