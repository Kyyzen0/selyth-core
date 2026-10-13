# Test entry point. Everything runs inside a virtualenv built from the pinned,
# hash-locked requirements.txt, so the validator version cannot drift.
#
#   make test                     run every schema check
#   make test PYTHON=python3.12   use another interpreter (>= 3.9) for the venv
#   make clean                    delete the venv
PYTHON ?= python3
VENV   := .venv
VPY    := $(VENV)/bin/python

.PHONY: test venv clean

test: venv
	$(VPY) tools/test-validation.py
	@echo
	@echo "== Obligation Set: schema, canonical digest, report coverage"
	$(VPY) tools/test-obligation-set.py
	@echo
	@echo "== Policy engine: decisions, safety, determinism, replay"
	$(VPY) tools/test-policy-engine.py
	@echo
	@echo "== Legacy suite (tools/test-schemas.py, tools/minischema.py), kept as a secondary check"
	$(VPY) tools/test-schemas.py

venv: $(VENV)/.installed

$(VENV)/.installed: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(VPY) -m pip install --quiet --disable-pip-version-check --require-hashes -r requirements.txt
	touch $@

clean:
	rm -rf $(VENV)
