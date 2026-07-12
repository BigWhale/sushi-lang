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


def print_library_info(library_path: Path) -> int:
    """Print formatted metadata from a .slib library file.

    Returns:
        0 on success, 2 on error.
    """
    from sushi_lang.backend.library_format import LibraryFormat
    from sushi_lang.backend.library_linker import LibraryError

    if not library_path.exists():
        print(f"Error: file not found: {library_path}", file=sys.stderr)
        return 2

    if not library_path.suffix == '.slib':
        print(f"Error: expected .slib file, got: {library_path}", file=sys.stderr)
        return 2

    try:
        metadata, bitcode = LibraryFormat.read(library_path)
    except LibraryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    # Header
    print(f"Library: {metadata['library_name']}")
    print(f"Platform: {metadata['platform']}")
    print(f"Compiler: {metadata['compiler_version']}")
    print(f"Compiled: {metadata['compiled_at']}")
    print(f"Protocol: {metadata['sushi_lib_version']}")
    print()

    # Public functions
    funcs = metadata.get('public_functions', [])
    if funcs:
        print(f"Public Functions ({len(funcs)}):")
        for func in funcs:
            params = ', '.join(f"{p['type']} {p['name']}" for p in func['params'])
            print(f"  fn {func['name']}({params}) {func['return_type']}")
        print()

    # Generic Functions (shipped as instantiable templates, not concrete callables)
    generic_funcs = metadata.get('templates', {}).get('generic_functions', [])
    if generic_funcs:
        print(f"Generic Functions ({len(generic_funcs)}):")
        for gf in generic_funcs:
            tps = gf.get('type_params', [])
            if tps:
                rendered = []
                for tp in tps:
                    constraints = tp.get('constraints') or []
                    if constraints:
                        rendered.append(f"{tp['name']}: {', '.join(constraints)}")
                    else:
                        rendered.append(tp['name'])
                generic = f"<{', '.join(rendered)}>"
            else:
                generic = ""
            print(f"  fn {gf['name']}{generic} (template)")
        print()

    # Public constants
    consts = metadata.get('public_constants', [])
    if consts:
        print(f"Public Constants ({len(consts)}):")
        for const in consts:
            print(f"  const {const['type']} {const['name']}")
        print()

    # Structs
    structs = metadata.get('structs', [])
    if structs:
        print(f"Structs ({len(structs)}):")
        for struct in structs:
            generic = ""
            if struct.get('is_generic') and struct.get('type_params'):
                type_params = ', '.join(struct['type_params'])
                generic = f"<{type_params}>"
            print(f"  struct {struct['name']}{generic}:")
            for field in struct['fields']:
                print(f"    {field['type']} {field['name']}")
        print()

    # Enums
    enums = metadata.get('enums', [])
    if enums:
        print(f"Enums ({len(enums)}):")
        for enum in enums:
            generic = ""
            if enum.get('is_generic') and enum.get('type_params'):
                type_params = ', '.join(enum['type_params'])
                generic = f"<{type_params}>"
            print(f"  enum {enum['name']}{generic}:")
            for variant in enum['variants']:
                if variant.get('has_data'):
                    print(f"    {variant['name']}({variant['data_type']})")
                else:
                    print(f"    {variant['name']}")
        print()

    # Dependencies
    deps = metadata.get('dependencies', [])
    if deps:
        print(f"Dependencies ({len(deps)}):")
        for dep in deps:
            print(f"  <{dep}>")
        print()

    # Size info
    print(f"Bitcode: {len(bitcode):,} bytes")

    return 0


@dataclass
class Session:
    """Everything the top-level guard needs to render whatever went wrong.

    The reporter is the one `_run` has been filling in, so diagnostics collected
    before a crash still print, with the crash appended.
    """
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
        "--lib-info",
        metavar="FILE",
        help="Display metadata from a .slib library file",
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
        return print_library_info(Path(args.lib_info))

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
