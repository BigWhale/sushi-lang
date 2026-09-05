# Memory Management

[← Back to Documentation](index.md)

Comprehensive guide to Sushi's memory management: RAII, references, borrowing, and ownership.

## Table of Contents

- [Philosophy](#philosophy)
- [RAII (Automatic Cleanup)](#raii-automatic-cleanup)
- [Move Semantics](#move-semantics)
- [References and Borrowing](#references-and-borrowing)
- [Own@(T) for Heap Allocation](#own-for-heap-allocation)
- [Manual Memory Management](#manual-memory-management)

## Philosophy

Sushi provides memory safety without garbage collection:

1. **RAII** - Resources freed automatically at scope exit
2. **Compile-time borrow checking** - Prevents use-after-free and double-free
3. **Move semantics** - Clear ownership transfer for every type that owns heap (dynamic arrays,
   `List@(T)`, `Own@(T)`, `HashMap@(K, V)`, capturing closures, `string`, and any struct/enum/fixed
   array holding one of those)
4. **Zero-cost abstractions** - No runtime overhead

## RAII (Automatic Cleanup)

Resources are automatically freed when they go out of scope.

> **Foreign pointers are unmanaged.** The FFI `ptr` type (from an
> `unsafe external "C"` block) is opaque and exempt from both RAII and borrow
> checking - a `ptr` has no destructor and you must call the matching C free
> yourself. The one thing the compiler *does* free automatically across the
> boundary is the temporary C `char*` copy created when marshalling a `string`
> argument: it is registered per-scope and freed on every exit path (no leak).
> See [Foreign Function Interface](ffi.md).

### Dynamic Arrays

```sushi
fn process() ~:
    let i32[] numbers = from([1, 2, 3])
    numbers.push(4)
    numbers.push(5)

    # numbers automatically freed here (scope exit)
    return Result.Ok(~)

fn main() i32:
    process()  # No manual cleanup needed
    return Result.Ok(0)
```

### Structs with Dynamic Fields

```sushi
struct Buffer:
    string[] lines
    i32[] numbers

fn process() ~:
    let Buffer buf = Buffer(
        lines: from(["line1", "line2"]),
        numbers: from([1, 2, 3])
    )

    # Both buf.lines and buf.numbers automatically freed
    return Result.Ok(~)
```

### Nested Structures

```sushi
struct Node:
    i32 value
    i32[] children

struct Tree:
    Node[] nodes

fn build_tree() ~:
    let Tree t = Tree(nodes: from([]))
    t.nodes.push(Node(value: 1, children: from([2, 3])))
    t.nodes.push(Node(value: 2, children: from([4, 5])))

    # Automatic recursive cleanup:
    # 1. t.nodes freed
    # 2. Each Node's children freed
    return Result.Ok(~)
```

## Move Semantics

Move-ness is **compositional** and answers one question: **does this type own heap that RAII must
free?** A value moves iff it does -- a **dynamic array (`T[]`), `List@(T)`, `Own@(T)`,
`HashMap@(K, V)`, a `string`, a capturing closure, or any struct/enum/fixed array holding one of
those**. Putting such a value in a constructor field or array literal, inserting it into a
container, binding it to a new name, capturing it in a closure, or handing it to a **`nom`**
parameter transfers ownership; the source is consumed and using it afterward is a use-after-move
error (`CE2405`).

**A plain call argument does NOT transfer.** A parameter is a borrow unless it says otherwise, so
`f(x)` leaves `x` yours. See [Function Arguments](#function-arguments) for the four modes.

Everything else **copies**: primitives, and *plain-data* composites built only from those. The
class is derived from the type's SHAPE, so there is nothing to opt into and no way for a type to
lie about what it owns.

**One exception, tracked per binding, not per type.** A `string` bound directly from a string
literal (`let string s = "hi"`) owns nothing -- it points into read-only program data with the
runtime `owned` bit clear -- so passing, rebinding, or capturing *that* binding behaves like a copy:
both sides stay usable, because nothing was ever heap-allocated to transfer. Any other `string` --
built by interpolation, returned from a call, read out of a container, or received as a parameter --
is an ordinary heap-owning value and moves. This exception lives on the *binding*, not the type: a
struct with a `string` field is a MOVE type even when every value it is ever constructed from is a
literal, because the exception does not propagate through a field. See "What Moves" and "What
Copies" below for both shapes side by side.

**A fixed array (`T[N]`) is not itself a heap allocation** -- its storage is inline in its owner --
but it moves too when its element type owns heap, for the same compositional reason a struct with an
owning field moves. `.clone()` exists on both fixed and dynamic arrays, since either one may need an
independent copy.

Every struct and enum also gets an auto-derived **`.clone()`** -- the single explicit way to copy an
owning value: `consume(nom buf.clone())` hands the callee an independent copy while `buf` stays
yours.

### What Moves

**Dynamic arrays, `List@(T)`, `Own@(T)`, and owning structs/enums:**
```sushi
struct Buffer:
    i32[] data

fn main() i32:
    let i32[] a = from([1, 2, 3])
    let i32[] b = a  # a moved to b

    # ERROR CE2405: cannot borrow moved variable 'a'
    # println(a.len())

    # A struct/enum that owns heap moves too (it contains a T[]):
    let Buffer buf = Buffer(data: from([1, 2, 3]))
    let Buffer other = buf  # buf moved to other
    # println(buf.data.len())  # ERROR CE2405: buf was moved

    return Result.Ok(0)
```

**A struct with a `string` field is a MOVE type too -- even when the string is a literal:**
```sushi
struct Person:
    string name
    i32 age

fn main() i32:
    let Person a = Person(name: "Ada", age: 30)
    let Person b = a  # a moved to b (a struct containing a string field owns heap)

    # ERROR CE2405: cannot borrow moved variable 'a'
    # println(a.name)

    println(b.name)
    return Result.Ok(0)
```

**A non-literal `string` moves on its own, with no struct involved:**
```sushi
fn consume(nom string s) ~:
    println(s)
    return Result.Ok(~)

fn main() i32:
    let string name = "world"
    let string greeting = "Hello, {name}!"  # interpolation heap-allocates: greeting owns heap
    consume(nom greeting)  # greeting handed over

    # ERROR CE2405: cannot borrow moved variable 'greeting'
    # println(greeting)

    println(name)  # OK: name is bound from a literal, so it owns nothing and never moves
    consume(nom name)
    println(name)  # still OK -- consume() got an independent, ownerless value

    return Result.Ok(0)
```

**A type that DECLARES a resource moves, whatever its fields say.** Most types own HEAP,
and the compiler works that out by walking the fields. A file or a socket holds one `i32`
descriptor, and no field walk can see that it means something: the type has to SAY so, by
implementing the built-in `Drop` perk.

```sushi
struct Ticket:
    i32 seat

extend Ticket with Drop:
    fn drop(poke self) ~:
        println("ticket {self.seat} returned")

fn take(nom Ticket t) ~:
    println("using ticket {t.seat}")
    return Result.Ok(~)

fn main() i32:
    let Ticket a = Ticket(7)
    take(nom a)          # a is handed over, and drops inside take()
    # println(a.seat)    # ERROR CE2405: a was moved

    let Ticket b = Ticket(9)
    println("holding {b.seat}")
    return Result.Ok(0)  # b drops here: "ticket 9 returned"
```

`Drop` needs no import. Four rules go with it:

- **`drop()` runs first, then the fields.** A struct that holds a `Ticket` runs its own
  `drop()` before the ticket is destroyed, so what it owns is still readable while it
  closes itself down.
- **Scope exit destroys in reverse declaration order.** The last binding opened is the
  first closed.
- **Only the unit that declares a type may implement `Drop` for it** (CE4012). Otherwise
  another unit could quietly replace the implementation and stop a resource being
  released.
- **There is no `.clone()`** on a type that owns a resource, or on anything holding one
  (CE2431). A deep copy would copy the descriptor number and leave two values that both
  drop -- a double release the copy verb would hide. The operation that means "a second
  owner" gets its own name, `.share()`, and it is `dup(2)`: a second descriptor over a
  SHARED open file description, so the offset is shared too.

### What Copies

**Primitives, and plain-data composites of only those:**
```sushi
struct Point:
    i32 x
    i32 y

fn main() i32:
    let i32 x = 42
    let i32 y = x  # x copied to y
    println(x)  # OK: x still valid

    let Point p = Point(x: 1, y: 2)
    let Point q = p  # p copied to q -- Point owns no heap
    println(p.x)  # OK: p still valid

    return Result.Ok(0)
```

**A `string` bound from a literal is the one exception that copies despite owning-heap being a
whole-program MOVE type:**
```sushi
fn main() i32:
    let string s1 = "hello"
    let string s2 = s1  # s1 copied to s2 -- s1 owns nothing, so nothing was transferred
    println(s1)  # OK: s1 still valid
    println(s2)  # OK: s2 still valid

    return Result.Ok(0)
```

### Reading Through a Borrow, Without Consuming

A field read, an index, and a container get-out -- `s.field`, `arr[i]`, `list.get(i)??` -- do not
copy. They hand back a **borrow**: a read-only view of storage the owner keeps and still frees.
Reading it is free. **Consuming** it -- handing it to a `nom` parameter, returning it, storing it
in a constructor -- is `CE2411`, because that would require ownership the borrow does not have.
`.clone()` is the escape:

```sushi
struct Wrapper:
    string inner

fn take(nom string s) ~:
    println(s)
    return Result.Ok(~)

fn main() i32:
    let Wrapper w = Wrapper(inner: "Mostly Harmless")

    # ERROR CE2411: cannot consume 'w.inner': another owner keeps this value
    # take(nom w.inner)

    take(nom w.inner.clone())  # OK: an independent copy
    println(w.inner)           # OK: w still owns and still has its value

    return Result.Ok(0)
```

A `let` behaves the same way for a source that reads through an owner: `let string x = w.inner`
binds `x` as a borrow of `w`, not an independent copy -- see
[Borrowed `let` Bindings](#borrowed-let-bindings) below.

### Taking a Field Out

A field read is a borrow, and that left one thing unspellable: handing a handle back **out** of the
value that holds it. `nom` marks the take:

```sushi
use <io/fs>
use <io/contracts>

struct Sink:
    File out
    string label

fn run() ~ | IoError:
    let File f = open("/tmp/report.txt", FileMode.Write())??
    let Sink s = Sink(f, "report")

    let File back = nom s.out    # the take: `back` owns the handle now
    back.write("Mostly Harmless\n")??
    back.close()??
    return Result.Ok(~)

fn main() i32:
    match run():
        Result.Ok(_) -> println("done")
        Result.Err(_) -> println("failed")
    return Result.Ok(0)
```

The marker is legal in a `let` initializer and in a `return`, one step off a bare name, and only
where that name is a local the function **owns** -- through a `peek`/`poke` parameter or a
`let`-borrow it is still `CE2411`, and so is a chain such as `nom a.b.c`. A field that owns nothing
is copied as it always was; the marker changes nothing there.

**A take spends the whole receiver.** There are no partial moves: what suppresses `s`'s own free is
the whole value and not one field, so the fields left behind are destroyed at the take and `s` is
finished. A later mention of it is `CE2405`. If the type declares `Drop`, that `drop()` does **not**
run -- a destructor is written for a value that goes away whole, so the method doing the take does
the finishing work itself:

```sushi
extend Sink into_inner(nom self) File:
    return nom self.out
```

### Writing an Array Element

`arr[i]` on the LEFT of a `:=` is the mirror of the read above. The read is a borrow; the
write is an **ownership sink**. Two things follow, and both are automatic:

- the element the write replaces is **freed first**, so a write in a loop does not leak;
- the new value is **consumed**, so an owned source is moved and a source that reads through
  an owner is `CE2411`.

```sushi
fn main() i32:
    let string[] words = from(["towel", "guide"])

    words[0] := "babel fish"       # the old "towel" is freed; the array owns the new value

    # ERROR CE2411: cannot consume 'words[1]': another owner keeps this value
    # words[0] := words[1]

    words[0] := words[1].clone()   # OK: an independent copy

    return Result.Ok(0)
```

An element can never be moved *out* of an array -- that is what the `CE2411` above is saying,
at every sink -- so an array owns every one of its elements for its whole life. That invariant
is what makes both the write above and the scope-exit destructor safe: each frees an element
that nothing else can have taken. (An element leaves only when the container *shrinks* past it,
as `arr.pop()`, `List@(T).pop()` and `List@(T).remove()` do -- each answers
`Maybe@(T)`, because a container that has shrunk to empty has nothing to hand over.)

The write must also be able to reach the owner, which is the [borrow](#references-and-borrowing)
question rather than the ownership one: it is rejected through a `peek` parameter (`CE2408`),
a `match`/`foreach` binding (`CE2414`), a method receiver without `poke self` (`CE2421`), an
unmarked parameter (`CE2422`), a `let` binding that borrows from an owner (`CE2426`), an
unbound chained receiver such as `o.get().items` (`CE2429`), and a constant (`CE2096`).

### Function Arguments

**A parameter is a borrow unless it says otherwise.** The caller keeps the value and frees it, so a
plain call leaves the argument usable:

```sushi
fn total(i32[] arr) i32:
    return Result.Ok(arr.len())

fn main() i32:
    let i32[] data = from([1, 2, 3])
    println(total(data).realise(-1))

    println(data.len())  # OK: data is still yours
    return Result.Ok(0)
```

To hand the value over, write **`nom`** on the parameter and again at the call site:

```sushi
fn eat(nom i32[] arr) i32:
    return Result.Ok(arr.len())
    # arr is freed here -- the callee is the owner

fn main() i32:
    let i32[] data = from([1, 2, 3])
    println(eat(nom data).realise(-1))

    # ERROR CE2405: cannot borrow moved variable 'data'
    # println(data.len())

    return Result.Ok(0)
```

The marker is written at **both** ends, or at neither. That is what keeps a consume visible where
the value is handed over: reading `f(s)` you know `s` survives, and reading `f(nom s)` you know it
does not. A marker on one end only is `CE2427`.

`.clone()` is how a caller hands over a value it wants to keep: `eat(nom data.clone())` gives the
callee an independent copy.

> **`main`'s `args`.** The `string[] args` parameter of `main` is a borrowed view of the process
> argument vector (its strings alias C `argv` memory), not a heap-owned array. It passes to an
> ordinary borrow parameter like anything else; handing it to a `nom` one is `CE2410`, because the
> callee would try to free `argv` and crash.

### The Four Modes

```sushi
fn f(string name) ~:          # borrow            -- caller frees; name stays usable
fn f(nom string name) ~:      # consume           -- callee frees; CE2405 after
fn f(peek string name) ~:     # borrow by pointer -- read only; caller frees
fn f(poke string name) ~:     # borrow by pointer -- read/write; caller frees
```

| | `string x` | `nom string x` | `peek string x` | `poke string x` |
|---|---|---|---|---|
| what crosses | the value | the value | a pointer | a pointer |
| who frees | caller | **callee** | caller | caller |
| callee may read | yes | yes | yes | yes |
| callee may write through it | no — `CE2422` | yes (its own copy) | no — `CE2408` | **yes, caller sees it** |
| callee may keep it | no — `CE2411` | **yes** | no — `CE2411` | no — `CE2411` |
| caller may use it after | yes | no — `CE2405` | yes | yes |
| how many at once | many | one | many | one, exclusive |

The default mode has no name of its own. It is *a borrow*: it does not pass the value.

A borrow and a `nom` cross the boundary as the same bytes -- for an owning type that is a small
descriptor whose data pointer aliases the caller's buffer. What differs is only who frees it. That
is why the mode has to be written down: nothing in the value itself says which side owns it.

`peek` and `poke` pass a **pointer** instead, which is what makes `poke` able to write back to the
caller's value, and what makes either one free for a large fixed array or plain struct.

The rule and its reasoning are [docs/design/borrow-model.md](design/borrow-model.md).

### Solution: Borrow by Pointer

```sushi
fn borrow(peek i32[] arr) ~:
    println("Length: {arr.len()}")
    # arr is not owned here, so it is not freed
    return Result.Ok(~)

fn main() i32:
    let i32[] data = from([1, 2, 3])
    borrow(peek data)  # Pass by read-only reference

    println(data.len())  # OK: data still valid

    return Result.Ok(0)
```

### Solution: Clone

```sushi
fn eat(nom i32[] a) ~:
    println("ate {a.len()} elements")
    return Result.Ok(~)

fn main() i32:
    let i32[] original = from([1, 2, 3])
    let i32[] copy = original.clone()  # Deep copy

    eat(nom copy)  # hand the copy over

    println(original.len())  # OK: original still valid

    return Result.Ok(0)
```

## References and Borrowing

References pass a **pointer** instead of the value, so the callee reads and writes the caller's
storage directly. Sushi has two by-pointer modes:

- **`peek T`** - Read-only borrow (multiple allowed)
- **`poke T`** - Read-write borrow (exclusive access)

An unmarked parameter is *also* a borrow -- see [The Four Modes](#the-four-modes) -- but it passes
the value rather than a pointer, so the callee cannot write back through it. Reach for `peek` or
`poke` when the callee must write (`poke`), or when the value is a large fixed array or plain struct
that would be expensive to copy.

The design document for the borrow mechanisms — where a reference type may appear, the six
ways a borrow is created, and the diagnostic for each rule — is
[docs/design/borrowing.md](design/borrowing.md).

### Read-Only References (peek)

Use `peek` when you only need to read data:

```sushi
fn add_one(peek i32 x) i32:
    let i32 val = x
    return Result.Ok(val + 1)

fn main() i32:
    let i32 num = 42

    let i32 result = add_one(peek num).realise(0)

    println("Original: {num}")    # OK: num not moved
    println("Result: {result}")   # 43

    return Result.Ok(0)
```

### Mutable References (poke)

Use `poke` when you need to modify the borrowed value:

```sushi
fn increment(poke i32 counter) ~:
    counter := counter + 1
    return Result.Ok(~)

fn main() i32:
    let i32 count = 0

    increment(poke count)
    increment(poke count)

    println("Count: {count}")  # 2

    return Result.Ok(0)
```

### Borrowing Struct Fields

```sushi
struct Config:
    i32 port
    string host

fn update_port(poke i32 p) ~:
    p := p + 100
    return Result.Ok(~)

fn main() i32:
    let Config cfg = Config(port: 8080, host: "localhost")

    # Borrow struct field directly (mutable)
    update_port(poke cfg.port)

    println("Port: {cfg.port}")  # 8180

    return Result.Ok(0)
```

### Nested Struct Fields

```sushi
struct Point:
    i32 x
    i32 y

struct Rectangle:
    Point top_left
    Point bottom_right

fn move_x(poke i32 coord) ~:
    coord := coord + 10
    return Result.Ok(~)

fn main() i32:
    let Rectangle rect = Rectangle(
        top_left: Point(x: 0, y: 0),
        bottom_right: Point(x: 10, y: 10)
    )

    # Borrow nested field (mutable)
    move_x(poke rect.top_left.x)

    println("X: {rect.top_left.x}")  # 10

    return Result.Ok(0)
```

### Array References

```sushi
fn sum_array(peek i32[] numbers) i32:
    let i32 total = 0
    foreach(n in numbers.iter()):
        total := total + n
    return Result.Ok(total)

fn main() i32:
    let i32[] data = from([1, 2, 3, 4, 5])

    let i32 sum = sum_array(peek data).realise(0)  # Zero-cost borrow

    println("Sum: {sum}")
    println("Array: {data.len()}")  # data still valid

    return Result.Ok(0)
```

### Borrow Rules

The compiler enforces these rules at compile time:

1. **Multiple `peek` borrows allowed**

```sushi
fn read_both(peek i32 a, peek i32 b) i32:
    return Result.Ok(a + b)

fn main() i32:
    let i32 x = 42
    # Multiple peek borrows of the same variable OK
    let i32 sum = read_both(peek x, peek x).realise(0)
    println(sum)  # 84
    return Result.Ok(0)
```

2. **Only one `poke` borrow at a time**

```sushi
fn main() i32:
    let i32 x = 42
    # ERROR CE2403: x already has an active poke borrow
    # bad_func(poke x, poke x)
    return Result.Ok(0)
```

3. **Cannot mix `peek` and `poke`**

```sushi
fn main() i32:
    let i32 x = 42
    # ERROR CE2407: cannot have peek and poke borrows simultaneously
    # mixed_func(peek x, poke x)
    return Result.Ok(0)
```

4. **`poke` coerces to `peek`**

```sushi
fn read_only(peek i32 x) i32:
    return Result.Ok(x)

fn main() i32:
    let i32 x = 42
    # OK: poke can be passed where peek is expected
    let i32 val = read_only(poke x).realise(0)
    return Result.Ok(0)
```

5. **Cannot move/rebind while borrowed**

```sushi
fn use_ref(poke i32 x) ~:
    x := x + 1
    return Result.Ok(~)

fn main() i32:
    let i32 num = 42
    use_ref(poke num)
    # ERROR CE2401: Cannot rebind while borrowed
    # num := 50
    return Result.Ok(0)
```

6. **Cannot borrow temporaries**

```sushi
# ERROR: Cannot borrow temporary
# let i32 x = add_one(peek (5 + 3))

# OK: Use variable
let i32 temp = 5 + 3
let i32 x = add_one(peek temp).realise(0)
```

### Borrowed `let` Bindings

A `let` that reads through an owner -- `s.field`, `arr[i]`, `own.get()`, `list.get(i)??` -- **binds
a borrow**, not a copy: no allocation happens, and the binding does not own what it points to.

```sushi
struct Wrapper:
    string inner

fn main() i32:
    let Wrapper w = Wrapper(inner: "hi")
    let string x = w.inner  # x borrows w.inner -- no copy, no error
    println(x)
    println(w.inner)  # w still owns it
    return Result.Ok(0)
```

The borrow lasts to the end of the block that declared it. Two things are checked while it is live:

1. **Mutating, freeing, or rebinding the owner is `CE2412`**, reported at the *use* of the borrowed
   binding that follows the change (not at the change itself -- the borrow is non-lexical):

```sushi
struct Wrapper:
    string inner

fn main() i32:
    let Wrapper w = Wrapper(inner: "hi")
    let string x = w.inner

    # ERROR CE2412: cannot mutate 'w' while 'x' borrows from it
    # w := Wrapper(inner: "bye")
    # println(x)

    println(x)  # OK as long as w is not touched while x is still used
    return Result.Ok(0)
```

2. **Consuming the binding itself is `CE2411`**, exactly like consuming a `match`/`foreach` binding
   or a direct field read -- `.clone()` is the escape (see
   [Reading Through a Borrow, Without Consuming](#reading-through-a-borrow-without-consuming)).

**A `let` may also declare a reference *type*** (#409): `let poke T x = <place>` binds a
pointer INTO the owner's storage, so a write through it reaches the owner -- the zero-copy
mutation path a bare `Own@(T)` local had none of -- and `let peek T x = <place>` is the
read-only twin. The binding is block-scoped and freezes its owner exactly as the implicit
borrow above does (`CE2412`); one `poke` binding of an owner at a time (`CE2403`), a `peek`
beside a live `poke` is `CE2407`, a write through a `peek` binding is `CE2408`, and
consuming the binding is `CE2411` as before. The place must have an address: a call
result is `CE2404`, a constant is `CE2400`.

```sushi
struct Wrapper:
    i32[] items

fn main() i32:
    let Own@(Wrapper) w = Own.alloc(Wrapper(from([])))

    if (true):
        let poke Wrapper inner = w.get()   # a pointer into the cell
        inner.items.push(4)                # reaches the Own's payload

    let peek Wrapper view = w.get()        # read-only; `inner`'s block has ended
    println("{view.items.len()}")          # 1
    return Result.Ok(0)
```

## Recursive Types

A type may refer to itself through any **indirection** — `Own@(T)`, `Maybe@(Own@(T))`,
`List@(T)`, or a dynamic `T[]`. All of them can be declared, constructed, read, nested and
dropped, and RAII frees every level exactly once.

A by-value self-reference has no finite size and is rejected with **CE2095**; a *fixed* `T[N]`
counts as by-value, a *dynamic* `T[]` does not.

### Recursion through a container

The container owns its elements, so a tree needs no explicit heap allocation:

```sushi
struct Tree:
    i32 value
    List@(Tree) kids

fn main() i32:
    let List@(Tree) kids = List.new()
    kids.push(Tree(2, List.new()))
    kids.push(Tree(3, List.new()))

    # The List is moved into the Tree, which now owns it
    let Tree root = Tree(1, kids)
    println("root: {root.value}, kids: {root.kids.len()}")

    let Tree first = root.kids.get(0).realise(Tree(0, List.new()))
    println("first child: {first.value}")

    return Result.Ok(0)
```

`Node[] kids` works the same way, built with `from([...])`. When `root` goes out of scope its
destructor walks the children, recursing into each one's own children, and frees every buffer.

## Own@(T) for Heap Allocation

`Own@(T)` provides explicit heap allocation for recursive types, and is the right choice for a
single owned successor rather than a collection of them.

### Creating Owned Values

```sushi
enum IntList:
    Nil
    Cons(i32, Own@(IntList))

fn main() i32:
    # Create owned nodes on the heap
    let Own@(IntList) tail = Own.alloc(IntList.Nil())
    let Own@(IntList) node = Own.alloc(IntList.Cons(2, tail))
    let IntList head = IntList.Cons(1, node)

    match head:
        IntList.Cons(value, _) ->
            println("Head: {value}")
        IntList.Nil ->
            println("Empty")

    return Result.Ok(0)
```

### Accessing Owned Values

```sushi
struct Node:
    i32 value

fn main() i32:
    let Own@(Node) owned = Own.alloc(Node(value: 42))

    # Dereference the owned pointer
    let Node node = owned.get()
    println("Value: {node.value}")

    return Result.Ok(0)
```

### Destroying Owned Values

```sushi
struct Node:
    i32 value

fn main() i32:
    let Own@(Node) owned = Own.alloc(Node(value: 42))

    # Manually destroy
    owned.destroy()

    return Result.Ok(0)
```

**Note:** Owned values are automatically cleaned up via RAII if not manually destroyed.

### Ownership Semantics

- **`alloc(value)` takes ownership.** When `value` is itself an owning value (an `Own@(T)`,
  a `List@(T)`, a dynamic array, a `string`, or a struct with owned fields), the source variable is
  *moved* into the new `Own` and may not be used afterwards (use-after-move is `CE2405`).
  Primitives are copied, so passing an `i32` variable leaves it usable.
- **`get()` reads through the pointer and hands back a borrow.** `get()` is a dereference: it
  returns a *view* of the payload, which the `Own` keeps owning and still frees. Reading it is
  free -- a `let x = own.get()` binds `x` as a borrow of `own`, exactly like a struct-field read
  (see [Borrowed `let` Bindings](#borrowed-let-bindings)). *Consuming* that view at a real
  ownership sink -- a `nom` argument, a constructor field, an enum payload, an indexed
  assignment `arr[i] := ...`, a `return` -- is **`CE2411`**, with `.clone()` as the escape.

```sushi
fn main() i32:
    let Own@(i32) inner = Own.alloc(42)
    let Own@(Own@(i32)) outer = Own.alloc(inner)  # inner is moved into outer
    let Own@(i32) copied = outer.get()            # copied BORROWS from outer; no allocation
    let i32 value = copied.get()                  # reading through a borrow is free
    println(value)                                # 42
    return Result.Ok(0)
```

  `copied` above does not own an independent copy of `inner` -- it is a live borrow of `outer`, so
  mutating or freeing `outer` while `copied` is in scope would be `CE2412`, and handing `copied`
  itself to a `nom` parameter would be `CE2411`:

```sushi
fn sink(nom Own@(i32) x) ~:
    println(x.get())
    return Result.Ok(~)

fn main() i32:
    let Own@(i32) inner = Own.alloc(42)
    let Own@(Own@(i32)) outer = Own.alloc(inner)
    let Own@(i32) copied = outer.get()

    # ERROR CE2411: cannot consume 'copied': another owner keeps this value
    # sink(nom copied)

    sink(nom copied.clone())  # OK: an independent copy
    return Result.Ok(0)
```

  This is what makes nested owners such as `Own@(Own@(T))` safe without an implicit copy at every
  `get()`: the borrow checker tracks exactly how long `copied` may live relative to `outer`, and
  `.clone()` is there for the case that genuinely needs an independent value.

## Manual Memory Management

When RAII isn't sufficient, use manual cleanup.

### .free() - Clear and Keep Usable

```sushi
fn main() i32:
    let i32[] arr = from([1, 2, 3, 4, 5])

    # Free memory, reset to empty
    arr.free()
    println("After free: {arr.len()}")  # 0

    # Can still use
    arr.push(10)
    println("After push: {arr.len()}")  # 1

    return Result.Ok(0)
```

### .destroy() - Free and Invalidate

```sushi
fn main() i32:
    let i32[] arr = from([1, 2, 3, 4, 5])

    # Destroy makes variable unusable
    arr.destroy()

    # ERROR CE2406: use of destroyed variable 'arr'
    # println(arr.len())

    return Result.Ok(0)
```

### When to Use Manual Cleanup

**Use `.free()`:**
- Clearing large collections
- Reusing variables in long-running functions
- Reducing memory footprint mid-function

**Use `.destroy()`:**
- Early cleanup before scope exit
- Clear ownership transfer intention
- Debug builds (catch use-after-free)

**Use RAII (default):**
- Most cases
- Short-lived variables
- Automatic cleanup at scope exit

### HashMap Memory Management

```sushi
use <collections/hashmap>

fn main() i32:
    let HashMap@(string, i32) map = HashMap.new()

    map.insert("a", 1)
    map.insert("b", 2)

    # Free all entries, reset to capacity 16
    map.free()

    # Still usable
    map.insert("c", 3)

    # Or destroy completely
    map.destroy()
    # map.len()  # ERROR CE2406

    return Result.Ok(0)
```

## Best Practices

### 1. Prefer RAII

```sushi
# Good: Automatic cleanup
fn process() ~:
    let i32[] data = from([1, 2, 3])
    # ... use data ...
    return Result.Ok(~)  # data freed automatically
```

### 2. Use References for Large Data

```sushi
# Good: Zero-cost read-only borrow
fn sum(peek i32[] numbers) i32:
    let i32 total = 0
    foreach(n in numbers.iter()):
        total := total + n
    return Result.Ok(total)
```

### 3. Clone Only When Necessary

```sushi
# Clone only if you need independent copy
let i32[] original = from([1, 2, 3])
let i32[] copy = original.clone()  # Explicit cost
```

### 4. Return by Value

```sushi
# Good: Caller takes ownership
fn create_array() i32[]:
    let i32[] arr = from([1, 2, 3])
    return Result.Ok(arr)  # Ownership moved to caller
```

### 5. Let the Signature Document the Transfer

The mode says who frees, so there is nothing left for a comment to claim:

```sushi
fn consume(nom i32[] arr) ~:
    # arr is freed at the end of this function -- `nom` says so, at both ends
    return Result.Ok(~)
```

## Memory Safety Guarantees

Sushi prevents common memory errors at compile time:

- ✅ No use-after-free (move checking)
- ✅ No double-free (move checking)
- ✅ No use-after-destroy (CE2406)
- ✅ No data races (single borrow rule)
- ✅ No dangling references (borrow checking)

---

**See also:**
- [Language Reference](language-reference.md) - Complete syntax
- [Error Handling](error-handling.md) - RAII with error propagation
- [Examples](examples/README.md) - Memory management patterns
