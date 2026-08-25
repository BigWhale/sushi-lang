# Changelog

All notable changes to Sushi Lang will be documented in this file.

## [Unreleased]

Two libraries written in Sushi itself, and the distribution form that carries them:
a `.slib` is now Sushi source plus an index, so one library file works on every
platform, and `use <compression/zlib>` is a complete DEFLATE codec with no C behind it.

### Added
- **The compiler understands documentation blocks: `##: ... :##`**. A doc block is part of
  the declaration, not a comment near it: the grammar sees it, the AST carries it, and the
  compiler checks what it says against the declaration beside it.

  One construct serves three positions -- above a declaration, first in a body, or first in
  a file, where it documents the unit. A block attaches to the declaration on the NEXT line,
  which is Go's rule; a blank line or a `#` comment breaks the attachment, and a block that
  attaches to nothing is **CW7001**. The text is dedented and not reflowed, so a block
  written inside a body renders flush and a fenced example keeps its own indent.

  The delimiters are asymmetric on purpose, and that is what lets the compiler say which
  mistake was made: an opener with no closer is **CE6011**, a closer with no opener is
  **CE6012**, and a line-initial `##:` inside a block is **CE6013**, reported at the inner
  opener with a note on the outer one. The closer is line-initial or the block is a
  one-liner, so a lazy match can no longer swallow the declarations between two blocks.

  Four tags are recognised, each an ordinary Markdown list item: `- Parameter <name>:`,
  `- Returns:`, `- Errors:` and `- Example:`. A new `docs` pass checks them: a tag that
  names no parameter of this callable is **CE7001**, one parameter documented twice is
  **CE7002**, a second `- Returns:` or `- Errors:` is **CE7003**, and a keyword within two
  edits of a real one is a typo rather than prose, **CE7004** with the tag it meant. A block
  in a body that is not the first item is **CE7005**, and a declaration documented both from
  above and from inside its body is **CE7006**.

  Nothing consumes the text yet. `docs/documentation-blocks.md` is the reference and
  `docs/design/documentation.md` carries the phases that follow.
- **An array literal element may repeat: `[value; count]`** (#446). A table of one value
  no longer has to be spelled out or built at run time. The form is an ELEMENT form, so
  runs mix with plain elements and repeat within one literal: `[0; 19]`, `[1, 0;3, 9, 7]`,
  and the RFC 1951 fixed literal code as `[8;144, 9;112, 7;24, 8;8]` on one line. It works
  in a `const`, in a fixed local, and inside `from(...)`.

  The count is a positive integer the compiler reads: a literal in any base, an integer
  constant, or an expression of them. Unlike a fixed array's SIZE, it is read at the
  typecheck pass rather than while the AST is built, so it may name a constant of another
  unit. A count that is not a count -- zero, negative, or unreadable -- is **CE2017**.

  The value is evaluated once and copied, so the element type must copy. Repeating a type
  that owns heap memory would need a deep copy per slot and `.clone()` is the only deep
  copy in Sushi, so it is **CE2018**.

  A long run emits a counted loop, never a line of stores: `from([-1; 32768])` is 223 lines
  of IR with one store in it. **CE2011** now compares the EXPANDED count, and when a literal
  has runs it lists each one with the absolute span it fills. That listing is what the count
  form otherwise lacks -- a literal one element short gives the compiler no way to know
  which run is wrong, since either could be, so it prints what it does know and the reader
  matches a boundary against it.

  Ruled in `docs/design/compile-time-evaluation.md`. The measurement behind the ruling: every
  long table in the repository is a run of one value, so this closes them all and Sushi needs
  no constant function or compile-time loop yet.
- **`<`, `>`, `<=` and `>=` order two strings, and a comparison now decides its operands**
  (#449). The four order operators read the UTF-8 bytes, which is what Rust and Go do:
  `memcmp` over the common prefix, then the length as the tiebreak, so `"apple" < "apples"`
  is true and `"Zoo" < "apple"` is true because a capital is a lower byte. This is a byte
  order and never a collation -- it does not normalize, so the two spellings of `é` are
  neither equal nor adjacent, and a list a person reads still needs a locale-aware
  comparison. Both a variable and a literal work on either side, and a constant folds the
  same answer a body computes.

  The crash this closes was one hole in a wider one: the typecheck pass never asked what a
  comparison may compare, so every pair it did not look at reached the backend and tried to
  compare a string, a struct, an enum or an array value as an `i32`. `"a" < "b"` was
  **CE0000**, and `string < i32`, `struct == struct`, `enum == enum` and `i32[] < i32[]`
  were each **CE0017** -- four internal errors telling the user to report a compiler bug for
  ordinary invalid code. One closed rule now answers all six operators: equality takes the
  numeric types, `bool` and `string`, an order takes the numeric types and `string`, a mixed
  pair is **CE2513** and a type carrying no such comparison is **CE2514**, which names the
  escape (`match` for an enum, the fields for a struct). Two numeric widths keep CE2510.

  A `bool` deliberately loses its order. It compiled quietly before and answered
  `false < true`, where the code almost always meant `!=` or a missing `and`. Rust and Go
  both accept it; Sushi does not.

- **A constant can index an array constant, and can compare two bools or two strings** (#441).
  `const i32 SMALLEST = PRIMES[0]` folds, because every element of an array constant is
  already an evaluated value. A bound is checked while compiling -- a constant cannot trap --
  so past the end is **CE2012** and a negative index is **CE2056**. `==` and `!=` on two bools
  and on two strings fold as well; both work at run time and only the constant evaluator
  refused them. Ordering two strings was still rejected here, and #449 has since made it
  work in a constant and in a body alike.

  `+` on two strings in a constant now reports **CE2509**, the code the rest of the language
  reports, in place of CE0110 "arithmetic on non-numeric type". Sushi has no concatenation
  operator anywhere -- interpolation is the way to combine strings -- so the old wording read
  as a constant-only limit rather than the language rule it is. Interpolation inside a
  constant is still not evaluated (#447), and there is still no compile-time loop, so a
  generated table has to be spelled out (#446).

- **A fixed array's size may be written in any base, or name a constant** (#439, #440). The
  grammar took a decimal literal and nothing else, so `u8[0x100]` came back as
  `CE6001: unexpected token '0x100'` while `u8[256]`, the same size, compiled. Every base a
  numeric literal has now works, with the underscore rule (CE6006) and the C-octal rule
  (CE2071) applying exactly as they do to a literal anywhere else, because the size goes
  through the same token seam.

  A size may also name an integer constant, so a size that repeats across declarations can
  be written once: `const i32 MAX_BITS = 15` then `i32[MAX_BITS]`, in a local, a struct
  field, a parameter or a return type. The constant may be an expression
  (`HALF * 2`) and may name another constant, because the real constant evaluator reads it.
  It must be declared in the SAME unit: a size is read while that unit's AST is built, long
  before any pass holds a program-wide constant table, so a constant next door is reachable
  as a value but not as a size. That limit is now in the Known Limitations list, where #440
  asked for it.

  A size that cannot count elements is **CE2099**, one code carrying the reason: an unknown
  name, a constant that is not an integer, or a zero. A zero used to leave the type unbuilt
  and surface as CE2007, a missing type annotation on a line that has one. A zero-length
  array stays illegal -- ruled, not overlooked.

- **`use <compression/zlib>`: DEFLATE and the zlib container, written in Sushi.** No C
  library and no FFI -- the whole codec is Sushi. `zlib_compress` and `zlib_uncompress`
  handle the RFC 1950 container with its Adler-32 trailer; `deflate_raw` and
  `inflate_raw` handle a bare RFC 1951 stream; `adler32` is the checksum on its own.
  Errors are values: `ZError` carries the byte offset, the symbol or the two checksums
  that explain the failure, and `zlib_error_text` renders any of them as one stable line.

  The decoder is complete -- stored, fixed Huffman and dynamic Huffman blocks -- so it
  reads anything a conforming encoder writes. It follows puff.c, so a Huffman code is a
  count per bit length plus the symbols sorted by length, and needs only flat integer
  arrays. The encoder emits stored and fixed-Huffman blocks with an LZ77 hash-chain
  matcher, and falls back to stored when coding would make the data bigger, so the output
  never exceeds the input by more than five bytes per 65535. It never emits a dynamic
  Huffman block, so its ratio is short of a full encoder's: about 1.2x `zlib -6` on prose.

  Validated differentially against Python `zlib` in both directions
  (`tests/unit/test_zlib_differential.py`, 318 cases): every stream Sushi writes is read
  back by a real zlib, and every stream a real zlib writes is read back by Sushi.

- **Source is the primary distribution form for a Sushi library.** A `.slib` carries the
  complete source text of its units next to the MessagePack index, and the consumer
  compiles those units and caches their object files. One artifact works on every
  platform, because text has no target triple. No AOT-compiled language ships a portable
  binary library: Rust, Go and Zig ship source, Apple ships per-platform slices, and only
  bytecode ecosystems have portable binaries. Sushi had already crossed half the line --
  a generic can never be pre-compiled, so generics always travelled as source text, and
  the compiler already compiled bundled stdlib modules that arrive as text. The design is
  `docs/design/libraries.md`.
- **`--lib-kind {source,binary,hybrid}`**, defaulting to `source`. Binary distribution is
  the opt-in and keeps the old behaviour exactly: concrete bodies as bitcode, generics as
  source slices. It buys less than it looks: a binary library still carries the source of
  its generics, because monomorphization needs the consumer's type arguments, so only
  concrete bodies are hidden.
- **A library states its own version.** `--lib-version X.Y.Z` supplies it, unless a
  `nori.toml` beside the sources does; the manifest never recorded one before, because
  `library_name` came from the output filename and nothing stated a version at all.
  Neither present is **CE3505**, and so is a `--lib-version` that contradicts the
  `nori.toml` -- silently preferring one would let a package ship under a version it does
  not claim.
- **A library states which compilers can build it.** A source library is compiled by the
  CONSUMER's compiler, so one that built cleanly under 0.11 can fail under 0.12. That is
  the standard cost of source distribution and it is not fixable, only declarable. Every
  build stamps `requires_compiler`, by default `~<major>.<minor>` of the building
  compiler, because pre-1.0 semver makes the minor the breaking unit. A consumer outside
  that range is **CE3503**, a hard error rather than a warning: an incompatibility that is
  only warned about surfaces later as a confusing error inside library source the consumer
  never wrote. The escape is **`--ignore-compiler-version`**, build-wide and obviously
  temporary.
- **`sushi_lang/internals/semver.py`**: `Version` with parsing and ordering, and a
  constraint matcher covering exact, `~X.Y`, caret and comparator ranges. It sits in
  `internals` rather than `packager` because the compiler needs it on the library-load
  path; the Nori resolver reuses it rather than growing a second implementation.
- **`--lib-info` reports the version-4 fields.** The report states the kind, the library
  version, the compiler constraint and the unit index, and prints only the lines the kind
  can answer for: no `Platform` and no `Bitcode` for a source library, no `Source` for a
  binary one. Both readers changed together -- the Sushi tool `toolchain/src/slib_info.sushi`
  and the Python fallback -- and `tests/unit/test_slib_info_parity.py` locks them to the
  same bytes.
- **`slib_sizes` in `use <toolchain/slib>`**, which reads the length of both payload
  sections in one pass and never reads a blob. `slib_bitcode_size` is now one line on top
  of it.
- **CE3506**, the source section's sibling of CE3510 and CE3511. One truncation code per
  section, because the message names which section is short, and that is what tells a
  reader where the file was cut.

### Changed
- **`compression/zlib` is rewritten onto the language it now has.** The module landed
  before four of the fixes in this release and carried a workaround for each. It opened
  with a CAUTION block telling the reader that a bitwise operator truncates in silence and
  to cast every operand -- the opposite of what #438 made true -- and it pointed at an
  untracked working document from a file that ships to users.

  Six fill loops become one repeated element each: the RFC 1951 fixed literal code is
  `from([8;144, 9;112, 7;24, 8;8])` instead of four `while` loops and 288 bounds-checked
  appends, and the encoder's 32768-entry hash head is `from([-1; 32768])`. Since Sushi has
  no module-level state, `zfixed_lit` and `zfixed_dist` run once per fixed block in a
  stream, so this is about 700 appends per block replaced by two allocations and six
  counted fills. Two fills keep their loop, and say why in a comment: their length is the
  input size or a parameter, so no count is readable at compile time.

  The encoder stops scanning for a code. `zlen_index` walked a 29-entry base table
  backwards and `zdist_index` a 30-entry one, once per emitted match. The inverse tables
  are now written directly -- 256 slots in 29 runs for the length code, and the range split
  zlib'"'"'s own encoder uses for the distance code, 256 slots in 16 runs each side -- so a
  lookup is one array read. Both keep their range check and their `ZError` channel, and a
  program compares every table against the scan it replaces across all 256 lengths and all
  32768 distances: no disagreement.

  Five shift counts drop a cast that #438 made unnecessary, since a shift count'"'"'s type is
  free; the operand casts stay, because CE2510 requires them. `zinflate_lengths` reads its
  three repeat symbols with a `match`, the form `inflate_raw` already used for the block
  type. No behaviour changes: 354 differential cases against Python zlib pass in both
  directions, over both containers.
- **An operation the compiler reads computes at the declared width, and a result that
  leaves the type is CE2077** (#446, Ruling 1 of `docs/design/compile-time-evaluation.md`).
  The evaluator held a Python integer of unlimited size and stamped the exact result with
  the type of the left operand, so `const u8 A = 200 + 100` held 300 while the program
  printed 44. Truncation hid that for `+`, `-`, `*`, `<<` and `~`, and exposed it wherever
  something read the held value: `A as u32` was 300, `A > 255` was true, and
  `(200 + 100) / 2` was 150 in a constant and 22 in a body.

  Sushi now follows Rust. The overflow-checked operators are `+`, `-`, `*`, `/`, `%` and
  unary minus, and each reports **CE2077** with the operator, the value and the type;
  division and unary minus have one case each, the smallest signed value. The width-defined
  operators -- `~`, `&`, `|`, `^`, `<<`, `>>` -- compute at the width and never report,
  because the bits that leave the type are lost by design. A cast is the escape and
  truncates. This is a breaking change: `let u8 x = 200 + 100` no longer compiles, and no
  test in the suite of 1782 needed a change for it.

  Two divergences close with it. A held value is now inside its own range, so a right shift
  of an unsigned constant no longer fills from a sign bit the type does not have --
  `~(0 as u32) >> 1` was 4294967295 and is 2147483647, which is what a body always read.
  And a cast in a constant truncates as the machine does, so `300 as u8` holds 44 rather
  than 300. One consequence for #447: a constant now always holds a value its type can
  hold, so interpolation in a constant has nothing left to reconcile.
- **The `.slib` container is at version 4.** `SPARE_1` and `SPARE_2` became `FLAGS` and
  `KIND`, so the fixed 52-byte header did not change size, and a length-prefixed source
  section sits between the metadata and the bitcode. `KIND` states which payload is
  present, so a reader can branch before it unpacks any MessagePack. `FLAGS` bit 0 is
  reserved for source compression and is always written as zero: Nori archives are already
  `tar.gz`, so the wire is compressed regardless. A version-3 file is **CE3509**; there is
  no upgrade shim, because Sushi has no users in the wild.
- **The platform gate applies to a binary library only.** `_check_library_platform` read
  one `platform` field and rejected the whole file, so a library carrying nothing
  platform-bound was refused anyway. **CE3504** is now never raised for a source library,
  and `--lib-info` prints no `Platform` line for one. That single condition is the whole
  cross-platform fix.
- **A bare `./sushic --lib` no longer builds.** A library must state a version, so a build
  with no `nori.toml` and no `--lib-version` is CE3505.
- **A source library's units are ordinary compilation units at the consumer.** They enter
  as `lib/<library>/<unit>`, so they can never collide with a consumer unit name; privacy
  stays the existing unit mechanism, with no new machinery; each one caches its own `.o`
  under `__sushi_cache__/units/lib/<library>/`, with the materialized source in
  `__sushi_cache__/libsrc/`. Four rules make it sound, and not one of them was in the
  plan -- each appeared when real libraries went through the new path: library units are
  COLLECTED first, because
  the compilation order puts dependents before dependencies; a consumer definition SHADOWS
  a library one silently, for functions, generics and perk impls, so `--lib-kind` changes
  distribution and never semantics; a monomorphized instance of a library generic goes
  home to the unit that DECLARED the generic, which makes its call to a library-private
  helper an intra-unit call and is what replaces the export closure; and that instance is
  folded into the unit's fingerprint, because which instances a library unit carries
  depends on what the consumer asked for and its own source hash cannot say so.
- **CE5007 cannot arise on the source path.** It exists because an export-closure private
  shares the consumer's flat namespace; namespaced units remove the shared namespace, and
  the instance-goes-home rule removes the need to ship privates at all.

- **A shift by a count that empties the type answers 0.** The count that CE2512 cannot
  read -- a loop index, a byte from a file -- used to reach LLVM as written, and a count
  at or above the width made the result poison: the program printed whatever the
  optimiser left behind, and on a signed `>>` the arm64 hardware answered with the value
  unchanged. A shift now has an answer everywhere. A count at or above the width moves
  every bit out, so the result is 0; an arithmetic right shift fills from the sign bit and
  leaves it behind, 0 for a positive value and -1 for a negative one; a negative count is
  out of range at the other end and answers the same way.

  This is Go's rule, chosen over the masking that Java and Rust-in-release expose, because
  masking answers `value << 8` on a u8 with the value itself -- a wrong answer that reads
  like a working shift, and one that silently aliases every eighth iteration of a
  byte-assembly loop onto the same result. Sushi now rejects more than any of the five
  languages measured at compile time, and leaves no undefined shift at run time.

  The range is read from the count as written, before it is narrowed to the value's width:
  a u64 count of 256 narrowed to a u8 is 0, which would have answered with the value
  unchanged. The shift itself is given a masked count so LLVM never emits an out-of-range
  shift, whose poison it is free to use. The cost is one compare and one conditional move,
  and nothing when the count is a constant -- a 5 MB zlib round trip measured 0.28s before
  and 0.28s after.

### Fixed
- **A msgpack length at or above 2^31 decoded as an empty value instead of an error**
  (#463). Each length-prefixed tag narrowed its `u64` prefix with a bare cast, so
  `0xffffffff` became -1, every taker looped `while (i < count)` zero times, and a
  five-byte buffer of nothing but a hostile header came back as `Ok`: `dd ff ff ff ff`
  was an empty array, `df ...` an empty map, `c6 ...` an empty bin and `db ...` an empty
  string. Python's `msgpack` rejects all four.

  The module's own header stated that such a length "casts to a negative count, loops zero
  times, and the next read reports Truncated". There is no next read when the header ends
  the buffer, so nothing reported anything; with a byte after it the result was `Trailing`,
  which named the wrong fault.

  One reader now takes every length prefix and rejects one the remaining buffer cannot
  supply. Every element of an array or a map needs at least one byte and a str or bin
  payload needs exactly its count, so `count > remaining` is unsatisfiable for all four
  families and `Truncated` is the accurate report -- no new `MpError` variant. It also
  catches an over-long POSITIVE length at the header rather than part way through the loop.
- **A declared type reaches a bare literal through `~`, and through a nested negation**
  (#448). `let u8 b = ~0` was **CE2002**, "cannot assign i32 to u8", and `const u8 MASK = ~0`
  said the same, so "every bit set" at any width other than `i32` needed an `as` cast --
  which is why the bitwise constant test carried `~(0x00 as u32)`. The literal now takes the
  declared type and the complement happens at that width: a `u8` reads 255, an `i8` reads -1,
  a `u64` reads all 64 bits.

  `~` was not a missing special case. The recursion that carries a declared numeric type
  down to the literal leaves had an arm for a binary operator and nothing else, and unary
  minus worked only through a LEAF case one function below it, which unwraps a negated bare
  literal to fold the sign into its range check. So the other half of the same hole was
  `let i8 x = -(1 + 2)`, a negation over anything that is not itself a literal. The
  recursion now descends through a unary operator whose result is its operand's type --
  `neg` and `~`, never `not`, which answers a `bool` -- and the negated bare literal stays
  one leaf, so `-128` still fits an `i8` and `-1` still misses a `u8`.

  The operand-driven half reads the same way. A bare literal takes its sibling's type
  through those operators too, so the bit test `flags & ~0x0F` on a `u8` is one width rather
  than the mixed `u8`/`i32` pair of **CE2510**, which is the shape a mask check inside an
  `if` always had. Nothing about `as` moved: `~wide` on a `u32` assigned to a `u8` is still
  CE2002, because this types LITERALS and never converts a typed value. Two diagnostics get
  better instead of disappearing -- `let u8 b = ~300` is now **CE2073**, the range check
  answering for itself rather than a type mismatch downstream of it, and `~1.5` is still
  **CE2004**.

- **A rejected library build no longer reports a spurious CE0000 over the real diagnostic**
  (#436). Three rejection sites in the manifest producer emitted their diagnostic and then
  raised `ValueError` to stop the build. Nothing caught it, so the top-level guard rendered
  it as an internal compiler error on top of the correct message, and a legitimate
  rejection of the user's own program came with an invitation to file a compiler bug. Each
  site now emits and returns, and `pipeline.py` decides control flow through
  `reporter.has_errors`, the way it already does before codegen.

  Two gates, and the placement is the point: the first sits after the export closure and
  before the bitcode compilation, so a CE5006 rejection still costs nothing, and the second
  sits after the manifest, which extracts the public API first and returns without writing
  once the reporter holds an error. A rejected build leaves no `.slib` behind and prints no
  success line. The exit code was already 2 and stays 2.

  Every rejected public export is now named in one build rather than one per build, because
  the loop reports instead of stopping at the first. Two findings came with the fix: the
  `_reject` helper inside the export-closure walk had been non-returning, which left the
  code after each call site unreachable, and the producer's **CE5002 cannot be reached from
  a CLI build at all** -- the typecheck pass's public-fn `ptr` fence (CE5008) tests the same
  condition and exits earlier. That shadowing is now pinned by a test, so a missing CE5002
  is never read as a regression.

- **A negative array index compiled, and reached run time as a trap** (#450). `numbers[-1]`
  and `numbers.get(-1)` both passed the compiler and RE2020 caught them at run time, although
  the compiler held the answer the whole time. A `-1` parses as a unary negation over a
  literal and not as a literal, so the guard in front of the bounds check asked for the wrong
  node, never matched, and left the `index < 0` branch behind it as dead code -- in both
  indexing forms, written out twice. One reader now takes a literal index, negated or not, and
  one validator carries the pair of codes for every indexing form, so a third form cannot
  inherit half the rule. A negative index is **CE2056**, whose text says exactly that; past
  the end stays CE2012.

  CE2056 had never been emitted anywhere. The constant evaluator in #441 reached for CE2014
  instead, which is the CAST code, so a negative index in a constant rendered as
  `invalid cast from '<missing:source>' to '<missing:target>'`. Its test asserted only the
  code, which is what let a broken message through; the negative-index cases now assert the
  text as well.

- **A string constant that was not a bare literal reported an internal compiler error**
  (#441). The backend matched a string constant by the SHAPE of its expression, so
  `const string ALIAS = GREETING` -- a program the typecheck pass accepts -- registered no
  global at all, and every use of `ALIAS` came back as `CE0055: unknown variable or constant`,
  a diagnostic that asks the user to report a compiler bug. A string array holding one
  non-literal element failed the same way. Constant emission now routes on the VALUE the
  evaluator produced and never on the expression that produced it, which is the same fix #260
  made for a literal array, applied at the seam instead of one shape at a time. A string is
  still finished in the backend: its bytes need a module to live in, and the evaluator has
  none.

- **A constant divided and took a remainder differently than a body did** (#441). The
  evaluator used Python's floor division and floor modulo, so `-7 / 2` was `-4` in a constant
  and `-3` everywhere else, and `-7 % 2` was `1` against `-1`. Integer division now truncates
  toward zero and a remainder takes the sign of its dividend, as `sdiv` and `srem` do; a float
  remainder is `fmod`, as `frem` is.

- **A bitwise operator on a float crashed the compiler.** `a & b` on two `f64` values
  reached the backend, where LLVM has no such instruction, and a program a user wrote came
  back as `CE0000: internal compiler error ... instruction requires integer or integer
  vector operands`. The operand gate asked for a numeric type, and a float is one; the
  test beside it has said "bitwise operators only work with integer types" since the day
  it was written. `& | ^ ~ << >>` now require an integer on every operand and report
  **CE2004** otherwise -- including a float next to an integer, which used to report the
  width rule (CE2510) for an operand that has no width to reconcile. A float's bits are
  reached the way they always were, through `f64.to_bits()` and `from_bits()`.

- **An enum payload slot allocated inside a loop leaked stack until the function
  returned.** A `??` unwrap, an enum construction, a match that binds a payload, and
  `List.push`/`List.pop` each allocated their scratch slot at the point of use, so in a
  loop the `alloca` landed in the loop body. LLVM only releases an alloca at function
  return, so every iteration took another 16 or 32 bytes and a loop of a few hundred
  thousand walked off the stack guard page with SIGSEGV. Optimisation did not help.

  All seven sites now take the slot from `entry_alloca`, which puts it in the function
  entry block and reuses it. Reuse is safe because each slot is scratch: the payload is
  loaded back out before the expression finishes, and a reference binding deliberately
  points into the scrutinee rather than the copy.

  The regression test drives all four shapes past a million iterations
  (`tests/error_handling/stack/test_run_try_in_long_loop.sushi`). Found while building
  `compression/zlib`, whose encoder died above 53 KB of ordinary text.

- **The library documentation described a container that no longer exists.**
  `docs/library-format.md` disagreed with itself about the version -- the layout diagram
  said 2 and two other places said 3 -- and its manifest schema presented
  `is_generic`/`type_params` on the concrete declaration lists as if they vary, when the
  producer filters every generic out of those lists. `docs/libraries.md` still cited
  `CW3505` for a platform mismatch, a warning deleted long ago; the error is CE3504, and a
  source library is not checked at all. Every bare `./sushic --lib` example in the docs was
  a command that now fails with CE3505, in six files.

- **The pre-push hook blocked every push** (#442). Git exports `GIT_DIR` to every hook
  it runs, and a push from a worktree always sets it. `GIT_DIR` overrides an explicit
  `-C`, so `read_commit` in the documentation-footer hook stopped answering about the
  directory it was given and answered about the repository being pushed. Its unit test
  hands it an empty directory and expects no commit, so the test failed, the hook's
  pytest gate failed with it, and `git push --no-verify` became the only way through --
  which turns off the whole gate, including the parts that work. CI never saw it, because
  there pytest is an ordinary step with no `GIT_*` set.

  `read_commit` now runs git with the `GIT_*` variables removed, so the argument it takes
  is the question it asks. `.githooks/pre-push` scrubs the same variables from every check
  it starts, which keeps the hook an honest mirror of CI.

- **A bitwise operator did not check operand width** (#438). `& | ^` accepted two
  different numeric types where `+` refuses them, and the backend then made the two sides
  agree without a word: it widened or **truncated** the right operand to the width of the
  left, and gave the result the type of the left operand. `low | wide`, a `u8` and a
  `u32`, compiled clean and printed 255, because 0x1FF had been cut to 0xFF. So did the
  byte-assembly shape behind every bit reader, checksum and length field:
  `accumulator := accumulator | (byte << 8)` never moved the accumulator, because the
  shift kept the `u8` width of `byte`.

  CE2510 is now one rule for every operator whose two operands must agree -- arithmetic,
  comparison and `& | ^` all reach `reject_mixed_numeric_operands`, which also retires the
  copy of the numeric-type list that the arithmetic path carried. `as` is the escape, as it
  is everywhere else. A shift is deliberately exempt: its right operand is a count, not a
  second value, so `value << places` with two widths stays legal and the result keeps the
  type of the left operand. `docs/language-reference.md` states the rule under Operators,
  where it was not written down before.

- **A shift count at or above the width of the value was accepted.** Found while fixing
  the mixed-width defect above, and it is what makes that report's own example wrong:
  `high << 8` on a `u8` moves every bit out of the type. LLVM answers such a shift with
  poison, so nothing was lost loudly -- the program printed whatever the optimiser left
  behind, 32 for `0x12 << 8` and 255 once an `|` was wrapped around it.

  A count the compiler can read -- a literal, a constant, an expression of them -- is now
  **CE2512**, which also covers a negative count. This is the rule Rust applies to the same
  program (`deny(arithmetic_overflow)`), and the escape is a cast on the VALUE:
  `(high as u32) << 8`. A computed count is still not checked, at compile time or at run
  time; `docs/language-reference.md` says so where it states the rule.


## [0.11.1] - 2026-08-22

The clean-up release after 0.11.0: the first two libraries written in Sushi itself, the
conditional-move leak, digit grouping in every numeric base, named semantic passes, and a
documentation site that says which version it documents -- with a highlighter that
knows the syntax it renders.

### Added
- **`use <encoding/msgpack>`: a MessagePack decoder written in Sushi** (R1.1). The first
  library written in the language itself, bundled as a Sushi-source stdlib module.
  `mp_decode` turns one buffer into a `MsgValue` tree, `mp_map_get` scans a map in wire
  order and clones the value out, and `mp_show` renders a value on one line (a float
  prints its IEEE-754 bits, so the rendering is deterministic). A decode error is a
  value, not a trap: `MpError` is one of Truncated, Unsupported, BadUtf8 or Trailing.
  Decode only. The single-byte tags dispatch through one integer `match`, which is why
  that feature landed first.
- **`use <toolchain/slib>`: a `.slib` metadata reader in Sushi.** Reads the 52-byte
  header and the msgpack metadata map of a version-3 library into a `MsgValue` tree
  (`slib_read_metadata`), plus the bitcode length (`slib_bitcode_size`); mirrors the
  Python `LibraryFormat.read_metadata_only` and never reads the bitcode. First stdlib
  source module that imports another source module.
- **The `toolchain/` directory: compiler tools written in Sushi.** `toolchain/src/`
  holds tool programs, `./toolchain/build.py` compiles them into the gitignored
  `toolchain/bin/`, and `sushic` delegates to a built tool when one exists. First tool:
  `slib-info`, which owns the full `--lib-info` report; `sushic --lib-info` runs it and
  returns its exit code, falling back to the built-in Python reader when no binary is
  present (always the case in a wheel install). `SUSHI_TOOLCHAIN=off` forces the
  fallback; `SUSHI_TOOLCHAIN_BIN` overrides the tool directory. A relative `--lib-info`
  path now resolves against the caller's directory through the `./sushic` wrapper.
- **Integer literal match arms** (#415). A `match` on an integer scrutinee dispatches on
  literal arms (`0xc0 ->`, `-1 ->`), compiled to one LLVM `switch`. A literal takes the
  scrutinee's type under the usual context-typing rule (a non-decimal literal is a bit
  pattern; out of range is CE2073). New diagnostics: CE2074 (an integer match needs a
  trailing `_` arm), CE2075 (duplicate literal arm by value), CE2076 (literal arm on an
  enum scrutinee, or enum-pattern arm on an integer scrutinee). Motivated by the tag
  dispatch in the first Sushi stdlib decoder.
- **Underscores in decimal and float literals**. `1_000_000`, `3.141_592` and
  `1_0.2_5e1_0` now group their digits like the prefixed bases always could. Decimal was
  the one base without them because the grammar imported Lark's stock `INT` (`/\d+/`)
  while hex, binary and octal were written by hand — an omission rather than a decision,
  and it landed on the common case. Underscores work in all three parts of a float, and
  in every position a literal appears: an array size and a match arm normalize through
  the same seam as an expression.

### Changed
- **The documentation site names its version.** The footer of every page on
  <https://bigwhale.github.io/sushi-lang> now reads
  `Sushi Lang <version> - documentation generated <date> - commit <sha>`. The site is
  built from main and carried no version, so a reader could not tell which compiler the
  pages describe. A MkDocs hook (`docs/hooks/version_footer.py`) reads the version from
  `pyproject.toml`, because the docs build has no `sushi_lang` install; a version it
  cannot read stops the build rather than publishing "unknown". The site stays
  unversioned: it always shows main, and the stamp says which main.
- **The semantic passes have names, not numbers.** The numbering had gone out of order:
  `Pass 1` ran after `Pass 1.5`, `1.6`, `1.7`, `1.75` and `1.8`, five steps carried no
  number at all, and `Pass 0` and `Phase 0` named one pass in two spellings. The fifteen
  passes are now `collect`, `externs`, `libraries`, `entrypoint`, `instantiate`,
  `monomorphize`, `resolve`, `finite-types`, `derive`, `shadowing` and `effects` over the
  whole program, then `scope`, `typecheck`, `lift` and `borrow` per unit. A name cannot go
  out of order. Four modules take their pass's name (`passes/resolve.py`,
  `finite_types.py`, `derive.py`, `lift.py`), and the `derive` pass drives both halves of
  its work — the clone loops moved out of `semantic_analyzer.py` into
  `derive.register_all_clones`. "Phase" is now reserved for the three sub-steps of the
  `typecheck` pass. The order lives in the `SemanticAnalyzer.check()` docstring;
  `docs/internals/semantic-passes.md` documents each pass and no longer states the wrong
  order. No compiler behaviour changed: no diagnostic, CLI flag or `EXPECT_*` directive
  ever carried a pass number.
- **The underscore rule is now one underscore between two digits**, checked for every
  base. The old terminals allowed any run of underscores in the interior, so `0xD__E`
  compiled and meant 222, and `0o7__7` meant 63; both are now **CE6006**. The rule
  matches Java, Python, Go, Ruby and JavaScript; Rust and Swift are the permissive
  camp Sushi has left. No literal in the test suite or the docs used a doubled
  underscore, so nothing valid changed meaning.
- **A malformed underscore reports what is wrong** (**CE6006**), instead of the token the
  lexer happened to stop on. The grammar's numeric terminals now match a permissive
  superset and placement is checked in one seam, because a terminal that simply fails to
  match reports the NEXT token: `0x_FF` used to be "unexpected token 'x_FF'" and `1_`
  was "unexpected token '_'". Each diagnostic carries the corrected spelling as a `help`.

### Removed
- **CW2409 (re-borrowing as `poke`) is retired.** Its only trigger was forwarding a whole
  `poke` parameter to another function -- the composition idiom the borrow model
  mandates. The call-site borrow dies with the statement, and CE2403, CE2407 and CE2411
  carry the safety, so the warning marked idiomatic code and guarded nothing. The first
  stdlib consumer fired it 40 times in every importing program. The clean-compile gate is
  `tests/memory/poke_forwarding/test_poke_pass_through_clean.sushi`.

### Fixed
- **A recursive type destroyed in two units no longer crashes the compiler.** The
  out-of-line destructor and clone caches held `ir.Function` handles across unit modules,
  so every fresh-cache multi-unit build that destroys such a type died with a CE0000 ICE
  ("use of undefined value @__sushi_dtor_..."). The caches are per unit now. A
  source-stdlib import makes every consumer a multi-unit build, so the first Sushi
  library met this first.
- **A conditional move no longer leaks the non-moving paths** (#414). A move inside an if
  arm, a match arm, or a loop body cancelled the owner's scope-exit free statically, so
  every path that skipped the move leaked the value — returning a local from one match
  arm leaked it on the other arm, and which arm leaked depended on emission order. The
  borrow pass now stamps conditionally moved owners, and the backend guards exactly their frees with
  a runtime drop flag; an unconditional move keeps the zero-cost static skip.
- **The documentation highlighter knows the current syntax again.** The Pygments lexer
  is not on the compiler's path, so the language moved under it in silence: its last real
  refresh targeted 0.10.0, and the version in its docstring was bumped twice over a lexer
  nobody had read. Three defects, all now fixed and gated:
  - **`@` had no rule at all.** The 0.11.0 generic form renders it unstyled, 1679 times
    across the corpus -- every `List@(i32)`, every `fn id@(T)`. It is punctuation now,
    like the parenthesis it always precedes.
  - **Two numeric shapes split in half.** An exponent-only float lexed as an integer
    followed by an identifier (`1e10` was `1` and `e10`), and the underscores 0.11.1
    added never reached the exponent (`1_0.2_5e1_0` lost its `_0`). The numeric rules
    mirror the grammar terminals now, in one place and with the same permissive
    superset.
  - **An interpolation was lexed by a second, smaller expression lexer** that knew
    neither `[`, `??` nor `==`, so `"{arr[0]}"` and `"{x.get(0)??}"` came out unstyled.
    `{...}` holds an ordinary expression and is lexed as one.

  `tests/unit/test_pygments_lexer.py` is what stops the next drift: every keyword in the
  grammar must be known to the lexer, every numeric shape the grammar accepts must lex as
  one token, and no character in the 1840-file corpus may reach the catch-all rule. The
  version claim is gone from the docstring -- it lied twice, and the gate is the honest
  version of it.

### Testing
- **The badges report both test suites, and report the run that gated the commit.** The
  `badges` job ran the corpus without `--enhanced`, so "Total Tests" counted compile-only
  checks while the real gate ran the binaries; pytest was not in the badges at all; and
  `|| true` plus `continue-on-error` plus a bare `json.load` left a crashed run showing
  its last green. Four badges become three -- `tests`, `sushi tests`, `python tests` --
  fed by the artifacts of the gating run. The badge goes red if either layer fails or if
  a job dies with nothing to say, and yellow when a leak check is skipped rather than run.
  The job publishes from main only, and the test jobs now run on a push to main as well,
  which closes a hole where a merge to main ran no test job at all. The renderer moved
  out of a heredoc into `.github/scripts/make_badges.py`, with tests.
- **Nine dead test files removed, and the eleven unexplained pytest skips resolved.** A
  measurement of the suite prompted this: pytest is 651 hand-written functions (the
  larger count is `parametrize` expansion), about 37 of 114 modules are the one-seam
  gates, and 14 assert-free candidates were all false positives. The `.sushi` corpus lost
  eight near-duplicates out of 1707 files, and `tests/types/test_types_f32.sushi` -- which
  claimed to verify an f32 declaration and had an empty body -- got a real one. Nine of
  the eleven skips were vacuous-case filters and left their `parametrize` lists. CI now
  fails when `sushic` is off PATH instead of skipping silently, the bug-dodging gate reads
  Python skip reasons and `xfail` markers as well as `.sushi` comments, and a `slow`
  marker gives `pytest -m "not slow"` in 67s against 162s.

## [0.11.0] - 2026-08-20

Three breaking language changes (generic syntax, ownership, borrow by default), the
reference-seam programme that made every borrow rule enforced in both directions, the Tier 0-6
remediation programme, the leak/RAII cluster, and the sixteen-round bug-clearing campaign that
emptied the ranked bug table. Everything below landed after the 0.10.0 release on 2026-07-07.

### Breaking
- **Borrow by default: a parameter mode is DECLARED, not derived from the callee** (#354;
  normative spec: `docs/design/borrow-model.md`). The compiler used to read the convention off the
  callee's implementation, so six kinds of callee gave four different answers -- and a stdlib call
  and its Sushi-source twin gave OPPOSITE ones. Four modes now, marked at both ends or at neither:

  | declaration | call site | who frees |
  |---|---|---|
  | `fn f(string x)` | `f(s)` | caller -- the default |
  | `fn f(nom string x)` | `f(nom s)` | **callee**; a later use of `s` is `CE2405` |
  | `fn f(peek string x)` | `f(peek s)` | caller; by pointer, read only |
  | `fn f(poke string x)` | `f(poke s)` | caller; by pointer, read/write |

  The user-visible consequences:
  - **`peek` and `poke` lose the `&`.** `&peek T` is now `peek T`, in every position: a parameter,
    a receiver (`poke self`), a call site, a `foreach` binding, a match binding, a function type.
    The three words are reserved
  - **A plain call no longer consumes.** `f(x)` leaves `x` yours, so the `.clone()` that used to be
    mandatory at ~20 call sites is not. Handing a value over is `nom` at both ends: missing or extra
    is **CE2427**
  - **The #298 method rule became the general rule.** Every parameter of every callable is a borrow
    unless it says `nom`, so writing through one is `CE2422` and consuming one is `CE2411` in a plain
    function too. `nom` works on a method parameter; `nom self` does not exist
  - **A pass-through needs `nom`.** `fn identity@(T)(nom T x) T` -- the mode is declared, so it does
    not vary per instantiation
  - **`nom` on an FFI extern parameter is CE2428.** A C callee never receives a Sushi value
  - **`.slib` format version 2 to 3.** A parameter record carries its `mode`, so a library can
    declare a borrow; an older container is rejected with `CE3509` rather than read with a guess
  - Fixed: the false `CE2405` at every stdlib call site that passed an owning value (#355); a method
    rebinding an owning parameter freed the caller's value (#356); `run()` leaked its collected argv
    (#357); an owning temporary passed to a method leaked (#358)
- **The ownership model is unified** (the ownership-refactor branch; normative spec:
  `docs/design/ownership-conventions.md`). One predicate answers "does this type own heap"
  (`typesys.owns_heap`), the compiler inserts NO implicit deep copy, and `.clone()` is the only
  deep copy in a program. The user-visible consequences:
  - **`string` moves.** A `string` owns heap and transfers at every ownership sink. A string bound
    directly from a literal owns no heap and still copies freely.
    *(Superseded within this release: `f(s)` does not move it -- `f(nom s)` does.)*
  - **A field read, an index and a container get-out are BORROWS.** Consuming one -- a call
    argument, a constructor field, a container insert, a return, a capture -- is `CE2411`; the
    escape is `.clone()`. `HashMap.get()` / `List.get()` / `arr[i]` no longer return independent
    copies
  - **A `let` binds; it does not take ownership.** A `let` bound from a borrow owns nothing, and
    mutating, freeing, rebinding or moving its owner while that binding is live is `CE2412`
  - **A by-value parameter is owned by the callee**, which frees it at scope exit -- uniformly for
    strings, function values, arrays, structs and enums, over direct, variadic and indirect calls.
    Extension/perk-method parameters stay borrows.
    *(Superseded within this release by borrow by default, above: the parameter is a borrow unless
    it says `nom`, and the method exception became the rule.)*
  - **A reference-typed `let` is rejected** (`let peek T x = ...`, `CE2413`, #252). It used to
    compile as an unchecked alias
- **Generic syntax is now `@(...)`, not `<...>`** (#235). This applies to every user-facing position:
  type references (`List@(i32)`, `Result@(T, E)`, `Maybe@(T)`, `Own@(T)`, `HashMap@(K, V)`), generic
  struct/enum declarations (`struct Pair@(T, U):`), generic function declarations
  (`fn id@(T)(T x) T:`), perk bounds (`@(T: Hashable + Displayable)`) and type packs
  (`@(...Ts: Display)`). Angle brackets no longer parse as a generic anywhere, so every existing
  source file must be updated. `use <io/stdio>` import paths are unchanged. Internally, interned type
  names and mangled symbols keep the `<...>` form; `display_type()` renders them back to `@(...)` for
  diagnostics, so user-visible output is consistent. The `>>`-splitting postlexer
  (`internals/generic_lexer.py`) is deleted -- ambiguity between `>>` and a nested generic close is
  gone by construction
- **Move semantics are now compositional** (#134). A value moves iff it *transitively* contains an
  owning resource -- a dynamic array, `List@(T)`, `Own@(T)`, a capturing closure, **or any
  struct/enum/fixed array holding one of those**. Previously only the three bare owning types moved
  and a struct wrapping one silently deep-copied, so refactoring `f(list)` into `f(Wrapper)` turned a
  move into a hidden O(n) copy. Passing such a value by value, rebinding it, placing it in a
  constructor field or array literal now moves it; reusing the source is `CE2405`. Plain-data and
  string-only composites still copy. The normative spec is `docs/design/move-semantics.md`
- **The test harness flag `--leaks` is removed** (#241). There are two modes now, not three:
  `--enhanced` runs everything and enforces everything it declares, and `--leaks-only` selects just
  the leak-annotated subset for CI's fast pre-gate. Both apply the identical check, so the flag that
  used to mean "also enforce leak assertions" no longer names anything. `--leaks` is an argparse
  error rather than a silent alias, and both harness parsers set `allow_abbrev=False` so it cannot
  resolve as a prefix of `--leaks-only` and quietly narrow a 1293-test run to 96
- **`arr.pop()` returns `Maybe@(T)`** (#377), matching `arr.get()` and `List@(T).pop()`. Popping
  an EMPTY array used to invent a value -- a real `0` for an `i32[]`, indistinguishable from a
  genuinely popped one -- and for any aggregate element (`string[]`, a struct with an owning
  field) it was a `CE0000` before the program ever ran. `a.pop() + 1` becomes `a.pop()?? + 1`;
  the popped element moves out inside the `Maybe.Some`

### Added
- **The read-only receiver gate: a write cannot reach through a borrow, and each kind says so**
  (`docs/design/borrowing.md` S5 -- six kinds, one dispatcher, each with its own code and its own
  escape). Every one of these shapes used to COMPILE CLEAN and lose the write, corrupt memory, or
  both:
  - **`CE2414`** (#253): a write through a `match`/`foreach` binding -- a mutating method
    (`p.push(9)`), a field assignment (`b.w := 99`), a `poke` borrow of it -- landed on a private
    deep copy and was silently discarded. A rebind of the binding itself (`n := 99`) stays legal
  - **`CE2408` widened from one shape to all of them** (#302, #307): only the rebind of a `peek`
    reference was checked; a mutating method (`a.push(9)`, `m.free()`), a field chain
    (`h.items.push(9)`), a field assignment and the `poke`-of-`peek` upgrade (#307) all wrote
    straight through to the caller's value
  - **`CE2421` / `CE2422`** (#326): a write through `self` or a by-value parameter -- a plain
    field write was silently lost, an owning field write was a double free plus a leak, and
    `self.items.push(9)` did not even reach codegen. Escapes: `poke self` / `poke T` / `nom T`
  - **`CE2426`** (#344): a write through a `let`-borrow binding (`let i32[] v = h.items` then
    `v.push(9)`, `v.destroy()`, `f(poke v)`). A `let`-borrow shares the owner's DATA, so a
    reallocating push freed the owner's buffer, which the owner then freed again
  - **`CE2429`** (#352 ruling, #407): a write through an UNBOUND CHAINED borrow. The rule: a
    write receiver must reach its root -- a name -- through member and index steps only;
    everything past a call boundary is a temporary copy. `o.get().items.push(9)` compiled,
    printed the old length, and the leak counters balanced; the indexed and field-assign faces
    were internal errors. Reads through the chain stay legal, and the chained indexed READ
    (`o.get().items[0]`, a `CE0000`) was fixed with the gate. A FRESH temporary
    (`from([1, 2]).push(9)`) is rejected by the same rule -- the statement discards the value.
    Escapes: clone-mutate-rebuild (`o := Own.alloc(h)`), or a nested `Own(poke inner)` binding
  - `CE2096` (a constant) completes the set; `tests/unit/test_readonly_receiver_matrix.py` pins
    every kind x shape cell
- **Reference bindings: `poke`/`peek` in a binding position mutates or reads IN PLACE**
  (#300, shipped in three phases; #327). `foreach(poke r in rows.iter())`, `Own(poke x)`, and --
  on the new aligned enum payload layout -- a top-level match binding `Shape.Poly(poke p)` bind a
  POINTER into the owner's storage, so `p.push(9)` and `r.n := 5` reach the owner with no copy.
  The binding registers as a reference, so the existing rules apply by construction: a `peek`
  write is `CE2408`, a consume is `CE2411`, and the OWNER IS FROZEN while the binding lives
  (`CE2412` -- mutating the container inside the loop, or rebinding the scrutinee under a payload
  borrow, Rust's E0506). Fences: `CE2423` (a ref binding over an iterable whose items have no
  address -- a range, `HashMap.entries()`), `CE2424` (a ref binding in a NESTED pattern).
  **`poke self`** (#327) is the receiver's spelling: `extend Counter bump(poke self) ~:` makes
  `self.n := v` and `self.items.push(9)` reach the CALLER's value; `peek self` states the
  read-only default; a receiver parameter anywhere else is `CE2425`. The enum layout that makes
  the match phase possible is `{i32 tag, [K x i64] data}` -- 8-aligned payload, one offset
  authority, the `align=1` access family retired
- **`CE2415`-`CE2420`** (#314-#319): a reference type in the six positions that have no semantics
  for one -- a struct field (was a `CE0017` ICE), an enum payload (was silently untracked), a
  RETURN type (**compiled into a dangling read of a dead frame**), a nested `peek peek T`, a
  generic type argument (was a `CE0022` ICE; no `Maybe`/`Result` exemption, because those two are
  exactly how a returned borrow would escape into a `match`), and an extension/perk target (was
  permanently unreachable dead code). Two positions promoted to tested support by the same batch:
  a reference parameter in a function type (`fn(peek i32) -> i32`) and the lambda satisfying it
  (`|peek i32 x|`)
- **Consuming a borrow is `CE2411` everywhere, references and method parameters included**
  (#301, #310, #311, #333, #338). A `peek`/`poke` parameter handed to an owning position
  (`eat(a)`, `return Result.Ok(a)`, `Holder(a)`, `l.push(s)`) used to be a `CE0129` ICE, and a
  `let` bound from one was a **compile-clean double free**. `return self` / `eat(self)` /
  `sink.push(self)` -- and the explicit-parameter twins -- were compile-clean double frees too
  (#333). The `string` carve-out was removed (#338): the view it let escape DANGLED when the
  receiver was a caller's local, so the `Display` idiom is `return self.clone()` -- and
  `string.clone()` itself copies UNCONDITIONALLY now (#340; it used to pass an `owned = 0` source
  through as an alias, so the escape dangled exactly like the thing it escaped). `.clone()` and
  `.hash()` work on a reference receiver (#312, #308 -- both were ICEs)
- **`CE0131`** (#398): `??` in an extension or perk method body. These bodies return a bare value
  (`CE2091`), so there is no error channel to propagate into. It used to be a misleading `CE2508`
  ("change the return type" -- which nothing can satisfy), and a TEMPLATE nobody instantiated was
  accepted silently; the check runs at Phase 0 collection, so it covers both. A lambda inside such
  a body keeps its own error channel, so `??` inside one is legal (#399)
- **The HashMap key gate actually fires** (#272): `CE2054` (unhashable key) was dead code -- an
  unhashable key compiled and crashed at codegen; a key type reaching a foreign `ptr` was a raw
  `NotImplementedError`, now `CE2055`. A perk `hash` implementation on the key type satisfies the
  gate, and the map probes with it
- **Indexed assignment: `arr[i] := value`** (#261). Both array kinds, every element type, and an
  array that is a struct field. The form parsed already and fell through the rebind dispatch to a
  `CE0022` internal error. The index is bounds-checked through the same helper the READ uses, so
  `RE2020` at run time and `CE2012` for a literal index past the end of a fixed array come for
  free. It is an ownership sink: the element the write replaces is freed first, an owned source is
  moved (`CE2405` on a later use), and a source that reads through an owner is `CE2411` with
  `.clone()` as the escape. The write must be able to reach the owner, so it is rejected through a
  `peek` parameter (`CE2408`), a `match`/`foreach` binding (`CE2414`), a method receiver without
  `poke self` (`CE2421`), an unmarked parameter (`CE2422`), a `let` binding that borrows from an
  owner (`CE2426`), and a constant (`CE2096`). `ConsumingUse` gains its twelfth member
- **`const string[N]` works** (#260). A `string`-element array constant emits a
  `[N x {i8*, i32, i8}]` global with `owned = 0` on every element, so RAII never frees a literal.
  Index, `.len()`, `.get()`, `.iter()`, `.hash()`, copying into a local and shadowing all work. It
  used to emit NO global at all -- the constant evaluator cannot build a string value, because one
  needs a global to point at and the evaluator has no module -- so every USE reported `CE0055`
  while the declaration reported nothing
- **`.clone()` is total over types.** Every struct and enum has an auto-derived clone, and so do a
  primitive (a plain copy), a `string`, a fixed array, a dynamic array, `List@(T)`, `Own@(T)`,
  `HashMap@(K, V)` and a function value (its heap environment is duplicated through the fat
  pointer's `clone_ptr` -- the fat pointer is four words now). `extend i32 clone()`,
  `extend f64 clone()` and `extend string clone()` are `CE2097`, on the same footing as
  `extend P clone()`. A method call on a function value is validated at all now: `g.shout()`
  reports `CE2008` instead of an internal error
- **Type-argument inference through a borrow.** `fn f@(T)(peek T x)` called as `f(peek v)` infers
  `T`; `peek Pair@(A, B)` binds both. The instantiation collector also infers from a `.clone()`
  argument (`f(p.clone())`). A generic that only reads its argument takes it as `peek T`
- **A rebind re-initializes its binding.** `f(s); s := "new"; println(s)` compiles and runs: the
  rebind clears the move and the scope exit frees the new value. The rebind also frees the OLD
  value where the binding still owned it (a string buffer and a closure environment were not freed
  at this position before). A rebind on one branch of an `if` stays conservative
- **An owned temporary that no binding names is freed.** `println(go().realise("err"))`, a `??`
  unwrap, a string-method result, and a string-typed interpolation part (`"{s.upper()}"`) are
  registered into the print-argument frame and freed after output through the owned-bit-guarded
  destructor
- **The ownership seam has a no-bypass gate.** Every transfer in the backend goes through
  `backend/ownership.py`; `tests/unit/test_consuming_use_coverage.py` fails the build when any
  other backend module touches a move-mark primitive. The clone/destroy pair per type kind lives in
  one handler table (`backend/lifecycle.py`), with a totality test asserting both halves per kind
- **`CE2097`: an extension method cannot shadow a built-in** (#239). Every layer of method
  resolution -- validation, inference and code generation -- picks a built-in *before* falling back
  to extension methods, so a colliding extension is compiled and then never called. It is not
  lower-priority, it is unreachable, which is the bar the `CW3505` note sets for erroring rather
  than warning. Verified silently dead beforehand: `extend P hash()` returned the compiler's FNV
  hash, `extend i32 to_str()` printed `5` rather than the user's string, `extend Box@(i32) hash()`
  was never even examined, and `extend string len()` collided at *link* time with the stdlib symbol
  (`CE0007`). The check covers every built-in family -- the compiler-derived `hash()`/`clone()`, the
  primitive and string methods, the array methods, and the `Result`/`Maybe`/`Own`/`List`/`HashMap`
  methods -- keying on whether a built-in genuinely exists for that exact (type, name) pair, never
  on the bare method name. Perk implementations are the sanctioned override and are unaffected:
  they take precedence at every layer by design. The rule is now written down in
  `docs/design/method-resolution.md`
- **`CE2096`: an in-place array method cannot target a constant** (#248). `.fill()` and `.reverse()`
  mutate their receiver, and a constant is emitted as a `global_constant` -- i.e. `.rodata` -- so a
  store through its address would be undefined behaviour rather than a diagnostic. Both used to be a
  `CE0000` ICE; a naive address fallback would have turned that into a runtime SIGBUS instead. Copy
  into a local and mutate that; a local *shadowing* a constant is freely mutable
- **`CE2095`: a type that contains itself by value has no finite size** (#240). Reported with the
  cycle chain (`A refers to B refers to A`), the way Rust reports `E0072` and Go reports "invalid
  recursive type". Indirection stays legal (`Own@(T)`, a dynamic `T[]`, `List@(T)`); a *fixed*
  `T[N]` is by value and is rejected. `struct S: S inner` used to escape as a bare `ValueError`
  rendered as `CE0000`, and the fixed-array and struct-to-enum-payload cycles compiled silently.
  A pure-enum cycle is still `CE2052`, which is more specific
- **Explicit call-site type arguments** (#235, closing #137 part 2): `identity@(i32)(5)`. All-or-
  nothing (`CE2062` on a partial list; a trailing pack relaxes the count), and only on a direct call
  to a named free function (`CE6102` on a method or indirect call). This makes a type parameter that
  appears only in return position expressible: `fn empty_list@(T)() List@(T):`
- **Auto-derived `.clone()` on every struct and enum** (#134) -- the explicit deep copy that
  complements move-by-value, registered in Pass 1.8 alongside the hash derivation
- **A syntax-error diagnostic family, `CE6001`-`CE6010`, `CE6101`, `CE6102`.** A syntax error used to
  bypass the reporter entirely: Lark's raw dump went to stderr with no code, no span and no caret,
  and it did not even set `has_errors`. Syntax errors now carry a code, a location, caret art, the
  expected-token list (capped at 8) and, where one applies, a `help`
- `CE4010`: generic perks (`perk Foo@(T):`) are rejected at declaration. They used to be silently
  accepted and inert. `CE4008`/`CE4009` were deleted as unreachable once this landed
- `CE0119` is now actually raised for a malformed `expand` -- a non-`Name` iterable, a name that is
  not the function's pack parameter (both previously unrolled silently to zero statements), or an
  `expand` in a function with no pack at all (previously a backend ICE)
- `CE2410`: moving `main`'s `string[] args` by value is now a compile error. It is a borrowed view of
  the process argv, so a by-value move made the callee free argv and double-free
- `CE0125`, `CE0121`, `CE0123`, `CE0127`, `CE2016`, `CE2062`, `CE6102` registered; `RE2023` replaces
  an unregistered `RE9999`
- Nested generic element types now render correctly in `List.debug()` / `HashMap.debug()` headers
  (#236) -- `List@(List@(i32)) {` rather than `List@(List<i32>) {`

### Changed
- **No Python traceback can reach a user.** Every failure -- grammar, AST builder, semantics, backend,
  stdlib -- renders as a structured diagnostic. `--traceback` opts one back in and appends it after
  the diagnostic. A compiler crash is a reported `CE0000` ICE and **exits 2**, not 1 (1 is the
  warnings code, which the test harness scored as success)
- The borrow checker now has an arm for every expression node (`CE0125` backstop). The previous
  silent fall-through meant *no borrow checking at all* for unhandled nodes -- the root cause of a
  bloom use-after-free that segfaulted, an unchecked range bound, and unchecked perk bodies
- `.destroy()` through a `poke` parameter now reaches the caller, via a cross-unit destroy-effect
  summary. This also fixed a pre-existing false positive where a `.destroy()` in one `if` arm leaked
  into its sibling arms
- The incremental cache key folds in a compiler-source digest, so editing any compiler `.py` is a
  cache miss by construction; `--clean-cache` is never needed for correctness. The cache is also safe
  for concurrent compilers
- `./nori` no longer swallows exit codes (#234) -- `./nori install nosuchpkg` exits 1
- **A user-declared struct is emitted as an LLVM identified type** (`%Point = type {i32, i32}`)
  rather than an anonymous literal (#257). Internal, with no surface-language effect, but it
  changes the shape of emitted IR and two rules follow from it. `IdentifiedStructType` is a
  *sibling* of `LiteralStructType`, not a subclass, so an `isinstance(..., ir.LiteralStructType)`
  check now means specifically "an anonymous fat pointer" -- use `ir.types.BaseStructType` for
  "any struct". That distinction also removes a latent shape collision: while user structs were
  literal, a `struct S: i32 a; i32 b; ptr p` matched `is_dynamic_array_type`'s `{i32, i32, T*}`
  sniff. The identified types live in a context owned by the codegen, not llvmlite's
  process-wide `global_context`, so two compilations in one process cannot inherit each other's
  layouts; symbol tables now also carry their module's type declarations so the `.slib` merge
  path re-emits them
- `destructors.py` no longer threads an explicit `builder` parameter through its 12 emitters
  (#257). The ambient `codegen.builder` is the backend's convention -- 1388 uses across 78 files
  -- and this was the only file imposing a second one, which is what let an out-of-line body
  emit into two functions at once
- **The borrow checker is a package** (`semantics/passes/borrow/`, the same shape as
  `passes/types/`), deduplicated and dispatched with `match`; diagnostic text verified identical
  across the split. Three diagnostics moved to where they can be answered: `CE2400` is emitted by
  Pass 1 now, so borrowing a constant reports "cannot borrow a non-local" instead of `CE1001`
  "undeclared identifier" about a constant declared two lines up (#330); `CE2401` (move of a value
  borrowed in the same statement, `both(poke s, s)`) is emitted at the consuming use, where it can
  actually fire -- its old site ran before the value walk, so the shape was a compile-clean double
  free (#329); `CE2402` is retired as unreachable. The scope pass gains the `CE0130` totality
  backstop (#245 -- `expand` statements used to get no scope analysis at all)
- **Method resolution has one answer site per question** (#269, #273, #296): the return-type
  tables and the family resolution order are unified, and LOCAL-WINS holds everywhere -- a local
  named after an enum used to resolve to the enum, so a method call on it was a `CE0017` ICE and
  a field read a `CE0034` ICE

### Fixed
- **Silent wrong data, five separate roots:**
  - an UNSIGNED value read through an index, a struct field, a container `get()`, a `??` result
    or `Maybe.realise()` printed as SIGNED -- `u8 255` printed `-1`, `u16 40000` printed `-25536`
    (#379)
  - `open(2)` was declared with a FIXED third parameter while the libc function is variadic, so
    on macOS arm64 the file mode was read off the stack: `copy()` gave its destination whatever
    happened to be there -- `0140` in one program, `0540` in another (#363). Variadic libc
    functions are declared `var_arg` now, and `tests/unit/test_stdlib_copy_mode.py` asserts the
    mode from pytest, because CI structurally cannot reach the macOS-arm64-only miscompile
  - two units with one lambda each collided on `__lambda_0`, so the second unit's closure
    **silently ran the first unit's body** (#402)
  - a moved-out flag leaked across an `if` join (`owns_no_heap`), so a program compiled, moved
    the value, and printed an EMPTY string with no diagnostic (#321)
  - the return-type stamp at a perk boundary was left unresolved, so
    `8.label().expect("boom")` on a `Maybe@(string)` printed an INTEGER (#387)
- **The leak cluster, second round.** Every stdlib call that passes a `string` where the callee
  wants a C string leaked the marshalled copy -- literals as much as owning values, 13 generated
  sites plus `open()`; marshalling now happens at the call site through one seam and is freed at
  scope exit (#291, #292). Every UNBOUND owning temporary leaked -- `give()??[0]`,
  `from([1, 2]).len()`, `a.clone()[0]`, `make_list()??.len()`, `make_own()??.get()` -- because
  the narrow registration router knew only the kinds whose storage is the alloca; one complete
  router now serves them all (#382). Three more owned temporaries had no owner: a string
  temporary outside a print argument (`go().realise("err")` -- unbounded in a loop), the print
  frame on a `??` early-exit path, and a bare enum constant initializer (`Shape.Empty`) (#293,
  #295, #289). A rebind through a `poke i32[]` parameter leaked the old buffer, and the `string`
  twin was a DOUBLE FREE, exit 133 (#304, #303; a `poke List@(i32)` parameter was a false
  `CE2002` "cannot assign List@(i32) to List@(i32)", #305)
- **The borrow checker's control-flow joins** (#287, #294, #321-#324): a rebind now clears every
  stale fact, so five false positives are gone -- `CE2406` after re-initializing a destroyed
  value, `CE2405` after a returning `if` arm and in a second `match` arm, `CE2412` in a sibling
  arm, `CE2411` after re-initialization -- and the one SILENT case (#321) is under "silent wrong
  data" above. A match/foreach binding shadowing an outer local gets its own scope (#337 -- a
  false `CE2411` after the match; #341 -- the item's type leaked out of the loop, a false
  `CE2006`)
- **Function types carry their modes, in both directions.** The `poke` -> `peek` coercion used to
  travel INTO a stored function type, so `let fn(peek i32) -> i32 g = poker` accepted a `poke`
  callee and `g(peek n)` wrote through a read-only borrow with no diagnostic (#335) -- borrow
  modes in a function type are invariant now (`CE2002` both ways). And `nom` was dropped by 8 of
  the 9 places that build a `FunctionType` and compared by none, so a `nom` in a fn-type
  annotation was a false `CE2427`, and dropping the marker to satisfy it was a compile-clean
  DOUBLE FREE (#368); every rebuild preserves the modes and `types_compatible` compares them.
  An indirect call through a fn-typed STRUCT FIELD skipped borrow registration entirely, so a
  program that is `CE2407` through a fn-typed local compiled clean and READ FREED MEMORY (#365)
- **A method call on a chained or indexed receiver works** (#285, #286, #284, #288, #265): a
  field or method chained onto `.realise(x)` was a `CE0000`; `rows[0].hash()` /
  `rows[0].clone()` on a struct or enum element was a `CE0019`; a dynamic array of a user
  struct/enum could not even be FILLED (`arr.push(P(3))` -- a false "expected P, got P"), and
  `Row[]` where `Row` has an `i32[]` field now hashes -- the old `CE0052` rejection was this bug
  wearing a different code; a fn-typed rebind was a false `CE2002`; and a static constructor as
  a PLAIN enum-constructor argument (`Boxed.Wrap(Own.alloc(h))`) was a false `CE0055`
- **A dynamic array has ONE value convention** (#281, #283, and two unfiled twins): `emit_expr`
  yields the `{len, cap, data}` descriptor BY VALUE everywhere, so `Own.alloc(from([1, 2, 3]))`
  and `l.push(from([1, 2]))` no longer die with `cannot store {i32,i32,i32*}* ...`, and
  `o.get().len()` / `o.get()[0]` work. Separately, `List@(i32[])` and `HashMap@(K, i32[])`
  failed with `CE0124` because each container hand-rolled its element-type reader and none had
  an array case -- one shared reader now (`docs/design/array-representation.md` is the spec)
- **An array literal's elements are context-typed in every position that has an element type**
  (#378): a wide literal in a `let`/`const`/`from([...])` was a false `CE2070`, and a plain
  `[1, 2]` against an `i64[2]` field, argument, return or payload was a false
  `CE2028`/`CE2006`/`CE2031`. A NEGATED literal was re-checked against i32 after it was stamped,
  so `let i64 a = -4294967303` was a false `CE2070` with no array anywhere
- **Extension and perk boundaries propagate the declared type** (#387): a generic constructor at
  a method's RETURN (`extend i32 halved() Maybe@(i32)`) or as a method-call ARGUMENT
  (`x.method(Maybe.Some(41))`) was a `CE2008`/`CE0113`/`CE0017`/`CE0055` depending on the type;
  one resolve-then-propagate seam answers all of them
- **Generic-target extensions are read like any other** (#389, #391, #394, #392): an extension
  spelled `extend Box@(T)` or `extend List@(i32)` was never COLLECTED from (false `CE2001`);
  every instantiation SHARED the template's body, so `extend Box@(T) unwrap() T` at
  `Box@(string)` was a compile-clean DOUBLE FREE (it is `CE2411` there now, and legal at
  `Box@(i32)`); an extension on a generic ENUM target compiled into dead code (every call a
  false `CE2008`); and a generic call typed through `self` in such a body was a `CE2061` --
  the monomorphized copies feed a second collection round now
- **Lambdas lift everywhere a statement lives** (#400, #403, #404, #399): a lambda declared in
  an `if`/`elif` arm was a `CE0055` ICE; `|i32 x| may_fail(x)??` drew a false `CW2511`/`CE2511`
  (a lambda has its own error channel); a multi-unit program whose generic extension and
  instantiation sat in different units failed at LINK with a duplicate symbol (`weak_odr` now);
  and a lambda in an extension/perk body lifts too
- **A concrete type argument in an extension target now CONSTRAINS** (#393; the ruling and the
  mechanism are in `docs/design/method-resolution.md`). `extend Box@(i32)` applies to
  `Box@(i32)` and to nothing else, exactly as a perk implementation on the same target already
  did. The argument was stored as a type-parameter NAME and substituted positionally, so the
  method registered for every instantiation of the base type: `extend Box@(i32) tag()` answered
  a `Box@(string)` receiver and printed its answer, and the same declaration with
  `self.value * 2` in the body was a `CE0000` on that receiver. Four consequences, all user
  visible:
  - **Two fully-concrete targets are two methods**, so one method name serves both.
    `extend Box@(i32) tag()` beside `extend Box@(string) tag()` used to be a false `CE0101`,
    because both were one template for a single `(base name, method)` slot
  - **A template plus a concrete target for one name is rejected** (`CE0101`, relational, either
    declaration order). Sushi has no specialization: an unreachable declaration is a diagnostic
    (`CE2097`'s rule), and under most-specific-wins whether the template's body is dead code
    would depend on which instantiations exist elsewhere in the program. The escape is a perk
    implementation on the concrete target
  - **A partially-concrete target is `CE2098`** (`extend Pair@(i32, U)`). It used to compile and
    run. Rejecting it is what keeps a specificity-ordering rule from ever being needed
  - **The diagnostic names the target it turns on** -- `extension method 'tag' for 'Box@(i32)'`,
    where it used to elide the arguments as `Box@(...)`
  Found by the test batch, and fixed here too: **a perk implementation on a concrete generic
  target with more than one argument never registered.** The perk-impl table built the target
  name itself and joined the arguments on `','` where the interned type name joins on `', '`, so
  `extend Pair@(i32, string) with Tagger` registered under `Pair<i32,string>` while the receiver
  resolved to `Pair<i32, string>` -- every call was `CE2008 undefined function`. The
  single-argument case matched by coincidence, which is what hid it
- **A dynamic array or a `List@(T)` of `i16`/`i64`/`u16`/`u64` was a `CE0079` internal error**
  (#375). `get_element_size_constant` was a hand-written chain of `==` comparisons naming i32,
  i8, pointer, float, double and struct -- and no other integer width -- so eight ordinary
  instantiations crashed the compiler. Its sibling `calculate_llvm_type_size` read the width off
  `ir.IntType` all along, which is what made "the two disagree" the shape of the bug rather than
  "the language does not support i64". It reads the width now.
  `tests/unit/test_element_size_is_total.py` asserts the two agree over every integer width, so
  the table cannot go partial again. `HashMap@(K, V)` was never affected: its entry type is a
  struct, which takes the `getelementptr(null, 1)` arm
- **A missing `main()` is a real diagnostic** (#251, `CE3007`), and it names `--lib`. The missing
  `_main` used to reach the LINKER, so the user got raw `cc` stderr followed by a `CE0000`
  "this is a bug in the Sushi compiler" -- for a condition in their own program. A failing link is
  `CE3008` now, carrying the linker's own output as notes; `subprocess.run(..., check=True)` let
  `CalledProcessError` escape to the top-level guard, which renders any uncaught exception as an
  ICE. One helper serves both link paths, and the intermediate `.o` is removed on the failure path
  as well as the success path
- **Five `CE0096` diagnostics printed their own placeholders** (#270) -- an f-string body passed
  without the `f` prefix, so `{receiver.id}.{method}()` reached the user literally. One is
  reachable from ordinary source: a stdio method call without `use <io/stdio>`
- **`CE0004` had two unrelated meanings** (#271). It is registered as `duplicate struct '{name}'`
  and was also emitted as a file-utility ARITY error, with `func`/`expected`/`got` -- so the
  message was about the wrong thing and none of the three parameters matched the template. The
  arity case is `CE2009`, the code the auto-derived builtins already use
- **A foreach binding shadowing an outer struct of a different type read the wrong field index**
  (#279, silent wrong data) -- the backend now resolves the binding's type through the scope-aware
  resolver, which also fixes `CE0056` on a field of a List/array iterator binding (#263) and on a
  nested `Own(inner)` pattern binding with a struct payload (#258)
- **Re-wrapping an enum-payload match binding into a constructor no longer double-frees** (#277,
  was exit 133 with no diagnostic). A match binding is a borrow and a constructor field takes
  ownership, so the shape is rejected at compile time with `CE2411`; the escape is
  `Shape.Poly(p.clone())`
- **A by-value `string` or `fn(...)` parameter no longer leaks.** The seam marked the caller's
  value moved while the callee freed nothing, so a heap string passed by value (`take(b)`) and a
  closure passed by value (`apply(make(2)??, 6)`) leaked their buffers. The callee owns and frees
  them now (see Breaking)
- **Built-in method calls have a return type again** (#239). A method call whose type Pass 2 cannot
  infer is not reported -- `validate_assignment_compatibility` treats an unknown value type as
  nothing to check -- so the annotation goes unvalidated and the mismatch surfaces in the backend as
  a `CE0017` internal error. Three families were affected, for three different reasons:
    - **struct/enum `.hash()` / `.clone()`**: `METHOD_TYPE_REGISTRY` had no checker for a plain
      `StructType`/`EnumType` at all. The new one is registered *ahead* of the container checkers
      (`check_result_methods` claims any method name on a `Result@(T, E)` receiver, and those carry
      an auto-derived `clone()`), and declines `Own@(T)`/`List@(T)`/`HashMap@(K, V)`, whose
      registered hash Pass 2 validation rejects anyway.
    - **every primitive method**: a regression. The inference path read the builtin-method registry,
      which the *backend* populates at import time -- and the pipeline imports codegen lazily, after
      semantic analysis, so it is empty when Pass 2 asks. It had been dead since the commit that
      removed the last `semantics -> backend` import, which was also what had accidentally been
      warming that registry. `let u32 b = f64val.to_bits()` did not even ICE: it compiled and
      silently truncated the 64-bit IEEE-754 pattern to 32 bits. Primitive return types now come
      from a semantics-side table keyed per (method, receiver), since `to_bits` is
      receiver-dependent (`f32 -> u32`, `f64 -> u64`).
    - **`string.to_str()` / `string.hash()`**: `infer_method_type` is first-match-wins, and the
      string checker matched on the receiver type alone -- so it claimed every method name on a
      `string`, and a claim whose inferrer then returns `None` ends the chain instead of falling
      through. Each checker now claims only what it can actually type.

  A perk implementation of the same name still wins at inference, matching validation and dispatch
- **`EXPECT_NO_LEAKS` is enforced by `--enhanced`** (#241). It was gated on the separate `--leaks`
  flag, so a plain `--enhanced` run skipped all 96 leak-annotated tests in silence -- no check, no
  skip notice, nothing in the summary -- and a leaking program passed the full suite. The issue
  described the `--enhanced` check as "weaker" than `--leaks-only`; it did not run at all. The
  interposer is now built on every enhanced run (gating that too meant a fresh checkout degraded to
  96 "not built" skips), a skipped assertion is recorded against the **test** name rather than the
  throwaway temp-binary name that maps back to nothing, and the summary groups skips by reason
  instead of claiming a hardcoded "no leak checker on `<platform>`" that was already wrong for the
  timeout and no-report cases. The two `test_warn_shadow_owning_*` tests, which `--enhanced` did not
  execute at all, now run. Cost of the new default: the full suite goes from 343s to 400s
- **A self-referential container field can be constructed, not just declared** (#257).
  `struct Tree: List@(Tree) kids` declared since #240 but `Tree(1, List.new())` was a `CE0000`
  ICE, so half the feature was unreachable; the `Node[]` spelling failed the same way with a
  different message. The issue described one root cause; there were **two**, and the `List`
  shape hit both. (1) A struct's LLVM type tied its recursive knot by caching an empty
  `LiteralStructType([])` placeholder and re-caching a new literal after walking the fields —
  which cannot work, because a literal struct type is a structural *value* with nothing to
  fill in, so the `{}` the walk had already embedded stayed empty forever and every element
  GEP through it had stride **zero**. User structs are now LLVM *identified* types
  (`%Tree = type {i32, {i32, i32, %Tree*}}`), whose `set_body` fills in place so the knot
  ties itself; `List`/`HashMap`/`Own`/`Entry` stay literal, being anonymous layout
  descriptors whose shape other backend code builds directly. (2) An out-of-line destructor
  body is emitted lazily, mid-emission of another function, and threaded its own builder
  down while the helpers it calls reach for the *ambient* `codegen.builder`/`codegen.func` —
  so the element loop landed in the caller's function and the element-destructor call in the
  destructor, referencing a value defined elsewhere. Both are now swapped for the duration,
  as the clone twin and the closure env destructor already did. Fixing either alone was
  unsafe: (2) without (1) would have replaced a compile error with a **double-free**
- **An array constant is usable directly** (#248): `PRIMES[0]`, `.len()`, `.get()`, `.iter()` and
  `.hash()` all worked only after copying the constant into a local, and were otherwise
  `CE0000: KeyError: 'undefined name: PRIMES'` -- so the documented feature was half-usable. The
  cause was an asymmetry inside name resolution, not a missing fallback: `emit_name` had always known
  about global constants, but every path that needed a name's *address* went straight to the
  local-alloca table. Copying into a local worked precisely because it is the only shape that never
  asks for the address. `backend/expressions/names.py` now owns both halves (`resolve_name_slot`,
  `resolve_name_semantic_type`) and six sites route through them, so the two cannot drift again.
  Reads compile to a `getelementptr` on the read-only global -- no copy, so constants stay zero-cost.
  Fixed with it: constants **shadowed locals** rather than the reverse, so with a `const i32[3] X` in
  scope a local `let i32[4] X` read the constant (`CE0017`) while `X[0]` read the local, and the
  scope pass never marked the shadowing local used (a bogus `CW1001`). Mutating a constant is now
  `CE2096`. Two adjacent gaps found here are filed separately: `const string[N]` never emits its
  global at all (#260), and `arr[i] := v` is unsupported and ICEs for locals too (#261)
- **`find_local_slot` fails as a diagnostic, not a bare `KeyError`** (#248). A name the semantic
  passes already accepted must resolve in the backend; when it did not, the `KeyError` reached the
  top-level guard as an anonymous `CE0000`, which is how five separate missed sites all reported
  identically with nothing naming the gap. It now raises the registered `CE0055`, with
  `try_find_local_slot` as the interrogative form for callers that have a real answer for "not a
  local" -- a global constant, a function reference, a struct field. `tests/unit/`
  `test_find_local_slot_invariant.py` gates it from the AST, so a future caller cannot quietly opt
  back out by catching `KeyError`
- **`Own@(T).get()` no longer hands a borrow to an ownership sink** (#256). `get()` is a
  dereference: it loads the payload uncopied, so the value is a view of storage the `Own` still
  frees. Nothing downstream knew that, so every by-value sink took a second owner of the same heap
  and both freed it. `let Holder back = o.get()`, `takes(o.get())` and `return Result.Ok(o.get())`
  died with SIGTRAP, and `match o.get():` on an owning enum freed the container's payload outright
  via the unowned-temporary path (#159). A `.get()` source is now treated exactly like a
  `MemberAccess` field read — cloned at the sink, per `docs/design/move-semantics.md` §3 (*"ownership
  sinks move; reads from a continuing owner copy"*) — which also retires the narrow name-based
  exception #106 added at the `let` sink, the only sink that knew. Reading straight through a
  get-out (`o.get().field`) takes no owner and still copies nothing. Deep-copying a payload that
  owns nothing is free, so only owning payloads pay; nested `Own@(Own@(T))` now costs one copy per
  level at a sink, the same residual copy §3.1 already accepts for `s.field` and on the same terms
- **Recursive structs compile** (#240). A struct referring to itself through any indirection --
  `Own@(Node)`, `Maybe@(Own@(Node))`, `List@(Node)`, `Node[]` -- was a `CE0000` `RecursionError` on
  *declaration alone*, which broke `docs/examples/20-ownership.sushi` and the linked-list pattern it
  teaches. The root cause was type identity, not the wrapper chain the issue named: `StructType` and
  `EnumType` hashed on the name but compared on the fields/variants, so two instances of one type
  resolved to different depths hash-matched and compared *unequal*, and resolution deep-walked struct
  fields to make structural equality agree. That walk cannot terminate on a cyclic type. The same
  defect was visible with no recursion at all (`CE2002: cannot assign Own@(T) to Own@(T)`) and is the
  root of the `CE0126` class. Named types are now identified nominally, as in Go and Rust; see
  `docs/design/type-identity.md`
- Reading a field straight off `Own@(T).get()` (`o.get().x`) reported `CE0069`, an internal-error
  code, for ordinary user code. Binding the value first already worked
- A generic-enum constructor nested inside a concrete-struct constructor
  (`Own.alloc(Holder(0, Maybe.None()))`) was never given its type and reached the backend as
  `CE0113`. Type propagation into constructor arguments only recursed for *generic* structs, so how
  deep it went depended on whether an intermediate type happened to be generic
- Declaration spans were dropped when per-unit symbol tables merged into the global table, silently
  demoting every diagnostic reported against it to tier 1 (no file:line:col, no caret)
- A cluster of RAII/leak defects: `Result@(T, E)` payloads are destroyed at scope exit (a
  `Result@(string, E)` used to leak unless the caller unwrapped it), `Own@(T)`/`List@(T)` nested
  inside a composite are destroyed, fixed-size arrays and their elements are destroyed, `getcwd`'s
  buffer is freed, an owning element is deep-copied out of `List.get()`, a `Result`/`Maybe` method
  receiver is emitted exactly once, and unowned `Result`/`Maybe` temporaries are destroyed
- `HashMap` and `List`/`Own` struct-field clone and destroy (#181)
- `Result@(T, E)` is now an ordinary interned `EnumType` like `Maybe@(T)`; the separate `ResultType`
  dataclass is deleted. The dual representation was the root of a Result-payload leak and of two
  identically-printing Results comparing unequal
- The `HashMap` probe loops are bounded, fixing a segfault on a destroyed map
- Indexing a fixed-size array through a struct field; inferring a struct type through an array index;
  loading a dynamic-array default in `realise()`; inferring `Own@(T)` method return types so an
  inline `match Own.get()` resolves; propagating the element type to an inline `Own.alloc` constructor
  argument
- Extension and perk-impl return expressions are now type-checked
- Quality gates: `ruff` clean at F,E,W,B and `mypy` clean over `internals`/`compiler`/`packager`/
  `sushi_stdlib`, both blocking in CI, alongside the cross-platform malloc-interposer leak gate
- **The wheel ships the stdlib bitcode** (#413; fixed after the section above was written,
  and shipped in the published 0.11.0). hatchling honours `.gitignore` when it selects
  files and the prebuilt bitcode has been gitignored since #155, so the first wheel carried
  zero `.bc` files and every stdlib-using program died at the missing-module rebuild.
  `tool.hatch.build.targets.wheel.artifacts` is the designed escape for a VCS-ignored build
  output. Two more holes went with it: `upload-artifact` drops hidden files, so the
  `.build_fingerprint` marker never reached the wheel; and excluding `sushi_stdlib/build.py`
  made an installed wheel compute a digest its own marker did not match, because the
  freshness fingerprint hashes that file. The release workflow now asserts all three on the
  built wheel before it publishes, and prints the inventory when one is missing.

### Testing
- The leak gate tells the truth in three more cases: a freed address reallocated by an untracked
  library no longer reports a false `DOUBLE_FREE` (#359); a colliding insert no longer consumes a
  LIVE tombstone, which made a genuine double free go unreported (#371); and an unsupported
  platform is a recorded skip instead of "interposer not built" (#275)
- The test harness itself is ruff-linted, in CI and pre-push -- 38 real harness defects fixed
  (#274) -- and a misconfigured stdlib registry entry raises loudly instead of registering
  nothing (#247)
- `tests/unit/test_path_references_exist.py`: every backtick-quoted `dir/file.ext` reference in
  documentation and comments must name a real file (#366). Its first run found 13 stale
  references beyond the 7 the issue filed

### Documentation
- **`tests/docs_sweep.py`** (#297): compiles every self-contained ```sushi block under `docs/`
  with a four-way outcome -- pass (exit 0 or 1; a warning is not a failure), expected-error
  (`<!-- docs-sweep: error CExxxx -->` on the line above the fence), skip
  (`<!-- docs-sweep: skip (reason) -->`), fail. A tool to run BY HAND, periodically --
  deliberately not a CI job. At close: 249 candidate blocks, 0 failing; the 13 genuinely
  drifting blocks were FIXED, not marked
- The seven stale references to the deleted `semantics/passes/borrow.py` are fixed, preferring
  symbol names over line numbers (#366)
- The borrow model is documented as one table across the language reference, the guide, the
  memory-management page and the tutorial; `docs/design/borrow-model.md` is the normative spec
  for how a value crosses a call boundary, and `docs/design/borrowing.md` for the reference
  mechanisms (#360)

## [0.10.0] - 2026-07-07

The closures release. Tier 1 closures are complete, and three follow-ups land together: an
opt-in combinators module, call-through arbitrary function values, and generic-function
references. This supersedes the 0.9.0 note that there were "no closures in v1".

### Added
- `use <collections/iter>`: `map`, `filter`, `fold`, and `compose` over `List<T>`, as ordinary
  generic free functions. This is the first Sushi-source standard-library module -- a bundled
  `.sushi` file merged as a compilation unit and monomorphized through the normal generic
  pipeline (no bitcode; nothing emitted unless a combinator is instantiated). Element types are
  copy/primitive for now
- Call-through arbitrary expressions that evaluate to a function value (T2.4): a captured closure
  called in a lambda body (`env.f(x)`, the basis for `compose` and capturing-and-calling a
  closure), a fn-typed struct field called directly as `obj.handler()` (a same-named method
  wins), and call-through a `List` get-out or parenthesized expression (`fns.get(0)??(x)`,
  `(e)()`)
- Generic-function references (T2.3): a generic function may be referenced as a value when an
  explicit function type is present, e.g. `let fn(i32) -> i32 g = identity` with
  `fn identity<T>(T x) T`. Passing a generic-fn reference into a higher-order function works via
  such a typed binding

### Changed
- The `??` operator now unwraps a first-class function call's result and a `Maybe` `Some`
  payload during type inference, so a function value called in a lambda body infers its return
  type. As a result, two type mismatches that previously surfaced as a backend cast failure now
  report the precise front-end `CE2002` at the assignment
- `CE2094` no longer rejects capturing-and-calling a closure value; an owning closure value is
  move-captured into the environment like `List`/`Own`
- `CE2093` is lifted for a generic-fn reference that has an explicit expected function type; a
  bare reference with no expected type (an argument position without a typed binding) still
  reports `CE2093`

## [0.9.1] - 2026-07-06

A maintenance release: a new safe process-spawn stdlib primitive (the last self-hosting
linchpin), the correctness fixes surfaced by the 0.9.0 stdout-correctness test baseline, and a
documentation-link fix.

### Added
- Safe process spawning in `sys/process`: `run(string cmd, string[] args) -> Result<ProcessOutput,
  ProcessError>`. Built on `posix_spawnp` — argv, no shell — it returns the child's exit code plus
  separately-captured stdout/stderr in `ProcessOutput`; a non-zero exit is `Ok`, and `SpawnFailed`
  / `SignalReceived` are the `ProcessError` variants. `run("clang", [...])` is verified end to end,
  completing the text-IR -> toolchain path for self-hosting

### Fixed
- `Maybe<T>` / `Result<T, E>` `.realise(default)` crashed for a struct payload with a nested
  aggregate field
- Nested `Own<Own<T>>` was double-freed on cleanup; ownership is now coherent through the nested box
- `HashMap.debug()` corrupted string keys/values in its output

### Testing
- Established a stdout-correctness test baseline: the runtime-output ratchet is at gap 0
  (`BASELINE = 0`) with a quarantine guard, so a wrong codegen result can no longer pass the suite
  silently
- Fixed stale hard-coded paths in the file-I/O tests

### Documentation
- README documentation links now point to the rendered docs site

## [0.9.0] - 2026-07-04

A maintenance release: one new language feature — context-typed numeric literals — plus a
batch of correctness fixes surfaced by a documentation audit, a diagnostic-wording change, and
CI/docs improvements.

### Added
- Context-typed numeric literals (Rust/Go untyped-constant model): a bare numeric literal takes
  its type from its expected/context type — annotation, const, function argument/return, struct
  field, array element, and binary-op operand (`a + 1` where `a: u8` types the `1` as `u8`) —
  range-checked at compile time. A literal with no numeric context still defaults to `i32`/`f64`.
  This is literal *typing*, not value coercion (an already-typed value still needs `as`)
  - New diagnostic **CE2073** (context-typed literal out of range for its target type); **CE2070**
    retained for a context-free literal that overflows its `i32` default

### Changed
- Diagnostic wording: user-facing "rebind" renamed to "reassign" — **CE1002** now reads
  "assignment to undeclared variable" and **CE2401** "cannot move/reassign"

### Fixed
- One-argument `Result<T>` annotation was rejected (CE2001); it now normalizes to
  `Result<T, StdError>` at parse time
- `foreach` / `.iter()` over a borrowed dynamic array (`peek`/`poke T[]`) failed with CE0042
- Higher-order function call-through (`??` through an `fn`-typed parameter) was order-dependent
  (CE0055 depending on definition order)
- `Maybe<enum>.realise(default)` with a non-primitive (enum) payload misrouted to the `Result`
  handler and failed to lower (CE0017)
- Loop-body locals abandoned via `break`/`continue` were never freed (RAII leak); a bounded
  loop-exit cleanup now frees them exactly once without double-freeing locals that outlive the loop
- Inline `match` / `??` on stdlib builtins (`getcwd`, `getenv`, `.find_last`) failed with CE0055 /
  "undefined name" unless the call was first bound to a `let`

### Documentation
- Language reference updated for context-typed numeric literals
- `sys/process` guide examples corrected (all 20 examples compile with zero errors/warnings)
- First-class-functions design note reconciled with the implemented compiler behavior
- CI: docs site auto-deploys after the Tests workflow passes on `main`

## [0.8.0] - 2026-06-11

A milestone release centered on self-hosting enablers: a foreign function interface, three
forms of variadic functions, generic instantiation across library boundaries, and first-class
functions — alongside a hardened test suite, asserted diagnostics, and a large batch of
correctness fixes.

### Added
- Foreign Function Interface (FFI) for calling C
  - `unsafe external "C" as <ns> [because "..."]:` blocks with namespaced call sites
    (`libc.strlen(s)`)
  - Opaque, unmanaged foreign `ptr` type (LLVM `i8*`), exempt from borrow checking and RAII
  - `string` arguments auto-marshalled to C `char*` and freed at scope exit (no leak)
  - Variadic externs: a bare trailing `...` binds libc varargs (`printf` family)
  - Full `ptr` quarantine — unit gate, and rejection of `ptr` in public APIs, operators, method
    calls, and generic arguments; diagnostics CW5001 and CE5001–CE5012
- Variadic functions in three deliberately separate forms
  - Native homogeneous `...T` — trailing arguments collected into an owned `T[]`
  - Parameter packs `...Ts` with a compile-time-unrolled `expand` block (heterogeneous, generic,
    zero-cost) — including perk-constrained packs
  - Parameter packs ship and monomorphize across `.slib` library boundaries
  - Diagnostics CE0114–CE0119, CE2090, CE2091
- Generic instantiation across `.slib` library boundaries
  - Generic functions, generic structs/enums, and variadic-generic packs ship as source templates
    and monomorphize at the consumer
  - Concrete perk implementations ship and register at the consumer
  - Export closure of library-private symbols an exported generic transitively references;
    CE5006 narrowed and CE5007 added
- First-class functions
  - Function types `fn(P...) -> T [| E]` and function values: reference a top-level function,
    store it in a variable/struct field/`List`, pass it, and call through it (no closures in v1)
  - Diagnostics CE2092 (call-through mismatch) and CE2093 (illegal/generic function reference)
- Testing and tooling
  - `pytest` layer for compiler internals (fingerprints, cache, semantic errors)
  - Dedicated enum suite and end-to-end incremental-compilation/cache tests
  - Performance regression harness (report mode)
  - Error codes asserted on the compilation path, with a broad diagnostics backfill

### Changed
- Runtime validation is the default for tests; CI runs the enhanced runner on a macOS + Linux
  matrix
- Internal refactors: `stdlib.py` split into a `stdlib/` package; `TypeValidator` decomposed into
  focused collaborator modules
- CI: GitHub Actions bumped to Node 24 runtimes; tests run on `merge_group`

### Fixed
- i64 width correctness across inference, printing, comparisons, and literals
- Unsigned `/` and `%` no longer emit signed `sdiv`/`srem`
- CE0021 crash on `Result<ptr>`; `ptr` is confined to its declaring unit (CE5008)
- Heap-owning struct value semantics (independent buffer per by-value argument)
- Local `List<T>` variables freed via RAII; dynamic-array RAII leak on the all-but-one return
  path
- `string[]` heap corruption from an element alloc-size mismatch
- `List<EnumType>.destroy()` internal compiler error
- `compute_unit_fingerprint` crash on enums
- `bool` string methods rendering as `0`/`1` in interpolation
- Direct `.hash()` on an enum value (CE0019 internal compiler error)

### Documentation
- Unified MkDocs site combining the guided tutorial and the reference, with Sushi syntax
  highlighting
- New guides and tutorial chapters for FFI / foreign pointers, variadic functions, and
  first-class functions
- Design notes for variadics and first-class functions
- CHANGELOG backfilled for 0.7.0 and 0.7.1

## [0.7.1] - 2026-03-21

### Added
- Glossary section in README

### Fixed
- Nori banner rendering

## [0.7.0] - 2026-03-21

### Added
- Nori package manager (`nori` CLI), shipped alongside the compiler
  - Commands: `init`, `build`, `install`, `list`, `info`, `remove`, `publish`, `search`, `status`, `login`, `help`
  - Packages install to `~/.sushi/bento/` with executable symlinks in `~/.sushi/bin/`
  - `.nori` archive format for distributable library packages
  - Compiler auto-discovers installed libraries without `SUSHI_LIB_PATH`
- Project-level dependency environments
  - Global versioned store (`~/.sushi/store/`) with per-project `.sushi_bento/` symlinks
  - Compiler resolves project-local dependencies before global packages
- Omakase package repository integration (omakase.lubica.net)
  - `nori login <api-key>` with credentials stored in `~/.sushi/credentials.toml`
  - `nori publish` uploads packages; `nori search` browses the registry; `nori status` reports login and published packages
  - Omakase API contract specification: authentication, users, groups, ownership, stats, and package storage layout

### Changed
- CI split into separate test and badges jobs

### Documentation
- New documentation: `docs/package-manager.md` for the Nori package manager

## [0.6.1] - 2026-02-22

### Added
- End-to-end release tests that validate wheels before publishing
  - Installs wheel into clean venv, compiles and runs 5 sushi programs
  - Tests basic compilation, stdlib linking, generics, HashMap, multi-file builds
  - Runs on both Linux and macOS in CI

### Changed
- Release workflow restructured: wheels are tested on both platforms before publishing to GitHub Releases

## [0.6.0] - 2026-02-22

### Added
- Incremental compilation with per-unit object file caching
  - Each .sushi unit compiles to its own .o file, cached in `__sushi_cache__/`
  - Cache invalidation via content-based SHA-256 semantic fingerprints
  - Stdlib and library imports also cached as separate .o files
  - Monomorphized generics use `linkonce_odr` linkage for linker deduplication
- New CLI flags: `--no-incremental`, `--clean-cache`, `--cache-dir`
- HashMap `.entries()` method returning `Iterator<Entry<K, V>>` for key-value pair iteration
- `Entry<K, V>` struct with `.key` and `.value` fields
- Error diagnostic infrastructure with note/help sub-diagnostics
  - Polished rendering with T-junction markers, continuous box drawing, colored notes
- `--platform` flag for stdlib build script

### Changed
- Multi-unit compilation now uses two-path architecture: monolithic (single-file) and incremental (multi-unit)
- Semantic analysis remains whole-program; only LLVM codegen is cached per-unit

### Fixed
- Struct type propagation in `Result.realise()` method
- Missing Linux stdlib .bc files
- CI workflow: always run tests on push, skip only for docs-only PRs
- Reliable code change detection using dorny/paths-filter

### Documentation
- Stdlib build and library format documentation
- Updated compiler reference with incremental compilation section
- Updated architecture docs with incremental compilation internals

## [0.5.0] - 2025-12-20

### Changed
- Restructured package into single `sushi_lang` directory for cleaner pip installation
- Updated all internal imports to use new package structure
- Release workflow now extracts changelog for GitHub release notes automatically
- Test workflow installs dev dependencies (tqdm) via `--extra dev`

### Added
- Package entry points (`__init__.py`, `__main__.py`) for proper Python module support
- PACKAGE.md with packaging and release documentation

## [0.4.2] - 2025-12-17

### Added
- Constant folding for arithmetic and bitwise operations
- Content-based string constant deduplication
- AST type annotations for TryExpr error propagation
- LibraryRegistry to centralize library metadata parsing
- AST visitor base class with complete node coverage
- Semantic analysis pipeline with timing instrumentation
- Progress bar for test runner (tqdm)

### Changed
- Enforce type propagation single entry point via private functions
- Implement type-level COW for monomorphization transformer
- Increase default test timeout from 5s to 10s
- Extend enum_utils with variant data packing and unpacking utilities
- Extract shared type resolution helpers for TypeMapper and TypeSizing
- Extract symbol merging into dedicated SymbolTableMerger class
- Refactor scope manager to use flat cache as primary storage
- Consolidate Result type handling into ResultBuilder class
- Consolidate type resolution functions into TypeResolver class
- Use --frozen flag when invoking sushi compiler

### Fixed
- Reference parameter type resolution and array indexing
- HashMap and generic types passed by reference to functions

### Examples
- Added rudimentary Markov chain example

## [0.4.1] - 2025-12-07

### Changed
- Refactored library_format.py to eliminate DRY violation
- Reorganized tests into appropriate subdirectories
- Test cleanup, added test lib build to run_tests.py

## [0.4.0] - 2025-12-07

### Added
- Unified binary library format (`.slib`)
  - Single file combines LLVM bitcode and MessagePack-encoded metadata
  - Magic bytes: sushi emoji surrounding "SUSHILIB"
  - Version field and reserved space for future extensions
- `--lib-info` CLI command for library introspection
  - Displays library name, platform, compiler version, compile timestamp
  - Lists public functions with full signatures
  - Shows structs, enums, constants, and dependencies
  - Reports bitcode size
- New error codes for library format validation
  - CE3508: Invalid magic bytes
  - CE3509: Unsupported format version
  - CE3510: Metadata section truncated
  - CE3511: Bitcode section truncated
  - CE3512: Invalid metadata (MessagePack decode error)
  - CE3513: File too large

### Changed
- Library output now uses `.slib` extension instead of `.bc`
- Removed separate `.sushilib` JSON manifest files
- Updated CE3500 error message for `.slib` extension requirement

### Dependencies
- Added `msgpack>=1.0` for binary metadata serialization

## [0.3.0] - 2025-12-06

### Added
- Library system for creating and using precompiled libraries
  - `--lib` flag compiles source to reusable bitcode (`.bc`) with manifest (`.sushilib`)
  - `use <lib/name>` syntax imports precompiled libraries
  - `SUSHI_LIB_PATH` environment variable for library search paths
  - Two-phase linking with priority-based symbol resolution (Main > Library > Stdlib > Runtime)
  - Dead code elimination removes unused library functions
  - Platform mismatch warnings (CW3505)
- Library error codes (CE35xx)
  - CE3500: Library output path must have .bc extension
  - CE3502: Library not found in search paths
  - CE3503: Invalid library manifest
  - CE3507: Failed to link library
- Library type registration from manifests
  - Structs, enums, and functions from libraries are registered in semantic analysis
  - Local definitions take precedence over library definitions
- Library integration tests in `tests/libs/`
  - Runtime symbol deduplication
  - Circular function calls
  - Symbol priority/override
  - Dead code elimination
- GenericTypeProvider interface for plugin-style generic types
  - HashMap now conditionally loaded via `use <collections/hashmap>`
- Math module enhancements (`use <math>`)
  - Trigonometric: `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`
  - Hyperbolic: `sinh`, `cosh`, `tanh`
  - Logarithmic: `log`, `log10`, `log2`
  - Exponential: `exp`, `pow`

### Changed
- Removed `--link` flag (breaking change)
  - Libraries are now imported via `use <lib/...>` statements in source code
  - This simplifies the compilation model to a single mechanism

### Documentation
- New documentation: `docs/libraries.md` - Complete library system guide
- Updated `docs/compiler-reference.md` with library options
- Updated `docs/examples/26-libraries.sushi` library usage example
- Updated `docs/stdlib/math.md` with new math functions

## [0.2.0] - 2025-11-27

### Added
- Dual-mode borrow syntax replacing single `&` operator
  - `peek T`: Read-only borrow (multiple allowed simultaneously)
  - `poke T`: Read-write borrow (exclusive access)
- Type coercion: `poke T` can be passed where `peek T` is expected
- New error codes for borrow checking:
  - CE2407: Cannot have peek and poke borrows simultaneously
  - CE2408: Cannot modify through peek reference (read-only)

### Changed
- Reference syntax now requires explicit borrow mode (peek or poke)
  - Old: `fn process(&i32 x) ~`
  - New: `fn process(poke i32 x) ~` or `fn process(peek i32 x) ~`
- Borrow checker updated for dual-mode semantics
- All tests migrated to new peek/poke syntax (29 files)

### Breaking Changes
- Plain `&T` syntax removed (no backward compatibility)
- All existing code using `&T` must be updated to `peek T` or `poke T`

## [0.1.0] - 2025-11-27

### Added
- Result<T, E> error type system implementation
  - Custom error types with | syntax: fn foo() T | ErrorType
  - Explicit Result<T, E> syntax for nested Results
  - Six built-in error enums: StdError, MathError, FileError, IoError, ProcessError, EnvError
  - Error type validation: CE2085 prevents mixing explicit Result with | syntax
- sys/process stdlib module for process control
  - exit(code) - Terminate process with exit code
  - getpid() - Get current process ID
  - sleep(seconds) - Sleep for N seconds (POSIX-compliant)
- Hash functions for Result<T, E> types
  - Enables Result types as HashMap keys
  - FNV-1a combining hash for Ok/Err variants
- Equality operations for Result<T, E> types
  - Enables == and != comparisons between Result values
- Warning CW2511 for ?? operator usage in main()
  - Encourages explicit error handling in entry point
  - Prevents propagation from top-level function

### Fixed
- Result<T, E> enum type size calculation for unit error types
- Result type resolution for stdlib file operations in match statements
- Result<T, E>.realise() type inference and integer conversions
- Result<T, E> recursive type propagation and stdlib backend integration
- Result<T, E> type propagation in Let statements
- Result method validation with user-defined generic enums
- Result<T, E> support for generic return types
- Nested Result pattern matching with enhanced type resolution
- Explicit Result<T, E> double-wrapping prevention
- Struct field extraction from Result enums with LLVM padding handling
- Result.Err() error type validation requiring error values
- FuncSig parameter types synchronization when resolving GenericTypeRef
- Result<T, E> boolean conditionals support (if/while statements)

### Changed
- Result.Err() now requires error value argument
  - Old: Result.Err()
  - New: Result.Err(StdError.Error)
- Result type annotation syntax requires explicit error type
  - Old: Result<T>
  - New: Result<T, E>
- Refactored type validation into modular components
  - semantics/passes/types/resolution.py - Type resolution
  - semantics/passes/types/propagation.py - Type propagation
  - semantics/passes/types/result_validation.py - Result pattern validation
- Refactored backend constants into modular architecture
- Refactored stdlib string operations into modular structure
- Refactored generics system (instantiate.py, monomorphize.py, collect.py)
- Complete test suite migration to Result<T, E> syntax

### Documentation
- Updated all 25 documentation examples to Result<T, E> syntax
- Updated language-reference.md with Result<T, E> type system
- Comprehensive error handling documentation in docs/error-handling.md
- sys/process module documentation in docs/stdlib/process.md
- ?? operator usage guidelines and best practices

## [0.0.12] - 2025-11-17

### Added
- Named parameters for struct constructors
  - Order-independent syntax: Point(y: 20, x: 10)
  - All-or-nothing: cannot mix positional and named arguments
  - Zero-cost abstraction resolved at compile-time
  - Error codes: CE2080-CE2083 for validation
- Single-quote string literals ('...') for plain strings without interpolation
  - Double quotes ("...") support {expr} interpolation
  - Single quotes provide literals for use in interpolation arguments
  - Example: {text.pad_left(10, '*')} uses single quotes for arguments
- File utilities in io/files module
  - remove(path) - Delete files
  - rename(old_path, new_path) - Rename/move files
  - mkdir(path, mode) - Create directories with permissions
  - rmdir(path) - Remove empty directories
  - copy(src, dest) - Copy files
- String methods in collections/strings module
  - reverse() - UTF-8 aware character-level reversal
  - repeat(n) - Repeat string n times
  - count(needle) - Count non-overlapping occurrences
  - find_last(needle) - Find last occurrence index
  - join(separator, array) - Join string array with separator
  - pad_left(width, pad_char) - Left-pad to width
  - pad_right(width, pad_char) - Right-pad to width
  - strip_prefix(prefix) - Remove prefix if present
  - strip_suffix(suffix) - Remove suffix if present

### Fixed
- Pattern matching segfault on Result<T> function calls
  - Added Call node handling in _get_scrutinee_type()
- HashMap array key implementation
  - Corrected GEP indexing for fixed and dynamic array equality
  - Fixed arrays now use gep_fixed_array_element() utility
- Directory-based stdlib imports now include submodules
  - use <collections> properly provides collections/strings
- String methods now UTF-8 aware
  - ss(start, length) works with character indices instead of bytes

### Changed
- Migrated to uv-only dependency management with direnv integration
- Refactored version management to use pyproject.toml as single source of truth
- Refactored file utilities to use fat_pointer_to_cstr() helper function
- Reorganized stdlib documentation into modular structure (docs/stdlib/)

### Validation
- Dynamic arrays disallowed as HashMap keys at compile time (error CE2058)
- Dynamic arrays disallowed in enum variants at compile time (error CE2059)
- Fixed arrays remain supported in both contexts

### Documentation
- Comprehensive documentation for named struct constructors
- Single-quote string literal syntax documented across language reference
- File utilities documentation in docs/stdlib/io/files.md
- String methods documentation in reorganized stdlib docs

## [0.0.11] - 2025-11-11

### Added
- Range expressions with .. (exclusive) and ..= (inclusive) operators
  - Zero-cost iteration that compiles to optimized for-loops
  - Automatic direction detection (ascending vs descending)
  - Supports break/continue statements
  - Returns Iterator<i32> for consistency with array iteration
- Random number generator module (<random>)
  - rand() -> u64: Random 64-bit unsigned integer
  - rand_range(i32, i32) -> i32: Random integer in range
  - srand(u64) -> ~: Seed RNG for reproducibility
  - rand_f64() -> f64: Random float in [0.0, 1.0)
  - POSIX-compliant using libc random()/srandom()
- Manual workflow dispatch trigger for CI

### Fixed
- GitHub Actions badges display

## [0.0.10] - 2025-11-11

### Added
- Full Linux platform support with CI testing
- Platform-specific errno access (`__errno_location` on Linux, `__error` on macOS)
- Platform-specific linker flags (`-lm` for math library on Linux)
- Stdio platform abstraction (stdin/stdout/stderr handles)
- Docker-based Linux build testing script

### Fixed
- Generic function enum handling in Match statements
- Perks with nested generic functions
- Buffer overflow in Maybe<T> for struct field method calls
- Method calls on borrowed variables inside functions
- Array access with .get() in structs
- Test suite compatibility with array fixes

### Changed
- Moved stdio platform implementations to `stdlib/src/_platform/{darwin,linux}/stdio.py`
- Updated backend to use platform detection for linker flags and errno functions
- Updated documentation to reflect Linux support status

## [0.0.9] - 2025-11-07

### Added
- Compile-time constant expression evaluation
  - Arithmetic operations: +, -, *, /, %
  - Bitwise operations: &, |, ^, ~, <<, >>
  - Logical operations: and, or, xor, not
  - Comparison operations: ==, !=, <, <=, >, >=
  - Type casts: as operator
  - Constant references with cycle detection
  - Array constants with constant elements
- Hexadecimal numeric literals (0xFF, 0xDEAD_BEEF)
- Binary numeric literals (0b1111, 0b1010_1010)
- Octal numeric literals (0o755, 0o644)
- Comprehensive constant expression test suite (19 tests)
- Test metadata guide (tests/TEST_METADATA_GUIDE.md)
- Error codes CE0108-CE0112 for constant expression validation

### Fixed
- ArrayType attribute error in const_eval.py (element_type -> base_type)
- Test logic bugs in test_constants_logical.sushi and test_constants_complex.sushi

### Changed
- Refactored AST builder into modular architecture (40+ modules)
  - semantics/ast_builder/ with utils/, types/, expressions/, statements/, declarations/
- Reorganized test suite into logical directory structure
  - tests/basic/, tests/constants/, tests/control_flow/, tests/error_handling/
  - tests/generics/, tests/io/, tests/literals/, tests/memory/, tests/operators/
  - tests/stdlib/, tests/strings/, tests/types/, tests/array/, tests/list/, tests/perks/
- Updated docs/language-reference.md with constant expressions section
- Updated docs/compiler-reference.md with constant expression error codes
- Bumped version to 0.0.9

### Documentation
- Complete constant expression syntax and examples
- All 5 constant expression error codes documented
- Test metadata format and requirements

## [0.0.8] - 2025-11-05

### Added
- Perks (traits/interfaces) system with static dispatch
  - Phase 1: Grammar, AST, and parsing support
  - Phase 2: Type system integration (PerkTable, PerkImplementationTable)
  - Phase 3: Implementation validation and checking
  - Phase 4: Constraint validation for generic structs, enums, and functions
  - Phase 5: Code generation with bare return types
- Generic functions with automatic type inference
- Generic functions with perk constraints (T: PerkName)
- Multiple perk constraints support (T: Perk1 + Perk2)
- Synthetic perk implementations for primitive types
- Comprehensive perks test suite (38 tests)
- Comprehensive perks documentation in docs/perks.md
- Example 23: Basic perks usage (docs/examples/23-perks-basic.sushi)
- Example 24: Generic constraints with perks (docs/examples/24-perks-constraints.sushi)
- Complete examples catalog in docs/examples/README.md
- Standard library documentation
  - docs/standard-library.md
  - docs/stdlib/env.md (environment variables)
  - docs/stdlib/math.md (mathematical functions)
  - docs/stdlib/platform.md (platform-specific APIs)
- Enhanced internals documentation
  - docs/internals/architecture.md
  - docs/internals/backend.md
- GitHub Actions workflow for automated testing
- Test badges in README.md showing test status
- Linux stdlib support in test builds

### Fixed
- f64 arithmetic and comparison operations
- Generic extension methods resolution during type validation
- Mixed-type numeric operations and validation
- Struct/enum extension method lookup
- Perk method argument type validation
- Result handling in perk tests
- BoundedTypeParam compatibility with generics system

### Changed
- Refactored name mangling to use shared utility functions
- Updated README.md with perks section and example
- Updated README.md with improved feature descriptions
- Updated docs/README.md to include perks in language reference
- Improved error messages for perk constraint violations

### Removed
- Various obsolete markdown documentation files

## [0.0.7] - 2025-11-03

First public release.
