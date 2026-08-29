# Compile-time evaluation

Issue #446 asked one question: does Sushi get a constant function, a compile-time loop, or
neither. This document answers it, and it rules on a second question that the research found
under it: what happens when a constant computes a value that its type cannot hold.

This document is normative for three things:

1. The rule an integer overflow follows in an expression that the compiler reads.
2. The syntax and the semantics of a repeated element in an array literal.
3. The condition that must be true before a constant function goes in.

Read `docs/language-reference.md` for the constant rules that hold today.

## 1. What the compiler does today

`semantics/passes/const_eval.py` is an expression walker. `evaluate` (`const_eval.py:73-104`)
sends work to nine node kinds: an integer, a float, a bool, a string, a binary operator, a
unary operator, an array literal, a name, a cast and an index. Every other node gets CE0108.

The evaluator has no environment. `_evaluate_name` (`const_eval.py:219-242`) reads a global
constant and nothing else. There is no statement, and there is no control flow.

The evaluator is a helper and not a pass (`semantics/semantic_analyzer.py:112-113`). Four
places call it:

| Caller | Purpose | Reporter |
|---|---|---|
| `passes/types/constants.py:27` | validate a `const` declaration | the real one |
| `backend/codegen_llvm.py:957` | make the LLVM initializer | silent |
| `passes/types/expressions.py:367` | read a shift count for CE2512 | silent |
| `ast_builder/builder.py:35` | read a fixed array size | silent |

The fourth caller runs while the compiler builds the AST, which is before any pass. This
matters to every later decision in this document.

**The back end is not the blocker.** `_materialize_constant` (`codegen_llvm.py:967-986`)
already builds an `ir.ArrayType` initializer of any length, and
`_register_global_constant` (`codegen_llvm.py:921-931`) puts it in `.rodata` with internal
linkage. A table of 256 or 32768 entries needs no new back-end work. Only the front end has
no way to write one.

**One compile-time loop exists, and it is not usable here.** `unroll_expands`
(`generics/monomorphize/unroll.py:42`) unrolls an `expand` statement over a variadic pack.
`_unroll_expand` (`unroll.py:63-101`) makes one deep copy of the body for each pack element
and renames the loop variable. It runs only inside `monomorphize_function`, it needs a pack
parameter, and it gives the body no index. Nothing anywhere puts a **value** into a body: the
type substitutor moves types only.

## 2. Ruling 1: an overflow is a diagnostic, not a wrap

**Implemented.** CE2077 is registered, the evaluator computes at the width, and the
typecheck pass asks the same question of a fold in a body. The measured cost of the
breaking change: no test in the suite needed a change.

### The behaviour this replaces

The evaluator holds a Python integer of unlimited size. `_eval_arithmetic`
(`const_eval.py:308-315`) marks the exact result with the type of the left operand. Nothing
compares that result against the type.

So this constant holds 300:

<!-- docs-sweep: skip (records today's behaviour, which this ruling changes) -->
```sushi
const u8 A = 200 + 100
```

The program still prints 44. llvmlite writes the text `i8 300`, and the LLVM IR parser
truncates it to `i8 44`. A body prints 44 as well, because `_fold_arithmetic_constants`
(`backend/expressions/operators.py:166-193`) masks the result and restores the sign at the
width of the type.

The printed value is correct, and this is why every test passes today. Truncation gives the
same answer for `+`, `-`, `*`, `<<` and `~`. It gives a different answer for `/`, `%`, `>>`,
a comparison, a widening cast, an array index and an array size. Each of these reads the
held value, so each of them can disagree with a body:

<!-- docs-sweep: skip (records today's behaviour, which this ruling changes) -->
```sushi
const u8  A = 200 + 100
const u32 W = A as u32          # the evaluator gives 300, a body gives 44
const bool B = A > 255          # the evaluator gives true, a body gives false
const u8  H = (200 + 100) / 2   # the evaluator gives 150, a body gives 22
```

### What other languages do

| Language | a `u8` constant of `200 + 100` | Model |
|---|---|---|
| C | 44, and no diagnostic. `-Wconversion` warns | promote to `int`, compute, then convert once |
| C++ | 44 for an unsigned type. An error for a signed overflow | signed overflow is undefined, so it is not constant |
| Java | an error: a lossy conversion | a narrowing conversion of a constant is legal only if the value fits |
| Go | an error: constant 300 overflows uint8 | exact arithmetic, then a check that the type can hold the value |
| Rust | an error: the operation would overflow | compute at the declared type. An overflow stops the compilation |
| Swift | an error: the operation results in an overflow | the same as Rust. `&+` wraps because the writer asks for it |

Only C truncates in silence, and only because it computes in `int` and converts once at the
store. Every language after C reports the program. No language wraps each operation and stays
quiet.

Sushi already holds this rule for a literal. `const u8 X = 300` is CE2073 today. The
expression `200 + 100` walks past the same rule only because nothing checks a computed value.

### The rule

**Sushi follows Rust. The compiler computes at the declared width, and it reports an
operation whose result leaves the type.**

The operators split in two groups. Get this split right, because it is the part that is easy
to reverse.

**An overflow-checked operator** reports a result that the declared type cannot hold:

| Operator | Note |
|---|---|
| `+` `-` `*` | the common case |
| `/` `%` | one case only: the smallest signed value with `-1` |
| unary `-` | one case only: the smallest signed value |

LLVM calls the `/` and `%` case undefined for both `sdiv` and `srem`, because the hardware
instruction traps. A compile-time report is therefore the only correct answer for it.

**A width-defined operator** computes at the width and never reports:

| Operator | Note |
|---|---|
| `~` `&` `\|` `^` | the result always fits the width |
| `<<` | the bits that leave the width are lost. `200 << 1` on a `u8` is 144 |
| `>>` | arithmetic on a signed type, logical on an unsigned type |

The shift **count** keeps the rule it has. A count the compiler can read must be 0 to
width-1, which is CE2512. A computed count past the width stays defined and unchecked. This
is Go's rule, and this document does not change it.

### Where the rule applies

The rule applies to an expression whose value the compiler reads. That is a constant, and a
fold of literals in a body. Both must give the same answer, because a reader expects one
meaning for one expression.

Run time does not change. Two locals still wrap, as they do in Rust with the overflow checks
off:

<!-- docs-sweep: skip (fragment, and the first line is what this ruling rejects) -->
```sushi
let u8 a = 200 + 100      # the compiler reports this
let u8 s = x + y          # this wraps at run time, with no check
```

### What this costs

- **The evaluator computes at the width.** `~0` on a `u32` becomes 4294967295 and no longer
  Python `-1`. The printed answer does not move, so
  `tests/types/unary_literal_context/test_run_const_not_of_a_literal.sushi` and
  `tests/constants/test_constants_bitwise.sushi:17` keep the output they expect. The held
  value stops being a lie.
- **The check belongs to the typecheck pass.** The back end does not report a language error,
  so `_fold_arithmetic_constants` is the wrong place. The typecheck pass already calls the
  evaluator (`passes/types/constants.py:27`), so this is one more call and not a new
  mechanism. A body is the second caller: `reject_overflowing_operation`
  (`passes/types/expressions.py`) reads every `+ - * / %` node and every unary minus with a
  silent reporter, and raises only an overflow recorded AT that node. That one rule keeps
  the count right -- the innermost operation of `(200 + 100) / 2` reports, the division
  around it does not, and a use of a constant that overflows adds nothing to the report at
  its declaration.
- **A new code.** Use CE2077. It is free, and `internals/errors/types.py:236` reserves
  CE2070 to CE2079 for radix and literal range errors, beside CE2070 and CE2073. The code
  says that an operation gives a value the type cannot hold, and it names the operator, the
  value and the type. `tests/unit/test_error_registry.py` gates the registration, so the
  implementing branch registers the code in `types.py` and nowhere else.
- **The change is breaking.** `let u8 x = 200 + 100` stops compiling. The measured cost
  against the full suite of 1782 tests: nothing needed a change. Two of them read a value
  the rule corrects and both keep their expected output.
- **Issue #447 gets smaller.** Under this rule a constant always holds a value that its type
  can hold. So the formatter that #447 needs has nothing left to reconcile, and the
  "wrap first" step that #447 describes disappears.

## 3. Ruling 2: an array literal takes a repeated element

**Implemented.** The grammar takes one new level, the AST carries the run rather than
expanding it, and one seam (`semantics/array_runs.py`) reads every count. Two questions
this section did not answer came up while it went in, and section 3.1 rules on them.

### The syntax

A repeated element is `value; count`. It stands anywhere an element stands, and it mixes with
plain elements in one literal:

<!-- docs-sweep: skip (proposed syntax, Ruling 2) -->
```sushi
const i32[288] ZFIXED_LIT = [8; 144, 9; 112, 7; 24, 8; 8]
const i32[30]  ZFIXED_DST = [5; 30]
const i32[19]  ZCLEN_ZERO = [0; 19]
let   i32[]    head = from([-1; 32768])
```

The grammar takes one new level. `sushi_lang/grammar.lark:209-210` becomes an element rule
with an optional count:

```
array_elements: array_element ("," array_element)*
array_element: expr [";" expr]
```

`;` appears nowhere in `grammar.lark` today, so the terminal is free.

### The rules

- The **count** is an integer that the compiler reads: a literal in any base, the name of an
  integer constant, or an expression of them. This is the reader a fixed array size already
  uses.
- The count must be **1 or more**. A count of zero spells nothing, and no case needs one.
- A repeated element is legal in **every array literal**: a `const` initializer, a fixed
  local, and the literal inside `from(...)`.
- The **expanded count** must match the declared size. A mismatch stays CE2011.
- The value is evaluated **once**, and the compiler makes N copies of the result.

### What the back end must do

| Target | Emission |
|---|---|
| a `const` | expand into the existing `_materialize_constant` initializer, in `.rodata` |
| a fixed local | a fill loop or a memset. Never N stores |
| `from([v; n])` | reserve the capacity once, then a fill loop or a memset. Never N pushes |

The array seams are in `backend/types/arrays/`. A long run must never become a long line of
stores, because the IR size and the compile time both grow with N.

A note on the stack: a fixed local of 32768 `i32` values is 128 KiB. The encoder case
therefore wants `from([-1; 32768])`, which puts the table on the heap.

### 3.1 Two rules the first draft did not state

**A repeated element must not own heap memory (CE2018).** ~~`[s; 3]` for a `string` asks the
compiler to put one owned value in three slots. That needs a deep copy per slot, and
`.clone()` is the only deep copy in Sushi -- the compiler inserts none.~~

**Superseded by #478, Ruling 7.** The premise expired when #479 gave `.fill()` a per-slot
`copy_out` through the sanctioned deep-clone seam: the language then answered one question
two ways, because `a.fill(towel)` was legal beside `from([towel; 2])`, which was not. A
repeated value is now a BORROW, and every slot takes its own copy. The rule anticipated its
own end -- "a rule that starts narrow relaxes later without breaking a program that compiles
today" -- and that is what happened.

**CE2011 lists the runs.** A run is written by length, so a literal that is one element short
gives the compiler no way to know WHICH run is short -- either of them could be. The
alternative spelling, Ada's `first .. last => value`, does not solve this either: it catches a
gap or an overlap, because each run states its absolute bounds, but a writer who shortens one
run and lengthens its neighbour leaves it silent too.

So the compiler prints what it does know. Every run, with the absolute span it fills, as a
note on its own source location:

```
error CE2011: array literal has 287 elements but declared type expects 288
note: run 1 fills 0..143    (144 elements)
note: run 2 fills 144..254  (111 elements)
note: run 3 fills 255..278   (24 elements)
note: run 4 fills 279..286    (8 elements)
```

A reader who knows the RFC 1951 boundary is 256 sees `255` and goes to run 2. This is the
information the index form spells by hand, and the compiler derives it from the counts
instead of asking the writer to repeat it. A literal of plain elements keeps its tier-2
rendering, because a list of 287 one-element runs helps nobody.

**A note on where the count is read.** Unlike a fixed array size, a repeat count is read at
the typecheck pass, not while the AST is built. So it may name a constant of ANOTHER unit --
the limit that Known Limitation 14 records for a size does not apply to a count.

### What this closes

**Adopted.** `compression/zlib` was rewritten onto this ruling, and the measurement below is
what the rewrite acted on.

`compression/zlib` is the only real client of a long table in the repository, and every table
it builds at run time is a run of one value:

| Site | Today | How often |
|---|---|---|
| `zlib.sushi:164-179` `zfixed_lit` | 288 entries by `push`, in four runs | each fixed block |
| `zlib.sushi:183-189` `zfixed_dist` | 30 entries of `5` by `push` | each fixed block |
| `zlib.sushi:253-263` `zinflate_clen` | 19 zeros by `push` | each dynamic block |
| `zlib.sushi:94-99` `zhuff_build` | `count[16]` and `offs[16]` zeroed by `push` | each Huffman code |
| `zlib.sushi:482`, fill at `:487-497` `zdeflate_fixed` | 32768 entries of `-1` by `push`, 128 KiB | each `deflate` call |

`zinflate_fixed` (`zlib.sushi:245-249`) calls the first two, and the block loop
(`zlib.sushi:623-635`) reaches it once for every fixed block in the stream. So a stream of
*k* fixed blocks pays about 700 bounds-checked appends *k* times, for two tables that the
format fixes and never changes.

The five tables that zlib does spell out (`zlib.sushi:23-45`) are 19 to 30 entries each. They
are the largest constant arrays in the repository. RFC 1951 specifies them, so they are
written values and not computed ones. This ruling does not change them.

### What this does not close

- A CRC-32 table. Each entry needs eight steps over an accumulator.
- A 256-entry character-class table for a lexer.
- The decode tables that a fast inflate uses. These are indexed by a code, and a code is
  computed, so they need Ruling 3.

These two are the evidence that Ruling 3 waits for.

**One item left this list during the rewrite.** The ENCODER's two lookups -- a length to its
length code, a distance to its distance code -- read as computed tables, and they are not:
each is a step function whose value is constant over a run, so a repeated element writes it
directly. `zlen_index` walked 29 base entries backwards for every match it emitted and now
reads one slot of a 256-entry table written in 29 runs. `zdist_index` does the same through
the range split zlib's own encoder uses, since one direct table would need 32768 slots.

The lesson generalizes, and it is worth stating before Ruling 3 opens: **a table is a run
table more often than it looks.** Ask whether the value is constant over intervals of the
index before concluding that it needs a loop to build.

## 4. Ruling 3: a constant function waits

Sushi does not get a constant function or a compile-time loop yet. The reason is not that the
feature is wrong. The reason is that the repository has no case for it: the tables it needs
are runs, and Ruling 2 writes those. A CRC-32 table does not exist in the repository, and
`zlib.sushi:12` records that gzip is out of scope, so there is nothing to make one for.

**The condition that opens it again: the first real need for a table that is not a run.** Two
candidates are visible now:

- A 256-entry character-class table for the self-hosted lexer, which `ROADMAP.md:109` names
  as the next phase. Sushi gives user code no character classification at all today.
- A CRC-32 table, if gzip goes in.

When one of these arrives, the cost is already known. Record it here so the decision is cheap:

- **Nothing puts a value into a body.** `unroll_expands` is a statement rewrite, it needs a
  variadic pack, and it gives no index. A constant function needs an environment, and that is
  new machinery.
- **Every function wraps its return in `Result`.** A constant function cannot, because a
  compile-time value carries no run-time error. It needs the rule an extension already
  follows: a bare `return`, and no `??` in the body. CE2091 and CE0131 are the codes that
  hold that rule for an extension.
- **A constant cannot be a struct or an enum.** So a constant function returns a number, a
  bool, a string, or a fixed array of those. `ConstantValue` (`const_eval.py:20-24`) holds
  exactly those shapes.
- **The pass order fights it.** The evaluator runs from the typecheck pass and from the back
  end, and the typecheck pass runs per unit and late. A constant function body must be
  typechecked before it runs, so it needs a whole-program pass ahead of every caller of the
  evaluator.
- **A constant function can never size an array.** `ast_builder/builder.py:35` reads a fixed
  array size while the AST is built, before any pass. This is the same limit that keeps a
  size from naming a constant of another unit.
- **It needs a budget and a cache.** The evaluator runs once per use and again in the back
  end, so a table would be computed several times. Recursion needs a limit. The precedents
  are `MONOMORPHIZE_MAX_DEPTH = 128` with CE0122
  (`generics/monomorphize/__init__.py:99`) and `MAX_EXPANSION_ROUNDS = 8`
  (`generics/instantiate/__init__.py:131`).
- **An interpreter is a second implementation of the language.** Every difference between it
  and the back end is a bug. #441 and #451 each fixed one of that kind: floor division
  against truncating division, and a string constant matched by its shape.

The shape a constant function would take, for the record:

<!-- docs-sweep: skip (proposed syntax, not implemented) -->
```sushi
const fn crc32_table() u32[256]:
    let u32[256] t = [0; 256]
    foreach(i in 0..256):
        let u32 c = i as u32
        foreach(k in 0..8):
            if ((c & 1) == 1):
                c := (c >> 1) ^ 0xEDB88320
            else:
                c := c >> 1
        t[i] := c
    return t

const u32[256] CRC32 = crc32_table()
```

Two things in that body are worth notice.

It uses an `if` and not the usual mask trick. The C form of this loop writes
`c = (c >> 1) ^ (0xEDB88320 & -(c & 1))`, and it depends on a subtraction that wraps to
all ones. **Ruling 1 reports that subtraction**, because `0 - 1` on a `u32` leaves the type.
So a constant function needs a statement-level branch to write a CRC table, and this is one
reason why a comprehension cannot replace one.

It also needs `[0; 256]` from Ruling 2 before it can fill anything. Ruling 2 is therefore not
wasted work if Ruling 3 opens later.

## 5. Alternatives, and why they lose

**An array comprehension**, such as `[for i in 0..N: expr]`. It needs an environment, so it
is not cheap. It also cannot make a decision, because Sushi has no conditional expression: an
`if` and a `match` are both statements. And it cannot keep an accumulator, so it cannot fold.
Those two gaps cost it both remaining cases: a character-class table needs a branch, and a
CRC table needs a fold of eight steps. A comprehension would also add a second compile-time
model beside a later constant function, and the two would have to agree.

**A `comptime` block.** It costs the same interpreter as a constant function and gives a
worse surface. A block has no name, no parameters and no return type, so nothing can reuse
it.

**Generation at build time.** The stdlib already has this escape. The Python generators in
`sushi_lang/sushi_stdlib/src/` emit a global directly with `ir.ArrayType` and
`ir.GlobalVariable`, and `src/string_helpers.py:19-26` is the pattern. A CRC-32 table for the
stdlib needs no language change at all. User code is different: it would need a build-script
story, and that belongs to Nori and not to the language.

**Construction at run time is not a workaround.** `grammar.lark:4` lists every top-level
form, and none of them declares a variable. Sushi has no module-level state, so a table built
at run time cannot be kept. This is why `zfixed_lit` runs again for every block, and why
`ZHuff` (`zlib.sushi:70`) is threaded through call after call as a `peek` parameter
(`zlib.sushi:215-216`). Any answer that says "build it at run time" also asks for a global,
and that is a larger language change than the one this document rules on.

## 6. Order

1. Ruling 1, the overflow rule. It is a fix, it stands alone, and it makes #447 smaller.
2. Ruling 2, the repeated element. It closes every table in the repository.
3. Ruling 3 stays closed until the condition in section 4 is true.

## History

- #441 asked which constants cannot be computed. #451 answered it: two divergences fixed,
  four gaps closed, and one language feature split out.
- #446 carries the language feature. This document rules on it.
- #447 carries interpolation in a constant. Ruling 1 removes the blocker that #447 states, and the feature landed on it: the evaluator renders a hole exactly as the run-time formatter does.
