# Test entry point. Everything runs inside a virtualenv built from the pinned,
# hash-locked requirements.txt, so the validator version cannot drift.
#
#   make test                     run every check: Python suites, Dafny backend, TypeScript port
#   make test-python              Python suites only (no Node needed)
#   make test-dafny               Dafny backend against a real Dafny 4.11.0 (SELYTH_DAFNY=<path> if not at ~/.local/opt/dafny/dafny)
#   make test-eval                evaluation harness (M6) on its scripted corpus: needs Dafny and Node, about five minutes
#   make test-ts                  TypeScript port only (needs Node >= 24; NODE_BIN=<dir> if not on PATH)
#   make test-properties          property-test backend, the second backend (needs Node >= 22.6
#                                 for type stripping; SELYTH_NODE=<path> if not on PATH)
#   make test-rust                compile and test the Rust test runner (rust/); needs cargo
#   make bench [EXAMPLE=gcd]      measure the gate's cost on an example (default ledger): per-obligation
#                                 table + bench report (needs Dafny)
#   make check [EXAMPLE=gcd]      the whole gate on an example in one command: validate + components,
#                                 workspace regeneration check, lower, Dafny verify, accept under the
#                                 lock, replay, property backend when the example has one (needs Dafny;
#                                 Node too for EXAMPLE=gcd)
#   make gcd-workspace            regenerate the gcd example's Dafny model from gcd.ts
#   make test PYTHON=python3.12   use another interpreter (>= 3.9) for the venv
#   make clean                    delete the venv
PYTHON ?= python3
VENV   := .venv
VPY    := $(VENV)/bin/python

NODE_BIN ?=
export PATH := $(if $(NODE_BIN),$(NODE_BIN):)$(PATH)

.PHONY: test test-python test-dafny test-ts test-properties test-eval test-rust bench check ledger-workspace gcd-workspace venv clean

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
	@echo "== M6-Lite agent runner: model-call counting proxy, budgets, launch gate"
	$(VPY) tools/test-agent-runner.py
	@echo
	@echo "== M6-Lite launch gate: corpus minimums, artefact digests, control plan, manifest digest"
	$(VPY) tools/test-launch-gate.py
	@echo
	@echo "== M6-Lite review trigger: ADR-004 classes, second-review sample, evidence package"
	$(VPY) tools/test-review.py
	@echo
	@echo "== M6 audit journal: hash chain, tamper detection, head anchor"
	$(VPY) tools/test-audit-journal.py
	@echo
	@echo "== M6-Lite launch rehearsal (dry run): corpus, gate, runs, review, journal; scoring skips without Dafny/Node"
	$(VPY) tools/test-m6-dry-run.py
	@echo
	@echo "== Bench report schema and summarise() (docs/specifications/bench-schema.json)"
	$(VPY) tools/test-bench.py
	@echo
	@echo "== Adversarial suite: an agent trying to get a bad change accepted is refused"
	$(VPY) tools/test-adversarial.py
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
	@echo
	@echo "== The gcd: third-party code (pallosp/congruence_solver, MIT) through the whole gate"
	$(VPY) tools/test-gcd.py

# Regenerate the ledger's Dafny model from ledger.ts (test-ts-to-dafny.py fails if it is stale).
ledger-workspace: venv
	$(VPY) tools/ts_to_dafny.py docs/specifications/examples/ledger/ledger.ts --spec docs/specifications/examples/ledger/ledger.translation.json -o docs/specifications/examples/ledger/ledger.workspace.dfy

# Same for the third-party gcd example (test-gcd.py fails if the workspace is stale).
gcd-workspace: venv
	$(VPY) tools/ts_to_dafny.py docs/specifications/examples/gcd/gcd.ts --spec docs/specifications/examples/gcd/gcd.translation.json -o docs/specifications/examples/gcd/gcd.workspace.dfy

# The TypeScript port is checked against the Python adapter, so it needs the venv too.
test-ts: venv
	@command -v node >/dev/null || { echo "Node >= 24 not found: install it or pass NODE_BIN=<dir with node>"; exit 1; }
	cd ts && npm ci --ignore-scripts --no-audit --no-fund && npm test

# The property-test backend (second backend; docs/specifications/property-backend.md): runs the
# acceptance criteria of an example on a seeded sample with Node's type stripping.
test-properties: venv
	@command -v node >/dev/null || { echo "Node >= 22.6 not found: install it or pass NODE_BIN=<dir with node>"; exit 1; }
	$(VPY) tools/test-properties.py

# The evaluation harness runs the corpus through Dafny and the Node oracle.
test-eval: venv
	@command -v node >/dev/null || { echo "Node >= 24 not found: install it or pass NODE_BIN=<dir with node>"; exit 1; }
	$(VPY) tools/test-evaluate.py
	$(VPY) tools/test-m6-dry-run.py

# The Rust test runner (rust/): compiles the CLI and runs its own tests.
test-rust:
	cd rust && cargo test --quiet

# Measure the gate's cost on an example (default ledger, EXAMPLE=gcd for the third-party one;
# E-05/P-01/F1): a table on stdout, the bench report JSON after it. Needs a real Dafny.
EXAMPLE ?= ledger
EXDIR   := docs/specifications/examples/$(EXAMPLE)
# When the example has a property sidecar (gcd), the bench runs the merged gate report
# (Dafny + property tests); otherwise the Dafny-only report.
BENCH_GATE := $(if $(wildcard $(EXDIR)/$(EXAMPLE).properties.json),--source $(EXDIR)/$(EXAMPLE).ts --spec $(EXDIR)/$(EXAMPLE).properties.json,)
bench: venv
	$(VPY) tools/bench.py --obligation-set $(EXDIR)/$(EXAMPLE).obligations.json \
	    --policy $(EXDIR)/$(EXAMPLE).policy.json \
	    --lock $(EXDIR)/$(EXAMPLE).lock.json \
	    --workspace $(EXDIR)/$(EXAMPLE).workspace.dfy $(BENCH_GATE)

# The whole gate on one example, in one command (validate, regenerate + compare the workspace,
# lower, verify with Dafny, accept under the lock, replay; property backend too when the example
# has one). Needs Dafny, and Node for EXAMPLE=gcd.
check: venv
	$(VPY) tools/check_example.py --example $(EXAMPLE)

clean:
	rm -rf $(VENV) ts/node_modules
