"""
LLVM optimization pipeline and verification for the Sushi language compiler.

This module handles LLVM IR optimization passes, module verification,
target machine setup, and the complete optimization pipeline from
basic mem2reg to full O1/O2/O3 optimizations.
"""
from __future__ import annotations

import typing
from typing import Optional, Any, Dict

from llvmlite import binding as llvm
from internals.errors import raise_internal_error
if typing.TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


class LLVMOptimizer:
    """Handles LLVM optimization pipeline, verification, and target setup."""

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        """Initialize optimizer with reference to main codegen instance.

        Args:
            codegen: The main LLVMCodegen instance providing context.
        """
        self.codegen = codegen
        self._llvm_init = False
        self._tm_cache: Dict[str, llvm.TargetMachine] = {}

    def optimize(self, llmod: llvm.ModuleRef, mode: str = "mem2reg") -> None:
        """Apply optimization passes to LLVM module.

        Supports different optimization levels from basic mem2reg promotion
        to full O1/O2/O3 optimization pipelines using the New Pass Manager.

        Args:
            llmod: The LLVM module to optimize.
            mode: Optimization mode - "none"/"o0", "mem2reg", "o1", "o2", "o3".

        Note:
            Requires llvmlite 0.45+ for New Pass Manager support.
        """
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
        """Apply minimal SROA (mem2reg-equivalent) optimization.

        Args:
            llmod: The LLVM module to optimize.
            tm: Target machine for optimization context.
        """
        pto = llvm.PipelineTuningOptions(speed_level=0, size_level=0)
        pb = llvm.PassBuilder(tm, pto)

        fpm = llvm.create_new_function_pass_manager()
        fpm.add_sroa_pass()

        for fn in llmod.functions:
            if not fn.is_declaration:
                fpm.run(fn, pb)

    @staticmethod
    def _apply_standard_optimization(llmod: llvm.ModuleRef, tm: llvm.TargetMachine, mode: str) -> None:
        """Apply standard O1/O2/O3 optimization pipelines.

        Args:
            llmod: The LLVM module to optimize.
            tm: Target machine for optimization context.
            mode: Optimization level ("o1", "o2", or "o3").
        """
        levels = {"o1": 1, "o2": 2, "o3": 3}
        level = levels.get(mode, 1)

        pto = llvm.PipelineTuningOptions(speed_level=level, size_level=0)
        pb = llvm.PassBuilder(tm, pto)

        # Create pass managers
        fpm = llvm.create_new_function_pass_manager()
        mpm = llvm.create_new_module_pass_manager()

        # Apply optimization pipeline based on level
        if mode == "o1":
            LLVMOptimizer._build_o1_pipeline(fpm, mpm)
        elif mode == "o2":
            LLVMOptimizer._build_o2_pipeline(fpm, mpm)
        elif mode == "o3":
            LLVMOptimizer._build_o3_pipeline(fpm, mpm)

        # Run function passes on all functions
        for fn in llmod.functions:
            if not fn.is_declaration:
                fpm.run(fn, pb)

        # Run module passes
        mpm.run(llmod, pb)

    @staticmethod
    def _build_o1_pipeline(fpm: Any, mpm: Any) -> None:
        """Build O1 optimization pipeline with basic optimizations.

        O1 focuses on quick compile times with essential optimizations:
        - Basic memory optimizations (SROA, mem2reg-equivalent)
        - Simple peephole optimizations
        - Dead code elimination
        - CFG simplification

        Args:
            fpm: Function pass manager to populate.
            mpm: Module pass manager to populate.
        """
        # Basic SROA and memory optimizations
        fpm.add_sroa_pass()

        # Basic simplifications
        fpm.add_simplify_cfg_pass()
        fpm.add_instruction_combine_pass()

        # Dead code elimination
        fpm.add_dead_code_elimination_pass()

        # Module-level optimizations
        mpm.add_global_dead_code_eliminate_pass()
        mpm.add_strip_dead_prototype_pass()

    @staticmethod
    def _build_o2_pipeline(fpm: Any, mpm: Any) -> None:
        """Build O2 optimization pipeline with moderate optimizations.

        O2 balances compile time with good performance improvements:
        - All O1 optimizations
        - Loop optimizations (rotation, simplification, deletion)
        - Memory optimization (memcpy, DSE)
        - Scalar optimizations (SCCP, reassociate, GVN)
        - Inlining and interprocedural optimizations
        - CFG improvements (jump threading, tail call elimination)

        Args:
            fpm: Function pass manager to populate.
            mpm: Module pass manager to populate.
        """
        # Early optimizations (O1 base)
        fpm.add_sroa_pass()
        fpm.add_simplify_cfg_pass()

        # Scalar optimizations
        fpm.add_sccp_pass()  # Sparse conditional constant propagation
        fpm.add_instruction_combine_pass()
        fpm.add_reassociate_pass()

        # CFG optimizations
        fpm.add_jump_threading_pass()
        fpm.add_simplify_cfg_pass()

        # Loop optimizations
        fpm.add_loop_simplify_pass()
        fpm.add_lcssa_pass()  # Loop-closed SSA form
        fpm.add_loop_rotate_pass()
        fpm.add_loop_deletion_pass()

        # More scalar optimizations
        fpm.add_instruction_combine_pass()
        fpm.add_new_gvn_pass()  # Global value numbering (redundancy elimination)

        # Memory optimizations
        fpm.add_mem_copy_opt_pass()
        fpm.add_dead_store_elimination_pass()

        # Cleanup
        fpm.add_aggressive_dce_pass()
        fpm.add_simplify_cfg_pass()

        # Tail call elimination
        fpm.add_tail_call_elimination_pass()

        # Module-level optimizations
        mpm.add_global_opt_pass()
        mpm.add_ipsccp_pass()  # Interprocedural SCCP
        mpm.add_dead_arg_elimination_pass()
        mpm.add_global_dead_code_eliminate_pass()
        mpm.add_constant_merge_pass()
        mpm.add_strip_dead_prototype_pass()

    @staticmethod
    def _build_o3_pipeline(fpm: Any, mpm: Any) -> None:
        """Build O3 optimization pipeline with aggressive optimizations.

        O3 prioritizes performance over compile time:
        - All O2 optimizations
        - Aggressive loop optimizations (unrolling, strength reduction)
        - Aggressive inlining and function merging
        - Additional scalar optimizations
        - Instruction sinking for better register allocation

        Args:
            fpm: Function pass manager to populate.
            mpm: Module pass manager to populate.
        """
        # Early optimizations (O2 base + more aggressive settings)
        fpm.add_sroa_pass()
        fpm.add_simplify_cfg_pass()

        # Scalar optimizations (first pass)
        fpm.add_sccp_pass()
        fpm.add_instruction_combine_pass()
        fpm.add_reassociate_pass()

        # CFG optimizations
        fpm.add_jump_threading_pass()
        fpm.add_simplify_cfg_pass()

        # Loop optimizations (aggressive)
        fpm.add_loop_simplify_pass()
        fpm.add_lcssa_pass()
        fpm.add_loop_rotate_pass()
        fpm.add_loop_unroll_pass()  # Aggressive unrolling
        fpm.add_loop_deletion_pass()
        fpm.add_loop_strength_reduce_pass()  # Loop strength reduction

        # More aggressive scalar optimizations
        fpm.add_instruction_combine_pass()
        fpm.add_new_gvn_pass()
        fpm.add_aggressive_instcombine_pass()  # More aggressive than standard

        # Memory optimizations
        fpm.add_mem_copy_opt_pass()
        fpm.add_dead_store_elimination_pass()

        # Instruction sinking for better register allocation
        fpm.add_sinking_pass()

        # Second round of optimizations
        fpm.add_instruction_combine_pass()
        fpm.add_simplify_cfg_pass()

        # Cleanup
        fpm.add_aggressive_dce_pass()
        fpm.add_simplify_cfg_pass()

        # Tail call elimination
        fpm.add_tail_call_elimination_pass()

        # Module-level optimizations (aggressive)
        mpm.add_global_opt_pass()
        mpm.add_ipsccp_pass()
        mpm.add_dead_arg_elimination_pass()
        mpm.add_argument_promotion_pass()  # Promote by-reference args to by-value
        mpm.add_merge_functions_pass()  # Merge identical functions
        mpm.add_global_dead_code_eliminate_pass()
        mpm.add_constant_merge_pass()
        mpm.add_strip_dead_prototype_pass()

        # Final module-level cleanup
        mpm.add_global_dead_code_eliminate_pass()

    @staticmethod
    def verify(llmod: llvm.ModuleRef, when: str = "unspecified") -> None:
        """Verify LLVM IR correctness and structure.

        Performs comprehensive verification of the LLVM module including
        type consistency, control flow validity, and instruction correctness.

        Args:
            llmod: The LLVM module to verify.
            when: Description of when verification is happening (for error messages).

        Raises:
            RuntimeError: If verification fails, includes full IR dump for debugging.
        """
        try:
            llmod.verify()
        except Exception as e:
            print(e)
            ir_dump = str(llmod)
            raise_internal_error("CE0015", message=f"LLVM IR verification failed ({when}): {e}")

    def ensure_llvm(self) -> None:
        """Initialize LLVM native target and assembly printer.

        Performs one-time initialization of LLVM's native target support
        and assembly printing capabilities. Safe to call multiple times.
        """
        if self._llvm_init:
            return
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()
        self._llvm_init = True

    def _create_target_machine_with_reloc(self, target_triple: str | None = None) -> llvm.TargetMachine:
        """Create target machine with appropriate relocation model for the platform.

        Configures relocation model based on target triple:
        - Linux (ARM64/x86_64): PIC relocation model (required for modern Linux)
        - macOS: Default relocation model (already position-independent)

        Args:
            target_triple: Target triple string, or None for default.

        Returns:
            Configured TargetMachine instance.
        """
        self.ensure_llvm()
        triple = target_triple or llvm.get_default_triple()
        target = llvm.Target.from_triple(triple)

        # Determine if we need PIC relocation model
        # Linux requires PIC for both ARM64 and x86_64
        reloc = "default"
        if "linux" in triple.lower():
            reloc = "pic"

        return target.create_target_machine(reloc=reloc)

    def ensure_target(self, mod: Optional[Any] = None, target_triple: str | None = None) -> llvm.TargetMachine:
        """Ensure module has target triple and data layout, return TargetMachine.

        Sets up proper target information for the module and creates or retrieves
        a cached target machine for the specified triple.

        Args:
            mod: The module to configure (ir.Module or ModuleRef, or None).
            target_triple: Specific target triple, or None for default.

        Returns:
            Configured TargetMachine for the target triple.
        """
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
        """Create a new target machine for the specified triple.

        Args:
            target_triple: Target triple string, or None for default.

        Returns:
            New TargetMachine instance.
        """
        self.ensure_llvm()
        triple = target_triple or llvm.get_default_triple()
        return self._create_target_machine_with_reloc(triple)

    @staticmethod
    def get_default_triple() -> str:
        """Get the default target triple for this platform.

        Returns:
            The default LLVM target triple string.
        """
        return llvm.get_default_triple()

    def clear_cache(self) -> None:
        """Clear the target machine cache.

        Useful for testing or when target requirements change.
        """
        self._tm_cache.clear()

    def get_cached_targets(self) -> list[str]:
        """Get list of cached target triples.

        Returns:
            List of target triple strings that have cached TargetMachines.
        """
        return list(self._tm_cache.keys())

    def is_llvm_initialized(self) -> bool:
        """Check if LLVM native support has been initialized.

        Returns:
            True if LLVM initialization has been completed.
        """
        return self._llvm_init

    @staticmethod
    def get_optimization_level_description(level: str) -> str:
        """Get a human-readable description of an optimization level.

        Args:
            level: Optimization level string (none/o0/mem2reg/o1/o2/o3).

        Returns:
            Description of what optimizations are applied at this level.
        """
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
        """Get list of available optimization levels.

        Returns:
            List of valid optimization level strings.
        """
        return ["none", "o0", "mem2reg", "o1", "o2", "o3"]
