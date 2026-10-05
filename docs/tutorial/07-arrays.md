# 7. Arrays

So far our programs have juggled a handful of individual values. Real programs need to
hold *collections* of things: a list of crew members, a row of scores, the first few
Fibonacci numbers. In Python you'd reach for a `list`; in Java, an array or `ArrayList`.
Sushi gives you two closely related tools, and the difference between them is worth
understanding up front.

## Two kinds of array

Sushi has **fixed-size arrays** and **dynamic arrays**.

- A **fixed-size array** has a length baked into its type. `i32[5]` is "five 32-bit
  integers", and it always will be — you can't grow or shrink it. It lives on the stack,
  like a local variable, so it's cheap.
- A **dynamic array** has a length decided at runtime. `i32[]` (note the empty brackets)
  is "some i32s", however many you end up putting in. It lives on the heap and can grow as
  you push more elements onto it.

If you know the size at the time you write the code, prefer a fixed array. If the size
depends on what happens while the program runs, you want a dynamic one.

```sushi
--8<-- "docs/tutorial/examples/07-arrays/fixed-and-dynamic.sushi"
```

Output:

```
There are 5 primes here.
Fibonacci so far: 5 numbers.
The Heart of Gold has 3 crew aboard.
```

A few things to notice:

- A fixed array is written as a plain literal: `[2, 3, 5, 7, 11]`. Its type `i32[5]` says
  exactly five elements.
- A dynamic array is built from a literal by wrapping it in `from(...)`: `from([1, 1, 2,
  3, 5])`. The `from` is what turns a literal into a growable, heap-allocated array.
- `new()` makes an *empty* dynamic array, ready to have elements pushed onto it. An empty
  array has no element to show its element type, so `new()` takes the type from its
  position: here, the declared type `string[]` of the `let`. A `new()` in a position with no
  type, such as `println(new().len())`, is the error [`CE2111`](../error-catalog.md#ce2111).
- `.len()` reports how many elements an array currently holds, and works on both kinds.

!!! note "Why `from(...)` for dynamic arrays?"
    A bare `[1, 2, 3]` is a fixed-size literal. Wrapping it in `from(...)` signals "I want
    a heap-allocated array I can grow", and the compiler allocates accordingly. It's a
    small bit of ceremony that keeps the two array kinds visibly distinct.

## Reading elements: fast vs. safe

There are two ways to pull a value out of an array, and they make different promises.

The first is **direct indexing** with square brackets, `arr[i]`. It's the syntax you know
from every other language, and it's fast. The catch: if `i` is out of bounds, the program
stops at runtime with error **[RE2020](../error-catalog.md#re2020)**. There's no quiet garbage value, no buffer
overread — Sushi checks the bounds and refuses to read past the end. It's "unsafe" only in
the sense that an out-of-range index ends the program.

The second is **safe access** with `.get(i)`. Instead of risking a crash, it returns a
`Maybe@(T)`: `Maybe.Some(value)` if the index is valid, or `Maybe.None()` if it isn't. You
met `Maybe` in [Chapter 6](06-error-handling.md); here it's how the array tells you
"there's nothing at that index" without blowing up.

```sushi
--8<-- "docs/tutorial/examples/07-arrays/indexing.sushi"
```

Output:

```
Captain (sort of): Zaphod
Index 1 is Ford.
There is nobody at index 42.
```

Asking for index 42 of a four-element array would crash with `crew[42]`, but `crew.get(42)`
calmly hands back `Maybe.None()`, and the `match` handles it. Use direct indexing when you
*know* the index is valid; reach for `.get(...)` when you're not sure.

An index is always an `i32`, in `arr[i]` and in `arr.get(i)`. An index variable of a
different integer type, such as a `u8`, is an error; convert it with `as i32`.

## Changing an element

Reading is only half of it. To *write* one element, put the same square-bracket form on
the left of a `:=`:

```sushi
--8<-- "docs/tutorial/examples/07-arrays/writing.sushi"
```

Output:

```
Index 0 is now Marvin.
Squares: 0 1 4 9 16
Index 1 is now Trillian, and index 3 is still Trillian.
```

The index is bounds-checked exactly like a read, so `crew[42] := "Slartibartfast"` stops
the program with **[RE2020](../error-catalog.md#re2020)** rather than writing past the end. Writing in a loop, as the
`squares` example does, is the everyday way to fill an array with computed values.

The last two lines are worth a second look. `crew[3]` reads Trillian's name *out of* the
array, but the array still owns that name and will still free it. If you could store the
same name in `crew[1]` as well, two slots would own one value, and the cleanup would free
it twice. So the compiler rejects the bare `crew[1] := crew[3]` with **[CE2411](../error-catalog.md#ce2411)**, and
`.clone()` is how you say "give me an independent copy". You met this idea as *ownership*;
[Chapter 12](12-memory-management.md) covers it properly.

!!! note "Fixed arrays are mutable — only their *length* is fixed"
    `i32[5]` means "always exactly five integers". It does not mean the five integers never
    change. You can write any element you like; you just cannot make it a sixth. The
    exception is an array **constant** (`const i32[3] PRIMES = [2, 3, 5]`): the compiler
    keeps it in read-only memory, and a write to one of its elements is the error [`CE2096`](../error-catalog.md#ce2096).

## Growing and iterating

Dynamic arrays earn their keep with a small set of methods:

- `.push(x)` appends `x` to the end, growing the array if needed.
- `.pop()` removes the last element and returns it as `Maybe@(T)` — an empty array
  answers `Maybe.None()`.
- `.iter()` produces something you can walk over with a `foreach` loop.
- `.clone()` makes a deep, independent copy.

```sushi
--8<-- "docs/tutorial/examples/07-arrays/grow-and-iterate.sushi"
```

Output:

```
Popped 30; 2 left.
Remaining scores:
  10
  20
Original length: 2
Backup length:   3
```

`foreach(s in scores.iter()):` is the idiomatic way to visit every element — you name each
element (`s`) and the loop body runs once per element, in order. And note what `.clone()`
buys you: pushing `99` onto `backup` leaves `scores` untouched. The copy is genuinely
independent, not a shared reference. (This deep-copy behaviour is part of Sushi's memory
model, which [Chapter 12](12-memory-management.md) explores.)

!!! note "You don't have to free arrays by hand"
    When an array goes out of scope, Sushi cleans up its memory automatically — including
    the elements inside it. This is RAII, and it means no `free()` calls and no leaks in
    ordinary code. You'll see the machinery behind it later in the tutorial.

## Changing the middle

`.push()` and `.pop()` work at the end. Two more methods work at any index:

- `.insert(i, x)` puts `x` at index `i` and moves the elements after it one slot to the
  right. `0 <= i <= len` is `Ok`, and `i == len` appends. Any other index is `Err`, and
  the array does not change. Because it can fail, it answers `Result@(~, StdError)`.
- `.remove(i)` takes the element at index `i` out, moves the rest one slot to the left, and
  gives the element to you as `Maybe@(T)`. An index out of range is `Maybe.None()`.

A few more methods save you from index arithmetic. `.first()` and `.last()` answer a
`Maybe@(T)`, so an empty array is not a crash. `.contains(x)` answers a `bool`, and
`.index_of(x)` answers the index as a `Maybe@(i32)`. `.truncate(n)` keeps the first `n`
elements, and `.clear()` removes all of them. Both keep the capacity, so you can use the
buffer again.

```sushi
--8<-- "docs/tutorial/examples/07-arrays/insert-and-remove.sushi"
```

Output:

```
Placed: true, refused: true, crew: 3
Removed Arthur
First is Ford; Trillian is at 1; Ford aboard: true
After truncate: 1
After clear: 0
```

A fixed array cannot change its length, so `.insert()`, `.remove()`, `.push()` and
`.pop()` on a `T[N]` are the error **[CE2023](../error-catalog.md#ce2023)**. The full list of array methods is in the
[Arrays](../stdlib/collections/arrays.md) reference.

## Repeated values, ranges and slices

You do not have to write every element of a literal. Two short forms fill many slots at
once:

- `[value; count]` repeats `value` `count` times. `[0; 5]` is five zeros.
- A range, `[1..=3]` or `[0..5]`, fills one slot for each number in the range.

You can mix both forms with plain elements: `[1..=3, 42, 7; 2]` is `1 2 3 42 7 7`. In a
fixed array or a `const`, the count must be a value that the compiler can read, such as a
literal or a constant. In `from(...)`, the count can be any `i32` expression, because a
dynamic array keeps its length at run time.

Four methods copy many elements at once. Each one only reads its source array:

- `.extend(src)` appends all of `src`.
- `.extend_range(src, start, count)` appends `count` elements of `src`, starting at
  `start`.
- `.s(start, end)` returns a new array with the elements from `start` up to, but not
  including, `end`.
- `.ss(start, count)` returns a new array with `count` elements, starting at `start`.

A range that goes past the end of the array is clamped, as the slices of a string are. These
methods never stop the program.

```sushi
--8<-- "docs/tutorial/examples/07-arrays/repeat-range-and-slices.sushi"
```

Output:

```
zeros: 0 0 0 0
second prime: 3
counted: 1 2 3 42 7 7
empty seats: 0 0 0
extended: 1 2 3 10 20 30 40
extend_range: 1 2 3 10 20 30 40 20 30
s(1, 4): 2 3 10
ss(3, 2): 10 20
s(5, 99): 30 40 20 30
```

## Arrays of arrays

An array element can be an array. Read the suffixes from **left to right**: each suffix
applies to the type on its left.

| Written | Is |
|---|---|
| `i32[][]` | a dynamic array of `i32[]` |
| `i32[3][]` | a dynamic array of `i32[3]` |
| `i32[][3]` | a fixed array of 3 `i32[]` |
| `i32[2][3]` | a fixed array of 3 `i32[2]` |

An index removes the last suffix. So for `i32[2][3] grid`, `grid[i]` is an `i32[2]` row and
`i` goes from 0 to 2.

```sushi
--8<-- "docs/tutorial/examples/07-arrays/nested.sushi"
```

Output:

```
rows: 3, columns: 2
bottom right: 42
ragged rows: 3, first row: 1 7
```

!!! note "C reads the other way"
    In C, `int grid[2][3]` is 2 rows of 3. In Sushi, `i32[2][3]` is 3 rows of 2. The
    Sushi order lets you read a type as you build it: start with an `i32[2]`, then make 3
    of them.

## What you learned

- Fixed-size arrays (`T[N]`) have a compile-time length and live on the stack; write them
  as plain literals like `[1, 2, 3]`.
- Dynamic arrays (`T[]`) grow at runtime; build them with `from([...])` or start empty with
  `new()`.
- `.len()` reports the current length.
- `arr[i]` is fast but crashes ([RE2020](../error-catalog.md#re2020)) on a bad index; `arr.get(i)` is safe and returns
  `Maybe@(T)`. An index is an `i32`.
- `arr[i] := value` writes one element, on both kinds of array. A fixed array's *length* is
  fixed; its contents are not.
- `.push()`, `.pop()`, `.iter()`, and `.clone()` are the everyday dynamic-array methods,
  and `foreach(x in arr.iter()):` is how you loop.
- `.insert(i, x)` and `.remove(i)` change a dynamic array at any index. `.first()`,
  `.last()`, `.contains()`, `.index_of()`, `.truncate()` and `.clear()` remove most of the
  index arithmetic.
- An array element can be an array. The suffixes read from left to right, so `i32[2][3]`
  is 3 rows of `i32[2]`.
- `[value; count]` and a range fill many slots of a literal. `.extend`, `.extend_range`,
  `.s` and `.ss` copy many elements at once.

Next we'll group related values into named types. On to
[Structs & Enums](08-structs-and-enums.md).
