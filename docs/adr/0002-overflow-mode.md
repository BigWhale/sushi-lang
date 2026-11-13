---
status: accepted
---

# Integer overflow traps by default, and one flag selects the overflow mode

At run time an integer overflow wrapped silently, and a division by zero was undefined
behaviour (on arm64, `7 / 0` gave 0). We add the flag `--overflow=trap|wrap`. The default is
`trap`. The flag does not depend on `--opt`, because Sushi has no debug build or release
build, and a flag that has one job is easier to reason about.

The checked operators are the CE2077 set: `+`, `-`, `*`, `/`, `%` and unary minus. So one
rule covers compile time and run time. In `trap` mode, an overflow is a runtime error with a
code that names the operator and the type. In `wrap` mode, the bits that leave the type are
lost. A shift amount that is equal to or larger than the width traps in `trap` mode and is
masked in `wrap` mode. `~`, `&`, `|`, `^` and the shifts stay width-defined, and `as` stays a
truncating cast.

An integer division or remainder by zero traps in **both** modes, because a wrap has no
meaning there. `MIN / -1` traps in `trap` mode and gives `MIN` in `wrap` mode. Thus `divmod`
answers a bare `(T, T)`, and `MathError` is deleted. A checked operation such as
`a.checked_add(b)` answers `Maybe@(T)`.

## Considered Options

- **Use the optimization level as the switch** (`none` and `mem2reg` trap, `O1` to `O3`
  wrap). Rejected: one flag would have two jobs, and the corpus could not run optimized code
  with traps.
- **Always trap, as Swift does.** Rejected: a program that needs wrapping arithmetic in a hot
  loop then has no build-wide way to get it.

## Consequences

The mode is in the object-cache key. A `.slib` keeps the mode that it was built with, and
`--lib-info` shows it. The stdlib Sushi modules compile with the mode of the program. The
test corpus runs once, in `trap` mode. A small set of fixtures builds with `--overflow=wrap`.
