"""Multi-file compilation orchestration."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from sushi_lang.compiler.loader import (
    get_effective_cwd,
    load_unit_recursively,
)
from sushi_lang.internals.diagnostics import StdlibBuildError, SushiError
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.ast import Program
from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.units import Unit, UnitManager


def _check_library_platform(metadata: dict, lib_path: str) -> None:
    """Reject a `.slib` carrying bitcode built for a different platform (CE3504).

    A source library states a platform too -- the machine that produced it -- but
    nothing in it is machine code, so the field says nothing about where it can be
    used. Skipping the check for `kind == "source"` is the whole cross-platform fix
    (`TODO.md` A3): the gate used to reject the WHOLE file over one field, so a
    library carrying nothing platform-bound was refused anyway.
    """
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.backend.platform_detect import current_platform_name

    if metadata.get("kind") == "source":
        return

    lib_platform = metadata.get("platform")
    host = current_platform_name()
    # "unknown" on either side means we could not determine a platform; do not block.
    if lib_platform and lib_platform != "unknown" and host != "unknown" and lib_platform != host:
        raise LibraryError("CE3504", lib_platform=lib_platform, current_platform=host)


def _check_library_compiler_version(metadata: dict, lib_path: str,
                                    current: str | None = None,
                                    ignore: bool = False) -> None:
    """Reject a `.slib` whose `requires_compiler` excludes the running compiler (CE3503).

    A source library is built by the CONSUMER's compiler, so an incompatibility is real
    and must be caught here rather than surfacing later as a confusing error inside
    library source the consumer never wrote. The check is SKIPPED, never failed, when
    either version cannot be parsed -- the gate exists to catch a genuine mismatch, not
    to fail a build over a field it could not read.
    """
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.internals.semver import InvalidVersion, Version, VersionReq

    if ignore:
        return

    requires = metadata.get("requires_compiler")
    if not requires:
        return

    if current is None:
        from sushi_lang import __version__
        current = __version__

    try:
        req = VersionReq.parse(requires)
        running = Version.parse(current)
    except InvalidVersion:
        return

    if not req.matches(running):
        raise LibraryError("CE3503",
                           lib=metadata.get("library_name") or lib_path,
                           requires=requires, current=current)


def _check_library_templates_version(metadata: dict, lib_path: str) -> None:
    """Refuse a binary `.slib` whose templates schema is not the current one (CE3512).

    Version 5 keys every closure record by its unit and gives a source-shipped
    template its `bindings` map (#494, D4). A version-4 library's bare-name records
    can bind a template to another unit's body, which is a silent wrong answer, so
    an old library is refused and must be rebuilt -- decision B of the epic. A
    SOURCE library recompiles from its units and reads none of this.
    """
    from sushi_lang.backend.library_errors import LibraryError

    if metadata.get("kind") == "source":
        return
    templates = metadata.get("templates") or {}
    version = templates.get("version")
    if version != 5:
        raise LibraryError(
            "CE3512", path=lib_path,
            reason=(f"templates schema version {version}; this compiler requires 5 "
                    "(rebuild the library)"))


def _inject_source_stdlib_units(unit_manager: UnitManager, reporter: Reporter) -> bool:
    """Merge bundled Sushi-source stdlib modules (e.g. <collections/iter>) as units."""
    from sushi_lang.internals.parser import parse_to_ast
    from sushi_lang.semantics.stdlib_registry import (
        SOURCE_STDLIB_MODULES, resolve_source_stdlib_path,
    )

    def _needed(units) -> set:
        needed = set()
        for unit in units:
            if unit.ast is None:
                continue
            for use_stmt in unit.ast.uses:
                if use_stmt.is_stdlib and use_stmt.path in SOURCE_STDLIB_MODULES:
                    needed.add(use_stmt.path)
        return needed

    while True:
        todo = _needed(list(unit_manager.units.values())) - set(unit_manager.units.keys())
        if not todo:
            return True
        for module_path in sorted(todo):
            src_path = resolve_source_stdlib_path(module_path)
            if src_path is None or not src_path.exists():
                from sushi_lang.internals import errors as er
                er.emit(reporter, er.ERR.CE0007, None,
                        detail=f"bundled stdlib module '{module_path}' not found at {src_path}")
                return False
            module_src = src_path.read_text(encoding="utf-8")
            try:
                module_ast, _ = parse_to_ast(module_src, dump_parse=False)
            except SushiError as e:
                e.filename = e.filename or str(src_path)
                raise
            # A provenance, the same way `_inject_library_source` sets one. A bundled
            # module is code the user did not write: without this the `docs` pass
            # reports OUR doc-block mistakes in every program that imports the module,
            # and every other diagnostic against it arrives unattributed
            # (documentation.md section 10, R24). The repo's own gate is
            # `tests/unit/test_stdlib_doc_blocks.py`.
            unit_manager.units[module_path] = Unit(
                name=module_path, file_path=src_path, ast=module_ast,
                dependencies=[], public_symbols={},
                provenance=(f"'{module_path}' is a bundled stdlib module written in "
                            f"Sushi, compiled here because of `use <{module_path}>`"),
            )


def _resolve_library_imports(unit_manager: UnitManager, reporter: Reporter, args,
                             cache_root: Path) -> tuple[object, set[str]] | None:
    """Resolve every `use <lib/...>`, injecting source libraries as ordinary units.

    A source library is not linked, registered or monomorphized through the library
    registry: its units join the consumer's unit table and the ordinary passes compile
    them, so a private helper stays private through `func.is_public` and there is no
    export closure to compute. A binary library keeps the old path and reaches the
    semantic analyzer through the resolver's `loaded_libraries`.

    Returns (resolver-or-None, binary import paths), or None when a library failed to
    load. A library's OWN `use <lib/...>` is not followed -- transitive library
    dependencies are unsupported, and a consumer states each library it uses.
    """
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.backend.library_format import LibraryFormat
    from sushi_lang.backend.library_paths import LibraryResolver
    from sushi_lang.internals import errors as er

    wanted: set[str] = set()
    for unit in list(unit_manager.units.values()):
        if unit.ast is None or unit.provenance is not None:
            continue
        wanted.update(u.path for u in unit.ast.uses if u.is_library)
    if not wanted:
        return None, set()

    resolver = LibraryResolver()
    binary_imports: set[str] = set()

    print(f"Linking {len(wanted)} custom libraries:")
    for lib_path in sorted(wanted):
        try:
            slib_path = resolver.resolve_library(lib_path)
            metadata = LibraryFormat.read_metadata_only(slib_path)
            _check_library_platform(metadata, lib_path)
            _check_library_compiler_version(
                metadata, lib_path,
                ignore=bool(getattr(args, "ignore_compiler_version", False)))
            _check_library_templates_version(metadata, lib_path)

            if metadata.get("kind") == "source":
                _inject_library_source(unit_manager, slib_path, metadata, lib_path,
                                       cache_root)
            else:
                binary_imports.add(lib_path)
                resolver.loaded_libraries[metadata["library_name"]] = metadata

            print(f"  - {' / '.join(lib_path.split('/'))}")
        except LibraryError as e:
            er.emit_exception(reporter, e)
            return None
        except SushiError:
            raise
    print()

    return (resolver if binary_imports else None), binary_imports


def _inject_library_source(unit_manager: UnitManager, slib_path: Path, metadata: dict,
                           lib_path: str, cache_root: Path) -> None:
    """Write a source library's units to disk and add them to the unit table.

    The units are materialized rather than kept in memory because two things read a
    unit's source off its path: the per-unit Reporter, which needs the text to draw a
    caret, and `compute_unit_fingerprint`, which hashes it to decide what to rebuild.
    A real file also gives the consumer somewhere to look when the error is ours.

    Names are prefixed `lib/<library>/<unit>`, so a library unit can never collide with
    a consumer unit of the same name, and every intra-library dependency is rewritten
    to match.
    """
    from sushi_lang.compiler.cache import CacheManager
    from sushi_lang.internals.parser import parse_to_ast
    from sushi_lang.backend.library_format import LibraryFormat

    _metadata, sources = LibraryFormat.read_source_only(slib_path)
    lib_name = metadata.get("library_name") or slib_path.stem
    version = metadata.get("library_version") or "unknown"
    provenance = (f"'{lib_name}' {version} is a source library, "
                  f"compiled here because of `use <{lib_path}>`")

    out_dir = CacheManager(cache_root).library_source_dir(lib_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    own = set(sources)

    for unit_name, text in sources.items():
        file_path = out_dir / f"{unit_name}.sushi"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if not file_path.exists() or file_path.read_text(encoding="utf-8") != text:
            file_path.write_text(text, encoding="utf-8")

        try:
            module_ast, _tree = parse_to_ast(text, dump_parse=False)
        except SushiError as e:
            e.filename = e.filename or str(file_path)
            raise

        unit = Unit(name=f"lib/{lib_name}/{unit_name}", file_path=file_path,
                    ast=module_ast, dependencies=[], public_symbols={},
                    from_library=True,
                    provenance=provenance)
        unit.dependencies = [f"lib/{lib_name}/{d}" for d in unit.dependencies if d in own]
        unit_manager.units[unit.name] = unit


def compile_multi_file(main_ast: Program, src_path: Path, reporter: Reporter,
                       args, is_library: bool = False) -> int:
    """Handle multi-file compilation when use statements are present."""
    main_unit_name = src_path.stem
    unit_manager = UnitManager(root_path=src_path.parent, reporter=reporter)

    main_unit = unit_manager.load_unit(main_unit_name, main_ast)
    if main_unit is None:
        return 2
    main_unit.is_entry = True

    loaded_units = {main_unit_name}
    for dep_name in main_unit.dependencies:
        if not load_unit_recursively(unit_manager, dep_name, loaded_units, reporter):
            return 2

    assert len(loaded_units) == len(unit_manager.units), \
        f"Unit count mismatch: loaded {len(loaded_units)} units but manager has {len(unit_manager.units)}"
    for unit_name in loaded_units:
        assert unit_name in unit_manager.units, \
            f"Unit '{unit_name}' was loaded but not found in unit manager"

    # Libraries resolve BEFORE the symbol table and before the stdlib injector: a
    # source library's units have to be in the table when it is built, and a bundled
    # module the LIBRARY uses still needs injecting.
    resolved = _resolve_library_imports(unit_manager, reporter, args,
                                        src_path.resolve().parent)
    if resolved is None:
        return 2
    library_linker, library_imports = resolved

    if not _inject_source_stdlib_units(unit_manager, reporter):
        return 2

    compilation_order = unit_manager.get_compilation_order()
    if compilation_order is None:
        return 2

    stdlib_units = set()
    for unit in compilation_order:
        if unit.ast:
            for use_stmt in unit.ast.uses:
                if use_stmt.is_stdlib:
                    stdlib_units.add(use_stmt.path)

    if len(compilation_order) > 1:
        print(f"Found {len(compilation_order)} units:")
        for unit in compilation_order:
            print(f"  - {unit.name} ({len(unit.public_symbols)} public symbols)")
        print()

    if stdlib_units:
        # Auto-build the current platform's stdlib bitcode if missing or if a
        # generator source changed, so we never link stale/absent .bc.
        from sushi_lang.backend.stdlib_builder import ensure_stdlib_built
        try:
            ensure_stdlib_built()
        except SushiError:
            raise
        except Exception as e:
            raise StdlibBuildError("CE0007", detail=str(e)) from e

        from sushi_lang.backend.codegen_llvm import LLVMCodegen
        temp_cg = LLVMCodegen()
        try:
            for unit_path in stdlib_units:
                temp_cg.stdlib._resolve_stdlib_unit(unit_path)
        except FileNotFoundError:
            from sushi_lang.internals import errors as er
            er.emit(reporter, er.ERR.CE3006, None, module=unit_path)
            return 2

        print(f"Linking {len(stdlib_units)} stdlib units:")
        for unit_path in sorted(stdlib_units):
            formatted_path = "stdlib / " + " / ".join(unit_path.split('/'))
            print(f"  - {formatted_path}")
        print()

    from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
    get_stdlib_registry()

    from sushi_lang.backend.stdlib_builder import read_generated_symbols

    generated_symbols = read_generated_symbols()
    if not generated_symbols and any(
            unit.ast is not None and unit.ast.externals for unit in compilation_order):
        # CE5013 refuses a generated stdlib symbol whether the program links that unit
        # or not (#472), so the list may not depend on whether the platform happens to
        # be built. Only a program that declares FFI pays for it, and only once.
        from sushi_lang.backend.stdlib_builder import ensure_stdlib_built
        try:
            ensure_stdlib_built()
        except SushiError:
            raise
        except Exception as e:
            raise StdlibBuildError("CE0007", detail=str(e)) from e
        generated_symbols = read_generated_symbols()

    multi_file_analyzer = SemanticAnalyzer(
        reporter, filename=main_unit_name, unit_manager=unit_manager,
        library_linker=library_linker,
        warn_missing_docs=bool(getattr(args, "warn_missing_docs", False)),
        generated_symbols=generated_symbols)
    multi_file_analyzer.check(main_ast)

    # A library must not carry main(): --lib used to embed it into the .slib silently,
    # where it collides at link time in every consumer. Reject it here (CE3501).
    #
    # An executable must carry one, and that is the mirror image (CE3007). Without the
    # check the missing `_main` symbol reached the LINKER, so the user got raw `cc`
    # stderr and then a CE0000 "this is a compiler bug" -- for a condition in their own
    # program (#251).
    from sushi_lang.internals import errors as er
    if is_library:
        for unit in compilation_order:
            if unit.ast is None:
                continue
            for func in unit.ast.functions:
                if func.name == "main":
                    er.emit(reporter, er.ERR.CE3501, func.name_span)
        # A library that extends a type it does not declare claims the method
        # name for every consumer (CW3003). The build proceeds: a warning names
        # the hazard and stops nothing.
        from sushi_lang.backend.library_manifest import own_units
        from sushi_lang.semantics.foreign_extensions import foreign_extension_claims
        for claim in foreign_extension_claims(own_units(compilation_order)):
            er.emit_with(reporter, er.ERR.CW3003, claim.span,
                         filename=claim.filename, type=claim.target).emit()
    elif not any(func.name == "main"
                 for unit in compilation_order if unit.ast is not None
                 for func in unit.ast.functions):
        er.emit_with(reporter, er.ERR.CE3007, None) \
            .help("add `fn main() i32:` to the program, or compile it as a library "
                  "with `--lib`").emit()

    if reporter.has_errors:
        return 2

    use_incremental = (
        len(compilation_order) > 1
        and not is_library
        and not getattr(args, 'no_incremental', False)
        and not getattr(args, 'dump_ll', False)
    )

    if use_incremental:
        return _compile_incremental(
            compilation_order, multi_file_analyzer, src_path, reporter, args,
            stdlib_units, library_imports, library_linker, unit_manager,
        )
    return _compile_monolithic(
        compilation_order, multi_file_analyzer, src_path, reporter, args,
        is_library, stdlib_units, library_imports, library_linker,
    )


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
    external_table = getattr(analyzer, 'externals', None)
    if external_table is not None:
        cg.external_table = external_table
    # Section 8's ladder, as the back end has to walk it: a bare callee is resolved
    # through the same per-unit scope the typecheck pass accepted it under.
    cg.unit_scopes = {name: table.scope
                      for name, table in getattr(analyzer, 'namespaces', {}).items()}

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
        # Extract the templates section FIRST: the export closure decides
        # which private functions must carry external (not internal) linkage
        # in the bitcode (their definitions resolve consumer call sites at
        # link time), and any CE5006 rejection aborts before the expensive
        # bitcode compilation.
        from sushi_lang.backend.library_manifest import (
            LibraryManifestGenerator, collect_unit_source, resolve_library_version,
        )
        # Resolve the library's own version FIRST: a missing or contradicted version is
        # CE3505, and there is no point compiling bitcode for a library that cannot be
        # stamped (the same reasoning as the export closure below).
        library_version = resolve_library_version(
            src_path.resolve().parent, args.lib_version, out_path.stem)
        manifest_gen = LibraryManifestGenerator(analyzer)
        templates = manifest_gen._extract_templates(compilation_order)
        # A rejected export closure (CE5006) ends the build HERE, before the expensive
        # bitcode compilation. The producer emits and returns; this gate is what stops
        # the build, the same way the pre-codegen gate above does (#436).
        if reporter.has_errors:
            return 2
        # The SYMBOLS, not the names: a private helper's symbol carries its unit, and
        # the promotion below looks it up in the emitted module by symbol. The manifest
        # record that the consumer reads and this set are the same field, so the two
        # cannot drift.
        closure_fn_symbols = {
            record["link_symbol"]
            for record in templates.get("private_functions", []) or []
            if record.get("link_symbol")
        }

        kind = args.lib_kind
        # A source library needs no bitcode at all. A hybrid still compiles it, and so
        # does a binary library, which is what every build produced before v4.
        if kind == "source":
            bitcode = b""
        else:
            bitcode = cg.compile_to_bitcode(compilation_order,
                                            debug=bool(args.dump_ll), opt=args.opt,
                                            verify=not args.no_verify,
                                            monomorphized_extensions=monomorphized_extensions,
                                            exported_private_functions=closure_fn_symbols)

        source = collect_unit_source(compilation_order) if kind != "binary" else None

        manifest_gen.generate(compilation_order, out_path, bitcode, templates=templates,
                              library_version=library_version, kind=kind, source=source)
        # CE0116 and CE5002 are found while the public API is extracted, so the second
        # gate is here. `generate()` wrote nothing; this is what keeps the success line
        # from following a diagnostic.
        if reporter.has_errors:
            return 2

        if args.write_ll:
            try:
                ll_path = out_path.with_suffix(".ll")
                ll_path.write_text(str(cg.module), encoding="utf-8")
                print(f"wrote LLVM IR: {ll_path}")
            except Exception as e:
                print(f"(warn) failed to write LLVM IR: {e}", file=sys.stderr)

        print(f"Success! Wrote library: {out_path}")
    else:
        cg.library_perk_impls = getattr(analyzer, 'library_perk_impls', [])
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

    cache.prepare()

    monomorphized_extensions = getattr(analyzer, 'monomorphized_extensions', [])

    struct_table = getattr(analyzer, 'structs', None)
    enum_table = getattr(analyzer, 'enums', None)
    func_table = getattr(analyzer, 'funcs', None)
    const_table = getattr(analyzer, 'constants', None)
    perk_impl_table = getattr(analyzer, 'perk_impls', None)
    cg = LLVMCodegen(struct_table=struct_table, enum_table=enum_table,
                     func_table=func_table, perk_impl_table=perk_impl_table,
                     const_table=const_table)
    external_table = getattr(analyzer, 'externals', None)
    if external_table is not None:
        cg.external_table = external_table
    # Section 8's ladder, as the back end has to walk it: a bare callee is resolved
    # through the same per-unit scope the typecheck pass accepted it under.
    cg.unit_scopes = {name: table.scope
                      for name, table in getattr(analyzer, 'namespaces', {}).items()}
    cg.main_expects_args = analyzer.main_expects_args
    cg.monomorphized_extensions = monomorphized_extensions
    cg.library_linker = library_linker
    cg.library_registry = getattr(analyzer, 'library_registry', None)
    cg.library_perk_impls = getattr(analyzer, 'library_perk_impls', [])

    obj_paths: list[Path] = []
    rebuilt = []
    cached = []

    t0 = time.monotonic()

    print("Code generation:")

    # Digest every imported `.slib` once. These feed both the consumer unit
    # fingerprints (so a library template change invalidates consumers that
    # monomorphize it -- cross-library generics) and the library `.o`
    # cache below.
    library_fingerprints: dict[str, str] = {}
    if library_linker is not None:
        for lib_path in sorted(library_imports):
            slib_path = library_linker.resolve_library(lib_path)
            library_fingerprints[lib_path] = compute_lib_fingerprint(slib_path)

    # Whole-program, so it is computed once and folded into every unit.
    drop_types = frozenset(cg.perk_impl_table.by_perk.get("Drop", ()))

    for unit in compilation_order:
        fp = compute_unit_fingerprint(
            unit, unit_manager, monomorphized_extensions,
            library_fingerprints=library_fingerprints,
            drop_types=drop_types,
        )

        if cache.has_cached_unit(unit.name, fp):
            obj_path = cache.unit_object_path(unit.name, fp)
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

    for stdlib_unit in sorted(stdlib_units):
        bc_paths = cg.stdlib._resolve_stdlib_unit(stdlib_unit)
        if not bc_paths:
            # Virtual/source stdlib unit (e.g. collections/hashmap, collections/iter):
            # no .bc to compile -- generic types are emitted inline and source
            # modules ship as compilation units. Nothing to cache or link here.
            continue
        fp = compute_stdlib_fingerprint(bc_paths)
        if cache.has_cached_stdlib(stdlib_unit, fp):
            obj_paths.append(cache.stdlib_object_path(stdlib_unit, fp))
        else:
            obj_bytes = cg.compile_stdlib_to_object(stdlib_unit, opt=args.opt)
            obj_path = cache.store_stdlib_object(stdlib_unit, obj_bytes, fp)
            obj_paths.append(obj_path)

    if library_linker is not None:
        for lib_path in sorted(library_imports):
            slib_path = library_linker.resolve_library(lib_path)
            fp = library_fingerprints.get(lib_path) or compute_lib_fingerprint(slib_path)
            lib_name = lib_path.replace("/", "_")
            if cache.has_cached_lib(lib_name, fp):
                obj_paths.append(cache.lib_object_path(lib_name, fp))
            else:
                obj_bytes = cg.compile_library_to_object(lib_path, library_linker, opt=args.opt)
                obj_path = cache.store_lib_object(lib_name, obj_bytes, fp)
                obj_paths.append(obj_path)

    codegen_time = time.monotonic() - t0

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
