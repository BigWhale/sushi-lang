# SLIR reference

SLIR is the Sushi Low-level IR. It is the input of code generation. This page is the
reference for SLIR as the code builds it. For the reasons behind each choice, read
[the IR design](../design/ir.md), section 8. This page does not repeat that text.

## What SLIR is

SLIR is a control-flow graph of basic blocks. Each function has locals, blocks, statements
and terminators. Every value lives in a place, and every ownership event is a statement:
a `Move` operand, a `Drop`, a `DropIfSet`. The emitter makes no ownership decision. It
writes what SLIR says.

```text
SHIR body + the answers of borrow
        |  SHIR-to-SLIR lowering   (semantics/slir/lower*.py, drops.py)
        v
SlirFunction
        |  SlirVerifier            (semantics/slir/verify*.py)  -> CE0158 on a fault
        v
SlirProgram                        (one SlirFunction for each body)
        |  emit_function           (backend/slir/)
        v
LLVM IR
```

- The stage is `SlirStage` (`semantics/slir/lower.py`). It runs for each unit, after the
  `borrow` pass. The `borrow` pass hands it each body that it finished with no error, with
  the side tables of the body (`BodyTables`: the typecheck, ownership and lift results).
  The stage does not lower a template check, the initializer of a constant or a `var`, or
  any body of a program that has an error.
- The stage verifies each function (and each lifted function) before it keeps it. Then it
  records the function in the `SlirProgram`, keyed by the `DeclId` of the SHIR body.
- The backend reads the `SlirProgram` through `SlirBodies` (`backend/slir/bodies.py`). A
  declaration whose body has no SLIR function is an internal error.
- SLIR holds bodies only. The backend reads declarations (symbols, types of a prototype,
  folded values of a global) from the `DeclTable` (`semantics/decl_table.py`). See
  [The declaration table](#the-declaration-table).

## The declaration table

`declarations()` in `semantics/decl_table.py` builds one frozen `DeclTable` for each
program. `codegen_for` (`compiler/pipeline.py`) calls it at the hand-off to the backend.
The table is the one reader of an AST declaration after the analysis. The backend reads
the table for the declaration round and never imports the AST. The records are:

| Class | Holds |
|---|---|
| `DeclTable` | `units`, `copies`, `shipped_perks`, `shipped_methods` |
| `UnitDecls` | the functions, methods and globals of one unit, with the perk symbols, the stdlib imports and the method names |
| `FunctionDecl` | `name`, `unit`, `symbol`, `is_public`, `params`, `ret`, `err_type`, `written_channel`, `resolved_result`, `body` |
| `MethodDecl` | an extension method, a static or a method of a perk implementation |
| `GlobalDecl` | a constant or a `var`, with its folded value |

The `body` field holds the `DeclId` that keys the `SlirProgram`. The field list of each
class is in `decl_table.py`. The design is in ir.md 6.3.

## One function

All classes are frozen, slotted dataclasses (gate:
`tests/unit/test_ir_nodes_are_slotted.py`). They live in `semantics/slir/function.py` and
`ids.py`.

| Class | Fields | Meaning |
|---|---|---|
| `SlirFunction` | `symbol`, `name`, `unit`, `params`, `locals`, `blocks`, `entry`, `environment`, `lifted` | One function. `symbol` is the monomorphic name in the module. `name` is the source name that the printer writes. `unit` is the declaring unit. `params` is a tuple of `(LocalId, Convention)`. `environment` is the local of the environment of a lambda function. `lifted` holds the functions that lowering cut from the lambdas of the body. |
| `SlirLocal` | `id`, `ty`, `name`, `loc`, `view` | One local. `name` is the source name, or None for a temporary. `view` marks a borrowed view: it never drops and never moves. |
| `SlirBlock` | `id`, `statements`, `terminator` | A basic block: statements, then exactly one terminator. |
| `SlirProgram` | (private table) | The `SlirFunction` of each body. `record(body, fn)` and `outcome_of(body)`. |
| `LocalId` | `index` | `_N` in the dump. |
| `BlockId` | `index` | `bbN` in the dump. |
| `BorrowId` | `index` | `bN` in the dump: one borrow region. |

The rules of the layout:

- `_0` is the return slot (`RETURN_SLOT` in `lower.py`). `_1` to `_n` are the parameters.
  The other locals follow, in the order that lowering makes them. A SLIR local id is not a
  SHIR local id.
- The entry block is `function.entry`. It is the first block.
- Each parameter has a `Convention` (`semantics/param_modes.py`): `OWNED` (`nom`),
  `GUARANTEED` (the default, a borrow by value), `IN_GUARANTEED` (`peek`, by address) and
  `INOUT` (`poke`, by address). The printer writes them as `@owned`, `@guaranteed`,
  `@in_guaranteed` and `@inout`.
- An address local is a local that a `Ref` writes (`address_locals()`). Its storage is the
  place of another value, so it owns nothing.
- The ids of a lifted function are its own.

## Reference tables

The tables below list each class with its fields in dataclass order. Every class is
frozen and slotted. Every statement and every terminator also carries a required `span`.

### Places

`semantics/slir/places.py`. A place is storage. `Place` is the union of the first seven
classes.

| Class | Fields | Meaning |
|---|---|---|
| `Local` | `id` | A local of the function. |
| `Global` | `symbol`, `ty` | A unit-level `var`. It is never moved out of. |
| `Field` | `base`, `index` | A field of a struct or a tuple, or a payload slot. |
| `Capture` | `base`, `index` | A subclass of `Field`. A slot of the environment of a lifted lambda. |
| `Index` | `base`, `index` | An element of an array, a `List@(T)` or a byte of a string. `index` is an operand. |
| `Downcast` | `base`, `variant` | An enum read as one variant. Valid only under that variant. |
| `Deref` | `base` | The value behind a pointer (a `peek` or `poke` reference). |

`root_of(place)` gives the local, the `var` or the capture that holds a place.
`locals_read(place)` gives the locals that a read of the place reads.

### Operands

An operand reads a place or holds a constant. `Operand` is the union of the three classes.

| Class | Fields | Meaning |
|---|---|---|
| `Copy` | `place` | The value is plain, or the source is a borrow. |
| `Move` | `place` | Ownership goes to the reader. The source is dead after it. |
| `Const` | `constant` | A constant. |

The `borrow` pass decides `Copy` and `Move`. Lowering writes that answer and never
classifies a use again.

### Constants

`semantics/slir/constants.py`. `Constant` is the union of these classes. The constant
evaluator makes the value. Lowering carries it through.

| Class | Fields | Meaning |
|---|---|---|
| `Int` | `value`, `ty` | An integer of a given type. |
| `Float` | `value`, `ty` | A float of a given type. |
| `Bool` | `value` | A `bool`. |
| `Blank` | (none) | `~`. |
| `Str` | `value` | A string literal as UTF-8 bytes. It owns no heap. |
| `Aggregate` | `ty`, `parts`, `variant` | A constant array, struct or enum variant. |
| `FnRef` | `symbol` | A named function. |

### Rvalues

`semantics/slir/rvalues.py`. `Rvalue` is the union of these classes.

| Class | Fields | Meaning |
|---|---|---|
| `Use` | `operand` | An operand alone. |
| `Binary` | `op`, `left`, `right` | A binary operator. It never carries `and` or `or`: a short circuit is control flow. |
| `Unary` | `op`, `operand` | A unary operator. |
| `Cast` | `operand`, `target` | A cast. |
| `Aggregate` | `kind`, `parts` | A built value. `kind` is an `AggKind`. `parts` are operands. |
| `Discriminant` | `place` | The tag of the enum in `place`, as an `i32`. |
| `Clone` | `operand` | The one deep copy. |
| `Len` | `place` | The length of an array, a `List@(T)` or a string. |
| `Slice` | `place`, `start`, `end` | A borrowed `T[]` view of `place`. |
| `Ref` | `place`, `mode` | The address of a place (`peek` or `poke`). It writes an address local. |

`operands_of(rvalue)` lists what an rvalue reads, in order.

### Aggregate kinds

`AggKind` is the union of these classes. The kind keeps a closure, an iterator and a
struct apart, because the backend reads the semantic type and never the layout.

| Class | Fields | Meaning |
|---|---|---|
| `AggStruct` | `ty` | A struct. |
| `AggTuple` | `ty` | A tuple. |
| `AggVariant` | `ty`, `variant` | An enum variant. |
| `AggArray` | `ty`, `runs` | A `T[N]` or a `T[]`. The parts are the parts of each run, in order. |
| `AggClosure` | `ty`, `env` | A function value. `env` is the environment type of a lambda, or None for a named function. |
| `AggIterator` | `ty`, `source`, `heap` | An iterator value. `source` is one row of the closed table below. `heap` is the heap decision of `borrow`. |

An array run is one of `RunOne` (one slot: one part), `RunRepeat` (two parts: the value,
then the count) and `RunSequence` (`step`; two parts: the first value, then the count).
`run_width(run)` gives the count of parts.

### Iterator sources

`IterSource` is the union of the producers and the adapters. Each row has a fixed list of
parts.

| Class | Fields | Parts |
|---|---|---|
| `IterElements` | `container`, `lent` | The container (`Copy` when lent, else `Move`). |
| `IterMapView` | `container`, `view`, `lent` | The container, as above. |
| `IterRange` | `step` | The first value and the count. |
| `IterNew` | `value`, `next_symbol`, `receiver` | The move of the value. `receiver` is the convention of `self` in `next()`. |
| `IterMap` | `source`, `function` | The move of the inner iterator, then the move of the function. |
| `IterFilter` | `source`, `function` | The same parts as `IterMap`. |
| `IterEnumerate` | `source` | The move of the inner iterator. |
| `IterZip` | `source`, `other` | The move of the inner iterator, then the move of the right iterator. |
| `IterCloneEach` | `source` | The move of the inner iterator. |

`IterAdapter` is the union of the last five classes.

### Statements

`semantics/slir/statements.py`. `SlirStatement` is the union of these classes. Sushi does
not unwind, so a call needs no unwind edge and `Call` is a statement.

| Class | Fields | Meaning |
|---|---|---|
| `Assign` | `place`, `rvalue`, `span` | Write an rvalue into a place. |
| `Call` | `dest`, `callee`, `args`, `span` | Call. `args` is a tuple of `(Operand, Convention)`. The result goes into `dest`. |
| `Drop` | `place`, `span` | An unconditional drop (RAII). |
| `DropIfSet` | `place`, `flag`, `span` | The drop of a conditionally moved local. `flag` is a `bool` local. |
| `BeginBorrow` | `id`, `place`, `mode`, `view`, `span` | The begin of a borrow region over `place`. `view` is the view local that the region lends to, or None. |
| `EndBorrow` | `id`, `span` | The end of the region `id`. |
| `StorageLive` | `local`, `span` | The storage of the local begins. |
| `StorageDead` | `local`, `span` | The storage of the local ends. |

`operands_of(statement)` and `call_operands(callee, args)` list what a statement reads.

### Callees

| Class | Fields | Meaning |
|---|---|---|
| `Direct` | `symbol`, `name`, `module` | A free function, a method, an extern, a conversion or a function of a registry stdlib module. `module` is the `use` path of the stdlib module, or None. |
| `Builtin` | `method`, `types` | An emitter family. `method` is a `BuiltinMethod` (a row of `METHOD_TYPE_REGISTRY`) or an `Intrinsic` (`println`, `print`, `interp` and so on). `types` holds the type of each operand of an intrinsic. |
| `Indirect` | `operand` | A call through the `fn_ptr` of a closure, or the step of an iterator. |

### Terminators

`semantics/slir/terminators.py`. `SlirTerminator` is the union of these five classes. A
call is not a terminator.

| Class | Fields | Meaning |
|---|---|---|
| `Goto` | `target`, `span` | Jump to a block. |
| `SwitchInt` | `operand`, `targets`, `otherwise`, `span` | Jump by an integer or a `bool`. `targets` is a tuple of `(int, BlockId)`. `otherwise` is None when no other value can reach the switch. |
| `Return` | `span` | Return the value in `_0`. |
| `Trap` | `code`, `args`, `span` | A run-time trap: a registered `RExxxx` code and the values that its text prints. |
| `Unreachable` | `span` | No path reaches this point. |

## Drop placement

`semantics/slir/drops.py` is the one home of the drops. `DropPlacement` keeps the scopes
and the state of each local while lowering walks the body. Lowering reports each event to
it, and it answers with the statements that an exit, a jump or a rebind needs. Gate:
`tests/unit/test_slir_drop_placement_is_one.py` (no other module builds a `Drop`).

The rules, in short. The full text is in [ir.md 8.10](../design/ir.md#810-drop-placement).

1. A local gets `StorageLive` before its first write and `StorageDead` at each exit of its
   scope.
2. A scope exit drops each owning local, newest first, before `StorageDead`. A `return`
   writes `_0` first.
3. The state of a local is live, moved or unknown. The `move` rows of `borrow` are the
   only source of a move. A live local gets `Drop`. A moved local gets nothing.
4. A local whose state differs between the paths of a join gets a `bool` flag. Its drop is
   `DropIfSet`. The lowering runs again until no walk finds a new flag.
5. A borrow never drops. An `OWNED` parameter drops in the callee. A `GUARANTEED`
   parameter starts as not owned.
6. A rebind computes the new value first, then drops the old value, then stores. The same
   order holds for an owning field, an element and a `Global`.
7. The hidden iterator and the item of a loop are owning locals of the loop scope.
8. A view local and an address local never drop.

The path state also holds the open borrow regions. An exit ends the region of each view
local that it leaves, before the drops.

## The verifier

`SlirVerifier` runs on every compile, for each function that lowering finished
(`verify_function` in `verify.py`). A failed rule is the internal error CE0158. The
message has this text:

```text
the SLIR of the function '{function}' breaks the verifier rule '{rule}': {detail}
```

The error is a fault in the compiler, not in the program. The code is registered in
`sushi_lang/internals/errors/internal.py`. The tests are
`tests/unit/test_slir_verifier.py`, `test_slir_verifier_arrays.py`,
`test_slir_verifier_globals.py`, `test_slir_verifier_libraries.py`,
`test_slir_verifier_values.py` and `test_slir_verify_regions.py`.

The order of the checks in `verify_function` is: the structure, the `ptr` quarantine (a
public function only), the capture places, the stack iterators, the regions, and the
dataflow. The names of the rules are the constants of `verify_rules.py`.

| Rule | Module | What it refuses |
|---|---|---|
| typed locals | `verify.py` | A local with no type, an unresolved type, or an id that is not its position. A parameter that is no local of the function. |
| typed places | `verify.py` | A place on a local that the function does not have.<br>A field of a type that has no fields.<br>A field number out of range.<br>An index that is no `i32`.<br>An index of a value that is no array.<br>A `Downcast` to a variant that the enum does not have.<br>A `Deref` of a value that is no pointer. |
| known blocks | `verify.py` | A duplicate block id, an entry that is no block, a jump to a missing block. |
| one terminator per block | `verify.py` | A block with no terminator, or a terminator among the statements. |
| spans | `verify.py` | A statement or a terminator with no span. |
| no move out of a global | `verify.py` | A `Move` of a `Global`. |
| no move of an element | `verify.py` | A `Move` of a place that holds an `Index`. |
| drop of an owning type | `verify.py`, `verify_arrays.py`, `verify_globals.py` | A `Drop` of a type that owns nothing. |
| bool drop flag | `verify.py` | A `DropIfSet` whose flag is not a `bool` local. A place with two flags. |
| a drop of a global before its store | `verify_globals.py` | A `Drop` of a `Global` that no `Assign` into the same place follows. A `DropIfSet` of a `Global`. |
| no short circuit in a binary | `verify.py` | A `Binary` with `and` or `or`. |
| a numeric cast | `verify.py` | A `Cast` between types that are not numeric, and not the same type. |
| a constant in range | `verify.py` | An `Int` outside the range of its type. |
| an integer switch | `verify.py` | A `SwitchInt` on an operand that is no integer and no `bool`, or with a case that the type lacks or repeats. |
| a switch on a tag | `verify.py` | A case that is no tag of the enum that a `Discriminant` read. |
| an enum value | `verify.py` | A variant that the enum does not declare, or a payload part of a wrong count or type. A `Discriminant` of a value that is no enum. |
| a struct value | `verify.py` | A struct value of a type that is no struct, or with a wrong field count or type. |
| an array value | `verify.py`, `verify_arrays.py` | An array value of a type that is no array.<br>Parts that do not fit the runs.<br>Parts that do not fit the element type.<br>A repeat run that moves its part.<br>A sequence run whose element is no `i32`.<br>A run count that is no constant. |
| a length and a slice | `verify_arrays.py` | A `Len` of a type that has no length. A `Slice` of a non-array, or with a bound that is no `i32`. |
| a view | `verify.py`, `verify_arrays.py` | A view local that is no `T[]`.<br>A view local that a statement other than a `Slice` writes.<br>A move or a drop of a view local.<br>A copy of a view into a local that owns.<br>A drop of a part of a view. |
| a peek return | `verify.py` | A `Copy` of an owning value into `_0` when `_0` is no view local. |
| registered trap | `verify.py` | A `Trap` with an unknown code, or with operands that do not fit the conversions of its text. |
| declared conventions | `verify_calls.py`, `verify_values.py` | A `Direct` call of a callee that has no declaration.<br>A `Direct` call with a wrong argument count.<br>An argument convention that is not the convention of the declared parameter.<br>The same rules for a static and for a built-in method.<br>An `Indirect` call of an operand that is no function type.<br>An `Indirect` call whose count or conventions are not those of its function type. |
| a receiver operand | `verify_calls.py` | A built-in method call with no receiver operand. |
| no copy of an owning value into an owned argument | `verify_calls.py` | A `Copy` of an owning value into an `OWNED` argument. A `string` and a function value are the carve-outs. |
| capture places | `verify_values.py` | A closure value of a wrong shape, a write of the environment, a move out of a capture. |
| an iterator value | `verify_values.py` | Parts that do not fit the row of the iterator. |
| a stack-state iterator never leaves | `verify_values.py` | A stack-state iterator that reaches `_0` or an owned argument. |
| no use after a move | `verify_flow.py` | A read or a move of a local that a path moved or dropped. A drop after a move. |
| no use of a local outside its storage | `verify_flow.py` | A read or a move of a local that holds no value on a path. |
| a written return slot | `verify_flow.py` | A `return` on a path that did not write `_0`. |
| one drop or move on every path | `verify_flow.py` | An owning local must be dropped or moved one time on each path. The verifier refuses:<br>A drop two times.<br>A drop of a local that holds no value.<br>A write or a `StorageDead` while the local holds a value.<br>A `return` while the local holds a value.<br>An `inout` parameter that holds no value at a `return`.<br>A move of a parameter that the function does not own.<br>A drop of a borrowed parameter.<br>A drop flag that does not agree with the state of its local.<br>A take that leaves an owning part with no `Drop`. |
| a view local | `verify_regions.py` | A view local that is not written by one statement on each path, that drops, moves, or is an owned argument. |
| an address local | `verify_regions.py` | A `Ref` that does not come before each use, or an address local that moves or drops as a whole. |
| a borrow region | `verify_regions.py` | A region that does not begin and end one time on each path, or that is open at a `return`. |
| no change of an owner in its region | `verify_regions.py` | A write, a move, a drop or an `INOUT` argument of an owner while its region is open. |
| no ptr at a public signature | `verify_quarantine.py` | A `ptr`, or a type that holds one, in a public signature. |

### Stated gaps

The verifier refuses a shape that no lowering makes yet, and that it does not check, with
the internal error CE0000:

- A `Drop` of a place that is no local, no field, no element and no `Global`.
- A `DropIfSet` of a place that is no local and no `Global`.

The verifier does not check the element type of a `List@(T)` index place. It holds no
table that resolves the argument of the list. The follow-up is issue #1278.

### Where to add a rule

`verify.py` is at its limit of 600 lines. Never add a rule to it. Put a rule that checks a
value or a place in `verify_values.py`, a rule that checks a call in `verify_calls.py`,
and a rule that checks a region in `verify_regions.py`. Add the name of the rule to
`verify_rules.py`. Add a test to the test file of the module: `test_slir_verifier_values.py`,
`test_slir_verifier.py` (for `verify_calls.py`) or `test_slir_verify_regions.py`.

## The emitter

`backend/slir/` turns one `SlirFunction` into one LLVM function. It follows the backend
rule: free functions that take a state first.

| Module | Holds |
|---|---|
| `bodies.py` | `SlirBodies`: the hand-off of the SLIR program to the backend. |
| `state.py` | `FunctionState`: the codegen, the LLVM function and builder, the storage of each local, the address slots, the LLVM block of each SLIR block, and the current span. `unhandled()` and `fault()` raise CE0000. |
| `emit.py` | `emit_function`, the statements, the terminators and `CALL_EMITTERS`. |
| `values.py` | The places, the operands, the constants and the rvalues. |
| `methods.py` | `METHOD_FAMILIES` and `emit_method`: one row for each family of built-in methods. |
| `intrinsics.py` | `INTRINSICS`: one row for each intrinsic, by name. |
| `iterators.py`, `closures.py`, `externs.py`, `foreign.py` | The iterator value, the closure value, a C function and a foreign pointer. |

How a function is emitted:

- `emit_function(codegen, function, filename=...)` is the stage entry. It finds the
  declaration that the declaration round made for `function.symbol`, and defines it.
- Each local is a slot in the entry block (through `entry_alloca`). A `peek` or `poke`
  parameter has no slot: its local is the incoming address. An address local has a slot
  that holds a pointer.
- Each SLIR block is one LLVM block, in order. `StorageLive`, `StorageDead`, `BeginBorrow`
  and `EndBorrow` emit nothing. A `Drop` goes through the one destroy seam of the backend.
- `CALL_EMITTERS` has one row for each variant of `SlirCallee`: `Direct`, `Builtin` and
  `Indirect`. Gate: `tests/unit/test_slir_call_dispatch_is_a_table.py`.
- A `Builtin` call goes to `INTRINSICS` (by the name of an intrinsic) or to
  `METHOD_FAMILIES` (by the family of a method).
- A shape that the emitter does not handle is the internal error CE0000, at the span of
  the statement.

No backend module imports the AST. The backend reads SLIR for bodies and the `DeclTable`
for declarations. Gate: `tests/unit/test_backend_imports_no_ast.py`. See
[the backend](backend.md) for the rest of code generation.

## The dump

`--dump-slir PATH` writes the SLIR of each body to `PATH`. `-` prints it on the terminal.
The compile goes on as usual. The text is the same on every run. The units and the
functions are in the order of the SHIR dump. A template check, the initializer of a
constant or a `var`, and a body with an error do not print. See the
[compiler reference](../compiler-reference.md#dump-slir). The printer is
`semantics/slir/printer.py`, and its module docstring is the full grammar.

Take this program:

```sushi
fn show(nom string s) ~:
    println(s)

fn main() i32:
    let string name = "Harmless"
    let string msg = "Mostly {name}"
    let i32 n = 2
    if (n > 1):
        show(nom msg)
    return 0
```

Run `./sushic prog.sushi --dump-slir -`. The dump is:

```text
unit prog

fn show(_1: string @owned) -> ~ {
    let _0: ~
    let _2: ~

  bb0:
    StorageLive(_2)
    _2 = call builtin println@(string)(copy _1 @guaranteed)
    StorageDead(_2)
    _0 = const ~
    Drop(_1)
    return
}

fn main() -> i32 {
    let _0: i32
    let _1: string                         // name
    let _2: string                         // msg
    let _3: bool
    let _4: i32                            // n
    let _5: bool
    let _6: ~

  bb0:
    StorageLive(_1)
    _1 = const "Harmless"
    StorageLive(_2)
    _2 = call builtin interp@(string, string)(const "Mostly " @guaranteed, copy _1 @guaranteed)
    StorageLive(_3)
    _3 = const true
    StorageLive(_4)
    _4 = const 2_i32
    StorageLive(_5)
    _5 = copy _4 > const 1_i32
    switchInt(copy _5) -> [0: bb2, otherwise: bb1]

  bb1:
    StorageDead(_5)
    StorageLive(_6)
    _6 = call @show(move _2 @owned)
    _3 = const false
    StorageDead(_6)
    goto -> bb3

  bb2:
    StorageDead(_5)
    goto -> bb3

  bb3:
    _0 = const 0_i32
    StorageDead(_4)
    DropIfSet(_2, _3)
    StorageDead(_3)
    StorageDead(_2)
    Drop(_1)
    StorageDead(_1)
    return
}
```

How to read it:

- The header gives each parameter with its type and its convention. `_1: string @owned`
  is a `nom` parameter. The result type follows `->`.
- `let` lines declare the locals. `_0` is the return slot. A `//` comment gives the source
  name of a local. A local with no comment is a temporary.
- `show` owns `_1`. It reads `_1` with `copy` for `println`, then it writes the return
  slot, then it drops `_1`. The drop comes after the value of `_0`.
- `_1 = const "Harmless"` stores a string literal. A literal owns no heap, so the local
  `name` owns nothing at run time, but its type owns, so it has a `Drop`.
- `_2` holds the result of the interpolation, so it owns heap memory.
- `_3` is a drop flag for `_2`. It is `true` after the declaration. The call
  `show(move _2 @owned)` is a `Move`: `_2` is dead on that path, so the flag is set to
  `false` right after.
- `switchInt(copy _5) -> [0: bb2, otherwise: bb1]` is the `if`. Value 0 (false) goes to
  `bb2`. Any other value goes to `bb1`.
- `bb3` is the join. The state of `_2` differs between the two paths, so its drop is
  `DropIfSet(_2, _3)`. `_1` is live on both paths, so its drop is a plain `Drop`.
- At the exit, the drops run newest first. Each drop comes before the `StorageDead` of
  its local.

## Golden dumps

A fixture can compare its SLIR dump with a golden file.

```text
# EXPECT_DUMP_SLIR: main.slir
```

- The value is the name of a file beside the fixture. It ends in `.slir`.
- The runner adds `--dump-slir` itself. A fixture cannot pass the flag in
  `COMPILER_FLAGS`.
- The comparison is exact, byte for byte. A missing golden file fails the fixture.
- To write or refresh the file, run
  `python tests/run_tests.py --bless --filter ir/slir/<area>/`. Then read the new file in
  the diff as you read code.
- The dump must not change with `PYTHONHASHSEED`. The runner step `golden_seed_gate`
  compiles each golden fixture again under another seed.
- The golden fixtures are in `tests/ir/slir/<area>/`. For example,
  `tests/ir/slir/drop/test_slir_drop_impl.sushi` and its `.slir` file.

The full text of the directive is in `tests/TEST_METADATA_GUIDE.md`.

## Add a SLIR construct

Follow this list. Write the failing fixture first.

1. **Fixture.** Add a `test_*.sushi` file under `tests/ir/slir/<area>/` with
   `EXPECT_DUMP_SLIR`. Show it fail. Do not change an existing fixture.
2. **Node.** Add the dataclass to `places.py`, `rvalues.py`, `statements.py`,
   `terminators.py` or `constants.py`. It must be frozen and slotted. A statement or a
   terminator takes a required `span`. Add the class to the union alias. If it is an
   rvalue, add it to `operands_of`.
3. **Lowering.** Write the lowering in the `lower_*.py` module of its area. Report each
   move, declaration and rebind to `DropPlacement`. Never build a `Drop` outside
   `drops.py`.
4. **Printer.** Add a row to `PRINTERS` in `printer.py`. Update the grammar in the module
   docstring.
5. **Verifier.** Add the rule name to `verify_rules.py`. Add the check to
   `verify_values.py`, `verify_calls.py` or `verify_regions.py`. Do not add to
   `verify.py`. Add a test to the test file of that module (see "Where to add a rule").
6. **Emitter.** Add the row: a new callee kind goes into `CALL_EMITTERS`, a new intrinsic
   into `INTRINSICS`, a new method family into `METHOD_FAMILIES`. Other classes get an arm
   in `emit.py` or `values.py`. The emitter must not import the AST.
7. **Gates.** Run `python -m pytest -q`. The gates for the slots, the call table, the
   drop placement and the AST imports must pass.
8. **Bless.** Run `python tests/run_tests.py --bless --filter ir/slir/<area>/`. Read the
   golden file. Then run the same filter without `--bless`.
