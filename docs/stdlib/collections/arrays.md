# Array Methods

[← Back to Standard Library](../../standard-library.md)

Built-in methods for fixed-size and dynamic arrays.

## Import

Arrays are built-in types and require no import. For dynamic array construction from literals, arrays are available by default.

```sushi
let i32[5] fixed = [1, 2, 3, 4, 5]  # Fixed-size array
let i32[] dynamic = from([1, 2, 3])  # Dynamic array
```

## Overview

Sushi provides two array types:
- **Fixed arrays** (`T[N]`): Stack-allocated, compile-time size
- **Dynamic arrays** (`T[]`): Heap-allocated, runtime size

Both types share common methods, while dynamic arrays have additional memory management methods.
Both are mutable in place: `arr[i] := v` writes one element, and `.fill()` / `.reverse()` write
all of them. Only the LENGTH of a fixed array is immutable.

## Every receiver shape

A built-in method works the same through every receiver: a local, a struct field, a nested
field, an array element, and a `peek` or `poke` parameter.

```sushi
struct Buf:
    i32[4] slots

fn zap(poke i32[4] arr) ~:
    arr.fill(0)                # reaches the caller's array
    return Result.Ok(~)

extend Buf clear(poke self) ~:
    self.slots.fill(0)         # reaches the caller's struct
    return ~

let Buf b = Buf(slots: [1, 2, 3, 4])
b.slots.fill(9)                # reaches the field
println(b.slots.len())
```

A method that WRITES -- `.fill()`, `.reverse()` -- needs a receiver it can reach. A constant
is rejected with **CE2096**, and the read-only receivers each have their own code: a `peek`
parameter is CE2408, a `match` or `foreach` binding is CE2414, a receiver without `poke self`
is CE2421, an unmarked parameter is CE2422, a borrowing `let` is CE2426, and an unbound
chained receiver is CE2429.

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
let i32 value = arr.get(2)??
```

**Note:** Direct indexing `arr[index]` is also available but throws RE2020 runtime error on out-of-bounds.

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
equality: the numeric types, `bool`, or `string` (a struct or enum element is CE2100).
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

**Limitation:** Nested arrays cannot be hashed.

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
**RE2020** at run time, and a literal index is rejected at compile time -- **CE2012** past
the end of a fixed array, **CE2056** if it is negative.

If the element type owns heap -- a `string`, a struct with a dynamic-array field -- the
element that the write replaces is freed first, so a write in a loop does not leak:

```sushi
let string[] words = from(["towel", "guide"])
words[0] := "babel fish"   # the old "towel" is freed; the array owns the new value
```

An indexed assignment takes ownership of the value, so the ordinary ownership rules apply.
An owned source is MOVED into the array, and using it afterwards is **CE2405**. A value read
out of a container is a BORROW, so storing it in another element is **CE2411** -- take an
independent value with `.clone()`:

```sushi
words[0] := words[1]           # ERROR CE2411: another owner keeps this value
words[0] := words[1].clone()   # correct
```

You may write only where the write can reach the owner. The compiler rejects the rest:

| receiver | code |
|---|---|
| a `peek` parameter | CE2408 |
| a `match` / `foreach` binding | CE2414 |
| the receiver of a method without `poke self` | CE2421 |
| an unmarked parameter | CE2422 |
| a `let` binding that borrows from an owner | CE2426 |
| an unbound chained receiver (`o.get().items`) | CE2429 |
| a constant | CE2096 |

A `poke` parameter, a `nom` parameter and a `poke self` receiver are all writable:

```sushi
fn set_first(poke i32[] numbers, i32 value) ~:
    numbers[0] := value        # reaches the caller's array
    return Result.Ok(~)
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
**CE2023** for the reason `.push()` is.

### `.extend_range(T[] other, i32 start, i32 count) -> ~`

Append `other[start .. start + count)`, with no temporary array in between.

```sushi
let i32[] out = from([0])
let i32[] src = from([10, 20, 30, 40, 50, 60])
out.extend_range(src, 2, 3)    # out is now [0, 30, 40, 50]
```

`.extend(src)` is `extend_range(src, 0, src.len())`.

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
unlike `arr[i]`, which traps **RE2020**: an index names ONE element and either has it or
does not, while a range asks for what overlaps and can always answer.

**The source may not be the destination.** `out.extend(out)` is **CE2430**. Growing the
destination may reallocate its buffer, which would leave the source pointer dangling in
the middle of the copy. Use `.clone()` or `.ss()` to take an independent source. A copy
that must read what it is writing -- a run expanded from its own tail -- is a different
operation, and stays a per-element loop.

### `.capacity() -> i32`

Get allocated capacity.

```sushi
println("Capacity: {arr.capacity()}")
```

### `.clone() -> T[]`

Deep copy of array.

```sushi
let i32[] copy = arr.clone()
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
# arr.len()  # ERROR CE2406: use of destroyed variable
```

## Byte Array Only (u8[])

### `.to_string() -> string`

Zero-cost UTF-8 conversion.

```sushi
let u8[] bytes = from([72 as u8, 105 as u8])
let string text = bytes.to_string()  # "Hi"
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
- Can grow with `.push()`

## Safe vs Unsafe Access

```sushi
let i32[] arr = from([1, 2, 3])

# Safe: Returns Maybe@(T)
let Maybe@(i32) safe = arr.get(0)
let i32 value = arr.get(0)??  # Error propagation

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

    return Result.Ok(0)
```
