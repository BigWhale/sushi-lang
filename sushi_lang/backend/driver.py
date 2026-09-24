"""The compile and link driver: it takes the IR a codegen builds to an object and a binary."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from llvmlite import ir, binding as llvm

from sushi_lang.semantics.units import Unit

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import ExtendDef


def _run_linker(cmd: List[str], cc: str) -> None:
    """Run the link step, and report a failure as a diagnostic rather than a crash.

    Both linking paths come here. `check=True` alone let `CalledProcessError` reach the
    top-level guard, which renders any uncaught exception as a CE0000 "this is a bug in
    the Sushi compiler" -- and printed the raw `cc` stderr ahead of it, unfiltered (#251).
    """
    from sushi_lang.internals.diagnostics import SushiError

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return

    error = SushiError("CE3008", cc=cc, status=result.returncode)
    for stream in (result.stderr, result.stdout):
        for line in (stream or "").splitlines():
            if line.strip():
                error.note(line.rstrip())
    raise error


def _link_command(cc: str, inputs: List[Path], out: Path, debug: bool) -> List[str]:
    """The `cc` command line that links `inputs` into the executable `out`."""
    cmd = [cc] + [str(p) for p in inputs]
    cmd.extend(["-o", str(out)])

    from sushi_lang.backend.platform_detect import get_current_platform
    if get_current_platform().is_linux:
        cmd.append("-lm")

    if debug:
        cmd.insert(1, "-g")
    return cmd


def _print_numbered(title: str, module: ir.Module) -> None:
    print(title)
    for i, line in enumerate(str(module).splitlines(), 1):
        print(f"{i:4} {line}")


def _set_weak_odr_on_perk_impls(module: ir.Module, units: list[Unit]) -> None:
    """Set weak_odr linkage on every perk-impl method in a library module."""
    from sushi_lang.semantics.library_templates import impl_method_symbol
    from sushi_lang.semantics.passes.collect.perks import _get_type_name

    for unit in units:
        if unit.ast is None:
            continue
        for perk_impl in unit.ast.perk_impls:
            type_name = _get_type_name(perk_impl.target_type)
            if type_name is None:
                continue
            for method in perk_impl.methods:
                symbol = impl_method_symbol(type_name, method.name)
                fn = module.globals.get(symbol)
                if fn is not None and isinstance(fn, ir.Function) and not fn.is_declaration:
                    fn.linkage = "weak_odr"


class LLVMDriver:
    """Compiles and links what one `LLVMCodegen` generates.

    The codegen generates IR; the driver verifies it, optimizes it, emits the objects and
    runs the linker. After a whole-program compile, `codegen.module` holds the linked and
    optimized module, which is what `--write-ll` writes.
    """

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        self.codegen = codegen

    def _verify_and_optimize(self, llmod: llvm.ModuleRef, opt: str, verify: bool,
                             label: str = "") -> None:
        optimizer = self.codegen.optimizer
        if verify:
            optimizer.verify(llmod, f"pre-optimization{label}")
        if opt != "none":
            optimizer.optimize(llmod, opt)
        if verify:
            optimizer.verify(llmod, f"post-optimization{label}")

    def compile_multi_unit(
        self,
        units: list[Unit],
        out: Path | None = None,
        cc: str = "cc",
        debug: bool = False,
        opt: str = "mem2reg",
        verify: bool = True,
        keep_object: bool = False,
        main_expects_args: bool = False,
        monomorphized_extensions: Optional[list['ExtendDef']] = None,
    ) -> Path:
        """Compile every unit into one module, and link it to a native executable.

        The loaded libraries are read off the codegen, where the pipeline's one hand-off
        put them (#645); taking them as arguments here gave the library build path no way
        to supply them.
        """
        cg = self.codegen
        cg.main_expects_args = main_expects_args
        cg.monomorphized_extensions = monomorphized_extensions or []

        mod_ir: ir.Module = cg.build_module_multi_unit(units)

        if debug:
            _print_numbered(";; Multi-unit IR (pre-opt)", mod_ir)

        llmod = llvm.parse_assembly(str(mod_ir))

        library_paths = set()
        stdlib_units = set()

        for unit in units:
            if unit.ast is not None:
                for use_stmt in unit.ast.uses:
                    if use_stmt.is_library:
                        library_paths.add(use_stmt.path)
                    elif use_stmt.is_stdlib:
                        stdlib_units.add(use_stmt.path)

        library_linker = cg.library_linker
        if library_linker is not None and library_paths:
            from sushi_lang.backend.module_linker import TwoPhaseLinker

            target_triple = llmod.triple if hasattr(llmod, 'triple') else ""
            data_layout = llmod.data_layout if hasattr(llmod, 'data_layout') else ""

            two_phase = TwoPhaseLinker(target_triple, data_layout)

            two_phase.add_main_module(llmod, "main")

            from sushi_lang.backend.library_format import LibraryFormat
            from sushi_lang.backend.library_errors import LibraryError
            for lib_path in library_paths:
                try:
                    slib_path = library_linker.resolve_library(lib_path)
                    metadata, bitcode = LibraryFormat.read(slib_path)
                    library_linker.loaded_libraries[metadata["library_name"]] = metadata

                    lib_mod = llvm.parse_bitcode(bitcode)
                    two_phase.add_library_module(lib_mod, metadata["library_name"])
                except LibraryError:
                    raise
                except Exception as e:
                    raise LibraryError("CE3507", lib=lib_path, reason=str(e)) from e

            for stdlib_path in stdlib_units:
                bc_paths = cg.stdlib._resolve_stdlib_unit(stdlib_path)
                for bc_path in bc_paths:
                    with open(bc_path, 'rb') as f:
                        stdlib_mod = llvm.parse_bitcode(f.read())
                        two_phase.add_stdlib_module(stdlib_mod, stdlib_path)

            llmod = two_phase.link()

        else:
            cg.stdlib.link_stdlib_modules(
                llmod, [unit.ast for unit in units if unit.ast is not None])

        cg.optimizer.ensure_target(llmod)
        self._verify_and_optimize(llmod, opt, verify)

        cg.module = llvm.parse_assembly(str(llmod))

        out_path = out or Path("a.out")
        return self.link_executable(llmod, out_path, cc, debug, keep_object=keep_object)

    def compile_to_bitcode(
        self,
        units: list[Unit],
        debug: bool = False,
        opt: str = "mem2reg",
        verify: bool = True,
        monomorphized_extensions: Optional[list['ExtendDef']] = None,
        exported_private_functions: frozenset[str] | set[str] = frozenset(),
    ) -> bytes:
        """Compile units to LLVM bitcode without linking to executable."""
        cg = self.codegen
        cg.monomorphized_extensions = monomorphized_extensions or []

        cg.is_library_mode = True

        # A bundled stdlib module and an injected source library both arrive as
        # ordinary units and carry a provenance; neither is this library's to own, and
        # the consumer holds its own copy of each (#594). `library_manifest.own_units`
        # is the same question about the same field, asked of the manifest.
        weak_units = frozenset(u.name for u in units if u.provenance is not None)

        mod_ir: ir.Module = cg.build_module_multi_unit(units, weak_units=weak_units)

        # A perk impl may ship through the manifest and be overridden locally. weak_odr,
        # not linkonce_odr: it must survive optimization while unreferenced in the library,
        # and it lets the consumer's strong definition win at link time.
        _set_weak_odr_on_perk_impls(mod_ir, units)

        # An export-closure private function must resolve consumer call sites at link
        # time, so promote it to external. A same-name consumer definition is CE5007.
        for name in exported_private_functions:
            fn = mod_ir.globals.get(name)
            if fn is not None and isinstance(fn, ir.Function) and not fn.is_declaration:
                fn.linkage = "external"

        if debug:
            _print_numbered(";; Library IR (pre-opt)", mod_ir)

        llmod = llvm.parse_assembly(str(mod_ir))

        # The stdlib `.bc` half of the same rule: the consumer links its own copy of
        # every module it imports, so this one may not be a second strong definition.
        from sushi_lang.backend.library_linkage import weaken_linked_symbols
        weaken_linked_symbols(llmod, cg.stdlib.link_stdlib_modules(
            llmod, [unit.ast for unit in units if unit.ast is not None]))

        cg.optimizer.ensure_target(llmod)
        self._verify_and_optimize(llmod, opt, verify)

        cg.module = llvm.parse_assembly(str(llmod))

        return llmod.as_bitcode()

    def link_executable(
        self,
        llmod: llvm.ModuleRef,
        out: Path,
        cc: str,
        debug: bool,
        tm: Optional[llvm.TargetMachine] = None,
        keep_object: bool = False,
    ) -> Path:
        """Emit object file and link to native executable."""
        optimizer = self.codegen.optimizer
        optimizer.ensure_llvm()

        if tm is None:
            tm = optimizer.ensure_target(llmod)

        obj_path = out.with_suffix(".o")
        obj_path.write_bytes(tm.emit_object(llmod))

        try:
            _run_linker(_link_command(cc, [obj_path], out, debug), cc)
        finally:
            # The object file is an intermediate either way. Leaving it behind on the
            # failure path was half of #251: a failed compile wrote a stale `.o` next
            # to the source and said nothing about it.
            if not keep_object:
                obj_path.unlink(missing_ok=True)
        return out

    def compile_single_unit_to_object(self, target_unit: Unit, all_units: list[Unit],
                                      opt: str = "mem2reg", verify: bool = True) -> bytes:
        """Compile a single unit to an object file (bytes)."""
        mod_ir = self.codegen.build_module_single_unit(target_unit, all_units)
        llmod = llvm.parse_assembly(str(mod_ir))

        tm = self.codegen.optimizer.ensure_target(llmod)
        self._verify_and_optimize(llmod, opt, verify, f" ({target_unit.name})")
        return tm.emit_object(llmod)

    def compile_stdlib_to_object(self, stdlib_unit: str, opt: str = "mem2reg") -> bytes:
        """Compile stdlib bitcode files to a single object file."""
        bc_paths = self.codegen.stdlib._resolve_stdlib_unit(stdlib_unit)
        llmod = None
        for bc_path in bc_paths:
            with open(bc_path, 'rb') as f:
                mod = llvm.parse_bitcode(f.read())
                if llmod is None:
                    llmod = mod
                else:
                    llmod.link_in(mod)

        if llmod is None:
            raise RuntimeError(f"No bitcode files found for stdlib unit: {stdlib_unit}")

        return self._emit_object(llmod, opt)

    def compile_library_to_object(self, lib_path: str, library_linker,
                                  opt: str = "mem2reg") -> bytes:
        """Compile a library .slib to an object file."""
        from sushi_lang.backend.library_format import LibraryFormat

        slib_path = library_linker.resolve_library(lib_path)
        _, bitcode = LibraryFormat.read(slib_path)
        return self._emit_object(llvm.parse_bitcode(bitcode), opt)

    def _emit_object(self, llmod: llvm.ModuleRef, opt: str) -> bytes:
        """Optimize a module that needs no verification, and emit its object."""
        tm = self.codegen.optimizer.ensure_target(llmod)
        if opt != "none":
            self.codegen.optimizer.optimize(llmod, opt)
        return tm.emit_object(llmod)

    def link_object_files(self, obj_paths: list[Path], out: Path, cc: str = "cc",
                          debug: bool = False) -> Path:
        """Link multiple .o files into a native executable."""
        _run_linker(_link_command(cc, obj_paths, out, debug), cc)
        return out
