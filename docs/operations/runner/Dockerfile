# Self-hosted Actions runner image for selyth-core, based on the official image.
# The official image ships git/curl/unzip/jq + the .NET 8 runtime (enough for the
# pinned Dafny 4.11.0); this adds what `make` needs to build the project venv.
FROM ghcr.io/actions/actions-runner:2.338.2

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends make python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*
USER runner
