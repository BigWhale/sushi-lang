from __future__ import annotations
import argparse, sys, os
from pathlib import Path
from lark import Lark, UnexpectedInput
from semantics.ast_builder import ASTBuilder, BlankReturnSyntaxError, UnterminatedInterpolationError, EmptyInterpolationError, CStyleOctalError
from backend.codegen_llvm import LLVMCodegen
from internals.indenter import LangIndenter
from semantics.semantic_analyzer import SemanticAnalyzer
from internals.report import Reporter
from semantics.units import UnitManager, Unit
from semantics.ast import Program

from internals.version import print_banner

GRAMMAR_PATH = Path(__file__).with_name("grammar.lark")

def _check_duplicate_uses(ast: Program, reporter: Reporter) -> None:
    """
    Check for duplicate use statements in a single file and emit warnings.

    Args:
        ast: The parsed AST of the file
        reporter: Reporter for warning collection
    """
    from internals import errors as er

    seen_units = {}  # unit_path -> first occurrence location

    for use_stmt in ast.uses:
        if use_stmt.path in seen_units:
            # Found duplicate - emit warning with location of first use
            prev_loc = seen_units[use_stmt.path]
            er.emit(reporter, er.ERR.CW3001, use_stmt.loc,
                   unit=use_stmt.path,
                   prev_loc=f"{prev_loc.line}:{prev_loc.col}")
        else:
            # First occurrence - track it
            seen_units[use_stmt.path] = use_stmt.loc

def get_effective_cwd() -> Path:
    """
    Get the effective current working directory for file resolution.

    This function checks for the SUSHI_CWD environment variable set by the sushic script.
    If present, it uses that directory (where sushic was originally executed).
    Otherwise, it falls back to the actual current working directory.

    Returns:
        Path object representing the directory where .sushi files should be resolved from.
    """
    sushi_cwd = os.environ.get('SUSHI_CWD')
    if sushi_cwd:
        return Path(sushi_cwd)
    return Path.cwd()

def _improve_parse_error(e: UnexpectedInput) -> str:
    """Improve parsing error messages for common cases."""
    error_text = str(e)

    # Check for common if statement parentheses error
    # This pattern matches when we expect LPAR (left parenthesis) but get something else
    if "Expected one of:" in error_text and "LPAR" in error_text:
        # Check if the error occurs around line content that starts with 'if' or 'elif'
        # We need to inspect the actual UnexpectedInput object for more context
        lines = error_text.split('\n')
        location_line = lines[0] if lines else ""

        # Extract line number to check source context
        import re
        line_match = re.search(r'at line (\d+)', location_line)
        if line_match:
            # This is likely an if/elif without parentheses error
            return f"{location_line}\nParsing error: Missing parentheses around if/elif condition.\nHint: Use 'if (condition):' instead of 'if condition:'"

    # For other errors, return original message
    return error_text

def parse_to_ast(src: str, dump_parse: bool = False):
    # Ensure completely fresh parser state for each parsing call
    # This is critical for multi-file compilation to prevent span contamination
    from internals.indenter import LangIndenter
    from internals.generic_lexer import GenericTypeLexer

    # Chain postlexers: first handle generics, then indentation
    class ChainedPostlexer:
        def __init__(self):
            self.generic_lexer = GenericTypeLexer()
            self.indenter = LangIndenter()
            # Expose always_accept from the indenter
            self.always_accept = self.indenter.always_accept

        def process(self, stream):
            # First split >> into > > for nested generics
            stream = self.generic_lexer.process(stream)
            # Then handle indentation
            return self.indenter.process(stream)

    kwargs = dict(
        parser="lalr",
        propagate_positions=True,
        maybe_placeholders=False,
        postlex=ChainedPostlexer(),
        lexer="basic",
    )
    parser = Lark.open(str(GRAMMAR_PATH), **kwargs)
    tree = parser.parse(src)
    if dump_parse:
        print(tree.pretty())

    # Create fresh AST builder for each parse
    ast_builder = ASTBuilder()
    return ast_builder.build(tree), tree


def load_unit_recursively(unit_manager: UnitManager, unit_name: str, loaded: set[str], reporter: Reporter) -> bool:
    """
    Recursively load a unit and all its dependencies.

    Args:
        unit_manager: The unit manager instance
        unit_name: Name of the unit to load
        loaded: Set of already loaded unit names to prevent infinite recursion
        reporter: Reporter for error reporting

    Returns:
        True if successful, False if there were errors
    """
    if unit_name in loaded:
        # Unit already loaded - skip to avoid duplication
        # This is normal behavior for diamond dependencies
        return True

    loaded.add(unit_name)

    # Resolve unit file path and read source
    unit_path = unit_manager.resolve_unit_path(unit_name)
    if not unit_path.exists():
        # Report CE3002 error directly
        if unit_manager.reporter:
            from internals import errors as er
            er.emit(unit_manager.reporter, er.ERR.CE3002, None, name=unit_name, path=unit_path)
        return False

    try:
        unit_src = unit_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"error: cannot read {unit_path}: {e}", file=sys.stderr)
        return False

    # Create unit-specific reporter
    unit_reporter = Reporter(source=unit_src, filename=str(unit_path))

    try:
        # Parse the unit
        unit_ast, _ = parse_to_ast(unit_src, dump_parse=False)

        # Check for missing trailing newline
        if unit_src and not unit_src.endswith('\n'):
            from internals import errors as er
            er.emit(unit_reporter, er.ERR.CW0001, None)

        # Check for duplicate use statements
        _check_duplicate_uses(unit_ast, unit_reporter)

        # Load the unit
        unit = unit_manager.load_unit(unit_name, unit_ast)
        if unit is None:
            # Error already reported by load_unit()
            reporter.items.extend(unit_reporter.items)
            return False

        # Recursively load dependencies
        for dep_name in unit.dependencies:
            if not load_unit_recursively(unit_manager, dep_name, loaded, reporter):
                reporter.items.extend(unit_reporter.items)
                return False

    except BlankReturnSyntaxError as e:
        # Use Reporter to emit properly formatted error with location
        from internals import errors as er
        er.emit(unit_reporter, er.ERR.CE2036, e.span)
        reporter.items.extend(unit_reporter.items)
        unit_reporter.print()
        return False
    except UnterminatedInterpolationError as e:
        # Use Reporter to emit properly formatted error with location
        from internals import errors as er
        er.emit(unit_reporter, er.ERR.CE2026, e.span)
        reporter.items.extend(unit_reporter.items)
        unit_reporter.print()
        return False
    except EmptyInterpolationError as e:
        # Use Reporter to emit properly formatted error with location
        from internals import errors as er
        er.emit(unit_reporter, er.ERR.CE2038, e.span)
        reporter.items.extend(unit_reporter.items)
        unit_reporter.print()
        return False
    except CStyleOctalError as e:
        # Use Reporter to emit properly formatted error with location
        from internals import errors as er
        octal_value = e.literal.lstrip('0') or '0'
        er.emit(unit_reporter, er.ERR.CE2071, e.span, literal=e.literal, octal=octal_value)
        reporter.items.extend(unit_reporter.items)
        unit_reporter.print()
        return False
    except UnexpectedInput as e:
        print(f"Parse error in {unit_path}:", file=sys.stderr)
        print(_improve_parse_error(e), file=sys.stderr)
        return False

    # Merge unit reporter into main reporter
    reporter.items.extend(unit_reporter.items)
    return True

def compile_multi_file(main_ast: Program, src_path: Path, reporter: Reporter, args) -> int:
    """
    Handle multi-file compilation when use statements are present.

    Args:
        main_ast: Parsed AST of the main file
        src_path: Path to the main source file
        reporter: Reporter for error/warning collection
        args: Command line arguments

    Returns:
        Exit code (0=success, 1=warnings, 2=errors) or None to continue with single-file compilation
    """
    # Infer main unit name from file path
    # For now, just use the stem (filename without extension)
    main_unit_name = src_path.stem

    # Initialize unit manager with the directory containing the main file for unit resolution
    # This ensures that `use "helper"` statements resolve relative to the main file's location
    unit_manager = UnitManager(root_path=src_path.parent, reporter=reporter)

    # Load main unit
    main_unit = unit_manager.load_unit(main_unit_name, main_ast)
    if main_unit is None:
        # Error already reported
        return 2

    # Recursively load all dependencies
    loaded_units = {main_unit_name}
    for dep_name in main_unit.dependencies:
        if not load_unit_recursively(unit_manager, dep_name, loaded_units, reporter):
            # Errors already reported
            return 2

    # Validate that all loaded units are in the unit manager
    # This ensures deduplication worked correctly
    assert len(loaded_units) == len(unit_manager.units), \
        f"Unit count mismatch: loaded {len(loaded_units)} units but manager has {len(unit_manager.units)}"

    # Validate that all unit names in loaded_units are in the manager
    for unit_name in loaded_units:
        assert unit_name in unit_manager.units, \
            f"Unit '{unit_name}' was loaded but not found in unit manager"

    # Build global symbol table and check for conflicts
    if not unit_manager.build_global_symbol_table():
        # Errors already reported
        return 2

    # Get compilation order
    compilation_order = unit_manager.get_compilation_order()
    if compilation_order is None:
        # Error already reported
        return 2

    # Collect stdlib unit imports from all units
    stdlib_units = set()
    for unit in compilation_order:
        if unit.ast:
            for use_stmt in unit.ast.uses:
                if use_stmt.is_stdlib:
                    stdlib_units.add(use_stmt.path)

    # Display compilation units only if there are multiple units (i.e., user has dependencies)
    if len(compilation_order) > 1:
        print(f"Found {len(compilation_order)} units:")
        for unit in compilation_order:
            print(f"  - {unit.name} ({len(unit.public_symbols)} public symbols)")
        print()

    # Validate and display stdlib units being linked
    if stdlib_units:
        # Early validation: check that all stdlib units exist before proceeding
        # This provides better error messages before semantic analysis
        from backend.codegen_llvm import LLVMCodegen
        temp_cg = LLVMCodegen()
        try:
            for unit_path in stdlib_units:
                # This will raise FileNotFoundError if unit doesn't exist
                temp_cg._resolve_stdlib_unit(unit_path)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2  # Compilation failed

        print(f"Linking {len(stdlib_units)} stdlib units:")
        for unit_path in sorted(stdlib_units):
            # Format as "stdlib / core / primitives" instead of "core/primitives"
            formatted_path = "stdlib / " + " / ".join(unit_path.split('/'))
            print(f"  - {formatted_path}")
        print()

    # Preprocessing: Register stdlib functions from imported modules
    from semantics.stdlib_registry import get_stdlib_registry
    stdlib_registry = get_stdlib_registry()

    # Register all stdlib functions in the function table for imported modules
    # This needs to be done before semantic analysis so type validation can use the registry
    # Note: The actual registration will happen during Pass 0 (CollectorPass)
    # Here we just ensure the registry is initialized and available

    # Run multi-file semantic analysis
    multi_file_analyzer = SemanticAnalyzer(reporter, filename=main_unit_name, unit_manager=unit_manager)
    try:
        multi_file_analyzer.check(main_ast)  # The main AST is passed but multi-file mode is detected
    except ValueError as e:
        # Catch circular dependency errors
        print(f"Compilation failed: {e}", file=sys.stderr)
        return 2

    # Check for semantic analysis errors after multi-file analysis
    if reporter.has_errors:
        return 2  # Compilation failed with errors

    # Multi-file semantic analysis completed successfully
    # Now perform multi-unit code generation
    try:
        from backend.codegen_llvm import LLVMCodegen
        # Pass struct, enum, function, constant, and perk implementation tables to codegen for type resolution
        struct_table = getattr(multi_file_analyzer, 'structs', None)
        enum_table = getattr(multi_file_analyzer, 'enums', None)
        func_table = getattr(multi_file_analyzer, 'funcs', None)
        const_table = getattr(multi_file_analyzer, 'constants', None)
        perk_impl_table = getattr(multi_file_analyzer, 'perk_impls', None)
        cg = LLVMCodegen(struct_table=struct_table, enum_table=enum_table, func_table=func_table, perk_impl_table=perk_impl_table, const_table=const_table)

        # Use multi-unit compilation - resolve output path relative to effective CWD
        effective_cwd = get_effective_cwd()
        if args.out:
            out_path = Path(args.out)
            if not out_path.is_absolute():
                out_path = effective_cwd / out_path
        else:
            # Default to source filename without extension
            source_name = src_path.stem  # e.g., "hello.sushi" -> "hello"
            out_path = effective_cwd / source_name
        # Pass monomorphized extension methods to codegen
        monomorphized_extensions = getattr(multi_file_analyzer, 'monomorphized_extensions', [])

        cg.compile_multi_unit(compilation_order, out=out_path, cc="cc",
                              debug=bool(args.dump_ll), opt=args.opt,
                              verify=not args.no_verify, keep_object=args.keep_object,
                              main_expects_args=multi_file_analyzer.main_expects_args,
                              monomorphized_extensions=monomorphized_extensions)

        if args.write_ll:
            try:
                ll_path = out_path.with_suffix(".ll")
                ll_path.write_text(str(cg.module), encoding="utf-8")
                print(f"wrote LLVM IR: {ll_path}")
            except Exception as e:
                print(f"(warn) failed to write LLVM IR: {e}", file=sys.stderr)

        print(f"Success! Wrote native binary: {out_path}")

        # Check for warnings after successful compilation
        if reporter.has_warnings:
            return 1  # Success with warnings
        return 0  # Success with no warnings

    except Exception as e:
        print(f"Compilation failed: {e}", file=sys.stderr)
        if args.traceback:
            import traceback
            traceback.print_exc()
        return 2  # Backend compilation failed

def main(argv: list[str] | None = None) -> int:
    print_banner()
    ap = argparse.ArgumentParser(prog="compiler", description="Language compiler")

    ap.add_argument("source", nargs='?', help="Path to source file (.sushi)")
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

    args = ap.parse_args(argv)

    # Handle --build-stdlib flag
    if args.build_stdlib:
        print("Building standard library...")
        build_script = Path(__file__).parent / "stdlib" / "build.py"

        if build_script.exists():
            import subprocess
            result = subprocess.run([sys.executable, str(build_script)])
            if result.returncode != 0:
                print("Error: Stdlib build failed", file=sys.stderr)
                return 2
            print()
        else:
            print("Error: Build script not found", file=sys.stderr)
            return 2

        # If --build-stdlib is the only action, exit successfully
        if not args.source:
            return 0

    # Check if source file was provided
    if not args.source:
        print("error: source file required (unless using --build-stdlib)", file=sys.stderr)
        return 2

    # Resolve source file path relative to the effective working directory
    effective_cwd = get_effective_cwd()
    src_path = Path(args.source)

    # If the source path is relative, resolve it relative to the effective CWD
    if not src_path.is_absolute():
        src_path = effective_cwd / src_path

    # Resolve to absolute path for consistent handling
    src_path = src_path.resolve()

    try:
        src = src_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"error: cannot read {src_path}: {e}", file=sys.stderr)
        return 2

    reporter = Reporter(source=src, filename=str(src_path))

    try:
        ast, tree = parse_to_ast(src, dump_parse=args.dump_parse)

        if args.dump_ast:
            print(ast)
            print()

        # Check for missing trailing newline
        if src and not src.endswith('\n'):
            from internals import errors as er
            er.emit(reporter, er.ERR.CW0001, None)

        # Check for duplicate use statements
        _check_duplicate_uses(ast, reporter)

        # Always use multi-file compilation mode
        # This handles both files with and without use statements uniformly
        result = compile_multi_file(ast, src_path, reporter, args)
        if result is not None:
            # Print errors/warnings before returning
            reporter.print()
            print()  # Extra newline after errors/warnings
            return result

    except BlankReturnSyntaxError as e:
        # Use Reporter to emit properly formatted error with location
        from internals import errors as er
        er.emit(reporter, er.ERR.CE2036, e.span)
        reporter.print()
        print()  # Extra newline after error
        return 2  # Parsing failed with errors
    except UnterminatedInterpolationError as e:
        # Use Reporter to emit properly formatted error with location
        from internals import errors as er
        er.emit(reporter, er.ERR.CE2026, e.span)
        reporter.print()
        print()  # Extra newline after error
        return 2  # Parsing failed with errors
    except EmptyInterpolationError as e:
        # Use Reporter to emit properly formatted error with location
        from internals import errors as er
        er.emit(reporter, er.ERR.CE2038, e.span)
        reporter.print()
        print()  # Extra newline after error
        return 2  # Parsing failed with errors
    except CStyleOctalError as e:
        # Use Reporter to emit properly formatted error with location
        from internals import errors as er
        octal_value = e.literal.lstrip('0') or '0'
        er.emit(reporter, er.ERR.CE2071, e.span, literal=e.literal, octal=octal_value)
        reporter.print()
        print()  # Extra newline after error
        return 2  # Parsing failed with errors
    except UnexpectedInput as e:
        print(_improve_parse_error(e), file=sys.stderr)
        return 2  # Parsing failed with errors


if __name__ == "__main__":
    raise SystemExit(main())
