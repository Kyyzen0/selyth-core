# Fixture repo for the selyth-test integration tests: not the real Makefile.
PYTHON ?= python3
VENV := .venv
VPY := $(VENV)/bin/python

.PHONY: test test-python test-dafny

test: test-python test-dafny

test-python: venv
	$(VPY) tools/test-ok.py
	@echo
	@echo "== heading"
	exit 1

test-dafny: venv
	$(VPY) tools/test-needs-dafny.py
