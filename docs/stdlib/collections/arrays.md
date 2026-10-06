# Array Methods

[← Back to Standard Library](../../standard-library.md)

Built-in methods for fixed-size and dynamic arrays.

## Import

Arrays are built-in types and require no import. For dynamic array construction from literals, arrays are available by default.

```sushi
let i32[5] fixed = [1, 2, 3, 4, 5]  # Fixed-size array
let i32[] dynamic = from([1, 2, 3])  # Dynamic array
```

The array methods need no import. A string method in an example (for example
`.upper()`) needs `use <collections/strings>` in the unit that calls it.

## Array Literals

An element of an array literal can be a plain value, a REPEATED value or a RANGE, and the
three forms mix in one literal:

```sushi
let i32[4] zeros = [0; 4]            # [0, 0, 0, 0]
let i32[5] counts = [0..=4]          # [0, 1, 2, 3, 4]
let i32[6] mixed = [9, 0; 3, 1..3]   # [9, 0, 0, 0, 1, 2]
```

A repeat count and a range bound are `i32` positions. Where the count must be readable
depends on the position: a fixed array and a `const` need a count that the compiler can
read ([CE2017](../../error-catalog.md#ce2017) / [CE2019](../../error-catalog.md#ce2019)), but `from()` takes any `i32` expression, because a `T[]` carries
its length:

```sushi
let i32 n = 3
let i32[] slots = from([0; n])       # length 3
let i32[] index = from([0..n])       # [0, 1, 2]
```

A repeated value is a borrow, and each slot takes its own copy, so an owning element
type (for example `string`) is allowed.

## Overview

Sushi provides two array types:
- **Fixed arrays** (`T[N]`): Stack-allocated, compile-time size
- **Dynamic arrays** (`T[]`): Heap-allocated, runtime size

Both types share common methods, while dynamic arrays have additional memory management methods.
Both are mutable in place: `arr[i] := v` writes one element, and `.fill()` / `.reverse()` write
all of them. Only the LENGTH of a fixed array is immutable.

A dynamic array is built by `from([...])` or `new()`. An empty `from([])` and a `new()` spell
no element type, so each takes the type of its position: a `let`, a struct field, a payload,
a parameter, a `.realise()` default, or an extension's bare `return`. `r.realise(from([]))`
over a `Result@(u8[], E)` is a `u8[]`.

## Every receiver shape

A built-in method works the same through every receiver: a local, a struct field, a nested
field, an array element, and a `peek` or `poke` parameter.

```sushi
struct Buf:
    i32[4] slots

fn zap(poke i32[4] arr) ~:
    arr.fill(0)                # reaches the caller's array

extend Buf clear(poke self) ~:
    self.slots.fill(0)         # reaches the caller's struct
    return ~

let Buf b = Buf(slots: [1, 2, 3, 4])
b.slots.fill(9)                # reaches the field
println(b.slots.len())
```

A method that WRITES -- `.fill()`, `.reverse()` -- needs a receiver it can reach. A constant
is rejected with **[CE2096](../../error-catalog.md#ce2096)**, and the read-only receivers each have their own code: a `peek`
parameter is [CE2408](../../error-catalog.md#ce2408), a `match` or `foreach` binding is [CE2414](../../error-catalog.md#ce2414), a receiver without `poke self`
is [CE2421](../../error-catalog.md#ce2421), an unmarked parameter is [CE2422](../../error-catalog.md#ce2422), a borrowing `let` is [CE2426](../../error-catalog.md#ce2426), and an unbound
chained receiver is [CE2429](../../error-catalog.md#ce2429).

A method that only READS -- `.len()`, `.get()`, `.iter()`, `.hash()`, `.clone()` -- accepts
any receiver, a constant included.

## Common Methods (Fixed and Dynamic)

### `.len() -> i32`

Get number of elements.

```sushi
let i32[5] arr = [1, 2, 3, 4, 5]
println(arr.len())  # 5
```

### `.get(i32 index) -> Maybe@(T)`

Bounds-checked access (returns `Maybe@(T)`).

```sushi
match arr.get(2):
    Maybe.Some(value) ->
        println("Value: {value}")
    Maybe.None() ->
        println("Index out of bounds")

# Or use error propagation
let i32 value = arr.get(2).or_err(nom StdError.Error)??
```

**Note:** Direct indexing `arr[index]` is also available but throws [RE2020](../../error-catalog.md#re2020) runtime error on out-of-bounds.

### `.first() -> Maybe@(T)` and `.last() -> Maybe@(T)`

`.get()` with the index built in: 0, and `len - 1`. An empty array answers
`Maybe.None()`. Both are READS, like `.get()`: the array keeps the element.

```sushi
let i32[] arr = from([10, 20, 30])
let i32 head = arr.first().realise(-1)   # 10
let i32 tail = arr.last().realise(-1)    # 30

let i32[] empty = from([])
empty.first().is_none()                  # true
```

### `.contains(T value) -> bool` and `.index_of(T value) -> Maybe@(i32)`

A linear search with the `==` the language defines, so the element type must have
equality: the numeric types, `bool`, `string`, or a struct or an enum with a derived or
implemented `Eq`. A closure element, or a struct that holds one, is [CE2100](../../error-catalog.md#ce2100). An `Eq`
implementation on the element is the override, and the search reads it.
`.index_of()` answers the FIRST match, left to right. The needle is a borrow.

```sushi
let string[] words = from(["alpha", "beta", "gamma"])
words.contains("beta")                    # true
let i32 at = words.index_of("beta").realise(-1)   # 1
words.index_of("delta").is_none()         # true
```

### `.iter() -> Iterator@(T)`

Create iterator for foreach loops.

```sushi
foreach(n in arr.iter()):
    println(n)
```

### `.hash() -> u64`

Compute hash of array contents.

```sushi
let u64 h = arr.hash()
```

A nested array hashes level by level. A fixed array and a dynamic array of the same
elements hash alike, at each level, so `[[1, 2], [3, 4]]` as an `i32[2][2]` and as an
`i32[][]` give one hash.

**Limitation:** The element type must have a hash. An array whose element has no hash
(today, `ptr[]`) is [CE0052](../../error-catalog.md#ce0052).

### Printing

An array goes into an interpolation hole and into `print` / `println` when its element has
a string form. It prints its elements in brackets, in the form a struct that holds it
prints: a string element in quotes, a nested array in its own brackets, and `[]` when it
is empty. A `u8[]` prints as numbers; `.to_string()` gives the text.

```sushi
fn main() i32:
    let i32[] xs = from([1, 2, 3])
    let i32[2][2] grid = [[1, 2], [3, 4]]
    println("{xs}")                     # [1, 2, 3]
    println(grid)                       # [[1, 2], [3, 4]]
    println(from(["a, b", "c"]))        # ["a, b", "c"]
    return 0
```

An element with no string form, such as a function value, is [CE2035](../../error-catalog.md#ce2035) in a hole and [CE2115](../../error-catalog.md#ce2115)
in `println`, and a note names the element type. An array prints, but it does not compare
at the top level: `==` and `<` on two arrays are still [CE2514](../../error-catalog.md#ce2514).

### `arr[index] := value`

Write one element, in place. Not a method -- it is the assignment form of `arr[index]`,
and it works on a fixed array and a dynamic array alike, for every element type.

```sushi
let i32[3] scores = [1, 2, 3]
scores[0] := 42            # scores is now [42, 2, 3]

let i32 i = 2
scores[i] := 99            # the index may be any i32 expression
```

The index is bounds-checked exactly like a read: an index past the end aborts with
**[RE2020](../../error-catalog.md#re2020)** at run time, and an index the compiler can read (a literal, a named constant, or
an expression of them) is rejected at compile time -- **[CE2012](../../error-catalog.md#ce2012)** past
the end of a fixed array, **[CE2056](../../error-catalog.md#ce2056)** if it is negative.

If the element type owns heap -- a `string`, a struct with a dynamic-array field -- the
element that the write replaces is freed first, so a write in a loop does not leak:

```sushi
let string[] words = from(["towel", "guide"])
words[0] := "babel fish"   # the old "towel" is freed; the array owns the new value
```

An indexed assignment takes ownership of the value, so the ordinary ownership rules apply.
An owned source is MOVED into the array, and using it afterwards is **[CE2405](../../error-catalog.md#ce2405)**. A value read
out of a container is a BORROW, so storing it in another element is **[CE2411](../../error-catalog.md#ce2411)** -- take an
independent value with `.clone()`:

```sushi
words[0] := words[1]           # ERROR CE2411: another owner keeps this value
words[0] := words[1].clone()   # correct
```

You may write only where the write can reach the owner. The compiler rejects the rest:

| receiver | code |
|---|---|
| a `peek` parameter | [CE2408](../../error-catalog.md#ce2408) |
| a `match` / `foreach` binding | [CE2414](../../error-catalog.md#ce2414) |
| the receiver of a method without `poke self` | [CE2421](../../error-catalog.md#ce2421) |
| an unmarked parameter | [CE2422](../../error-catalog.md#ce2422) |
| a `let` binding that borrows from an owner | [CE2426](../../error-catalog.md#ce2426) |
| an unbound chained receiver (`o.get().items`) | [CE2429](../../error-catalog.md#ce2429) |
| a constant | [CE2096](../../error-catalog.md#ce2096) |

A `poke` parameter, a `nom` parameter and a `poke self` receiver are all writable:

```sushi
fn set_first(poke i32[] numbers, i32 value) ~:
    numbers[0] := value        # reaches the caller's array
```

### `.fill(T value) -> ~`

Fill all elements with value (in-place).

```sushi
arr.fill(0)  # All elements become 0
```

The argument is a **borrow**, which makes `fill` the one container write that does not
consume. Every other one -- `.push()`, an array literal element, `arr[i] := v` -- takes
ownership by position. `fill` cannot, because it has N slots to satisfy and one value.

Each slot therefore takes its own deep copy, and the value stays yours:

```sushi
let string towel = "mostly harmless".upper()
let string[] a = from(["x", "y"])
let string[] b = from(["p", "q", "r"])

a.fill(towel)                  # two copies
b.fill(towel)                  # three more
println(towel)                 # and the source is still usable
```

**The value may not be a slot of the array it fills** (**[CE2430](../../error-catalog.md#ce2430)**), when the element type
owns a resource. Each slot destroys what it held before it stores its copy, so
`a.fill(a[0])` would destroy slot 0 and then copy freed storage into every later slot. An
index reads as any slot, and a get-out (`a.get(0).or_err(nom e)??`) is refused the same way. Take an
independent value first: `a.fill(a[0].clone())`. A plain element type is a copy and stays
legal: `b.fill(b[2])` on an `i32[]` is fine.

A `let` binding or a `match` payload binding of a slot is also a borrow of the array.
`let string first = a[0]` then `a.fill(first)` is **[CE2412](../../error-catalog.md#ce2412)**, because `fill` changes `a`
while `first` borrows from it. Clone the value first here too.

An owning element type costs one allocation per slot. Use `.fill()` on a large array of
`string` or another owning type only when you mean that. A plain element type -- `i32`,
`bool`, `f64`, a struct of only those -- copies nothing, because a shallow store of a
plain value **is** the value.

Filling an array that already holds owning elements destroys what each slot held, so
nothing leaks.

### `.reverse() -> ~`

Reverse array elements (in-place).

```sushi
let i32[5] arr = [1, 2, 3, 4, 5]
arr.reverse()  # [5, 4, 3, 2, 1]
```

### `.clone() -> T[N]` or `T[]`

Deep copy of the array. It works on a fixed array and on a dynamic array, and the copy has
the type of the receiver.

```sushi
let i32[] copy = arr.clone()
let i32[3] f = [1, 2, 3]
let i32[3] g = f.clone()
```

## Dynamic Array Only

### `.push(T element) -> ~`

Append element to end (grows array).

```sushi
let i32[] arr = from([1, 2, 3])
arr.push(42)
# arr is now [1, 2, 3, 42]
```

### `.pop() -> Maybe@(T)`

Remove and return the last element. An empty array has no last element, so it answers
`Maybe.None()` rather than inventing a value — the same shape `.get()` and
`List@(T).pop()` use.

```sushi
match arr.pop():
    Maybe.Some(last) -> println("Popped: {last}")
    Maybe.None() -> println("nothing to pop")

let i32 last = arr.pop().realise(-1)   # or a default
```

### `.insert(i32 index, T element) -> Result@(~, StdError)`

Put the element at `index` and move the elements from `index` on one slot to the right.
`0 <= index <= len` is `Ok`, and `index == len` appends. Any other index is `Err`
(`StdError.Error`), and the array does not change. This is the `List@(T).insert()`
contract.

The element is CONSUMED, as `.push()` consumes it. The insert takes it BEFORE it checks the
index, so a refused insert destroys the element; nothing leaks. A full buffer grows by
doubling first. A fixed array cannot grow, so a `T[N]` receiver is **[CE2023](../../error-catalog.md#ce2023)**, the same
refusal as `.push()`.

```sushi
fn fill(poke i32[] arr) ~ | StdError:
    arr.insert(1, 2)??               # [1, 2, 3]
    arr.insert(arr.len(), 4)??       # [1, 2, 3, 4]: an append
    return Result.Ok(~)

fn main() i32:
    let i32[] arr = from([1, 3])
    let bool done = fill(poke arr).is_ok()
    match arr.insert(9, 0):          # index past len
        Result.Ok(_) -> println("ok")
        Result.Err(_) -> println("refused: {done} {arr.len()}")
    return 0
```

### `.remove(i32 index) -> Maybe@(T)`

Take the element at `index` out, and move the elements after it one slot to the left.
`Maybe.Some(element)` hands the element's ownership to the caller. An index out of range
answers `Maybe.None()`, and nothing moves. This is the `List@(T).remove()` contract, and
the same `Maybe` shape as `.pop()`. A `T[N]` receiver is **[CE2023](../../error-catalog.md#ce2023)**.

```sushi
fn main() i32:
    let string[] words = from(["a".clone(), "b".clone(), "c".clone()])
    match words.remove(1):
        Maybe.Some(w) -> println("removed {w}")   # words is ["a", "c"]
        Maybe.None() -> println("no such index")
    let string last = words.remove(1).realise("none".clone())
    println("{last} {words.len()}")
    return 0
```

On an array of arrays the slot is the inner array: `grid.remove(0)` hands a whole row to the
caller, and `grid[i].insert(j, v)` inserts into row `i` in place.

### `.clear() -> ~` and `.truncate(i32 n) -> ~`

`.truncate(n)` keeps the first `n` elements and destroys the rest; `.clear()` is
`truncate(0)`. Neither grows: a count past the length is a no-op, and a negative count
clamps to 0, the way the slice family clamps. Capacity and the buffer STAY -- that is
what separates them from `.free()`, and it is the point: a scratch array in a loop
empties without a realloc.

```sushi
let i32[] arr = from([1, 2, 3, 4, 5])
arr.truncate(2)     # [1, 2], capacity unchanged
arr.clear()         # [], capacity unchanged
arr.push(9)         # reuses the buffer
```

### `.extend(T[] other) -> ~`

Append every element of `other`. The destination grows ONCE, to exactly the length it
needs -- a `.push()` loop pays a bounds check, a capacity check and an amortized realloc
per element.

```sushi
let i32[] out = from([1, 2])
let i32[] body = from([3, 4, 5])
out.extend(body)               # out is now [1, 2, 3, 4, 5]
println(body.len())            # 3: the source is a BORROW and stays yours
```

The source may be a fixed array or a dynamic one. The **destination** must be dynamic: a
fixed array's length is part of its type, so it cannot grow, and `.extend()` on one is
**[CE2023](../../error-catalog.md#ce2023)** for the reason `.push()` is.

### `.extend_range(T[] other, i32 start, i32 count) -> ~`

Append `other[start .. start + count)`, with no temporary array in between.

```sushi
let i32[] out = from([0])
let i32[] src = from([10, 20, 30, 40, 50, 60])
out.extend_range(src, 2, 3)    # out is now [0, 30, 40, 50]
```

`.extend(src)` is `extend_range(src, 0, src.len())`.

### `.extend_str(string s) -> ~`

Append the bytes of `s` to a `u8[]`. The method grows the array once and copies the bytes
in one copy. `buf.extend(s.to_bytes())` gives the same bytes, but it copies them two times.

```sushi
fn main() i32:
    let u8[] buf = from([])
    buf.extend_str("Mostly ")
    buf.extend_str("Harmless")
    println("{buf.to_string()} {buf.len()}")
    return 0
```

Output:

```
Mostly Harmless 15
```

Only a dynamic `u8[]` takes `.extend_str()`. Another element type and a fixed `u8[N]` are
**[CE2023](../../error-catalog.md#ce2023)**. An argument that is not a `string` is
**[CE2023](../../error-catalog.md#ce2023)** too. The string is a borrow: it stays yours.

### `.s(i32 start, i32 end) -> T[]` and `.ss(i32 start, i32 count) -> T[]`

A **fresh** array holding a range of the source. The two spell the range differently and
do nothing else differently: `.s()` takes an exclusive END index, and `.ss()` takes a
LENGTH. They are named for `string.s(start, end)` and `string.ss(start, length)`, which
mean the same for text.

```sushi
let i32[] src = from([10, 20, 30, 40, 50, 60])
let i32[] by_end = src.s(2, 5)    # [30, 40, 50]
let i32[] by_len = src.ss(2, 3)   # the same, and src is untouched
```

`s(a, b)` is `ss(a, b - a)`. Use whichever the surrounding code already computes: a loop
that carries an end index reads better with `.s()`, and one that carries a count reads
better with `.ss()`.

Both work on a fixed array too, and both always answer a `T[]`, because the length is a
run-time value.

**A range outside the source is CLAMPED**, exactly as it is for the string twins. Nothing
traps and nothing is refused: a start before the beginning becomes 0, a start past the end
gives an empty array, a run past the end stops at the end, and an end before the start
gives an empty array.

| call on a 5-element source | answer |
|---|---|
| `.s(-2, 3)` | the first 3 elements -- the start clamps FIRST |
| `.s(9, 12)` | empty |
| `.s(3, 1)` | empty -- an end before the start |
| `.ss(2, 99)` | the last 3 elements |
| `.ss(2, -2)` | empty |

Every row is what `string.s` and `string.ss` answer for the same arguments.

### The rules the three share

**The source is a borrow**, so it stays yours and both arrays end up independent. For a
plain element type the copy is a `memcpy`. For an owning one every copied slot takes its
own deep copy, so a `string[]` costs one allocation per element:

```sushi
let string[] out = from(["towel"])
let string[] more = from(["babel", "fish"])
out.extend(more)
println("{out[1]} {more[0]}")  # babel babel -- two owners, two buffers
```

**A bad range is clamped, never trapped.** `.extend_range()` narrows the same way the
slices do -- one rule, one place -- so a count past the end appends what is there and a
negative one appends nothing. A `count` of zero copies nothing. This is deliberately
unlike `arr[i]`, which traps **[RE2020](../../error-catalog.md#re2020)**: an index names ONE element and either has it or
does not, while a range asks for what overlaps and can always answer.

**The source may not be the destination.** `out.extend(out)` is **[CE2430](../../error-catalog.md#ce2430)**. Growing the
destination may reallocate its buffer, which would leave the source pointer dangling in
the middle of the copy. Use `.clone()` or `.ss()` to take an independent source. A copy
that must read what it is writing -- a run expanded from its own tail -- is a different
operation, and stays a per-element loop.

### `.capacity() -> i32`

Get allocated capacity.

```sushi
println("Capacity: {arr.capacity()}")
```

### `.free() -> ~`

Clear and reset to zero capacity (still usable).

```sushi
arr.free()
arr.push(1)  # OK: Can still use
```

### `.destroy() -> ~`

Free memory and invalidate (unusable).

```sushi
arr.destroy()
# arr.len()  # ERROR CE2406 (use of destroyed variable)
```

## Byte Array Only (u8[])

### `.to_string() -> string`

Zero-cost UTF-8 conversion.

```sushi
let u8[] bytes = from([72 as u8, 105 as u8])
let string text = bytes.to_string()  # "Hi"
```

`.to_string()` does not check the bytes. The bytes must be valid UTF-8.

### `.to_string_checked() -> Result@(string, StdError)`

The checked conversion: it validates the bytes as UTF-8 first, and answers
`Result.Err(StdError.Error)` when they are not valid.

```sushi
let u8[] bad = from([0xff as u8, 0x61 as u8])
match bad.to_string_checked():
    Result.Ok(s) -> println(s)
    Result.Err(_) -> println("bad utf8")
```

## Memory Management

### Fixed Arrays
- Stack-allocated
- Size known at compile-time
- Automatic cleanup when out of scope
- Cannot grow or shrink

### Dynamic Arrays
- Heap-allocated
- Size determined at runtime
- RAII cleanup with recursive element destruction
- Move semantics (ownership transfer)
- Can grow with `.push()`, `.insert()` and `.extend()`

## Safe vs Unsafe Access

```sushi
let i32[] arr = from([1, 2, 3])

# Safe: Returns Maybe@(T)
let Maybe@(i32) safe = arr.get(0)
let i32 value = arr.get(0).or_err(nom StdError.Error)??  # Error propagation

# Unsafe: Direct indexing (throws RE2020 if out of bounds)
let i32 direct = arr[0]

# Writing one element. Bounds-checked the same way; there is no safe `.set()` form.
arr[0] := 42
```

**Best practice:** Use `.get()` for safety, use `[index]` for idiomatic access when bounds are known.

## Performance

- **Access** (`.get()`, `[index]`): O(1)
- **Element write** (`arr[i] := v`): O(1), plus the destructor of the element it replaces
- **Push** (`.push()`): Amortized O(1)
- **Extend** (`.extend()`, `.extend_range()`, `.s()`, `.ss()`): O(n) with ONE allocation -- a
  `memcpy` for a plain element type, one clone per slot for an owning one
- **Pop** (`.pop()`): O(1)
- **Insert / Remove** (`.insert()`, `.remove()`): O(n), one `memmove` of the slots after the
  index
- **Fill** (`.fill()`): O(n)
- **Reverse** (`.reverse()`): O(n)
- **Hash** (`.hash()`): O(n)
- **Clone** (`.clone()`): O(n)

## Implementation Details

- Dynamic arrays use exponential growth strategy
- Runtime bounds checking for all access methods
- RAII cleanup recursively destroys nested structures
- Move semantics prevent use-after-move errors
- `.destroy()` marks array as invalid at compile-time

## Best Practices

- Use fixed arrays when size is known at compile-time
- Use dynamic arrays for runtime-sized collections
- Prefer `.get()` over direct indexing for safety
- Use `.clone()` sparingly (deep copy overhead)
- Call `.free()` to reclaim memory early if array is no longer needed
- Use `.iter()` for idiomatic iteration in foreach loops
- Prefer `List@(T)` over dynamic arrays for complex operations

## Example Usage

```sushi
fn main() i32:
    # Fixed array
    let i32[3] fixed = [1, 2, 3]
    println("Fixed length: {fixed.len()}")

    # Dynamic array
    let i32[] dynamic = from([1, 2, 3])
    dynamic.push(4)
    dynamic.push(5)

    # Safe access
    match dynamic.get(2):
        Maybe.Some(value) ->
            println("Element 2: {value}")
        Maybe.None() ->
            println("Out of bounds")

    # Iteration
    foreach(n in dynamic.iter()):
        println(n)

    # In-place operations
    dynamic.reverse()
    dynamic.fill(0)

    # Cleanup
    dynamic.free()

    return 0
```
