# Dynamic Array Value Representation

Status: **Decided** (a `T[]` is its descriptor, by value).
Companion to
`string-representation.md`, which answers the same question for `string`.

## Decision

A dynamic array value is a **3-field descriptor**:

```
{ i32 len, i32 cap, T* data }
```

`ll_type(DynamicArrayType)` says so. The rule this document adds is about the value:

> **The value of a `T[]` is the DESCRIPTOR, by value** — the same contract every other type
> has. Exactly one place turns it into an address: `as_array_address` in
> `backend/types/arrays/addressing.py`.

An address that reaches `as_array_address` is **kept**, never re-spilled. The receiver of an
array method never needs the seam: it is a SLIR place, and the emitter gives the method the
address of that place (`docs/design/ir.md` 8.6), so a method that changes the array writes
its owner. The seam turns a borrowed `T[]` argument (the source of a bulk copy, the other
array of `eq_range`) into an address. That value is a copy that the method only reads, so
nobody can observe the copy that `as_array_address` spills.

## Why the descriptor goes by value

One contract for every position is the point. If one spelling of an array (an inline
`from([...])`) gave a pointer and another (a `Name`) gave the descriptor, a value position
could receive a pointer and an address position could receive a value, and the result would
depend on how the array was spelled. The container sinks (`Own.alloc`, `List.push`) want a
value to `store`, and the array methods want an address to `gep`, so neither side can be
normalized alone.

Two positions give the direction of the rule -- a struct field and a function parameter both
take the descriptor by value:

```llvm
%Row.0 = type { { i32, i32, ptr } }
define internal { i32, [2 x i64] } @take({ i32, i32, ptr } %a)
  %v = load { i32, i32, ptr }, ptr %v_struct        ; the call site loads first
```

Every producer answers the descriptor: `to_bytes()` and `split()` included, and
`File.read_bytes` is Sushi source over `fd_read` (`src_sushi/io/fs.sushi`). The regression
test is `tests/array/value_seam/`. In SLIR a field read such as `let i32[] b = w.items` is a
place (`Field`), and the `let` is a `Copy` or a `Move` of it, so no emitter has to ask whether
a value is the descriptor or its address. `types/core/inference.py` keeps an "if it is a
pointer to a dynamic array, load it" branch for its own callers.

## Why not make `ll_type` a pointer instead

That is rejected. The descriptor is already a fat pointer; a second indirection would change
the ABI of every struct with an array field and every `T[]` parameter, and it would re-open
the question of who owns the pointee — a question the descriptor answers by being owned
wherever it is stored.

## The fixed array's own seam

A `T[N]` has no duality: `[N x T]` is a value everywhere. A fixed-array field read and an
element read are places in SLIR, and a `Copy` of the place gives the value.

A fixed array needs one rule for the RECEIVER of a built-in method. A receiver that falls
back to a slot of a COPY is silently wrong: `b.slots.fill(9)` would fill the copy and leave
the owner unchanged, and no diagnostic is possible, because that store is legal. In SLIR the
receiver is the first operand of the call, a `Copy` of its place or a constant. The emitter
gives the array family the ADDRESS of that place (`operand_address`,
`backend/slir/values.py`), for an `Inout` receiver and a `Guaranteed` one alike:

| receiver | address |
|---|---|
| local | its slot |
| `peek` / `poke` parameter | the pointer it arrived as |
| field or element chain | a GEP of the place |
| slice | the view local |
| constant | a new entry-block slot that holds its value |
| temporary | the temporary local of the statement |

A constant and a temporary are only read. A method that changes the array cannot get one:
[CE2096](../error-catalog.md#ce2096) refuses a method that changes a constant, and the
borrow pass refuses each other unwritable receiver ([CE2408](../error-catalog.md#ce2408),
[CE2414](../error-catalog.md#ce2414), [CE2421](../error-catalog.md#ce2421),
[CE2422](../error-catalog.md#ce2422), [CE2426](../error-catalog.md#ce2426),
[CE2429](../error-catalog.md#ce2429)) before SLIR lowering runs.

A borrowed `T[N]` ARGUMENT (the source of a bulk copy) arrives as its value.
`as_fixed_array_address` (`backend/types/arrays/fixed_addressing.py`), the fixed twin of
`as_array_address`, keeps an address and spills a value into an entry-block slot.

## A large aggregate moves and clones by address

LLVM is slow on one first-class value of a large aggregate: the time for one `load` or
`store` of it grows faster than its size, and SROA splits it into one scalar for each
slot. Thus an aggregate of more than `AGGREGATE_VALUE_LIMIT` (64) bytes never travels as
one value (`moves_by_address`, `backend/types/arrays/runs.py`). The rule is the size of
the LLVM type, and it is the same for each aggregate kind: a fixed array `T[N]`, a struct,
a tuple and an enum (a `Result` and a `Maybe` included). A move or a copy of it is one
`memcpy` (`emit_aggregate_copy`), and a literal fills its slot.

**A struct, a tuple and an enum above the bound are built in their place** (#1287). The
SLIR `assign` of such an aggregate writes each field, or the tag and each payload field,
through its address (`emit_struct_into`, `backend/types/structs.py`;
`emit_enum_variant_into`, `backend/expressions/enums.py`). A part that moves by address is
copied from its address. When a part moves by address, the value is built in a fresh slot
and then copied into the place, because a reference can make any place an alias of the
destination. The tag of an enum in a place is read through its address
(`enum_utils.get_tag_ptr`), never from a loaded value.

**A position that stores a part takes its address.** A part of a type that moves by
address is the address of its value, and `emit_part_into` (`runs.py`) copies its bytes:

- The element of `List.push`. The row of `push` in `LIST_EMITTERS` is `by_address`, and
  the SLIR container row gives the address of such an argument.
- A capture of a closure. The environment slot of the capture gets the bytes.

**A fixed array above the bound is CLONED by address** (#1279). The deep copy reads a
source address and writes a destination slot that the caller gives:

- The seam is `copy_out_into(codegen, dest, source, type)` (`backend/ownership.py`),
  the destination form of `copy_out`. `copy_out` stays the one deep-clone entry.
- A plain element is one `memcpy`. An owning element is a counted walk over the slots
  (`emit_container_walk`), and each slot takes `copy_out_into`. The clone is not unrolled.
- The clone half of the fixed-array row in the `backend/lifecycle.py` handler table takes
  `dest=`. The out-of-line clone function of such a type is
  `void __sushi_clone_<T>(<T>* dest, <T>* source)`. Each other type keeps
  `<T> __sushi_clone_<T>(<T> value)`.
- The SLIR emitter gives `assign` of a `Clone` the address of the source place and of the
  destination place (`backend/slir/values.py`). It does not load the operand.
- A struct, a tuple and an enum above the bound clone by address too (`clones_by_address`):
  the bytes go into the destination, and then each owning field, or the owning payload of
  the active variant, is cloned in place. A smaller struct that holds a field above the
  bound is cloned in a slot in the same way, so no field is one value.
- A repeated element above the bound that owns a resource (`[["x".clone(); 512]; 4]`)
  takes `copy_out_into` from the address of the value for each slot.
- A caller that has only a value (for example a constant) spills it into a slot and loads
  the clone from a second slot.

The destination holds no value to destroy. The two addresses are equal or do not overlap:
two places of one fixed-array type cannot overlap in part.

A fixed array of 64 bytes or less keeps the clone by value, one element at a time.

**An aggregate above the bound crosses a call boundary by address** (#1285, #1287). One function
decides the LLVM signature for the two ends of a call: `call_signature(answer, params)`
(`backend/functions/signature.py`). Each declaration site and each call emitter calls it,
and the gate `tests/unit/test_call_signature_is_one.py` refuses a second rule:

- A parameter of each mode is a pointer. A `peek` or a `poke` parameter is a pointer
  already. For a by-value parameter (the default borrow, and `nom`) the caller gives the
  address of the place of the argument, and the callee copies the bytes into its own
  entry-block slot at its entry (`emit_aggregate_copy`, one `memcpy`). Thus a write to
  the parameter in the callee does not change the place of the caller, as before. A
  constant argument goes into a slot first (`operand_address`). The ownership does not
  change: the SLIR drops of the caller and of the callee are the same as for a value.
- The answer goes into a slot that the caller gives as the LAST parameter, and the
  function answers `void`. The callee builds the answer in its own `_0` slot and copies it
  into the slot of the caller at the `return`. So a slot of the caller that is also a
  place that the callee reads (through a `poke` argument or a `var`) does not change
  before the callee ends. The caller gives the address of the destination place of the
  call.
- The last position keeps the environment of a function value first:
  `(env, <params>, <answer slot>)`. So the type of a function value, its lifted lambda and
  the thunk of a named function agree, and a thunk forwards its parameters unchanged.
- A binary or a hybrid `.slib` declares the functions of its manifest through the same
  function, so the library and the consumer agree.
- An indirect call outside SLIR (an iterator adapter, `map_err`) that has a value for a
  by-address position puts the value into a slot first, and reads an answer that moves by
  address from a slot (`emit_indirect_call`, `backend/runtime/closures.py`).

- A `| E` function whose `Result` is above the bound gives the `Result` in the slot of
  the caller, as each other answer above the bound.
- The `next` of an iterator (`_next_type`, `backend/runtime/iterators.py`) and the step of
  a HashMap cursor (`backend/generics/hashmap/cursor.py`) take their signature from
  `call_signature` too. A `Maybe@(T)` above the bound goes into the slot of the caller
  (`emit_return`), and a `foreach` gives the address of its destination place
  (`emit_call_answer`, `backend/functions/signature.py`). `Iterator.new` passes that slot
  on to the `next()` of the value.
- The out-of-line functions of a derived contract take an aggregate above the bound by
  address: `__sushi_eq_<T>(<T>*, <T>*)`, `__sushi_cmp_<T>(<T>*, <T>*)` and
  `__sushi_fmt_<T>(<T>*, buffer*)` (`takes_address`, `backend/types/contracts.py`). The
  seams `emit_value_eq`, `emit_value_compare`, `emit_value_fmt` and `emit_value_to_str`
  take a value or the address of such a value, and a field or an element above the bound
  is read by its address (`held_operand`).

The limits of this rule:

- An `unsafe external` keeps its C signature. C has no fixed array by value.
- `main` keeps its fixed signature.
- A stdlib row (`backend/expressions/calls/stdlib/signatures.py`) keeps the signature of
  its generator. No row takes or answers a type above the bound; a unit test holds it.
- A container method that answers a `Maybe@(T)` (`List.get`, `pop`, `first`, `last`)
  builds the `Maybe` as one value (#1290). The hash of a value (`emit_value_hash`) reads a
  value.

## A run-time length, and the cursor

An array literal element may fill more than one slot: `value; count` repeats one value, and
`a..b` yields a sequence. Both may have a count the compiler cannot read, and
only in a `from()` literal -- a fixed array's length is part of its TYPE, and a constant's
evaluator needs the values.

So the fill holds no compile-time integer. `EmittedRun` carries an `ir.Value` count and no
start; `fill_runs` threads a **cursor** instead:

```python
cursor = ir.Constant(i32, 0)
for run in emitted:
    base = gep_array_element(codegen, data_ptr, cursor)
    ...                                     # fill run.count slots from base
    cursor = builder.add(cursor, run.count)
```

The cursor is why a run-time element may sit anywhere in a literal: nothing depends on a
compile-time position. When every count is constant the adds are folded in the emitter, so
an all-readable literal emits no run-time arithmetic for the cursor.

`emit_dynamic_array_of_length` (`backend/types/arrays/utils.py`) is the allocation for a
run-time length. Capacity equals the length rather than the next power of two, which is safe
at zero because `emit_dynamic_array_push` already selects a capacity of one when it sees
zero. It is named rather than inlined because a fresh array of a COPIED range needs the same
allocation with a different filler.

**A readable count never pays for the run-time mechanism.** llvmlite does not fold, so a
readable range must be turned into values by the front end; at `--opt none` there is no
second chance. Three tiers: a readable range
under `UNROLL_LIMIT` stores literals and emits no arithmetic, a longer one walks a constant
trip count, and an unreadable one walks with `first`, `step` and `count` computed.

## The empty array, and `new()`

An empty array is `{0, 0, null}`. `emit_empty_dynamic_array` (`backend/types/arrays/utils.py`)
is the one builder of it, and `new()` and `from([])` are the same array. A literal cannot
count zero elements with a count that the compiler can read: `from([0; 0])` is [CE2017](../error-catalog.md#ce2017).

`new()` names no element type. It takes one from the position it stands in: the typecheck pass
writes the type of the position as the type row of the node in `TypeckResults`, as it gives
an array literal's elements their declared type. Every value position does so -- a call
argument, an enum payload, a struct field, a rebind, and a `.realise()` default -- so the
emitter always has a type to build from. An empty `from([])` or `new()` in a position that
gives no type (a receiver, an index base, a `println` argument) is the user error [CE2111](../error-catalog.md#ce2111). A
missing stamp in a position that gives a type is a compiler fault, [CE0042](../error-catalog.md#ce0042), and never a guess.

The `let` route is separate and stays so: `declare_dynamic_array` writes `{0, 0, null}` into
the slot it allocates, so `let i32[] e = new()` has nothing left to do and stores nothing.
That is the same reason `from()` has its own arm there -- a declaration fills the slot it owns
rather than building a value to copy into it.

## The one element address

SLIR lowering makes an element read and an element write into the place `Index`, after the
bounds check (`element`, `semantics/slir/lower_arrays.py`; `Trap("RE2020")` on the failing
edge, no check under `dont_panic`). The emitter turns the place into an address with
`element_address` (`backend/types/arrays/indexing.py`), the single place that does so. It
has two consumers: the READ (`arr[i]`) and the WRITE (`arr[i] := v`). That is why the
write is bounds-checked by construction rather than by a second check written beside it.

The write lowers its VALUE before the index and its check. A dynamic array can reallocate
while the value is evaluated -- `a[0] := grow(poke a)??` is a legal program -- so an address
taken first would point into the buffer that `realloc` released. Rust orders `a[i] = v` the
same way, right operand before place.

## A nested array

An array element can be an array (ruling R1). The suffixes read from LEFT TO RIGHT: a
suffix applies to the type on its left. The grammar says so with one left-recursive rule
(`array_type` in `grammar.lark`), and `ArrayType.__str__`, `display_type` and
`parse_type_string` all peel the LAST suffix.

| Written | Is | Layout |
|---|---|---|
| `i32[][]` | a dynamic array of `i32[]` | a descriptor of descriptors |
| `i32[3][]` | a dynamic array of `i32[3]` | a descriptor; each slot is 12 bytes inline |
| `i32[][3]` | a fixed array of 3 `i32[]` | 3 descriptors inline |
| `i32[2][3]` | a fixed array of 3 `i32[2]` | `[3 x [2 x i32]]` |

An index removes the last suffix: for `i32[2][3] m`, `m[i]` is an `i32[2]` and `i` is in
`0..3`. C reads the other way (`int m[2][3]` is 2 rows of 3), by intent: the Sushi order is
the order in which the type is built.

No new representation is necessary. A nested array is an ordinary element in a slot: an
inner `T[]` is its 16-byte descriptor in the slot, and an inner `T[N]` is the whole fixed
array inline in the slot. The element-generic code (the lifecycle handler table, the
destructors, `copy_out` per slot, the LLVM type mapping) treats it as any other element.

Two place emitters must give the inner array its ADDRESS, never a spilled copy. A copy
takes the write or the growth, and the element keeps the old value (or, after a growth, a
pointer to the freed buffer, which is then freed twice):

- `place_address` (`backend/slir/values.py`) chains through an `Index` place: for
  `a[i][j]` the address of the element `a[i]` is the base of the second index.
- The receiver of a mutating method is a place in SLIR (`Index` of the outer array), so the
  emitter gives the method the address of the element, as it gives a local its slot. So
  `a[i].push(x)` grows the inner array in place, when the groups B1 and B2 bring it back.

## The one bulk copy

`extend`, `extend_range`, `s` and `ss` are the same operation with different arguments, so
they share one emitter (`backend/types/arrays/copy.py`). Four emitters would mean four
bounds rules and four answers to what the source owns.

The rule the copy follows:

> **A bulk write borrows its source, and every slot it writes takes its own `copy_out`.**

A write that fills N slots cannot consume, because consuming means one value reaching one
position and a bulk write has no single position. That covers one value and N slots -- the
repeated element, and `.fill()` -- and it covers N source values and N slots.

A plain element type takes a `memcpy`, because a shallow store of a plain value IS the
value and a walk would emit N stores for nothing. An owning one walks and clones through
`copy_out`, the decision `backend/lifecycle.py`'s handler table already makes.

`.s(start, end)` and `.ss(start, count)` differ in one thing: whether the second argument is
an exclusive END or a LENGTH. `clamp_range` takes that as a flag and narrows both the same
way, and the arguments reach it RAW -- the start is clamped FIRST, which is what makes
`.s(-2, 3)` three elements and not five.

**A range outside the source is clamped, never trapped**, the same answer `string.s` and
`string.ss` give. That also makes the walk safe by construction: it compares with an
unsigned predicate, so a negative count would read as four billion, and the clamp removes
that rather than leaving a guard to fire.

Clamping is deliberately unlike `arr[i]`, which traps [RE2020](../error-catalog.md#re2020). An index names ONE element and
either has it or does not; a range asks for what overlaps, and can always answer.

**The source may not alias the destination** ([CE2430](../error-catalog.md#ce2430)). Growing the destination may
reallocate its buffer, which leaves the source pointer dangling mid-copy. A copy that must
read what it is writing is a different operation: a DEFLATE back-reference expands a run by
reading bytes the same loop just wrote, and it stays a per-element loop.

`fill` reads the same code for its own reason: a borrowed value that is a slot of the
receiver (`a.fill(a[0])`) is destroyed by the first store, and every later slot copies
freed storage. It is refused when the element type owns a resource; a plain element is a
copy and has no alias.

## A slice

A slice `data[a..b]` is a `T[]` and needs no new representation: its descriptor is
`{b - a, b - a, data + a}`, a length, a capacity equal to the length, and a pointer into the
storage of the array ([Slices](slices.md)). The capacity is the length, so nothing can read
it as room to grow; the borrow pass refuses every growth through a slice anyway.

SLIR lowering checks `0 <= a <= b <= len()` (RE2028) and writes the descriptor into a VIEW
local with a `Slice` rvalue (`docs/design/ir.md` 8.7). `slice_value`
(`backend/types/arrays/indexing.py`) is the one emitter of the descriptor, through the
`gep_utils` trio. A method receiver (`data[1..3].len()`), a chained index (`data[1..3][0]`)
and a `peek` argument read the view local as any other place. The slice is BORROWED: a view
local owns nothing, and no drop frees it or the storage it points into.

The bulk copy reads a slice like any other source. A slice of the destination itself is
CE2430, as `a.extend(a)` is: the alias check sees through a slice to the array it views.

## The type-argument reader

A container reads its element types from the `generic_args` of its instance, and never
from its interned name (`List<i32[]>`). The name is a spelling, not a thing to parse. The one
reader is `instance_type_arguments` (`semantics/generics/list.py`). It resolves each argument
RECURSIVELY against the struct and enum tables at read time, because the monomorphize pass can
leave a nested reference unresolved (`List<List<i32>>` holds a `GenericTypeRef`).
`parse_list_types` and `parse_hashmap_types` call it. `split_type_arguments`
(`semantics/generics/type_strings.py`) is the one splitter of a type STRING, the form that a
library manifest carries.

One reader, not one per container: a hand-rolled reader that parses the interned name
misses a case (an array element, `List@(T[])` or `HashMap@(K, V[])`), the element resolves to
`None`, and the program fails with an internal error. A hole in one reader is then a hole in
one place.
