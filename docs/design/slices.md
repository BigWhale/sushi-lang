# Slices: a borrowed view of a part of an array

Status: BUILT (#1226, phase 5 of the epic #1230). The rulings are David's (2026-10-10 and
2026-10-11) and are settled.

```sushi
fn send(u8[] bytes) i32:
    return bytes.len()

fn main() i32:
    let u8[] data = from([1, 2, 3, 4, 5, 6])
    let u8[] head = data[0..4]       # a view of data[0], data[1], data[2], data[3]
    println(send(head))              # the call reads the bytes of `data`; nothing is copied
    return 0
```

## 1. The problem

Before the slice, the stdlib copied a part of an array to give it to a function that only
reads it. `send_all` in `<net/tcp>` copied the rest of the buffer after each partial send,
which is quadratic in the length when the peer takes small parts. The zlib decoder copied
the body of a stream and two lists of code lengths. `.s()` and `.ss()` are copies, and
there was no other spelling.

## 2. The rulings

| # | Ruling |
|---|---|
| **S1** | The spelling is `data[a..b]` and `data[a..=b]`. A range in an index position is a slice. The index is a position of a range, beside a `foreach` iterable, an array-literal element and a value where `Iterator@(i32)` is expected. `.s()` and `.ss()` stay: they are the copies |
| **S2** | Outside `0 <= a <= b <= len()` is a run-time trap. This is the rule of `arr[i]`, not the clamp rule of `.s()` and `.ss()`. `a == b` is an empty view. A range whose readable bounds go down is CE2125, as in every other range position |
| **S3** | The receivers are `T[]` and `T[N]`. The value is always a `T[]`. A `List@(T)` receiver is not in this design |
| **S4** | A slice is read-only. It is not a `poke` view: a callee could `push` on a `poke T[]` and move the storage that an interior pointer points into. A writable view needs its own no-growth rule, and that is not in this design |
| **S5** | A slice does not escape. It cannot be returned, stored in a field or a container, captured, or given with `nom`. One exception: a function or a method with a borrowed return (`peek T`, #1229) returns a slice of a SOURCE of it (`return xs[a..b]`), and the caller's answer is a let-borrow of the owners of the source arguments (`borrow-model.md` §10h) |
| **S6** | A string view is not in this design |
| **S7** | The `dont_panic` marker removes the bounds check of an element access `[]` (D2 of [dont_panic](dont-panic.md)), and NOT the range check of a slice. A bad slice is a descriptor with a wrong length that leaves the marked body, and every callee checks against that length, so the undefined behaviour would reach code that is not marked (against D6). The check runs once, when the slice is made, not in the inner loop |
| **S8** | A slice of a temporary lives for one statement. `send(make()[0..4])` is legal: the temporary lives until the end of the statement. `let u8[] h = make()[0..4]` is an error, because the array is freed at the end of the statement. A slice never copies, in any position. An index of a temporary is different: `let string s = make()[1]` takes the one element, and that stays |

**The value.** A slice is a BORROWED `T[]`, not a new type. Its descriptor is
`{b - a, b - a, data + a}`, the `{i32 len, i32 cap, T* data}` layout of every `T[]`
([array representation](array-representation.md)). An index read is already a borrow, so
a `let` of a slice is a let-borrow: the owner is frozen while the slice lives, and a
consuming use and a write through the slice are refused. No lifetimes are necessary.

**Ruled out.** The Go model, where a slice shares the array with no check, and the Swift
model, copy-on-write with a reference count. Sushi frees an array at scope exit and has no
reference count.

## 3. Semantics

### 3.1 Where a slice is legal

A slice is legal in every position that reads a `T[]`: a plain argument and a `peek`
argument, a `let`, a receiver of a method that does not change the array (`len`, `get`,
`contains`, `iter`, `clone`, `hash`), the source of a bulk copy (`out.extend(data[a..b])`),
an interpolation hole, and the array of another index or slice (`data[1..4][0]`,
`data[1..4][1..3]`). A slice of a slice views the storage of the first array.

### 3.2 What is refused, and with which code

| Fault | Code |
|---|---|
| A write through a slice: an element write, a method that changes the array (`push`, `sort`), a `poke` argument, a `poke` item of a `foreach`, a rebind of a `let` of a slice | CE2426 |
| A `nom` argument, a `return` from an owned return, a constructor field, a container insert, a capture | CE2411 |
| A `return` of a slice of a local, of a temporary or of a parameter that is not a source, from a `peek` return | CE2443 |
| A change of the array while a `let` of a slice of it lives | CE2412 |
| A `poke` of the array and a slice of it in one call | CE2407 |
| A bulk copy into the array from a slice of the same array (`data.extend(data[0..2])`) | CE2430 |
| A `let` of a slice of a temporary | CE2437 |
| A `let peek` or a `let poke` of a slice: a slice is a value, not a place | CE2404 |
| A bound that is not i32 | CE2121 |
| Readable bounds that go down (`data[3..1]`) | CE2125 |
| A slice of a string, a slice with a reversed range (`data[(0..3).rev()]`) | CE2122 |
| A range outside `0 <= a <= b <= len()` at run time | RE2028 |

### 3.3 The run-time check

The check is `0 <= a <= b <= len()`. The end is exclusive: `data[a..=b]` checks
`b + 1`. Two unsigned compares hold it: `end <= len()` holds the end in `[0, len()]`, and
then `start <= end` holds the start in `[0, end]`. RE2028 prints both bounds and the
length, with the end as an exclusive end: `data[1..=3]` on an array of size 3 prints
`slice 1..4 is out of bounds for an array of size 3`.

## 4. Build choices

- **The node.** A slice is an `IndexAccess` whose index is a `RangeExpr`.
  `IndexAccess.is_slice` is the one predicate. Thus every walk that reads a place
  (`walk_place`, `borrow_owner`, `reads_through_owner`) sees a slice as a read through its
  array's owner, with no new arm.
- **The type.** The typecheck pass checks the range with `validate_range_value` (CE2121,
  CE2125) and stamps the `T[]` in `IndexAccess.inferred_element_type`, the field that holds
  the type an index yields. `holds_a_range_value` (`semantics/ranges.py`) takes the index
  as a legal position of a range, so a slice in a generic argument solves its type.
- **The borrow pass.** `source_provenance` answers BORROWED for a slice, as for every
  `IndexAccess`, and `read_type` answers the stamped `T[]`, which owns: so a consuming use
  is CE2411. `bind` refuses a `let` of a slice of a temporary with CE2437
  (`reject_slice_of_temporary`, `passes/borrow/consume.py`): the base of the slice is not
  kept storage (`names_kept_storage`). `record_borrowed_binding` gives a slice of a `let`
  of a slice the owners of that `let` too (`slice_owners`). `reject_readonly_write` refuses
  a write whose receiver crosses a slice (`slice_on_path`, `passes/borrow/reads.py`), with
  CE2426, before it reads the table of read-only receivers. An unmarked slice argument
  takes a shared borrow of the owner of its array (`register_implicit_borrow`).
- **The backend.** `emit_slice_address` (`backend/types/arrays/indexing.py`) emits the
  descriptor into an `entry_alloca` slot through the `gep_utils` descriptor trio, after the
  check `emit_slice_check` (`backend/types/arrays/bounds.py`). A read of a slice loads the
  slot. `emit_element_pointer` answers the slot for a slice, so a method receiver, a chained
  index and a `peek` argument reach it with no new path. The base array goes through the
  address seam of an element access (`_array_slot`), so a temporary base is parked and
  freed by its scope.
- **No free.** The slice is BORROWED, so `ownership.bind` answers `owns=False` and
  `initialize_dynamic_array` relinquishes the registration, as for
  `let i32[] v = holder.items`. No scope frees a slice.
- **`dont_panic`.** `stamp_unchecked_indexes` does not stamp a slice, and the slice
  emitter does not read the stamp. A marked body whose only `[]` is a slice removes no
  check, so the marker gets CW0004.

## 5. The tests

The fixtures are in `tests/array/slices/`. They cover `a..b`, `a..=b` and `a..a` on `T[]`
and `T[N]`, an owning element type (`string[]`), a plain and a `peek` argument, a slice of
a temporary in one statement, computed bounds, a slice in many reading positions, the
run-time traps (an end past the length, bounds that go down, a negative start, an
inclusive end at the length), the `dont_panic` body, and each refusal of section 3.2.

## 6. Not in this design

A `List@(T)` slice, a string view, and a writable slice. A function returns a slice
through a borrowed return (`peek T`, `borrow-model.md` §10h, #1229).
