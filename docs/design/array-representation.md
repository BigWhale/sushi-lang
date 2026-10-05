# Dynamic Array Value Representation

Status: **Decided** (a `T[]` is its descriptor, by value, and `emit_expr` yields it).
Companion to
`string-representation.md`, which answers the same question for `string`.

## Decision

A dynamic array value is a **3-field descriptor**:

```
{ i32 len, i32 cap, T* data }
```

`ll_type(DynamicArrayType)` says so. The rule this document adds is about `emit_expr`:

> **`emit_expr` of a `T[]` yields the DESCRIPTOR, by value** — the same contract every other
> type has. Exactly one place turns it into an address: `as_array_address` in
> `backend/types/arrays/addressing.py`.

An address that reaches `as_array_address` is **kept**, never re-spilled. That is what makes
a mutating method reach the owner: a `Name` receiver hands over its slot, and a field read
hands over a GEP into the struct. A value can only have come from a temporary, so nobody else
can observe the copy `as_array_address` spills.

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
test is `tests/array/value_seam/`. Three consumers keep an "if it is a pointer to a dynamic
array, load it" branch -- `statements/variables.py`, `statements/initialization.py` and
`types/core/inference.py`. They are a normalisation point, not a workaround: a field read
such as `let i32[] b = w.items` is a GEP by nature, and the `let` is where it must be loaded
and deep-copied.

## Why not make `ll_type` a pointer instead

That is rejected. The descriptor is already a fat pointer; a second indirection would change
the ABI of every struct with an array field and every `T[]` parameter, and it would re-open
the question of who owns the pointee — a question the descriptor answers by being owned
wherever it is stored.

## The fixed array's own seam

A `T[N]` has no duality: `[N x T]` is a value everywhere, and `emit_member_access` hands a fixed-array field over BY VALUE while it hands a
dynamic one over as a GEP. That difference is deliberate and stays -- returning a pointer from
the fixed field read would change what an assignment, an argument and a hash receive.

A fixed array needs the other half: **a rule for the RECEIVER of a built-in method**. A
receiver that falls back to an `alloca` of a COPY is silently wrong: `b.slots.fill(9)` would
fill the copy and leave the owner unchanged, and no diagnostic is possible, because that store
is legal.

`as_fixed_array_address` (`backend/types/arrays/fixed_addressing.py`) is the one rule. It
resolves the address from the AST rather than from the value, through
`try_get_struct_alloca`, which already walks a `Name`, a nested field chain, an `IndexAccess`
and a reference parameter.

It takes one flag, and that flag is the whole of the read/write split:

| receiver | write address | read address |
|---|---|---|
| local `Name` | its alloca | the same |
| `peek` / `poke` parameter | the pointer it arrived as | the same |
| field or element chain | a GEP | the same |
| constant `Name` | **none. [CE0132](../error-catalog.md#ce0132)** | its global |
| temporary | **none. [CE0132](../error-catalog.md#ce0132)** | a `park_value` spill |

**A read may spill a value that names no storage. A write may not, and there is no fallback.**
That is what keeps a store out of `.rodata` -- a constant resolves for a read and to nothing
for a write, so no such binary can be built even if [CE2096](../error-catalog.md#ce2096) were bypassed. The other unwritable
receivers have their own diagnostics ([CE2408](../error-catalog.md#ce2408), [CE2414](../error-catalog.md#ce2414), [CE2421](../error-catalog.md#ce2421), [CE2422](../error-catalog.md#ce2422), [CE2426](../error-catalog.md#ce2426), [CE2429](../error-catalog.md#ce2429)), so
reaching [CE0132](../error-catalog.md#ce0132) means one of them did not fire. Same treatment `backend/ownership.py` gives a
consuming use with no decision.

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
stamps `DynamicArrayNew.resolved_type` in `propagate_types_to_value`, beside the arm that gives
an array literal's elements their declared type. Every value position funnels there -- a call
argument, an enum payload, a struct field, a rebind, and a `.realise()` default -- so the
emitter always has a type to build from. An empty `from([])` or `new()` in a position that
gives no type (a receiver, an index base, a `println` argument) is the user error [CE2111](../error-catalog.md#ce2111). A
missing stamp in a position that gives a type is a compiler fault, [CE0042](../error-catalog.md#ce0042), and never a guess.

The `let` route is separate and stays so: `declare_dynamic_array` writes `{0, 0, null}` into
the slot it allocates, so `let i32[] e = new()` has nothing left to do and stores nothing.
That is the same reason `from()` has its own arm there -- a declaration fills the slot it owns
rather than building a value to copy into it.

## The one element address

`emit_element_pointer` (`backend/types/arrays/indexing.py`) is the single place that turns an
`IndexAccess` into an element address, and it emits the bounds check on the way. It has two
consumers: the READ (`arr[i]`) and the WRITE (`arr[i] := v`). That is why the
write is bounds-checked by construction rather than by a second check written beside it.

The write emits its VALUE before it asks for the address. A dynamic array can reallocate while
the value is being emitted -- `a[0] := grow(poke a)??` is a legal program -- so an address taken
first would point into the buffer that `realloc` released. Rust orders `a[i] = v` the same way,
right operand before place.

## A nested array

An array element can be an array (ruling R1). The suffixes read from LEFT TO RIGHT: a
suffix applies to the type on its left. The grammar says so with one left-recursive rule
(`array_type` in `grammar.lark`), and `ArrayType.__str__`, `display_type`,
`parse_type_string` and `resolve_type_from_string` all peel the LAST suffix.

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

- `emit_element_pointer` chains through an `IndexAccess` receiver: for `a[i][j]` it calls
  itself on `a[i]`, and the address of that element is the base of the second index.
- `emit_receiver_value` (`backend/expressions/calls/utils.py`) gives an `IndexAccess`
  receiver whose element is a dynamic array its element address, as the `Name` arm gives
  its slot. So `a[i].push(x)` grows the inner array in place.

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
`None`, the typecheck pass stamps nothing on the `??`, and the backend reports **[CE0124](../error-catalog.md#ce0124)**.
A hole in one reader is then a hole in one place.
