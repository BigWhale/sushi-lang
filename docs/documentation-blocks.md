# Documentation Blocks

[← Back to Documentation](index.md)

A documentation block is part of the declaration, not a comment near it. The grammar sees
the block, the compiler puts it in the AST, and the compiler checks what the block says
against the declaration beside it. A block in an illegal position is an error, and not
text that disappears without a signal.

```sushi
##:
Jumps through hyperspace.

- Parameter distance: How far to jump, in parsecs.
- Returns: The time the jump took, in seconds.
- Errors: When the drive is cold, this returns `JumpError.NotReady`.
:##
fn hyperspace_jump(i32 distance) i32 | JumpError:
    return Result.Ok(distance * 2)
```

## The delimiters

A block opens with `##:` and closes with `:##`.

- The closer is **line-initial**, or the block is a **one-liner**. A `:##` closes a
  block of more than one line only when nothing but whitespace comes before it on its
  line. `##: A short block :##` stays legal, because the closer sits on the opening line.
- Blocks **do not nest**. A block ends at the first closer that qualifies.
- An **unmatched `##:` is an error**, and so is a **`:##` with no opener**.

The two delimiters are different from each other on purpose. A symmetric delimiter — one
sigil that both opens and closes, the way Python's `"""` works — cannot tell an unclosed
block from a stray terminator. Sushi can, and it reports the two mistakes with different
codes.

The line-initial rule is what makes the report possible. Without it, an opener with no
closer runs on to the next `:##` anywhere in the file, and swallows every declaration
between the two. That is the failure this feature exists to remove.

## The three positions

One construct serves three positions. There is no second sigil.

| Position | Documents |
|---|---|
| Immediately above a declaration | that declaration |
| First item in a body | the function that encloses the body |
| First item in a file, attached to nothing | the unit |

```sushi
##:
Unit docs. This block is the first item in the file, and nothing follows it on the
next line.
:##

##: The answer to life, the universe and everything. :##
const i32 ANSWER = 42

fn main() i32:
    ##:
    A block that is first in a body documents the function around it.
    :##
    println("{ANSWER}")
    return Result.Ok(0)
```

A block inside a body must be the **first item** in that body. A block between two
statements is an error (`CE7005`), not a warning: inside a body there is no declaration
it could have meant, so there is nothing to guess at.

A declaration can carry a block above it or a block in its body, but not both
(`CE7006`).

### Where a block may stand

Each of these positions parses:

```sushi
##: A point in two dimensions. :##
struct Point:
    ##: The horizontal offset. :##
    i32 x
    ##: The vertical offset. :##
    i32 y

##: What the drive is doing. :##
enum Drive:
    ##: The coil is cold. :##
    Cold
    ##: The coil is warm. :##
    Ready

##: Anything that can name itself. :##
perk Named:
    ##: The name of this thing. :##
    fn name() string

##: A point names itself. :##
extend Point with Named:
    ##: A point is always called the same thing. :##
    fn name() string:
        return "point"

##: Foreign declarations borrowed from libc. :##
unsafe external "C" as libc because "read the length of a C string":
    ##: The length in bytes, without the terminator. :##
    fn strlen(string s) i64 = "strlen"

fn main() i32:
    let Point p = Point(1, 2)
    println(p.name())
    return Result.Ok(0)
```

## Attachment

**A block attaches to the declaration on the next line.** A blank line breaks the
attachment. This is Go's rule.

An ordinary `#` comment between the block and the declaration breaks the attachment as
well. The compiler collapses a run of comment lines into the newline that carries them,
so a comment line and a blank line look the same to it. The block then warns that it
documents nothing (`CW7001`). To keep the attachment, move the comment.

```sushi
##: This block documents the constant below it. :##
const i32 ANSWER = 42

##: This block documents nothing, and warns. :##

fn main() i32:
    println("{ANSWER}")
    return Result.Ok(0)
```

A block that attaches to nothing, and is not the first item in its file, warns. A block
that IS the first item in its file documents the unit, and never warns.

## Interior whitespace

The text is **dedented, not reflowed**. The compiler removes the longest indent that
every non-blank line shares. Everything after that stays exactly as written, so a fenced
example keeps its own indent.

The first line is what follows `##:` on the opening line. It carries no indent of its
own, so it does not take part in the measurement.

```sushi
fn main() i32:
    ##:
    This block is written inside a body, and it renders flush.

        This line stays indented, because its indent is more than the common one.
    :##
    return Result.Ok(0)
```

The indent of the **opening line** is not free. A block is one token, so the compiler
never looks inside it, but the `##:` itself lines up like any statement. A block that
does not line up with the code around it is an indent error (`CE6004`).

## Tags

A tag is a Markdown list item. The phase that renders Markdown therefore needs no second
parser, and a block that nobody renders still reads as a sensible list.

| Tag | Meaning |
|---|---|
| `- Parameter <name>:` | one declared parameter, named |
| `- Returns:` | the success value |
| `- Errors:` | when and why the error arm is taken |
| `- Example:` | introduces a fenced code block |

Everything that is not a recognised tag is prose. The first paragraph is the **summary**,
and is what a one-line listing shows.

At most one `- Returns:` and at most one `- Errors:`. Many `- Parameter` tags are legal,
one for each parameter.

### Parameter, not Argument

The tag names the thing the function **declares**, so it is a parameter. An argument is
what a caller passes. The language uses this vocabulary everywhere else — the `Param`
class, `semantics/param_modes.py`, and `CE2427` — and a doc tag is not the place to say
something different.

### Returns describes T

`- Returns:` describes **T**, and not the `Result@(T, E)` that wraps it. The wrapper is
implicit in every signature in the language, and to restate it on every function would be
noise.

A function that returns `~` needs no `- Returns:` at all.

### A typo is not prose

A list item shaped `- <Word>[ <name>]:` is a tag candidate. A candidate whose word is a
keyword is a tag. A candidate whose word is within two edits of a keyword is a typo
(`CE7004`), and the compiler names the tag it thinks you meant. Everything else is prose.

```
- Parameter a: ...     tag
- Returns: ...         tag
- Paramter a: ...      CE7004, help: did you mean `- Parameter:`
- Retruns: ...         CE7004, help: did you mean `- Returns:`
- Note: ...            prose -- four edits from every keyword
- Deprecated: ...      prose -- reserved for a later phase
- Traps: ...           prose -- reserved for a later phase
- see docs/ffi.md      prose -- no `Word:` shape
```

Two edits is the boundary because that is what a mistyped keyword looks like: a
transposition, plus a dropped letter. A misspelled tag is silently invisible in every
documentation system that reads it as text, which is why it earns a code of its own.

## Diagnostics

### CE6011 — a block is opened and never closed

The caret goes on the opening `##:`, because that is where you can fix it.

```
##: docs for x
const i32 x = 1
```

### CE6012 — a `:##` with no opener

```
:##
```

### CE6013 — a block is opened inside a block

The outer block swallowed everything between the two openers. The caret goes on the inner
opener, and a note points at the outer one.

```
##:
The outer block.

##: The inner opener, which cannot be a block of its own.
:##
```

### CE7001 — a `- Parameter` tag names no parameter

```
##:
Adds two numbers.

- Parameter q: There is no parameter called q.
:##
fn add(i32 a, i32 b) i32:
    return Result.Ok(a + b)
```

### CE7002 — one parameter is documented twice

A `- Parameter` tag is keyed by the name it carries. Two tags for one name is almost
always a tag that was copied and not renamed.

```
- Parameter a: The first addend.
- Parameter a: The second addend, with the name never renamed.
```

### CE7003 — a second `- Returns:` or `- Errors:`

These two tags are singletons: a declaration has one success value and one error arm.

```
- Returns: The sum.
- Returns: The sum, said twice.
```

### CE7004 — an unrecognised tag keyword

```
- Retruns: The sum.
```

### CE7005 — a block in a body is not the first item

```
fn probe() i32:
    let i32 n = 7
    ##: This block is not the first item in the body. :##
    return Result.Ok(n)
```

### CE7006 — a declaration is documented twice

```
##: Documents the function from above. :##
fn probe() i32:
    ##: And documents the same function from inside its body. :##
    return Result.Ok(7)
```

### CW7001 — a block documents nothing

The block attaches to no declaration, and it is not the first item in its file. A blank
line or a `#` comment between the block and the declaration is the usual cause.

## What is not built yet

The compiler reads doc blocks and checks them. Nothing consumes the text yet. These parts
come later, and `docs/design/documentation.md` is the plan for each of them:

- A `.slib` records the doc text for each symbol, and `--lib-info` prints it.
- A `- Example:` block compiles and runs in the toolchain.
- `--warn-missing-docs` reports a public symbol with no block, an undocumented parameter,
  and a missing `- Returns:` or `- Errors:`.
- Markdown rendering.

## See also

- [Language Reference](language-reference.md#documentation-blocks) — the grammar
- [Language Guide](language-guide.md#documenting-code) — a shorter tour
- [Compiler Reference](compiler-reference.md) — the CLI and its flags
