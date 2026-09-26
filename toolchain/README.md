# Toolchain

Tools that compile Sushi live here, written in Sushi. The directory is
repo-only: it does not ship in the wheel. Reusable library code lives in the
stdlib instead, under `sushi_lang/sushi_stdlib/src_sushi/toolchain/` (imported
with `use <toolchain/...>`); this directory holds the executable programs.

## Layout

- `src/` — one `.sushi` program per tool.
- `bin/` — build output (gitignored).
- `build.py` — builds every tool in its `TOOLS` table into `bin/`.

## Build

```
./toolchain/build.py
```

The build is manual. Run it again after you change a tool source or the
compiler; a stale binary keeps its old behavior until you do. The compiler's
incremental cache keeps a repeat run cheap.

## Delegation

`sushic` runs a toolchain binary when one exists, and falls back to its Python
implementation when one does not. The contract:

- `sushic --lib-info FILE` runs `toolchain/bin/slib-info FILE` and returns its
  exit code. The tool owns the full report; the Python fallback
  (`print_library_info` in `sushi_lang/compiler/cli.py`) prints the same body.
  The parity of the two is not gated today: pytest runs no compiler, and no
  fixture compares the tool with the fallback yet.
- **A switch is spelled the same at both ends.** `sushic --lib-info FILE --docs`
  passes `--docs` through to the tool as itself, and `--color=always|never` the
  same way (`auto` is the default and says nothing, so it is not passed). The
  delegation forwards only the switches it knows, so a new one is a change at
  both ends and in the fallback. The tool answers `--help` on its own; `sushic
  --help` lists the compiler's own flags and does not run the tool.
- **The colour ladder is written twice, and must stay one ladder.** `--color`,
  then `NO_COLOR`, then `CLICOLOR_FORCE`, then `TERM=dumb`, then whether stdout
  is a terminal. Python has it in `internals/styling.py:should_colour`, the tool
  in `want_colour`. `tests/unit/test_report_colour.py` locks each rung on the
  Python side; nothing compares the two coloured reports today.
- A wheel install has no `toolchain/` directory, so it always uses the fallback.
- Error messages can differ between the tool and the fallback; the success
  report cannot.

Environment variables:

- `SUSHI_TOOLCHAIN=off` (or `0`) — skip the tools, use the Python fallback.
- `SUSHI_TOOLCHAIN_BIN=DIR` — look for tool binaries in `DIR` instead of
  `toolchain/bin/`.

## Tools

| tool | source | does |
|---|---|---|
| `slib-info` | `src/slib_info.sushi` | print the metadata report of a `.slib` library (`--docs` adds every documentation block, `--color` forces or forbids colour, `--help` explains itself) |
