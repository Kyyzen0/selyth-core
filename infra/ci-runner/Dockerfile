# Self-hosted GitHub Actions runner for selyth-core (private repository).
# Reference: infra/ci-runner/ in Kyyzen0/Kut-Studio, adapted to this repo's CI:
# no FFmpeg/PySide6; adds make/python3 and the .NET 8 runtime that Dafny 4.11 needs.
# The actions/runner binary and the gh CLI are pinned by version AND sha256,
# verified at build time (sha256sum -c). Build for linux/amd64 (see compose):
# Dafny 4.11.0 ships no linux-arm64 build, so the image is pinned to amd64 and
# runs on Apple Silicon through Docker Desktop's qemu emulation.
FROM mcr.microsoft.com/dotnet/runtime-deps:8.0-noble

ARG TARGETARCH
ARG RUNNER_VERSION=2.338.2
ARG GH_VERSION=2.105.0
# Official release asset hashes: actions/runner v2.338.2, cli/cli v2.105.0.
ARG RUNNER_SHA256_AMD64=baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40
ARG RUNNER_SHA256_ARM64=670adb2f268fe21dcbb58b3db0e0aa9d5b3bddfe2c651481336e561ff86de945
ARG GH_SHA256_AMD64=79e7ab5db738c48777b696e374f8b428f0647debfb424f44d2f426370407fe2f
ARG GH_SHA256_ARM64=b1278c75107423d818f1937fd92cafc9e5e9b606631568f68d7fb816f5885a02

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -y \
    && apt-get install -y --no-install-recommends \
       ca-certificates git curl unzip jq make sudo \
       python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1001 --shell /bin/bash runner \
    && echo "runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/runner \
    && chmod 0440 /etc/sudoers.d/runner

RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) RUNNER_SHA="$RUNNER_SHA256_AMD64"; RUNNER_ARCH=x64;   GH_SHA="$GH_SHA256_AMD64"; GH_ARCH=amd64 ;; \
      arm64) RUNNER_SHA="$RUNNER_SHA256_ARM64"; RUNNER_ARCH=arm64; GH_SHA="$GH_SHA256_ARM64"; GH_ARCH=arm64 ;; \
      *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/gh.tgz \
      "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${GH_ARCH}.tar.gz"; \
    echo "$GH_SHA  /tmp/gh.tgz" | sha256sum -c -; \
    tar xzf /tmp/gh.tgz -C /tmp; \
    install -m 0755 "/tmp/gh_${GH_VERSION}_linux_${GH_ARCH}/bin/gh" /usr/local/bin/gh; \
    rm -rf /tmp/gh*; \
    curl -fsSL -o /tmp/runner.tgz \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"; \
    echo "$RUNNER_SHA  /tmp/runner.tgz" | sha256sum -c -; \
    mkdir -p /home/runner; \
    tar xzf /tmp/runner.tgz -C /home/runner; \
    rm -f /tmp/runner.tgz; \
    chown -R runner:runner /home/runner

COPY --chown=runner:runner entrypoint.sh job-reset.sh /home/runner/
RUN chmod +x /home/runner/entrypoint.sh /home/runner/job-reset.sh

USER runner
WORKDIR /home/runner
ENTRYPOINT ["/home/runner/entrypoint.sh"]
