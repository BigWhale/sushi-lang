"""The Python half of the `--lib-info` report: a `.slib` manifest, printed for a reader.

The Sushi tool `toolchain/src/slib_info.sushi` is the other half, and it owns the report
when it is built; this module is the fallback when it is not (every wheel install). The
two print byte-identical reports. The report's sections are the rows of `_SECTIONS`.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from sushi_lang.internals.styling import Palette, should_colour


class _Report:
    """What one `--lib-info` run was asked for: the doc blocks, and the palette.

    ONE value threaded through the renderer rather than one parameter per switch. A
    style is a STRING here and never a branch, so a painted line and a plain one are
    written once -- which is also what makes R43's constraint hold by construction:
    painting changes no text.
    """

    __slots__ = ("docs", "p")

    def __init__(self, docs: bool, colour: bool):
        self.docs = docs
        self.p = Palette(colour)


def _surface(type_str: str) -> str:
    """One manifest type string, in the surface `@(...)` spelling a reader is owed.

    The manifest carries the INTERNAL identity name, `List<i32>`, because a consumer
    reads it back through `parse_type_string`. Angle brackets are never user-visible
    text (`docs/design/type-identity.md`), so the report converts here and nowhere else.
    """
    from sushi_lang.semantics.generics.type_display import display_type_name
    return display_type_name(type_str)


def _render_params(params: list, self_mode: str | None = None) -> str:
    """One parameter list, as a signature reads it.

    `nom` is the one mode a TYPE cannot spell, so the record's own `mode` field is the
    only place it can come from. `peek` and `poke` ride on the type itself, so the type
    string already carries them and printing the mode again would double it. A perk
    method's receiver mode is the record's own field for the same reason, and it prints
    first, where the declaration wrote it: `(poke self, i32 width)`.
    """
    rendered = [f"{self_mode} self"] if self_mode else []
    for param in params:
        mode = "nom " if param.get("mode") == "nom" else ""
        rendered.append(f"{mode}{_surface(param['type'])} {param['name']}")
    return ", ".join(rendered)


def _render_type_params(records: list | None) -> str:
    """The `@(T: Perk, U)` suffix of a generic declaration, or "" when there is none."""
    if not records:
        return ""
    rendered = []
    for tp in records:
        constraints = tp.get('constraints') or []
        rendered.append(f"{tp['name']}: {' + '.join(constraints)}"
                        if constraints else tp['name'])
    return f"@({', '.join(rendered)})"


def _render_signature(func: dict, p: Palette) -> str:
    """One function's whole signature, concrete or generic.

    ONE renderer, so a template prints what a concrete function prints. A generic used
    to print `(template)` where its parameters belong, which is why its `- Parameter`
    tags were stored and never rendered. An extension method prints the same
    signature after its target, with no `fn`.
    """
    generic = _render_type_params(func.get('type_params'))
    params = _render_params(func.get('params') or [], func.get('self_mode'))
    name = f"{p.bold}{func['name']}{p.reset}"
    line = f"{name}{generic}({params}) {_surface(func['return_type'])}"
    # A record with no `error_type` is bare, or spells its Result in `return_type`, so it
    # prints no arm either.
    error = func.get('error_type')
    return f"{line} | {_surface(error)}" if error else line


def _reexport_target(record: dict) -> str:
    """A re-export record as the `public use` that produced it (#585).

    A unit target is quoted and the other two are angled, which is how each is
    written -- so the section reads back as the statements the author wrote.
    """
    path = record.get('path', '')
    return f'"{path}"' if record.get('kind') == 'unit' else f"<{path}>"


def _section(title: str, items: list, p: Palette) -> bool:
    """Open a section, or answer False when it holds nothing.

    A section with no members prints no header. Every section obeys it, which is why
    the guard is one function rather than one `if` per section -- and why the header's
    style is written once.
    """
    if not items:
        return False
    print(f"{p.bold}{title}{p.reset} {p.dim}({len(items)}){p.reset}:")
    return True


# The rendered Markdown subset, longest opener first: `**` has to be tried before `*`,
# or every bold run would read as two empty italics. A CLOSED set -- a link, a table, a
# heading and a nested list print as the author wrote them, which is what every construct
# outside the subset has always done.
_MARKS = (("**", "bold"), ("`", "cyan"), ("*", "italic"))


def _mark_at(line: str, i: int) -> tuple[str | None, str]:
    """The mark that opens at `i`, and the style it asks for."""
    for mark, style in _MARKS:
        if line.startswith(mark, i):
            return mark, style
    return None, ""


def _is_span(mark: str, inner: str, close: int) -> bool:
    """Is this a span, or is it punctuation the author meant literally?"""
    if close == -1 or not inner:
        return False
    return mark == "`" or not (inner[0].isspace() or inner[-1].isspace())


def _render_inline(line: str, p: Palette) -> str:
    """One line of prose with its Markdown marks turned into styles (R44).

    PLAIN mode returns the line untouched, which is R40: a captured report keeps the
    marks, so it loses no information and `` `spin_up` `` still reads as a symbol.

    A span that does not close prints verbatim, and so does an EMPTY one. An emphasis
    span must also hug its text -- `2 * 3 * 4` is arithmetic, not an italic ` 3 ` --
    which is CommonMark's flanking rule in the one form this subset needs. Inline code
    is exempt from it, because a code span may legitimately hold spaces.
    """
    if not p.reset:
        return line

    out: list[str] = []
    i, n = 0, len(line)
    while i < n:
        mark, style = _mark_at(line, i)
        if mark is None:
            out.append(line[i])
            i += 1
            continue
        close = line.find(mark, i + len(mark))
        inner = line[i + len(mark):close] if close != -1 else ""
        if _is_span(mark, inner, close):
            out.append(f"{getattr(p, style)}{inner}{p.reset}")
            i = close + len(mark)
        else:
            out.append(mark)
            i += len(mark)
    return "".join(out)


def _print_doc_lines(indent: str, text: str, opener: str = "",
                     opener_width: int | None = None,
                     p: Palette | None = None) -> None:
    """One line of output per line of `text`. A blank line prints EMPTY.

    `split` and not `splitlines`: the latter drops a trailing empty field and also
    breaks on `\r`, `\x0b`, `\x0c` and the Unicode separators, and the Sushi tool's
    `.split("\n")` does neither. The two must cut the same bytes.

    `opener` prefixes the FIRST line only, and every line after it is indented past the
    opener rather than under it -- the hanging indent a tag needs (R38 rule 3). The text
    is re-indented and never reflowed (R39): it breaks where the author wrote a newline,
    which is what keeps a fenced example intact.

    `opener_width` is the opener's VISIBLE width, which is not its length once it carries
    escapes: an alignment measured in bytes would indent a coloured continuation by the
    width of the escapes as well.
    """
    hang = indent + " " * (len(opener) if opener_width is None else opener_width)
    for i, line in enumerate(text.split("\n")):
        if not line:
            print()
            continue
        if p is not None:
            line = _render_inline(line, p)
        print(f"{indent}{opener}{line}" if i == 0 else f"{hang}{line}")


def _doc_tags(doc: dict, owner: dict | None) -> list[tuple[str, str, str]]:
    """Every tag of one record as (keyword, name, text), in the order R38 prints them.

    The keyword and the name are kept apart because they PAINT apart: the keyword is a
    label and the name is a symbol. `name` is empty for a tag that has none.

    Parameters come first and in DECLARATION order, read from the owner's own `params`
    array and looked up by name: a map's wire order is not the signature's order. A
    record with no `params` array -- a struct, a unit, a generic type -- yields none.
    """
    named = doc.get('params') or {}
    tags = [("Parameter", p['name'], named[p['name']])
            for p in ((owner or {}).get('params') or [])
            if named.get(p['name'])]
    tags += [(label, "", doc[key])
             for key, label in (('returns', 'Returns'), ('errors', 'Errors'))
             if doc.get(key)]
    return tags


def _tag_opener(keyword: str, name: str, p: Palette) -> tuple[str, int]:
    """One tag's `- Keyword name: ` opener, painted, and its VISIBLE width."""
    plain = f"- {keyword} {name}: " if name else f"- {keyword}: "
    label = f"{p.blue}{keyword}{p.reset}"
    if name:
        label += f" {p.cyan}{name}{p.reset}"
    return f"- {label}: ", len(plain)


def _print_doc_record(doc: dict | None, owner: dict | None, indent: str,
                      opts: '_Report') -> bool:
    """One doc record -- the summary, the body, then the tags -- and whether it printed.

    A blank line separates every part from the one above it: the body from the summary,
    the first tag from the prose (R38 rule 1), and each tag from the last (rule 4). One
    predicate does all three, because "something is already above me" is the whole
    condition.

    The ANSWER is what rule 2 needs: a caller prints a blank line before the next record
    when this one left a block behind, and prints nothing extra when it did not.

    THE gate for `--docs`: every doc record in the report comes through here, so the
    switch is read once rather than at each of the ten sections.
    """
    if not doc or not opts.docs:
        return False

    printed = False
    summary = doc.get('summary', '')
    body = doc.get('body', '')
    if summary:
        _print_doc_lines(indent, summary, p=opts.p)
        printed = True
    if body:
        if printed:
            print()
        _print_doc_lines(indent, body, p=opts.p)
        printed = True

    for keyword, name, text in _doc_tags(doc, owner):
        if printed:
            print()
        opener, width = _tag_opener(keyword, name, opts.p)
        _print_doc_lines(indent, text, opener, width, opts.p)
        printed = True

    # An example is the LAST thing a record says: a parameter is a contract and an
    # example is a demonstration. Its body is CODE, so it is indented rather than hung,
    # and dim rather than rendered -- a backtick inside a program is a program's
    # backtick.
    for example in doc.get('examples') or []:
        if printed:
            print()
        opener, width = _tag_opener("Example", "", opts.p)
        caption = example.get('caption') or ""
        if caption:
            # A caption is a tag's text like any other, so it hangs and it renders.
            _print_doc_lines(indent, caption, opener, width, opts.p)
        else:
            print(f"{indent}{opener}".rstrip())
        for line in example['code'].split("\n"):
            print(f"{indent}    {opts.p.dim}{line}{opts.p.reset}" if line else "")
        printed = True

    return printed


def _print_doc(owner: dict, indent: str, opts: '_Report') -> bool:
    """The doc record of one manifest entry, when it carries one."""
    return _print_doc_record(owner.get('doc'), owner, indent, opts)


class _Records:
    """One section's records, separated by rule 2.

    A blank line goes BEFORE a record whose predecessor printed a block, never after
    one: an after-rule would double with the blank line every section already prints
    when it closes. A run of bare signatures has no block to close, so it stays as dense
    as the plain report is.
    """

    def __init__(self) -> None:
        self._pending = False

    def open(self) -> None:
        """Announce the next record, closing the block above it if there was one."""
        if self._pending:
            print()
        self._pending = False

    def close(self, printed: bool) -> None:
        """Record whether this entry left a block behind."""
        self._pending = printed


def _render_impl_target(impl: dict) -> str:
    """An implementation's target: `Gadget`, or `Box@(T)` for a generic-target template.

    A template record carries the target's BASE name and its parameters as written,
    so the header is rebuilt here rather than parsed out of the source slice.
    """
    target = _surface(impl['type'])
    type_args = impl.get('type_args') or []
    return f"{target}@({', '.join(type_args)})" if type_args else target


def _named_suffix(record: dict) -> str:
    """The `@(T, U)` of a concrete struct or enum record, which is always empty today."""
    if record.get('is_generic') and record.get('type_params'):
        return f"@({', '.join(record['type_params'])})"
    return ""


# One line per record kind: the table below names them, and the key scan in
# `tests/unit/test_slib_manifest_keys_agree.py` reads each by name.
def _unit_line(unit: str, p: Palette) -> str:
    return f"  {unit}"


def _reexport_line(record: dict, p: Palette) -> str:
    return f"  {record['unit']}: public use {_reexport_target(record)}"


def _function_line(func: dict, p: Palette) -> str:
    return f"  fn {_render_signature(func, p)}"


def _constant_line(const: dict, p: Palette) -> str:
    return f"  const {_surface(const['type'])} {const['name']}"


def _variable_line(var: dict, p: Palette) -> str:
    return f"  var {_surface(var['type'])} {var['name']}"


def _struct_line(struct: dict, p: Palette) -> str:
    return f"  struct {struct['name']}{_named_suffix(struct)}:"


def _field_line(field: dict, p: Palette) -> str:
    return f"    {_surface(field['type'])} {field['name']}"


def _enum_line(enum: dict, p: Palette) -> str:
    return f"  enum {enum['name']}{_named_suffix(enum)}:"


def _variant_line(variant: dict, p: Palette) -> str:
    if variant.get('has_data'):
        payload = ", ".join(_surface(t) for t in variant['data_types'])
        return f"    {variant['name']}({payload})"
    return f"    {variant['name']}"


def _perk_line(perk: dict, p: Palette) -> str:
    return f"  perk {perk['name']}:"


def _impl_line(impl: dict, p: Palette) -> str:
    return f"  extend {_render_impl_target(impl)} with {impl['perk']}:"


def _method_line(method: dict, p: Palette) -> str:
    return f"    fn {_render_signature(method, p)}"


def _extension_line(ext: dict, p: Palette) -> str:
    static = "static " if ext.get('static') else ""
    return f"  extend {_surface(ext['type'])} {static}{_render_signature(ext, p)}"


def _foreign_line(claim: dict, p: Palette) -> str:
    return f"  extend {_surface(claim['type'])} {claim['method']}"


def _dependency_line(dep: dict, p: Palette) -> str:
    """A module, or a library with the name and the version that the build found."""
    line = f"  <{dep['path']}>"
    if dep.get('kind') == 'library':
        line += f" ({dep.get('library_name') or '?'} {dep.get('library_version') or '?'})"
    return line


def _own_doc(record: dict, metadata: dict) -> tuple[Optional[dict], Optional[dict]]:
    """A record's own doc block, with the record as the owner of its parameter tags."""
    return record.get('doc'), record


def _unit_doc(unit: str, metadata: dict) -> tuple[Optional[dict], Optional[dict]]:
    """A unit's doc block, kept in `unit_docs` beside the ordered `units` list."""
    return (metadata.get('unit_docs') or {}).get(unit), None


def _generic_named(keyword: str) -> Callable[[dict, Palette], str]:
    """The header of a generic struct or a generic enum: the two records share a shape.

    A generic struct's FIELD blocks are not in the index at all (documentation.md S8,
    R3), so neither record has members to print.
    """
    return lambda record, p: (
        f"  {keyword} {record['name']}{_render_type_params(record.get('type_params'))}:")


@dataclass(frozen=True)
class _Section:
    """One section of the report: where its records are, and how one record prints.

    `keys` are read from the manifest, or from its `templates` map when `in_templates`,
    and joined in order. `doc` is None for a section whose records carry no doc block.
    `members` is a record's own list (fields, variants, methods) and how one member
    prints; each member is a record of its own for rule 2's spacing, with its block one
    step deeper.
    """
    title: str
    keys: tuple[str, ...]
    line: Callable[[Any, Palette], str]
    in_templates: bool = False
    doc: Optional[Callable[[Any, dict], tuple[Optional[dict], Optional[dict]]]] = _own_doc
    members: Optional[tuple[str, Callable[[dict, Palette], str]]] = None


_SECTIONS: tuple[_Section, ...] = (
    _Section("Units", ("units",), _unit_line, doc=_unit_doc),
    _Section("Re-exports", ("reexports",), _reexport_line, doc=None),
    _Section("Public Functions", ("public_functions",), _function_line),
    _Section("Generic Functions", ("generic_functions",), _function_line, in_templates=True),
    _Section("Public Constants", ("public_constants",), _constant_line),
    _Section("Public Variables", ("public_variables",), _variable_line),
    _Section("Public Structs", ("structs",), _struct_line, members=("fields", _field_line)),
    # A generic struct beside its concrete twin, not filed away with the other
    # templates: a reader looking for `Box` wants it near `Point`.
    _Section("Generic Structs", ("generic_structs",), _generic_named("struct"),
             in_templates=True),
    _Section("Public Enums", ("enums",), _enum_line, members=("variants", _variant_line)),
    _Section("Generic Enums", ("generic_enums",), _generic_named("enum"), in_templates=True),
    # Every public perk ships, plus any perk an exported template names (#543), so this
    # section is the contracts a consumer can be asked to satisfy -- each with the
    # method signatures that satisfying it means (#537). ONE method printer serves the
    # contract and its implementations.
    _Section("Perks", ("perks",), _perk_line, in_templates=True,
             members=("methods", _method_line)),
    # Which types satisfy which contract: the concrete implementations, then the
    # generic-target templates, in ONE section -- a reader asking "who implements Show"
    # wants one list, and `extend Box@(T) with Show` answers for every `Box` (#537).
    _Section("Perk Implementations", ("perk_impls", "generic_perk_impls"), _impl_line,
             in_templates=True, members=("methods", _method_line)),
    # The methods a consumer can call on a type, the concrete ones and then the
    # templates, in ONE section, as the perk implementations above.
    _Section("Extension Methods", ("extensions", "generic_extensions"), _extension_line,
             in_templates=True),
    _Section("Foreign Extensions", ("foreign_extensions",), _foreign_line, doc=None),
    _Section("Dependencies", ("dependencies",), _dependency_line, doc=None),
)


def _print_section(section: _Section, metadata: dict, opts: _Report) -> None:
    """One section: the header, each record and its members, and the closing blank line."""
    source = (metadata.get('templates') or {}) if section.in_templates else metadata
    items = [record for key in section.keys for record in (source.get(key) or [])]
    if not _section(section.title, items, opts.p):
        return
    records = _Records()
    for record in items:
        records.open()
        print(section.line(record, opts.p))
        if section.doc is not None:
            doc, owner = section.doc(record, metadata)
            records.close(_print_doc_record(doc, owner, "    ", opts))
        # A member is a record too, and the owner's own block is the record above the
        # first one -- so one tracker covers the owner and its members alike.
        if section.members is None:
            continue
        key, member_line = section.members
        for member in record.get(key) or []:
            records.open()
            print(member_line(member, opts.p))
            records.close(_print_doc(member, "      ", opts))
    print()


def print_library_info(library_path: Path, show_docs: bool = False,
                       color: str = "auto") -> int:
    """Print formatted metadata from a .slib library file. A fault raises its code."""
    opts = _Report(show_docs, should_colour(sys.stdout, color))
    from sushi_lang.backend.library_format import LibraryFormat, check_manifest
    from sushi_lang.backend.library_errors import LibraryError

    if library_path.suffix != '.slib':
        raise LibraryError("CE3516", path=str(library_path))
    metadata, source_size, bitcode_size = LibraryFormat.read_section_sizes(library_path)
    check_manifest(metadata, str(library_path))

    # A field the kind makes meaningless is not printed: a source library runs
    # everywhere, so it has no platform, and it carries no bitcode to measure.
    kind = metadata.get('kind', '')
    requires = metadata.get('requires_compiler', '')

    print(f"Library: {metadata['library_name']}")
    print(f"Version: {metadata['library_version']}")
    print(f"Kind: {kind}")
    if kind != 'source':
        print(f"Platform: {metadata['platform']}")
    print(f"Compiler: {metadata['compiler_version']}")
    if requires:
        print(f"Requires compiler: {requires}")
    print(f"Compiled: {metadata['compiled_at']}")
    print(f"Protocol: {metadata['sushi_lib_version']}")
    print()

    for section in _SECTIONS:
        _print_section(section, metadata, opts)

    if kind != 'binary':
        print(f"Source: {opts.p.dim}{source_size:,}{opts.p.reset} bytes")
    if kind != 'source':
        print(f"Bitcode: {opts.p.dim}{bitcode_size:,}{opts.p.reset} bytes")

    return 0
