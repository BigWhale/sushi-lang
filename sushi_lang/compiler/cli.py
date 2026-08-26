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


def library_info_command(library_path: Path, show_docs: bool = False) -> int:
    """--lib-info: run the toolchain slib-info tool, or the Python fallback.

    The tool owns the report and its exit code propagates. Only a failure to
    execute the binary at all falls back to print_library_info.

    `--docs` is spelled the same at both ends, so the switch travels as itself.
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
        cmd.append(str(library_path))
        try:
            return subprocess.run(cmd).returncode
        except OSError:
            pass
    return print_library_info(library_path, show_docs)


def _surface(type_str: str) -> str:
    """One manifest type string, in the surface `@(...)` spelling a reader is owed.

    The manifest carries the INTERNAL identity name, `List<i32>`, because a consumer
    reads it back through `parse_type_string`. Angle brackets are never user-visible
    text (`docs/design/type-identity.md`), so the report converts here and nowhere else.
    """
    from sushi_lang.semantics.generics.type_display import display_type_name
    return display_type_name(type_str)


def _render_params(params: list) -> str:
    """One parameter list, as a signature reads it.

    `nom` is the one mode a TYPE cannot spell, so the record's own `mode` field is the
    only place it can come from. `peek` and `poke` ride on the type itself, so the type
    string already carries them and printing the mode again would double it.
    """
    rendered = []
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


def _render_signature(func: dict) -> str:
    """One function's whole signature, concrete or generic.

    ONE renderer, so a template prints what a concrete function prints. A generic used
    to print `(template)` where its parameters belong, which is why its `- Parameter`
    tags were stored and never rendered.
    """
    generic = _render_type_params(func.get('type_params'))
    params = _render_params(func.get('params') or [])
    line = f"fn {func['name']}{generic}({params}) {_surface(func['return_type'])}"
    # The default error type is StdError and a signature that takes it does not say so,
    # so a record with no `error_type` prints no arm either.
    error = func.get('error_type')
    return f"{line} | {_surface(error)}" if error else line


def _print_generic_named(templates: dict, key: str, title: str, keyword: str,
                         show_docs: bool) -> None:
    """One section of generic structs, or one of generic enums.

    The two records have the same shape -- a name, its type parameters and its own
    block -- so they share a printer and differ by the keyword. A generic struct's FIELD
    blocks are not in the index at all (documentation.md S8, R3), so neither record has
    members to print.
    """
    items = templates.get(key) or []
    if not _section(title, items):
        return
    for record in items:
        generic = _render_type_params(record.get('type_params'))
        print(f"  {keyword} {record['name']}{generic}:")
        _print_doc(record, "    ", show_docs)
    print()


def _section(title: str, items: list) -> bool:
    """Open a section, or answer False when it holds nothing.

    A section with no members prints no header. Every section obeys it, which is why
    the guard is one function rather than one `if` per section.
    """
    if not items:
        return False
    print(f"{title} ({len(items)}):")
    return True


def _print_doc_lines(indent: str, text: str) -> None:
    """One line of output per line of `text`. A blank line prints EMPTY.

    `split` and not `splitlines`: the latter drops a trailing empty field and also
    breaks on `\r`, `\x0b`, `\x0c` and the Unicode separators, and the Sushi tool's
    `.split("\n")` does neither. The two must cut the same bytes.
    """
    for line in text.split("\n"):
        print(f"{indent}{line}" if line else "")


def _print_doc_record(doc: dict | None, owner: dict | None, indent: str,
                      show_docs: bool = True) -> None:
    """One doc record: the summary, a blank line, the body, then the tags.

    No blank line before the tags, and the one above the body prints only when there is
    a summary to separate it from (`docs/design/documentation.md` section 9).

    Parameters print in DECLARATION order, read from the owner's own `params` array and
    looked up by name. A map's wire order is not the signature's order, and a record
    with no `params` array -- a struct, a unit, a template -- renders no parameter line.

    THE gate for `--docs`: every doc record in the report comes through here, so the
    switch is read once rather than at each of the seven sections.
    """
    if not doc or not show_docs:
        return

    summary = doc.get('summary', '')
    body = doc.get('body', '')
    if summary:
        _print_doc_lines(indent, summary)
    if body:
        if summary:
            print()
        _print_doc_lines(indent, body)

    named = doc.get('params') or {}
    for param in (owner or {}).get('params') or []:
        text = named.get(param['name'])
        if text:
            _print_doc_lines(indent, f"- Parameter {param['name']}: {text}")

    for key, label in (('returns', 'Returns'), ('errors', 'Errors')):
        text = doc.get(key)
        if text:
            _print_doc_lines(indent, f"- {label}: {text}")


def _print_doc(owner: dict, indent: str, show_docs: bool) -> None:
    """The doc record of one manifest entry, when it carries one."""
    _print_doc_record(owner.get('doc'), owner, indent, show_docs)


def print_library_info(library_path: Path, show_docs: bool = False) -> int:
    """Print formatted metadata from a .slib library file."""
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
    if _section("Units", units):
        unit_docs = metadata.get('unit_docs') or {}
        for unit in units:
            print(f"  {unit}")
            _print_doc_record(unit_docs.get(unit), None, "    ", show_docs)
        print()

    templates = metadata.get('templates') or {}

    funcs = metadata.get('public_functions', [])
    if _section("Public Functions", funcs):
        for func in funcs:
            print(f"  {_render_signature(func)}")
            _print_doc(func, "    ", show_docs)
        print()

    generic_funcs = templates.get('generic_functions', [])
    if _section("Generic Functions", generic_funcs):
        for gf in generic_funcs:
            print(f"  {_render_signature(gf)}")
            _print_doc(gf, "    ", show_docs)
        print()

    consts = metadata.get('public_constants', [])
    if _section("Public Constants", consts):
        for const in consts:
            print(f"  const {_surface(const['type'])} {const['name']}")
            _print_doc(const, "    ", show_docs)
        print()

    structs = metadata.get('structs', [])
    if _section("Structs", structs):
        for struct in structs:
            generic = ""
            if struct.get('is_generic') and struct.get('type_params'):
                generic = f"@({', '.join(struct['type_params'])})"
            print(f"  struct {struct['name']}{generic}:")
            _print_doc(struct, "    ", show_docs)
            for field in struct['fields']:
                print(f"    {_surface(field['type'])} {field['name']}")
                _print_doc(field, "      ", show_docs)
        print()

    # A generic struct beside its concrete twin, not filed away with the other
    # templates: a reader looking for `Box` wants it near `Point`.
    _print_generic_named(templates, 'generic_structs', "Generic Structs", "struct",
                         show_docs)

    enums = metadata.get('enums', [])
    if _section("Enums", enums):
        for enum in enums:
            generic = ""
            if enum.get('is_generic') and enum.get('type_params'):
                generic = f"@({', '.join(enum['type_params'])})"
            print(f"  enum {enum['name']}{generic}:")
            _print_doc(enum, "    ", show_docs)
            for variant in enum['variants']:
                if variant.get('has_data'):
                    print(f"    {variant['name']}({_surface(variant['data_type'])})")
                else:
                    print(f"    {variant['name']}")
                _print_doc(variant, "      ", show_docs)
        print()

    _print_generic_named(templates, 'generic_enums', "Generic Enums", "enum", show_docs)

    # A perk reaches the manifest only when an exported generic's constraint names it,
    # so this section lists the contracts a consumer can actually be asked to satisfy.
    perks = templates.get('perks', [])
    if _section("Perks", perks):
        for perk in perks:
            print(f"  perk {perk['name']}:")
            _print_doc(perk, "    ", show_docs)
        print()

    impls = templates.get('perk_impls', [])
    if _section("Perk Implementations", impls):
        for impl in impls:
            print(f"  extend {_surface(impl['type'])} with {impl['perk']}:")
            _print_doc(impl, "    ", show_docs)
            for method in impl.get('methods') or []:
                print(f"    fn {method['name']}")
                _print_doc(method, "      ", show_docs)
        print()

    deps = metadata.get('dependencies', [])
    if _section("Dependencies", deps):
        for dep in deps:
            print(f"  <{dep}>")
        print()

    if kind != 'binary':
        print(f"Source: {source_size:,} bytes")
    if kind != 'source':
        print(f"Bitcode: {bitcode_size:,} bytes")

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
    print_banner()

    args = _parse_args(argv)

    if args.version:
        return 0

    if args.lib_info:
        return library_info_command(Path(args.lib_info), args.docs)

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
