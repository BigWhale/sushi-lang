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
  The parity test `tests/unit/test_slib_info_parity.py` locks the two together.
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
| `slib-info` | `src/slib_info.sushi` | print the metadata report of a `.slib` library |
