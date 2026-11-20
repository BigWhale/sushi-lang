# `!`: the never-return marker

Status: BUILT (#1325). The ruling is David's (2026-11-20), with part 2 of the same day
(section 3, items 10 to 12).

## Summary

- `!` in the return position of a `fn` or of an `unsafe external` binding says that a call
  of the function never returns: `fn exit(i32 code) ! = "exit"`, `public fn fail(string msg) !:`.
- `!` is a return marker. It is not a type. It cannot be the type of a variable, a
  parameter, a field, a type argument or an element, and it has no error channel.
- A `!` body must not reach its end, and must not hold a `return`.
- A call of a `!` function is legal only as a statement. It ends the path: no CE0107 after
  it, CE0140 on a statement after it, no drop after it, and `Unreachable` after it in SLIR.
- `exit` of `<sys/process>` is Sushi source. The compiler has no list of the functions that
  never return: the declared return is the one home of the rule.

## 1. The problem

Before this change, Sushi had no way to say that a function never returns. One function
needed it: `exit` of `<sys/process>`.

- The rule was a registry fact, `_NEVER_RETURNS` in `semantics/stdlib_registry.py`, and it
  had one reader: SLIR lowering put `Unreachable` after the call.
- The path check did not know the rule, and the `borrow` pass did not know it either.

So a function that ended with `exit(...)` got CE0107:

```
use <sys/process>

fn stop() i32:
    exit(3)

fn main() i32:
    return stop()
```

The author had to write a `return` after `exit` that never ran. `exit` could not move to
Sushi source, because the fact was keyed on its registry row. A user function that only
calls `exit` (a `fail(msg)` helper) could not carry the rule at all.

## 2. Precedent

| Language | Spelling | Kind |
|---|---|---|
| Rust | `fn exit(code: i32) -> !` | `!` is the never type. On stable Rust it is a return type only; the full type (`!` in any position) is still unstable. |
| Zig | `fn exit(status: u8) noreturn` | `noreturn` is a type. A call of it ends the path, and an expression of it fits any type. |
| Swift | `func fatalError(...) -> Never` | `Never` is an enum with no case. A `Never` function ends the path for the definite-return check. |
| Kotlin | `fun fail(msg: String): Nothing` | `Nothing` is the bottom type: it is a subtype of every type. |
| C | `_Noreturn void exit(int)`, C23 `[[noreturn]]` | An attribute of the function, not a type. The return type stays `void`. |
| Go | none | `os.Exit` is an ordinary function. The compiler has a list of the calls that end a path for the "missing return" check (`panic`, and nothing else); after `os.Exit` the author writes a `panic` or a `return`. |

Two models exist. Rust, Zig, Swift and Kotlin give the fact a TYPE, and the type checker
reads it. C gives it an attribute of the function. Go has neither, and the compiler keeps a
hard-coded list. The old `_NEVER_RETURNS` was the Go model.

## 3. The ruling (2026-11-20)

1. The spelling is `!`, in the return position of a `fn` and of an `unsafe external`
   binding. `!` stays the NOT operator in an expression. The grammar keeps the two apart, as
   it does for `~` (the unit type and the bitwise NOT).
2. `!` is a return marker only, not a type. It cannot be the type of a variable, a
   parameter, a field, a type argument or an element. A later change can make it a bottom
   type without a break.
3. No channel: `! | E` is refused. An error return is a return.
4. No `!` lambda, and no `!` function type (`fn() -> !`).
5. A `!` function must not reach the end of its body, and must not contain a `return`.
6. A call of a `!` function ends the path: CE0107 does not fire after it, and CE0140 fires
   on a statement after it. SLIR puts `Unreachable` after the call. The `borrow` pass and
   the drop placement treat the path as ended, so no drop runs after the call.
7. `main` cannot be `!`: `main` returns a bare integer.
8. One home for the rule: the declared return type. `_NEVER_RETURNS` and
   `stdlib_never_returns` are deleted.
9. `exit` moves to `src_sushi/sys/process.sushi`, and its Python generator row goes.

Part 2 of the ruling (2026-11-20):

10. A method, a static, a perk method and a conversion refuse `!` (CE0161).
11. A `!` function used as a value has its own code (CE0163).
12. A call of a `!` function is legal only as a statement. Every other position has its own
    code (CE0164), so no `!` call stands inside an expression, and SLIR lowering has one
    path for the call: the statement.
13. CW3002 for an own `exit` beside a flat `use <sys/process>` is a new warning, not a
    breaking change.

## 4. The rules

- **R1. The positions.** `!` is legal as the WHOLE return of a free function (`fn f() !:`,
  `public fn f() !:`, a generic function) and of an extern (`fn exit(i32 code) ! = "exit"`).
  Every other position is CE0161: a parameter, a `let`, a field, a variant payload, a type
  argument, an array element, a cast, the return of a function type, the return of a
  lambda, and the return of an extension method, a static, a perk contract method, a perk
  implementation method and a conversion. A method is not in the ruling, so it takes no `!`.
- **R2. No channel.** `fn f() ! | E` is CE0162. The declaration keeps the `!` and drops
  the channel.
- **R3. The body.** A `!` body that can reach its end is CE0159. A `return` in a `!` body
  is CE0160, with or without a value. A `return` in a lambda inside a `!` body belongs to
  the lambda, and is legal.
- **R4. What ends the path.** A statement that is a call of a `!` function ends the path,
  as a `return` does. The call can be a free function, a generic function, a function
  behind a `use ... as` alias, a function of a library, or an extern. So an `if` whose every
  arm ends in a `!` call, with an `else`, ends the path, and so does an exhaustive `match`.
- **R5. Only a statement.** A call of a `!` function is legal only as a statement of its
  own. Every other position is CE0164: an argument (also of another `!` call), a `let`
  initializer, an operand, a `return` value, the value of a `match` arm, a hole of an
  interpolation and the body of an expression lambda. No value of the type `!` exists.
- **R6. No function value.** A `!` function is not a function value (CE0163), because its
  value would need the type `fn(...) -> !`.
- **R7. `main`.** `fn main() !` is CE0106: `main` returns a bare integer.
- **R8. Drops.** No drop runs after a `!` call. The locals of the function stay live when
  the process ends. `exit` gives the memory back to the system. This is the rule of C and of
  Rust: `std::process::exit` runs no destructor.

## 5. Why a marker and not a bottom type

A bottom type is a type that fits every type. With it, `let i32 x = fail("x")` and
`return if_bad ?? fail("x")` are legal, and the type checker gives the type `!` to an
expression that never gives a value: a `return`, a `break`, a call of `exit`.

The smallest version that solves the problem is a marker:

- The problem is a fact about a CALL: the path ends after it. A marker on the declaration
  states that fact, and the path check reads it from the callee. No expression gets a new
  type.
- A bottom type changes the type checker in each position that compares two types: an
  argument, a `let`, a `return`, an arm of a `match` expression, a branch of a ternary
  form, the type arguments of a generic call. Each comparison needs a rule for `!`, and
  generic inference needs a rule for a type argument that is `!`.
- Sushi has no `match` or `if` expression today, so the main use of a bottom type (an arm
  that ends the path in a value position) has no place yet.

## 6. What a later bottom type would add

A later change can make `!` a type without a break, because every program that is legal
today stays legal:

- `!` becomes legal in the positions of R1, first in a function type (`fn(string) -> !`), so
  a `!` function is a function value (CE0163 goes) and a lambda can be `!`.
- An expression of the type `!` fits every type. A call of a `!` function in a value
  position (`let i32 x = fail("x")`) is legal (CE0164 goes), and the path ends at that expression.
- The path check reads the type of each expression and not only the statement-level call.

The marker of today is the declaration form of the same fact, so the record stays valid:
the declared return stays the one home.

## 7. Where the rule lives

| What | Where |
|---|---|
| The grammar: `BANG` in a type position (`never_t`), and `BANG` or `not` for the NOT operator | `grammar.lark` |
| R1, R2: the one reader that accepts `!`, at the top of the return of a function or an extern; every other type position refuses it | `read_return_type`, `ast_builder/declarations/signatures.py`; `TypeParser.parse_type`, `ast_builder/types/parser.py` |
| The marker in the declaration: `BuiltinType.NEVER` in the return slot of the AST, the signature records, the manifest and the cache key | `semantics/typesys.py` |
| Whether a callable never returns, and the `~` that its return slot holds | `never_returns` / `returned_value`, `semantics/never_return.py` |
| The callee row of a `!` call | `ResolvedCall.never_returns`, `semantics/shir/typeck.py`, written by `user_callee` and `check_extern_call` |
| R5: a `!` call only as a statement (CE0164) | `check_call`, `passes/types/visit/calls.py`, over `TypeValidator.statement_calls`, which `check_expr_stmt` fills |
| R3, R4: the path predicate, which the `typecheck` pass, the `borrow` pass and SLIR lowering read | `semantics/shir/paths.py` (the `calls` argument); CE0159 in `passes/types/control_flow.py`; CE0160 in `passes/types/visit/statements.py` |
| `Unreachable` after the call, and no drop after it: the statement is the one path, and it asks the path predicate | `Lowering.expr_stmt`, `semantics/slir/lower.py` |
| R6 | `_value`, `passes/types/visit/function_values.py` |
| R7 | `_check_entrypoint`, `semantics/semantic_analyzer.py` |

The `effects` pass (the destroy summary of the `poke` parameters) needs no change. It is a
flow-insensitive union over the statements of a body, so a path that ends gives it nothing
new, and CE0140 refuses a statement after a `!` call.

## 8. Diagnostics

| Code | Fault |
|---|---|
| CE0159 | A `!` body can reach its end |
| CE0160 | A `return` in a `!` body |
| CE0161 | `!` in a position that is not the return of a function or an extern |
| CE0162 | `! \| E`: a channel on a `!` return |
| CE0106 | `fn main() !` (the entry point rule) |
| CE0163 | A `!` function as a value |
| CE0164 | A `!` call in a position that is not a statement |
| CE0140 | A statement after a `!` call |
