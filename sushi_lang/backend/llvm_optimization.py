"""LLVM optimization pipeline and verification for the Sushi language compiler."""
from __future__ import annotations

import typing
from typing import Optional, Any, Callable, Dict, NamedTuple

from llvmlite import binding as llvm
from sushi_lang.internals.errors import raise_internal_error
if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


_F = llvm.FunctionPassManager
_M = llvm.ModulePassManager
_FunctionPass = Callable[[llvm.FunctionPassManager], None]
_ModulePass = Callable[[llvm.ModulePassManager], None]


class _Pipeline(NamedTuple):
    speed_level: int
    function_passes: tuple[_FunctionPass, ...]
    module_passes: tuple[_ModulePass, ...]


_O2_CANONICALIZE: tuple[_FunctionPass, ...] = (
    _F.add_sroa_pass, _F.add_simplify_cfg_pass,
    _F.add_sccp_pass, _F.add_instruction_combine_pass, _F.add_reassociate_pass,
    _F.add_jump_threading_pass, _F.add_simplify_cfg_pass,
    _F.add_loop_simplify_pass, _F.add_lcssa_pass, _F.add_loop_rotate_pass,
)
_O2_REDUNDANCY: tuple[_FunctionPass, ...] = (_F.add_instruction_combine_pass, _F.add_new_gvn_pass)
_O2_MEMORY: tuple[_FunctionPass, ...] = (_F.add_mem_copy_opt_pass, _F.add_dead_store_elimination_pass)
_O2_CLEANUP: tuple[_FunctionPass, ...] = (
    _F.add_aggressive_dce_pass, _F.add_simplify_cfg_pass, _F.add_tail_call_elimination_pass,
)
_O2_INTERPROCEDURAL: tuple[_ModulePass, ...] = (
    _M.add_global_opt_pass, _M.add_ipsccp_pass, _M.add_dead_arg_elimination_pass,
)
_O2_MODULE_CLEANUP: tuple[_ModulePass, ...] = (
    _M.add_global_dead_code_eliminate_pass, _M.add_constant_merge_pass,
    _M.add_strip_dead_prototype_pass,
)

# O3 is the O2 segments in the O2 order, with its own passes put between them.
_PIPELINES: dict[str, _Pipeline] = {
    "o1": _Pipeline(
        1,
        (_F.add_sroa_pass, _F.add_simplify_cfg_pass, _F.add_instruction_combine_pass,
         _F.add_dead_code_elimination_pass),
        (_M.add_global_dead_code_eliminate_pass, _M.add_strip_dead_prototype_pass),
    ),
    "o2": _Pipeline(
        2,
        _O2_CANONICALIZE + (_F.add_loop_deletion_pass,) + _O2_REDUNDANCY + _O2_MEMORY
        + _O2_CLEANUP,
        _O2_INTERPROCEDURAL + _O2_MODULE_CLEANUP,
    ),
    "o3": _Pipeline(
        3,
        _O2_CANONICALIZE
        + (_F.add_loop_unroll_pass, _F.add_loop_deletion_pass, _F.add_loop_strength_reduce_pass)
        + _O2_REDUNDANCY + (_F.add_aggressive_instcombine_pass,)
        + _O2_MEMORY
        + (_F.add_sinking_pass, _F.add_instruction_combine_pass, _F.add_simplify_cfg_pass)
        + _O2_CLEANUP,
        _O2_INTERPROCEDURAL + (_M.add_argument_promotion_pass, _M.add_merge_functions_pass)
        + _O2_MODULE_CLEANUP,
    ),
}


class LLVMOptimizer:
    """Handles LLVM optimization pipeline, verification, and target setup."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize optimizer with reference to main codegen instance."""
        self.codegen = codegen
        self._llvm_init = False
        self._tm_cache: Dict[str, llvm.TargetMachine] = {}

    def optimize(self, llmod: llvm.ModuleRef, mode: str = "mem2reg") -> None:
        """Apply optimization passes to LLVM module."""
        m = (mode or "none").lower()
        if m in ("none", "o0"):
            return

        tm = self._create_target_machine_with_reloc()

        if m == "mem2reg":
            self._apply_mem2reg_optimization(llmod, tm)
        else:
            self._apply_standard_optimization(llmod, tm, m)

    @staticmethod
    def _apply_mem2reg_optimization(llmod: llvm.ModuleRef, tm: llvm.TargetMachine) -> None:
        """Apply minimal SROA (mem2reg-equivalent) optimization."""
        pto = llvm.PipelineTuningOptions(speed_level=0, size_level=0)
        pb = llvm.PassBuilder(tm, pto)

        fpm = llvm.create_new_function_pass_manager()
        fpm.add_sroa_pass()

        for fn in llmod.functions:
            if not fn.is_declaration:
                fpm.run(fn, pb)

    @staticmethod
    def _apply_standard_optimization(llmod: llvm.ModuleRef, tm: llvm.TargetMachine, mode: str) -> None:
        """Apply the O1/O2/O3 pipeline that `_PIPELINES` holds for `mode`."""
        pipeline = _PIPELINES.get(mode)
        if pipeline is None:
            raise_internal_error("CE0000", detail=f"no optimization pipeline for mode '{mode}'")
            return

        pto = llvm.PipelineTuningOptions(speed_level=pipeline.speed_level, size_level=0)
        pb = llvm.PassBuilder(tm, pto)

        fpm = llvm.create_new_function_pass_manager()
        for add_function_pass in pipeline.function_passes:
            add_function_pass(fpm)
        mpm = llvm.create_new_module_pass_manager()
        for add_module_pass in pipeline.module_passes:
            add_module_pass(mpm)

        for fn in llmod.functions:
            if not fn.is_declaration:
                fpm.run(fn, pb)

        mpm.run(llmod, pb)

    @staticmethod
    def verify(llmod: llvm.ModuleRef, when: str = "unspecified") -> None:
        """Verify LLVM IR correctness and structure."""
        try:
            llmod.verify()
        except Exception as e:
            raise_internal_error("CE0015", message=f"LLVM IR verification failed ({when}): {e}")

    def ensure_llvm(self) -> None:
        """Initialize LLVM native target and assembly printer."""
        if self._llvm_init:
            return
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()
        self._llvm_init = True

    def _create_target_machine_with_reloc(self, target_triple: str | None = None) -> llvm.TargetMachine:
        """Create target machine with appropriate relocation model for the platform."""
        self.ensure_llvm()
        triple = target_triple or llvm.get_default_triple()
        target = llvm.Target.from_triple(triple)

        reloc = "default"
        if "linux" in triple.lower():
            reloc = "pic"

        return target.create_target_machine(reloc=reloc)

    def ensure_target(self, mod: Optional[Any] = None, target_triple: str | None = None) -> llvm.TargetMachine:
        """Ensure module has target triple and data layout, return TargetMachine."""
        self.ensure_llvm()

        triple = target_triple or llvm.get_default_triple()

        tm = self._tm_cache.get(triple)
        if tm is None:
            tm = self._create_target_machine_with_reloc(triple)
            self._tm_cache[triple] = tm

        if mod is not None:
            mod.triple = triple
            mod.data_layout = str(tm.target_data)

        return tm

    def create_target_machine(self, target_triple: str | None = None) -> llvm.TargetMachine:
        """Create a new target machine for the specified triple."""
        self.ensure_llvm()
        triple = target_triple or llvm.get_default_triple()
        return self._create_target_machine_with_reloc(triple)

    @staticmethod
    def get_default_triple() -> str:
        """Get the default target triple for this platform."""
        return llvm.get_default_triple()
