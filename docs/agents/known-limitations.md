# Known limitations (current)

This file holds the list of what the language and the compiler cannot do today, and the
workaround for each where one exists. Read this file before you report a gap as a bug,
before you design a feature that is near one of these items, and when a program is refused
and the reason is not clear. A limitation that is fixed leaves this list; the history is in
`CHANGELOG.md` and the GitHub issues.

1. A constant is built from literals, other constants (including an element of an array
   constant), operators, `as`, a STRUCT construction whose every argument is itself
   constant -- positional or named, and nested (`Segment(Point(3, 4), 7)`) -- and an
   ENUM VARIANT whose every payload is (#551): `Sign.Plus`, `Shape.Circle(5)`, a string,
   a struct or an enum in the payload, and `Maybe.None` against the declared
   `Maybe@(T)`. A generic struct or enum is built against the DECLARED type. Another
   unit's public constant, type, constructor and variant are all reachable, flat or
   behind an alias (`const sh.Shape S = sh.Shape.Circle(2)`, #561), and a foreign
   initializer is folded in ITS unit's scope; a stdlib constant folds with its import
   (`PI / 2.0` under `use <math>`, #560). It cannot call a function or a method or name a
   local, and there is no compile-time loop. A table that is a RUN of one value is writable --
   `[8;144, 9;112]` (#446, Ruling 2) -- and so is one that counts, `[0..=5]` (#478). A
   table whose value changes with the index in any other way still has to be spelled
   out. Interpolation in a constant is evaluated (#447): a hole takes any constant
   expression and prints as the run-time formatter would; a struct or an enum in a hole is
   CE0108 and a comparison of one is CE0110 there, because `Display`, `Eq` and `Ord` run
   only on run-time values (a body compares and prints a constant). A struct or enum constant is
   `.rodata` like any other: writing a field is refused, and so is a `poke self` method on one,
   because `.rodata` may not be written. A `peek self` method READS one, and so
   does `let peek T x = C` (#713).
2. A nested array is not an array extension TARGET (`extend T[][]`, `extend i32[3][]` are CE2101; `extend T[]` binds `T = i32[]` and covers the receiver).
3. A named argument names a FIELD, so a struct construction is the only place that takes
   one: a function call, a method call and an enum variant construction read their
   arguments by position, and a name written there is an error. A struct construction
   cannot mix positional and named arguments (the grammar refuses it).
4. Perks cannot have type parameters (an error on the perk or an implementation method; a type
   parameter on a method in the perk DECLARATION is a parse error), inheritance, default implementations (defaults: parked in `TODO.md` P2-4), a `Self` type, or a STATIC method (no `Self`, so a contract cannot hold a constructor). Only a PREDEFINED contract (`Eq`, `Ord`) names its receiver, through the compiler's `ReceiverType`.
5. Libraries: a BINARY `.slib` (`--lib-kind binary`) is not portable across platforms, while the default source `.slib` is. Source is portable as text, not in behaviour: there is no conditional compilation, so an FFI-heavy library is still platform-specific.
6. Generic inference gaps: a bare-param lambda argument to a generic cannot be inferred (annotate: `|i32 x| ...`) — the same rule for a method-level type parameter on an extension (`map@(U)`); a method call has no explicit `@(...)` slot at all (inference-only); a type parameter that only the return type names needs explicit type arguments.
7. Closures: `peek`/`poke` borrow capture, bound-method values, and C callbacks are still missing (Tier 2); a lambda parameter cannot have an owning type (`T[]`, `List@(T)`, `Own@(T)`, a struct that owns, a `Drop` handle, a function type), while a `string` parameter is legal; a NAMED function with an owning parameter is a legal function value. A GENERIC function is a function value wherever a position states the function type (`let fn(i32) -> i32 g = ident`); the error is only for a position that solves nothing (`map(xs, identity)`).
8. Variadics: no pack forwarding (`g(pack...)`) and no pack indexing; a value pack is not a tuple, by design (tuple ruling 14, `docs/design/tuples.md`); variadics are not allowed in perk/extension methods (CE0115); a public native `...T` cannot export through `.slib` (deferred, CE0116).
9. An index position takes a value of any integer type (docs/adr/0001-index-width-i64.md): `arr[i]`, `s[i]`, a slice bound, a repeat count, and the position or count argument of a built-in method (`get`, `insert`, `remove`, `truncate`, `swap`, `resize`, `s`, `ss`, `extend_back` on `T[]`; `insert`, `get`, `remove`, `reserve`, `index_of_from` on `List@(T)`; `List.with_capacity`; `sleft`, `sright`, `char_at`, `repeat`, `s`, `ss`, `pad_left`, `pad_right` on a string). A bare literal there takes `i64`, and a float is refused. The value is widened to `i64`, and the bounds check compares at 64 bits. The stored count stays in the `i32` range, so a count (a repeat or range run, `resize`, `List.with_capacity`, `reserve`, `repeat`, the width of `pad_left` and `pad_right`) above the `i32` maximum stops the program with RE2033. A range bound (`a..b`) is still an `i32` position: a typed value of another type needs `as i32`. `len()`, `size()`, `capacity()`, `count`, `tombstone_count()` and the position in `index_of`, `index_of_from`, `find` and `find_last` answer `i64`, and an `i32` binding needs `as i32` (`let i32 n = xs.len() as i32`). A `ptr` offset (`p.load_i64(96)`) is `i32`. `let f32 x = 5` needs `5.0`.
10. A bare `Own@(T)` local cannot be matched with `Own(poke h)` (nested-only); the checked `let poke Holder h = o.get()` binding (#409) is the mutation path into its payload.
11. A fixed array's size is a literal in any base or the name of an integer constant **of the same unit** — the size is read while that unit's AST is built, so a constant next door is a value and not a size. A size that cannot count elements is an error, and there is no zero-length array.
12. A TYPE is one per program: two units still cannot each declare a `Node` (the duplicate-type error), a consumer cannot declare a GENERIC type name a BINARY library exports publicly (the same duplicate-type error, #728), and a consumer cannot declare a TYPE name a source library declares privately (the library-name clash). A CONCRETE type name a binary library exports is the library-name clash too (#739): one diagnostic at the declaration and no cascade, because the consumer keeps its own type. A FUNCTION of that shape coexists, and shadowing a library's PUBLIC function is legal. A library's PRIVATE type -- every one of a source library (#814), and one of a binary library that a public generic reaches, a generic template included (#761) -- is the library-name clash, and the analysis stops after it; a binary library's private concrete type that only a compiled body uses does not ship, and a consumer type of that name coexists. A consumer's constant beside a library's private one of the same name coexists (#507). A `fn` and a `const` coexist across units and against a source library (in one unit it is an error); the type half is `docs/design/type-identity.md`'s to rule. A private FIELD does not exist: a struct is public or private whole. A static on a built-in generic obeys the alias: `hm.HashMap.new()` behind `use <collections/hashmap> as hm`, and the bare `HashMap.new()` is then refused like the bare type (#506). `List.new()` and `f64.from_bits(b)` are always bare, because nothing imports those names.
13. Iterators: a user function cannot adapt a lent iterator (a `nom Iterator@(...)` parameter refuses one, CE2411); no `take`/`skip`/`chain`, no `poke` items, no `List` slice and no string view.

