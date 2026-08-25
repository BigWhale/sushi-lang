# Design: Documentation blocks

**Status: the language understands doc blocks, and a library carries them.** Each
phase below moves one part from DESIGN to BUILT; this banner records where the line is.
The user-facing reference for what is built is `docs/documentation-blocks.md`.

| Phase | Content | State |
|---|---|---|
| 1 | This document | BUILT |
| 2 | Grammar, AST, attachment rules, the `docs` pass, CE6011/CE6012/CE6013 and CE70xx | BUILT |
| 3 | `.slib` manifest carriage; `slib-info` prints a plain dump | BUILT |
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

| Condition | Kind |
|---|---|
| a public symbol with no doc block | warning |
| a documented function with an undocumented parameter | warning |
| a non-`~` function with no `- Returns:` | warning |
| a function declaring `\| E` with no `- Errors:` | warning |

The flag is a long `--flag`, matching every flag in `sushi_lang/compiler/cli.py` but `-o`.
A `-W` tier system was considered and set aside: it is a whole CLI surface to design, and
it would have to decide which existing `CWxxxx` warnings move behind a tier. That is a
separate piece of work, and this feature does not need it.

One flag is still more than it looks. This would be the compiler's **first** warning-control
flag. `cli.py` has no `warn` in it at all, `Reporter.warn()` records unconditionally, and
`SemanticAnalyzer` takes no options object to thread a flag through. The nearest precedent is
CW5001, silenced by writing `because "..."` on the declaration — a source opt-out, not a CLI
gate. Phase 5 owns that plumbing, and it is why phase 5 is a phase of its own.

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

The metadata blob is deliberately never compressed: it is the index, and every reader must be
able to take it cheaply. Committed fixtures run 1-9 KB today, so prose for every public symbol
is plausibly the same order as the entire existing index — in the one section that is always
read in full and, for the default source kind, duplicating the source blob.

Three things hold that in hand, in order. The key is absent when there is no doc block, so an
undocumented library pays nothing. The record stores the parsed fields and not the raw block.
And if it ever does bite, one of the two reasons `libraries.md` gives for not compressing has
expired — `compression/zlib.sushi` is in the stdlib now, so a Sushi-side inflate is no longer
missing, and `FLAGS` bit 0 is already claimed. That is a `libraries.md` decision and not this
feature's to take.

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
section, once in the index. This section accepted that duplication and those are its price.

**R8 takes the decision.** Nothing here is revisited without a new measurement.

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

A new `semantics/library_docs.py` was the alternative. Rejected: the backend manifest
generator already imports this module, one function does not earn a file, and a new path
named here has to exist in the same commit (`tests/unit/test_path_references_exist.py` reads
a path reference as a promise).

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

**R8 — the size is accepted. No compression.** A documented library pays for its docs, and
an undocumented one pays nothing because the key is absent. The numbers are in "Measured, at
phase 3" above. Compressing the metadata blob is a `docs/design/libraries.md` decision and
this feature does not take it.

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

Phase 3 is a plain dump. No pagination, no colour, no wrapping. Colour and structure are
phase 6.

Docs are indented two spaces under the signature they belong to, inside the existing
sections:

```
Public Functions (1):
  fn hyperspace_jump(i32 a, u8 b) i32
    Jumps through hyperspace.

    The drive needs a warm coil.
    - Parameter a: The incoming argument.
    - Parameter b: The second one.
    - Returns: The jump distance in parsecs.
    - Errors: When the drive is cold, this returns `JumpError.NotReady`.
```

One blank line between the summary and the body, and none before the tags. R6 carries the
whole rule, including what a blank line inside a body prints as.

A symbol with no docs renders exactly as it does today, with no blank line and no
placeholder. This matches the existing convention, where an empty section is suppressed
rather than printed empty.

The two implementations need three helpers each, and phase 3 wrote them under these names:

- `ml_is_nil(MsgValue) -> bool`, because `ml_get_str` cannot tell an absent key from an
  empty string -- both give `""`. A doc record is read with `ml_get` and tested for `Nil`.
- `print_lines(indent, text)` -- `text.split("\n")`, one `println` per line, an empty line
  printed empty. Python must use `str.split("\n")` and **not** `splitlines()`: the latter
  drops the trailing empty field and also breaks on `\r`, `\x0b` and `\x0c`. Measured
  against `.split("\n")` on `"a\nb"`, `"a\n"`, `"\na"`, `"a\n\nb"`, `"a"` and `""`, the
  two agree on every one.
- `print_doc_record(doc, owner, indent)` -- the whole record in R6's order. It reads the
  owner's own `params` array for the order and looks each name up in `doc.params`.

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

---

## 10. Doc tests

Phase 4. A fenced code block under `- Example:` is compiled and run.

### Wrapping

A snippet with no `fn main(` is wrapped, the way rustdoc wraps one. Written by the author
as two lines of intent:

    - Example:
    ```sushi
    let i32 d = hyperspace_jump(3, 7)??
    println("{d}")
    ```

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
`CExxxx`, skip, and fail as the residual — the temp-directory handling, the thread pool, and
`NO_COLOR=1` so stderr matching is robust. It stays a by-hand tool and deliberately not a CI job, which is the
ruling that shaped it.

Two things do not carry over. Its candidate filter requires both `fn main(` and `return`
to be present in a block, to tell a runnable example from a quoted signature. Wrapping
makes that test wrong for doc snippets, so the new collector needs its own rule: a doc
example is runnable unless it is marked otherwise. And its skip and expected-error markers
are HTML comments, which a `.sushi` file cannot carry — the doc-block collector needs a
marker that is legal inside a doc block.

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

**Phase 5 — completeness.** `--warn-missing-docs` and its four lints.

**Phase 6 — Markdown.** Rendering, a richer `slib-info`, and the user-facing guide. The
Markdown checker is written in Sushi and lives in `toolchain/`, which makes it the second
inhabitant after `slib-info` and another test of the language against a real problem.

### What each phase MUST do

Check the user documentation that it is still valid and add things that were done in
that phase if applicable.

Stay on the same branch: feat/doc-block, commit the work it was completed in that phase.

### What each phase must NOT do

Phase 2 must not add a fallback for an unparseable doc block. A block either parses or is
a diagnostic; a silent recovery path would reintroduce exactly the failure mode this
feature removes.

Phase 3 must not teach `slib-info` to parse source, must not move the container version for
an added optional key, and must not put a `doc` key on a private or closure-path record.

Phase 5 must not turn any always-on check into a warning, or any warning into an
always-on error. The split in §6 is the contract: a claim that contradicts the declaration
is wrong today, and an absent claim is a matter of policy.
