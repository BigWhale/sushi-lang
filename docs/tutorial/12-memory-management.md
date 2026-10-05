# 12. Memory Management

Most languages use one of two strategies for memory. Python and Java give the job to a
**garbage collector**: you allocate, and a runtime later removes what you do not use. This
causes pauses and timing that you cannot predict. C gives the job to **you**: you `malloc`
and you `free`. A mistake causes leaks, double frees and use-after-free bugs.

Sushi uses a third strategy, as Rust and modern C++ do: the **compiler** tracks ownership
and adds the cleanup for you, at compile time. There is no collector at run time. The
compiler also refuses a program that can corrupt memory. This chapter shows how.

## RAII: cleanup at the end of a scope

RAII means "Resource Acquisition Is Initialization". The idea is simple: **when a value
goes out of scope, the compiler releases its resources**. A list on the heap, a string
buffer, a file handle: when the variable that holds it gets to the end of its block, the
cleanup occurs.

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

There is no `crew.free()`, no `delete` and no `defer`. The `List` allocated a buffer on
the heap, and the buffer is freed when `main` returns. You write the acquisition; the
compiler writes the release.

!!! note "Where the cleanup goes"
    The compiler puts the cleanup at every exit from the scope: the end of the block, an
    early `return`, a `break` or `continue`, and the error path of a `??`. So when an error
    propagates out of a function, the function first cleans up all that it acquired.

When a scope holds more than one value, the values are destroyed in **reverse declaration
order**: the last value declared is the first value destroyed.

## Drop: your own release step

A type such as `File` holds a resource that the compiler cannot see in its fields: an
operating-system descriptor. To give a type its own release step, implement the predefined
perk `Drop`. It has one method, `fn drop(poke self) ~`.

```sushi
--8<-- "docs/tutorial/examples/12-memory-management/drop-order.sushi"
```

Output:

```
Arthur and Ford have towels
Ford hands back the towel
Arthur hands back the towel
```

The compiler calls `drop()` when the value is destroyed, then destroys the fields. `ford`
is declared last, so it is destroyed first.

Some rules for `Drop`:

- Only the unit that declares the type can implement `Drop` for it ([`CE4012`](../error-catalog.md#ce4012)).
- A type that implements `Drop` **owns a resource**, so it moves (see the next section).
- `.clone()` on such a type is an error ([`CE2431`](../error-catalog.md#ce2431)), because the copy is a second handle
  to one resource. A handle type that can share its resource gives a method for it: `File`
  and `TcpListener` have `.share()`.

## Move versus copy

When you assign one variable to another, what occurs to the original? The answer depends
on the type:

- **Primitives copy.** The original stays valid.
- **A string bound directly from a literal copies.** It points into read-only program
  data and owns nothing.
- **Every other string moves**, as does every other owning type. The original is
  consumed, and you cannot use it again.
- **Dynamic arrays move.**

```sushi
--8<-- "docs/tutorial/examples/12-memory-management/move-vs-copy.sushi"
```

Output:

```
x is still usable: 42
y is a copy: 42
s1: Mostly Harmless
s2: Mostly Harmless
taken: Mostly Harmless, edition 42
dest length after move: 3
```

Why the difference? A dynamic array owns a heap buffer. If `dest := source` only copied
the pointer, two variables would own one buffer, and at the end of the scope RAII would
free it two times. A **move** transfers the ownership and marks the source as gone. So
there is always one owner and one free. A use of a moved variable is an error:
**[CE2405](../error-catalog.md#ce2405): cannot borrow moved variable**. The compiler finds the use-after-free before the
program runs. If you need two independent values, ask for a copy with `.clone()`.

The compiler never adds a deep copy. `.clone()` is the only deep copy.

These types **own** something, and so they move:

- a dynamic array `T[]`, `List@(T)`, `HashMap@(K, V)` and `Own@(T)`,
- a string that owns heap,
- a closure that captures an owning value (chapter 18),
- a type that implements `Drop`,
- a struct or enum that holds one of the above. A struct with a `string` field moves,
  also when the field came from a literal.

All other types are **plain**, and they copy.

## Passing a value to a function

Chapter 4 used the parameter modes ([Parameter modes](04-functions.md#parameter-modes)).
This section and the next explain them in full.

Assignment moves, but **a call does not**. A parameter is a *borrow* unless it says
otherwise: the caller keeps the value and frees it, so `f(x)` leaves `x` usable. To give a
value to the callee, write `nom` on the parameter and again at the call site:

```sushi
fn eat(nom i32[] items) i32:
    return items.len()
    # items is freed here -- this function is the owner now

fn main() i32:
    let i32[] data = from([1, 2, 3])
    println(eat(nom data))
    # println(data.len())   # CE2405: data was handed over
    return 0
```

The marker is at **both** ends, or at neither. When you read `f(s)`, you know that `s` is
usable after the call. When you read `f(nom s)`, you know that it is not. You do not have to
open `f`. A marker at one end only is **[CE2427](../error-catalog.md#ce2427)**.

An unmarked parameter is a **read-only** borrow. A write through it is an error: `l.push(3)`
on a `List@(i32) l` parameter is **[CE2422](../error-catalog.md#ce2422)**. A primitive parameter can get a new value
(`x := 5`), but the change stays in the callee.

When a callee needs its own value and the caller must keep its value, use `.clone()`:
`eat(nom data.clone())`.

(One special case: the `string[] args` of `main` is a borrowed view of the process
arguments, not an array on the heap. You can give it to an ordinary borrow parameter. To
give it to a `nom` parameter is **[CE2410](../error-catalog.md#ce2410)**.)

## References: borrowing by pointer

Two more modes give the callee a **pointer** to the caller's value:

- `peek T`: a **read-only** borrow. You can read the value, but not change it. Many `peek`
  borrows can exist at the same time.
- `poke T`: a **read-write** borrow. You can change the caller's value. It is exclusive:
  only one at a time.

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

The caller keeps `answer` the full time: it lends the value and uses it again after the
call. A `peek` borrow passes a value read-only with no copy. A `poke` borrow lets a
function change the caller's variable: `increment` changes `answer` from 41 to 42.

Look at the last line: `announce` wants a `peek`, and we give it a `poke`. This is
permitted: a read-write borrow can **coerce down** to a read-only borrow. The reverse is
not permitted.

## Reference bindings: `let poke` and `let peek`

A `let` can also hold a pointer into a place: a variable, or a field or element of one.
Write the mode after `let`:

```sushi
--8<-- "docs/tutorial/examples/12-memory-management/let-borrow.sushi"
```

Output:

```
p.x is now 42
q.y is 9
```

`r` points into `p`, so `r.x := 42` changes `p`. The binding lives to the end of its
block. While it lives, these rules apply:

- The owner is frozen: `p.x := 3` while `r` lives is **[CE2412](../error-catalog.md#ce2412)**.
- Only one `poke` binding of a value can live at a time (**[CE2403](../error-catalog.md#ce2403)**), and a `peek` and a
  `poke` of one value cannot live together (**[CE2407](../error-catalog.md#ce2407)**).
- A write through a `peek` binding is **[CE2408](../error-catalog.md#ce2408)**.
- A binding needs a place: `let poke T x = make()` (a temporary) is **[CE2404](../error-catalog.md#ce2404)**.

## Pattern bindings have a mode

A binding in a `match` arm has a mode too:

| Binding | What it does |
|---|---|
| `Shape.Poly(p)` | Borrows the payload. You can read it. |
| `Shape.Poly(poke p)` | Points into the payload. You can change it. |
| `Shape.Poly(nom p)` | Takes the payload. |

To take a payload, the `match` must **own** its scrutinee. A temporary (for example, the
result of a call) is owned. For a local variable, write `match nom x:`. This gives the
variable to the match, and a later use of `x` is **[CE2405](../error-catalog.md#ce2405)**. A `nom` binding under a plain
`match x:` is **[CE2432](../error-catalog.md#ce2432)**.

```sushi
--8<-- "docs/tutorial/examples/12-memory-management/pattern-modes.sushi"
```

Output:

```
2 crates on board
shipped 3 crates
```

The first `match` borrows the list and reads its length. The second `match` changes the
list in place. The third `match` takes `cargo`, and the `nom crates` binding gives the list
to `ship`.

## The borrow-checking rules

The compiler enforces the exclusivity at compile time. The full rules:

- Many `peek` borrows of one value can be active at the same time.
- Only **one** `poke` borrow can be active at a time.
- You cannot mix `peek` and `poke` borrows of one value at the same time.
- A `poke` coerces to `peek`; the reverse is not permitted.

These rules prevent aliasing bugs: shared read-only access is safe, and a writer must
have exclusive access.

The program below breaks the second rule. It does **not compile**: it asks for two
exclusive `poke` borrows of `num` at one call site.

<!-- docs-sweep: error CE2403 -->
```sushi
fn swap(poke i32 a, poke i32 b) ~:
    let i32 t = a
    a := b
    b := t

fn main() i32:
    let i32 num = 42
    swap(poke num, poke num)   # two poke borrows of num at once
    return 0
```

The compiler refuses it with **[CE2403](../error-catalog.md#ce2403): 'num' already has an active poke borrow (only one
exclusive borrow allowed)**, and it shows where the first borrow started. A `peek` and a
`poke` of the same value gives the related **[CE2407](../error-catalog.md#ce2407)**. To correct the program, give each
exclusive borrow its own variable.

## Own@(T): explicit heap allocation

Sometimes you must put a value on the heap by name. The usual case is a **recursive
type**: a struct that contains itself, for example a node of a linked list that points to
the next node. A struct cannot contain a full copy of itself, so the recursive field must be
a pointer. `Own@(T)` is that owned heap pointer.

```sushi
--8<-- "docs/tutorial/examples/12-memory-management/own.sushi"
```

Output:

```
Heap-allocated answer: 42
Vogon #7: Prostetnic Vogon Jeltz
```

The three methods:

- `Own.alloc(value)`: puts `value` on the heap and gives back an `Own@(T)`.
- `.get()`: reads the value.
- `.destroy()`: frees it immediately.

You seldom need `.destroy()`. RAII frees an `Own@(T)` when it goes out of scope: `answer`
in the example has no `.destroy()` and leaks nothing. Use `.destroy()` to release a large
allocation early. For a recursive structure, use a struct field of type
`Maybe@(Own@(Node))`: `Maybe.None()` marks the end of the chain, and Sushi has no null.

!!! note "No nulls"
    Sushi has no `null` literal. An absent pointer is `Maybe.None()`, a present pointer is
    `Maybe.Some(...)`, and the compiler makes you handle both. So a null-pointer
    dereference cannot occur.

## What you learned

- **RAII** frees resources at scope exit, in reverse declaration order. There is no
  collector and no manual `free`.
- The **`Drop`** perk gives a type its own release step. `.clone()` on such a type is
  **[CE2431](../error-catalog.md#ce2431)**.
- A parameter **borrows** unless it says `nom`, so a call does not consume the argument.
  Write `nom` at the declaration and at the call site (**[CE2427](../error-catalog.md#ce2427)** if not).
- Primitives and literal-bound strings **copy** on assignment. Every owning type (a heap
  string, an array, a container, a `Drop` type, a struct that holds one) **moves**, and a
  use after the move is **[CE2405](../error-catalog.md#ce2405)**. Use `.clone()` for an independent copy.
- **References** lend without owning: `peek` is read-only and shared, `poke` is
  read-write and exclusive, and `poke` coerces down to `peek`. `let peek` and `let poke`
  bind a reference in a block.
- A **pattern binding** borrows, points into (`poke`) or takes (`nom`) the payload.
  `match nom x:` gives a local to the match.
- The **borrow checker** enforces these rules at compile time (for example, two `poke`
  borrows of one value is **[CE2403](../error-catalog.md#ce2403)**; `peek` with `poke` is **[CE2407](../error-catalog.md#ce2407)**).
- **Own@(T)** is explicit heap allocation for recursive types: `.alloc()`, `.get()`,
  `.destroy()`. RAII usually frees it for you.

The next chapter shows the standard collections. Go to [Collections](13-collections.md).
