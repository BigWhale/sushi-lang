"""CLI entry point and argument parsing."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sushi_lang.internals.diagnostics import (
    InternalCompilerError,
    StdlibBuildError,
    SushiError,
)
from sushi_lang.internals.report import Reporter
from sushi_lang.internals.styling import COLOUR_CHOICES, Palette, set_colour_override, should_colour
from sushi_lang.internals.version import print_banner


def _find_toolchain_tool(name: str) -> Optional[Path]:
    """Locate a built toolchain binary (repo checkouts only; a wheel has none).

    SUSHI_TOOLCHAIN=off (or 0) skips the tools; SUSHI_TOOLCHAIN_BIN overrides
    the toolchain/bin/ directory.
    """
    import os

    if os.environ.get("SUSHI_TOOLCHAIN", "").lower() in ("0", "off"):
        return None
    override = os.environ.get("SUSHI_TOOLCHAIN_BIN")
    if override:
        bin_dir = Path(override)
    else:
        import sushi_lang
        bin_dir = Path(sushi_lang.__file__).resolve().parent.parent / "toolchain" / "bin"
    tool = bin_dir / name
    if tool.is_file() and os.access(tool, os.X_OK):
        return tool
    return None


def library_info_command(library_path: Path, show_docs: bool = False,
                         color: str = "auto") -> int:
    """--lib-info: run the toolchain slib-info tool, or the Python fallback.

    The tool owns the report and its exit code propagates. Only a failure to
    execute the binary at all falls back to print_library_info.

    `--docs` and `--color` are spelled the same at both ends, so a switch travels as
    itself rather than being translated into a name only one side knows.
    """
    from sushi_lang.compiler.loader import get_effective_cwd

    if not library_path.is_absolute():
        library_path = get_effective_cwd() / library_path

    tool = _find_toolchain_tool("slib-info")
    if tool is not None:
        import subprocess
        cmd = [str(tool)]
        if show_docs:
            cmd.append("--docs")
        # `auto` is the default and says nothing, so it is not worth a word on the line.
        # The tool reads the same environment variables this process did.
        if color != "auto":
            cmd.append(f"--color={color}")
        cmd.append(str(library_path))
        try:
            return subprocess.run(cmd).returncode
        except OSError:
            pass
    return print_library_info(library_path, show_docs, color)


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
        rendered.append(f"{tp['name']}: {', '.join(constraints)}"
                        if constraints else tp['name'])
    return f"@({', '.join(rendered)})"


def _render_signature(func: dict, p: Palette) -> str:
    """One function's whole signature, concrete or generic.

    ONE renderer, so a template prints what a concrete function prints. A generic used
    to print `(template)` where its parameters belong, which is why its `- Parameter`
    tags were stored and never rendered.
    """
    generic = _render_type_params(func.get('type_params'))
    params = _render_params(func.get('params') or [], func.get('self_mode'))
    name = f"{p.bold}{func['name']}{p.reset}"
    line = f"fn {name}{generic}({params}) {_surface(func['return_type'])}"
    # The default error type is StdError and a signature that takes it does not say so,
    # so a record with no `error_type` prints no arm either.
    error = func.get('error_type')
    return f"{line} | {_surface(error)}" if error else line


def _print_generic_named(templates: dict, key: str, title: str, keyword: str,
                         opts: '_Report') -> None:
    """One section of generic structs, or one of generic enums.

    The two records have the same shape -- a name, its type parameters and its own
    block -- so they share a printer and differ by the keyword. A generic struct's FIELD
    blocks are not in the index at all (documentation.md S8, R3), so neither record has
    members to print.
    """
    items = templates.get(key) or []
    if not _section(title, items, opts.p):
        return
    records = _Records()
    for record in items:
        records.open()
        generic = _render_type_params(record.get('type_params'))
        print(f"  {keyword} {record['name']}{generic}:")
        records.close(_print_doc(record, "    ", opts))
    print()


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


def _print_methods(owner: dict, records: '_Records', opts: '_Report') -> None:
    """The method records of a perk or an implementation, one signature per line.

    ONE printer for the contract and its implementations, so a method prints the same
    way wherever it stands; each method is a record of its own for rule 2's spacing.
    """
    for method in owner.get('methods') or []:
        records.open()
        print(f"    {_render_signature(method, opts.p)}")
        records.close(_print_doc(method, "      ", opts))


def print_library_info(library_path: Path, show_docs: bool = False,
                       color: str = "auto") -> int:
    """Print formatted metadata from a .slib library file."""
    opts = _Report(show_docs, should_colour(sys.stdout, color))
    from sushi_lang.backend.library_format import LibraryFormat
    from sushi_lang.backend.library_errors import LibraryError

    if not library_path.exists():
        print(f"Error: file not found: {library_path}", file=sys.stderr)
        return 2

    if not library_path.suffix == '.slib':
        print(f"Error: expected .slib file, got: {library_path}", file=sys.stderr)
        return 2

    try:
        metadata, source_size, bitcode_size = LibraryFormat.read_section_sizes(library_path)
    except LibraryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

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

    units = metadata.get('units', [])
    if _section("Units", units, opts.p):
        unit_docs = metadata.get('unit_docs') or {}
        records = _Records()
        for unit in units:
            records.open()
            print(f"  {unit}")
            records.close(
                _print_doc_record(unit_docs.get(unit), None, "    ", opts))
        print()

    templates = metadata.get('templates') or {}

    funcs = metadata.get('public_functions', [])
    if _section("Public Functions", funcs, opts.p):
        records = _Records()
        for func in funcs:
            records.open()
            print(f"  {_render_signature(func, opts.p)}")
            records.close(_print_doc(func, "    ", opts))
        print()

    generic_funcs = templates.get('generic_functions', [])
    if _section("Generic Functions", generic_funcs, opts.p):
        records = _Records()
        for gf in generic_funcs:
            records.open()
            print(f"  {_render_signature(gf, opts.p)}")
            records.close(_print_doc(gf, "    ", opts))
        print()

    consts = metadata.get('public_constants', [])
    if _section("Public Constants", consts, opts.p):
        records = _Records()
        for const in consts:
            records.open()
            print(f"  const {_surface(const['type'])} {const['name']}")
            records.close(_print_doc(const, "    ", opts))
        print()

    variables = metadata.get('public_variables', [])
    if _section("Public Variables", variables, opts.p):
        records = _Records()
        for var in variables:
            records.open()
            print(f"  var {_surface(var['type'])} {var['name']}")
            records.close(_print_doc(var, "    ", opts))
        print()

    structs = metadata.get('structs', [])
    if _section("Public Structs", structs, opts.p):
        records = _Records()
        for struct in structs:
            records.open()
            generic = ""
            if struct.get('is_generic') and struct.get('type_params'):
                generic = f"@({', '.join(struct['type_params'])})"
            print(f"  struct {struct['name']}{generic}:")
            # A FIELD is a record too, and the struct's own block is the record above
            # the first one -- so one tracker covers the struct and its members alike.
            records.close(_print_doc(struct, "    ", opts))
            for field in struct['fields']:
                records.open()
                print(f"    {_surface(field['type'])} {field['name']}")
                records.close(_print_doc(field, "      ", opts))
        print()

    # A generic struct beside its concrete twin, not filed away with the other
    # templates: a reader looking for `Box` wants it near `Point`.
    _print_generic_named(templates, 'generic_structs', "Generic Structs", "struct",
                         opts)

    enums = metadata.get('enums', [])
    if _section("Public Enums", enums, opts.p):
        records = _Records()
        for enum in enums:
            records.open()
            generic = ""
            if enum.get('is_generic') and enum.get('type_params'):
                generic = f"@({', '.join(enum['type_params'])})"
            print(f"  enum {enum['name']}{generic}:")
            records.close(_print_doc(enum, "    ", opts))
            for variant in enum['variants']:
                records.open()
                if variant.get('has_data'):
                    print(f"    {variant['name']}({_surface(variant['data_type'])})")
                else:
                    print(f"    {variant['name']}")
                records.close(_print_doc(variant, "      ", opts))
        print()

    _print_generic_named(templates, 'generic_enums', "Generic Enums", "enum", opts)

    # Every public perk ships, plus any perk an exported template names (#543), so this
    # section is the contracts a consumer can be asked to satisfy -- each with the
    # method signatures that satisfying it means (#537).
    perks = templates.get('perks', [])
    if _section("Perks", perks, opts.p):
        records = _Records()
        for perk in perks:
            records.open()
            print(f"  perk {perk['name']}:")
            records.close(_print_doc(perk, "    ", opts))
            _print_methods(perk, records, opts)
        print()

    # Which types satisfy which contract: the concrete implementations, then the
    # generic-target templates, in ONE section -- a reader asking "who implements Show"
    # wants one list, and `extend Box@(T) with Show` answers for every `Box` (#537).
    impls = (templates.get('perk_impls') or []) + (templates.get('generic_perk_impls') or [])
    if _section("Perk Implementations", impls, opts.p):
        records = _Records()
        for impl in impls:
            records.open()
            print(f"  extend {_render_impl_target(impl)} with {impl['perk']}:")
            records.close(_print_doc(impl, "    ", opts))
            _print_methods(impl, records, opts)
        print()

    foreign = metadata.get('foreign_extensions', [])
    if _section("Foreign Extensions", foreign, opts.p):
        for claim in foreign:
            print(f"  extend {_surface(claim['type'])} {claim['method']}")
        print()

    deps = metadata.get('dependencies', [])
    if _section("Dependencies", deps, opts.p):
        for dep in deps:
            print(f"  <{dep}>")
        print()

    if kind != 'binary':
        print(f"Source: {opts.p.dim}{source_size:,}{opts.p.reset} bytes")
    if kind != 'source':
        print(f"Bitcode: {opts.p.dim}{bitcode_size:,}{opts.p.reset} bytes")

    return 0


@dataclass
class Session:
    """Everything the top-level guard needs to render whatever went wrong."""
    args: argparse.Namespace
    reporter: Reporter = field(default_factory=Reporter)
    src_path: Optional[Path] = None
    crash: Optional[BaseException] = None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="compiler", description="Language compiler")

    ap.add_argument("source", nargs='?', help="Path to source file (.sushi)")
    ap.add_argument("--version", action="store_true", help="Show version and exit")
    ap.add_argument("--dump-parse", action="store_true", help="Print raw Lark tree")
    ap.add_argument("--dump-ast", action="store_true", help="Print AST")
    ap.add_argument("-o", "--out", metavar="OUT",
                    help="Output binary path (default: source filename without extension)")
    ap.add_argument("--write-ll", action="store_true",
                    help="Write LLVM IR to <OUT>.ll file")
    ap.add_argument("--dump-ll", action="store_true",
                    help="Dump generated LLVM IR to terminal")
    ap.add_argument(
        "--opt",
        choices=["none", "mem2reg", "O1", "O2", "O3"],
        default="mem2reg",
        help="Optimization level. 'mem2reg' promotes locals to SSA without a full pipeline.",
    )
    ap.add_argument(
        "--no-verify",
        action="store_true",
        help="Disable LLVM IR verification (pre/post optimization).",
    )
    ap.add_argument(
        "--keep-object",
        action="store_true",
        help="Keep the generated .o file after linking",
    )
    ap.add_argument(
        "--traceback",
        action="store_true",
        help="Print full traceback on backend errors (for debugging)",
    )
    ap.add_argument(
        "--build-stdlib",
        action="store_true",
        help="Rebuild standard library from source",
    )
    ap.add_argument(
        "--lib",
        action="store_true",
        help="Compile to library bitcode (no main() required)",
    )
    ap.add_argument(
        "--lib-kind",
        choices=["source", "binary", "hybrid"],
        default="source",
        help="How the library ships: source text, compiled bitcode, or both",
    )
    ap.add_argument(
        "--lib-version",
        metavar="X.Y.Z",
        help="Version of the library being built (a nori.toml beside the sources wins)",
    )
    ap.add_argument(
        "--lib-info",
        metavar="FILE",
        help="Display metadata from a .slib library file",
    )
    ap.add_argument(
        "--docs",
        action="store_true",
        help="With --lib-info: print the documentation block of every symbol that has one",
    )
    ap.add_argument(
        "--color",
        choices=list(COLOUR_CHOICES),
        default="auto",
        help="When to use ANSI colour. 'auto' reads NO_COLOR, CLICOLOR_FORCE, TERM and "
             "whether the stream is a terminal.",
    )
    ap.add_argument(
        "--ignore-compiler-version",
        action="store_true",
        help="Load libraries whose requires_compiler excludes this compiler (CE3503)",
    )
    ap.add_argument(
        "--warn-missing-docs",
        action="store_true",
        help="Warn about a declaration, a parameter, a return value, an error arm or a "
             "unit with no documentation (CW7002-CW7006)",
    )
    ap.add_argument(
        "--no-incremental",
        action="store_true",
        help="Force full rebuild, ignoring cached object files",
    )
    ap.add_argument(
        "--clean-cache",
        action="store_true",
        help="Remove __sushi_cache__/ directory and exit",
    )
    ap.add_argument(
        "--cache-dir",
        metavar="PATH",
        help="Custom cache directory location (default: __sushi_cache__/)",
    )
    return ap.parse_args(argv)


def _run(session: Session) -> int:
    """Everything the compiler does. Raises; never reports."""
    from sushi_lang.compiler.loader import get_effective_cwd, check_duplicate_uses
    from sushi_lang.compiler.pipeline import compile_multi_file
    from sushi_lang.internals import errors as er
    from sushi_lang.internals.parser import parse_to_ast

    args = session.args

    if args.clean_cache:
        from sushi_lang.compiler.cache import CacheManager
        effective_cwd = Path(args.source).resolve().parent if args.source else Path.cwd()
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        cm = CacheManager(effective_cwd, cache_dir=cache_dir)
        if cm.cache_path.exists():
            cm.wipe()
            print(f"Removed cache: {cm.cache_path}")
        else:
            print("No cache found.")
        if not args.source:
            return 0

    if args.lib and args.out and not args.out.endswith('.slib'):
        er.emit(session.reporter, er.ERR.CE3500, None, path=args.out)
        return 2

    if args.build_stdlib:
        print("Building standard library...")
        from sushi_lang.backend.stdlib_builder import detect_platform
        from sushi_lang.sushi_stdlib.build import build_all
        try:
            build_all(detect_platform())
        except SushiError:
            raise
        except Exception as e:
            raise StdlibBuildError("CE0007", detail=str(e)) from e
        print()

        if not args.source:
            return 0

    if not args.source:
        print("error: source file required (unless using --build-stdlib)", file=sys.stderr)
        return 2

    src_path = Path(args.source)
    if not src_path.is_absolute():
        src_path = get_effective_cwd() / src_path
    src_path = src_path.resolve()
    session.src_path = src_path

    try:
        src = src_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: cannot read {src_path}: {e}", file=sys.stderr)
        return 2

    session.reporter.source = src
    session.reporter.filename = str(src_path)

    ast, _tree = parse_to_ast(src, dump_parse=args.dump_parse)

    if args.dump_ast:
        print(ast)
        print()

    if src and not src.endswith('\n'):
        er.emit(session.reporter, er.ERR.CW0001, None)

    check_duplicate_uses(ast, session.reporter)

    return compile_multi_file(ast, src_path, session.reporter, args, is_library=args.lib)


def _as_ice(exc: Exception) -> InternalCompilerError:
    """Wrap an unexpected exception as a reportable internal compiler error."""
    detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    ice = InternalCompilerError("CE0000", detail=detail)
    ice.__cause__ = exc
    return ice


def _report(session: Session, exc: SushiError) -> int:
    """Turn a raised diagnostic into a reported one. Always an error: exit 2."""
    from sushi_lang.internals import errors as er

    if isinstance(exc, InternalCompilerError):
        exc.note("this is a bug in the Sushi compiler, not in your program")
        if not session.args.traceback:
            exc.help("re-run with --traceback for the full Python traceback, "
                     "then please report it")

    er.emit_exception(session.reporter, exc)
    return 2


def _flush(session: Session) -> None:
    """Print the collected diagnostics, then the Python traceback if asked for."""
    session.reporter.print()
    print()

    if session.crash is not None and session.args.traceback:
        import traceback
        traceback.print_exception(session.crash)


def main(argv: list[str] | None = None) -> int:
    """Main compiler entry point."""
    # The banner is a coloured line, so it comes AFTER the flag that decides its colour.
    # A usage error now prints argparse's message alone, with no banner above it.
    args = _parse_args(argv)
    set_colour_override(args.color)
    print_banner()

    if args.version:
        return 0

    if args.lib_info:
        return library_info_command(Path(args.lib_info), args.docs, args.color)

    session = Session(args=args)

    try:
        rc = _run(session)
    except KeyboardInterrupt:
        return 130
    except SushiError as exc:
        session.crash = exc
        rc = _report(session, exc)
    except Exception as exc:
        session.crash = exc
        rc = _report(session, _as_ice(exc))

    _flush(session)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
