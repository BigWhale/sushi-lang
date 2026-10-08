# The `assert` statement

Status: DECIDED. The rulings are David's (2026-10-02).

## The rule

```sushi
use <collections/iter>

fn mean(i32[] xs) i32:
    assert(xs.len() > 0)
    let i32 total = xs.fold(0, |i32 a, i32 b| a + b)
    assert(total >= 0, "the total of {xs.len()} values went negative")
    return total / xs.len()
```

`assert(cond)` and `assert(cond, message)` are a STATEMENT. When `cond` is `true`, the
program continues. When `cond` is `false`, the program stops with a runtime error:

```
Runtime Error RE2026: assertion failed at src/stats.sushi:7:5: the total of 3 values went negative
Runtime Error RE2026: assertion failed at src/stats.sushi:4:5
```

The exit code is 1, the same as every other runtime error.

`assert` is a reserved word. A variable, a function, a type, a field or a method cannot
take it as its name ([CE6001](../error-catalog.md#ce6001), as for every keyword).

## The four rulings

1. **The message is any `string` expression.** A literal, an interpolation, a call that
   answers a `string`. The program evaluates the message ONLY when the condition is
   `false`. A passing assert does not build its message, so it costs no allocation.
2. **The output carries `file:line:col`.** The position is the position of the `assert`
   keyword. The file name is the name that a compile-time diagnostic in that file shows:
   a path relative to the working directory of the build (`./src/stats.sushi`), or the
   base name of a file outside it. A generic function of a binary library is a slice of
   source in the manifest, so its position is the one its diagnostics show: the label
   `<template:lib:name>` and the line in the slice.
3. **An assert is always on.** No flag, no build mode turns it off. Sushi has no
   debug/release split, and a condition with a side effect (`assert(xs.pop().is_some())`)
   must do the same thing in every build. A flag can come later; it is not part of this
   design.
4. **A condition that the compiler can read gets no special treatment.** `assert(false)`
   is an ordinary run-time trap. It does NOT end the path: the missing-return rule ([CE0107](../error-catalog.md#ce0107))
   and the dead-statement rule ([CE0140](../error-catalog.md#ce0140)) read it as a statement that falls through. A
   condition that folds to `true` or to `false` gets no warning and no error.

## Why a statement, and not a function

- **The message is lazy.** A function evaluates its arguments before the call. An assert
  must not build its message when the condition holds.
- **The position is the call site's.** A function knows its own position, not the
  caller's. The statement takes the position from its own node.
- **No import.** An assert is available in every unit, as `print` is.

## Why a trap, and not a `Result`

An assert states an invariant: a state that a correct program never reaches. A failure is
a DEFECT, not data. The error channel `| E` is for a failure the caller can handle. An
assert does not touch the channel: a bare function and a function with a channel can each
assert, and a channel does not catch the trap. This is the rule of "Traps Are Not Errors"
in `docs/error-handling.md`.

## The semantic rules

- **The condition is a `bool` and nothing else.** It is the tenth condition position,
  beside `if`, `while` and the operands of `and`/`or`/`xor`/`not`. It goes through
  `reject_non_bool_condition`, so a `Result` or a `Maybe` gets [CE2516](../error-catalog.md#ce2516) and every other
  type gets [CE2005](../error-catalog.md#ce2005).
- **The message is a `string`.** Any other type is a type error. An interpolation is a
  `string`, and its holes follow the hole rule (`Display`).
- **Both expressions are READS.** They borrow, as the argument of `print` does. A
  consuming use inside them follows the ordinary rules.
- **An assert stands in any statement position**: a block, a match arm (`X -> assert(...)`),
  a lambda body, a generic body, an `expand` body. It is not a top-level declaration.
- **A generic body is checked once, where it is written**, as every other statement is
  (`docs/design/checked-generics.md`). A type parameter is opaque there, so a condition
  `a == b` on a `T` needs `T: Eq`, and a hole in the message needs `Display`. The
  monomorphized copy keeps the position of the template.

## The run-time behaviour

1. The program evaluates the condition in a scope of its own, so the temporaries of the
   condition are freed before the branch.
2. On `true`, control goes to the next statement.
3. On `false`, the program evaluates the message (when there is one), prints the line to
   stderr and calls `exit(1)`. Output that the program wrote to `stdout` before the trap
   is already out, because `stdout` is not buffered. A `BufWriter` that is not drained
   loses its window, as with every other runtime error.

The trap path does not free the message or the other values in scope: the process stops.

## The implementation

- **One code, [RE2026](../error-catalog.md#re2026).** The text in the registry is `assertion failed at {where}`.
  `{where}` is `file:line:col`, and the backend formats it at COMPILE time, as [RE2023](../error-catalog.md#re2023)
  formats `{pattern}`.
- **The message is a run-time value.** A Sushi string is `{i8* data, i32 size, i8 owned}`
  and has no NUL terminator, so the backend prints it with `%.*s` from its size and its
  data. This is `emit_runtime_error_with_message` in `RuntimeErrors`
  (`backend/runtime/errors.py`): it prints the compile-time text, then `: ` and the
  message, then the newline. The statement is `emit_assert`
  (`backend/statements/control_flow.py`).
- **The message is a temporary of the fail block.** It is built in a scope of its own,
  and that scope is popped after the trap, when the block is terminated, so no cleanup of
  a value made there reaches the passing path.
- **The file name is stamped on the node.** The AST builder writes
  `Assert.source_label` when it builds the node, so a monomorphized copy and an `expand`
  copy carry it with no extra step. The name is `display_filename`
  (`internals/report.py`), the one function a diagnostic prints its file through. A parse
  that has no reporter (a bundled stdlib module, a source library, a library template)
  passes `source_label` to `parse_to_ast`, the same label its diagnostics use.
- **The label is in the unit's cache key.** It is relative to the working directory of the
  build, so the unit fingerprint (`compiler/fingerprint.py`) hashes it beside the source.
  Without it, a build from another directory with one `--cache-dir` reused an object that
  printed the old path.
