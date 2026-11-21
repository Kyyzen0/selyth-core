# selyth-test (Rust)

A small zero-dependency Rust CLI that runs the Selyth Core test suites. The Makefile stays
the single source of truth: the runner parses its suite targets (`test-python`, `test-dafny`,
`test-eval`, `test-ts`, `test-properties`), so a new `$(VPY) tools/test-*.py` line is picked
up automatically — no Rust change needed.

```
cargo run -- list                 # show the discovered steps and their requirements
cargo run -- run                  # all suites that can run on this machine
cargo run -- run python           # only the Python suites, in parallel
cargo run -- run python --jobs 8 --only test-ledger   # filter steps by name
cargo run -- run --strict         # a skipped step (missing Dafny/Node) fails the run
cargo test                        # unit + integration tests of the runner itself
```

Options: `--root DIR` (repo root, default `.`), `--jobs N` (default 4), `--only SUBSTR`,
`--strict`, `--python PATH` (interpreter for `$(VPY)` steps, default `<root>/.venv/bin/python`;
`make venv` is run automatically if it is missing), `--dafny PATH`, `--node PATH` (tool
detection also reads `SELYTH_DAFNY`, `NODE`/`SELYTH_NODE`, then `PATH`, then
`~/.local/opt/dafny/dafny`).

Each step prints `[pass]` / `[FAIL]` (with the tail of its output) / `[skip]` (tool missing)
plus its duration and suite; the run ends with a `summary: N passed, M failed, K skipped`
line and exits 1 if anything failed. Steps needing Dafny or Node are skipped, not failed,
unless `--strict`.

CI runs this in the `test-rust` job of `.github/workflows/ci.yml` (`cargo test`, then the
Python suite through the runner as a smoke test).
