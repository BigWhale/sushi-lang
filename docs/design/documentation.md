# Design: Documentation blocks

**Status: none of this is built.** The document specifies a feature that does not exist
yet. Each phase below moves one part from DESIGN to BUILT; this banner records where the
line is.

| Phase | Content | State |
|---|---|---|
| 1 | This document | BUILT |
| 2 | Grammar, AST, attachment rules, the `docs` pass, CE6011/CE6012 and CE70xx | DESIGN |
| 3 | `.slib` manifest carriage; `slib-info` prints a plain dump | DESIGN |
| 4 | `- Example:` blocks compile and run in the toolchain | DESIGN |
| 5 | `--warn-missing-docs` completeness lints | DESIGN |
| 6 | Markdown rendering, and a Markdown checker written in Sushi | DESIGN |

Written for a compiler contributor. The user-facing guide comes with phase 6.

---

## 1. Purpose and mental model

**A doc block is part of the declaration, not a comment near it.**

That sentence is the whole design. Everything below follows from it.

Python takes the opposite position. A docstring is a runtime string object, the language
knows nothing about it, and tools recover the structure by reading source again. The cost
is visible: Python cannot document a constant at all, because a docstring is the first
statement of a body and an assignment has no body. PEP 224 proposed attribute docstrings
and was rejected. Sphinx works around it by reading a `#:` comment, which is a tool
convention with no language behind it.

Sushi takes the other three positions, the ones Rust, Nim and Swift take:

1. **The grammar sees the block.** It is a token, it lands in the AST, and a block in an
   illegal position is a syntax error rather than text that silently disappears.
2. **The artifact carries the docs.** A `.slib` records doc text per symbol, so
   `--lib-info` answers what a library contains without a source tree.
3. **The toolchain runs the examples.** A code block inside a doc block compiles, the way
   a Rust doctest or a Go `Example` function does.

### What grammar-awareness buys

A tool that scrapes source can only reproduce what the author wrote. A compiler that owns
the doc block can check it against the declaration standing next to it:

- A `- Parameter q:` that names no parameter of this function is wrong, and the compiler
  knows it is wrong.
- A `- Errors:` can be read against the declared `| E` arm. Rust and Go have no equivalent
  check, because neither has a declared error type.
- `slib-info` can print the parameter **mode** — `nom`, `peek`, `poke` — beside each
  documented parameter, with nothing supplied by the author.

None of these are available to pydoc, and none are the point of the feature on their own.
They are the reason the block belongs in the grammar rather than beside it.

---

## 2. Syntax

A doc block opens with `##:` and closes with `:##`.

```sushi
##:
A unit-level doc block. Not mandatory, but the compiler encourages one.
:##

##: A simple const :##
const i32 foo = 42

##:
Jumps through hyperspace.

- Parameter a: The incoming argument.
- Parameter b: The second one. No type is given here; it comes from the declaration.
- Returns: The jump distance in parsecs.
- Errors: When the drive is cold, this returns `JumpError.NotReady`.
:##
fn hyperspace_jump(i32 a, u8 b) i32 | JumpError:
    return Result.Ok(0)

fn make_tea(u8 strength) ~:
    ##:
    A block inside a body documents the function that encloses it.
    :##
    return Result.Ok(~)
```

The block holds prose. Whitespace inside it never reaches the parser, so the text may be
laid out however reads best.

### Delimiter rules

- **No nesting.** A block ends at the first `:##`. An inner `##:` is ordinary text.
- **An unmatched `##:` is an error.** Silently treating it as a comment would let a whole
  documented API vanish from a build with no signal.
- **A `:##` with no opener is an error**, for the same reason read backwards.

The two are separate errors rather than one, because the asymmetric delimiters let the
compiler say which mistake was made. A symmetric delimiter — `###` opening and closing,
the way Python's `"""` works — cannot tell an unclosed block from a stray terminator.

### Positions

One construct serves three positions. The delimiters make the position unambiguous, so no
second sigil is needed. This is what Rust needed `//!` for, and Nim needs the
first-statement rule for.

| Position | Documents |
|---|---|
| Immediately above a declaration | that declaration |
| First item in a body | the enclosing function |
| First item in a file, attached to nothing | the unit |

A block inside a body must be the **first item** in that body. A block floating between two
statements is an error, not a warning: inside a body there is no declaration it could
plausibly have meant, so there is nothing to guess at.

The grammar stays permissive and the builder rejects the bad position, which is how the
compiler already handles a nested `fn` — the grammar reaches `function_def` from statement
position, and `ast_builder/statements/parser.py` rejects it with CE6101. A parse error
would only be able to say "unexpected token"; a builder check says which rule was broken.

### Attachment

**A block attaches to the declaration on the next line.** A blank line breaks the
attachment. This is Go's rule, and Go has run on it since before Go 1.0.

Blank lines are invisible to the grammar — `_NEWLINE` collapses a run of them into one
token — so attachment is a span comparison in the AST builder, not a grammar rule:
`decl.loc.line == doc.loc.end_line + 1`.

A block that attaches to nothing, and is not the first item in its file, is a **warning**:
it documents nothing, and the author almost certainly meant it to document something.

```sushi
##:
Unit docs. Nothing follows on the next line, and this is the first block in the file.
:##

##: This block documents nothing, and warns. :##

##: This one documents the constant below it. :##
const i32 answer = 42
```

### Interior whitespace

The text is **dedented, not reflowed**. The common leading indent across all non-blank
lines is stripped, so a block written inside a function body renders flush. Everything
after that is preserved exactly.

Interior runs of spaces are left alone. Collapsing them would destroy a fenced code
example, and a Markdown renderer collapses them at display time anyway.

---

## 3. Tag vocabulary

A tag is a Markdown list item. That is deliberate: the phase-6 Markdown work needs no
second parser, and an unrendered block still reads as a sensible list.

| Tag | Phase | Meaning |
|---|---|---|
| `- Parameter <name>:` | 2 | one declared parameter, named |
| `- Returns:` | 2 | the success value |
| `- Errors:` | 2 | when and why the error arm is taken |
| `- Example:` | 4 | introduces a fenced code block |
| `- Deprecated:` | reserved | not parsed yet |
| `- Traps:` | reserved | runtime traps such as RE2020; not parsed yet |

Everything that is not a recognised tag is prose. The first paragraph is the **summary**,
and is what a one-line listing shows.

### Parameter, not Argument

The tag names the thing the function **declares**, so it is a parameter. An argument is
what a caller passes. The language already uses this vocabulary throughout — the
`parameters` grammar rule, the `Param` class, `semantics/param_modes.py`, and CE2427
"the `nom` marker is written at both ends" — and a doc tag should not be the one place
that says something else.

### Returns describes T

`- Returns:` describes **T**, not the `Result@(T, E)` that wraps it. The wrapper is
implicit in every signature in the language, and restating it on every function would be
noise.

A function returning `~` needs no `- Returns:` at all. `slib-info` renders "Returns
nothing." for it, and the phase-5 lint does not ask for one.

---

## 4. Grammar changes

### The terminals

```lark
DOC_BLOCK.10: /##:[\s\S]*?:##/
DOC_OPEN.5:   /##:/
DOC_CLOSE.5:  /:##/
```

`[\s\S]` matches a newline without needing a DOTALL flag, and the lazy quantifier stops at
the first `:##`, which is what "no nesting" means in practice.

The priorities matter. Lark's basic lexer resolves overlapping terminals by priority, so
`DOC_BLOCK` must outrank `COMMENT: /#+[^\n]*/`, which would otherwise claim a single-line
block — both match `##: foo :##` to the same length, and length alone does not separate
them. `DOC_OPEN` and `DOC_CLOSE` sit between the two: they match only
when `DOC_BLOCK` could not, which is exactly the unmatched-delimiter case, and reaching the
parser is how they become a diagnostic.

`COMMENT` itself is unchanged. Priority does the separation.

### The newline terminal must be narrowed

This is the part that is easy to miss. Two terminals eat comments, not one:

```lark
_NEWLINE: /(\r?\n[ \t]*(?:#[^\n]*\r?\n[ \t]*)*)+/
```

A run of full-line comments is absorbed **into the newline token itself**, so a doc block
on its own line would be swallowed before any terminal priority applied. The inner group
needs one lookahead:

```lark
_NEWLINE: /(\r?\n[ \t]*(?:#(?!#:)[^\n]*\r?\n[ \t]*)*)+/
```

`###` banner comments are unaffected: after the first `#`, the next two characters are
`##`, not `#:`, so the group still matches them.

**Measured**: the corpus contains zero occurrences of `:#` in any `.sushi` file, in any
position. No existing source changes meaning.

### The rules

`DOC_BLOCK` is a single token, so it never needs to be an optional prefix on twelve
declaration rules. Seven edits, all the same shape:

```lark
toplevel: use_stmt | const_def | ... | external_block | DOC_BLOCK

block: _NEWLINE _INDENT (_NEWLINE | DOC_BLOCK | statement)+ _DEDENT

struct_def:     ... _INDENT (DOC_BLOCK _NEWLINE | struct_field)+  _DEDENT
enum_def:       ... _INDENT (DOC_BLOCK _NEWLINE | enum_variant)+  _DEDENT
perk_def:       ... _INDENT (DOC_BLOCK _NEWLINE | perk_method)+   _DEDENT
external_block: ... _INDENT (DOC_BLOCK _NEWLINE | extern_decl)+   _DEDENT
extend_suffix:  ... _INDENT (DOC_BLOCK _NEWLINE | function_def)+  _DEDENT
```

`toplevel` and `block` need no trailing `_NEWLINE`, because both already carry `_NEWLINE`
as an alternative in their repetition. The five member blocks do not, so they spell it.

The parser is LALR(1) (`sushi_lang/internals/parser.py:40`). A single-token alternative
introduces no conflict: the parser shifts `DOC_BLOCK` and the following token decides
whether a declaration follows.

### Indentation is a non-question, by construction

`LangIndenter` (`sushi_lang/internals/indenter.py:15`) is a postlexer that reads
`_NEWLINE` tokens and emits `_INDENT` / `_DEDENT`. A doc block is **one token containing
its own newlines**, so the indenter never sees inside it. Indentation within a block is
therefore free — not by a rule anyone has to enforce, but because nothing looks at it.

This is the strongest argument for delimiters over a line sigil. Had the design used `##`
per line, every doc line would have been a token, and comment-only lines — which are
indentation-neutral today, because `handle_NL` measures the indent after the *last*
newline in the token — would have started to count. A doc block that did not line up with
its declaration would have become an indent error, and that is a behaviour change nobody
asked for.

### A doc block must never become a statement

`parse_block` (`sushi_lang/semantics/ast_builder/statements/blocks.py:13`) routes every
child through `parse_stmt`. Its loop gains one branch that peels `DOC_BLOCK` children out
and hands them to the attachment step instead.

This is not a detail. If a doc block reached the statement dispatcher as an AST statement
class, every exhaustive statement dispatcher would need an arm for it or would raise an
ICE — the backend `emit_stmt` match, the `scope`, `borrow` and `typecheck` passes, the
monomorphize transformer, and `semantics/visitors.py`. Peeling it in `parse_block` is the
difference between a contained change and one that touches the whole compiler.

### Keep `loc` on the declaration keyword

The parser runs with `propagate_positions=True`. If a doc block became a child of a
declaration rule, that declaration's `loc` would start at the doc block, and every caret
that points at a declaration would move up several lines.

It does not, because a doc block is a sibling rather than a child, and attachment happens
in the builder. The `DocBlock` carries its own span. Any future change that makes the
block a grammar child of the declaration must anchor `loc` on the declaration keyword
explicitly.

---

## 5. AST representation

Two dataclasses in `sushi_lang/semantics/ast.py`:

```python
@dataclass
class DocTag:
    kind: str                    # "parameter" | "returns" | "errors" | "example"
    name: Optional[str]          # the parameter name, for kind == "parameter"
    text: str
    loc: Optional[Span] = None

@dataclass
class DocBlock:
    summary: str                 # the first paragraph
    text: str                    # the whole block, dedented, tags included
    tags: List[DocTag]
    loc: Optional[Span] = None
```

A `doc: Optional[DocBlock] = None` field goes on `FuncDef`, `ConstDef`, `StructDef`,
`StructField`, `EnumDef`, `EnumVariant`, `PerkDef`, `PerkMethodSignature`, `ExtendDef`,
`ExtendWithDef`, `ExternalBlock` and `ExternalDecl`.

The nearest existing precedent for author prose surviving into the AST is
`ExternalBlock.reason` (`ast.py:205`), the `because "..."` string.

Both classes must appear in `ast.py`'s `__all__`.
`tests/unit/test_ast_all_is_complete.py` is the gate, and it exists because `Spread` once
went missing from it.

### One parser, one attachment function

A new `docs.py` under `sushi_lang/semantics/ast_builder/declarations/` holds both, and is
the only place that understands doc syntax:

- `parse_doc_block(token) -> DocBlock` — strip delimiters, dedent, split summary from
  body, recognise tags.
- `attach_docs(items) -> None` — walk a sequence of sibling nodes, bind each block to the
  node on the next line, and report the unattached ones.

The twelve declaration builders must not each grow doc handling of their own. They call
`attach_docs` once per block they own.

---

## 6. The `docs` semantic pass

A new whole-program pass named `docs`, running **after `collect` and before `externs`**.

The order lives in the `SemanticAnalyzer.check()` docstring, which is the authority;
`docs/internals/semantic-passes.md` describes each pass, and the pass list in `CLAUDE.md`
gains one name.

Two reasons for that position:

- The pass needs the AST and the symbol table, and nothing else. Parameter names, the
  return type, the error arm and the public flag are all on the declaration already. No
  type resolution is required.
- It must run **before `instantiate` and `monomorphize`**. A generic function's doc block
  is written once, and checking it after monomorphization would report the same mistake
  once per instantiation.

### Always on

| Condition | Kind |
|---|---|
| `- Parameter` names a parameter this callable does not declare | error |
| two `- Parameter` tags for the same name | error |
| an unrecognised tag keyword, `- Paramter:` | error |
| a doc block that documents nothing | warning |

The typo case earns a code of its own rather than being treated as prose. A misspelled tag
is silently invisible in every documentation system that treats it as text, and that is
the failure this feature exists to remove.

### Behind `--warn-missing-docs`

Completeness is opt-in, the way `missing_docs` is in Rust. A codebase that has not been
documented yet must not become a wall of warnings on the day the feature lands.

| Condition | Kind |
|---|---|
| a public symbol with no doc block | warning |
| a documented function with an undocumented parameter | warning |
| a non-`~` function with no `- Returns:` | warning |
| a function declaring `\| E` with no `- Errors:` | warning |

The flag is a long `--flag`, matching every other flag in `sushi_lang/compiler/cli.py`.
A `-W` tier system was considered and set aside: it is a whole CLI surface to design, and
it would have to decide which existing `CWxxxx` warnings move behind a tier. That is a
separate piece of work, and this feature does not need it.

---

## 7. Diagnostics

### The two syntax errors

CE6011 and CE6012, in `sushi_lang/internals/errors/syntax.py`, which owns the CE6xxx range.
The highest code there today is CE6010, with CE6101 and CE6102 in the sub-family above.

- **CE6011** — a doc block is opened and never closed. The location is the opening `##:`,
  not the end of the file, because the opener is where the author can fix it.
- **CE6012** — a `:##` with no opening `##:`.

### The doc family

CE70xx, in a **new** `docs.py` module under `sushi_lang/internals/errors/`. A code may only be added
in the file that owns its range, and CE7xxx is entirely unused today.

This needs three supporting changes:

1. A `DOCS = "docs"` member on `Category` in `internals/errors/registry.py`.
2. An import of the new module in `internals/errors/__init__.py`. Registration is an
   import side effect; nothing references the module by name.
3. An amendment to `_category_of_range()` in `tests/unit/test_error_registry.py:94`. Its
   final statement is a catch-all `return {Category.SYNTAX}` for anything at or above 6000,
   so CE70xx would be forced into the wrong category. It becomes a bounded
   `if number < 7000: return {Category.SYNTAX}` followed by `return {Category.DOCS}`.

Warnings go in `warnings.py` regardless of family, as every warning does.

`tests/unit/test_error_registry.py` holds an exact `REGISTRY_SIZE` tripwire whose inline
comment is a running changelog of every code added and removed. It needs a bump and a
written justification for each new code, in the same voice as the entries already there.

---

## 8. `.slib` carriage

**This section is a placeholder.** The container is still moving — `--lib-info` grew the
v4 fields only recently, and the user-facing doc pass in `docs/design/libraries.md` has not
landed. The doc-block feature does not depend on how either settles. What follows specifies
the field, not the container.

### Docs live in the manifest

`docs/design/libraries.md:116` is normative:

> **The rule: everything in the library must be knowable from the manifest alone.**
> `--lib-info` must never parse source to answer what a library contains.

So doc text is a manifest field, in structured form, and is **not** re-derived from the
v4 source section. That holds even though a source library ships its whole source: the
source is the authority, the manifest is the index, and `--lib-info` reads the index.

### The record

Every per-symbol record gains an optional `doc` key:

```
"doc": {
    "summary": "Jumps through hyperspace.",
    "text":    "<the whole block, dedented, tags included>",
    "params":  {"a": "The incoming argument.", "b": "The second one."},
    "returns": "The jump distance in parsecs.",
    "errors":  "When the drive is cold, this returns `JumpError.NotReady`."
}
```

The key is **absent** when a symbol has no doc block. Every existing `.slib` fixture stays
valid, and an undocumented library grows by nothing.

### What changes, and what does not

The six record literals live in `sushi_lang/backend/library_manifest.py`: public functions
at `:182` and each parameter inside that record, constants at `:207`, struct fields at
`:232`, enum variants at `:271`, with the enclosing struct and enum records around them.
The generic path goes through `serialize_generic_*` in
`sushi_lang/semantics/library_templates.py`.

Three things deliberately do **not** move:

- The container `VERSION` stays at 4. The metadata blob is an open msgpack dict with no
  schema enforcement (`backend/library_format.py`), so an added optional key is not a
  format change.
- `sushi_lib_version` stays at `"2.0"`. A reader that does not know the key ignores it.
- `slib.sushi` needs no change. `slib_read_metadata` returns the whole `MsgValue` tree, so
  the self-hosted reader reaches a new key without being taught about it.

### One stale statement to fix elsewhere

`docs/library-format.md` still documents container version 2 in one place and 3 in two
others, with no SOURCE section and no `kind`, `units`, `library_version` or
`requires_compiler` fields. It does not belong to this work; it belongs to the phase-6 doc
pass in `docs/design/libraries.md`.

---

## 9. `slib-info` rendering

Phase 3 is a plain dump. No pagination, no colour, no wrapping. Colour and structure are
phase 6.

Docs are indented two spaces under the signature they belong to, inside the existing
sections:

```
Public Functions (1):
  fn hyperspace_jump(i32 a, u8 b) i32
    Jumps through hyperspace.
    - Parameter a: The incoming argument.
    - Parameter b: The second one.
    - Returns: The jump distance in parsecs.
    - Errors: When the drive is cold, this returns `JumpError.NotReady`.
```

A symbol with no docs renders exactly as it does today, with no blank line and no
placeholder. This matches the existing convention, where an empty section is suppressed
rather than printed empty.

### The parity obligation

`slib-info` has **two** implementations, and they must produce byte-identical success
reports:

- Python, `print_library_info` in `sushi_lang/compiler/cli.py`
- Sushi, `toolchain/src/slib_info.sushi`

`tests/unit/test_slib_info_parity.py:86` locks them with
`assert py_run.stdout.endswith(tool_run.stdout)`, and `toolchain/README.md` states the
contract: error messages may differ between the tool and the fallback, the success report
may not.

So every rendering change here is two implementations plus a rebuild through
`toolchain/build.py`. This is the real cost of the requirement, and it is worth paying —
the parity gate is what keeps the self-hosted tool honest.

---

## 10. Doc tests

Phase 4. A fenced code block under `- Example:` is compiled and run.

### Wrapping

A snippet with no `fn main(` is wrapped, the way rustdoc wraps one. Written by the author
as two lines of intent:

    - Example:
    ~~~sushi
    let i32 d = hyperspace_jump(3, 7)??
    println("{d}")
    ~~~

and compiled as a program, with the import injected, the body indented into
`fn main() i32:`, and `return Result.Ok(0)` appended.

(The fences above are drawn with tildes only so this document can show a fence inside a
fence. A real doc block uses backticks.)

A snippet that declares its own `main` is compiled verbatim. One rule, and it covers the
whole-program case for free.

The reason to wrap is that an example is documentation first. Six lines of ceremony around
two lines of intent teaches the ceremony.

### The runner

There is **no importable compile driver**. `sushi_lang.compiler.cli.main(argv)` prints a
banner, builds its own `Reporter` internally and never returns it, and writes diagnostics
to the console. Every existing harness shells out to `./sushic`, and the doc-test runner
does the same.

`tests/docs_sweep.py` gains a second collector that walks `.sushi` files for doc blocks.
It already carries everything else needed: the outcome vocabulary — pass, expected-error
`CExxxx`, skip — the temp-directory handling, the thread pool, and `NO_COLOR=1` so stderr
matching is robust. It stays a by-hand tool and deliberately not a CI job, which is the
ruling that shaped it.

One thing does not carry over. Its candidate filter requires both `fn main(` and `return`
to be present in a block, to tell a runnable example from a quoted signature. Wrapping
makes that test wrong for doc snippets, so the new collector needs its own rule: a doc
example is runnable unless it is marked otherwise.

---

## 11. Rejected alternatives

**Inside only, the Python model.** Rejected on coverage. Five declaration forms have no
body to sit inside — `const_def`, `struct_field`, `enum_variant`, `perk_method` and
`extern_decl` — and constants are among the first things anyone wants to document. This is
the same wall PEP 224 hit.

**A `##` line sigil, the Rust and Nim model.** Workable, and it was the leading candidate
until the indentation consequence surfaced. Every doc line becomes a token, so doc lines
stop being indentation-neutral and a misaligned block becomes an indent error. It also
needs a second sigil for the enclosing-item position, the way Rust needs `//!`. The
delimited form gives three positions with one construct and no indentation change.

**A `doc:` indented block.** The prettiest option, and the most Sushi-like. Rejected
because the Lark lexer would tokenize the prose: `50%` produces an operator, `don't` opens
an unterminated string, and `List@(T)` parses as generic syntax. Free text inside an
indented block needs a lexer mode, and Lark does not have one. This is why every language
that has this feature uses a comment-flavoured delimiter.

**Symmetric delimiters**, `###` to open and close. Rejected because a symmetric delimiter
cannot distinguish an unclosed block from a stray terminator, and both are required to be
errors.

**`@param` tags, the Javadoc model.** Rejected twice over. `@` is Sushi's generic sigil —
`List@(i32)`, `fn id@(T)`, `@(T: Hashable)` — and putting the language's most distinctive
mark in a second unrelated job costs more than the familiarity is worth. `@param` is also
not valid Markdown structure, so phase 6 would need a second parser.

**Prose only, no tags, the Rust model.** Rust documents parameters with a `# Arguments`
heading and a bullet list, and checks nothing. Rejected because the checks in §6 are the
main thing a compiler-owned doc block can offer, and they need a name to check against.

**Re-deriving docs from the `.slib` source section.** Tempting, since a source library
ships its whole source. Rejected by `docs/design/libraries.md:116`: `--lib-info` must never
parse source, and re-parsing would make the tool depend on a working compiler to answer a
question about a file.

---

## 12. Phases

**Phase 2 — the language.** The three terminals, the `_NEWLINE` narrowing, the seven rule
edits, the `DOC_BLOCK` peel in `parse_block`, `DocBlock` and `DocTag`, the doc parser and
the attachment function, the `docs` pass with its always-on checks, CE6011, CE6012 and the
CE70xx module. At the end of this phase the compiler understands doc blocks and nothing
consumes them.

**Phase 3 — the library.** The `doc` key on the six manifest records and the generic
serializers, and the plain dump in both `slib-info` implementations. At the end of this
phase a documented library tells you what it contains.

**Phase 4 — the examples.** `- Example:` parsing, the wrapping rule, and the second
collector in `docs_sweep.py`.

**Phase 5 — completeness.** `--warn-missing-docs` and its four lints.

**Phase 6 — Markdown.** Rendering, a richer `slib-info`, and the user-facing guide. The
Markdown checker is written in Sushi and lives in `toolchain/`, which makes it the second
inhabitant after `slib-info` and another test of the language against a real problem.

### What each phase must not do

Phase 2 must not add a fallback for an unparseable doc block. A block either parses or is
a diagnostic; a silent recovery path would reintroduce exactly the failure mode this
feature removes.

Phase 3 must not teach `slib-info` to parse source, and must not move the container
version for an added optional key.

Phase 5 must not turn any always-on check into a warning, or any warning into an
always-on error. The split in §6 is the contract: a claim that contradicts the declaration
is wrong today, and an absent claim is a matter of policy.
