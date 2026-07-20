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
3. **Move semantics** - Clear ownership transfer (dynamic arrays only)
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

Move-ness is **compositional**: a value moves iff it transitively contains an owning resource --
a **dynamic array (`T[]`), `List@(T)`, `Own@(T)`, a capturing closure, or any struct/enum/fixed
array holding one of those**. Passing such a value by value, binding it to a new name, putting it
in a constructor field or array literal, or capturing it in a closure transfers ownership; the
source is consumed and using it afterward is a use-after-move error (`CE2405`).

Everything else **copies**: primitives, `string`, and *plain-data or string-only* composites (a
`struct { string name; i32 id }` stays a copy type -- passing it by value clones the string field
and the source stays usable). This is Rust's `Copy` tier, derived automatically from a type's shape
rather than opted into.

Every struct and enum also gets an auto-derived **`.clone()`** -- the single explicit way to copy an
owning value: `consume(buf.clone())` hands the callee an independent copy while `buf` stays yours.

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

### What Copies

**Primitives, strings, and string-only / plain-data composites:**
```sushi
struct Named:
    string name
    i32 id

fn main() i32:
    let i32 x = 42
    let i32 y = x  # x copied to y
    println(x)  # OK: x still valid

    let string s1 = "hello"
    let string s2 = s1  # s1 copied to s2
    println(s1)  # OK: s1 still valid

    # A struct whose only owning content is a string is a COPY type:
    let Named a = Named(name: "hi", id: 1)
    let Named b = a  # a copied to b (the string field is cloned)
    println(a.name)  # OK: a still valid

    return Result.Ok(0)
```

To read from a struct field without consuming its owner, note that a **field read
copies** (`take(wrapper.inner)` deep-copies `inner`; `wrapper` stays usable). Only a bare owning
variable moves. There are no partial moves and no local borrow bindings, so reaching through an
owner (`s.field`) still copies -- clone or borrow the whole value to avoid the copy.

### Function Arguments

```sushi
fn consume(i32[] arr) ~:
    println("Length: {arr.len()}")
    # arr automatically freed here (the callee now owns it)
    return Result.Ok(~)

fn main() i32:
    let i32[] data = from([1, 2, 3])
    consume(data)  # data moved into function

    # ERROR CE2405: cannot borrow moved variable 'data'
    # println(data.len())

    return Result.Ok(0)
```

The same holds for `List@(T)`, `Own@(T)`, and **owning struct/enum** value parameters: a bare owning
argument is moved into the callee, which frees it exactly once at scope exit. To pass an owning value
without giving it up, borrow it (`&peek` / `&poke`) or pass an explicit `.clone()`.

> **`main`'s `args`.** The `string[] args` parameter of `main` is a borrowed view of the process
> argument vector (its strings alias C `argv` memory), not a heap-owned array. Do not move it by
> value into a helper -- borrow it (`fn run(&peek string[] args)`), or the callee would try to free
> `argv` and crash.

### Solution: Use References

```sushi
fn borrow(&peek i32[] arr) ~:
    println("Length: {arr.len()}")
    # arr not owned, so not freed
    return Result.Ok(~)

fn main() i32:
    let i32[] data = from([1, 2, 3])
    borrow(&peek data)  # Pass by read-only reference

    println(data.len())  # OK: data still valid

    return Result.Ok(0)
```

### Solution: Clone

```sushi
fn main() i32:
    let i32[] original = from([1, 2, 3])
    let i32[] copy = original.clone()  # Deep copy

    consume(copy)  # Move copy

    println(original.len())  # OK: original still valid

    return Result.Ok(0)
```

## References and Borrowing

References allow temporary access without transferring ownership. Sushi has two borrow modes:

- **`&peek T`** - Read-only borrow (multiple allowed)
- **`&poke T`** - Read-write borrow (exclusive access)

### Read-Only References (&peek)

Use `&peek` when you only need to read data:

```sushi
fn add_one(&peek i32 x) i32:
    let i32 val = x
    return Result.Ok(val + 1)

fn main() i32:
    let i32 num = 42

    let i32 result = add_one(&peek num).realise(0)

    println("Original: {num}")    # OK: num not moved
    println("Result: {result}")   # 43

    return Result.Ok(0)
```

### Mutable References (&poke)

Use `&poke` when you need to modify the borrowed value:

```sushi
fn increment(&poke i32 counter) ~:
    counter := counter + 1
    return Result.Ok(~)

fn main() i32:
    let i32 count = 0

    increment(&poke count)
    increment(&poke count)

    println("Count: {count}")  # 2

    return Result.Ok(0)
```

### Borrowing Struct Fields

```sushi
struct Config:
    i32 port
    string host

fn update_port(&poke i32 p) ~:
    p := p + 100
    return Result.Ok(~)

fn main() i32:
    let Config cfg = Config(port: 8080, host: "localhost")

    # Borrow struct field directly (mutable)
    update_port(&poke cfg.port)

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

fn move_x(&poke i32 coord) ~:
    coord := coord + 10
    return Result.Ok(~)

fn main() i32:
    let Rectangle rect = Rectangle(
        top_left: Point(x: 0, y: 0),
        bottom_right: Point(x: 10, y: 10)
    )

    # Borrow nested field (mutable)
    move_x(&poke rect.top_left.x)

    println("X: {rect.top_left.x}")  # 10

    return Result.Ok(0)
```

### Array References

```sushi
fn sum_array(&peek i32[] numbers) i32:
    let i32 total = 0
    foreach(n in numbers.iter()):
        total := total + n
    return Result.Ok(total)

fn main() i32:
    let i32[] data = from([1, 2, 3, 4, 5])

    let i32 sum = sum_array(&peek data).realise(0)  # Zero-cost borrow

    println("Sum: {sum}")
    println("Array: {data.len()}")  # data still valid

    return Result.Ok(0)
```

### Borrow Rules

The compiler enforces these rules at compile time:

1. **Multiple `&peek` borrows allowed**

```sushi
fn read_both(&peek i32 a, &peek i32 b) i32:
    return Result.Ok(a + b)

fn main() i32:
    let i32 x = 42
    # Multiple &peek borrows of the same variable OK
    let i32 sum = read_both(&peek x, &peek x).realise(0)
    println(sum)  # 84
    return Result.Ok(0)
```

2. **Only one `&poke` borrow at a time**

```sushi
fn main() i32:
    let i32 x = 42
    # ERROR CE2403: x already has an active &poke borrow
    # bad_func(&poke x, &poke x)
    return Result.Ok(0)
```

3. **Cannot mix `&peek` and `&poke`**

```sushi
fn main() i32:
    let i32 x = 42
    # ERROR CE2407: cannot have &peek and &poke borrows simultaneously
    # mixed_func(&peek x, &poke x)
    return Result.Ok(0)
```

4. **`&poke` coerces to `&peek`**

```sushi
fn read_only(&peek i32 x) i32:
    return Result.Ok(x)

fn main() i32:
    let i32 x = 42
    # OK: &poke can be passed where &peek is expected
    let i32 val = read_only(&poke x).realise(0)
    return Result.Ok(0)
```

5. **Cannot move/rebind while borrowed**

```sushi
fn use_ref(&poke i32 x) ~:
    x := x + 1
    return Result.Ok(~)

fn main() i32:
    let i32 num = 42
    use_ref(&poke num)
    # ERROR CE2401: Cannot rebind while borrowed
    # num := 50
    return Result.Ok(0)
```

6. **Cannot borrow temporaries**

```sushi
# ERROR: Cannot borrow temporary
# let i32 x = add_one(&peek (5 + 3))

# OK: Use variable
let i32 temp = 5 + 3
let i32 x = add_one(&peek temp).realise(0)
```

## Own@(T) for Heap Allocation

`Own@(T)` provides explicit heap allocation for recursive types.

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
  a `List@(T)`, a dynamic array, or a struct with owned fields), the source variable is
  *moved* into the new `Own` and may not be used afterwards (use-after-move is `CE2405`).
  Primitives are copied, so passing an `i32` variable leaves it usable.
- **`get()` borrows.** It yields a non-owning view of the payload; the binding is never a
  second owner, so the container remains responsible for freeing it. This makes nested
  owners such as `Own@(Own@(T))` safe: the outer owner frees every level exactly once.

```sushi
fn main() i32:
    let Own@(i32) inner = Own.alloc(42)
    let Own@(Own@(i32)) outer = Own.alloc(inner)  # inner is moved into outer
    let Own@(i32) borrowed = outer.get()          # borrow, not a new owner
    let i32 value = borrowed.get()
    println(value)                               # 42
    return Result.Ok(0)
```

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
fn sum(&peek i32[] numbers) i32:
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

### 5. Document Ownership Transfer

```sushi
# Takes ownership of input array
fn consume(i32[] arr) ~:
    # arr freed at end of function
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
