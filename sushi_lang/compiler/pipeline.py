"""Multi-file compilation orchestration."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from sushi_lang.compiler.loader import (
    get_effective_cwd,
    load_unit_recursively,
)
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.ast import Program
from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.units import UnitManager


def _activate_generic_provider(unit_path: str) -> None:
    """Activate generic type provider for a stdlib unit if applicable.

    Maps stdlib unit paths to generic type providers and activates them
    in the GenericTypeRegistry. Must be called BEFORE semantic analysis.
    """
    from sushi_lang.semantics.generics.providers.registry import GenericTypeRegistry

    generic_type_map = {
        "collections/hashmap": "HashMap",
    }

    generic_name = generic_type_map.get(unit_path)
    if generic_name is not None:
        GenericTypeRegistry.activate(generic_name)


def compile_multi_file(main_ast: Program, src_path: Path, reporter: Reporter,
                       args, is_library: bool = False) -> int:
    """Handle multi-file compilation when use statements are present.

    Args:
        main_ast: Parsed AST of the main file.
        src_path: Path to the main source file.
        reporter: Reporter for error/warning collection.
        args: Command line arguments.
        is_library: If True, compile to library bitcode.

    Returns:
        Exit code (0=success, 1=warnings, 2=errors).
    """
    # Initialize generic type provider registry
    from sushi_lang.semantics.generics.providers import register_all_providers
    from sushi_lang.semantics.generics.providers.registry import GenericTypeRegistry
    register_all_providers()
    GenericTypeRegistry.deactivate_all()

    main_unit_name = src_path.stem
    unit_manager = UnitManager(root_path=src_path.parent, reporter=reporter)

    # Load main unit
    main_unit = unit_manager.load_unit(main_unit_name, main_ast)
    if main_unit is None:
        return 2

    # Recursively load all dependencies
    loaded_units = {main_unit_name}
    for dep_name in main_unit.dependencies:
        if not load_unit_recursively(unit_manager, dep_name, loaded_units, reporter):
            return 2

    assert len(loaded_units) == len(unit_manager.units), \
        f"Unit count mismatch: loaded {len(loaded_units)} units but manager has {len(unit_manager.units)}"
    for unit_name in loaded_units:
        assert unit_name in unit_manager.units, \
            f"Unit '{unit_name}' was loaded but not found in unit manager"

    # Build global symbol table and check for conflicts
    if not unit_manager.build_global_symbol_table():
        return 2

    compilation_order = unit_manager.get_compilation_order()
    if compilation_order is None:
        return 2

    # Collect stdlib and library imports from all units
    stdlib_units = set()
    library_imports = set()
    for unit in compilation_order:
        if unit.ast:
            for use_stmt in unit.ast.uses:
                if use_stmt.is_stdlib:
                    stdlib_units.add(use_stmt.path)
                    _activate_generic_provider(use_stmt.path)
                elif use_stmt.is_library:
                    library_imports.add(use_stmt.path)

    # Display compilation units only if there are multiple
    if len(compilation_order) > 1:
        print(f"Found {len(compilation_order)} units:")
        for unit in compilation_order:
            print(f"  - {unit.name} ({len(unit.public_symbols)} public symbols)")
        print()

    # Validate and display stdlib units being linked
    if stdlib_units:
        from sushi_lang.backend.codegen_llvm import LLVMCodegen
        temp_cg = LLVMCodegen()
        try:
            for unit_path in stdlib_units:
                temp_cg.stdlib._resolve_stdlib_unit(unit_path)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2

        print(f"Linking {len(stdlib_units)} stdlib units:")
        for unit_path in sorted(stdlib_units):
            formatted_path = "stdlib / " + " / ".join(unit_path.split('/'))
            print(f"  - {formatted_path}")
        print()

    # Register stdlib functions
    from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
    get_stdlib_registry()

    # Validate and load library imports
    library_linker = None
    if library_imports:
        from sushi_lang.backend.library_linker import LibraryLinker, LibraryError
        from sushi_lang.backend.library_format import LibraryFormat
        library_linker = LibraryLinker()

        print(f"Linking {len(library_imports)} custom libraries:")
        for lib_path in sorted(library_imports):
            try:
                slib_path = library_linker.resolve_library(lib_path)
                metadata = LibraryFormat.read_metadata_only(slib_path)
                library_linker.loaded_libraries[metadata["library_name"]] = metadata

                formatted_path = " / ".join(lib_path.split('/'))
                print(f"  - {formatted_path}")
            except LibraryError as e:
                print(f"{e}", file=sys.stderr)
                return 2
        print()

    # Run multi-file semantic analysis
    multi_file_analyzer = SemanticAnalyzer(reporter, filename=main_unit_name,
                                           unit_manager=unit_manager,
                                           library_linker=library_linker)
    try:
        multi_file_analyzer.check(main_ast)
    except ValueError as e:
        print(f"Compilation failed: {e}", file=sys.stderr)
        return 2

    if reporter.has_errors:
        return 2

    # Determine whether to use incremental compilation
    use_incremental = (
        len(compilation_order) > 1
        and not is_library
        and not getattr(args, 'no_incremental', False)
        and not getattr(args, 'dump_ll', False)
    )

    # Code generation
    try:
        if use_incremental:
            return _compile_incremental(
                compilation_order, multi_file_analyzer, src_path, reporter, args,
                stdlib_units, library_imports, library_linker, unit_manager,
            )
        else:
            return _compile_monolithic(
                compilation_order, multi_file_analyzer, src_path, reporter, args,
                is_library, stdlib_units, library_imports, library_linker,
            )
    except Exception as e:
        print(f"Compilation failed: {e}", file=sys.stderr)
        if args.traceback:
            import traceback
            traceback.print_exc()
        return 2


def _compile_monolithic(compilation_order, analyzer, src_path, reporter, args,
                        is_library, stdlib_units, library_imports, library_linker) -> int:
    """Original single-module compilation path."""
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

    struct_table = getattr(analyzer, 'structs', None)
    enum_table = getattr(analyzer, 'enums', None)
    func_table = getattr(analyzer, 'funcs', None)
    const_table = getattr(analyzer, 'constants', None)
    perk_impl_table = getattr(analyzer, 'perk_impls', None)
    cg = LLVMCodegen(struct_table=struct_table, enum_table=enum_table,
                     func_table=func_table, perk_impl_table=perk_impl_table,
                     const_table=const_table)

    effective_cwd = get_effective_cwd()
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = effective_cwd / out_path
    else:
        source_name = src_path.stem
        if is_library:
            out_path = effective_cwd / (source_name + ".slib")
        else:
            out_path = effective_cwd / source_name

    monomorphized_extensions = getattr(analyzer, 'monomorphized_extensions', [])

    if is_library:
        bitcode = cg.compile_to_bitcode(compilation_order,
                                        debug=bool(args.dump_ll), opt=args.opt,
                                        verify=not args.no_verify,
                                        monomorphized_extensions=monomorphized_extensions)

        from sushi_lang.backend.library_manifest import LibraryManifestGenerator
        manifest_gen = LibraryManifestGenerator(analyzer)
        manifest_gen.generate(compilation_order, out_path, bitcode)

        if args.write_ll:
            try:
                ll_path = out_path.with_suffix(".ll")
                ll_path.write_text(str(cg.module), encoding="utf-8")
                print(f"wrote LLVM IR: {ll_path}")
            except Exception as e:
                print(f"(warn) failed to write LLVM IR: {e}", file=sys.stderr)

        print(f"Success! Wrote library: {out_path}")
    else:
        cg.compile_multi_unit(compilation_order, out=out_path, cc="cc",
                              debug=bool(args.dump_ll), opt=args.opt,
                              verify=not args.no_verify, keep_object=args.keep_object,
                              main_expects_args=analyzer.main_expects_args,
                              monomorphized_extensions=monomorphized_extensions,
                              library_linker=library_linker,
                              library_registry=analyzer.library_registry)

        if args.write_ll:
            try:
                ll_path = out_path.with_suffix(".ll")
                ll_path.write_text(str(cg.module), encoding="utf-8")
                print(f"wrote LLVM IR: {ll_path}")
            except Exception as e:
                print(f"(warn) failed to write LLVM IR: {e}", file=sys.stderr)

        print(f"Success! Wrote native binary: {out_path}")

    if reporter.has_warnings:
        return 1
    return 0


def _compile_incremental(compilation_order, analyzer, src_path, reporter, args,
                         stdlib_units, library_imports, library_linker,
                         unit_manager) -> int:
    """Incremental compilation path: per-unit .o caching."""
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.compiler.cache import CacheManager
    from sushi_lang.compiler.fingerprint import (
        compute_unit_fingerprint,
        compute_stdlib_fingerprint,
        compute_lib_fingerprint,
    )

    effective_cwd = get_effective_cwd()
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = effective_cwd / out_path
    else:
        out_path = effective_cwd / src_path.stem

    cache_dir = Path(args.cache_dir) if getattr(args, 'cache_dir', None) else None
    cache = CacheManager(src_path.parent, opt_level=args.opt, cache_dir=cache_dir)

    # Validate cache or wipe and rebuild
    if not cache.is_valid():
        cache.invalidate_and_rebuild()
    else:
        cache.ensure_dirs()

    monomorphized_extensions = getattr(analyzer, 'monomorphized_extensions', [])

    # Create a single codegen instance for all per-unit compilations
    struct_table = getattr(analyzer, 'structs', None)
    enum_table = getattr(analyzer, 'enums', None)
    func_table = getattr(analyzer, 'funcs', None)
    const_table = getattr(analyzer, 'constants', None)
    perk_impl_table = getattr(analyzer, 'perk_impls', None)
    cg = LLVMCodegen(struct_table=struct_table, enum_table=enum_table,
                     func_table=func_table, perk_impl_table=perk_impl_table,
                     const_table=const_table)
    cg.main_expects_args = analyzer.main_expects_args
    cg.monomorphized_extensions = monomorphized_extensions
    cg.library_linker = library_linker
    cg.library_registry = getattr(analyzer, 'library_registry', None)

    # Compute fingerprints and determine what needs rebuilding
    obj_paths: list[Path] = []
    rebuilt = []
    cached = []

    t0 = time.monotonic()

    print("Code generation:")

    for unit in compilation_order:
        fp = compute_unit_fingerprint(unit, unit_manager, monomorphized_extensions)

        if cache.has_cached_unit(unit.name, fp):
            obj_path = cache.unit_object_path(unit.name)
            obj_paths.append(obj_path)
            cached.append(unit.name)
            print(f"  {unit.name:<30s} [cached]")
        else:
            obj_bytes = cg.compile_single_unit_to_object(
                unit, compilation_order,
                opt=args.opt, verify=not args.no_verify,
            )
            obj_path = cache.store_unit_object(unit.name, obj_bytes, fp)
            obj_paths.append(obj_path)
            rebuilt.append(unit.name)
            print(f"  {unit.name:<30s} [rebuilt]")

    # Compile stdlib modules to cached .o files
    for stdlib_unit in sorted(stdlib_units):
        bc_paths = cg.stdlib._resolve_stdlib_unit(stdlib_unit)
        fp = compute_stdlib_fingerprint(bc_paths)
        if cache.has_cached_stdlib(stdlib_unit, fp):
            obj_paths.append(cache.stdlib_object_path(stdlib_unit))
        else:
            obj_bytes = cg.compile_stdlib_to_object(stdlib_unit, opt=args.opt)
            obj_path = cache.store_stdlib_object(stdlib_unit, obj_bytes, fp)
            obj_paths.append(obj_path)

    # Compile library modules to cached .o files
    if library_linker is not None:
        for lib_path in sorted(library_imports):
            slib_path = library_linker.resolve_library(lib_path)
            fp = compute_lib_fingerprint(slib_path)
            lib_name = lib_path.replace("/", "_")
            if cache.has_cached_lib(lib_name, fp):
                obj_paths.append(cache.lib_object_path(lib_name))
            else:
                obj_bytes = cg.compile_library_to_object(lib_path, library_linker, opt=args.opt)
                obj_path = cache.store_lib_object(lib_name, obj_bytes, fp)
                obj_paths.append(obj_path)

    codegen_time = time.monotonic() - t0

    # Link all .o files together
    t1 = time.monotonic()
    cg.link_object_files(obj_paths, out_path, cc="cc", debug=bool(getattr(args, 'dump_ll', False)))
    link_time = time.monotonic() - t1

    total_units = len(compilation_order)
    stdlib_count = len(stdlib_units)
    lib_count = len(library_imports)
    print(f"\nCodegen: {total_units} units ({len(cached)} cached, {len(rebuilt)} rebuilt) in {codegen_time:.2f}s")
    link_desc = f"{total_units} units"
    if stdlib_count:
        link_desc += f" + {stdlib_count} stdlib"
    if lib_count:
        link_desc += f" + {lib_count} libs"
    print(f"Linking: {link_desc} in {link_time:.2f}s")

    if args.write_ll:
        print("(note: --write-ll not supported in incremental mode)")

    print(f"Success! Wrote native binary: {out_path}")

    if reporter.has_warnings:
        return 1
    return 0
