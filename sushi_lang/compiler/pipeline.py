"""Multi-file compilation orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional
import errno
import os
import time
from pathlib import Path

from sushi_lang.compiler.loader import (
    load_unit_recursively,
)
from sushi_lang.compiler.cache import CacheManager, publish_atomically
from sushi_lang.internals.diagnostics import StdlibBuildError, SushiError
from sushi_lang.internals.host import host_key
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.ast import Program
from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.units import Unit, UnitManager
from sushi_lang.semantics.unit_reuse import UnitReuse

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.backend.link_plan import LinkInputs
    from sushi_lang.compiler.options import BuildOptions


def _check_library_name(metadata: dict, lib_path: str, slib_path: Path) -> None:
    """Refuse a `.slib` whose stamped name is not the name of the `use` (CE3521).

    The name of a `use <lib/...>` is the last part of its path, and a build stamps the
    stem of its output file. A copied or renamed file has another name than its stamp.
    The check runs before the graph looks the name up, so a copy beside its original is
    this error and not two versions of one library.
    """
    from sushi_lang.backend.library_errors import LibraryError

    lib = lib_path.rsplit("/", 1)[-1]
    built = metadata["library_name"]
    if built == lib:
        return
    error = LibraryError("CE3521", use_path=lib_path, lib=lib, path=str(slib_path),
                         built=built)
    own_path = f"{lib_path.rsplit('/', 1)[0]}/{built}" if "/" in lib_path else built
    error.help(f"build the library again with the name '{lib}' (`-o {lib}.slib`), or "
               f"write `use <{own_path}>`, with the library's own name")
    raise error


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


def _inject_source_stdlib_units(unit_manager: UnitManager, reporter: Reporter) -> bool:
    """Merge the bundled Sushi-source stdlib modules that the build needs, as units.

    A module is needed when a unit imports it, and when a unit names a method that the
    module declares on a built-in type. Such a method
    needs no import (`docs/design/extension-visibility.md` R1), so its module is loaded
    with no `use`. That load brings no name to a unit (R2).
    """
    from sushi_lang.internals.parser import parse_to_ast
    from sushi_lang.semantics.stdlib_registry import (
        BUILTIN_TYPE_METHOD_MODULES, PLATFORM_SOURCE_MODULES, SOURCE_STDLIB_MODULES,
        builtin_type_method_names, get_stdlib_registry, keep_builtin_type_methods,
        may_declare_methods, named_methods, platform_source,
        resolve_source_stdlib_path, stdlib_imports,
    )

    texts: dict[str, str] = {}
    sources: dict[str, tuple[Path, str, Program]] = {}
    named: dict[str, frozenset[str]] = {}

    def _imported(units) -> set:
        needed = set()
        paths = [path for unit in units if unit.ast is not None
                 for path in stdlib_imports(unit.ast)]
        for path in paths:
            if path in SOURCE_STDLIB_MODULES:
                needed.add(path)
                continue
            # A registry module's re-exports (`StdlibModule.reexports`) ride with
            # its import, so a source module one of them names is compiled too.
            module = get_stdlib_registry().get_module(path)
            for other in getattr(module, "reexports", ()):
                if other in SOURCE_STDLIB_MODULES:
                    needed.add(other)
        return needed

    def _for_methods(units) -> set:
        names = set().union(*(named.setdefault(unit.name, named_methods(unit.ast))
                              for unit in units if unit.ast is not None))
        return {module for module in BUILTIN_TYPE_METHOD_MODULES
                if may_declare_methods(_text(module), names)
                and builtin_type_method_names(_source(module)[2]) & names}

    def _text(module_path: str) -> str:
        if module_path not in texts:
            texts[module_path] = SOURCE_STDLIB_MODULES[module_path].read_text(
                encoding="utf-8")
        return texts[module_path]

    def _source(module_path: str) -> tuple[Path, str, Program]:
        if module_path not in sources:
            src_path = SOURCE_STDLIB_MODULES[module_path]
            module_src = _text(module_path)
            try:
                module_ast, _ = parse_to_ast(module_src, dump_parse=False)
            except SushiError as e:
                e.filename = e.filename or str(src_path)
                raise
            sources[module_path] = (src_path, module_src, module_ast)
        return sources[module_path]

    def _inject(module_path: str, for_methods: bool) -> bool:
        src_path = resolve_source_stdlib_path(module_path)
        if module_path in PLATFORM_SOURCE_MODULES and platform_source(module_path) is None:
            from sushi_lang.internals import errors as er
            er.emit(reporter, er.ERR.CE3021, None, module=module_path,
                    host=host_key(),
                    hosts=", ".join(sorted(PLATFORM_SOURCE_MODULES[module_path])))
            return False
        if src_path is None or not src_path.exists():
            from sushi_lang.internals import errors as er
            er.emit(reporter, er.ERR.CE0007, None,
                    detail=f"bundled stdlib module '{module_path}' not found at {src_path}")
            return False
        src_path, module_src, module_ast = _source(module_path)
        reason = f"because of `use <{module_path}>`"
        if for_methods:
            keep_builtin_type_methods(module_ast)
            reason = "for the methods that it declares on the built-in types"
        # A provenance, the same way `_inject_library_source` sets one. A bundled
        # module is code the user did not write: without this the `docs` pass
        # reports OUR doc-block mistakes in every program that imports the module,
        # and every other diagnostic against it arrives unattributed
        # (documentation.md section 10, R24).
        unit_manager.units[module_path] = Unit(
            name=module_path, file_path=src_path, ast=module_ast,
            dependencies=[], public_symbols={}, source=module_src,
            provenance=(f"'{module_path}' is a bundled stdlib module written in "
                        f"Sushi, compiled here {reason}"),
        )
        return True

    # The imports first, to the end of their chain: an imported module is loaded whole.
    # Then the modules of the methods of the built-in types, and what those use. Such a
    # module is loaded for its methods alone, because no unit imported it.
    for_methods: set[str] = set()
    while True:
        units = list(unit_manager.units.values())
        todo = _imported(units) - set(unit_manager.units)
        if not todo:
            break
        if not all(_inject(module_path, False) for module_path in sorted(todo)):
            return False
    while True:
        units = list(unit_manager.units.values())
        todo = (_for_methods(units)
                | _imported([unit for unit in units if unit.name in for_methods])
                ) - set(unit_manager.units)
        if not todo:
            return True
        for_methods.update(todo)
        if not all(_inject(module_path, True) for module_path in sorted(todo)):
            return False


_Origin = tuple[str, Optional[Span], Optional[str]]


@dataclass
class _Wanted:
    """One `use <lib/...>` of the graph: the path, how it was reached, what it expects.

    `recorded_version` is the version a library recorded for the path, with the note that says
    which library recorded it. The consumer's own `use` expects nothing.
    """
    path: str
    origin: _Origin
    recorded_version: Optional[tuple[str, str]] = None


@dataclass
class _Loaded:
    """One library of the graph, by its stamped name: the version, and how it came in."""
    version: str
    slib_path: Path
    origin: _Origin

    def found(self) -> tuple[str, _Origin]:
        """The version, and the note that says which path reached it and where."""
        text, span, filename = self.origin
        return self.version, (f"{text}, and the search finds {self.version} at "
                              f"{self.slib_path}", span, filename)


def _add_origin_note(error: SushiError, origin: _Origin) -> None:
    text, span, filename = origin
    if span is not None:
        error.note_at(text, span, filename)
    else:
        error.note(text)


def _two_versions(name: str, first: tuple[str, _Origin],
                  second: tuple[str, _Origin]) -> SushiError:
    """CE3519: one library at two versions in one graph, with the path to each."""
    from sushi_lang.backend.library_errors import LibraryError

    error = LibraryError("CE3519", lib=name, first=first[0], second=second[0])
    _add_origin_note(error, first[1])
    _add_origin_note(error, second[1])
    return error


def _check_expected(name: str, loaded: _Loaded, wanted: _Wanted) -> None:
    """Refuse a dependency record whose version is not the one the graph holds."""
    if wanted.recorded_version is None:
        return
    version, recorded_by = wanted.recorded_version
    if version != loaded.version:
        raise _two_versions(name, (version, (recorded_by, None, None)), loaded.found())


def _consumer_wants(unit_manager: UnitManager) -> list[_Wanted]:
    """Every `use <lib/...>` that a unit of the consumer writes, in written order."""
    wants = []
    for unit in list(unit_manager.units.values()):
        if unit.ast is None or unit.provenance is not None:
            continue
        for use in unit.ast.uses:
            if use.is_library:
                note = (f"'{unit.name}' imports it with `{_spelled(use)}`",
                        use.loc, str(unit.file_path))
                wants.append(_Wanted(use.path, note))
    return wants


def _spelled(use) -> str:
    return f"{'public ' if use.is_public else ''}use <{use.path}>"


def _resolve_library_imports(unit_manager: UnitManager, reporter: Reporter,
                             ignore_compiler_version: bool, cache: CacheManager) -> bool:
    """Load the whole library graph: every `use <lib/...>`, transitively (#1120).

    LOADING is transitive. A library records every `use <lib/...>` that its units write,
    plain or public, and the build loads each one, so a library's body has what it
    calls. VISIBILITY is not: the `namespaces` pass gives a unit the names of what it
    imports itself and of what a `public use` hands on, and nothing that a plain `use`
    of a library loaded.

    A library is identified by the `library_name` stamped into its `.slib`. Two paths to
    one library load it once; two versions of one library are CE3519. Each library is
    injected as ordinary units.

    Every `use <lib/...>` of every unit is then stamped with the name and the version it
    found. False when a library failed to load.
    """
    from sushi_lang.backend.library_errors import LibraryError
    from sushi_lang.backend.library_format import LibraryFormat, check_manifest
    from sushi_lang.backend.library_paths import LibraryResolver
    from sushi_lang.internals import errors as er

    todo = _consumer_wants(unit_manager)
    if not todo:
        return True

    resolver = LibraryResolver()
    names: dict[str, str] = {}
    loaded: dict[str, _Loaded] = {}

    print("Linking custom libraries:")
    while todo:
        wanted = todo.pop(0)
        try:
            known = names.get(wanted.path)
            if known is not None:
                _check_expected(known, loaded[known], wanted)
                continue
            try:
                slib_path = resolver.resolve_library(wanted.path)
            except LibraryError as e:
                _add_origin_note(e, wanted.origin)
                raise
            metadata = LibraryFormat.read_metadata_only(slib_path)
            check_manifest(metadata, str(slib_path))
            try:
                _check_library_name(metadata, wanted.path, slib_path)
            except LibraryError as e:
                _add_origin_note(e, wanted.origin)
                raise
            name = metadata["library_name"]
            version = metadata["library_version"]
            names[wanted.path] = name
            if name in loaded:
                first = loaded[name]
                if first.version != version:
                    raise _two_versions(name, first.found(),
                                        _Loaded(version, slib_path, wanted.origin).found())
                _check_expected(name, first, wanted)
                continue
            loaded[name] = _Loaded(version, slib_path, wanted.origin)
            _check_expected(name, loaded[name], wanted)
            _check_library_compiler_version(metadata, wanted.path,
                                            ignore=ignore_compiler_version)

            injected = _inject_library_source(unit_manager, slib_path, metadata,
                                              wanted.path, cache)
            todo.extend(_source_library_dependencies(metadata, injected))
            print(f"  - {' / '.join(wanted.path.split('/'))}")
        except LibraryError as e:
            er.emit_exception(reporter, e)
            return False
    print()

    _stamp_library_uses(unit_manager, names, loaded)
    return True


def _stamp_library_uses(unit_manager: UnitManager, names: dict[str, str],
                        loaded: dict[str, _Loaded]) -> None:
    """Stamp each `use <lib/...>` with the name and the version of what it found."""
    for unit in unit_manager.units.values():
        if unit.ast is None:
            continue
        for use in unit.ast.uses:
            name = names.get(use.path) if use.is_library else None
            if name is not None:
                use.library_name = name
                use.library_version = loaded[name].version


def _manifest_dependencies(manifest: dict) -> tuple[dict, ...]:
    """Every `dependencies` record of a manifest (#1120): what the library's units use."""
    return tuple(record for record in manifest.get("dependencies") or ()
                 if isinstance(record, dict))


def _library_dependencies(manifest: dict) -> tuple[dict, ...]:
    """The `dependencies` records that name another library."""
    return tuple(record for record in _manifest_dependencies(manifest)
                 if record.get("kind") == "library" and record.get("path"))


def _recorded_versions(metadata: dict) -> dict[str, str]:
    """The version a library's build found for each `lib/...` path it uses."""
    return {record["path"]: record["library_version"]
            for record in _library_dependencies(metadata)
            if record.get("library_version")}


def _source_library_dependencies(metadata: dict, units: list[Unit]) -> list[_Wanted]:
    """Every `use <lib/...>` of a source library's units, plain or public, with the note.

    The statement is in the text, so the note points at it. The version comes from the
    manifest record, which a library built before #1120 does not carry.
    """
    name = metadata.get("library_name") or "?"
    versions = _recorded_versions(metadata)
    wants = []
    for unit in units:
        if unit.ast is None:
            continue
        for use in unit.ast.uses:
            if not use.is_library:
                continue
            note = (f"'{name}' needs it, with `{_spelled(use)}`", use.loc,
                    str(unit.file_path))
            version = versions.get(use.path)
            expected = None if version is None else (
                version, f"'{name}' was built against {version}, with `{_spelled(use)}`")
            wants.append(_Wanted(use.path, note, expected))
    return wants


def _inject_library_source(unit_manager: UnitManager, slib_path: Path, metadata: dict,
                           lib_path: str, cache: CacheManager) -> list[Unit]:
    """Write a source library's units to disk and add them to the unit table.

    The units are materialized rather than kept in memory because the per-unit
    Reporter reads a unit's source off its path to draw a caret. A real file also gives
    the consumer somewhere to look when the error is ours. The cache key does not read
    the file: it hashes `Unit.source`, the text that was parsed. Two libraries of one
    name share the directory, so the file is published atomically.

    Names are prefixed `lib/<library>/<unit>`, so a library unit can never collide with
    a consumer unit of the same name, and every intra-library dependency is rewritten
    to match.
    """
    from sushi_lang.internals.parser import parse_to_ast
    from sushi_lang.backend.library_format import LibraryFormat
    from sushi_lang.compiler.fingerprint import compute_lib_fingerprint
    from sushi_lang.semantics.unit_reuse import label_library_file

    _metadata, sources = LibraryFormat.read(slib_path)
    digest = compute_lib_fingerprint(slib_path)
    lib_name = metadata.get("library_name") or slib_path.stem
    version = metadata.get("library_version") or "unknown"
    provenance = (f"'{lib_name}' {version} is a source library, "
                  f"compiled here because of `use <{lib_path}>`")

    from sushi_lang.backend.driver import writing_output

    out_dir = cache.library_source_dir(lib_name)
    with writing_output(out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
    own = set(sources)
    injected: list[Unit] = []

    for unit_name, text in sources.items():
        file_path = out_dir / f"{unit_name}.sushi"
        if not file_path.exists() or file_path.read_text(encoding="utf-8") != text:
            publish_atomically(file_path, text.encode("utf-8"))

        try:
            module_ast, _tree = parse_to_ast(text, dump_parse=False)
        except SushiError as e:
            e.filename = e.filename or str(file_path)
            raise

        unit = Unit(name=f"lib/{lib_name}/{unit_name}", file_path=file_path,
                    ast=module_ast, dependencies=[], public_symbols={},
                    from_library=True, source=text,
                    provenance=provenance, library_digest=digest)
        label_library_file(file_path, unit.name)
        unit.dependencies = [f"lib/{lib_name}/{d}" for d in unit.dependencies if d in own]
        unit_manager.units[unit.name] = unit
        injected.append(unit)
    return injected


def build_stdlib(rebuild: bool = False) -> None:
    """Build the platform's stdlib bitcode: always with `rebuild`, else only when it is
    missing or a generator source changed, so a build never links stale or absent .bc.
    A generator failure is CE0007."""
    try:
        if rebuild:
            from sushi_lang.backend.stdlib_builder import detect_platform
            from sushi_lang.sushi_stdlib.build import build_all
            build_all(detect_platform())
        else:
            from sushi_lang.backend.stdlib_builder import ensure_stdlib_built
            ensure_stdlib_built()
    except SushiError:
        raise
    except Exception as e:
        raise StdlibBuildError("CE0007", detail=str(e)) from e


def _resolve_out_path(options: BuildOptions, src_path: Path) -> Path:
    """The one output path and its one check, after the analysis and before code
    generation: `-o` against the user's directory, else the source's stem there (`.slib`
    for a library). A library path needs the `.slib` extension (CE3500). The object, the
    `.ll` and the `.slib` are written beside the path, so a directory that does not exist
    is CE3019, and a path that is a directory or is in a directory that cannot be written
    is CE3020."""
    from sushi_lang.compiler.cli import COMMAND_LINE

    if options.lib and options.out and not options.out.endswith(".slib"):
        raise SushiError("CE3500", filename=COMMAND_LINE, path=options.out)
    if options.out:
        out_path = Path(options.out).resolve()
    else:
        suffix = ".slib" if options.lib else ""
        out_path = Path.cwd() / (src_path.stem + suffix)
    if not out_path.parent.is_dir():
        raise SushiError("CE3019", filename=COMMAND_LINE, path=options.out,
                         directory=out_path.parent)
    refusal = None
    if out_path.is_dir():
        refusal = errno.EISDIR
    elif not os.access(out_path.parent, os.W_OK):
        refusal = errno.EACCES
    if refusal is not None:
        raise SushiError("CE3020", filename=COMMAND_LINE, path=options.out or out_path,
                         reason=os.strerror(refusal))
    return out_path


def _load_units(main_ast: Program, src_path: Path, reporter: Reporter) -> UnitManager | None:
    """The entry unit and every unit it imports, transitively."""
    main_unit_name = src_path.stem
    unit_manager = UnitManager(root_path=src_path.parent, reporter=reporter)

    main_unit = unit_manager.load_unit(main_unit_name, main_ast, source=reporter.source)
    if main_unit is None:
        return None
    main_unit.is_entry = True

    loaded_units = {main_unit_name}
    for dep_name in main_unit.dependencies:
        if not load_unit_recursively(unit_manager, dep_name, loaded_units, reporter):
            return None
    return unit_manager


def _stdlib_units(compilation_order: list[Unit],
                  reporter: Reporter) -> set[str] | None:
    """Every stdlib module the program links, built and resolved (CE3006 when unknown)."""
    from sushi_lang.backend.stdlib_linker import linked_stdlib_paths
    from sushi_lang.semantics.stdlib_registry import named_methods, stdlib_imports
    programs = [unit.ast for unit in compilation_order if unit.ast]
    stdlib_units = set(linked_stdlib_paths(
        [path for program in programs for path in stdlib_imports(program)],
        frozenset().union(*(named_methods(program) for program in programs))))

    if len(compilation_order) > 1:
        print(f"Found {len(compilation_order)} units:")
        for unit in compilation_order:
            print(f"  - {unit.name} ({len(unit.public_symbols)} public symbols)")
        print()

    if stdlib_units:
        build_stdlib()

        from sushi_lang.backend.codegen_llvm import LLVMCodegen
        from sushi_lang.semantics.passes.collect import PerkImplementationTable
        # This codegen resolves stdlib paths and emits nothing, so no drops question
        # reaches its empty perk table.
        temp_cg = LLVMCodegen(perk_impl_table=PerkImplementationTable())
        try:
            for unit_path in stdlib_units:
                temp_cg.stdlib._resolve_stdlib_unit(unit_path)
        except FileNotFoundError:
            from sushi_lang.internals import errors as er
            er.emit(reporter, er.ERR.CE3006, None, module=unit_path)
            return None

        print(f"Linking {len(stdlib_units)} stdlib units:")
        for unit_path in sorted(stdlib_units):
            formatted_path = "stdlib / " + " / ".join(unit_path.split('/'))
            print(f"  - {formatted_path}")
        print()
    return stdlib_units


def _generated_symbols(compilation_order: list[Unit]):
    """The generated stdlib symbols CE5013 refuses.

    CE5013 refuses a generated stdlib symbol whether the program links that unit or not
    (#472), so the list may not depend on whether the platform happens to be built. Only
    a program that declares FFI pays for a build, and only once.
    """
    from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
    get_stdlib_registry()

    from sushi_lang.backend.stdlib_builder import read_generated_symbols

    generated_symbols = read_generated_symbols()
    if not generated_symbols and any(
            unit.ast is not None and unit.ast.externals for unit in compilation_order):
        build_stdlib()
        generated_symbols = read_generated_symbols()
    return generated_symbols


def _unit_reuse(options: BuildOptions, cache: CacheManager, incremental: bool,
                unit_manager: UnitManager) -> Optional[UnitReuse]:
    """The reuse decisions of this build, or None when no unit may skip its body passes.

    A build that dumps SHIR or SLIR prints each body, and a build that is not incremental
    (`--lib`, `--no-incremental`, `--dump-ll`, a program of one unit) emits each body in
    one module, so each needs the body passes of every unit. A per-user cache that is not
    the user's alone is not read: then only the source library units, whose objects are in
    the project cache, are reused.
    """
    from sushi_lang.compiler.fingerprint import compute_unit_fingerprint
    if not incremental or options.dump_shir is not None or options.dump_slir is not None:
        return None

    def key_of(unit: Unit, copies: list, drops: frozenset) -> str:
        return compute_unit_fingerprint(unit, unit_manager, copies, drop_types=drops)
    return UnitReuse(cache.user_stdlib_path, cache.library_reuse_path, key_of)


def _analyse(unit_manager: UnitManager, compilation_order: list[Unit],
             reporter: Reporter, entry_name: str, options: BuildOptions,
             reuse: Optional[UnitReuse] = None) -> SemanticAnalyzer:
    """Every semantic pass over the whole program."""
    analyzer = SemanticAnalyzer(
        reporter, filename=entry_name, unit_manager=unit_manager,
        lints=options.lints,
        generated_symbols=_generated_symbols(compilation_order), is_library=options.lib,
        dont_panic=options.dont_panic, overflow=options.overflow, reuse=reuse)
    analyzer.check()
    if options.dont_panic and not analyzer.marked and not reporter.has_errors:
        from sushi_lang.compiler.cli import COMMAND_LINE
        from sushi_lang.internals import errors as er
        er.emit(reporter, er.ERR.CW0003, None, filename=COMMAND_LINE, flag="--dont-panic",
                reason="on a build that marks no function")
    return analyzer


def _write_shir_dump(analyzer: SemanticAnalyzer, target: str, reporter: Reporter) -> None:
    """`--dump-shir`: the SHIR of each body that the scope pass checked, as UTF-8 text.

    The analyzer lowers every unit. The dump prints the units of the program and leaves out each
    unit that has a provenance (a bundled stdlib unit, a source library unit), so a golden
    file does not change when the stdlib changes. Each body prints with the answers in its
    side tables (docs/design/ir.md 7.3, 7.9).
    """
    from sushi_lang.semantics.shir.printer import print_units

    shir = analyzer.shir
    units = [(lowered.unit.name,
              [(body, shir.typeck_of(body), shir.ownership_table_of(body), shir.lift_table_of(body))
               for body in lowered.bodies()])
             for lowered in shir.units if lowered.unit.provenance is None]
    _write_dump("SHIR", print_units(units, shir.decls), target, reporter)


def _write_slir_dump(analyzer: SemanticAnalyzer, target: str, reporter: Reporter) -> None:
    """`--dump-slir`: the SLIR of each body that reached the SLIR stage, as UTF-8 text.

    The units and the bodies are those of `--dump-shir`, in the same order. A body that
    reached the SLIR stage prints its function; a body that did not reach it (a template
    check, a body that a pass refused) does not print (docs/design/ir.md 8.2).
    """
    from sushi_lang.semantics.slir.printer import print_units

    slir = analyzer.slir
    units = [(lowered.unit.name,
              [outcome for outcome in (slir.outcome_of(body.fn_id) for body in lowered.bodies())
               if outcome is not None])
             for lowered in analyzer.shir.units if lowered.unit.provenance is None]
    _write_dump("SLIR", print_units(units), target, reporter)


def _write_dump(kind: str, text: str, target: str, reporter: Reporter) -> None:
    """Write the text of one dump to `target`; `-` prints it on stdout."""
    from sushi_lang.compiler.cli import COMMAND_LINE
    from sushi_lang.internals import errors as er

    if target == "-":
        print(text, end="")
        return
    try:
        Path(target).write_text(text, encoding="utf-8")
    except OSError as exc:
        er.emit(reporter, er.ERR.CW0005, None, filename=COMMAND_LINE, dump=kind, path=target,
                reason=exc.strerror or str(exc))


def compile_multi_file(main_ast: Program, src_path: Path, reporter: Reporter,
                       options: BuildOptions) -> int:
    """Compile a program or a library: load, resolve libraries, analyse, gate, emit."""
    unit_manager = _load_units(main_ast, src_path, reporter)
    if unit_manager is None:
        return 2

    # Libraries resolve BEFORE the symbol table and before the stdlib injector: a
    # source library's units have to be in the table when it is built, and a bundled
    # module the LIBRARY uses still needs injecting.
    cache = CacheManager.for_run(options, src_path.parent)
    if not _resolve_library_imports(unit_manager, reporter,
                                    options.ignore_compiler_version, cache):
        return 2

    if not _inject_source_stdlib_units(unit_manager, reporter):
        return 2

    compilation_order = unit_manager.get_compilation_order()
    if compilation_order is None:
        return 2

    stdlib_units = _stdlib_units(compilation_order, reporter)
    if stdlib_units is None:
        return 2

    use_incremental = (len(compilation_order) > 1 and not options.lib
                       and not options.no_incremental and not options.dump_ll)
    reuse = _unit_reuse(options, cache, use_incremental, unit_manager)
    analyzer = _analyse(unit_manager, compilation_order, reporter, src_path.stem, options,
                        reuse)
    if options.dump_shir is not None:
        _write_shir_dump(analyzer, options.dump_shir, reporter)
    if options.dump_slir is not None:
        _write_slir_dump(analyzer, options.dump_slir, reporter)
    if reporter.has_errors:
        return 2
    analyzer.check_error_holes()
    out_path = _resolve_out_path(options, src_path)
    if use_incremental:
        return _compile_incremental(
            compilation_order, analyzer, out_path, reporter, options,
            stdlib_units, unit_manager, cache,
        )
    return _compile_monolithic(compilation_order, analyzer, out_path, reporter, options)


def codegen_for(analyzer: SemanticAnalyzer, units: list[Unit]) -> 'LLVMCodegen':
    """One `LLVMCodegen` over the analyzer's program tables.

    The ONE hand-off from the front end to the back end. Both build paths need the same
    five tables, the externals and the per-unit namespaces, and each used to read them
    off the analyzer by attribute name with a silent default -- two copies of one list,
    where a name the analyzer stopped carrying would have answered None in both (#673).

    The SLIR of the program travels the same way: every body that the backend defines
    comes from the `SlirFunction` of the analysis (docs/design/ir.md 8.2). So does the
    declaration table of `units` (ir.md 6.3), made here, after the last pass of the
    analysis: the declaration round reads each declaration from it.
    """
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.backend.slir.bodies import SlirBodies
    from sushi_lang.semantics.decl_table import declarations

    tables = analyzer.tables
    cg = LLVMCodegen(struct_table=tables.structs, enum_table=tables.enums,
                     perk_impl_table=tables.perk_impls)
    cg.external_table = tables.externals
    cg.overflow = analyzer.overflow
    cg.decls = declarations(units, tables, analyzer.shir,
                            copies=analyzer.monomorphized_extensions)
    cg.slir_bodies = SlirBodies(analyzer.slir)
    return cg


def _link_inputs(analyzer: SemanticAnalyzer, options: BuildOptions) -> 'LinkInputs':
    """What the link of the program is asked for: the `link` clause of each unit with an
    AST (a unit from the cache included: the analysis reads every AST)."""
    from sushi_lang.backend.link_plan import LinkInputs

    return LinkInputs(requests=tuple(analyzer.link_requests),
                      search_dirs=options.link_dirs)


def _write_ll(cg: 'LLVMCodegen', out_path: Path, reporter: Reporter) -> None:
    from sushi_lang.internals import errors as er

    ll_path = out_path.with_suffix(".ll")
    try:
        ll_path.write_text(str(cg.module), encoding="utf-8")
    except OSError as exc:
        er.emit(reporter, er.ERR.CW0002, None, path=ll_path,
                reason=exc.strerror or str(exc))
        return
    print(f"wrote LLVM IR: {ll_path}")


def _emit_library(analyzer, compilation_order, out_path: Path,
                  reporter: Reporter, options: BuildOptions) -> bool:
    """Write the `.slib`. False when a gate refused it; the reporter holds why."""
    from sushi_lang.compiler.library_manifest import (
        LibraryManifestGenerator, resolve_library_version,
    )
    # A missing or contradicted version is CE3505, before the manifest is made.
    library_version = resolve_library_version(options.lib_version, out_path.stem)
    manifest_gen = LibraryManifestGenerator(analyzer)
    manifest_gen.generate(compilation_order, out_path,
                          library_version=library_version,
                          source=manifest_gen.source_map(compilation_order))
    # CE5002 is found while the public API is extracted, so the second gate is here.
    # `generate()` wrote nothing; this is what keeps the success line from following a
    # diagnostic.
    return not reporter.has_errors


def _compile_monolithic(compilation_order, analyzer, out_path: Path,
                        reporter: Reporter, options: BuildOptions) -> int:
    """One module for the whole program: a native binary, or a `.slib`."""
    from sushi_lang.backend.driver import LLVMDriver
    cg = codegen_for(analyzer, compilation_order)
    driver = LLVMDriver(cg)

    if options.lib:
        if not _emit_library(analyzer, compilation_order, out_path, reporter, options):
            return 2
        written = "library"
    else:
        driver.compile_multi_unit(compilation_order, out=out_path, cc="cc",
                                  debug=options.dump_ll, opt=options.opt,
                                  verify=not options.no_verify,
                                  keep_object=options.keep_object,
                                  main_expects_args=analyzer.main_expects_args,
                                  link=_link_inputs(analyzer, options))
        written = "native binary"

    if options.write_ll:
        _write_ll(cg, out_path, reporter)

    print(f"Success! Wrote {written}: {out_path}")
    return 1 if reporter.has_warnings else 0


def _compile_incremental(compilation_order, analyzer, out_path: Path, reporter: Reporter,
                         options: BuildOptions, stdlib_units, unit_manager,
                         cache: CacheManager) -> int:
    """Incremental compilation path: per-unit .o caching.

    The module of every unit that the cache does not hold is built before the first
    object is written, so an internal error of the emitter leaves no output behind.

    A bundled stdlib unit whose object depends on nothing of the program (it holds no
    template copy) keeps its object in the per-user cache, with the record of its body
    beside it, and a source library unit of that kind keeps them in the reuse directory of
    the project cache; `analyzer.reuse` says which. Every other object is in the project
    cache.
    """
    from sushi_lang.compiler.fingerprint import (
        compute_unit_fingerprint,
        compute_stdlib_fingerprint,
    )

    monomorphized_extensions = analyzer.monomorphized_extensions

    from sushi_lang.backend.driver import LLVMDriver
    cg = codegen_for(analyzer, compilation_order)
    driver = LLVMDriver(cg)
    cg.main_expects_args = analyzer.main_expects_args

    obj_paths: list[Path] = []
    rebuilt = []
    cached = []

    t0 = time.monotonic()

    # Whole-program, so it is computed once and folded into every unit.
    from sushi_lang.backend.ownership import drops_of
    drop_types = drops_of(cg)

    fingerprints = {
        unit.name: compute_unit_fingerprint(
            unit, unit_manager, monomorphized_extensions,
            drop_types=drop_types,
        )
        for unit in compilation_order
    }
    reuse = analyzer.reuse
    stored = {unit.name: reuse.stored_key(unit, fingerprints[unit.name])
              for unit in compilation_order if reuse is not None}

    def object_path(unit: Unit) -> Path:
        key = stored.get(unit.name)
        if key is not None:
            return reuse.object_path(unit, key)
        return cache.unit_object_path(unit.name, fingerprints[unit.name])

    def is_cached(unit: Unit) -> bool:
        if stored.get(unit.name) is not None:
            return object_path(unit).is_file()
        return cache.has_cached_unit(unit.name, fingerprints[unit.name])

    for unit in compilation_order:
        if reuse is not None and reuse.is_reused(unit) and not is_cached(unit):
            from sushi_lang.internals import errors as er
            er.raise_internal_error(
                "CE0000", detail=f"the object of the reused unit '{unit.name}' left the "
                                 f"cache during the build: {object_path(unit)}")
    modules = {unit.name: cg.build_module_single_unit(unit, compilation_order)
               for unit in compilation_order if not is_cached(unit)}

    cache.prepare()
    print("Code generation:")
    for unit in compilation_order:
        obj_path = object_path(unit)
        module = modules.get(unit.name)
        if module is None:
            cached.append(unit.name)
            print(f"  {unit.name:<30s} [cached]")
        else:
            obj_bytes = driver.compile_single_unit_to_object(
                module, unit.name, opt=options.opt, verify=not options.no_verify,
            )
            if stored.get(unit.name) is not None:
                publish_atomically(obj_path, obj_bytes)
            else:
                obj_path = cache.store_unit_object(unit.name, obj_bytes,
                                                   fingerprints[unit.name])
            rebuilt.append(unit.name)
            print(f"  {unit.name:<30s} [rebuilt]")
        obj_paths.append(obj_path)
        key = stored.get(unit.name)
        record = reuse.record_text(unit) if key is not None else None
        if record is not None and not reuse.record_path(unit, key).is_file():
            publish_atomically(reuse.record_path(unit, key), record.encode("utf-8"))

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
            obj_bytes = driver.compile_stdlib_to_object(stdlib_unit, opt=options.opt)
            obj_path = cache.store_stdlib_object(stdlib_unit, obj_bytes, fp)
            obj_paths.append(obj_path)

    codegen_time = time.monotonic() - t0

    t1 = time.monotonic()
    driver.link_object_files(obj_paths, out_path, cc="cc", debug=options.dump_ll,
                             link=_link_inputs(analyzer, options))
    link_time = time.monotonic() - t1

    total_units = len(compilation_order)
    stdlib_count = len(stdlib_units)
    if reuse is not None and (reuse.reused_count(False) or reuse.reused_count(True)
                              or reuse.fell_back):
        print(f"\nFront end: {reuse.reused_count(False)} stdlib units reused, "
              f"{reuse.reused_count(True)} library units reused, "
              f"{len(reuse.fell_back)} checked again")
    print(f"\nCodegen: {total_units} units ({len(cached)} cached, {len(rebuilt)} rebuilt) in {codegen_time:.2f}s")
    link_desc = f"{total_units} units"
    if stdlib_count:
        link_desc += f" + {stdlib_count} stdlib"
    print(f"Linking: {link_desc} in {link_time:.2f}s")

    from sushi_lang.compiler.cli import COMMAND_LINE
    from sushi_lang.internals import errors as er
    for flag, given in (("--write-ll", options.write_ll),
                         ("--keep-object", options.keep_object)):
        if given:
            er.emit(reporter, er.ERR.CW0003, None, filename=COMMAND_LINE, flag=flag,
                    reason="on the incremental build of a program of more than one unit; "
                           "add --no-incremental")

    print(f"Success! Wrote native binary: {out_path}")

    if reporter.has_warnings:
        return 1
    return 0
