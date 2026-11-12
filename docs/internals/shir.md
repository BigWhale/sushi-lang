# SHIR reference

SHIR is the Sushi High-level IR. This page is the reference for SHIR as the code builds it.
It tells you what each part is and where it lives. The reasons for the design and its
rules are in [the IR design](../design/ir.md). Read that document
for the "why". This page does not repeat it.

All paths on this page are relative to the repository root. The SHIR package is
`sushi_lang/semantics/shir/`.

## What SHIR is

SHIR is the form of a function body that the per-body passes read. The AST builder makes
the AST. After the `scope` pass, lowering turns each body into a `ShirBody`. Three passes
then walk that body: `typecheck`, `lift` and `borrow`. After `borrow`, the SLIR stage
lowers the body to SLIR (see [the SLIR reference](slir.md)).

```text
AST  --scope-->  Bindings  --lowering-->  ShirBody  --typecheck--> TypeckResults
                                                    --lift------->  LiftResults
                                                    --borrow----->  OwnershipResults
                                                    --SLIR lowering--> SLIR
```

Three rules shape SHIR:

- A SHIR node holds what the source says. It holds no computed fact. A pass writes each
  answer into its own side table ([the IR design](../design/ir.md), 7.3). So a node has no type field.
- Every class is slotted (gate: `tests/unit/test_ir_nodes_are_slotted.py`).
- Ids come from counters and are given in the order of the walk. The same program gives
  the same ids on every run (gate: `tests/unit/test_shir_ids_are_deterministic.py`).

The AST still does the work that comes before SHIR and around it:

- The AST holds the declarations: functions, structs, enums, perks, constants and `var`
  storage. The passes before `scope` (`collect`, `resolve`, `monomorphize` and the others)
  read the AST and the symbol tables.
- The `scope` pass reads the AST of a body and writes a `Bindings` table. It is the one
  pass before SHIR that is per body.
- The declaration half of `typecheck` (signatures, declared types) still reads the AST.
  The body half reads SHIR.

`semantic_analyzer.py` runs the sequence for one unit: `scope`, then `lower_unit`, then
`typecheck`. `typecheck` gives each finished body to `lift`, and `lift` gives it to
`borrow`. See [the semantic passes](semantic-passes.md) for the pass order.

## A body

`body.py` defines `ShirBody`, the unit of SHIR.

| Field | Type | Meaning |
|---|---|---|
| `fn_id` | `DeclId` | The declaration that owns the body. |
| `origin` | `BodyOrigin` | Why the body exists: `Instance`, `TemplateCheck(template)` or `Initializer`. |
| `params` | `list[ShirParam]` | The parameters, in order. |
| `locals` | `list[ShirLocal]` | Every local of the body, parameters included, in id order. |
| `block` | `ShirBlock` | The statements of the body. |
| `copies` | `ExpandCopies` | The copy boundaries of an unrolled `expand`. |

The parts of a body:

| Class | Fields | Meaning |
|---|---|---|
| `Instance` | none | The body of a function or a method. It can be a monomorphized copy. |
| `TemplateCheck` | `template` | The one check of a template at opaque type arguments. Only this body can hold an `Expand`. |
| `Initializer` | none | The one-expression body of a `const` or a `var` initializer. It is one `Return`. |
| `ShirParam` | `local`, `ty`, `convention`, `loc`, `pack` | A parameter. `convention` is the `Convention` of the declared mode. `pack` is true for a value pack parameter (`...Ts args`). |
| `ShirLocal` | `id`, `name`, `declared`, `loc`, `declared_loc` | A local. `name` is `None` for a hidden local. `declared` is the type as written, if any. |
| `CopyRange` | `start`, `stop` | The statements `start` to `stop` (not included) of a block: one copy of an `expand`. |
| `ExpandCopies` | by block id | The copy boundaries of each block. Lowering is the one writer (gate: `tests/unit/test_shir_expand_copies_have_one_writer.py`). |
| `ShirBlock` | `shir_id`, `loc`, `statements` | A list of statements in the written order. It is in `nodes.py`. |

`Convention` is in `sushi_lang/semantics/param_modes.py`. Its four values are `OWNED`
(`nom`), `GUARANTEED` (no marker), `IN_GUARANTEED` (`peek`) and `INOUT` (`poke`).

### Ids

`ids.py` defines three id classes. Each one is a frozen, slotted dataclass with one
`index` field.

| Class | Names |
|---|---|
| `ShirId` | A SHIR node. It is unique in one body. |
| `LocalId` | A local of one body: a parameter, a binder, or a hidden local. |
| `DeclId` | A declaration of the program: a function, a constant, a `var` or a type. |

`IdAllocator` gives the ids of one kind: 0, 1, 2 and up, in the order of the calls. An id
never comes from `id()` or from the order of a hash.

`decls.py` defines `DeclNames`, the table that gives each `DeclId` and keeps its written
name. `declare(name)` gives a new `DeclId` to a body. `refer(name)` gives one id for each
written name that a body refers to (a constant, a `var`, a function, a type), so two
references to one name share an id. A reference id is not the id of the body that it
names. `name_of` gives the written name back, for the printer and for the SLIR lowering.

The declaration table of ir.md 6.3 is a different record. `declarations(units, tables,
shir, *, copies, shipped_methods, shipped_perks)` in `sushi_lang/semantics/decl_table.py`
makes one frozen `DeclTable` after the analysis, from the AST and the symbol tables.
`codegen_for` (`sushi_lang/compiler/pipeline.py`) calls it, and the declaration round of
the backend reads it. The entry of a function or a method (`FunctionDecl`, `MethodDecl`)
keeps in `body` the `DeclId` that `declare` gave to its SHIR body, and the backend finds
the `SlirFunction` of the body by that id. The table keeps no reference id.

### Bindings

`bindings.py` defines `Bindings`, the table of one body that the `scope` pass writes. The
`scope` pass is the one writer (gate: `tests/unit/test_bindings_have_one_writer.py`).
Lowering reads it. The table answers three questions:

- `resolution_of(node)`: what a name occurrence denotes.
- `local_of(owner, name)`: the `LocalId` of the binder `name` that `owner` declares.
- `captures_of(lam)`: the names that a lambda reads from an enclosing scope, in the order
  of their first use.

The table keys on the AST node, never on its span. A hidden binder has no span.

A resolution is one of these classes:

| Class | Fields | Meaning |
|---|---|---|
| `Local` | `local` | A binder of this body. |
| `Const` | `name` | A `const`: of this unit, of an imported unit, or of a stdlib module. |
| `Storage` | `name` | A unit-level `var`. |
| `Function` | `name` | A function: a function value, or the callee of a call. |
| `Namespace` | `name` | A `use ... as` alias, or the namespace of an `unsafe external` block. |
| `TypeName` | `name` | A type name used as the receiver of a variant or a static. |
| `Unresolved` | `name` | No declaration has this name here. The `scope` pass gives CE1001 or CE1002. |

`Capture` (`name`, `local`) is one captured name of a lambda.

## Node reference

The tables below list every node class, with its fields as the dataclass declares them.
The classes are data only.

Every node class has the fields `shir_id` and `loc` from `ShirNode` (`nodes.py`). The
tables omit those two fields. A "part" is a class that a node holds. A part is not a node:
it has no `shir_id`.

The three unions that dispatch reads are `ShirExpr` (`exprs.py`), `ShirStmt` (`stmts.py`)
and `ShirPattern` (`patterns.py`). A gate checks that each node is in its union (gate:
`tests/unit/test_shir_node_unions_are_total.py`).

### Expressions (`exprs.py`)

| Class | Fields | Meaning |
|---|---|---|
| `Lit` | `value` | A literal. `value` is a `Constant`. |
| `LocalRef` | `local` | A read of a local. |
| `ConstRef` | `const_id` | A `const`. Its value is computed on the AST. |
| `StorageRef` | `decl` | A unit-level `var`: a place with an address. |
| `GlobalRef` | `decl` | A function used as a value. |
| `Field` | `base`, `name`, `member_loc` | A struct field, or a tuple element (`"0"`). The index is an answer of `typecheck`. |
| `Index` | `base`, `index`, `unchecked` | `a[i]`. `unchecked` is true under a `dont_panic` header. |
| `Slice` | `base`, `start`, `end`, `inclusive` | `data[a..b]`: a borrowed view. |
| `Call` | `target`, `args` | A call. `target` is what the source writes (a `CallTarget`). The resolved `Callee` is in `TypeckResults`. |
| `Unary` | `op`, `operand` | A unary operator. |
| `Binary` | `op`, `lhs`, `rhs` | A binary operator. |
| `Cast` | `operand`, `target` | A numeric cast or a conversion (`as`). |
| `Borrow` | `place`, `mode` | A `peek` or `poke` of a place. |
| `Take` | `place` | A marked field take, `nom s.out`, in a `let` or a `return`. |
| `Try` | `inner` | `??`, kept whole. |
| `Lambda` | `params`, `ret`, `err`, `body`, `expr_body`, `captures` | A lambda. The node stays in the tree through `borrow`. |
| `StructInit` | `decl`, `fields`, `written_order`, `names`, `named`, `decl_loc` | A struct construction. `fields` are in declaration order. `written_order` is the order of evaluation. |
| `EnumInit` | `decl`, `variant`, `payload`, `decl_loc` | An enum variant construction. |
| `TupleInit` | `parts` | A tuple literal. |
| `ArrayInit` | `kind`, `runs` | An array literal, `from([...])` or `new()`. |
| `Range` | `start`, `end`, `inclusive`, `reverse` | A range. `reverse` is true after an odd number of `.rev()` calls. |
| `Interp` | `parts` | An interpolated string. A part is a `str` or an expression. |
| `Hole` | see [Holes](#holes) | A construct that lowering does not handle. |

`ExprNode` is the base class of every expression node, except `Hole`.

Parts that expressions hold:

| Class | Fields | Meaning |
|---|---|---|
| `IntConst` | `value`, `radix`, `byte_spelling` | An integer literal. `radix` and `byte_spelling` are the written form. |
| `FloatConst` | `value` | A float literal. |
| `BoolConst` | `value` | A bool literal. |
| `BlankConst` | none | `~`. |
| `StrConst` | `value` | A string literal. |
| `Arg` | `value`, `marker`, `bloom`, `marker_loc` | A call argument. `marker` is the written call-site mode, or `None`. `bloom` is `arr...`. |
| `LambdaParam` | `local`, `convention` | A lambda parameter. Its name and type are on its `ShirLocal`. |
| `Fixed` | none | The kind of `[...]`. |
| `Dynamic` | `new` | The kind of `from([...])` (`new` false) and `new()` (`new` true). |
| `Single` | `value` | An array run of one element. |
| `Repeat` | `value`, `count` | `[v; n]`. The value is evaluated first. |
| `RangeRun` | `range` | `[0..=5]`: a range that fills the slots it spans. |

`Constant` is the union of the five literal classes. `ArrayKind` is `Fixed | Dynamic`.
`ArrayRun` is `Single | Repeat | RangeRun`.

### Call targets (`exprs.py`)

A `Call` holds the target as the source writes it. `CallTarget` is the union of these four
parts.

| Class | Fields | Meaning |
|---|---|---|
| `Named` | `ref`, `type_args`, `type_args_loc` | A name that `scope` resolved, or any expression that can be called. `type_args` are the written type arguments (`identity@(i32)(5)`). |
| `Dotted` | `recv`, `name` | `x.f(...)`: a built-in, an extension, a perk method or an adapter. |
| `Static` | `ty`, `name` | A call on a type name: `Vec.at`, `List.new`. It has no receiver. |
| `BuiltinFn` | `name` | A function that the language gives with no declaration: `print`, `println`. |

### Callees (`callee.py`)

A `Callee` is what a call resolves to. It is an answer of `typecheck` (a row of
`TypeckResults`), not a field of a node. The set is closed. `Callee` is the union of these
classes.

| Class | Fields | Meaning |
|---|---|---|
| `Free` | `decl` | A free function. |
| `Method` | `recv`, `decl`, `receiver` | An extension method or a method of a perk implementation. `receiver` is the convention of the declared `self`. |
| `Static` | `ty`, `decl` | A user static: `Vec.at(3, 4)`. |
| `Builtin` | `recv`, `method` | A built-in method. `method` is a `BuiltinMethod`. |
| `BuiltinStatic` | `ty`, `method` | `List.new`, `HashMap.new`, `Iterator.new`. |
| `Conversion` | `decl` | `e as B`, and `??` across two error types. |
| `Extern` | `decl`, `symbol` | A C function of an `unsafe external` block. `symbol` is the link name. |
| `Stdlib` | `decl`, `module` | A function of a registry stdlib module. `module` is the `use` path. |
| `Intrinsic` | `name` | A function that the language gives: `print`, `println`. |
| `Indirect` | `value`, `field` | A call through a function value: a closure, a local of a function type, or a field of a function type. |

`BuiltinMethod` (`family`, `name`, `nom_self`) names a row of `METHOD_TYPE_REGISTRY`.
`nom_self` is true for a method that takes its receiver.

### Statements (`stmts.py`)

| Class | Fields | Meaning |
|---|---|---|
| `Assign` | `target`, `value`, `is_init` | A `let` (`is_init` true) or a rebind. A rebind frees the old value. |
| `Destructure` | `targets`, `value` | `let (a, b) = v`, or `(a, b) := v` with `Place` targets. |
| `ExprStmt` | `value` | An expression used as a statement. |
| `Return` | `value` | A `return`. |
| `If` | `cond`, `then`, `otherwise` | An `if`. An `elif` is an `If` in `otherwise`. |
| `Loop` | `source`, `body` | A `while` or a `foreach`. The walk kind is an answer of `typecheck`. |
| `Match` | `scrutinee`, `arms`, `consumes` | A `match`. `consumes` is true for `match nom r:`. |
| `Assert` | `cond`, `message` | An assertion. The message is evaluated only when `cond` fails. |
| `Break` | none | A `break`. |
| `Continue` | none | A `continue`. |
| `Expand` | `binder`, `pack`, `body` | An `expand`. It is in a template check body only. |
| `Hole` | see [Holes](#holes) | A construct that lowering does not handle. |

Parts that statements hold:

| Class | Fields | Meaning |
|---|---|---|
| `Discard` | none | `_` in a destructure, or as the binder of a loop. It binds nothing. |
| `Nested` | `targets`, `loc` | A nested destructure: `(a, b)` in `let ((a, b), c) = v`. |
| `Place` | `target` | A place of a tuple rebind: `a` in `(a, b) := v`. |
| `Bare` | none | A binder with no mode. |
| `Ref` | `mode`, `loc` | A `peek` or `poke` binder. It points into the item or the payload. |
| `Cond` | `cond` | The loop source of `while (cond)`. |
| `Items` | `iterable`, `binder`, `by` | The loop source of `foreach (binder in iterable)`. `by` is the written mode of the binder. |

`DestructureTarget` is `LocalId | Discard | Nested | Place`. `ItemMode` is `Bare | Ref`.
`LoopSource` is `Cond | Items`.

### Patterns (`patterns.py`)

A pattern keeps what the source says. A literal and a range bound keep their written
values and their source text, because the value of a bound depends on the type at its
position. That type is an answer of `typecheck`.

| Class | Fields | Meaning |
|---|---|---|
| `Wildcard` | none | `_`. |
| `Literal` | `value`, `written` | An integer or a string arm. `written` is the source text. |
| `IntRange` | `low`, `high`, `inclusive` | `0x80..=0x8f`. |
| `Variant` | `decl`, `variant`, `items`, `enum_loc`, `variant_loc` | `Shape.Circle(r)`: one item for each payload value. |
| `Tuple` | `elements` | A tuple pattern. |
| `Own` | `inner` | `Own(poke h)`: it unwraps an `Own@(T)`. |
| `Or` | `alternatives` | `1 \| 2`. Each alternative binds the same names. |

Parts that patterns hold:

| Class | Fields | Meaning |
|---|---|---|
| `PatBinding` | `local`, `by` | A name that a pattern binds. `by` is a `BindMode`. |
| `Nom` | none | A `nom` binding: the arm takes the payload. |
| `RangeBound` | `value`, `written`, `loc` | One bound of a range. |
| `Arm` | `pattern`, `body` | A match arm. An arm that the source writes as one expression has a block of one. |

`BindMode` is `Bare | Ref | Nom`. A pattern item is `ShirPattern | PatBinding`.
`spelling.py` writes a pattern as the source writes it. The typecheck diagnostics and the
SLIR trap of a match read it.

### Loop kinds (`loop_kind.py`)

The `typecheck` pass picks the walk of each `Loop` once and records it in `TypeckResults`.
A kind holds no operand. The `LoopSource` of the node holds what the source says.

| Class | Fields | Meaning |
|---|---|---|
| `While` | none | The kind of every `Cond` source. |
| `CountedRange` | none | A range written as the iterable. |
| `Cursor` | none | A direct walk of contiguous elements: `T[]`, `T[N]`, `List@(T)`, a slice. |
| `HashMapCursor` | `view` | A direct walk of one view of a HashMap. |
| `Protocol` | `next`, `item` | A walk of a type that carries `next()`. `next` is the resolved call. `item` is the `T` of the `Maybe@(T)`. |
| `IteratorValue` | none | A walk of an `Iterator@(T)` value, through its `next_fn`. |

`HashMapView` is `Keys | Values | Entries | Pairs`. Each class has no field. `HASHMAP_VIEWS`
maps a producer name to its view.

### Places and paths

A place is an expression that has an address. The place nodes are `LocalRef`, `StorageRef`,
`Field`, `Index` and `Slice`. `Borrow` and `Take` hold a place. The walk of a place root is
`walk_place` in `sushi_lang/semantics/places.py`. It is outside SHIR, so that `typecheck`
does not import the borrow pass.

`paths.py` is about control paths, not places. It holds ONE predicate that two passes use.
`typecheck` asks it whether a body can reach its end with no `return` (CE0107) and whether
a statement comes after one that ends the path (CE0140). `borrow` asks it at a join.

| Function | Meaning |
|---|---|
| `terminates(node, leaves_round=, uncovered=)` | Whether every path through a statement or a block leaves the function. With `leaves_round`, it also counts `break` and `continue`. |
| `ends_the_path(statement)` | Whether no statement after `statement` can run. |
| `first_dead_statement(block, body)` | The first pair of an ender and the statement after it. |
| `reached_statements(block, body)` | Each statement that a path reaches. |

`RULES` has a rule for each statement class (gate:
`tests/unit/test_shir_paths_are_total.py`).
A loop never ends the path. A `match` ends it when it has an arm and each arm ends it,
except a match whose arms miss a value.

### Holes

`Hole` (`nodes.py`) stands where lowering cannot handle a construct. It is in the
expression union and in the statement union. It names the AST class of the construct in
`ast_class`. Lowering records each hole in `LoweredProgram.holes`.

| Field | Meaning |
|---|---|
| `ast_class` | The name of the AST class. |
| `codes` | The error codes of an ERROR hole. An empty tuple means no error hole. |
| `member` | An `UnknownMember`: the written member of a type that is no variant (CE2045). |
| `named` | The first name of a named argument that is not allowed. |
| `call` | The `call` with no type arguments, for written type arguments after a dot. |
| `type_args_loc` | The span of those type arguments. |

Lowering is total over the AST body classes. A hole stays in two cases only:

- The backstop of a class that has no row in `LOWERING`.
- The error hole of a program that always has an error: a name with no declaration, or a
  form that the language always refuses. SHIR has no node for it.

Strict mode decides what a hole does. See [Lowering](#lowering).

## The results tables

No node holds a fact that a pass finds. Each pass writes its answers into a table of the
body. Lowering makes the three tables when it lists a body (`LoweredProgram.add`) and
keeps them in `BodyTables` beside the body. All three share `side_table.py`:

- `write_once` refuses a second answer for one key. It is an internal error.
- `SideTable` refuses a copy. A copy would be a second table for one body.
- `MissingAnswer` and `raise_first_gap` report the first gap of a completeness check.

Each table has a read API and a write API. The one writer pass calls the write API and no
other module does (gate: `tests/unit/test_result_tables_have_one_writer.py`).

| Table | Module | Writer pass | Completeness check | Code |
|---|---|---|---|---|
| `TypeckResults` | `typeck.py` | `typecheck` | `typeck_check.py`, `check_answers` | CE0156 |
| `OwnershipResults` | `ownership.py` | `borrow` | `ownership_check.py`, `check_ownership` | CE0157 |
| `LiftResults` | `lift.py` | `lift` | `lift_check.py`, `check_lift` | CE0157 |

### TypeckResults

`typecheck` writes one row kind for each decision. A key has one answer.

| Row | Key | Answer | Read with |
|---|---|---|---|
| node type | `ShirId` | `Type` | `type_of` |
| local type | `LocalId` | `Type` | `local_type` |
| capture type | lambda id, `LocalId` | `Type` | `capture_type` |
| callee | call id | `ResolvedCall` | `callee_of` |
| argument convention | call id, position | `Convention` | `convention_of` |
| instance type | init id | `Type` | `instance_of` |
| field index | node id | `int` | `field_index_of` |
| loop kind | loop id | `LoopKind` | `loop_kind_of` |
| range value | node id | `bool` | `range_value_of` |
| conversion | node id | `ConversionChoice` | `conversion_of` |
| try facts | node id | `TryFacts` | `try_facts_of` |
| display type | node id, part | `Type` | `display_type_of` |
| match facts | match id | `MatchFacts` | `match_facts_of` |
| element type | node id, target path | `Type` | `element_type_of` |
| value | node id | `ConstantValue` | `value_of` |
| global | node id | `str` | `global_of` |
| sources | call id | `tuple[int, ...]` | `sources_of` |
| iterator next | call id | `ResolvedCall` | `iterator_next_of` |

The answer classes are in `typeck.py`:

- `ResolvedCall` (`callee`, `type_args`, `unit`, `link_symbol`, `tail`): the callee with
  the type arguments of a generic callee, the unit that declares it, and the variadic
  `Tail`. A library callee has a `link_symbol` and no unit.
- `Tail` (`position`, `ty`, `convention`): the variadic tail of a call.
- `ConversionChoice` is `Conversion | NoConversion`. `NoConversion` is a numeric cast, or a
  `??` whose error type is the error type of the function.
- `TryFacts` (`success_tag`, `ok`, `err`, `returns`): the facts of a `??`.
- `MatchFacts` (`scrutinee`, `integer`): the type of the scrutinee, and the integer type of
  the literal arms.

The value row holds the value of an integer literal at its type, and the value of the
constant that a `ConstRef` names. An initializer has no value row. The sources row holds,
for a callable with a `peek` return, the position of each argument in the source set. It
uses `-1` for the receiver. `lambda_conventions` gives the convention of each lambda
parameter from the function type.

### OwnershipResults

`borrow` writes these rows.

| Row | Key | Answer | Read with |
|---|---|---|---|
| ownership | consumed node id | `Ownership` | `ownership_of` |
| capture ownership | lambda id, `LocalId` | `Ownership` | `capture_ownership_of` |
| conditional move | `LocalId` | flag | `is_conditionally_moved`, `conditionally_moved` |
| iterator state | call id | `bool` (lent or owned) | `lent_of` |
| lent on heap | call id | `bool` | `lent_on_heap` |
| walk in place | loop id | `bool` | `in_place_of` |
| borrow region | view `LocalId` | `Region` | `region_of`, `regions` |

`Ownership` is in `sushi_lang/semantics/ownership.py`. Its values are MOVE, ADOPT and
REJECT. A conditionally moved local gets a drop flag in SLIR lowering. `Region` (`owners`,
`until`, `leaving`) is the borrow region of a view: each owner that it freezes, the
statement of its last use on each path, and its leaving ends.

### LiftResults

`lift` rewrites no node. The `Lambda` stays in the tree, and SLIR lowering cuts the
function and builds the closure from this table. The mode of a capture is an answer of
`borrow`, not of this table.

| Row | Key | Answer | Read with |
|---|---|---|---|
| symbol | lambda id | `str` | `symbol_of` |
| environment | lambda id | `tuple[LocalId, ...]` | `environment_of` |

The environment is the captured locals in slot order.

### The completeness checks

When a pass ends on a body with no error, a check walks the body once and finds each
answer that a node needs and that the table does not hold. A gap is an internal error that
names the answer, the node class, the position and the body. These errors are a fault in
the compiler, not in the program.

- CE0156 (`typeck_check.py`): "the typecheck pass recorded no {answer} for the {node} at
  {where} in the body '{body}'". `NEEDS` maps each node class to the answers it needs.
  `KNOWN_WITH` holds the shorter lists that `borrow` needs. Read the text of the code in
  `sushi_lang/internals/errors/internal.py`.
- CE0157 (`ownership_check.py` and `lift_check.py`): "the {writer} pass recorded no
  {answer} in its {table} for the {node} at {where} in the body '{body}'". The `writer`
  and `table` parameters tell `borrow` from `lift`.

`ownership_check.py` finds the consuming uses with one table, `CONSUMED`, with a row for
each node class. The closed set of consuming uses is in
[the ownership conventions](../design/ownership-conventions.md). Tests for the checks:
`tests/unit/test_typeck_results_are_complete.py`,
`tests/unit/test_ownership_results_are_complete.py` and
`tests/unit/test_lift_results_are_complete.py`.

## Lowering

`lowering.py` is the entry point. `lower_body(bindings, ctx)` builds one `ShirBody`: the
frame (declaration, origin, parameters, parameter locals) and then the block. `lower_unit`
calls it for each body that the `scope` pass checked in one unit. `lower_copy` lowers each
copy that the copy fixpoint places after the per-unit loop.

`LoweredProgram` keeps the result: `decls` (the `DeclNames` of the program), `units` (a
`LoweredUnit` for each unit), `holes`, and the `BodyTables` of each body. A `LoweredUnit`
lists the written bodies in the order of the walk, then each copy in the order of its
name. `body_of(decl)` gives the body of a declaration.

The lowering of the nodes is a table.

- `LOWERING` (`lower_nodes.py`) maps each AST body class to its lowering function. The
  class of an AST node is the key. A class with no row is a hole.
- `ARGUMENTS` (`lower_nodes.py`) maps the AST classes that are the form of an argument
  (`Spread`, `Borrow`) to their lowering.
- `PATTERNS` (`lower_patterns.py`) maps each AST class that a pattern position holds.

The lowering of aggregates, binders and patterns is in `lower_aggregates.py`,
`lower_binders.py` and `lower_patterns.py`. `BodyLowerer` holds the state of one body:
the id counters, the locals, the copy boundaries and the hole list.

The gate `tests/unit/test_shir_lowering_is_one_table.py` holds five rules:

1. Each key of `LOWERING` and `ARGUMENTS` is an AST body class.
2. Each row that lowering has today is in its table. The sets only grow.
3. The pattern table has a row for each pattern class, and no other row.
4. No lowering module tests an AST class with `isinstance`. A second dispatch would be a
   construct that the totality gate cannot see.
5. Each row is callable.

`tests/unit/test_shir_lowering_is_total.py` checks that lowering is total over the AST
body classes.

### Strict mode

A hole does not stop the compile by default. The variable `SUSHI_SHIR_STRICT=1` makes a
hole an internal error (CE0000). One place reads the variable (gate:
`tests/unit/test_shir_strict_is_read_once.py`): `is_strict` in `lower_nodes.py`.
`stops_on_hole(scope_failed)` decides for a unit. A hole stops the compile only when strict
mode is on and the `scope` pass reported no error in the unit (gate:
`tests/unit/test_shir_strict_skips_a_failed_scope.py`).

An error hole never stops the compile on its own. The program always has an error there,
and that error is the diagnostic. On every compile, an error hole whose codes no error
offered is an internal error when the program has no error. Gate:
`tests/unit/test_shir_error_holes_carry_codes.py`. The test runner sets
`SUSHI_SHIR_STRICT=1` for every compile that it starts. See
[the compiler reference](../compiler-reference.md#dump-shir) for the full text.

## The walk

`walk.py` is the one walk over a SHIR tree.

- `walk(value, path, reached_in=None)` gives each value of the SHIR package under `value`:
  a node, a part (an argument, a call target, a loop source, an arm) and an id. It gives
  the root too. A value comes before the values that it holds. The order is fixed, so it
  is the same on every run.
- Each value comes with its path from the root. A field adds `.name`. A list item adds
  `[index]`. A block adds nothing, so the path of a statement is the path of its block and
  its index (`block[6].body[0]`).
- With `reached_in` (the body), a block gives only the statements that a path reaches.
- `parts(value)` gives each node right under `value`. It does not go into a node that it
  meets.

The walk goes into a field, a list and a tuple. No field holds a node in another
container, and the gate `test_shir_node_unions_are_total.py` checks that. The cost of the
walk grows with the size of the tree (gate: `tests/unit/test_shir_walk_is_linear.py`).

A pass does not write its own walk for dispatch. `dispatch.py` gives the one table
dispatch. `arm_of(arms, node, stage)` gives the arm of a node from the table of a pass.
A class with no row is an internal error at the span of the node. `unhandled` is the
internal error of a node that a stage has a row for but cannot handle in its shape or
state. In a body that has an error, it ends the walk instead (`AfterAnError`). The
`typecheck` pass uses `ARMS` in `semantics/passes/types/visit/__init__.py` and `borrow`
uses `ARMS` in `semantics/passes/borrow/arms.py`.

## The printer

`printer.py` writes the text of `--dump-shir`. It is a pure function of the bodies.

- `PRINTERS` is the one table. It has a row for each class that a body can hold. A class
  with no row is the internal error CE0154 (gate:
  `tests/unit/test_shir_printer_is_total.py`).
- The text is the same on every run and under every hash seed. The printer reads no
  address and no hash order.
- `print_body` writes one body. `print_units` writes the program: each unit, then each of
  its bodies.

The printer also prints the answers of the three passes in place:

- `typecheck`: the resolved callee in place of the call target, the resolved convention of
  each argument, and the type of each local.
- `borrow`: the `Ownership` of a consuming use as a word before the node, a `cond_moved`
  line, a `regions` line, `iterator: lent` or `iterator: owned` and `heap:` after the
  arguments of an iterator call, and `in_place:` after the body of a loop.
- `lift`: `symbol:` and `env:` after the fields of a lambda.

Where a table has no row, the printer writes the form as the source has it.

## The dump

`--dump-shir PATH` writes the SHIR of each body to `PATH`. A `-` prints it on the terminal.
The compile goes on as usual. The dump is written when the semantic analysis ends. The
flag is in `sushi_lang/compiler/cli.py`, and `_write_shir_dump` in
`sushi_lang/compiler/pipeline.py` writes it. A unit of the bundled stdlib and a unit of a
source library are not in the dump.

This program has a `let`, an `if`, a call and a `match` on a `Maybe`:

```sushi
fn describe(i32 n) string:
    if (n > 0):
        return "Mostly Harmless"
    return "Harmless"

fn first_or_zero(i32[] xs) i32:
    match xs.get(0):
        Maybe.Some(v) -> return v
        Maybe.None -> return 0

fn main() i32:
    let i32 x = 42
    let string s = describe(x)
    println(s)
    let i32[] data = from([1, 2, 3])
    return first_or_zero(data) - 1
```

Run `./sushic prog.sushi --dump-shir -`. The dump is:

```text
unit prog

ShirBody describe
  params : [(_0 "n": i32, Guaranteed)]
  locals : []
  block  :
    If(Binary(>, LocalRef(_0), Lit(0)), then: [Return(adopt Lit("Mostly Harmless"))])
    Return(adopt Lit("Harmless"))

ShirBody first_or_zero
  params : [(_0 "xs": i32[], Guaranteed)]
  locals : [_1 "v": i32]
  block  :
    Match(Call(Builtin(LocalRef(_0), "get"), [(Lit(0), Guaranteed)]), [Arm(Variant(@Maybe, "Some", [(_1, Bare)]), [Return(adopt LocalRef(_1))]), Arm(Variant(@Maybe, "None", []), [Return(adopt Lit(0))])], consumes: false)

ShirBody main
  params : []
  locals : [_0 "x": i32, _1 "s": string, _2 "data": i32[]]
  block  :
    Assign(LocalRef(_0), adopt Lit(42), is_init: true)
    Assign(LocalRef(_1), adopt Call(Free(@describe), [(LocalRef(_0), Guaranteed)]), is_init: true)
    ExprStmt(Call(Intrinsic("println"), [(LocalRef(_1), Guaranteed)]))
    Assign(LocalRef(_2), adopt ArrayInit(Dynamic, [Single(adopt Lit(1)), Single(adopt Lit(2)), Single(adopt Lit(3))]), is_init: true)
    Return(adopt Binary(-, Call(Free(@first_or_zero), [(LocalRef(_2), Guaranteed)]), Lit(1)))
```

How to read it:

- `unit prog` starts a unit. Each `ShirBody` below it is one body, in the order of the
  walk. The name after `ShirBody` is the written name of the declaration. A template check
  adds `(TemplateCheck("..."))` after the name.
- `params` lists the parameters as `(_<index> "name": type, Convention)`. `_0` is the
  `LocalId`. The type is the answer of `typecheck` for the local. A plain parameter has
  the convention `Guaranteed`.
- `locals` lists the other locals. In `main`, `_0` is `x`. Each body has its own local
  ids. They start at 0 again in each body.
- `block` has one statement on each line. A node prints as its kind and its fields in
  parentheses, as the dataclass lists them. The ids and spans do not print.
- `@name` is a declaration. `@Maybe` is the enum. `Free(@describe)` is the callee that
  `typecheck` resolved, in place of the written call target.
- `(LocalRef(_0), Guaranteed)` is an argument with its resolved convention.
- `Builtin(LocalRef(_0), "get")` is the callee of `xs.get(0)`: a built-in method on the
  receiver `_0`.
- `adopt` is the `Ownership` that `borrow` gave to a consuming use. A plain value, such as
  an integer or a string literal, gets `adopt`. An owning value gets `move`. The word
  comes before the node. See [the borrow pass](semantic-passes.md).
- `(_1, Bare)` in a `Variant` pattern is a `PatBinding`: the local `_1` with no mode.
- A `Maybe.Some(v)` arm binds `v`. The `Variant` is `@Maybe` with the variant name
  `"Some"`.
- `is_init: true` marks a `let`. A rebind prints `is_init: false`.

The dump has no `iterator:` line, no `cond_moved` line and no hole because this program
needs none of them. A program with a lambda prints `symbol:` and `env:`. A program with a
construct that lowering does not handle prints `<hole: AstClass>`. An error hole prints its
codes: `<hole: IndexAccess, error: CE2122 | CE2404>`.

## Golden dumps

A fixture can compare its dump with a golden file. The directive goes in the leading
comment block of the fixture:

```text
# EXPECT_DUMP_SHIR: test_shir_match_result.shir
```

- The value is a file name beside the fixture. It ends in `.shir`, and it has no
  directory part. The directive can stand once.
- The runner adds `--dump-shir <path>` to the compile and compares the dump with the
  golden file, byte for byte. A difference fails the fixture and shows a diff. A missing
  golden file fails the fixture.
- `python tests/run_tests.py --bless` runs only the fixtures that compare a golden dump
  and writes each dump to its golden file. A run without `--bless` never writes one. Read
  the new file in the diff as you read code.
- The dump must not depend on `PYTHONHASHSEED`. After the main run, the runner step
  `golden_seed_gate` compiles each golden fixture again under another pinned seed.
- `COMPILER_FLAGS` refuses `--dump-shir` and `--dump-slir`. The runner owns them.

The golden fixtures of SHIR are in `tests/ir/shir/`, one directory for each family of
constructs (`calls`, `closures`, `match`, `foreach`, `error_holes` and others). A fixture
is named `test_shir_<topic>.sushi`, and its golden file is `test_shir_<topic>.shir`. The
code that compares them is `tests/golden_dumps.py`. The full text of the directive is in
`tests/TEST_METADATA_GUIDE.md`.

## How to add a SHIR construct

Do the steps in this order. The gates fail if you miss one. Write the golden fixture first
and show it red.

1. **Node.** Add the dataclass to `exprs.py`, `stmts.py` or `patterns.py`. Make it slotted.
   Give it no computed field. Add it to the union (`ShirExpr`, `ShirStmt` or
   `ShirPattern`). A part that a node holds goes in the same module. Gates:
   `test_ir_nodes_are_slotted.py` and `test_shir_node_unions_are_total.py`. The second
   gate holds the field tables of the design, so update `docs/design/ir.md` 7.4, 7.5 or
   7.8 with it.
2. **Lowering row.** Add the AST class to `LOWERING` in `lower_nodes.py`. A pattern class
   goes in `PATTERNS`. Add the class name to `LOWERED` in
   `tests/unit/test_shir_lowering_is_one_table.py`. Do not test the class with
   `isinstance` in a lowering module.
3. **Printer row.** Add the class to `PRINTERS` in `printer.py`. Gate:
   `test_shir_printer_is_total.py`.
4. **Path rule.** A new statement class needs a rule in `RULES` in `paths.py`. Gate:
   `test_shir_paths_are_total.py`.
5. **Walk.** The walk reads the dataclass fields, so it needs no row. Check that every
   node is in a field, a list or a tuple, and in no other container.
6. **Typecheck arm.** Add an arm to `ARMS` in `semantics/passes/types/visit/__init__.py`
   (a pattern goes in `PATTERN_ARMS`). Write every answer that the node needs into
   `TypeckResults`. Add a row to the needs table in `typeck_check.py`, so that CE0156
   finds a missing answer. If the node needs a new kind of answer, add a row kind to
   `typeck.py` with its read and write API.
7. **Borrow and lift arms.** Add an arm to `ARMS` in `semantics/passes/borrow/arms.py`. If
   the node is a consuming use, add it to the rules of `ownership_check.py`. A lambda-like
   node also needs the `lift` pass.
8. **SLIR lowering.** Add the lowering of the node to the SLIR stage
   (`sushi_lang/semantics/slir/`). See [the SLIR reference](slir.md).
9. **Golden fixture.** Add `tests/ir/shir/<family>/test_shir_<topic>.sushi` with
   `# EXPECT_DUMP_SHIR:` and write the golden file with `--bless`. Add the usual
   `EXPECT_*` directives for the behavior of the program.
10. **Run.** Run `python tests/run_tests.py --filter ir/shir` and
    `python -m pytest -q tests/unit -k shir`.

A new result row follows the same rule: a row kind in the table, a write method that uses
`write_once`, a read method, a rule in the check, and a row in the printer if the dump
shows it.
