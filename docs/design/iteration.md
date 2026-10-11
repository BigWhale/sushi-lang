# Iteration — the `foreach` protocol, and where a fallible iterator puts its failure

Status: SHIPPED. This is the decision record.
The rulings here are David's and are settled.

The headline: `foreach` walks a type that carries `next()`, and a fallible iterator says
so in its ITEM rather than on the loop head.

```sushi
use <io/fs>
use <io/buf>

fn show(string path) ~ | IoError:
    let File f = open(path, FileMode.Read())??
    let BufReader@(File) r = BufReader.new(nom f, 8192)
    foreach(line?? in r.lines()):
        println(line)
    return Result.Ok(~)
```

## The concept

Two things are walkable, and they are walked by different machinery.

An **iterator** is `{i32 index, i32 length, T* data}` — a cursor over contiguous storage.
`.iter()` answers one on an array or a `List@(T)`, `.keys()` / `.values()` / `.entries()`
answer one on a `HashMap`, and a range is one. It has no `next` to call: the loop reads the
length and indexes. Its type is `Iterator@(T)`, and a program can write that name after
`use <collections/iter>` (ruling 9). It has no method
either: `a.iter().len()` and `a.iter().next()` are each **[CE2008](../error-catalog.md#ce2008)** at the call. A range is
not a value, so `(0..3).len()` is **[CE2122](../error-catalog.md#ce2122)**. `.rev()` is the one method a range takes:
the AST builder folds it into the range, which then yields the same values, last first.

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

`foreach(n in c)` walks a `Countdown`.

## The rulings

### 1. A protocol, not a type and not a perk

`foreach` knows the METHOD, and `Iterator@(T)`'s layout does not change. Two other candidates
are dropped.

A **perk** — `perk Iterator@(Item): fn next() Maybe@(Item)` — is not expressible: a perk
cannot carry a type parameter (**[CE4010](../error-catalog.md#ce4010)**), so the contract cannot name what it yields.
Widening perks to carry type parameters is a language change with no other consumer, and
buying it for one loop is the wrong trade.

A **closure payload** on the iterator struct is the second candidate. It
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

A fourth is ruled out before the others: a deferred **`.err()` after the loop** — Go's
`Scanner` shape — cannot report WHICH line failed, and per-line detection is the whole
reason for the protocol. Sushi also has no unwind, so a throw out of the loop body is not
an option.

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
line is `Some(Ok(""))` and the end is `None` — which is the same distinction
`File.readln()` makes.

### 3. `??` on the binder is the short form, and it is a MARKER

The common case is "leave on the first failure", and writing a `match` for it every time
would be a tax on the ordinary path. `foreach(line?? in r.lines())` is the short form.

It is **not a second feature**. The AST builder renames the loop's own binding to a hidden
name and prepends one statement to the body:

```
foreach(line?? in it):        →     foreach(#fe_itemN in it):
    BODY                                  let <T> line = #fe_itemN??
                                          BODY
```

That is the entire implementation. The unwrap, the error-type check (the same type, or a
declared conversion that the `??` calls, else **[CE2511](../error-catalog.md#ce2511)**), the refusal in a bare body
(**[CE0131](../error-catalog.md#ce0131)**, `main` included) and the scope cleanup on the propagation path are the ones
`??` already has in every other position — there is no second implementation to keep in
step. The one thing the parser cannot know is that `let`'s type, and the `foreach` validator
fills it in from the item type.

The `let` is ONE object in two places: the first statement of the body, and the loop's
`item_try_let`, which is how the validator reaches it. A pass that copies the loop keeps
the two as one object. The copy of a generic template takes `item_try_let` from the copied
body, so a `??` binder in a generic function, a generic extension, a lambda inside one and
an `expand` body works as it does in a plain body (#1140).

A declared type on a `??` binder names what the USER binds, which is the unwrapped value,
so `foreach(string line?? in r.lines())` puts `string` on the `let`.

A `??` binder over an item that is not a `Result` has nothing to unwrap: **[CE2517](../error-catalog.md#ce2517)**. It is
not [CE2515](../error-catalog.md#ce2515), which is a resolution fallback for a chained call whose channel is unhandled,
and not [CE2516](../error-catalog.md#ce2516), which is a wrapper standing where a bool belongs. Here the item is the
right shape for the loop and the wrong shape for the marker.

### 4. A stop must be reachable, so four `next()` shapes are refused

Each refusal has the same reason: the loop must be able to call the method repeatedly and
read a stop out of its answer. All four answer **[CE2033](../error-catalog.md#ce2033)**.

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

### 6. `foreach` consumes its iterable, and the loop owns the iterator — except through `poke`

The same as for every other iterable, and load-bearing here: a protocol iterator can own a
resource. A `Lines@(R)` owns a `BufReader@(R)` that owns a `File`, so the loop holds a real
resource, three levels deep.

The iterator therefore lives in a local of its own in a scope that closes after the loop's
end block, registered through `register_owning_value` — the complete registry router, not
`create_local`'s default, which does not know a dynamic array, a `List@(T)` or an `Own@(T)`.
Every exit path destroys it: the end of the input, a `break`, a `return` from the
body, and the propagation path a `??` binder takes.

The move into that local is a consuming position, the same as `let T x = it`: it goes
through the ownership seam (`consume`), and the borrow pass consumes the iterable the same
way. So the loop destroys the iterator and nothing else does (#1145):

- a named local or a `nom` parameter is marked moved, so its scope exit skips it, and a
  later mention of it is **[CE2405](../error-catalog.md#ce2405)**;
- a borrow that owns a resource (a plain or a `peek` parameter, a field read) is
  **[CE2411](../error-catalog.md#ce2411)**, because another owner still frees it and
  `next()` moves the cursor, which is a write; `.clone()` gives the loop a value of its
  own;
- a plain or a `peek` borrow of a value that owns nothing is copied, and the loop walks
  the copy, so the source does not change;
- a temporary (`foreach(line?? in r.lines())`) has no owner, and the loop adopts it.

**The exception is a walk through `poke`** (#1258). When the iterable is a place whose root
is a `poke` reference (a `poke` parameter, a `poke self` receiver, a `let poke` binding, a
`poke` pattern or item binding, or a member or an index chain off one of them), the loop
walks the value IN PLACE. It does not consume the value: `next()` runs on the caller's
storage, and after the loop, a `break` or a `return`, the caller's value is where the walk
stopped. This holds for every walkable value, an owning one and a plain one alike:

```sushi
fn first(poke Words w) ~:
    foreach(s in w):          # walks the caller's `w`; it does not take it
        println(s)
        break
```

The caller then continues the walk where `first` stopped. The typecheck pass decides it
once and stamps `Foreach.walks_in_place`; the borrow pass and the backend read that stamp.
The borrow pass also sets the stamp for a `let` that holds a LENT iterator (ruling 10): that
`let` cannot give its value away, so the loop walks it in place and does not take it.
In the borrow pass the walk is a change of the walked value, the same as a call of
`next()` on it, so a `let`-borrow that reads out of the same owner and is used after the
loop is **[CE2412](../error-catalog.md#ce2412)**. In the backend the loop's hidden slot is a
`poke` reference to the caller's storage, and the loop exit destroys nothing. The body may
read or rebind the walked value: each round calls `next()` on the storage that the value
holds at that time.

The item of a protocol iterator is registered as an owner too: it is the payload of a
fresh `Maybe@(T)` nobody else frees, so the iteration owns it, the body may hand it away,
and the scope exit destroys what the body did not take. The `??` binder is the same rule
and not an exception: `foreach(line?? in it)` is `let T line = <item>??`, and `??` over a
named wrapper the writer owns SPENDS it (`borrow-model.md` §10d). On the Ok path the
payload becomes the `let`'s, on the Err path it becomes the caller's, and the item is freed
by nobody because the `??` marked it moved through the ownership seam. The binder has no
special case in the backend.

### 7. A reference binding is refused over a protocol iterator

`foreach(poke r in it)` binds a POINTER into the container's element storage. A protocol
iterator has none: the item is the value `next()` answered, held in the loop's own slot.
So a reference binding over one is **[CE2423](../error-catalog.md#ce2423)**, whatever the iterable's spelling — the check
asks the protocol and not the method name, because a user `iter()` answering a protocol
iterator would otherwise pass the name test and bind a pointer into a temporary.

### 8. `_` discards the item

A loop that only repeats its body (fill `n` slots, skip `n` bytes) has no use for its
item. A named binder that the body never reads is [CW1001](../error-catalog.md#cw1001), and that is correct: it hides
nothing that a reader must know. So `_` is the binder that binds nothing, the spelling a
`match` pattern has. It is never [CW1001](../error-catalog.md#cw1001), and the body cannot name it,
because `_` is not a name in an expression.

The AST builder gives the loop a hidden name with no span, and the scope pass never
reports a binding with no written name. So every later pass sees an ordinary `foreach`.
The discard takes every binder form: a written type, `peek`/`poke`, and the `??` marker.
`foreach(_?? in it)` keeps the propagation and discards the value: the desugar of ruling 3
binds the unwrapped value to a second hidden name with no span, and the scope exit destroys
it. It needs no new AST shape.

```
foreach(_?? in it):           →     foreach(#fe_itemN in it):
    BODY                                  let <T> #fe_discardM = #fe_itemN??
                                          BODY
```

### 9. `Iterator@(T)` is a type a program can write, and its item has a mode

`Iterator@(T)` is the compiler's own type, as `HashMap@(K, V)` is: one `IteratorType`
answers the written name, `xs.iter()` and a range (epic #1230, #1227).

**The import gates the name only.** Writing `Iterator@(...)` needs
`use <collections/iter>`. With no import, the name is
**[CE2001](../error-catalog.md#ce2001)**, and the help names the import. Behind
`use <collections/iter> as it`, the name is `it.Iterator`. A value, a `foreach`, `.iter()`
and a range need no import. `<collections/iter>` is also a Sushi-source module, and it
brings the name as a re-export of its unit: one provider holds both roles
(`passes/namespaces.py:_stdlib_provider`).

**The item has a mode.** `Iterator@(T)` gives OWNED items. `Iterator@(peek T)` gives
BORROWED items. A tuple item takes the mode on each element:
`Iterator@((i32, peek string))`. `poke` is not an item mode.

```sushi
use <collections/iter>

fn total(poke Iterator@(peek i32) numbers) i32:
    let i32 sum = 0
    foreach(n in numbers):
        sum := sum + n
    return sum

fn main() i32:
    return 0
```

**`peek` is a type argument only here.** It stands at the top of the item, or at the top
of one element of a tuple item, and nowhere deeper. Every other position keeps its error:
`List@(peek T)`, `Maybe@(peek T)`, `Result@(peek T, E)`, `Iterator@(poke T)`,
`Iterator@(peek (i32, string))` and `Iterator@(List@(peek T))` are
**[CE2419](../error-catalog.md#ce2419)**, a `peek` field is
**[CE2415](../error-catalog.md#ce2415)**, and a `peek` in a tuple deeper in the item is
**[CE6107](../error-catalog.md#ce6107)**.

**A plain item makes one type.** For an item that holds no resource,
`Iterator@(peek T)` and `Iterator@(T)` are one type: equal, one hash and one interned
name. The rule reads `owns_resource` with the program's `Drop` set, so a type that
implements `Drop` owns, also when no field owns heap. Each element of a tuple item is
decided alone. The rule is decided at each INSTANCE and never in a template, because a
template does not know whether its `T` holds a resource: in a template,
`Iterator@(peek T)` and `Iterator@(T)` stay two types.

The rule holds inside a generic instance too: `Maybe@(Iterator@(peek i32))` and
`Maybe@(Iterator@(i32))` are one instance, and so are a `List`, a `Result` payload, a user
struct instance and a tuple that hold the iterator. After the collect pass the analyzer
gives the enum table the owner of an item (`install_item_owner`), as the table carries the
derived methods, and every seam that interns an instance settles its arguments by it: the
monomorphizer entry, the `Result` and `Maybe` interning, and the tuple interning. The
typecheck pass settles a declared generic type before it names the instance.

**The name is reserved.** As for `HashMap`, a user struct named `Iterator` is
**[CE0004](../error-catalog.md#ce0004)**, and a user enum of that name is
**[CE0006](../error-catalog.md#ce0006)**.

**How it is stored.** `IteratorType.element_type` is the value type, with no `peek` in it.
`IteratorType.peeks` is the mode: empty when every item is owned, one flag for the whole
item, one flag per element for a tuple item. A diagnostic and an interned name show the
item as written (`Iterator@(peek string)`, `Iterator<$Tuple<i32, peek string>>`), so a
binary library's manifest carries the mode and `--lib-info` prints it.

### 10. An `Iterator@(T)` is a value

An iterator can be stored, passed and walked, and an iterator over a container copies
nothing (#1259).

```sushi
use <collections/iter>

fn count(poke Iterator@(peek string) it) i32:
    let i32 n = 0
    foreach(s in it):                  # walks the caller's iterator in place
        n := n + s.len()
    return n

fn main() i32:
    let string[] names = from(["a", "b"])
    let Iterator@(peek string) it = names.iter()     # borrows names, no copy
    println(count(poke it))
    let Iterator@(i32) r = 0..3                      # a range is a value here
    foreach(x in r):
        println(x)
    return 0
```

**The value has the shape of a closure:** `{next_fn, state, drop_fn, clone_fn}`.
`next_fn(state)` answers `Maybe@(T)`: the next item, or `None` at the end. `drop_fn(state)`
frees what the state owns; it is null when the state owns nothing. `clone_fn(state)`
answers a whole new value. A function value `fn() -> Maybe@(T)` has the same four fields,
so the backend reads the SEMANTIC type, never the layout, to know which one it has.

**Each source supplies its own three functions**: a cursor over contiguous elements (`T[]`,
`T[N]` and `List@(T)` share it), a cursor over the buckets of a HashMap for each of
`keys()`, `values()`, `entries()` and `pairs()` (a bucket with no entry is stepped over), a
range, and `Iterator.new`. A function is emitted once per module, with a name made from
the source and its types (`__sushi_iter_next_...`).

**The producers lend their items.** `xs.iter()` on `T[]`, `T[N]` and `List@(T)`, and the
four HashMap methods, answer `Iterator@(peek T)`. For a plain `T` that is the type
`Iterator@(T)` (ruling 9). `Entry@(K, V)` is not a writable type name, so the iterator of
`.entries()` is reached through a generic or walked where it is made; `.pairs()` is the
writable alternative.

**The state.** A producer over a PLACE (`xs.iter()`, `self.items.iter()`, `m.keys()`)
makes a LENT iterator: its state is a stack slot of the function (an `entry_alloca`, no
`malloc`), because a lent iterator can never leave its function, and its `drop_fn` is
null. A producer over a TEMPORARY (`from([1, 2]).iter()`, `make_map().keys()`) makes an
OWNED iterator: the iterator takes the temporary, and its state holds the cursor and the
container on the heap. A range and `Iterator.new` are owned too. The `drop_fn` of an owned
state destroys the container or the value it holds, and then frees the state. A `drop_fn`
never frees a stack slot. The borrow pass chooses, and stamps the producer with
`MethodCall.iterator_state` (`CURSOR`, `LENT` or `OWNED`, `semantics/generics/iterators.py`).

**The walk.** A `foreach` over an iterator VALUE calls `next_fn` until it answers `None`. A
`foreach` over a source the compiler can read keeps its DIRECT walk, with no call through
`next_fn` and no allocation: `foreach(x in xs.iter())`, `foreach(i in 0..n)`,
`foreach(k in m.keys())`, `foreach(poke r in xs.iter())`. The typecheck pass decides it:
it stamps the producer of a direct walk `CURSOR`, so the producer answers its bare cursor,
and it stamps `Foreach.value_iterator` for a walk of a value. The loop owns the item of an
`Iterator@(T)` (it is the payload of a fresh `Maybe`, as for a protocol walk), and it
borrows the item of an `Iterator@(peek T)`, as it borrows an array element.

**The borrow.** A lent iterator is a let-borrow of the owners of its place: the owners are
frozen until the last use of the iterator (**[CE2412](../error-catalog.md#ce2412)**), and
the iterator cannot be given with `nom`, returned, stored in a field or a container, or
captured (**[CE2411](../error-catalog.md#ce2411)**). It is still DESTROYED at scope exit,
because it owns its value: "it borrows its source" and "it owns its value" are two facts,
and `ownership.bind` keeps them apart (`borrow-model.md` §10g). A write through a lent
`let` is legal: `count(poke it)` moves its own cursor, and a `foreach` walks it in place.
An owned iterator owns everything it walks, and it moves freely.

**A returned iterator.** A lent iterator leaves its function in one form: a function or a
method with a borrowed return, `peek Iterator@(T)` (#1229, `borrow-model.md` §10h).

```sushi
use <collections/iter>

fn evens(i32[] xs) peek Iterator@(i32):
    return xs.iter().filter(nom |i32 x| x % 2 == 0)

fn main() i32:
    let i32[] xs = from([1, 2, 3, 4])
    foreach(x in evens(xs)):           # the answer borrows xs and owns its state
        println(x)
    return 0
```

Its state cannot stay in a stack slot of the function, so it moves to the HEAP at that
return. The two facts stay apart: `iterator_state` says what the value borrows (`LENT`),
and `state_on_heap` (`semantics/generics/iterators.py`) says where its state is. The borrow
pass stamps `iterator_leaves` on each link of the chain that the return gives (a producer
and each lazy adapter, through each `let` that held a link), so EVERY link moves: an
adapter state holds its inner iterator by value, and the state pointer of that inner value
must not point into the frame. A heap lent cursor frees its block in its `drop_fn`, and a
heap adapter state destroys what it holds and frees its block. The caller holds a lent
iterator (§10g): the owners of the source arguments are frozen, and the caller destroys the
answer. `peek Iterator@(T) | E` gives the iterator in the `Ok` payload. A user function that
ADAPTS a borrowed iterator (a `nom Iterator@(...)` parameter) is still not in this design:
a function takes the container instead.

**`.clone()` copies the cursor, not the container.** The clone of a lent iterator borrows
the same owners, and its cursor is a heap copy that its `drop_fn` frees. The clone of an
owned iterator deep-copies what its state holds, so the two walk alone. The clone of an
iterator made by `Iterator.new` over a value that holds a resource has no deep copy, and
the type of the iterator does not show it: the program stops there with
**[RE2027](../error-catalog.md#re2027)**. Beside `clone()`, an iterator has the adapters of ruling 11.

**A range is a value** in every position where `Iterator@(i32)` is expected, written or
propagated: a `let`, an argument, a `return`, a constructor field, a payload, a container
element. The propagation phase stamps `RangeExpr.is_value`, and the backend makes an owned
iterator over the formula of `emit_range`. In an array literal a range keeps its meaning
as a run of elements (`from([0..3])` is 0, 1, 2). A generic `T` that nothing else solves
stays **[CE2122](../error-catalog.md#ce2122)**, and so does every other position.

**`Iterator.new(nom x)`** is a static in the built-in statics table. `x` is a value of a
type with `next()` answering `Maybe@(T)` (the protocol of ruling 1;
**[CE2033](../error-catalog.md#ce2033)** otherwise), and the result is `Iterator@(T)` with
owned items. It names the type, so it needs `use <collections/iter>`
(**[CE2001](../error-catalog.md#ce2001)** without it). There is no implicit conversion. The
typecheck pass builds and checks the `next()` call, as for a protocol `foreach`, and the
backend reads the symbol and the receiver mode from it.

**CE2127 is retired.** It refused a HashMap iterator outside the iterable of a `foreach`,
because the walk found the bucket layout only from the receiver of the call. The value now
carries its own `next_fn`. A borrowed HashMap iterator in a position that takes it is
CE2411, as every other lent iterator is.

### 11. The adapters are compiler code

An iterator has eight built-in adapters (#1228). The LAZY ones answer a new iterator:
`map`, `filter`, `enumerate`, `zip` and `clone_each`. The CONSUMERS walk their receiver to
the end at the call: `fold`, `partition` and `collect` (into a `List@(T)`). `take`, `skip`,
`chain` and the others are not built.

```sushi
fn main() i32:
    let i32[] xs = from([1, 2, 3, 4])
    let List@(i32) squares = xs.iter().filter(nom |i32 x| x % 2 == 0).map(nom |i32 x| x * x).collect()
    let string[] names = from(["a", "bb"])
    foreach(p in names.iter().zip(nom squares.iter())):
        println("{p.0} {p.1}")                 # a 4, then bb 16: nothing is copied
    return 0
```

**Why the compiler.** A Sushi body cannot hold a `peek` item: `peek` is a legal type
argument only in `Iterator@(...)`, so there is no `Maybe@(peek T)`. And a Sushi function
cannot adapt a borrowed iterator (below). So the adapters are a method family of the
compiler (`"iterator"` in `METHOD_TYPE_REGISTRY`), and they travel with the type: no import.
`use <collections/iter>` is necessary only to write the name. An import used only for an
adapter gets [CW3006](../error-catalog.md#cw3006): a name that stands only behind a dot as
a called method is no use of a function of the same name that the import brings.

**The signature.** Each adapter is written as a built-in signature
(`ITERATOR_METHOD_SIGNATURES`, `semantics/generics/iterators.py`), as a generic extension
of `Iterator@(T)` would be: `map@(U)(nom fn(T) -> U f)`, `filter(nom fn(T) -> bool pred)`,
`zip@(U)(nom Iterator@(U) other)`, `fold@(A)(nom A init, nom fn(A, T) -> A f)`,
`partition(nom fn(T) -> bool pred)`, and no argument for the other three. Every receiver is
`nom self`. `T` is the VALUE type of the item, with no `peek`: a function reads the item as
a borrow, whatever its mode. The one solver of a built-in signature solves the method-level
type parameter from the arguments.

**The item mode of the result** follows the receiver (`adapter_result`,
`semantics/passes/types/iterator_adapters.py`):

| Adapter | Item of the result |
|---|---|
| `map` | the owned result `U` of the function |
| `filter` | the item of the receiver, in its mode |
| `enumerate` | `(i32, item)`, the item in its mode: `Iterator@((i32, peek string))` |
| `zip` | `(left item, right item)`, each in its mode: `Iterator@((peek string, i32))` |
| `clone_each` | the item, owned: one `.clone()` for each lent part (the rule of `.clone()`) |

`peek` stands only at the top of an item or of one tuple element. So `enumerate` or `zip`
over a tuple item whose parts have two modes is refused with
[CE2419](../error-catalog.md#ce2419): the `peek` would stand deeper. A tuple item whose
parts are all lent is lent whole in its element.

**`collect` and `partition` over a borrowed item.** There is no `List@(peek T)`, and the
compiler inserts no deep copy. So `collect` and `partition` over an item with a `peek` part
of an owning type are [CE2129](../error-catalog.md#ce2129), with the help
`.clone_each().collect()`. Over a plain item they are legal. In a template `T` is opaque and
may own, so the check holds where the template is written.

**The borrow.** Each adapter takes its receiver with `nom self`. A built-in adapter accepts
a LENT iterator as its receiver, and `zip` accepts one as its argument
(`take_lent_iterator`, `semantics/passes/borrow/iterators.py`): a `let` that held it is
spent, as `f(nom it)` spends a local. The result borrows the owners of each lent input, so
`zip` of two lent iterators borrows both. This holds ONLY for a built-in adapter: a user
function with a `nom Iterator@(...)` parameter refuses a lent iterator
([CE2411](../error-catalog.md#ce2411)), because its type does not show the borrow. A
`foreach` over a lent value that a `let` does not hold (an adapter chain, `it.clone()`)
freezes its owners for the whole loop, as a direct walk freezes its container.

**The state.** A lazy adapter over a lent input is LENT: its state is a stack slot of the
function, as the state of its input is. Over owned inputs it is OWNED: its state is on the
heap. The borrow pass stamps `MethodCall.iterator_state`. The state holds the inner iterator
and the function (`map`, `filter`), the count (`enumerate`) or the second iterator (`zip`).
Its `next_fn` asks the inner iterator for an item and changes it. Its `drop_fn` destroys the
inner iterator and the function, and frees the state only when it is on the heap. Its
`clone_fn` makes a heap state with a clone of each held value. So a `break` out of a
`foreach` over a chain frees the whole chain: the loop destroys the outer value, and each
`drop_fn` destroys the value it holds.

**Items that are not passed on.** `map` destroys each item after its function reads it,
`filter` destroys an item that its predicate refuses, `zip` destroys a left item when the
right side ends, and `fold` destroys each item after its step. Each destroys only the parts
that the item OWNS (`destroy_owned_parts`, `backend/runtime/iterator_adapters.py`); a lent
part is left to its owner. A `foreach` over a tuple item whose parts have two modes binds
the item as a borrow, and gives each owned part a hidden local that the body frees.

**The consumers** run in the caller's function: a loop over `next_fn` that fills a
`List@(T)` (`collect`, `partition`) or carries the accumulator (`fold`). The consumer took
the iterator and the function, so it destroys both after the last item. `fold` gives the
accumulator and the item to its function as borrows, and destroys each accumulator that a
step replaces.

**The eager methods stay.** `xs.map(f)`, `xs.filter(p)`, `xs.fold(...)`,
`xs.enumerate()`, `xs.zip(...)` and `xs.partition(p)` on `List@(T)` and `T[]` keep their
signatures. Each is a one-line forwarder over the adapters
(`docs/design/ufcs-combinators.md`).

## A `File` has no line loop

**A `File` has no line loop**, and that is a decision rather than an omission: an
unbuffered handle yielding lines is one system call per line, which is the cost the buffer
exists to remove. `File.readln()` stays as the one-line unbuffered read.

## Where the pieces live

| piece | file |
|---|---|
| the `??` binder's desugar, and the `_` binder | `semantics/ast_builder/statements/loops.py` |
| the protocol's resolution, and the call it builds | `resolve_protocol_iterator`, `semantics/passes/types/statements.py` |
| the method ladder it resolves through | `resolve_method`, `semantics/passes/types/calls/methods.py` |
| the loop arm | `_emit_protocol_foreach`, `backend/statements/loops.py` |
| `Lines@(R)` and `lines()` | `sushi_stdlib/src_sushi/io/buf.sushi` |
| the written `Iterator@(...)` and its item mode | `semantics/ast_builder/types/iterators.py` |
| the item mode: the written item, the manifest string, one type for a plain item | `semantics/generics/iterators.py` |
| the plain-item rule in the typecheck pass | `settle_item_modes`, `semantics/passes/types/utils.py` |
| the plain-item rule where an instance is interned | `settle_in_tables` over `EnumTable.item_owner`, `semantics/generics/iterators.py` |
| the reserved name | `_compiler_type_names`, `semantics/passes/collect/utils.py` |
| the choice of the walk (direct, or a value) | `_choose_the_walk`, `semantics/passes/types/statements.py` |
| the range value | `propagate_types_to_value`, `semantics/passes/types/propagation.py` |
| `Iterator.new` | `_validate_iterator_new`, `semantics/passes/types/calls/methods.py`; `try_emit_iterator_new`, `backend/expressions/calls/intrinsics.py` |
| the lent and the owned iterator in the borrow pass | `semantics/passes/borrow/iterators.py` |
| the value, the state and the three functions of each source | `backend/runtime/iterators.py` |
| the HashMap cursor | `bucket_source`, `backend/generics/hashmap/methods/iterators.py` |
| the walk of a value | `_emit_value_foreach`, `backend/statements/loops.py` |
| the adapter signatures and the item mode of `enumerate` and `zip` | `ITERATOR_METHOD_SIGNATURES`, `enumerated`, `zipped`, `semantics/generics/iterators.py` |
| the result of an adapter, and CE2129 | `adapter_result`, `semantics/passes/types/iterator_adapters.py` |
| an adapter that takes a lent iterator | `take_lent_iterator`, `semantics/passes/borrow/iterators.py` |
| the state and the three functions of each adapter, and the consumers | `backend/runtime/iterator_adapters.py` |

## See also

- [Language Reference](../language-reference.md) — For-Each Loops, the normative surface
- [io/buf](../stdlib/io/buf.md) — `Lines@(R)`, `next()`, and the buffered layer under it
- [Error Handling](../error-handling.md) — `??` in every position it is legal
- [Method Resolution](method-resolution.md) — the ladder `next()` resolves through
