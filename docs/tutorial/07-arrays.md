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
- `new()` makes an *empty* dynamic array, ready to have elements pushed onto it.
- `.len()` reports how many elements an array currently holds, and works on both kinds.

!!! note "Why `from(...)` for dynamic arrays?"
    A bare `[1, 2, 3]` is a fixed-size literal. Wrapping it in `from(...)` signals "I want
    a heap-allocated array I can grow", and the compiler allocates accordingly. It's a
    small bit of ceremony that keeps the two array kinds visibly distinct.

## Reading elements: fast vs. safe

There are two ways to pull a value out of an array, and they make different promises.

The first is **direct indexing** with square brackets, `arr[i]`. It's the syntax you know
from every other language, and it's fast. The catch: if `i` is out of bounds, the program
stops at runtime with error **RE2020**. There's no quiet garbage value, no buffer
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

## Growing and iterating

Dynamic arrays earn their keep with a small set of methods:

- `.push(x)` appends `x` to the end, growing the array if needed.
- `.pop()` removes the last element and returns it.
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

## What you learned

- Fixed-size arrays (`T[N]`) have a compile-time length and live on the stack; write them
  as plain literals like `[1, 2, 3]`.
- Dynamic arrays (`T[]`) grow at runtime; build them with `from([...])` or start empty with
  `new()`.
- `.len()` reports the current length.
- `arr[i]` is fast but crashes (RE2020) on a bad index; `arr.get(i)` is safe and returns
  `Maybe@(T)`.
- `.push()`, `.pop()`, `.iter()`, and `.clone()` are the everyday dynamic-array methods,
  and `foreach(x in arr.iter()):` is how you loop.

Next we'll group related values into named types. On to
[Structs & Enums](08-structs-and-enums.md).
