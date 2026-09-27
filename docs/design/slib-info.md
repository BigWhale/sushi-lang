# slib-info: design records

The `slib-info` tool prints the metadata report of a `.slib` library. Its source is
`toolchain/src/slib_info.sushi`, and `./toolchain/build.py` builds it into `toolchain/bin/`.
`sushic --lib-info` runs the built tool, and uses the Python fallback (`print_library_info`
in `sushi_lang/compiler/lib_info.py`) when there is no tool. The delegation contract is in
`toolchain/README.md`. The report and its rules (R38 to R49) are in
`docs/design/documentation.md`.

This page holds the design records that were `#` comments in the tool source. The doc
blocks in the source say what each declaration does. This page says why.

## 1. The two halves print one report

On the success path, the report of the tool is the same as the report of the Python
fallback, line for line. Exit code 0 is a report or a `--help` answer. Exit code 2 is a
usage error or a read error. An error message can be different between the two halves. The
success report cannot.

The I/O error words (`io_cause`) are the words that the Python fallback gives for each
errno, so both halves print the same line.

## 2. The palette

The tool uses the sixteen-colour palette and nothing wider. The report has six kinds of
text, so 256 colours or truecolor give nothing, and they cost a read of `COLORTERM`. Each
terminal that shows colour shows these. The Python side has the same escapes in
`sushi_lang/internals/styling.py`.

## 3. The styles are strings

`Opts` holds the six style strings of one run, or six empty strings. Each printer takes
`Opts` as a borrow. A printer writes a style string, and never makes a decision on colour
at the call site. Thus a coloured line and a plain line are written once. This also makes
the constraint of R43 true by construction: remove the escapes from a coloured report, and
you get the plain report.

A constant is unit-level storage and is never moved out of (CE2436). Thus `make_opts`
clones each escape constant into the struct.

## 4. The manifest is read in place

A function cannot return a borrow, so no reader gives a subtree back. A printer finds a key
with `map_index`, and gives `vals[at]` to the next printer as a borrow. Only a leaf string
is copied. An absent key prints as a `Nil` value prints.

`ml_get_str` gives `""` for an absent key and for an empty string. Thus the tool tests a
doc record for `Nil` (`ml_is_nil`) before it reads the record.

## 5. The surface spelling of a type

The manifest holds an interned identity name, for example `List<i32>`, because a consumer
parses that string back. That spelling is a wire format. Angle brackets are never text that
a user sees (R45). `to_surface` changes the name to the `@(...)` spelling. Its four rules
are the four rules of `display_type_name` on the Python side: a name with no `<` stays; a
function type (`->`) stays; a name whose `<` and `>` do not balance stays; else each `<`
becomes `@(` and each `>` becomes `)`.

## 6. The rendered Markdown subset

`render_inline` changes the Markdown marks of one line of prose into styles (R44). The
subset is closed: inline code, bold and italic. A link, a table, a heading and a nested list
print as the author wrote them.

In plain mode the line does not change (R40). A captured report keeps the marks, so it loses
no information, and `` `spin_up` `` still reads as a symbol.

The rules of a span:

- The longest opener goes first. `**` is tried before `*`, or each bold run reads as two
  empty italics.
- A mark that does not close is punctuation. An empty span is punctuation.
- An emphasis span must touch its text on both sides, so `2 * 3 * 4` is arithmetic and not
  an italic ` 3 `. This is the flanking rule of CommonMark, in the one form that the subset
  needs. Inline code does not have this rule, because a code span can hold spaces.

Rendering is per line, so a span never goes across a newline.

## 7. Lines, openers and the hanging indent

`print_lines` prints each line of a text with its indent. A blank line prints empty, so the
report has no trailing whitespace. `.split("\n")` agrees with the Python `str.split("\n")`
on each edge case, which the parity of the two halves needs.

The opener (for example `- Returns: `) comes before the first line only. Each line after it
is indented past the opener, and not under it: the hanging indent that a tag needs (R38
rule 3). The text is indented again and never reflowed (R39). It breaks where the author
wrote a newline, so a fenced example stays correct.

The width of the opener is its visible width. With colour, that is not its length in
bytes: an alignment in bytes would also indent a coloured continuation by the width of the
escapes. Thus each caller gives the width of the plain opener.

## 8. The layout of a doc record

`print_doc_record` prints the summary, the body, then the tags, and tells if it printed
something.

A blank line separates each part from the part above it: the body from the summary, the
first tag from the prose (R38 rule 1), and each tag from the tag before it (rule 4). One
condition does all three: "a part is already above this one".

The answer of the function is what rule 2 needs. A caller prints a blank line before the
next record when this record printed a block, and nothing when it did not.

Parameters print in the order of the declaration. The printer reads the `params` array of
the owner, and finds each text by name: the wire order of a map is not the order of the
signature. An owner with no `params` array (a unit, a struct, a generic type) prints no
parameter line.

An example is the last part of a record (R48): a parameter is a contract, and an example is
a demonstration. Its body is code, so it is indented and not hung, and it is dim and not
rendered. A backtick in a program belongs to the program. A caption is the text of a tag,
so it hangs and it renders.

## 9. The blank line before a record

`open_record` puts the blank line of rule 2 before a record whose predecessor printed a
block, and never after a record. A rule after the record would give two blank lines with
the blank line that each section prints at its end. A run of signatures with no blocks
has no block to close, so it stays as dense as the plain report. The answer of
`open_record` is the new `pending` value of the caller.

A field and a variant are records too. The block of the struct or the enum is the record
above the first member, so one `pending` flag covers the owner and its members.

## 10. One printer for each kind of record

- **A section** with no members prints no header. `section` is the one function that
  prints a header, so each section obeys the rule, and the style of the header is written
  once.
- **A signature.** `render_signature` is the one renderer for a concrete and a generic
  function, and for a perk method. A generic function printed `(template)` where its
  parameters go, before this renderer, and thus its `- Parameter` tags were stored and
  never printed (R46). The default error type is `StdError`, and a signature that takes it
  does not say so. Thus a record with no `error_type` prints no error arm (R49).
- **A parameter mode.** `nom` is the one mode that a type cannot spell, so it comes from the
  `mode` field of the record. `peek` and `poke` are already part of the type string. The
  receiver mode of a perk method is a field of the record too, and it prints first, where
  the declaration wrote it: `(poke self, i32 width)`.
- **A binding.** A constant and a variable have the same record shape, and differ only by
  the keyword.
- **A generic struct and a generic enum** have the same record shape: a name, its type
  parameters and its block. They share a printer and differ by the keyword. The field
  blocks of a generic struct are not in the index (`docs/design/documentation.md`
  section 8), so the record has no members to print.
- **A method** of a perk or of an implementation. One printer for the contract and its
  implementations, so a method prints the same way in each position. Each method is a
  record of its own, for the spacing of rule 2.
- **Perks.** The section lists the contracts that a consumer can be asked to satisfy: each
  public perk, and each perk that an exported template names.
- **Perk implementations.** The concrete implementations, then the generic-target
  templates, in one section: a reader who asks "what implements `Show`" wants one list, and
  `extend Box@(T) with Show` answers for each `Box`. The record of a generic-target
  template holds the base name and the parameters as written, so the tool builds the header
  again, and never parses it from the source slice. `perk` is a reserved word, so the tool
  cannot bind the name of the contract to a variable called `perk`.
- **Foreign extensions** are the types that the library adds methods to and does not
  declare: the consumer half of CW3003. A library that extends only what it declares has
  no key, and the section prints nothing.
- **Re-exports.** One record for each `public use`, printed as the statement that made it:
  a unit target is in quotes, and a stdlib or library target is in angle brackets. A library
  that re-exports nothing has no key, and the section prints nothing.

## 11. The payload sizes

The report ends with the size of each payload section that the kind of the library has. A
field that the kind makes meaningless does not print: a source library has no platform and
no bitcode, and a binary library has no source. `group_thousands` writes a size with comma
separators, as the Python `{n:,}` does.

## 12. The command line and the colour decision

`run_tool` reads the command line in one pass. A flag is a flag in each position, and the
one bare word is the path. `--help` is answered when the tool sees it, so it wins over each
other word. The help goes to stdout with exit code 0, because help that the user asked for
is an answer and not an error. `USAGE` is the one spelling of the summary line, for
`--help` and for each usage error: two spellings drift when a person edits one of them.

`want_colour` is the one colour decision, in the order of R41: `--color`, then `NO_COLOR`,
then `CLICOLOR_FORCE`, then `TERM=dumb`, then whether stdout is a terminal. These are the
same five rungs as `should_colour` in `sushi_lang/internals/styling.py`. `NO_COLOR` and
`CLICOLOR_FORCE` answer to their presence (the rule of no-color.org), so an empty value is
a value and not an absence. Thus `env_is_set` and `env_value` are two functions.
