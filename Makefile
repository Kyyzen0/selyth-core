# Test entry point. Everything runs inside a virtualenv built from the pinned,
# hash-locked requirements.txt, so the validator version cannot drift.
#
#   make test                     run every check: Python suites, Dafny backend, TypeScript port
#   make test-python              Python suites only (no Node needed)
#   make test-dafny               Dafny backend against a real Dafny 4.11.0 (SELYTH_DAFNY=<path> if not at ~/.local/opt/dafny/dafny)
#   make test-eval                evaluation harness (M6) on its scripted corpus: needs Dafny and Node, about five minutes
#   make test-ts                  TypeScript port only (needs Node >= 24; NODE_BIN=<dir> if not on PATH)
#   make bench                    measure the gate's cost on the ledger example: per-obligation table + bench report (needs Dafny)
#   make test PYTHON=python3.12   use another interpreter (>= 3.9) for the venv
#   make clean                    delete the venv
PYTHON ?= python3
VENV   := .venv
VPY    := $(VENV)/bin/python

NODE_BIN ?=
export PATH := $(if $(NODE_BIN),$(NODE_BIN):)$(PATH)

.PHONY: test test-python test-dafny test-ts test-eval bench ledger-workspace venv clean

test: test-python test-dafny test-ts test-eval

test-python: venv
	$(VPY) tools/test-validation.py
	@echo
	@echo "== TypeScript to Dafny translator: generated workspace, refusals, differential"
	$(VPY) tools/test-ts-to-dafny.py
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
	@echo "== M6-Lite task manifest schema (docs/evaluation/task-schema.json)"
	$(VPY) tools/test-task-schema.py
	@echo
	@echo "== Bench report schema and summarise() (docs/specifications/bench-schema.json)"
	$(VPY) tools/test-bench.py
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
	@echo
	@echo "== Negative scenarios (M5), end to end with Dafny"
	$(VPY) tools/test-scenarios.py
	@echo
	@echo "== The ledger: twenty obligations on a small module, with a mutation study"
	$(VPY) tools/test-ledger.py

# Regenerate the ledger's Dafny model from ledger.ts (test-ts-to-dafny.py fails if it is stale).
ledger-workspace: venv
	$(VPY) tools/ts_to_dafny.py docs/specifications/examples/ledger/ledger.ts --spec docs/specifications/examples/ledger/ledger.translation.json -o docs/specifications/examples/ledger/ledger.workspace.dfy

# The TypeScript port is checked against the Python adapter, so it needs the venv too.
test-ts: venv
	@command -v node >/dev/null || { echo "Node >= 24 not found: install it or pass NODE_BIN=<dir with node>"; exit 1; }
	cd ts && npm ci --ignore-scripts --no-audit --no-fund && npm test

# The evaluation harness runs the corpus through Dafny and the Node oracle.
test-eval: venv
	@command -v node >/dev/null || { echo "Node >= 24 not found: install it or pass NODE_BIN=<dir with node>"; exit 1; }
	$(VPY) tools/test-evaluate.py

# Measure the gate's cost on the ledger example (E-05/P-01/F1): a table on stdout,
# the bench report JSON after it. Needs a real Dafny, like test-dafny.
bench: venv
	$(VPY) tools/bench.py --obligation-set docs/specifications/examples/ledger/ledger.obligations.json \
	    --policy docs/specifications/examples/ledger/ledger.policy.json \
	    --lock docs/specifications/examples/ledger/ledger.lock.json \
	    --workspace docs/specifications/examples/ledger/ledger.workspace.dfy

clean:
	rm -rf $(VENV) ts/node_modules
