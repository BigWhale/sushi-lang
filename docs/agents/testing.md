# Testing: the runner, its gates and the docs sweep

This file holds the details of the test runner: the flags, the gates that run as runner
steps, the fixture rules and the docs sweep. The core rules (one runner, a flag selects,
pytest never runs the compiler, test types, test-first) are in `CLAUDE.md`. Read this file
when you run a part of the suite, add or change a fixture directive, change the runner, or
edit the docs. The full text of each `# EXPECT_*` directive is in
`tests/TEST_METADATA_GUIDE.md`; this file does not copy it.

## The runner

- `enhanced_test_runner.py` runs every fixture and honours every `# EXPECT_*` directive.
  `run_tests.py` is the front end: it builds the stdlib, the helpers and the leak
  interposer, gives the runner its own temporary cache directory, arms the spelling gate
  and hands over.
- `select_fixtures` (`tests/test_metadata.py`, beside `collect_fixtures`) is the one
  selector.
- `python tests/run_tests.py` runs the whole corpus, every directive, `EXPECT_NO_LEAKS`
  included. The malloc interposer is in `tests/leakcheck`. A missing interposer is a loud
  skip, never a silent pass. The interposer counts BYTES and sees no file descriptor that
  the program opened and did not close; `EXPECT_NO_OPEN_FDS` checks those.
- `--compile-only` runs the fixtures that never execute a binary (every `test_err_`, most
  `test_warn_`; about 40% of the corpus), asserted IN FULL. It is the gate that matches a
  diagnostics change. The rule is `should_run_runtime_test`, not a second list.
- `--leaks-only` runs the leak-annotated subset. It is a selector, not a stronger check.
- A leak assertion that was SKIPPED fails the run (#605): a run that asserted nothing is
  not a pass. `--allow-leak-skips` is the one escape, for a platform with no interposer.
- A run that selects NO fixture fails (#765), in every spelling.
- `--enhanced` is accepted and does nothing. Every run is that run. It stays for the CI
  lines and for a typed habit.
- `--filter <pattern>` selects by a substring of the path under `tests/`.
- `-j N` sets the parallel job count. The default is 4, which suits a GitHub standard
  runner (4 vCPU on Linux, 3 on macOS). `SUSHI_TEST_JOBS` overrides it on a development
  machine with more cores (`export SUSHI_TEST_JOBS=8`). An explicit `-j` beats the
  variable. Scaling is sublinear, because every test runs `clang`.
- The compiler runs as a child of a fork-server by default (`tests/fork_server.py`).
  `--fresh-processes` starts a fresh `sushic` for each compile.

## Runner steps (gates)

Each step has a runner test under `tests/unit/runner/`.

- **The stdlib doc-block gate** (`stdlib_doc_gate`, #953). A full run and `--compile-only`
  compile ONE program that imports every `SOURCE_STDLIB_MODULES` module with
  `--warn-missing-docs` and `SUSHI_STDLIB_DOC_GATE=1`, and fail on any docs-pass
  diagnostic under `src_sushi/`. The variable makes the `docs` pass check the BUNDLED stdlib
  units too (it skips every provenance unit otherwise). A user's source library stays
  skipped. When the variable is unset, nothing changes. A stdlib module is never built
  alone (the home modules of the predefined enums cannot be), so the gate reads the real
  path. A selection that holds no stdlib module prints its skip.
- **The library-reader gate** (`lib_reader_gate`): damaged copies of a hybrid and a binary
  library (truncated, bad magic, oversize length, a manifest with a missing field) go
  through both `--lib-info` halves and a consumer. Every case must print its code.
- **The library-report gate** (`lib_info_report_gate`): a multi-payload variant and a
  two-constraint generic in three kinds, through both halves and a consumer.
- `--filter stdlib/slib/lib_reader/` selects the two library gates.
- **The dead-code gate** (`stdlib_dead_code_gate`): the all-modules program and each
  `toolchain/src/` program compile under `--warn-unused` with `SUSHI_STDLIB_DEAD_GATE=1`.
  Any dead-code warning under `src_sushi/` or `toolchain/src/` fails.
- **`SUSHI_SPELLING_GATE`**: the front end arms it, and it is off for a user. It refuses a
  diagnostic that prints a RETIRED spelling. The rule and its patterns are in
  `docs/agents/language-rules.md` (Generic syntax).

## Fixtures

- Test types, by name: `test_<name>.sushi` must compile (exit 0); `test_warn_` exits 1;
  `test_err_` exits 2. New tests go under `tests/<area>/<feature>/`.
- The directives live in the leading comment block. Output and diagnostics:
  `EXPECT_RUNTIME_EXIT`, `EXPECT_STDOUT_EXACT`, `EXPECT_STDOUT_CONTAINS`,
  `EXPECT_STDERR_CONTAINS`, `EXPECT_STDERR_EMPTY`, `EXPECT_NO_LEAKS`, `EXPECT_NO_OPEN_FDS`,
  `EXPECT_ERROR_CODE`, `EXPECT_ERROR_CODES_EXACT` (the exact code MULTISET, warnings
  included: `CE0112, CE0112` is two, so a fixture can assert that a fault is reported
  once), `EXPECT_IR_HOLDS` / `EXPECT_IR_LACKS` (a text in the IR of one function),
  `EXPECT_IR_FUNCTION` / `EXPECT_IR_NO_FUNCTION` (a glob over the symbols the IR defines;
  with no `$` it reads the stem after the unit: `Box__string_tag`), `COMPILER_FLAGS` (extra
  `sushic` flags; a flag the runner owns is refused).
- Directives for the driver and the cache. The runner owns `-o`, `--cache-dir`,
  `--clean-cache` and `--lib-info`, so these directives state what a fixture cannot pass:
  the REBUILD form (a `v2/` directory beside the fixture is copied over it for a second
  build with the same cache; `EXPECT_REBUILT` / `EXPECT_CACHED` /
  `EXPECT_STDOUT_EXACT_BEFORE_REBUILD`; `REBUILD_FLAGS` adds flags to the second build
  alone); `RUN_IN_FIXTURE_DIR` (start `sushic` in a copy of
  the fixture's directory); `BUILD_LIB: x.sushi` (a source `.slib` on `SUSHI_LIB_PATH`),
  `BUILD_LIB_BINARY` / `BUILD_LIB_HYBRID` (the same, built `--lib-kind binary` / `hybrid`),
  each with a `-> name.slib` and an `@ version` form, built in written order and required
  to exit 0; `BUILD_LIB_WARNS: x.sushi [kind] -> CW1002` (a library that must build with
  exactly that set of warnings); `BUILD_LIB_AT: x.sushi -> path` (a `.slib` inside the
  copy); `STDLIB_MODULE: name=path`; `FIXTURE_CACHE_DIR: rel`; `EXPECT_PATH_EXISTS` /
  `EXPECT_PATH_ABSENT` / `EXPECT_PATH_EXISTS_BEFORE_CLEAN`; `THEN_CLEAN_CACHE: bare|source`;
  `OUTPUT_PATH: rel` (the `-o` path inside the copy; the one way a fixture spells `--lib`).
- A success test that prints MUST carry an `EXPECT_STDOUT_*` (ratchet:
  `tests/unit/test_stdout_coverage.py`).
- A fixture's IDENTITY is its path under `tests/`, not its file name. `collect_fixtures` is
  the one collector and `fixture_binary_name` names the output binary, both in
  `tests/test_metadata.py` (#604). A file name must still be UNIQUE across `tests/`,
  because the runners report and quarantine by name (gate:
  `tests/unit/runner/test_fixture_identity_is_its_path.py`).
- The runner reads a fixture as BYTES. A directive block it cannot decode FAILS the
  fixture. Every corpus gate reads through one reader (`corpus_files` / `corpus_text`,
  `tests/test_metadata.py`). A file that is not UTF-8 is allowed only beside a `test_err_`
  fixture that declares the source-read error.

## pytest

- `python -m pytest -q` runs the Python unit layer (`testpaths = tests/unit`), WITHOUT the
  runner tests (`pytest.ini`: `addopts = -m "not runner"`). `python -m pytest -q -m runner`
  runs them.
- `tests/unit/compiler_stage_gate.py` (registered in `tests/unit/conftest.py`) fails any
  pytest test outside `tests/unit/runner/` that parses Sushi, runs the analyzer, generates
  code or starts `sushic`.
- pytest keeps what only Python can check: scans of the compiler's Python source (the
  one-seam gates), vulture, and checks of the registries and tables.

## The docs sweep

`python tests/docs_sweep.py` has THREE collectors and one outcome vocabulary. Run it BY
HAND after you edit docs; it is deliberately not a CI job. `--only {all,docs,examples,files}`
picks one. Outcomes: pass (exit 0 or 1), expected-error, skip, fail (drift).

- **`docs`** compiles every self-contained ```` ```sushi ```` block under `docs/`. The
  markers are HTML comments on the line ABOVE the fence
  (`<!-- docs-sweep: error CExxxx -->`, `<!-- docs-sweep: skip (reason) -->`). A fence it
  cannot close (`~~~`, 4+ backticks) is stepped over whole.
- **`examples`** parses every `.sushi` file under the scan roots and compiles each
  `- Example:` fence from OUTSIDE its unit. The markers ride on the fence's own info string
  (` ```sushi `, `no_run`, `skip (reason)`, `error CExxxx`). A private declaration or a unit
  that declares `main` is a printed SKIP. Output is never asserted.
- **`files`** compiles every `.sushi` FILE under `docs/` (#547) and never runs one. A file
  with no `fn main(` at line start is a LIBRARY and is built with `--lib`, so "no main" is a
  category and not drift. Every library of a directory is built BEFORE that directory's
  programs, into a temp dir that the programs get on `SUSHI_LIB_PATH`; this checks the
  tutorial's `use <lib/...>` page end to end. The corpus needs NO marker today. The escape
  hatch is `# docs-sweep: skip (reason)` / `# docs-sweep: error CExxxx` in the file's
  LEADING comment block.
- Output and cache both go to a temp dir (`--cache-dir`), so the tree stays clean.
