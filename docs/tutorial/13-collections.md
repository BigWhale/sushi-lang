# 13. Collections

Python gives you `list` and `dict`; Java gives you `ArrayList` and `HashMap`. Sushi gives you
`List<T>` and `HashMap<K, V>` — the same two everyday workhorses, with one important
difference you already met in the [previous chapter](12-memory-management.md): they manage
their own memory. You never free them by hand; RAII does it at scope exit. And because they're
**generic**, the element types are checked at compile time, so a `List<i32>` can never
accidentally hold a string.

This chapter covers both, end to end.

## List&lt;T&gt;: the growable array

`List<T>` is an ordered, dynamically-sized sequence. It starts empty, grows as you push onto
it, and is built into the language — **no import required**.

```sushi
--8<-- "docs/tutorial/examples/13-collections/list-basics.sushi"
```

Output:

```
Crew size: 3
Captain: Arthur Dent
  - Arthur Dent
  - Ford Prefect
  - Zaphod Beeblebrox
Beamed out: Zaphod Beeblebrox
Remaining: 2
```

The core methods at a glance:

- `List.new()` — create an empty list.
- `.push(value)` — append an element (the list resizes itself as needed).
- `.len()` — how many elements it holds.
- `.get(index)` — **safe** indexed access, returning `Maybe<T>`.
- `.pop()` — remove and return the last element as `Maybe<T>`.
- `.iter()` — produce an iterator for a `foreach` loop.

The thing to internalise is that **`.get()` and `.pop()` return `Maybe<T>`, not `T`**. There's
no way to read past the end of a Sushi list and get garbage or a crash: an out-of-bounds
`.get()` simply hands you `Maybe.None()`, and you `match` on it. (We met `Maybe<T>` back in
[error handling](06-error-handling.md); this is the same idea applied to indexing.)

### Inserting and removing in the middle

Appending isn't the only option. `.insert(index, value)` places an element at a position,
shifting the rest right, and `.remove(index)` takes one out, shifting the rest left and
returning it as `Maybe<T>`.

```sushi
--8<-- "docs/tutorial/examples/13-collections/list-insert-remove.sushi"
```

Output:

```
After insert: 10 15 20 30
Removed: 20
After remove: 10 15 30
Length after free: 0
```

That last line shows `.free()`. You don't normally need it — RAII frees the list at scope
exit either way — but it lets you reclaim the buffer *early* if you're done with a large list
long before its scope ends. After `.free()` the list is empty but still perfectly usable.

!!! note "`insert`/`remove` cost"
    `.push()` and `.pop()` are O(1) (amortised), but `.insert()` and `.remove()` are O(n)
    because every later element has to shift over. For a stack-like workload, prefer pushing
    and popping the end; for frequent middle-insertions, reconsider whether a list is the
    right shape.

## HashMap&lt;K, V&gt;: key-value lookups

When you want to look things up *by name* rather than by position, you want a `HashMap<K, V>`.
It maps keys to values with O(1)-average lookups. Unlike `List`, it lives in the standard
library, so it needs an import:

```sushi
use <collections/hashmap>
```

```sushi
--8<-- "docs/tutorial/examples/13-collections/hashmap-basics.sushi"
```

Output:

```
Entries: 3
Arthur is 42
No Marvin here
Arthur is now 43
Entries after free: 0
```

The essentials:

- `HashMap.new()` — create an empty map.
- `.insert(key, value)` — add a pair, or **replace** the value if the key already exists.
- `.get(key)` — look up a value, returning `Maybe<V>` (so a missing key is `Maybe.None()`).
- `.contains_key(key)` — test for a key without pulling the value out.
- `.len()` — number of entries.
- `.free()` — reclaim memory early (the map stays usable, just like a list).

The same safety theme runs through it: `.get()` hands back a `Maybe<V>`, so "key not found"
is a value you handle, not an exception that explodes or a sentinel you might forget to check.

### Iterating over a map

A `HashMap` gives you three iterators:

- `.keys()` — each key.
- `.values()` — each value.
- `.entries()` — each `Entry`, a small struct exposing both `.key` and `.value`.

```sushi
--8<-- "docs/tutorial/examples/13-collections/hashmap-iter.sushi"
```

Output:

```
Number of names: 3
Total name length: 18
Sum of scores: 277
Entries seen: 3
Total via entries: 277
```

Notice the example accumulates totals (counts and sums) rather than printing each entry as it
comes. That's deliberate: **a hash map has no defined iteration order**, so relying on the
order things come out would be a bug. Aggregate, or sort afterwards, if you need determinism.
(`.len()` on the string keys needs `use <collections/strings>`, which is why the example
imports it too.)

!!! note "Iterators need a plain variable"
    There's one sharp edge worth knowing: `.keys()`, `.values()`, and `.entries()` only work
    when the receiver is a **plain variable name**. `scores.entries()` is fine; chaining the
    call onto something else, like `get_map().entries()`, is not yet supported. Bind the map
    to a variable first, then iterate it.

## What you learned

- **`List<T>`** is the built-in growable array: `.new()`, `.push()`, `.pop()`, `.len()`,
  `.get()` (returns `Maybe<T>`), `.insert()`, `.remove()`, iterate via `.iter()`, and free
  early with `.free()`.
- **`HashMap<K, V>`** needs `use <collections/hashmap>` and maps keys to values: `.new()`,
  `.insert()` (replaces on duplicate key), `.get()` (returns `Maybe<V>`), `.contains_key()`,
  `.len()`, `.free()`.
- Iterate a map with `.keys()`, `.values()`, or `.entries()` (whose `Entry` has `.key` and
  `.value`) — but only on a plain variable, and never assume an order.
- Both collections are **generic** (type-checked at compile time) and **RAII-managed** (freed
  automatically at scope exit), and both lean on `Maybe<T>` to make missing elements safe.

Next up: reaching beyond your own code — the standard library, foreign functions, and shared
libraries. On to [Standard Library, FFI & Libraries](14-stdlib-ffi-libraries.md).
