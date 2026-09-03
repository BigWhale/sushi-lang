# Unit-level storage: the `var` declaration

**Status: DECIDED** (issue #546, ruled 2026-09-02; built 2026-09-03). A `var` is one storage
per program, in the data segment, with an address.

## The rule

```sushi
public var File stdout = File(fd: STDOUT_FD, owned: false)   # another unit may name it
var i32 next_id = 0                                          # this unit only
```

A `var` declaration stands at the top level of a unit, beside a `const`. It has the same
shape -- a marker, a type, a name, an initializer -- and a different kind:

| | `const` | `var` |
|---|---|---|
| storage | `.rodata`, one copy per module that reads it | the data segment, ONE per program |
| address | none: a read copies the value out | yes: a rebind, a `poke`, a field write and a mutating method reach it |
| initializer | a constant expression | a constant expression, plus an EMPTY container |
| lifetime | none | initialized before `main`, never destroyed at exit |
| moved out of | never (a copy has nothing to move) | never (CE2436); a plain value copies out |
| visibility | `public` explicit, private by default | the same |

## What it is for

The console handles were the forcing case. `stdout`, `stderr` and `stdin` were `File`
CONSTANTS, and a constant has no address: the moment the `Writer` contract took
`poke self` (so that a `BufWriter@(W)` could implement it), `stdout.write(...)` would have
stopped compiling (CE2400). A `var` gives the console handle an address, so the spelling
stays and the contract can move.

A private `var` is the common case in Go and Zig code, and it is what keeps `public var`
honest:

- **A flag set once, read everywhere in the unit.** `var bool verbose = false`, set from
  `argv` in `main`, read by every printer in the file, with no parameter threaded through
  twenty signatures.
- **A counter or an id generator.** `var i32 next_id = 0` behind `fn fresh_id() i32`.
- **State a module keeps for itself.** A random generator's seed.
- **A cache or a registry**, filled on first use: `var List@(Entry) cache = List.new()`.

## The initializer

The initializer is a constant expression, evaluated by the same evaluator a `const` uses:
a literal, a constant, an operator, `as`, an interpolation, a struct built from constants.
There is no run-before-`main` initializer (Go's), because that brings initialization ORDER
with it. Two consequences:

- **A `var` cannot name another `var`** in its initializer, and a `const` cannot name a
  `var` at all: the value is read at run time (CE0108 either way).
- **An empty container qualifies**: `List.new()`, `from([])` and `new()` are the literal
  descriptor `{0, 0, null}` and allocate nothing, so the backend emits them as the zero
  value of the type. `HashMap.new()` does not qualify: it mallocs its buckets on the
  spot. `from([1, 2])` does not either: the elements need a buffer. Both are CE0108. One
  predicate, `allocates_nothing` in `passes/const_eval.py`, is read by the typecheck pass
  and the backend alike, so the two cannot disagree about what qualifies.
- A `var` whose type is an enum waits on #551 (a constant cannot construct an enum), so
  the `var Maybe@(HashMap@(K, V)) cache = Maybe.None` shape the ruling names is not
  writable yet. `var List@(T)` covers the cache case until then.

## The borrow checker: one storage class beside "local"

A `var` gets a `BorrowState` at every function's entry (`is_unit_var`), so the rules that
already exist apply to it with one addition:

- **Borrowable like a local.** `peek v` and `poke v` take its address; one `poke` at a
  time (CE2403); a `poke` beside a `peek` is CE2407. `foreach(poke r in v.iter())` points
  into its element storage.
- **Never moved out of** (CE2436). `f(nom v)`, `let T x = v`, `return v` and a `nom self`
  method such as `close()` would hand storage nothing re-initializes to a callee or a
  binding that frees it. The rule is CE2410's, the one that fences `main`'s argv view,
  and it applies to an OWNING type only: a plain `var i32` copies out freely.
- **A rebind is the one way to change what it holds.** `stdout := f` consumes `f`, drops
  the old value the way a local's rebind does, and stores the new one. A `let`-borrow out
  of a `var` (`let string first = names[0]`) freezes it exactly as it freezes a local:
  a mutation of the `var` while the binding is live is CE2412 at the binding's next use.
- **Never frozen across a call**, because no function owns it. A callee may rebind a
  `var` the caller is reading; that is what storage means, and it is the caller's to
  order.

The scope pass owns "what kind of name is this": a `var` passes the three CE2400 gates a
constant fails (a `poke`/`peek` of it, a `poke` foreach over it, a `poke self` call on it)
and the CE1002 gate on a rebind target. The typecheck pass's CE2096 gate (a write into a
constant) asks the record's `is_var` and lets a `var` through -- behind an alias too, so
`geo.count := 3` writes and `geo.SIZE := 3` is still refused.

## Never destroyed at exit

A `var` is registered with no scope, so nothing frees it when `main` returns. That is
deliberate: the process ends, the operating system reclaims the pages, and an exit-time
destructor pass would need an order between units that nothing else needs. A program whose
`var` holds heap at exit therefore shows those bytes to a leak checker; a test over such a
program carries no `EXPECT_NO_LEAKS`.

## The backend: one storage, external linkage

A constant is emitted `internal` into every module that reads it, which is harmless for
a value. A `var` is ONE storage, so:

- the declaring unit's module DEFINES it (`@io$fs$stdout = global %File {...}`), with
  external linkage under the unit-mangled symbol (`docs/design/unit-namespaces.md`
  section 9);
- every other module DECLARES it (`@io$fs$stdout = external global %File`);
- a consumer of a BINARY library declares it under the manifest's `link_symbol`, and the
  library's bitcode carries the definition.

The reads and writes reach it through the seams that already existed for a constant:
`resolve_name_slot` answers the global where it answered a local's slot, and
`namespaced_storage` (backend/expressions/names.py) is the ONE reader of an alias that
reaches storage -- a rebind, a `poke`, a field write and a mutating method all ask it.

## Library manifest

`public_variables` mirrors `public_constants` -- `name`, `unit`, `type`, the declaration as
`source`, an optional `doc` -- plus `link_symbol`. A source library needs none of it: its
units are recompiled and the per-unit rule above applies. A private `var` is named in
`not_exported` with kind `variable`, so a consumer naming it hears CE3005 and not CE1001.
A private `var` an exported generic's body names ships in the export closure's `constants`
with its `link_symbol`, and the consumer declares it.

## What was refused

- **`static`**: in Sushi "static" already means a function called on a TYPE name
  (`List.new()`, `f64.from_bits(b)`; `Static(ty, name)` in `docs/design/ir.md`), "static
  dispatch" is everywhere, and in C `static` means internal linkage, close to the
  opposite of an exported unit-level value.
- **`let` at the top level**: `let` names a block-scoped binding with RAII drop, and one
  word would carry two lifetimes.
- **`global`**: the runner-up. `var` is the Go, Zig, Swift, Nim and Pascal word for
  unit-level storage, and it reads against `const` the way the language needs.
- **A kind public by nature**: a `var` that could only be public would be the one place
  where the visibility rule bends, and a unit's own counter, cache or flag has every
  reason to stay private.
- **Two contracts** (a `BufRead`-style perk for the buffered types) and **relaxing
  CE2400** so a constant could satisfy a `poke self` contract: both were the routes the
  ruling on #546 did not take. A buffered-direction contract for `lines()`, `read_line()`
  and `fill()` is a separate, later question.

## History

- 2026-09-02: ruled on #546 (route 2); the keyword cross-check chose `var`; the four
  design items (initializer, borrow class, manifest, alias) ruled the same day.
- 2026-09-03: built. CE2436 added; the console handles became `public var`.
