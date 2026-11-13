---
status: accepted
---

# The index type is `i64`, and Sushi has no `usize`

Every index, count, length and range bound was an `i32`, so an array could not hold more than
2 GiB and every caller of `File.size()` had to cast. We make the index type `i64`: `len()`,
`size()` and every count answer `i64`. We add no unsigned size type. A bare integer literal
stays `i32` by default.

An index position accepts a value of any integer type, as in Go. The bounds check at run time
refuses a negative value and a value that is too large. A literal in an index position takes
`i64`. In a range `a..b`, a literal bound takes the type of the other bound, and two typed
bounds of different types are a type error.

## Considered Options

- **A `usize`, as in Rust.** Rejected: it adds a type, a cast at each mix of signs, and a
  run-time underflow on `n - 1` when `n` is 0.
- **Only `i64` in an index position.** Rejected: an `i32` loop counter then needs `as i64` at
  each index.
- **An implicit widening without loss, at index positions only.** Rejected: it is a widening
  rule that is true in one place only. The Go rule is simpler, and the bounds check already
  exists.
- **A 64-bit default literal, as in Go.** Rejected: it changes every program. A narrowing such
  as `let i32 n = arr.len() as i32` is explicit, and that is correct.

## Consequences

This is a breaking change, and we keep no backward compatibility. It comes before the stdlib
grows larger, because each new stdlib signature that takes a count adds to the cost.
