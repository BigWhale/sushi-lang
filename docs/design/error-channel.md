# The error channel is opt-in

Status: SHIPPED. This is the decision record. The rulings are David's (2026-09-30) and
are settled.

## The rule

A callable has an error channel only when its signature writes `| E`, or when it returns
an explicit `Result@(T, E)`. The rule is the same for a free function, an extension or
perk method, a lambda and a function type. There is no default error type.

```sushi
use <collections/strings>

fn parse(string text) i32 | StdError:    # a channel: the call yields Result@(i32, StdError)
    if (text.is_empty()):
        return Result.Err(StdError.Error)
    return Result.Ok(text.len())

fn double(i32 x) i32:                    # bare: the call yields i32
    return x * 2
```

- A body with a channel spells both constructors: `return Result.Ok(x)` and
  `return Result.Err(e)`. A bare `return x` there is [CE2030](../error-catalog.md#ce2030).
- A BARE body returns the value. `return Result.Ok(...)` and `return Result.Err(...)`
  are [CE2091](../error-catalog.md#ce2091), and a `??` is [CE0131](../error-catalog.md#ce0131). A bare function that calls a fallible one handles
  the error itself (`match`, `.realise(default)`), or its signature writes `| E`.
- The call of a bare function yields the value. `??` on it is [CE2507](../error-catalog.md#ce2507), and `.realise` is
  [CE2008](../error-catalog.md#ce2008), because the value is not a `Result`.
- A body that answers a value, or a Result, ends in a `return` on every path ([CE0107](../error-catalog.md#ce0107)). A
  bare `~` body answers nothing and may reach its end.
- `main` is bare. It returns the exit code (`return 0`), and `| E` on it is [CE0106](../error-catalog.md#ce0106). A `??`
  in `main` is [CE0131](../error-catalog.md#ce0131).
- A lambda has a channel only when its TYPE says `| E`. The type comes from the
  annotation on the lambda (`|i32 x| -> i32 | E: ...`) or from the expected type (a
  parameter, a `let`, a field). The compiler never infers a channel from the body. An
  expression body is `return e` when the lambda is bare, and `return Result.Ok(e)` when
  it has a channel. A `??` in a lambda with a channel is legal inside any body, a bare
  one included (#399).
- A function type without `| E` is bare: `fn(i32) -> i32`. With it, `fn(i32) -> i32 | E`.
  The channel is part of the type, so the two do not convert ([CE2002](../error-catalog.md#ce2002)).
- A function with a channel that returns a FUNCTION TYPE writes the explicit form,
  `fn make() Result@(fn(i32) -> i32, StdError):`. A `| E` written after a function type
  belongs to that function type, so `fn make() fn(i32) -> i32 | StdError:` is a bare
  function that returns a function type with a channel.
- The combinators of `<collections/iter>` (`map`, `filter`, `fold`, `compose`) are bare:
  they take bare functions and yield the value.
- A perk contract method is bare or has a channel. [CE0133](../error-catalog.md#ce0133) checks that an implementation
  agrees.

## A bare function is the exception

Use a bare function seldom, and only when it is needed: the function is total over its
inputs and will stay so (a checksum, a pure arithmetic or string helper, a path join),
and a channel would only force a dead `.realise` or `??` on every caller.

Write a channel for everything else: a function that does I/O, parses, allocates on a
size it is given, or can gain a failure later. The reason for a public function is the
ABI: a channel added later changes the signature, and that breaks every caller and every
binary `.slib`. A public function keeps a channel when there is any doubt. The compiler
does not enforce this rule; there is no lint.

## Traps are not errors

A runtime error ([RE2020](../error-catalog.md#re2020) for an index out of bounds, [RE2021](../error-catalog.md#re2021) for a failed allocation, the
other `RExxxx` codes) is a defect, not an error. It stops the program. A bare function
can trap exactly as a function with a channel can, and a channel does not catch a trap.

## Why

One rule for every callable gives one field one meaning: `err_type` of None is "bare" on
a function and on a method alike. A default error type makes every caller of a total
function write a dead `??` or `.realise(0)`, and it gives `main` an error channel that
exits 1 and prints nothing.

The precedent is the same in the languages Sushi takes its error model from: a Rust `fn`
returns `T` unless it writes `Result<T, E>`; a Swift function throws only when it writes
`throws`; a Zig function fails only when its return type is an error union `E!T`; an Elm
function returns `Result` only when its type says so.

## Rejected options

- **A marker for the bare form, with a default error type** (`fn f() u32 | never`). It
  keeps the default that forces the dead unwrap, and adds a second spelling.
- **An uninhabited error type** (Swift `throws(Never)`, Rust `Infallible`). A Result that
  cannot fail still has the Result ABI and still needs an unwrap at every call.
- **Inferring "cannot fail" from the body.** An edit to the body would then change a
  public signature, and the ABI of a binary `.slib`, in silence.

## The one predicate

`has_channel` (`sushi_lang/semantics/channel.py`) is the one question, and every reader
asks it: the collect pass ([CE0131](../error-catalog.md#ce0131), [CE2085](../error-catalog.md#ce2085)), the typecheck pass (the body state, the
return rule, the `??` channel, [CE0107](../error-catalog.md#ce0107), what a call yields), the lift pass (the desugar of
an expression lambda), and the backend (`channel_result_of`, which declares a callable
with its channel Result, its bare return, or void). The `.slib` manifest states
`has_channel` for every function, helper and method record. The templates schema is 8
and the container version is 5. A library with an older schema or container version is
refused ([CE3512](../error-catalog.md#ce3512), [CE3509](../error-catalog.md#ce3509)) and never read as bare.
