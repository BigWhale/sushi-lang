"""LLVM optimization pipeline and verification for the Sushi language compiler."""
from __future__ import annotations

import typing
from typing import Optional, Any, Dict

from llvmlite import binding as llvm
from sushi_lang.internals.errors import raise_internal_error
if typing.TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


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
        """Apply standard O1/O2/O3 optimization pipelines."""
        levels = {"o1": 1, "o2": 2, "o3": 3}
        level = levels.get(mode, 1)

        pto = llvm.PipelineTuningOptions(speed_level=level, size_level=0)
        pb = llvm.PassBuilder(tm, pto)

        fpm = llvm.create_new_function_pass_manager()
        mpm = llvm.create_new_module_pass_manager()

        if mode == "o1":
            LLVMOptimizer._build_o1_pipeline(fpm, mpm)
        elif mode == "o2":
            LLVMOptimizer._build_o2_pipeline(fpm, mpm)
        elif mode == "o3":
            LLVMOptimizer._build_o3_pipeline(fpm, mpm)

        for fn in llmod.functions:
            if not fn.is_declaration:
                fpm.run(fn, pb)

        mpm.run(llmod, pb)

    @staticmethod
    def _build_o1_pipeline(fpm: Any, mpm: Any) -> None:
        """Build O1 optimization pipeline with basic optimizations."""
        fpm.add_sroa_pass()

        fpm.add_simplify_cfg_pass()
        fpm.add_instruction_combine_pass()

        fpm.add_dead_code_elimination_pass()

        mpm.add_global_dead_code_eliminate_pass()
        mpm.add_strip_dead_prototype_pass()

    @staticmethod
    def _build_o2_pipeline(fpm: Any, mpm: Any) -> None:
        """Build O2 optimization pipeline with moderate optimizations."""
        fpm.add_sroa_pass()
        fpm.add_simplify_cfg_pass()

        fpm.add_sccp_pass()  # Sparse conditional constant propagation
        fpm.add_instruction_combine_pass()
        fpm.add_reassociate_pass()

        fpm.add_jump_threading_pass()
        fpm.add_simplify_cfg_pass()

        fpm.add_loop_simplify_pass()
        fpm.add_lcssa_pass()  # Loop-closed SSA form
        fpm.add_loop_rotate_pass()
        fpm.add_loop_deletion_pass()

        fpm.add_instruction_combine_pass()
        fpm.add_new_gvn_pass()  # Global value numbering (redundancy elimination)

        fpm.add_mem_copy_opt_pass()
        fpm.add_dead_store_elimination_pass()

        fpm.add_aggressive_dce_pass()
        fpm.add_simplify_cfg_pass()

        fpm.add_tail_call_elimination_pass()

        mpm.add_global_opt_pass()
        mpm.add_ipsccp_pass()  # Interprocedural SCCP
        mpm.add_dead_arg_elimination_pass()
        mpm.add_global_dead_code_eliminate_pass()
        mpm.add_constant_merge_pass()
        mpm.add_strip_dead_prototype_pass()

    @staticmethod
    def _build_o3_pipeline(fpm: Any, mpm: Any) -> None:
        """Build O3 optimization pipeline with aggressive optimizations."""
        fpm.add_sroa_pass()
        fpm.add_simplify_cfg_pass()

        fpm.add_sccp_pass()
        fpm.add_instruction_combine_pass()
        fpm.add_reassociate_pass()

        fpm.add_jump_threading_pass()
        fpm.add_simplify_cfg_pass()

        fpm.add_loop_simplify_pass()
        fpm.add_lcssa_pass()
        fpm.add_loop_rotate_pass()
        fpm.add_loop_unroll_pass()  # Aggressive unrolling
        fpm.add_loop_deletion_pass()
        fpm.add_loop_strength_reduce_pass()  # Loop strength reduction

        fpm.add_instruction_combine_pass()
        fpm.add_new_gvn_pass()
        fpm.add_aggressive_instcombine_pass()  # More aggressive than standard

        fpm.add_mem_copy_opt_pass()
        fpm.add_dead_store_elimination_pass()

        fpm.add_sinking_pass()

        fpm.add_instruction_combine_pass()
        fpm.add_simplify_cfg_pass()

        fpm.add_aggressive_dce_pass()
        fpm.add_simplify_cfg_pass()

        fpm.add_tail_call_elimination_pass()

        mpm.add_global_opt_pass()
        mpm.add_ipsccp_pass()
        mpm.add_dead_arg_elimination_pass()
        mpm.add_argument_promotion_pass()  # Promote by-reference args to by-value
        mpm.add_merge_functions_pass()  # Merge identical functions
        mpm.add_global_dead_code_eliminate_pass()
        mpm.add_constant_merge_pass()
        mpm.add_strip_dead_prototype_pass()

        mpm.add_global_dead_code_eliminate_pass()

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

    def clear_cache(self) -> None:
        """Clear the target machine cache."""
        self._tm_cache.clear()

    def get_cached_targets(self) -> list[str]:
        """Get list of cached target triples."""
        return list(self._tm_cache.keys())

    def is_llvm_initialized(self) -> bool:
        """Check if LLVM native support has been initialized."""
        return self._llvm_init

    @staticmethod
    def get_optimization_level_description(level: str) -> str:
        """Get a human-readable description of an optimization level."""
        descriptions = {
            "none": "No optimizations - fastest compilation",
            "o0": "No optimizations - fastest compilation",
            "mem2reg": "Basic SROA (memory-to-register promotion) - minimal optimization for SSA",
            "o1": "Basic optimizations - quick compile time with essential improvements",
            "o2": "Moderate optimizations - balanced compile time and performance",
            "o3": "Aggressive optimizations - maximum performance, longer compile time"
        }
        return descriptions.get(level.lower(), "Unknown optimization level")

    @staticmethod
    def list_available_levels() -> list[str]:
        """Get list of available optimization levels."""
        return ["none", "o0", "mem2reg", "o1", "o2", "o3"]
