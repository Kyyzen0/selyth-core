# Test entry point. Everything runs inside a virtualenv built from the pinned,
# hash-locked requirements.txt, so the validator version cannot drift.
#
#   make test                     run every check: Python suites, Dafny backend, TypeScript port
#   make test-python              Python suites only (no Node needed)
#   make test-dafny               Dafny backend against a real Dafny 4.11.0 (SELYTH_DAFNY=<path> if not at ~/.local/opt/dafny/dafny)
#   make test-ts                  TypeScript port only (needs Node >= 24; NODE_BIN=<dir> if not on PATH)
#   make test PYTHON=python3.12   use another interpreter (>= 3.9) for the venv
#   make clean                    delete the venv
PYTHON ?= python3
VENV   := .venv
VPY    := $(VENV)/bin/python

NODE_BIN ?=
export PATH := $(if $(NODE_BIN),$(NODE_BIN):)$(PATH)

.PHONY: test test-python test-dafny test-ts venv clean

test: test-python test-dafny test-ts

test-python: venv
	$(VPY) tools/test-validation.py
	@echo
	@echo "== Obligation Set: schema, canonical digest, report coverage"
	$(VPY) tools/test-obligation-set.py
	@echo
	@echo "== Policy engine: decisions, safety, determinism, replay"
	$(VPY) tools/test-policy-engine.py
	@echo
	@echo "== TypeScript adapter: Verification IR determinism"
	$(VPY) tools/test-ts-adapter.py
	@echo
	@echo "== Legacy suite (tools/test-schemas.py, tools/minischema.py), kept as a secondary check"
	$(VPY) tools/test-schemas.py

venv: $(VENV)/.installed

$(VENV)/.installed: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(VPY) -m pip install --quiet --disable-pip-version-check --require-hashes -r requirements.txt
	touch $@

# Runs real Dafny: the backend is only as tested as the prover behind it.
test-dafny: venv
	$(VPY) tools/test-dafny-backend.py

# The TypeScript port is checked against the Python adapter, so it needs the venv too.
test-ts: venv
	@command -v node >/dev/null || { echo "Node >= 24 not found: install it or pass NODE_BIN=<dir with node>"; exit 1; }
	cd ts && npm ci --ignore-scripts --no-audit --no-fund && npm test

clean:
	rm -rf $(VENV) ts/node_modules
