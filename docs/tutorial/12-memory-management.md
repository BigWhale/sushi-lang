# 12. Memory Management

Most languages pick one of two strategies for memory. Python (and Java) hand the job to a
**garbage collector**: you allocate freely and a runtime later sweeps up what you abandoned,
at the cost of pauses and unpredictable timing. C hands the job to **you**: you `malloc` and
you `free`, and if you get it wrong you get leaks, double-frees, and use-after-free bugs.

Sushi takes a third road, the one Rust and modern C++ travel: the **compiler** tracks
ownership and inserts cleanup for you, at compile time, with no runtime collector. You get
C's predictability and Python's "I didn't have to think about it" — and the compiler refuses
to build programs that would corrupt memory. This chapter is how that works.

## RAII: cleanup rides on scope

RAII stands for "Resource Acquisition Is Initialization", which is a mouthful for a simple
idea: **when a value goes out of scope, its resources are released automatically**. A
heap-allocated list, a string buffer, a file handle — when the variable holding it reaches
the end of its block, the compiler has already arranged for the cleanup.

```sushi
--8<-- "docs/tutorial/examples/12-memory-management/raii.sushi"
```

Output:

```
Crew size: 3
  - Arthur Dent
  - Ford Prefect
  - Trillian
```

Notice what's *missing*: there is no `crew.free()`, no `delete`, no `defer`. The `List`
allocated a buffer on the heap, and that buffer is freed the instant `main` returns. You
write the acquisition; the compiler writes the release.

!!! note "Where the cleanup actually goes"
    The compiler emits the destructor call at every exit from the scope — the normal
    fall-through at the end, an early `return`, and the error path of a `??`. That last one
    matters: even when an error propagates out mid-function, everything acquired so far is
    cleaned up first. RAII and error handling cooperate.

## Move versus copy

When you assign one variable to another, what happens to the original? Sushi answers this
differently depending on the type, and the rule is short:

- **Primitives and strings are copied.** The original stays valid.
- **Dynamic arrays are moved.** The original is consumed and may not be used again.

```sushi
--8<-- "docs/tutorial/examples/12-memory-management/move-vs-copy.sushi"
```

Output:

```
x is still usable: 42
y is a copy: 42
s1: Mostly Harmless
s2: Mostly Harmless
dest length after move: 3
```

Why the difference? A dynamic array owns a heap buffer. If assigning `dest := source` merely
copied the pointer, you'd have two variables believing they own the same buffer — and when
both went out of scope, RAII would free it twice. By **moving** instead (transferring
ownership and marking the source as gone), Sushi guarantees exactly one owner, so exactly one
free. Touch a moved-out variable and the compiler stops you cold with **CE1004: use of moved
variable** — a use-after-free caught before the program ever runs. If you genuinely need two
independent arrays, ask for one explicitly with `.clone()`.

## References: borrowing without owning

Moving is great for ownership, but you often want to *lend* a value to a function without
giving it away. That's a **borrow**, and Sushi has two flavours:

- `&peek T` — a **read-only** borrow. You may look, not touch. Many peeks can coexist.
- `&poke T` — a **read-write** borrow. You may modify in place. Exclusive: only one at a time.

```sushi
--8<-- "docs/tutorial/examples/12-memory-management/references.sushi"
```

Output:

```
Starting value: 41
The answer is 41
The answer is 42
The answer is 42
```

The caller never loses `answer`; it lends it out and keeps using it afterwards. A `&peek`
borrow is the zero-cost way to pass something read-only (no copy of the underlying data is
made), and a `&poke` borrow lets a function mutate the caller's variable directly, which is
how the unit-returning `increment` bumped `answer` from 41 to 42.

Note the last line: `announce` wants a `&peek`, and we handed it a `&poke`. That's allowed —
a read-write borrow can safely **coerce down** to a read-only one. The reverse never happens:
you cannot smuggle a read-only borrow into a slot that wants to write.

## The borrow-checking rules

The exclusivity is not a guideline; it's enforced at compile time. The full ruleset:

- Any number of `&peek` borrows of the same value may be active at once.
- Only **one** `&poke` borrow may be active at a time.
- You may **not** mix `&peek` and `&poke` borrows of the same value simultaneously.
- A `&poke` coerces to `&peek` (safe downgrade); the reverse is forbidden.

These are exactly the rules that make data races and aliasing bugs impossible: shared
read-only access is fine, but anyone who can *write* must have exclusive access.

Here's a program that breaks the second rule. It does **not compile** — it asks for two
exclusive `&poke` borrows of `num` at the same call site:

```sushi
fn swap(&poke i32 a, &poke i32 b) ~:
    let i32 t = a
    a := b
    b := t
    return Result.Ok(~)

fn main() i32:
    let i32 num = 42
    swap(&poke num, &poke num)   # two &poke borrows of num at once
    return Result.Ok(0)
```

The compiler rejects it with **CE2403: 'num' already has an active &poke borrow (only one
exclusive borrow allowed)**, and helpfully points at where the first borrow started. (Mixing
a `&peek` and a `&poke` of the same value instead trips the closely related **CE2407**.) The
fix is to give each exclusive borrow its own variable — the borrow checker is telling you,
correctly, that two mutable aliases to the same memory is a bug.

## Own&lt;T&gt;: explicit heap allocation

Sometimes you need a value on the heap *by name* — most commonly for **recursive types**,
where a struct must contain itself (a linked-list node pointing at the next node). A struct
can't physically embed an infinitely-nested copy of itself, so the recursive field has to be
a pointer. `Own<T>` is that owned heap pointer.

```sushi
--8<-- "docs/tutorial/examples/12-memory-management/own.sushi"
```

Output:

```
Heap-allocated answer: 42
Vogon #7: Prostetnic Vogon Jeltz
```

The three methods you'll reach for:

- `Own.alloc(value)` — allocate `value` on the heap and hand back an `Own<T>`.
- `.get()` — read the value back out.
- `.destroy()` — free it by hand, right now.

You rarely *need* `.destroy()`: like everything else in this chapter, an `Own<T>` is freed
automatically by RAII when it goes out of scope (`answer` in the example never gets a manual
`.destroy()` and leaks nothing). It's there for the cases where you want to release a large
allocation early. For an actual recursive structure, the pattern is a struct field typed
`Maybe<Own<Node>>` — `Maybe.None()` marks the end of the chain, and Sushi stays entirely
null-free.

!!! note "No nulls, ever"
    You may have noticed Sushi has no `null` literal. An absent pointer is `Maybe.None()`, a
    present one is `Maybe.Some(...)`, and the compiler forces you to handle both. The entire
    category of null-pointer dereferences simply doesn't exist here.

## What you learned

- **RAII** frees resources automatically at scope exit — no collector, no manual `free`.
- Primitives and strings are **copied** on assignment; dynamic arrays are **moved**, leaving
  the source invalid (use-after-move is caught as **CE1004**), so each heap buffer has exactly
  one owner and one free. Use `.clone()` for an independent copy.
- **References** lend without owning: `&peek` is read-only and shareable, `&poke` is
  read-write and exclusive, and `&poke` coerces down to `&peek`.
- The **borrow checker** enforces those rules at compile time (e.g. two `&poke` borrows of one
  value is **CE2403**; mixing `&peek` with `&poke` is **CE2407**).
- **Own&lt;T&gt;** is explicit heap allocation for recursive types: `.alloc()`, `.get()`,
  `.destroy()` — though RAII usually frees it for you.

Next up: the standard collections that put all of this to work. On to
[Collections](13-collections.md).
