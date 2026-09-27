"""CLI entry point and argument parsing."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from sushi_lang.internals.diagnostics import InternalCompilerError, SushiError
from sushi_lang.internals.report import Reporter
from sushi_lang.internals.styling import COLOUR_CHOICES, set_colour_override
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
    from sushi_lang.compiler.lib_info import print_library_info
    return print_library_info(library_path, show_docs, color)


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
    ap.add_argument(
        "--color",
        choices=list(COLOUR_CHOICES),
        default="auto",
        help="When to use ANSI colour. 'auto' reads NO_COLOR, CLICOLOR_FORCE, TERM and "
             "whether the stream is a terminal.",
    )

    build = ap.add_argument_group("build")
    library = ap.add_argument_group(
        "library", "Read only by a --lib build; --docs only by --lib-info.")
    inspect = ap.add_argument_group("inspection")
    cache = ap.add_argument_group(
        "cache", "The incremental build of a program of more than one unit.")
    build.add_argument("-o", "--out", metavar="OUT",
                       help="Output binary path (default: source filename without extension)")
    build.add_argument(
        "--opt",
        choices=["none", "mem2reg", "O1", "O2", "O3"],
        default="mem2reg",
        help="Optimization level. 'mem2reg' promotes locals to SSA without a full pipeline.",
    )
    build.add_argument(
        "--no-verify",
        action="store_true",
        help="Disable LLVM IR verification (pre/post optimization).",
    )
    build.add_argument(
        "--keep-object",
        action="store_true",
        help="Keep the generated .o file after linking (not on the incremental build; "
             "add --no-incremental)",
    )
    build.add_argument(
        "--build-stdlib",
        action="store_true",
        help="Rebuild standard library from source",
    )
    build.add_argument(
        "--ignore-compiler-version",
        action="store_true",
        help="Load libraries whose requires_compiler excludes this compiler (CE3503)",
    )
    build.add_argument(
        "--warn-missing-docs",
        action="store_true",
        help="Warn about a declaration, a parameter, a return value, an error arm or a "
             "unit with no documentation (CW7002-CW7006)",
    )
    library.add_argument(
        "--lib",
        action="store_true",
        help="Compile to library bitcode (no main() required)",
    )
    library.add_argument(
        "--lib-kind",
        choices=["source", "binary", "hybrid"],
        help="How the library ships: source text, compiled bitcode, or both "
             "(default: source)",
    )
    library.add_argument(
        "--lib-version",
        metavar="X.Y.Z",
        help="Version of the library being built (a nori.toml beside the sources wins)",
    )
    library.add_argument(
        "--lib-info",
        metavar="FILE",
        help="Display metadata from a .slib library file",
    )
    library.add_argument(
        "--docs",
        action="store_true",
        help="With --lib-info: print the documentation block of every symbol that has one",
    )
    inspect.add_argument("--dump-parse", action="store_true", help="Print raw Lark tree")
    inspect.add_argument("--dump-ast", action="store_true", help="Print AST")
    inspect.add_argument("--write-ll", action="store_true",
                         help="Write LLVM IR to <OUT>.ll file")
    inspect.add_argument("--dump-ll", action="store_true",
                         help="Dump generated LLVM IR to terminal")
    inspect.add_argument(
        "--traceback",
        action="store_true",
        help="Print full traceback on backend errors (for debugging)",
    )
    cache.add_argument(
        "--no-incremental",
        action="store_true",
        help="Force full rebuild, ignoring cached object files",
    )
    cache.add_argument(
        "--clean-cache",
        action="store_true",
        help="Remove __sushi_cache__/ directory and exit",
    )
    cache.add_argument(
        "--cache-dir",
        metavar="PATH",
        help="Custom cache directory location (default: __sushi_cache__/)",
    )
    return ap.parse_args(argv)


COMMAND_LINE = "<command line>"

# (flag attribute, spelling, when it has no effect, why)
_NO_EFFECT: tuple[tuple[str, str, Callable[[argparse.Namespace], bool], str], ...] = (
    ("docs", "--docs", lambda a: not a.lib_info, "without --lib-info"),
    ("lib_kind", "--lib-kind", lambda a: not a.lib, "without --lib"),
    ("lib_version", "--lib-version", lambda a: not a.lib, "without --lib"),
    ("keep_object", "--keep-object", lambda a: a.lib,
     "with --lib: a library build writes no object file"),
)


def _validate_args(args: argparse.Namespace, reporter: Reporter) -> None:
    """Every flag-combination rule, read once after parsing (CW0003)."""
    from sushi_lang.internals import errors as er

    for attr, flag, ignored, reason in _NO_EFFECT:
        if getattr(args, attr) and ignored(args):
            er.emit(reporter, er.ERR.CW0003, None, filename=COMMAND_LINE,
                    flag=flag, reason=reason)
    if args.lib_kind is None:
        args.lib_kind = "source"


def _run(session: Session) -> int:
    """Everything the compiler does. Raises; never reports."""
    from sushi_lang.compiler.loader import check_duplicate_uses, read_source
    from sushi_lang.compiler.options import BuildOptions
    from sushi_lang.compiler.pipeline import build_stdlib, compile_multi_file
    from sushi_lang.internals import errors as er
    from sushi_lang.internals.parser import parse_to_ast

    args = session.args
    options = BuildOptions.from_args(args)

    if args.clean_cache:
        from sushi_lang.compiler.cache import CacheManager
        root = Path(args.source).resolve().parent if args.source else Path.cwd()
        cm = CacheManager.for_run(options, root)
        if cm.cache_path.exists():
            cm.wipe()
            print(f"Removed cache: {cm.cache_path}")
        else:
            print("No cache found.")
        if not args.source:
            return 1 if session.reporter.has_warnings else 0

    if args.lib and args.out and not args.out.endswith('.slib'):
        er.emit(session.reporter, er.ERR.CE3500, None, path=args.out)
        return 2

    if args.build_stdlib:
        print("Building standard library...")
        build_stdlib(rebuild=True)
        print()

        if not args.source:
            return 1 if session.reporter.has_warnings else 0

    if not args.source:
        er.emit(session.reporter, er.ERR.CE3018, None, filename=COMMAND_LINE)
        return 2

    src_path = Path(args.source).resolve()
    session.src_path = src_path

    src = read_source(src_path, session.reporter)
    if src is None:
        return 2

    session.reporter.source = src
    session.reporter.filename = str(src_path)

    ast, _tree = parse_to_ast(src, dump_parse=args.dump_parse,
                              reporter=session.reporter)

    # The parse STAGE batches, and stops before the next one. A rule that recovered
    # put a substitute node in the tree, so a pass reading it would blame the user for
    # a value the builder invented (#641).
    if session.reporter.has_errors:
        return 2

    if args.dump_ast:
        print(ast)
        print()

    if src and not src.endswith('\n'):
        er.emit(session.reporter, er.ERR.CW0001, None)

    check_duplicate_uses(ast, session.reporter)

    return compile_multi_file(ast, src_path, session.reporter, options)


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


def _enter_user_directory() -> None:
    """Start in the directory the user ran `sushic` from, which the wrapper names in
    SUSHI_CWD, so every relative path and default directory is the user's."""
    import os

    user_cwd = os.environ.get("SUSHI_CWD")
    if user_cwd:
        os.chdir(user_cwd)


def main(argv: list[str] | None = None) -> int:
    """Main compiler entry point."""
    _enter_user_directory()
    # The banner is a coloured line, so it comes AFTER the flag that decides its colour.
    # A usage error now prints argparse's message alone, with no banner above it.
    args = _parse_args(argv)
    set_colour_override(args.color)
    print_banner()

    if args.version:
        return 0

    session = Session(args=args)
    if args.lib_info:
        # The library is named on the command line, and every diagnostic names its path.
        session.reporter.filename = COMMAND_LINE
    else:
        _validate_args(args, session.reporter)

    try:
        if args.lib_info:
            rc = library_info_command(Path(args.lib_info), args.docs, args.color)
        else:
            rc = _run(session)
    except KeyboardInterrupt:
        return 130
    except SushiError as exc:
        session.crash = exc
        rc = _report(session, exc)
    except Exception as exc:
        session.crash = exc
        rc = _report(session, _as_ice(exc))

    if args.lib_info and session.crash is None:
        # The report is the whole output: no diagnostic, and no blank line after it.
        return rc
    _flush(session)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
