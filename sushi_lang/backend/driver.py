"""The compile and link driver: it takes the IR a codegen builds to an object and a binary."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from llvmlite import ir, binding as llvm

from sushi_lang.backend.link_plan import NO_LINK_INPUTS, LinkInputs, LinkPlan, run_link
from sushi_lang.semantics.units import Unit

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


@contextmanager
def writing_output(path: Path) -> Iterator[None]:
    """An `OSError` while the compiler writes a file that the command line asked for
    (the `-o` output, the object beside it, the cache) is CE3020, never CE0000."""
    try:
        yield
    except OSError as exc:
        from sushi_lang.compiler.cli import COMMAND_LINE
        from sushi_lang.internals.diagnostics import SushiError
        raise SushiError("CE3020", filename=COMMAND_LINE, path=exc.filename or path,
                         reason=exc.strerror or str(exc)) from exc


def _print_numbered(title: str, module: ir.Module) -> None:
    print(title)
    for i, line in enumerate(str(module).splitlines(), 1):
        print(f"{i:4} {line}")


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
        link: LinkInputs = NO_LINK_INPUTS,
    ) -> Path:
        """Compile every unit into one module, and link it to a native executable."""
        cg = self.codegen
        cg.main_expects_args = main_expects_args

        mod_ir: ir.Module = cg.build_module_multi_unit(units)

        if debug:
            _print_numbered(";; Multi-unit IR (pre-opt)", mod_ir)

        llmod = llvm.parse_assembly(str(mod_ir))

        cg.stdlib.link_stdlib_modules(llmod, cg.declaration_table())

        tm = cg.optimizer.ensure_target(llmod, opt)
        self._verify_and_optimize(llmod, opt, verify)

        cg.module = llvm.parse_assembly(str(llmod))

        out_path = out or Path("a.out")
        return self.link_executable(llmod, out_path, cc, debug, tm=tm, keep_object=keep_object,
                                    link=link)

    def link_executable(
        self,
        llmod: llvm.ModuleRef,
        out: Path,
        cc: str,
        debug: bool,
        tm: Optional[llvm.TargetMachine] = None,
        keep_object: bool = False,
        link: LinkInputs = NO_LINK_INPUTS,
    ) -> Path:
        """Emit object file and link to native executable."""
        optimizer = self.codegen.optimizer
        optimizer.ensure_llvm()

        if tm is None:
            tm = optimizer.ensure_target(llmod)

        obj_path = out.with_suffix(".o")
        obj_bytes = tm.emit_object(llmod)
        with writing_output(obj_path):
            obj_path.write_bytes(obj_bytes)

        try:
            run_link(cc, LinkPlan.of([obj_path], out, debug, link))
        finally:
            # The object file is an intermediate either way. Leaving it behind on the
            # failure path was half of #251: a failed compile wrote a stale `.o` next
            # to the source and said nothing about it.
            if not keep_object:
                obj_path.unlink(missing_ok=True)
        return out

    def compile_single_unit_to_object(self, mod_ir: ir.Module, unit_name: str,
                                      opt: str = "mem2reg", verify: bool = True) -> bytes:
        """Compile the module of one unit (`build_module_single_unit`) to an object file
        (bytes)."""
        llmod = llvm.parse_assembly(str(mod_ir))

        tm = self.codegen.optimizer.ensure_target(llmod, opt)
        self._verify_and_optimize(llmod, opt, verify, f" ({unit_name})")
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

    def _emit_object(self, llmod: llvm.ModuleRef, opt: str) -> bytes:
        """Optimize a module that needs no verification, and emit its object."""
        tm = self.codegen.optimizer.ensure_target(llmod, opt)
        if opt != "none":
            self.codegen.optimizer.optimize(llmod, opt)
        return tm.emit_object(llmod)

    def link_object_files(self, obj_paths: list[Path], out: Path, cc: str = "cc",
                          debug: bool = False, link: LinkInputs = NO_LINK_INPUTS) -> Path:
        """Link multiple .o files into a native executable."""
        run_link(cc, LinkPlan.of(obj_paths, out, debug, link))
        return out
