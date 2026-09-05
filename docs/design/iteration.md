# Iteration — the `foreach` protocol, and where a fallible iterator puts its failure

Status: SHIPPED (the handles epic, Phase 7d, 2026-09-02). This is the decision record.
The rulings here are David's and are settled.

The headline: `foreach` walks a type that carries `next()`, and a fallible iterator says
so in its ITEM rather than on the loop head.

```sushi
use <io/fs>
use <io/buf>

fn show(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let BufReader@(File) r = BufReader.new(nom f, 8192)??
    foreach(line?? in r.lines()):
        println(line)
    return Result.Ok(~)
```

## The concept

Two things are walkable, and they are walked by different machinery.

An **iterator** is `{i32 index, i32 length, T* data}` — a cursor over contiguous storage.
`.iter()` answers one on an array or a `List@(T)`, `.keys()` / `.values()` / `.entries()`
answer one on a `HashMap`, and a range is one. It has no `next` to call: the loop reads the
length and indexes. `Iterator@(T)` is deliberately not a nameable type (**CE2001**), so an
iterator only ever appears as the iterable of the loop that consumes it.

A **protocol iterator** is any type carrying a nullary `next()` that answers `Maybe@(T)`.
The loop calls it until it answers `None`. There is no type to implement and no perk to
name: a struct becomes walkable by gaining one method.

```sushi
struct Countdown:
    i32 at

extend Countdown next(poke self) Maybe@(i32):
    if (self.at <= 0):
        let Maybe@(i32) ended = Maybe.None()
        return ended
    let i32 now = self.at
    self.at := self.at - 1
    let Maybe@(i32) got = Maybe.Some(now)
    return got
```

`foreach(n in c)` now walks a `Countdown`.

## The rulings

### 1. A protocol, not a type and not a perk

`foreach` learns the METHOD, and `Iterator@(T)`'s layout is untouched. Two candidates were
weighed and dropped.

A **perk** — `perk Iterator@(Item): fn next() Maybe@(Item)` — is not expressible: a perk
cannot carry a type parameter (**CE4010**), so the contract cannot name what it yields.
Widening perks to carry type parameters is a language change with no other consumer, and
buying it for one loop is the wrong trade.

A **closure payload** on the iterator struct was the shape an earlier draft carried. It
needs a layout change to a type every array walk in the language goes through, and it buys
nothing the protocol does not: a protocol on `next()` needs no layout change at all, which
is strictly less machinery.

The cost of the protocol is a method call per iteration where the cursor walk has an index
increment. That is the correct cost, because the two are not doing the same work: a cursor
knows every element up front, and a `Lines@(R)` cannot know the next line without reading
it.

### 2. The protocol carries NO error channel

A `next()` declaring `| E` answers `Result@(Maybe@(T), E)` rather than `Maybe@(T)`, and is
therefore not walkable. This is the ruling everything else follows from, so it is worth the
argument in full.

**Three shapes were available** for a loop over something that can fail mid-stream.

| shape | who does it | the item the body sees |
|---|---|---|
| the failure in the ITEM | this design | `Result@(T, E)` — a value |
| the failure on the LOOP HEAD | Swift's `for try await` | a bare `T`; the loop machinery consumed the error |
| a call inside a `while` | Zig, C | whatever the caller unpacks |

A fourth was ruled out before the others: a deferred **`.err()` after the loop** — Go's
`Scanner` shape — cannot report WHICH line failed, and per-line detection is the whole
reason the phase existed. Sushi also has no unwind, so throwing out of the loop body was
never on the table.

**The loop-head form has no long form, and that is what decided it.** Its item is a bare
`T`, so the error has already been consumed by the loop machinery by the time the body
runs. "Report this line's failure and carry on" is then not expressible at any price. The
item-as-value form gives all four behaviours one mechanism:

```sushi
foreach(item in r.lines()):
    match item:
        Result.Ok(line) -> println(line)          # use it
        Result.Err(_) -> println("<unreadable>")  # report and CARRY ON
```

Bail out, substitute, skip, stop — one mechanism instead of four, because the item is an
ordinary value and every tool the language already has works on it: a `match`,
`.realise(default)`, `break`, `return`.

So a fallible iterator sets `T` to a `Result`: `next()` answers
`Maybe@(Result@(T, E))`. The outer `Maybe` says whether the input has more; the inner
`Result` says whether reading it worked. **The two are never the same answer** — a blank
line is `Some(Ok(""))` and the end is `None` — which is the same distinction ruling R22
made for `File.readln()`.

### 3. `??` on the binder is the short form, and it is a MARKER

The common case is "leave on the first failure", and writing a `match` for it every time
would be a tax on the ordinary path. `foreach(line?? in r.lines())` is the short form.

It is **not a second feature**. The AST builder renames the loop's own binding to a hidden
name and prepends one statement to the body:

```
foreach(line?? in it):        →     foreach(__fe_itemN in it):
    BODY                                  let <T> line = __fe_itemN??
                                          BODY
```

That is the entire implementation. The unwrap, the exact-error-type check (**CE2511**), the
warning in `main` (**CW2511**) and the scope cleanup on the propagation path are the ones
`??` already has in every other position — there is no second implementation to keep in
step. The one thing the parser cannot know is that `let`'s type, and the `foreach` validator
fills it in from the item type.

A declared type on a `??` binder names what the USER binds, which is the unwrapped value,
so `foreach(string line?? in r.lines())` puts `string` on the `let`.

A `??` binder over an item that is not a `Result` has nothing to unwrap: **CE2517**. It is
not CE2515, which is a resolution fallback for a chained call whose channel is unhandled,
and not CE2516, which is a wrapper standing where a bool belongs. Here the item is the
right shape for the loop and the wrong shape for the marker.

### 4. A stop must be reachable, so three `next()` shapes are refused

Each refusal has the same reason: the loop must be able to call the method repeatedly and
read a stop out of its answer. All three answer **CE2033**.

| the shape | why it cannot work |
|---|---|
| `next()` answering a bare `T` | nothing says when to stop |
| `next()` declaring `\| E` | answers a `Result`, not a `Maybe` (ruling 2) |
| `next(nom self)` | answers ONCE and spends the iterator; the second call would read a value that has been given away |
| `next(i32 n)` | the loop has nothing to hand it |

A `peek self` or `poke self` receiver is fine, and `poke self` is what a real iterator
wants — a cursor that does not move is an infinite loop, which is the author's mistake to
make and not one the compiler can tell from a legitimate infinite iterator.

### 5. A line iterator stops STICKILY

`Lines@(R)` answers a read failure **once**, as `Some(Err(e))`, and every call after it
answers `None`. No budget, no retry parameter, no knob.

The reason is that a `foreach` must not be able to spin. A descriptor that fails every read
would otherwise hand the body an `Err` forever, and a loop whose body reports and carries
on (the behaviour ruling 2 exists to allow) would never end. The sticky stop makes "report
and carry on" safe by construction: the loop sees at most one failure per iterator.

### 6. `foreach` consumes its iterable, and the loop owns the iterator

Unchanged from every other iterable, and load-bearing here for a new reason. Every iterator
before this design was a non-owning cursor over somebody else's buffer, so no `foreach` arm
had ever destroyed one. A `Lines@(R)` owns a `BufReader@(R)` that owns a `File`, so the loop
holds a real resource, three levels deep.

The iterator therefore lives in a local of its own in a scope that closes after the loop's
end block, registered through `register_owning_value` — the complete registry router, not
`create_local`'s default, which does not know a dynamic array, a `List@(T)` or an `Own@(T)`
(#382). Every exit path destroys it: the end of the input, a `break`, a `return` from the
body, and the propagation path a `??` binder takes.

The item of a protocol iterator is registered as an owner too: it is the payload of a
fresh `Maybe@(T)` nobody else frees, so the iteration owns it, the body may hand it away,
and the scope exit destroys what the body did not take. The `??` binder is the same rule
and not an exception: `foreach(line?? in it)` is `let T line = <item>??`, and `??` over a
named wrapper the writer owns SPENDS it (`borrow-model.md` §10d, #548). On the Ok path the
payload becomes the `let`'s, on the Err path it becomes the caller's, and the item is freed
by nobody because the `??` marked it moved through the ownership seam. Until #548 the
backend registered no owner for an item under a binder instead, a special case that hid the
general defect: a hand-written `let string got = r??` over a named Result local double-freed.

### 7. A reference binding is refused over a protocol iterator

`foreach(poke r in it)` binds a POINTER into the container's element storage. A protocol
iterator has none: the item is the value `next()` answered, held in the loop's own slot.
So a reference binding over one is **CE2423**, whatever the iterable's spelling — the check
asks the protocol and not the method name, because a user `iter()` answering a protocol
iterator would otherwise pass the name test and bind a pointer into a temporary.

## What this replaced

`File.lines()` was a compiler builtin, and the only reading method the compiler still
defined on a handle. It faked laziness through a sentinel: the iterator's `length` field
held `-1` to mean "this is not a buffer", and the data slot carried a heap cell holding the
DESCRIPTOR. `foreach` then read a line per iteration through a second loop arm.

Two things were wrong with it beyond the shape. The sentinel was tested at RUN TIME with
both loops emitted every time, which was merely wasteful on an array walk but became a LINK
failure once the lazy arm called a stdlib function — every program iterating a `string[]`
then referenced `sushi_io_files_fd_readln`. And the iterator had no destructor, so every
`lines()` leaked sixteen bytes.

**A `File` keeps no line loop now**, and that is a decision rather than an omission: an
unbuffered handle yielding lines is one system call per line, which is the cost the buffer
exists to remove. `File.readln()` stays as the one-line unbuffered read.

## Where the pieces live

| piece | file |
|---|---|
| the `??` binder's desugar | `semantics/ast_builder/statements/loops.py` |
| the protocol's resolution, and the call it builds | `resolve_protocol_iterator`, `semantics/passes/types/statements.py` |
| the method ladder it resolves through | `resolve_method`, `semantics/passes/types/calls/methods.py` |
| the loop arm | `_emit_protocol_foreach`, `backend/statements/loops.py` |
| `Lines@(R)` and `lines()` | `sushi_stdlib/src_sushi/io/buf.sushi` |

## See also

- [Language Reference](../language-reference.md) — For-Each Loops, the normative surface
- [io/buf](../stdlib/io/buf.md) — `Lines@(R)`, `next()`, and the buffered layer under it
- [Error Handling](../error-handling.md) — `??` in every position it is legal
- [Method Resolution](method-resolution.md) — the ladder `next()` resolves through
