# Compile-time evaluation

One question comes first: does Sushi get a constant function, a compile-time loop, or
neither. This document answers it, and it rules on a second question that the research found
under it: what happens when a constant computes a value that its type cannot hold.

This document is normative for three things:

1. The rule an integer overflow follows in an expression that the compiler reads.
2. The syntax and the semantics of a repeated element in an array literal.
3. The condition that must be true before a constant function goes in.

Read `docs/language-reference.md` for the constant rules that hold.

## 1. What the compiler does

`semantics/const_eval.py` is an expression walker. `evaluate` decides every `Expr` kind
through one table, `ConstantEvaluator.HANDLERS`: a literal of each kind, a binary and a
unary operator, an array literal, a name, a cast, an index, an interpolated string, a
struct construction, a member access and a dot call each have a handler, and the eleven
kinds named in `NOT_CONSTANT` -- a method call, an enum constructor, `new()`, `from()`, a
borrow, a `??`, a range, a spread, a lambda, a blank and a tuple -- answer [CE0108](../error-catalog.md#ce0108) through
the one backstop. The table gives each kind the phrase that [CE0108](../error-catalog.md#ce0108) prints, as the source
writes the expression ("a `from(...)` call is not a compile-time constant").
`tests/unit/test_const_eval_dispatch_is_total.py` holds the two sets against the `Expr`
union, so a kind added to the language cannot fall through in silence.

The evaluator has no environment. `_evaluate_name` reads a global constant and nothing
else. There is no statement, and there is no control flow.

The evaluator is a helper and not a pass (the `SemanticAnalyzer.check()` docstring says
so). Three callers reach it:

| Caller | Purpose | Reporter |
|---|---|---|
| the `typecheck` pass, through `constant_evaluator` and `operand_evaluator` (`passes/types/__init__.py`) | validate a `const` declaration (`passes/types/constants.py`); read a shift count for [CE2512](../error-catalog.md#ce2512), a divisor for [CE0112](../error-catalog.md#ce0112) and a repeat count (`passes/types/expressions.py`) | the real one for a declaration, silent for the reads |
| the backend, through `LLVMCodegen.constant_evaluator` (`backend/codegen_llvm.py`) | make the LLVM initializer | silent |
| `ASTBuilder.integer_constant` (`ast_builder/builder.py`) | read a fixed array size | silent |

The `typecheck` pass and the backend share the collect pass's constant table and its fold
memo. The AST builder keeps a table of its own, because it runs while the compiler builds
the AST, which is before any pass. This matters to every later decision in this document.

The divisor read and the shift-count read occur at each level of a nest, and each one
reads the whole right operand. Their evaluators (`operand_evaluator`) share one more
table, `folds` in `passes/types/operator_nest.py`. It holds the value of each `BinaryOp`
and `UnaryOp` that they folded, by the node and by the builtin type that the read asked
for. Thus a level reads the value of its operand from the table, and a nest is folded
one time. The table is not on the AST, and it lives until the outermost expression is
validated. A propagation removes the value of each node under the value that it stamps.
No other evaluator reads the table, because a read from it records no overflow.

**The back end is not the blocker.** `_materialize_constant` (`backend/codegen_llvm.py`)
builds an `ir.ArrayType` initializer of any length, and `_register_global_constant` (same
file) puts it in `.rodata` with internal linkage. A table of 256 or 32768 entries needs no new back-end work. Only the front end has
no way to write one.

**One compile-time loop exists, and it is not usable here.** `unroll_expands`
(`generics/monomorphize/unroll.py`) unrolls an `expand` statement over a variadic pack.
`_unroll_expand` (same file) makes one deep copy of the body for each pack element
and renames the loop variable. It runs only inside `monomorphize_function`, it needs a pack
parameter, and it gives the body no index. Nothing anywhere puts a **value** into a body: the
type substitutor moves types only.

## 2. Ruling 1: an overflow is a diagnostic, not a wrap

**Implemented.** [CE2077](../error-catalog.md#ce2077) is registered, the evaluator computes at the width, and the
typecheck pass asks the same question of a fold in a body.

### The problem the rule solves

An evaluator that holds an exact integer of unlimited size and never compares it against
the type gives a constant a value its type cannot hold:

<!-- docs-sweep: skip (fragment with no main; under Ruling 1 the declaration is CE2077) -->
```sushi
const u8 A = 200 + 100
```

Such an evaluator holds 300 here, and the program prints 44: llvmlite writes the text
`i8 300`, and the LLVM IR parser truncates it to `i8 44`. Truncation gives the same answer
for `+`, `-`, `*`, `<<` and `~`. It gives a different answer for `/`, `%`, `>>`, a
comparison, a widening cast, an array index and an array size. Each of these reads the held
value, so each of them can disagree with a body:

<!-- docs-sweep: skip (fragment with no main; under Ruling 1 the declaration is CE2077) -->
```sushi
const u8  A = 200 + 100
const u32 W = A as u32          # an exact evaluator gives 300, a body gives 44
const bool B = A > 255          # an exact evaluator gives true, a body gives false
const u8  H = (200 + 100) / 2   # an exact evaluator gives 150, a body gives 22
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

Sushi holds this rule for a literal: `const u8 X = 300` is [CE2073](../error-catalog.md#ce2073). Ruling 1 applies the
same rule to a computed value such as `200 + 100`.

### The rule

**The compiler computes at the declared width, and it reports an operation whose result
leaves the type.** Computing wider and truncating at the store -- C's model -- makes the
evaluator disagree with the machine: a `u8` constant of `200 + 100` would hold 300, so a
widening cast reads 300 while the same expression at run time prints 44. One expression
has to have one meaning, and reporting is the only answer that keeps it.

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

The shift **count** has its own rule. A count the compiler can read must be 0 to
width-1, which is [CE2512](../error-catalog.md#ce2512). A computed count past the width is defined and unchecked. This
is Go's rule.

### Where the rule applies

The rule applies to an expression whose value the compiler reads. That is a constant, and a
fold of literals in a body. Both must give the same answer, because a reader expects one
meaning for one expression.

At run time, two locals wrap, because the compiler inserts no check there:

<!-- docs-sweep: skip (fragment, and the first line is what this ruling rejects) -->
```sushi
let u8 a = 200 + 100      # the compiler reports this
let u8 s = x + y          # this wraps at run time, with no check
```

**One compile-time home.** The evaluator is the only place in the compiler that
computes an integer operator. The backend folds nothing: it emits the instruction for two
constants as for two locals, and LLVM is the run-time home by definition. The gate is
`tests/unit/test_integer_operator_semantics_agree.py`: per operator and per width, the
evaluator's value and the value a JIT-compiled copy of the emitted instruction computes
are one bit pattern, and a constant fold in the backend's operator emitter is refused
by its source. The smallest signed value `% -1` is [CE2077](../error-catalog.md#ce2077).

### What this costs

- **The evaluator computes at the width.** `~0` on a `u32` is 4294967295, the value the
  machine holds. The printed answer is the same as at run time, and
  `tests/types/unary_literal_context/test_run_const_not_of_a_literal.sushi` and
  `tests/constants/scalar_folding/test_constants_bitwise.sushi` hold it.
- **The check belongs to the typecheck pass.** The back end does not report a language
  error. The typecheck pass calls the evaluator for a declaration
  (`passes/types/constants.py`). A body is the second caller: `reject_overflowing_nest`
  (`passes/types/expressions.py`) reads a NEST of `+ - * / %` nodes and unary minus nodes
  with a silent reporter. The nest is the top node and each operand below it that has the
  type of its operator (`passes/types/operator_nest.py`), so a nest is folded one time, at
  its top, and the check is linear in the depth of the nest. It raises each overflow
  recorded AT a node of the nest. That one rule keeps the count right -- the innermost
  operation of `(200 + 100) / 2` reports, the division around it does not, and a use of a
  constant that overflows adds nothing to the report at its declaration.
- **The code is [CE2077](../error-catalog.md#ce2077).** It is in the CE2070 to CE2079 range of
  `internals/errors/types.py`, beside [CE2070](../error-catalog.md#ce2070) and [CE2073](../error-catalog.md#ce2073). It says that an operation gives a
  value the type cannot hold, and it names the operator, the value and the type.
- **The rule is strict.** `let u8 x = 200 + 100` does not compile.
- **A constant holds a value that its type can hold.** So the formatter that renders a
  constant interpolation has nothing to reconcile, and it needs no "wrap first" step.

## 3. Ruling 2: an array literal takes a repeated element

**Implemented.** The grammar has one level for it, the AST carries the run rather than
expanding it, and one seam (`semantics/array_runs.py`) reads every count. Section 3.1 rules
on two more questions.

### The syntax

A repeated element is `value; count`. It stands anywhere an element stands, and it mixes with
plain elements in one literal:

<!-- docs-sweep: skip (fragment with no main; a top-level `let` is not a program) -->
```sushi
const i32[288] ZFIXED_LIT = [8; 144, 9; 112, 7; 24, 8; 8]
const i32[30]  ZFIXED_DST = [5; 30]
const i32[19]  ZCLEN_ZERO = [0; 19]
let   i32[]    head = from([-1; 32768])
```

The grammar has one level for it: an element rule with an optional count
(`sushi_lang/grammar.lark`, the `array_element` rule):

```
array_elements: array_element ("," array_element)*
array_element: expr (";" expr)?
```

`;` appears nowhere else in `grammar.lark`.

### The rules

- In a `const` initializer and a fixed local, the **count** is an integer that the compiler
  reads: a literal in any base, the name of an integer constant, or an expression of them.
  An unreadable count there is [CE2017](../error-catalog.md#ce2017).
- In the literal inside `from(...)`, the count is any `i32` expression, because a `T[]`
  carries its length. A run-time count of zero is data, and a negative run-time count
  clamps to zero.
- A readable count must be **1 or more**. A readable count of zero spells nothing, and it is
  [CE2017](../error-catalog.md#ce2017).
- A repeated element is legal in **every array literal**: a `const` initializer, a fixed
  local, and the literal inside `from(...)`.
- The **expanded count** must match the declared size. A mismatch is [CE2011](../error-catalog.md#ce2011).
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

### 3.1 Two more rules

**A repeated value is a borrow.** Every slot takes its own copy through `copy_out`, the
deep-clone seam, so an owning type is legal: `from([towel; 2])` follows the same rule as
`a.fill(towel)`.

**[CE2011](../error-catalog.md#ce2011) lists the runs.** A run is written by length, so a literal that is one element short
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
the same-unit limit on a fixed array size ([CE2099](../error-catalog.md#ce2099)) does not apply to a count.

### What this closes

**Adopted.** `compression/zlib` is the real client of a long table in the repository, and
every table it builds at run time is a run of one value. Each one is a repeated element:

| Site | Literal | How often |
|---|---|---|
| `fixed_lit` | `from([8;144, 9;112, 7;24, 8;8])`, 288 entries in four runs | each fixed block |
| `fixed_dist` | `from([5; 30])` | each fixed block |
| `inflate_clen` | `from([0; 19])` | each dynamic block |
| `huff_build` | `count` and `offs`, each `from([0; 16])` | each Huffman code |
| `deflate_fixed` | `from([-1; 32768])`, 128 KiB | each `deflate` call |

The tables that zlib spells out are 19 to 30 entries each. RFC 1951 specifies them, so they
are written values and not computed ones.

### What this does not close

- A CRC-32 table. Each entry needs eight steps over an accumulator.
- A 256-entry character-class table for a lexer.
- The decode tables that a fast inflate uses. These are indexed by a code, and a code is
  computed, so they need Ruling 3.

These two are the evidence that Ruling 3 waits for.

**The encoder's lookups are not on this list.** The ENCODER's two lookups -- a length to its
length code, a distance to its distance code -- read as computed tables, and they are not:
each is a step function whose value is constant over a run, so a repeated element writes it
directly. `len_index` reads one slot of a 256-entry table written in 29 runs. `dist_index` does the same through
the range split zlib's own encoder uses, because one direct table would need 32768 slots.

The lesson generalizes, and it is worth stating before Ruling 3 opens: **a table is a run
table more often than it looks.** Ask whether the value is constant over intervals of the
index before concluding that it needs a loop to build.

## 4. Ruling 3: a constant function waits

Sushi does not get a constant function or a compile-time loop yet. The reason is not that the
feature is wrong. The reason is that the repository has no case for it: the tables it needs
are runs, and Ruling 2 writes those. A CRC-32 table does not exist in the repository, and
the unit block of `zlib.sushi` records that gzip is out of scope, so there is nothing to make one for.

**The condition that opens it again: the first real need for a table that is not a run.** Two
candidates are visible now:

- A 256-entry character-class table for a self-hosted lexer, which is planned work. Sushi gives user code no character classification at all today.
- A CRC-32 table, if gzip goes in.

When one of these arrives, the cost is already known. Record it here so the decision is cheap:

- **Nothing puts a value into a body.** `unroll_expands` is a statement rewrite, it needs a
  variadic pack, and it gives no index. A constant function needs an environment, and that is
  new machinery.
- **A constant function is bare.** A compile-time value carries no run-time error, so a
  constant function has no `| E` channel. It follows the rule of every bare function: a bare
  `return`, and no `??` in the body. [CE2091](../error-catalog.md#ce2091) and [CE0131](../error-catalog.md#ce0131) are the codes that hold that rule.
- **A constant has a closed set of shapes.** A number, a bool, a string, a fixed array, a
  struct construction and an enum variant, each built from constant parts. So a constant
  function returns one of those. `ScalarConstant` and `AggregateConstant` (`const_eval.py`)
  hold exactly those shapes.
- **The pass order fights it.** The evaluator runs from the typecheck pass and from the back
  end, and the typecheck pass runs per unit and late. A constant function body must be
  typechecked before it runs, so it needs a whole-program pass ahead of every caller of the
  evaluator.
- **A constant function can never size an array.** `ASTBuilder.integer_constant` reads a
  fixed array size while the AST is built, before any pass. This is the same limit that keeps a
  size from naming a constant of another unit.
- **It needs a budget and a cache.** The evaluator runs once per use and again in the back
  end, so a table would be computed several times. Recursion needs a limit. The precedents
  are `MONOMORPHIZE_MAX_DEPTH = 128` with [CE0122](../error-catalog.md#ce0122)
  (`generics/monomorphize/__init__.py`).
- **An interpreter is a second implementation of the language.** Every difference between it
  and the back end is a bug. Two such differences are known cases: floor division against
  truncating division, and a string constant matched by its shape.

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
`ir.GlobalVariable`, and the string-literal helper in `src/string_helpers.py` is the pattern. A CRC-32 table for the
stdlib needs no language change at all. User code is different: it would need a build-script
story, and that belongs to Nori and not to the language.

**Construction at run time is a different tool.** A `var` at the top of a unit is storage
(`docs/design/unit-storage.md`): one per program, initialized before `main`. Its initializer
is a constant expression or an empty container, so a table built at run time is kept in a
`var` that starts empty and is filled on first use (`var Maybe@(T) cache = Maybe.None`).
That keeps a table, but it does not compute one at compile time: the table is not
`.rodata`, and each program pays for the build when it runs. `compression/zlib` does not use
it: `fixed_lit` builds its `ZHuff` for each block, and the value goes from call to call as a
`peek` parameter.
