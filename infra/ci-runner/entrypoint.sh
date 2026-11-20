#!/bin/sh
# Self-hosted runner entrypoint: registers ONCE with the registration token,
# then runs forever waiting for jobs ("Listening for Jobs"). Never a per-job
# `docker run`. The token is valid 1 hour and used exactly once.
#
# The token is scrubbed from the process environment before exec'ing run.sh:
# a plain `unset` would NOT hide it (it stays visible in /proc/1/environ of the
# shell); `env -u` removes it from the exec'd process image for real.
set -eu
cd /home/runner

if [ ! -f .runner ]; then
  : "${REPO_URL:?REPO_URL is required}"
  : "${RUNNER_TOKEN:?RUNNER_TOKEN is required for the first registration only}"
  ./config.sh --unattended \
    --url "$REPO_URL" \
    --token "$RUNNER_TOKEN" \
    --name "${RUNNER_NAME:-$(hostname)}" \
    --labels "kut-ci" \
    --work "_work" \
    --disableupdate
fi

# Post-job cleanup hook. Must never leave GITHUB_WORKSPACE missing.
export ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/home/runner/job-reset.sh

exec env -u RUNNER_TOKEN ./run.sh
