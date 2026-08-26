# Design: Documentation blocks

**Status: the language understands doc blocks, a library carries them, the toolchain runs
the examples, and the compiler says what a block leaves out.** Each phase below moves one
part from DESIGN to BUILT; this banner records where the line is.
The user-facing reference for what is built is `docs/documentation-blocks.md`.

| Phase | Content | State |
|---|---|---|
| 1 | This document | BUILT |
| 2 | Grammar, AST, attachment rules, the `docs` pass, CE6011/CE6012/CE6013 and CE70xx | BUILT |
| 3 | `.slib` manifest carriage; `slib-info` prints a plain dump | BUILT |
| 4 | `- Example:` blocks compile and run in the toolchain | BUILT |
| 5 | `--warn-missing-docs` completeness lints | BUILT |
| 6 | Markdown rendering, and a Markdown checker written in Sushi | DESIGN |

Written for a compiler contributor. `docs/documentation-blocks.md` is the user-facing
guide, written in phase 2 and extended by every phase since.

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
- A `- Errors:` can be required of a function that declares its own error type. Rust and Go
  have no equivalent check, because neither has a declared error type. Note the limit: every
  function has an error arm — `| E` when written, `StdError` when not — so what §6 checks is
  that the tag is present and not what the prose says.
- `slib-info` could print the parameter **mode** — `nom`, `peek`, `poke` — beside each
  documented parameter, with nothing supplied by the author. The mode is already in the
  manifest and is not printed today; §9 carries that as phase-3 work.

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

- **The closer is line-initial**, or the block is a one-liner. A `:##` closes a multi-line
  block only when nothing but whitespace precedes it on its line. `##: A simple const :##`
  stays legal, because there the closer sits on the opening line.
- **No nesting.** A block ends at the first closer that qualifies.
- **An unmatched `##:` is an error.** Silently treating it as a comment would let a whole
  documented API vanish from a build with no signal.
- **A `:##` with no opener is an error**, for the same reason read backwards.
- **A line-initial `##:` inside a block is an error**, not text.

The first and last rules are load-bearing, because a lazy delimiter match cannot report an
unclosed block on its own. `/##:[\s\S]*?:##/` does not stop at the end of the block the
author meant. It runs to the next `:##` anywhere in the file. Measured:

```
##: docs for x
const i32 x = 1

##: docs for y :##
```

matches as ONE token. `const i32 x = 1` leaves the program with no diagnostic, which is
exactly the failure this feature exists to remove. Without the line-initial rule the match
also ends inside a string literal: `let string s = "a :## b"` terminates it.

The two rules close the gap between them, and which code fires depends on what follows.
An opener with no qualifying closer anywhere after it is **CE6011** — which is what the
example above now reports, because the later block closes on its own opening line and so
cannot close this one. An opener that does reach a line-initial closer further down has
swallowed the blocks in between, and their openers are still sitting in its interior: that is
**CE6013**, the signal GCC's `-Wcomment` gives for a `/*` inside a block comment. Neither
code works alone. CE6011 by itself can only ever reach the last unclosed opener in a file,
which is why the first draft of this section specified a diagnostic that could almost never
fire.

The unmatched opener and the unmatched closer are separate codes rather than one, because the
asymmetric delimiters let the compiler say which mistake was made. A symmetric delimiter —
`###` opening and closing, the way Python's `"""` works — cannot tell an unclosed block from
a stray terminator.

### Positions

One construct serves three positions, and no second sigil is needed. This is what Rust needs
`//!` for, and Nim the first-statement rule. What separates the three here is position and
the attachment rule below, not the delimiters — so the same three positions would be open to
a line sigil too, and §11 does not claim otherwise.

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

An ordinary `#` comment between the block and the declaration breaks the attachment as well,
and that is where the rule departs from Go's. Go keeps a run of comment lines inside the doc
group. Sushi cannot: `_NEWLINE` absorbs comment lines and is a filtered token, so the builder
never sees one. It compares line numbers, and to it a comment line and a blank line are the
same thing. The block then warns that it documents nothing. Teaching the builder to read raw
source lines would fix this and is deliberately not done — the warning is a signal, and the
escape is to move the comment.

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

At most one `- Returns:` and at most one `- Errors:`. A second of either is an error, the
same way a second `- Parameter` for one name is.

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
DOC_BLOCK.10: /##:[^\n]*?:##|##:[\s\S]*?\n[ \t]*:##/
DOC_OPEN.5:   /##:/
DOC_CLOSE.5:  /:##/
```

`DOC_BLOCK` is two alternatives, one-liner first: a same-line closer, then a line-initial
one. `[\s\S]` matches a newline without needing a DOTALL flag. The regex itself is phase-2's
to settle; what §2 fixes is the two rules it has to enforce. A single lazy
`/##:[\s\S]*?:##/` enforces neither, and §2 shows what that costs.

The priorities matter. Lark's basic lexer (`lexer="basic"`,
`sushi_lang/internals/parser.py:44`) resolves overlapping terminals by priority, so
`DOC_BLOCK` must outrank `COMMENT: /#+[^\n]*/`, which would otherwise claim a single-line
block — both match `##: foo :##` to the same length, and length alone does not separate
them. `DOC_OPEN` and `DOC_CLOSE` sit between the two: they match only when `DOC_BLOCK` could
not, which is exactly the unmatched-delimiter case.

A basic lexer also means there is no parser context to appeal to. A `##:` means the same
thing everywhere, decided at lex time. And the highest terminal priority in the grammar today
is 4 (`ELLIPSIS`), so `.10` and `.5` open a band above everything rather than fitting into
one.

`COMMENT` itself is unchanged. Priority does the separation.

### A terminal that no rule names is deleted

`DOC_OPEN` and `DOC_CLOSE` are the whole diagnostic path, and Lark removes a terminal that no
rule references. Measured, with the two of them unreferenced: an unclosed `##:` falls through
to `%ignore COMMENT` and is discarded, and a stray `:##` raises `UnexpectedCharacters` on the
colon — CE6002, pointing at a character, rather than CE6012 pointing at a delimiter. Neither
code can fire at all.

**They are kept by `%ignore`, and a lexer callback raises the diagnostic.** `%ignore` counts
as a reference, so the terminal survives the pruning; the token is then dropped before the
parser, which is what makes the rest of the grammar unaware of it. The diagnostic comes from
`lexer_callbacks`, which fire for an ignored terminal exactly as they do for a kept one.

```lark
// Ignored to keep Lark from deleting them, never to discard one: reaching
// either terminal is a diagnostic, raised from its lexer callback.
%ignore DOC_OPEN
%ignore DOC_CLOSE
```

This is one mechanism for both codes, in every position, and the caret lands on the delimiter
itself — which is what §7 asks for.

Carrying the two as `toplevel` alternatives was tried first and rejected on measurement. It
fails on §2's own runaway example: `##:` shifts as a legal `toplevel`, the following ` docs`
lexes as `NAME`, and the parser dies on a `NAME` token several columns to the right. A
`token.type` match in `lark_to_diagnostic` cannot rescue that, because the failing token is
not `DOC_OPEN`. A bare `##:` and a bare `:##` at top level also parse to a complete tree, and
reach a diagnostic only if the builder rejects them — two more places to be right, for a
mechanism that still cannot reach the case the feature exists for.

`lark_to_diagnostic` (`sushi_lang/internals/parse_errors.py`) therefore needs no per-token
mapping, and `TOKEN_NAMES` is unchanged.

### The third delimiter error comes from the same place

CE6013 — a line-initial `##:` inside a block — is interior to a `DOC_BLOCK` token, so no
terminal can match it and no rule can reach it. It is found by scanning the matched token,
from a third `lexer_callbacks` entry on `DOC_BLOCK` itself.

That keeps all three delimiter diagnostics in one place, at lex time, before the AST builder
has an opinion about anything. `SushiError` already carries `notes`, and `emit_exception`
renders them, so the relational note on the outer opener that §7 requires works from a
callback without any new machinery.

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

A `###` comment is unaffected: after the first `#`, the next two characters are `##`, not
`#:`, so the group still matches it.

**Measured** across every `.sushi` file in the tree: zero occurrences of `:#` in any
position, and zero line-initial `##`. No existing source changes meaning. There are no `###`
banner comments in the corpus either — the only `###` in any `.sushi` file is string-literal
test data in `tests/types/result/propagation/test_propagation_preserves_error_data.sushi` —
so the paragraph above covers a case the tree does not yet contain.

A file count is deliberately not quoted. It is stale the week after it is written, and the
claim that matters is that the count of `:#` is zero, not how many files were read to find
that out. Phase 2 re-runs the measurement rather than trusting this paragraph, and
`tests/unit/test_doc_block_grammar.py` keeps it true afterwards.

### The rules

`DOC_BLOCK` is a single token, so it never needs to be an optional prefix on twelve
declaration rules. Seven edits, near enough the same shape:

```lark
program: (_NEWLINE | DOC_BLOCK | toplevel)+

block: _NEWLINE _INDENT (_NEWLINE | DOC_BLOCK | statement)+ _DEDENT

struct_def:     ... _INDENT (DOC_BLOCK _NEWLINE | struct_field)+  _DEDENT
enum_def:       ... _INDENT (DOC_BLOCK _NEWLINE | enum_variant)+  _DEDENT
perk_def:       ... _INDENT (DOC_BLOCK _NEWLINE | perk_method)+   _DEDENT
external_block: ... _INDENT (DOC_BLOCK _NEWLINE | extern_decl)+   _DEDENT
extend_suffix:  ... _INDENT (DOC_BLOCK _NEWLINE | function_def)+  _DEDENT
```

`program` and `block` need no trailing `_NEWLINE`, because both already carry `_NEWLINE`
as an alternative in their repetition. The five member blocks admit only their own member
rule, so they spell it.

The top-level alternative goes on `program`, not on `toplevel`, so the token arrives as a
direct child of `program`. `builder.build()` skips a non-`Tree` child already, so nothing in
that loop changes. On `toplevel` the token would instead be wrapped in a `toplevel` tree that
holds no declaration, and every `_first_tree` lookup in the loop would fall through it.
`DOC_OPEN` and `DOC_CLOSE` appear in no rule at all — they are held by `%ignore` and reported
from a lexer callback, as above.

`extend_suffix` is the one edit that is not the shape it looks. It is two aliased
alternatives: `extend_with_def` is the indented `function_def+` body the sketch shows, while
`extend_def` ends in `block` and is already covered by the `block` edit. Only the first
alternative changes.

The parser is LALR(1) (`sushi_lang/internals/parser.py:40`). A single-token alternative
introduces no conflict: the parser shifts `DOC_BLOCK` and the following token decides
whether a declaration follows.

### Indentation: the interior is free, the opening column is not

`LangIndenter` (`sushi_lang/internals/indenter.py`) is a postlexer that reads `_NEWLINE`
tokens and emits `_INDENT` / `_DEDENT`. A doc block is **one token containing its own
newlines**, so the indenter never sees inside it. Indentation within a block is therefore
free — not by a rule anyone has to enforce, but because nothing looks at it.

The opening `##:` is a different matter, and an earlier draft of this section had it wrong.
The `_NEWLINE` that precedes a block ends with the block's own leading indent, and
`handle_NL` measures the indent after the *last* newline in the token. The opener's column is
therefore measured like any statement's, and a block that does not line up with the code
around it is a CE6004 indent error rather than a doc diagnostic. Interior lines are free; the
first line is not.

That still favours delimiters over a line sigil, but by less than it first appeared. Under
`##` per line every doc line becomes a token and every doc line has to line up. The delimited
form constrains one line instead of all of them. It is a reduction, not an exemption, and §11
carries the argument that actually decides the question.

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
    kind: str                    # "parameter" | "returns" | "errors" | "example" | "unknown"
    name: Optional[str]          # the parameter name, for kind == "parameter"
    text: str
    loc: Optional[Span] = None
    word: str = ""               # the keyword AS WRITTEN; what CE7004 reports

@dataclass
class DocBlock:
    summary: str                 # the first paragraph
    text: str                    # the whole block, dedented, tags included
    tags: List[DocTag]
    loc: Optional[Span] = None
    orphan_reason: Optional[Literal["detached", "in-body"]] = None
```

Two fields were added while phase 2 was built, and both carry a decision the pass cannot
make for itself.

`DocTag.word` holds the keyword exactly as the author typed it. A near miss reaches the
pass as `kind == "unknown"`, and CE7004 has to name what was written, so `name` cannot
carry it -- on an `unknown` tag `name` is not a parameter name and reusing it would say
something false.

`DocBlock.orphan_reason` says WHY a block reached `orphan_docs`. The two ways of
documenting nothing are separate rules with separate codes -- CW7001 for a block that
attaches to nothing, CE7005 for one that stands in a body it is not the first item of --
and the builder is the only place that still knows which happened. Comparing spans in the
pass to recover it would be the same fact derived twice.

A `doc: Optional[DocBlock] = None` field goes on `FuncDef`, `ConstDef`, `StructDef`,
`StructField`, `EnumDef`, `EnumVariant`, `PerkDef`, `PerkMethodSignature`, `ExtendDef`,
`ExtendWithDef`, `ExternalBlock` and `ExternalDecl`.

Two more classes carry one, and neither is a declaration:

- **`Program.doc`** — §2's third position is a block that documents the unit. There is no
  declaration to hang it on, so it lives on the unit's own node.
- **`Block.doc`** — a body-first block is parsed before its enclosing declaration exists.
  `parse_block` parks it here, and `parse_funcdef` and `parse_extenddef` lift it onto the
  declaration.

**`Program.orphan_docs: List[DocBlock]`** holds every block that attached to nothing. It has
to exist because the builder cannot report: `ASTBuilder` takes no `Reporter`, and a block
that documents nothing is a warning the `docs` pass raises. Dropping such a block in the
builder would make it vanish silently, which is the failure mode of §1 read backwards.

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
- `attach_docs(children, built, ast_builder) -> None` — walk a container's parse-tree
  children beside the nodes just built from them, bind each block to the node that starts on
  the next line, and send the rest to `ast_builder.orphan_docs`.

The twelve declaration builders must not each grow doc handling of their own. They call
`attach_docs` once per block they own.

### Nothing may vanish

A doc block that is written and then silently dropped is the failure this feature exists to
remove, so the builder accounts for every one of them.

`parse_block` parks a body-first block on `Block.doc` and records the pair in
`ast_builder.pending_body_docs`. `parse_funcdef` and `parse_extenddef` lift `body.doc` onto
the declaration and drop the pair. Any pair still standing when `build()` finishes belongs to
a block that takes no docs — a lambda body, an `if` arm — and becomes an orphan. A block in a
body that is not its first item is an orphan too, and a different code, because inside a body
there is no declaration it could plausibly have meant.

That is O(1) bookkeeping and total by construction: every block ends up attached, lifted, or
in `orphan_docs`. `tests/unit/test_doc_block_attachment.py` is the gate, and it asserts the
totality rather than any one of the three outcomes.

---

## 6. The `docs` semantic pass

A new whole-program pass named `docs`, running **after `collect` and before `externs`**.

The order lives in the `SemanticAnalyzer.check()` docstring, which is the authority;
`docs/internals/semantic-passes.md` describes each pass, and the pass list in `CLAUDE.md`
gains one name. Fifteen passes becomes sixteen, so every place that states the count moves
with it.

Two reasons for that position:

- The pass needs the AST and the symbol table, and nothing else. Parameter names, the
  return type, the error arm and the public flag are all on the declaration already. No
  type resolution is required.
- It must run **before `instantiate` and `monomorphize`**. A generic function's doc block
  is written once, and checking it after monomorphization would report the same mistake
  once per instantiation.

The seam is exact. In `_check_multi_file` (`sushi_lang/semantics/semantic_analyzer.py`) the
pass goes after the global tables are assigned and aliased, and before the externs loop. Any
earlier and the merged symbol table does not exist yet.

It is whole-program in the shape `collect` is: a loop over units against one shared table.
That is worth saying, because every always-on check below is local to a single declaration
and would work per unit. The position ahead of `monomorphize` is what makes the pass
whole-program, not the checks.

**Library units are skipped.** A source library's units arrive in the unit table as ordinary
units, and a consumer must not be told about the library author's doc typos. Under
`--warn-missing-docs` it matters more: a consumer would otherwise be warned once per
undocumented symbol in every library it imports.

### Always on

| Condition | Code | Kind |
|---|---|---|
| `- Parameter` names a parameter this callable does not declare | CE7001 | error |
| two `- Parameter` tags for the same name | CE7002 | error |
| a second `- Returns:` or `- Errors:` | CE7003 | error |
| an unrecognised tag keyword, `- Paramter:` | CE7004 | error |
| a doc block in a body that is not the first item | CE7005 | error |
| a declaration with a block above it and a block first in its body | CE7006 | error |
| a doc block that documents nothing | CW7001 | warning |

CE7001 through CE7004 are tag errors and CE7005 and CE7006 are position errors, which is why
they are numbered in those two runs. CE7001, CE7002, CE7003 and CE7006 are relational: a
caret on the tag or the block, and a `note` with its own `file:line:col` on the declaration,
on the first tag, or on the other block.

**The two repeat cases are separate codes, because they are separate rules.** `- Parameter`
is keyed by the name it carries — many of them are legal, one per parameter — so CE7002 says
which parameter was documented twice, and its note points at the first tag for that name.
`- Returns:` and `- Errors:` are singletons by §3, keyed by the tag alone, so CE7003 says the
tag may appear once. The two mistakes read differently and are fixed differently: one is a
copied-and-not-renamed tag, the other is a tag written twice.

The typo case earns a code of its own rather than being treated as prose. A misspelled tag
is silently invisible in every documentation system that treats it as text, and that is
the failure this feature exists to remove.

### Telling a typo from prose

A tag is a Markdown list item (§3), and so is an ordinary prose bullet. The rule that
separates them has to catch `- Paramter:` without claiming `- Note that this is fast.`

A list item shaped `- <Word>[ <name>]:` is a **tag candidate**. A candidate whose word is a
known keyword is a tag. A candidate whose word is within edit distance 2 of a keyword is
CE7004, with a `help` line naming the tag that was meant. Everything else is prose.

```
- Parameter a: ...     tag
- Returns: ...         tag
- Paramter a: ...      CE7004, help: did you mean `- Parameter:`
- Retruns: ...         CE7004, help: did you mean `- Returns:`
- Note: ...            prose -- distance 4 from every keyword
- Deprecated: ...      prose -- reserved by §3, and no code may be registered for it
- see docs/ffi.md      prose -- no `Word:` shape
```

Distance 2 is the boundary because it catches a transposition plus a dropped letter, which
is what a mistyped keyword looks like, and stops short of `Note`. The reserved tags stay
prose deliberately: `tests/unit/test_error_registry.py` is an exact-match ratchet on codes
nothing emits, so a code cannot be registered for `- Deprecated:` until phase 6 emits one.

### Behind `--warn-missing-docs`

Completeness is opt-in, the way `missing_docs` is in Rust. A codebase that has not been
documented yet must not become a wall of warnings on the day the feature lands.

**Five lints, not four.** The table below carries one row this section did not have before
phase 5: a unit with no block. A unit block travels in the `.slib` as `unit_docs` and
`--lib-info` prints it under the unit name, so a library whose units say nothing is the
first hole a reader meets (R32).

| Condition | Code |
|---|---|
| a declaration with no doc block | CW7002 |
| a documented callable with a parameter that no `- Parameter` tag names | CW7003 |
| a documented callable that returns a value, with no `- Returns:` | CW7004 |
| a documented function that declares `\| E`, with no `- Errors:` | CW7005 |
| a unit with no doc block | CW7006 |

The codes go in `internals/errors/warnings.py`, which holds every warning whatever its
family, with `Category.DOCS`. CW7001 is there already.

**Every declaration is asked, public and private** (R29). The `public` marker is not the
test, and the reason is the ruling itself: an internal API is documented surface as much as
an exported one. The marker could not have answered the question either, because a constant
carries no `PUBLIC` at all (#466, still open). A struct field and an enum variant are each
asked on their own, because each carries its own `doc` key in the manifest and `--lib-info`
prints each under its owner (R31).

**Two exemptions** (R30). `fn main()` is nobody's API, and a library cannot declare one at
all (CE3501). An `unsafe external` block and the declarations inside it carry
`because "..."`, which acknowledges the contract that matters at that seam. Nothing else is
exempt, and one predicate is the only place either one is named.

**A block lint presupposes a block** (R33). CW7003, CW7004 and CW7005 fire only on a
declaration that ALREADY carries a block. Without the rule every undocumented function
would collect CW7002 AND CW7004 for one omission, and the whole tree would report 1130
undocumented parameters and 3319 missing `- Returns:` instead of 8 and 15. A block that is
absent is CW7002 and nothing else.

**The caret.** Every declaration kind carries a span narrow enough to point at:
`name_span` for a constant, struct, enum, perk, perk method, extern declaration, function
and extension; `perk_name_span` for a perk implementation; `namespace_span` for an external
block; `loc` for a struct field, an enum variant, and for the parameter CW7003 is about.
CW7006 has no declaration to point at, so it is reported on the unit with no caret. It is
the one lint about something that is not there.

The flag is a long `--flag`, matching every flag in `sushi_lang/compiler/cli.py` but `-o`.
A `-W` tier system was considered and set aside: it is a whole CLI surface to design, and
it would have to decide which existing `CWxxxx` warnings move behind a tier. That is a
separate piece of work, and this feature does not need it.

One flag is still more than it looks. This is the compiler's **first** warning-control
flag. The nearest precedent is CW5001, silenced by writing `because "..."` on the
declaration — a source opt-out, not a CLI gate. Phase 5 owns that plumbing, and it is why
phase 5 is a phase of its own.

### Phase 5 rulings

**R28 — one switch.** `--warn-missing-docs` is one long flag with no value, and it turns on
every lint above. An optional value list can be added later without a change of spelling,
so nothing is lost by starting with the switch.

**R29 — every declaration warns, public and private.** Stated above. An author who leaves a
private helper undocumented has to be told, because a reader of the code is a reader.

**R30 — two exemptions: `fn main()` and the FFI seam.** Stated above.

**R31 — a member warns.** A struct field and an enum variant each carry their own `doc` key
in the manifest, and the index can see the gap, so the lint says so. This is 41 of the
bundled stdlib's 114 findings.

**R32 — a unit with no block warns.** The fifth lint. Section 6 named four.

**R33 — the three block lints presuppose a block.** Stated above, with the measurement that
settles it.

**R34 — one walk.** `documented()` yields every block that is attached. The lint needs the
other half, so `declarations(program)` yields `(kind, node)` for every declaration and
`documented()` filters it. Two walks over one AST would drift, and `tests/docs_sweep.py`
reads the same walk (R22). The walk keeps the order `documented()` used, because the sweep
numbers its generated `doc_example_<n>` helpers from it.

**R35 — the flag is a keyword argument, not an options object.** `SemanticAnalyzer.__init__`
gains `warn_missing_docs: bool = False`, beside `unit_manager`, `library_linker` and
`library_registry`, which are keywords already. `compile_multi_file` unpacks it from `args`
the way `--ignore-compiler-version` is unpacked. A `CompilerOptions` object is the right
answer to the SECOND warning flag and the wrong answer to the first.

**R36 — the test runner gains a `COMPILER_FLAGS:` directive.** A `.sushi` fixture could not
turn a compiler flag on, so a flag-gated diagnostic had no fixture. One field on
`TestMetadata`, one branch in the directive parser, and one insertion at each of the two
`./sushic` call sites. A flag the runner owns — `-o`, `--lib`, `--lib-info`,
`--clean-cache`, `--build-stdlib`, `--cache-dir` — is refused with a printed warning.

**R37 — phase 5 does not document the stdlib.** Documenting the bundled modules is a proof
of concept that comes AFTER the implementation. The repo gate is a shrink-only budget, in
the shape `REGISTRY_SIZE` already uses, and not an assertion of zero.

### Measured, at phase 5

The test tree is not a corpus: nobody runs this flag over it. What counts is the bundled
stdlib and the toolchain.

```
== stdlib(src_sushi), 4 modules      == toolchain/src, 1 program
   CW7002 no block       110            CW7002 no block        25
   CW7003 parameter        0            CW7006 unit             1
   CW7004 returns          0            TOTAL                  26
   CW7005 errors           0
   CW7006 unit             4         (function 25; main is exempt)
   TOTAL                 114

(function 51, variant 28, field 13, constant 8, struct 6, enum 4)
```

CW7003, CW7004 and CW7005 report nothing because no bundled module carries a block yet.
They are self-limiting by R33, and they are what makes the flag useful once the blocks
exist. `tests/unit/test_stdlib_doc_blocks.py` holds the 114 as a shrink-only budget.

---

## 7. Diagnostics

### The three syntax errors

CE6011, CE6012 and CE6013, in `sushi_lang/internals/errors/syntax.py`, which owns the CE6xxx
range. The main family tops out at CE6010 today, with CE6101 and CE6102 in the sub-family
above; all three codes are free.

- **CE6011** — a doc block is opened and never closed. The location is the opening `##:`,
  not the end of the file, because the opener is where the author can fix it.
- **CE6012** — a `:##` with no opening `##:`.
- **CE6013** — a line-initial `##:` inside a doc block. The location is the inner opener,
  with a `note` on the outer one. The block that is actually broken is the outer one, and the
  note is what says so, which makes this a relational diagnostic. Rendering it with a single
  location would be a regression.

All three are raised from `lexer_callbacks`, as §4 sets out: CE6011 and CE6012 from the two
`%ignore`d delimiter terminals, CE6013 from a scan of the matched `DOC_BLOCK` token. One
place, at lex time, before the builder has an opinion.

### The doc family

CE70xx, in a **new** `docs.py` module under `sushi_lang/internals/errors/`. A code may only be added
in the file that owns its range, and CE7xxx is entirely unused today.

This needs four supporting changes:

1. A `DOCS = "docs"` member on `Category` in `internals/errors/registry.py`.
2. An import of the new module in `internals/errors/__init__.py`. Registration is an
   import side effect; nothing references the module by name.
3. An amendment to `_category_of_range()` in `tests/unit/test_error_registry.py`. Its final
   statement is a catch-all `return {Category.SYNTAX}` for anything at or above 6000, so
   CE70xx would be forced into the wrong category. It becomes a bounded
   `if number < 7000: return {Category.SYNTAX}` followed by `return {Category.DOCS}`.
   `RANGE_EXEMPT` is not the escape: it is documented shrink-only.
4. The removal of the `internals/errors/docs.py` entry from `ALLOWED` in
   `tests/unit/test_path_references_exist.py`. It was added as an explicitly TEMPORARY
   exemption so that this document could name a module that did not exist yet. Phase 2
   creates the module, so phase 2 takes the exemption back out.

Warnings go in `warnings.py` regardless of family, as every warning does. A doc warning can
carry `Category.DOCS` and still live there, because the range test returns every category for
a `CW` code.

`tests/unit/test_error_registry.py` holds an exact `REGISTRY_SIZE` tripwire, and it needs a
bump. Nothing else: the running changelog that comment used to carry was deleted in #444, and
the comment now says why. Why a code exists belongs in its `doc` field in
`internals/errors/docs.py`; what changed belongs in the `CHANGELOG` and the git log.

Phase 2 registers ten codes in all — CE6011, CE6012 and CE6013 in `syntax.py`, CE7001 to
CE7006 in the new `docs.py`, and CW7001 in `warnings.py`. Every one of them is emitted by the
end of the phase, because `test_unreferenced_codes_match_the_allowlist` is an exact-match
ratchet: a code registered and never emitted fails the suite. Nothing may be reserved here
for a later phase.

---

## 8. `.slib` carriage

`docs/design/libraries.md` is BUILT at container version 4, and `docs/library-format.md`
carries the v4 schema. The container is settled, so this section specifies the field and the
records that hold it.

### Docs live in the manifest

`docs/design/libraries.md` is normative:

> **The rule: everything in the library must be knowable from the manifest alone.**
> `--lib-info` must never parse source to answer what a library contains. For a source
> library the index is *derived* from the units at build time; the source section is the
> authority, and the index is a cache of it.

So doc text is a manifest field, in structured form, and is **not** re-derived from the v4
source section.

The last sentence of the rule is the one that costs something here. A source `.slib` is the
default kind and ships every unit's complete source text, doc blocks included, so a doc key
in the manifest is a **second copy of text already in the file**. That is deliberate, and it
is what an index is for: `--lib-info` answers without a parser, on a library whose compiler
may not even match. §11 rejects re-derivation for the same reason.

### The record

Every per-symbol record gains an optional `doc` key:

```
"doc": {
    "summary": "Jumps through hyperspace.",
    "body":    "The drive needs a warm coil. See `spin_up`.",
    "params":  {"a": "The incoming argument.", "b": "The second one."},
    "returns": "The jump distance in parsecs.",
    "errors":  "When the drive is cold, this returns `JumpError.NotReady`."
}
```

`summary` is the first paragraph. `body` is the prose after it, with the tags removed. The
whole dedented block is **not** stored. The index would then carry its own input, in a
section that is never compressed and that every reader unpacks in full, duplicating text the
source section already holds verbatim. A renderer that wants the block back builds it from
the fields.

Every key is optional, and the `doc` key itself is **absent** when a symbol has no doc block.
Every existing `.slib` fixture stays valid, and an undocumented library grows by nothing.

`examples` is reserved for phase 4, and `deprecated` and `traps` for the reserved tags in §3.

### Where the key goes

Two producers, and the count is not six. It is every record that names a symbol an author can
write a doc block on:

| Producer | Records that gain `doc` |
|---|---|
| `backend/library_manifest.py` | public function, public constant, struct, struct field, enum, enum variant |
| `semantics/library_templates.py` | `serialize_generic_function`, `serialize_generic_struct`, `serialize_generic_enum`, `serialize_perk`, `serialize_perk_impl` |

Line numbers are deliberately not given. The four an earlier draft of this section carried
had drifted by five to seven lines within a fortnight, in a commit that had nothing to do
with the manifest.

Two records deliberately do **not** gain the key:

- **The parameter record.** Per-parameter text lives in the enclosing function's `doc.params`
  map, keyed by name. A parameter is not a symbol; it is part of one.
- **The binary closure path.** Those records describe private symbols shipped so that a
  binary library links. A private symbol is not part of the documented API.

One gap had no home when this section was written: the per-method record inside
`serialize_perk_impl` was `{name, symbol}`, so a documented perk method did not survive the
boundary. **R3 closes it** -- that record gains a `doc` key. A perk DEFINITION has no
methods array, so its methods' blocks travel only inside the source slice.

### A generic's doc block does not travel for free

`slice_decl_source` takes `[loc.line, loc.end_line)`, and §4 keeps a doc block a **sibling**
of its declaration rather than a child, precisely so that it does not move `loc`. The two
facts compose: the slice starts at the declaration keyword, so the doc block is outside it.
A generic's docs are lost unless the record carries them.

State it as an invariant in both directions. The slice excludes the block; the record carries
it. And the consumer's re-parse — `deserialize_generic_function` runs `parse_to_ast` and
asserts exactly one declaration — must tolerate a doc block should one ever land inside a
slice, because a grammar that refused one there would turn a slicing bug into a parse failure
at the consumer.

### Unit docs

§2's third position is a block that documents the unit. It has nowhere to live in a
per-symbol record, so the manifest gains one top-level key:

```
"unit_docs": {"lib/hyperdrive/engine": { ...a doc record... }}
```

A map beside the existing `units` array, not a change to it. `units` is an ordered list and
the order is load-bearing for the consumer's injection; readers index it as an array, and
`slib_info.sushi`'s `ml_len` and `ml_at` helpers work on an `Arr` and return nothing for a
`Map`.

A **library**-level description is not this feature's business. `nori.toml`
`[package] description` already carries one, and it is the only prose Omakase renders.

### What does not change

- The container `VERSION` stays at **4**. The metadata blob is an open msgpack dict, and the
  whole of read-side validation is the magic, the version, per-section truncation, msgpack
  well-formedness and a size cap. There is no key set and no schema, and every consumer reads
  it through `.get()`. An added optional key is not a format change.
- `sushi_lib_version` stays at `"2.0"`. A reader that does not know the key ignores it.
- `slib.sushi` needs no change. `slib_read_metadata` returns the whole `MsgValue` tree, so
  the self-hosted reader reaches a new key without being taught about it.

### What does change in Sushi

That last point is about the *reader*, not about the *tool*. `slib_info.sushi` navigates by
known keys, and it needs work:

- **Parameters render in declaration order, read from the signature and looked up by name.**
  This is normative, not a suggestion. `ml_get_str(params, name)` while walking the
  function's existing `params` array costs no new helper. The reason is the ORDER and only
  the order: whatever order a map happened to have would not be the signature's.

  An earlier draft of this bullet also said a `Map` cannot be walked at all. That is true
  of the `ml_*` helpers -- `ml_len` and `ml_at` are `Arr`-only -- and not of the language.
  `MsgValue.Map(MsgValue[], MsgValue[])` destructures in a `match`, and `mp_map_get` in the
  stdlib does exactly that. Phase 3 reads `unit_docs` by key the same way.
- **A multi-line `body` needs a line splitter**, and its indent has to match Python's byte
  for byte. §9 carries that obligation.
- **`ml_get_str` cannot tell an absent key from an empty string.** Both give `""`. Suppress
  on empty, or test for `Nil` first.

### Size

The metadata blob is not compressed today: it is the index, and every reader must be able to
take it cheaply. Committed fixtures run 1-9 KB, so prose for every public symbol is plausibly
the same order as the entire existing index — in the one section that is always read in full
and, for the default source kind, duplicating the source blob.

Two things hold that in hand whatever happens. The key is absent when there is no doc block,
so an undocumented library pays nothing, and the record stores the parsed fields and not the
raw block.

The third is that **the blob will be compressed** (R8). Both of the reasons `libraries.md`
gave for not compressing have expired: `compression/zlib.sushi` is in the stdlib, so a
Sushi-side inflate is no longer missing, and `FLAGS` bit 0 is already claimed. That is a
`libraries.md` decision and a container decision, so it is not this feature's to take — but
it is the reason nothing here is shaped around a byte budget.

### Measured, at phase 3

Two libraries were built against a real tree, each beside an undocumented twin carrying the
same declarations:

| Library | Index without docs | Index with docs | Growth |
|---|---|---|---|
| 16 records: every position this section names | 1,638 B | 3,130 B | +1,492 B (+91%) |
| 83 records: 40 functions with all four tags, 8 structs, 6 enums | 5,787 B | 22,578 B | +16,791 B (+290%) |

The second is the stdlib-sized case, and it is about **202 bytes a documented symbol**. Of
its 16,791 bytes, **13,921 are the prose itself** and 2,870 are msgpack framing -- 35 bytes
a record, which is the `doc` key, the field names and the length prefixes.

The whole file for that library, at the default source kind: 9,176 B with no doc blocks in
the source at all, 26,181 B with the blocks and no index carriage, and 42,972 B with both.
So **doc prose costs about twice its own size in a source `.slib`**: once in the source
section, once in the index — and both copies are plain text in an uncompressed container,
which is what makes the number look large and what compression takes back.

These are measurements and not a budget. **R8 is the ruling.**

### What phase 3 cannot promise

"Every documented symbol appears in `--lib-info`" is not true today, and this feature does not
make it true:

- A **perk** reaches the manifest only when an exported generic's constraint names it, and a
  **perk implementation** only when its perk was already seen. A documented perk that no
  generic references is not in the file at all.
- **Externals are never serialized.** The external namespaces are read to drive the CE5006
  rejection and nothing else, and `dependencies` is a list of bare strings with nowhere to
  hang a key.
- **Generic declarations are excluded from `public_functions`, `structs` and `enums`** by
  design; their docs ride in `templates`.

Making perk serialization unconditional is a `libraries.md` change and is out of scope here.
Record the limit rather than papering over it.

Phase 3 found three more, and each one is a limit of a record and not of the file:

- **An extension has no manifest record at all.** `extend i32 squared()` reaches a consumer
  through the source section or through monomorphized bitcode, and `--lib-info` has never
  listed one. Its doc block cannot travel in the index.
- **A generic struct's field blocks, and a perk definition's method blocks, travel only
  inside the source slice** (R3). They are in the file; the index cannot answer for them.
- **An `- Example:` is dropped from the index** (R7).

### Phase 3 rulings

Each one closes a question this section or S9 left open.

**R1 — `body` is a parsed field, and it stops at the first tag.** `DocBlock.body`, set by
`parse_doc_block` in the same walk that builds the summary and the tags. It is the entries
between the end of the summary and the FIRST tag candidate; everything from that candidate
onward belongs to the tags. Two consequences, both accepted: prose written after the tags is
not carried, and a fenced example cannot leak into the body. `DocTag.word` and
`DocBlock.orphan_reason` are the precedent -- the parse knows the answer and no consumer
should derive it again.

The derivation an earlier draft implied is not safe. "The block with the tag lines removed"
leaks example code into the body, because a Markdown list item breaks at a blank line: a
fenced `- Example:` tag stops at the fence's first blank line, and a line-removal rule then
reads the rest of the fence as prose.

**R2 — one function builds every record.** `doc_record(doc) -> Optional[dict]` in
`semantics/library_templates.py`, called by both producers through the `with_doc(record,
node)` convenience beside it. It returns `None` for a block that is absent or says nothing,
and it omits every field that has no text. That module's docstring widens to say what it is:
the parts of a manifest that come from the AST -- the generic templates, and the doc records.

A new module of its own beside it was the alternative. Rejected: the backend manifest
generator already imports this module, and one function does not earn a file. Note the
second cost of naming one -- `tests/unit/test_path_references_exist.py` reads a path-shaped
reference in `docs/` as a promise, so a path named here has to exist in the same commit,
even when the sentence naming it says the file was NOT written.

**R3 — the key goes where a record already exists.** `serialize_perk_impl` has a `methods`
array, so each method record gains `doc`. `serialize_perk` has none, so the perk gains its
own `doc` and nothing more; inventing an array there is a `library-format.md` change and is
out of scope. The same limit applies to a generic struct's fields and a generic enum's
variants: the record is a source slice with no member array.

**R4 — a private record carries no doc.** `_extract_templates` marks a closure-shipped
generic `record["private"] = True`, and drops the doc key on the same line.
`templates.private_functions` and `templates.constants` never gain one. A private symbol is
not part of the documented API.

**R5 — the report prints the mode a type cannot carry, which is `nom`.** S9 said the report
drops the mode. Measured: it drops `nom` only. `peek` and `poke` ride on `ReferenceType`, so
`str(ty)` already spells them and `--lib-info` has been printing `fn reads(peek i32 n) i32`
all along. `nom` is the one mode no type can spell, so the record's own `mode` field is its
only source. Printing a mode that is already in the type string would double it, so
`render_params` prefixes `nom ` and nothing else, on both sides. S1's claim is now true of
the tool for all three.

**R6 — the render order, and the blank lines.** Per record: the summary, a blank line, the
body, then the tags. No blank line before the tags, which is what S9's example shows. The
tags print in order: `- Parameter` in DECLARATION order, then `- Returns:`, then `- Errors:`.

A blank line inside a body prints as an empty line with no indent, so the report carries no
trailing whitespace. The blank line between the summary and the body prints only when there
is a body to separate. S9's example has no body and is unchanged by this rule.

**R7 — `- Example:` is not carried.** This section reserves the `examples` key for phase 4,
so phase 3 stores no example and prints none. For a source library the text stays in the
source section; for a binary library it is not in the file. This is the one thing an author
can write that phase 3 drops.

**R8 — the size is not a constraint, because the blob will be compressed.** Ruled by David
on 2026-08-25, against an earlier draft of this ruling that accepted the size as a permanent
cost: the index is going to be zlib-compressed, so its size is not a reason for this feature
to store less than it needs. An undocumented library still pays nothing, because the key is
absent.

Phase 3 does not do the compressing. That is a `FLAGS` bit, a read side in both `slib.sushi`
and the Python reader, and a `docs/design/libraries.md` decision about when the index is
cheap to take — one feature at a time. What changes here is only what the number means: the
measurement above is what tells that work what it is worth, and it is not an argument for
carrying less text.

**R9 — `unit_docs` uses `own_units()`.** The same filter as `collect_unit_source`, so the
index, the unit array and the source section can never disagree about which units are ours.
A bundled stdlib module's docs are not shipped.

**R10 — no new codes.** A doc record is data. Every block in it already parsed and already
passed the `docs` pass, so phase 3 has no new failure of its own.

**R11 — the report prints docs in every section that exists.** Public functions, generic
functions, public constants, structs and their fields, enums and their variants. Perks, perk
implementations, generic structs and generic enums have no section in the report today, so
their records carry the key and nothing prints it. Phase 3 adds no section.

**R12 — the unit block prints under its unit name**, two spaces further in, in the existing
`Units (n):` section. A record with no `params` array renders no parameter line, so a
`- Parameter` tag on a unit, a struct or a template is stored and not printed.

---

## 9. `slib-info` rendering

Two reports, behind one switch. The PLAIN one is the API surface -- one line per symbol,
dense, and the report phase 3 shipped. `--docs` (R50) adds every documentation block.
Neither paginates, neither reflows.

Docs are indented two spaces under the signature they belong to, inside the existing
sections:

```
Public Functions (1):
  fn hyperspace_jump(i32 a, u8 b) i32 | JumpError
    Jumps through hyperspace.

    The drive needs a warm coil.

    - Parameter a: The incoming argument.

    - Parameter b: The second one.

    - Returns: The jump distance in parsecs.

    - Errors: When the drive is cold, this returns `JumpError.NotReady`, and
              `JumpError.Overheated` when it is too warm.
```

**R6 as amended by R38.** Phase 3 put a blank line between the summary and the body and
nowhere else. R38 adds three more and one alignment rule; R6's ORDER half is untouched,
and so is what a blank line inside a body prints as.

A symbol with no block renders with no blank line and no placeholder. That is why a run of
bare signatures stays dense, in the documented report as much as in the plain one: rule 2
closes a block, and a signature with no block has none to close.

The two implementations need these helpers, under these names:

- `ml_is_nil(MsgValue) -> bool`, because `ml_get_str` cannot tell an absent key from an
  empty string -- both give `""`. A doc record is read with `ml_get` and tested for `Nil`.
- `print_lines(indent, text, opener)` -- `text.split("\n")`, one `println` per line, an
  empty line printed empty, and `opener` on the FIRST line with every later line indented
  past it. Python must use `str.split("\n")` and **not** `splitlines()`: the latter drops
  the trailing empty field and also breaks on `\r`, `\x0b` and `\x0c`. Measured against
  `.split("\n")` on `"a\nb"`, `"a\n"`, `"\na"`, `"a\n\nb"`, `"a"` and `""`, the two
  agree on every one. The hanging indent lives here and not in a tag printer, so one
  function owns both the cut and the alignment.
- `print_doc_record(doc, owner, indent, show) -> bool` -- the whole record in R6's order,
  and **whether it printed**, which is what rule 2 reads. It gates `--docs` for the whole
  report, and reads the owner's own `params` array for the order.
- `open_record(pending) -> bool` / `_Records` -- rule 2's blank line, before a record and
  never after one.

### The parity obligation

`slib-info` has **two** implementations, and they must produce byte-identical success
reports:

- Python, `print_library_info` in `sushi_lang/compiler/cli.py`
- Sushi, `toolchain/src/slib_info.sushi`

`tests/unit/test_slib_info_parity.py` locks them with
`assert py_run.stdout.endswith(tool_run.stdout)`, and `toolchain/README.md` states the
contract: error messages may differ between the tool and the fallback, the success report
may not. `tests/unit/test_slib_info_docs.py` locks the same thing on a DOCUMENTED library,
and the older module keeps an undocumented one, which is the regression that says a report
with no docs in it is unchanged.

So every rendering change here is two implementations plus a rebuild through
`toolchain/build.py`. This is the real cost of the requirement, and it is worth paying —
the parity gate is what keeps the self-hosted tool honest.

Three costs in particular, since "a plain dump" understates them:

- **A multi-line body needs a line splitter on both sides**, with identical blank-line and
  trailing-newline handling. This turned out to be the cheap half:
  `Sushi .split("\n")` and Python `str.split("\n")` agree on every edge case, so the
  splitter is one call on each side. `group_thousands` in `slib_info.sushi` is not the
  precedent for it.
- **`toolchain/build.py` runs by hand.** A stale `toolchain/bin/slib-info` keeps printing the
  old report, and **nothing tells you.** An earlier draft of this bullet said the parity test
  catches it. It does not: `test_slib_info_parity.py` compiles `TOOL_SRC` into a temporary
  directory on every run, so no test reads the built binary. After a rendering change, run
  `./toolchain/build.py` by hand; a green suite is not evidence that you did.
- **Parameter modes.** §1 says `slib-info` can print `nom` / `peek` / `poke` from the manifest
  alone. Measured: it prints two of the three already, because `peek` and `poke` are part of
  the type string. R5 adds the third and makes §1 true.

### Phase 6 rulings

**R38 — the record layout, amending R6.** R6 said "no blank line before the tags, which is
what S9's example shows". At the size a real library reaches that is the fault, not the
rule: measured on 40 documented functions, 8 structs and 16 fields, the report is 428
lines and ten terminal screens with no blank line anywhere between one symbol and the
next. Whitespace is the only thing that makes that stream scannable.

Five rules:

1. **A blank line before the first tag**, when there is a tag and prose above it. This is
   the amendment to R6.
2. **A blank line before a record whose predecessor printed a block.** Before and never
   after: an after-rule doubles with the blank line every section already prints when it
   closes. A member is a record too, so a struct's own block is separated from its first
   field and one field from the next.
3. **A hanging indent on a continuation**, aligned under the tag's TEXT and not under its
   dash, or a wrapped line reads as a new item.
4. **A blank line between tags.** Ruled by David on 2026-08-26. A parameter, a return and
   an error are three kinds of claim; it costs about six lines a documented function, and
   the report is opt-in (R50), so the reader who asked for prose is the one who pays.
5. **A blank line before a section header**, which the report had already.

Rules 1 and 4 are ONE predicate in the implementation -- "something is already above
me" -- because that is the whole condition either of them tests.

**R39 — no reflow.** A tag's text wraps where the author wrote a newline and nowhere else.
Rule 3 re-indents a continuation that already exists; it does not rewrap a long line. A
reflow would destroy a fenced example, and §2 does not reflow the text anywhere else in
this feature.


**R45 — user-visible text spells a generic `@(...)`.** The report printed
`fn pick_bigger<T: Doubler> (template)` and `struct Box<T>:`. Angle brackets are the
INTERNAL identity spelling and `docs/design/type-identity.md` reserves them for interned
names, mangled symbols and the match sites that read them.

The MANIFEST keeps them. A consumer reads every `type` and `return_type` back through
`parse_type_string`, so those strings are a wire format, not display text; converting them
at the producer would break every library already built. **The renderer converts**, which
is what `display_type_name` already did for diagnostics: no `<`, an `->` anywhere, or
unbalanced brackets all mean "leave it alone"; otherwise `<` opens and `>` closes. The
Sushi tool spells the same four rules as `to_surface`.

**R46 — a generic function's record carries its signature.** `params`, `return_type` and
`error_type`, the same three keys a concrete record carries, built by ONE function
(`signature_record`) so a template and a concrete function cannot drift apart. Without the
parameter list a template's `- Parameter` tags named nothing a report could print them
against: they were stored by phase 3 and rendered by nothing.

`(template)` is gone with it. It stood where the parameters belong, and the section header
already says `Generic Functions`.

Slicing the signature out of the record's `source` field was rejected: §8's rule is that
`--lib-info` never parses source, and a tool that reads `source` to render a signature is
one refactor away from parsing it.

**R47 — every manifest section has a renderer.** Generic Structs, Generic Enums, Perks and
Perk Implementations were carried by phase 3 and printed by neither implementation. Each
one is suppressed when empty, which is the existing convention, and each generic section
stands beside its concrete twin rather than being filed away with the other templates: a
reader looking for `Box` wants it near `Point`.

Two limits stay, and §8 records both: a generic struct's FIELD blocks are not in the index
at all (R3), and a perk reaches the manifest only when an exported generic's constraint
names it.

**R49 — a function's error arm travels.** `fn improbability(i32) i32 | DriveError` reached
the manifest as `{name, params, return_type: "i32"}` and printed as `fn improbability(i32
factor) i32`. The error type was not a render fault: it was **uncarried**, and §8's rule
forbids `--lib-info` from reading source to recover it.

The record gains an optional `error_type`, absent when the declaration does not spell one --
the default is `StdError`, and a record that named the default would claim the author wrote
it. An added optional key does not move the container version (§8).

**R50 — the doc blocks are opt-in, behind `--docs`.** Ruled by David on 2026-08-26.
Measured on a realistic library -- 40 documented functions, 8 structs, 16 fields -- the
report is 428 lines, ten terminal screens, of which the signature lines are one and a half.
A reader asking what a library exports was reading nine screens of prose to find out.

One switch for the blocks and the examples together, spelled `--docs` at BOTH ends, so the
delegation forwards it as itself rather than translating a name. Every doc record in either
implementation comes through one function (`_print_doc_record` / `print_doc_record`), so
the switch is read once and not at each of the ten sections.

The tool grew a real command-line parser with it, and answers `--help` on its own. A flag
is a flag wherever it stands, the one bare word is the path, and a second file is a usage
error. Its usage line has one spelling, a `const`, because two of them drift the first time
one is edited.

---

## 10. Doc tests

Phase 4. A fenced code block under `- Example:` is compiled and run.

### Wrapping

A snippet with no `fn main(` is wrapped, the way rustdoc wraps one. Written by the author
as two lines of intent:

~~~sushi
- Example:
```sushi
let i32 d = hyperspace_jump(3, 7)??
println("{d}")
```
~~~

and compiled as a program, with the import injected, the body indented into
`fn main() i32:`, and `return Result.Ok(0)` appended.

A snippet that declares its own `main` is compiled verbatim. One rule, and it covers the
whole-program case for free.

The reason to wrap is that an example is documentation first. Six lines of ceremony around
two lines of intent teaches the ceremony.

### Showing a fence inside a fence: the outer one is `~~~`

The illustration above is the shape every page that teaches this feature needs: a fenced
block whose contents are a doc block that itself holds a fence. **The convention is a
`~~~` outer fence**, in `docs/documentation-blocks.md`, in the language reference, and
here.

CommonMark closes a fence only with a fence of the SAME character that is at least as
long, so a longer run of backticks would work too. Tildes are the convention because the
outer and the inner delimiter then look different, which is the whole point of the
illustration. `pymdownx.superfences` is in `mkdocs.yml` and renders both.

`tests/docs_sweep.py` implemented no part of that rule. It matched ` ```sushi ` and
` ``` ` at column 1 and nothing else, so it read INSIDE an illustration and collected the
inner example as a block of its own -- measured with a tilde outer fence and with a
four-backtick one, and it happened with both. R27 taught it to step over a fence it cannot
close, and both collectors now share one implementation of the rule
(`closes_fence`).

A doc block cannot contain a doc block, so this is a problem for `.md` pages only: a `##:`
inside a block is CE6013 whether it is indented or not.

### The runner

There is **no importable compile driver**. `sushi_lang.compiler.cli.main(argv)` prints a
banner, builds its own `Reporter` internally and never returns it, and writes diagnostics
to the console. Every existing harness shells out to `./sushic`, and the doc-test runner
does the same.

`tests/docs_sweep.py` gains a second collector that walks `.sushi` files for doc blocks.
It already carries everything else needed: the outcome vocabulary — pass, expected-error
`CExxxx`, skip, and fail as the residual — the temp-directory handling, the thread pool, and
`NO_COLOR=1` so stderr matching is robust. It stays a by-hand tool and deliberately not a CI job, which is the
ruling that shaped it.

Two things do not carry over. Its candidate filter requires both `fn main(` and `return`
to be present in a block, to tell a runnable example from a quoted signature. Wrapping
makes that test wrong for doc snippets, so the new collector needs its own rule: a doc
example is runnable unless it is marked otherwise. And its skip and expected-error markers
are HTML comments, which a `.sushi` file cannot carry — the doc-block collector needs a
marker that is legal inside a doc block. R16 gives it one: the marker rides on the fence's
own info string.

An example is compiled from OUTSIDE the unit it documents, which is rustdoc's model and
R18's ruling. Two things are then out of reach, and R21 makes each one a printed skip
rather than a failure: a PRIVATE declaration, which the generated file cannot call, and a
unit that declares `main`, which cannot be imported beside a second `main`.

### Phase 4 rulings

The numbering continues S8's list, so no number is used twice in this document.

**R13 — the block is partitioned before the tags are read.** `parse_doc_block` first
splits the dedented entries into prose regions and fenced regions. `_read_tags` and
`_read_body` see only the prose. Three defects go away at once: a tag-shaped line inside
example code is no longer a tag, an example is no longer truncated by a blank line, and
the body rule needs no special case for a fence.

Measured against the phase-3 parse, each defect was real. A line-initial `- Returns:` in
example code parsed as a `returns` tag and truncated the example there. `_read_tags` folds
a tag's continuation lines with `part.strip()`, so the indentation of an `if` body was
destroyed. Both rules are right for prose and wrong for code.

**R14 — an example is its own structure, kept verbatim.** A third dataclass in `ast.py`:

```python
@dataclass
class DocExample:
    code: str                    # the fence body, dedented by the fence's own indent
    attrs: str = ""              # the fence info string, as written
    loc: Optional[Span] = None   # the opening fence, so a diagnostic can point at it
    defect: Optional[Literal["no-fence", "unterminated"]] = None
```

and `DocBlock.examples: List[DocExample]`. The `- Example:` tag keeps only its caption —
the words on the tag line, usually none. An example carried in `DocTag.text` is not
usable, because the text of a tag is stripped and folded.

The code is dedented twice: once by the block rule, and once by the indent of its own
opening fence. The second one is CommonMark's rule for a fenced block, and it is what lets
an author indent the fence under its list item. Indentation INSIDE the fence is untouched,
which is the whole reason the structure exists.

The parse records the defect and the pass reports it, which is the split
`DocBlock.orphan_reason` settled in phase 2. Both classes go in `__all__`, and
`tests/unit/test_ast_all_is_complete.py` is the gate.

**R15 — many examples are legal, in source order.** Nothing changes in the `docs` pass:
`_SINGLETON_TAGS` is `("returns", "errors")` and an example was never in it. A declaration
with two examples has two things to show.

**R16 — the attributes ride on the fence info string, in the vocabulary the sweep already
has.** A `.sushi` file cannot carry an HTML comment, so the marker moves into the fence:

| Fence | Meaning |
|---|---|
| ` ```sushi ` | compile and run; a non-zero exit is a failure |
| ` ```sushi no_run ` | compile only — the example needs a file, a socket, or a long loop |
| ` ```sushi skip (reason) ` | do not compile; the reason is printed |
| ` ```sushi error CExxxx ` | must exit 2 and name every code given |
| any other info string | not a Sushi example; the sweep ignores it |

`skip` and `error` are the words the Markdown collector already uses, so the tool keeps one
dialect with two carriers. `no_run` is new and has no Markdown twin, because the Markdown
collector does not run anything. A renderer takes the FIRST word of an info string as the
language, so the extra words are harmless in phase 6.

**R17 — two new codes, both always on.** `- Example:` with no fenced block after it is
CE7007. A fence inside a doc block that never closes is CE7008. Both go in
`internals/errors/docs.py`.

They are always on rather than a phase-5 policy lint, because each one is a claim that
contradicts itself. The whole job of the tag is to introduce a fence (S3), so a tag with
nothing to introduce is wrong the way a `- Parameter q:` that names no parameter is wrong.
S6's split holds: an ABSENT example is policy, and stays phase 5's business.

**R18 — an example is compiled from OUTSIDE the unit.** One generated entry file, with
`use "<the documented unit>"` at the top. This is rustdoc's model: a doctest links the
crate and sees the public API. The import names the unit's own stem, not a path: the
entry file stands beside the unit, so the sibling name is what an author would write.

Compiling INSIDE the unit was measured and works — the generated file is the unit's own
source with the wrapper appended, and it reaches private declarations. Rejected: an example
that calls what a reader cannot call is not documentation, and the inside model still
cannot handle a unit that declares `main`. One mechanism, not two.

A unit import resolves against the ENTRY file's directory and there is no search path, so a
`use "helpers/x"` inside the documented unit resolves only from that unit's own directory.
The generated file therefore goes into a temp COPY of that directory. The copy is made once
per unit, not once per example.

**R19 — the wrapper is a helper plus a `match`, not a bare `main`.** For a snippet with no
`fn main(`:

```
use "<unit>"
<the snippet's own use lines>

fn doc_example_<n>() ~:
    <the snippet, indented four spaces, blank lines left blank>
    return Result.Ok(~)

fn main() i32:
    match doc_example_<n>():
        Result.Ok(_) ->
            return Result.Ok(0)
        Result.Err(_) ->
            return Result.Ok(1)
```

A body with `??` directly inside `fn main()` warns CW2511 on every such example. That
warning exists to discourage `??` in `main`, so a harness that writes the discouraged form
on the author's behalf teaches it. Measured: the helper form warns nothing, and an example
whose `??` fails still exits 1. The name carries the block index, so it cannot collide with
a symbol in the imported unit.

A snippet that declares its own `fn main(` is compiled verbatim, with the import injected
above it. That is the wrapping rule above, and it is unchanged.

**R20 — `use` lines are hoisted, and the injected import is not repeated.** A `use` inside
a function body does not parse, so every line that matches `^use ` moves to the top of the
generated file, in the order written. The unit import is injected only when the snippet does
not already import that unit, because a duplicate `use` is CW3001. `println` needs no
import at all, so the wrapper injects no stdio.

**R21 — two skips, each with its reason printed.** An example is SKIPped, not failed, when
the collector can see that it cannot be compiled from outside: the declaration is private
(measured CE3005), or the unit declares `main` (measured CE0101). The collector knows both
facts because it parses (R22). A skip prints its reason and is counted, the way the
Markdown collector prints a marked skip, so the hole stays visible.

**R22 — the collector parses, it does not scan.** It calls `parse_to_ast` and reuses the
`docs` pass's own walk over documented declarations, so it sees exactly what the pass sees
— a body-first block included — and it gets `is_public`, the declaration name and the unit's
own `main` for free. The walk becomes public API: `_documented` is renamed `documented` in
`semantics/passes/docs.py`, with its caller updated.

A file with no `##:` in it is never parsed, so the walk costs about a second over the
whole tree and the unparsed count means something: it is the files that carry a block and
do not parse. There are three, and all three are the CE6011, CE6012 and CE6013 fixtures,
which fail on purpose. The 33 other files in the tree that do not parse hold no block and
are not read.

**R23 — the manifest carries the code, and prints none of it.** `doc_record` gains
`examples: [str]` — the code of each example in source order. The attributes are not
carried, because an attribute is a harness instruction and not documentation. The key is
absent when there is no example. `slib-info` does not print examples: S9 is a plain dump,
and a fenced program inside it would bury the signature. Phase 6 renders them.

This closes R7, the one thing an author could write that phase 3 dropped.

**R24 — a bundled stdlib module is a library unit as far as the `docs` pass is concerned.**
`_inject_source_stdlib_units` builds a `Unit` with no provenance, and the pass skips a unit
only when it has one. Measured: a `CW7001` in `collections/iter.sushi` is then reported in
every program that imports the module. So the injector sets a provenance, the same way
`_inject_library_source` does, and a user is never told about a stdlib doc typo.

Nothing triggers this today, because no bundled module carries a block. It is the trap
waiting for whoever writes the first one, and it is the prerequisite for documenting the
stdlib later. It lands here because it is one line, and because the measurement that
justifies it is fresh.

The same line silences the diagnostic for us, so the repo needs its own gate: one pytest
module runs `check_docs` over every module in `SOURCE_STDLIB_MODULES` and asserts no CE70xx
and no CW7001.

**R25 — the sweep grows a selector, and runs each example in its own directory.**
`--only {all,docs,examples}`, and `all` is the default. Each run gets its own working
directory inside the temp tree, so an example that writes a file leaves nothing behind. The
compile timeout is 60 s, as it is today, and the run timeout is 10 s. A timeout is a failure
with its own label. Output is NOT asserted: an example is documentation, and an
expected-output mechanism would make it a test.

**R26 — the corpus proves the plumbing, and nothing more.** The tree holds 35 attached doc
blocks and, before this phase, no `- Example:` with a fence at all. So the phase writes the
smallest set of examples that exercises every fence, and then stops. It does NOT put an
example on every documented declaration, and it does not document the stdlib.

Four fixture files in `tests/docs/examples/`. An earlier draft of this ruling said
three, on the strength of one measurement — that the contents of a fence never reach the
host build, because a fence is text inside one `DOC_BLOCK` token, so a file whose fences
hold deliberately broken Sushi still compiles clean. That is true, and it is not enough:
R21 makes every example in a unit that declares `main` a SKIP, and a `test_` file has to
declare one. One file cannot both exit 0 as a test and show the sweep four outcomes.

- **`fence_outcomes.sushi`** carries all four attributes and declares no `main`, so it is
  not a test and the collector can import it. This is where the four outcomes are
  asserted, by `tests/unit/test_doc_examples_runner.py` and by the sweep.
- **`test_doc_example_fences.sushi`** carries the same four spellings and declares `main`.
  It asserts the other half, in CI: four legal fence spellings raise no CE70xx, and the
  deliberately broken Sushi in its `error` fence never reaches the build. The sweep reports
  its four examples as skips, which is R21 working.
- **two `test_err_` files**, one for CE7007 and one for CE7008, because each has to exit 2
  on its own.

An example that cannot compile is a legitimate fixture and not a gap: `error` and `skip`
exist precisely for code that must not build, or must not run.

**Real examples are a later editorial pass.** The bundled stdlib modules are where they pay
off, and writing them is a review of four modules' prose rather than plumbing. R24 is the
prerequisite that pass needs, and it lands here so the pass can start whenever it is worth
starting.

**R27 — the Markdown collector honours CommonMark's own fence rule.** A fenced block is
closed by a fence of the SAME character that is at least as long, which is how the format
already lets one fence hold another. `collect_blocks` implements none of it. So when a line
opens a fence that this collector cannot close — a `~~~`, or four or more backticks — it
steps to that fence's own closer before it resumes scanning.

Measured both ways: with a `~~~sushi` outer fence AND with a four-backtick one, the
phase-3 collector reached inside an illustration and pulled the inner example out as a
block of its own. The `~~~` convention above is the spelling; this ruling is what makes it
safe.

---

## 11. Rejected alternatives

**Inside only, the Python model.** Rejected on coverage. Five declaration forms have no
body to sit inside — `const_def`, `struct_field`, `enum_variant`, `perk_method` and
`extern_decl` — and constants are among the first things anyone wants to document. This is
the same wall PEP 224 hit.

**A `##` line sigil, the Rust and Nim model.** The closest call in this document, and the
case for it is stronger than an earlier draft of this section allowed. A line cannot be
unterminated, so it has no runaway and CE6011, CE6012 and CE6013 all stop existing. It
renders correctly in any editor that highlights `#` as a line comment, where a delimited
block renders its interior as code. And the indentation objection is real but smaller than it
looked: the delimited form still measures the opening `##:` column (§4), so the difference is
one constrained line against all of them, not an exemption. Nor does Sushi need Rust's second
`//!` sigil to reach the enclosing-item position — the blank-line rule in §2 separates the
three positions, and it would do so for either form.

What decides it is the interior. A doc block holds a fenced code example (§10). Under a line
sigil every line of every example carries a `## ` that the doc-test runner has to strip and
that an author has to re-apply after each edit. The delimited form gives free-form prose and
free-form fences, against one added rule (§2) and a first line that has to line up.

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

**Re-deriving docs from the `.slib` source section.** Tempting, and more tempting than it
looks, because a source library is the default kind and does ship its whole source — so the
manifest key is knowingly a second copy (§8). Rejected anyway: `--lib-info` must never parse
source, and re-parsing would make the tool depend on a working compiler to answer a question
about a file.

---

## 12. Phases

**Phase 2 — the language.** The three terminals, the `_NEWLINE` narrowing, the seven rule
edits, the `DOC_BLOCK` peel in `parse_block`, `DocBlock` and `DocTag`, the doc parser and
the attachment function, the `docs` pass with its always-on checks, CE6011, CE6012, CE6013
and the CE70xx module. At the end of this phase the compiler understands doc blocks and
nothing consumes them. This phase also writes the complete documentation, about the doc
block. Later phases simply add what they added to the feature.

**Phase 3 — the library.** The `doc` key on the concrete manifest records, the generic and
perk serializers and the `unit_docs` map, and the plain dump in both `slib-info`
implementations. At the end of this phase a documented library tells you what it contains,
within the limits §8 records.

**Phase 4 — the examples.** `- Example:` parsing, the wrapping rule, and the second
collector in `docs_sweep.py`.

**Phase 5 — completeness.** `--warn-missing-docs` and its five lints, the `declarations()`
walk they read, and the `COMPILER_FLAGS:` test directive that lets a `.sushi` fixture turn
a compiler flag on. At the end of this phase the compiler answers both halves of the
question: what a block claims, and what it leaves out.

**Phase 6 — Markdown.** Rendering, a richer `slib-info`, and the user-facing guide. The
Markdown checker is written in Sushi and lives in `toolchain/`, which makes it the second
inhabitant after `slib-info` and another test of the language against a real problem.

### What each phase MUST do

Check the user documentation that it is still valid and add things that were done in
that phase if applicable. Move this section's own phase row, and the status banner at the
top, in the same commit — a phase that ships and still reads DESIGN is the one drift a
reader cannot detect.

Phases 2 to 5 shared one branch, `feat/doc-block`, and merged together. Phase 6 has its
own branch, `feat/doc-block-markdown`.

### What each phase must NOT do

Phase 2 must not add a fallback for an unparseable doc block. A block either parses or is
a diagnostic; a silent recovery path would reintroduce exactly the failure mode this
feature removes.

Phase 3 must not teach `slib-info` to parse source, must not move the container version for
an added optional key, and must not put a `doc` key on a private or closure-path record.

Phase 4 must not make the sweep a CI job, must not assert an example's output, and must not
teach the wrapper to reach a private symbol. Each one turns documentation into a test, and
the third one documents a call a reader cannot make.

Phase 5 must not turn any always-on check into a warning, or any warning into an
always-on error. The split in §6 is the contract: a claim that contradicts the declaration
is wrong today, and an absent claim is a matter of policy.
