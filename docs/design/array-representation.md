# Dynamic Array Value Representation

Status: **Decided** (a `T[]` is its descriptor, by value, and `emit_expr` yields it).
Closes #281 and #283, plus two shapes neither issue named. Companion to
`string-representation.md`, which answers the same question for `string`.

## Decision

A dynamic array value is a **3-field descriptor**:

```
{ i32 len, i32 cap, T* data }
```

`ll_type(DynamicArrayType)` has always said so. The rule this document adds is about
`emit_expr`:

> **`emit_expr` of a `T[]` yields the DESCRIPTOR, by value** — the same contract every other
> type has. Exactly one place turns it into an address: `as_array_address` in
> `backend/types/arrays/addressing.py`.

An address that reaches `as_array_address` is **kept**, never re-spilled. That is what makes
a mutating method reach the owner: a `Name` receiver hands over its slot, and a field read
hands over a GEP into the struct. A value can only have come from a temporary, so nobody else
can observe the copy `as_array_address` spills.

## What was wrong

`emit_expr` disagreed with itself. An inline `from([...])` returned a POINTER to a fresh
alloca; `emit_expr` of a `Name` returned the descriptor. So a *value* position could receive
a pointer and an *address* position could receive a value, and which one you got depended on
how the array was spelled:

| shape | what arrived | what the consumer wanted | symptom |
|---|---|---|---|
| `Own.alloc(from([1,2,3]))` | pointer | value, to `store` | `cannot store {i32,i32,i32*}* to {i32,i32,i32*}*` (#281) |
| `l.push(from([1,2]))` | pointer | value, to `store` | the identical message (#283) |
| `let w = ...; Own.alloc(w)` then `o.get().len()` | value | address, to `gep` | `'IntType' object has no attribute 'gep'` |
| `o.get()[0]` | value | address, to `gep` | `'LiteralStructType' object has no attribute 'pointee'` |

The last two were never filed. They are the same defect seen from the other side, and they
are the reason a fix that only normalized the two container sinks would have been wrong: the
array *methods* want an address just as much as the sinks want a value.

Two positions were already right, and they are the evidence for which way round the rule
goes — a struct field and a function parameter both take the descriptor by value:

```llvm
%Row.0 = type { { i32, i32, ptr } }
define internal { i32, [2 x i64] } @take({ i32, i32, ptr } %a)
  %v = load { i32, i32, ptr }, ptr %v_struct        ; the call site loads first
```

## Why not make `ll_type` a pointer instead

That would make the type system agree with what `emit_expr` used to produce, and it was
rejected. The descriptor is already a fat pointer; a second indirection would change the ABI
of every struct with an array field and every `T[]` parameter, and it would re-open the
question of who owns the pointee — a question the descriptor answers today by being owned
wherever it is stored.

## The one element address

`emit_element_pointer` (`backend/types/arrays/indexing.py`) is the single place that turns an
`IndexAccess` into an element address, and it emits the bounds check on the way. It has two
consumers now: the READ (`arr[i]`) and, since #261, the WRITE (`arr[i] := v`). That is why the
write is bounds-checked by construction rather than by a second check written beside it.

The write emits its VALUE before it asks for the address. A dynamic array can reallocate while
the value is being emitted -- `a[0] := grow(poke a)??` is a legal program -- so an address taken
first would point into the buffer that `realloc` released. Rust orders `a[i] = v` the same way,
right operand before place.

## The type-argument reader

`List@(i32[])` and `HashMap@(K, V[])` failed for a second, independent reason. A container
recovers its element type by parsing its own interned name (`List<i32[]>`), and each
container carried a hand-rolled reader: a builtin dictionary plus a struct-table and an
enum-table lookup. **None of the three had an array case**, so the element resolved to `None`,
the typecheck pass stamped nothing on the `??`, and the backend reported **CE0124**.

All three now call `resolve_type_argument` (`semantics/generics/type_strings.py`), which wraps
the real resolver and returns `None` for a name it cannot place — the answer a type-argument
caller wants, where a manifest reader wants the raise.

`HashMap@(K, V[])` was broken exactly as `List@(T[])` was, and no issue mentioned it. That is
the argument for one reader rather than three: the third copy had the same hole and nobody
had looked.
