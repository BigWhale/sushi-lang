# Sorting

[← Back to Standard Library](../../standard-library.md)

Sorting and binary search over a dynamic array `T[]`: `.sort()`, `.sort_by(cmp)` and
`.binary_search(v)`.

## Import

```sushi
use <collections/sort>
```

The built-in array methods need no import. These three are extension methods written in
Sushi, so a unit that calls them must import the module, as for `.map()` and `<collections/iter>`.
A call without the import is [CE3015](../../error-catalog.md#ce3015), and its help names the import.

## Overview

`collections/sort` is a **Sushi-source** standard-library module: it ships as bundled
`.sushi` source, and it is merged as a compilation unit when you import it. The methods
are generic extension methods, so the compiler emits code only for an instance that a
program uses.

The methods move elements only through the built-in [`.swap(i, j)`](arrays.md). No element
is copied, cloned or dropped, so an element type that owns a resource (a `string`, a
struct that holds a `List@(T)`) works, and nothing leaks.

The receiver is a dynamic array `T[]`. An `extend T[]` method does not reach a fixed
array, so a `T[N]` has none of these methods ([CE2008](../../error-catalog.md#ce2008)).

## `.sort() -> ~`

Puts the array in ascending order by `<`, in place. The element type must have an order:
a numeric type, `string`, or a struct or an enum with a derived or implemented `Ord`. An
element type with no order is [CE4006](../../error-catalog.md#ce4006).

The sort is **stable**: equal elements keep their order.

```sushi
use <collections/sort>

fn main() i32:
    let i32[] xs = from([3, -1, 42, 0, 3])
    xs.sort()
    println("{xs}")            # [-1, 0, 3, 3, 42]
    return 0
```

The algorithm is a bottom-up merge sort of the element indexes, followed by one pass that
puts each element in its place with `.swap()`. The merge sort makes O(n log n) comparisons
in the worst case, and it uses two `i32[]` buffers of length n. The last pass makes fewer
than n swaps.

## `.sort_by(fn(T, T) -> i32 cmp) -> ~`

Puts the array in the order that `cmp` gives, in place. A negative `cmp(a, b)` puts `a`
first, zero means equal, and a positive result puts `a` after `b`. This is the sign rule of
the `compare` method of `Ord`. The sort is stable, and it is the algorithm of `.sort()`.

```sushi
use <collections/sort>

struct Item:
    i32 key
    string tag

fn by_key(Item a, Item b) i32:
    return a.key - b.key

fn main() i32:
    let Item[] items = from([Item(2, "b"), Item(1, "a"), Item(2, "c")])
    items.sort_by(by_key)
    println("{items[0].tag}{items[1].tag}{items[2].tag}")   # abc
    let i32[] xs = from([4, 9, 1])
    xs.sort_by(|i32 a, i32 b| b - a)                         # descending
    println("{xs}")                                          # [9, 4, 1]
    return 0
```

## `.binary_search(T v) -> Maybe@(i32)`

Finds `v` in an array sorted in ascending order by `<`. The answer is `Maybe.Some(i)` when
element `i` is neither less than `v` nor greater than `v`, and `Maybe.None()` when no such
element exists. When several elements are equal to `v`, the index of any one of them can
be the answer. The needle is a borrow, as for `.contains()`.

On an array that is not sorted the answer is not defined. The method always ends, and it
never reads outside the array.

```sushi
use <collections/sort>

fn main() i32:
    let i32[] xs = from([1, 3, 5, 7])
    println(xs.binary_search(5).realise(-1))   # 2
    println(xs.binary_search(4).realise(-1))   # -1
    return 0
```
