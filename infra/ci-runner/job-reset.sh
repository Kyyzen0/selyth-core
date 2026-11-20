#!/bin/sh
# Post-job cleanup hook (ACTIONS_RUNNER_HOOK_JOB_COMPLETED).
# Wipes the work area between jobs to keep the volume from growing, but NEVER
# deletes GITHUB_WORKSPACE itself: a job starting without its workspace dir
# fails in ~8 seconds with "No such file or directory". It is recreated here
# after every cleanup.
set -eu
work="${ACTIONS_RUNNER_WORK:-/home/runner/_work}"
if [ -d "$work" ]; then
  find "$work" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
fi
mkdir -p "$work"
if [ -n "${GITHUB_WORKSPACE:-}" ]; then
  mkdir -p "$GITHUB_WORKSPACE"
fi
