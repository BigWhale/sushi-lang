# semantics/pipeline.py
"""Semantic analysis pipeline with timing and flexible pass management.

Provides a structured way to execute semantic analysis passes with:
- Timing instrumentation for performance analysis
- Named pass execution for debugging
- Shared context between passes
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from semantics.ast import Program


@dataclass
class PassResult:
    """Result of executing a single pass."""
    name: str
    duration_ms: float
    success: bool
    error: Optional[str] = None


@dataclass
class PipelineContext:
    """Shared context passed between pipeline passes.

    Stores tables and intermediate results that passes need to share.
    """
    # Symbol tables (populated by collector pass)
    constants: Any = None
    structs: Any = None
    enums: Any = None
    generic_enums: Any = None
    generic_structs: Any = None
    perks: Any = None
    perk_impls: Any = None
    funcs: Any = None
    extensions: Any = None
    generic_extensions: Any = None
    generic_funcs: Any = None

    # Intermediate results
    type_instantiations: Any = None
    func_instantiations: Any = None
    monomorphizer: Any = None
    concrete_enums: Dict[str, Any] = field(default_factory=dict)
    concrete_structs: Dict[str, Any] = field(default_factory=dict)
    monomorphized_extensions: List[Any] = field(default_factory=list)

    # Configuration
    main_expects_args: bool = False


class SemanticPipeline:
    """Manages semantic analysis passes with timing instrumentation.

    Example usage:
        pipeline = SemanticPipeline(reporter)
        pipeline.add_pass("collect", collector_pass_fn)
        pipeline.add_pass("instantiate", instantiate_pass_fn)
        pipeline.add_pass("scope", scope_pass_fn)
        pipeline.add_pass("types", type_pass_fn)
        pipeline.add_pass("borrow", borrow_pass_fn)
        results = pipeline.execute(program)
    """

    def __init__(self, reporter: 'Reporter', verbose: bool = False) -> None:
        """Initialize pipeline.

        Args:
            reporter: Error reporter for diagnostics
            verbose: If True, print timing info after execution
        """
        self.reporter = reporter
        self.verbose = verbose
        self._passes: List[tuple[str, Callable[[Program, PipelineContext], None]]] = []
        self._results: List[PassResult] = []
        self.context = PipelineContext()

    def add_pass(
        self,
        name: str,
        pass_fn: Callable[['Program', PipelineContext], None],
    ) -> 'SemanticPipeline':
        """Register a pass in the pipeline.

        Args:
            name: Human-readable name for the pass
            pass_fn: Function that takes (program, context) and runs the pass

        Returns:
            Self for method chaining
        """
        self._passes.append((name, pass_fn))
        return self

    def execute(self, program: 'Program') -> List[PassResult]:
        """Execute all registered passes in order.

        Args:
            program: The AST to analyze

        Returns:
            List of PassResult with timing information
        """
        self._results = []

        for name, pass_fn in self._passes:
            start = time.perf_counter()
            error_msg = None
            success = True

            try:
                pass_fn(program, self.context)
            except Exception as e:
                success = False
                error_msg = str(e)
                # Re-raise to stop pipeline on error
                raise

            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                result = PassResult(
                    name=name,
                    duration_ms=duration_ms,
                    success=success,
                    error=error_msg,
                )
                self._results.append(result)

        if self.verbose:
            self._print_timing()

        return self._results

    def get_results(self) -> List[PassResult]:
        """Get results from last execution."""
        return self._results

    def total_duration_ms(self) -> float:
        """Get total duration of all passes in milliseconds."""
        return sum(r.duration_ms for r in self._results)

    def _print_timing(self) -> None:
        """Print timing summary to stderr."""
        import sys
        total = self.total_duration_ms()
        print("\n=== Semantic Analysis Timing ===", file=sys.stderr)
        for result in self._results:
            pct = (result.duration_ms / total * 100) if total > 0 else 0
            status = "OK" if result.success else "FAIL"
            print(f"  {result.name:30} {result.duration_ms:8.2f}ms ({pct:5.1f}%) [{status}]", file=sys.stderr)
        print(f"  {'TOTAL':30} {total:8.2f}ms", file=sys.stderr)
        print("=" * 40, file=sys.stderr)


def create_standard_pipeline(
    reporter: 'Reporter',
    library_linker: Optional[Any] = None,
    verbose: bool = False,
) -> SemanticPipeline:
    """Create a pipeline with standard semantic analysis passes.

    This is a convenience function that sets up the default pass order.

    Args:
        reporter: Error reporter
        library_linker: Optional library linker for multi-file compilation
        verbose: If True, print timing after execution

    Returns:
        Configured SemanticPipeline
    """
    from semantics.passes.collect import CollectorPass
    from semantics.passes.scope import ScopeAnalyzer
    from semantics.passes.types import TypeValidator
    from semantics.passes.borrow import BorrowChecker

    pipeline = SemanticPipeline(reporter, verbose=verbose)

    def collect_pass(program: 'Program', ctx: PipelineContext) -> None:
        collector = CollectorPass(reporter)
        (ctx.constants, ctx.structs, ctx.enums, ctx.generic_enums,
         ctx.generic_structs, ctx.perks, ctx.perk_impls, ctx.funcs,
         ctx.extensions, ctx.generic_extensions, ctx.generic_funcs) = collector.run(program)

    def instantiate_pass(program: 'Program', ctx: PipelineContext) -> None:
        from semantics.generics.instantiate import InstantiationCollector
        collector = InstantiationCollector(
            struct_table=ctx.structs.by_name,
            enum_table=ctx.enums.by_name,
            generic_structs=ctx.generic_structs.by_name,
            generic_funcs=ctx.generic_funcs.by_name,
        )
        ctx.type_instantiations, ctx.func_instantiations = collector.run(program)

    def monomorphize_pass(program: 'Program', ctx: PipelineContext) -> None:
        from semantics.generics.monomorphize import Monomorphizer
        from semantics.generics.constraints import ConstraintValidator

        constraint_validator = ConstraintValidator(
            perk_table=ctx.perks,
            perk_impl_table=ctx.perk_impls,
            reporter=reporter,
        )

        monomorphizer = Monomorphizer(
            reporter=reporter,
            constraint_validator=constraint_validator,
        )

        # Separate enum and struct instantiations
        enum_instantiations = set()
        struct_instantiations = set()
        for base_name, type_args in ctx.type_instantiations:
            if base_name == "Result" and len(type_args) == 2:
                continue
            if base_name in ctx.generic_enums.by_name:
                enum_instantiations.add((base_name, type_args))
            elif base_name in ctx.generic_structs.by_name:
                struct_instantiations.add((base_name, type_args))

        # Monomorphize generic enums
        ctx.concrete_enums = monomorphizer.monomorphize_all(
            ctx.generic_enums.by_name, enum_instantiations
        )
        for enum_name, enum_type in ctx.concrete_enums.items():
            ctx.enums.by_name[enum_name] = enum_type
            ctx.enums.order.append(enum_name)

        # Set up function monomorphization
        monomorphizer.generic_funcs = ctx.generic_funcs.by_name
        monomorphizer.generic_enums = ctx.generic_enums.by_name
        monomorphizer.generic_structs = ctx.generic_structs.by_name
        monomorphizer.func_table = ctx.funcs
        monomorphizer.enum_table = ctx.enums
        monomorphizer.struct_table = ctx.structs
        monomorphizer.monomorphize_all_functions(ctx.func_instantiations, program)

        # Monomorphize generic structs
        ctx.concrete_structs = monomorphizer.monomorphize_all_structs(
            ctx.generic_structs.by_name, struct_instantiations
        )
        for struct_name, struct_type in ctx.concrete_structs.items():
            ctx.structs.by_name[struct_name] = struct_type
            ctx.structs.order.append(struct_name)

        ctx.monomorphizer = monomorphizer

    def ast_transform_pass(program: 'Program', ctx: PipelineContext) -> None:
        from semantics.passes.ast_transform import (
            resolve_struct_field_types, resolve_enum_variant_types
        )
        resolve_struct_field_types(ctx.structs, ctx.enums)
        resolve_enum_variant_types(ctx.structs, ctx.enums)

    def hash_registration_pass(program: 'Program', ctx: PipelineContext) -> None:
        from semantics.passes.hash_registration import (
            register_all_struct_hashes, register_all_enum_hashes, register_all_array_hashes
        )
        register_all_struct_hashes(ctx.structs)
        register_all_enum_hashes(ctx.enums, reporter)
        register_all_array_hashes(ctx.structs, ctx.enums)

    def extension_monomorphize_pass(program: 'Program', ctx: PipelineContext) -> None:
        from backend.generics.extensions import monomorphize_all_extension_methods
        from semantics.passes.collect import ExtensionMethod

        # Build struct_instantiations from concrete_structs
        struct_instantiations = set()
        for name in ctx.concrete_structs:
            # Parse name to extract base name and type args
            if '<' in name:
                base = name[:name.index('<')]
                # Simple extraction - would need refinement for complex cases
                struct_instantiations.add((base, ()))

        concrete_extension_defs = monomorphize_all_extension_methods(
            ctx.generic_extensions.by_type,
            struct_instantiations,
            ctx.concrete_structs,
        )

        for (target_type_name, method_name, type_args), extend_def in concrete_extension_defs.items():
            ctx.monomorphized_extensions.append(extend_def)
            extension_method = ExtensionMethod(
                target_type=extend_def.target_type,
                name=extend_def.name,
                params=extend_def.params,
                ret_type=extend_def.ret,
            )
            ctx.extensions.add_method(extension_method)

    def scope_pass(program: 'Program', ctx: PipelineContext) -> None:
        analyzer = ScopeAnalyzer(
            reporter,
            ctx.constants,
            ctx.structs,
            ctx.enums,
            ctx.generic_enums,
            ctx.generic_structs,
        )
        analyzer.run(program)

    def type_pass(program: 'Program', ctx: PipelineContext) -> None:
        monomorphized_funcs = {}
        if ctx.monomorphizer:
            monomorphized_funcs = ctx.monomorphizer.monomorphized_functions

        validator = TypeValidator(
            reporter,
            ctx.constants,
            ctx.structs,
            ctx.enums,
            ctx.funcs,
            ctx.extensions,
            ctx.generic_enums,
            ctx.generic_structs,
            ctx.perks,
            ctx.perk_impls,
            ctx.generic_extensions,
            ctx.generic_funcs,
            monomorphized_functions=monomorphized_funcs,
        )
        validator.run(program)

        # Validate monomorphized extension methods
        for extend_def in ctx.monomorphized_extensions:
            validator._validate_extension_method(extend_def)

    def borrow_pass(program: 'Program', ctx: PipelineContext) -> None:
        checker = BorrowChecker(
            reporter,
            struct_names=ctx.structs.by_name if ctx.structs else None,
        )
        checker.run(program)

    # Register passes in order
    pipeline.add_pass("collect", collect_pass)
    pipeline.add_pass("instantiate", instantiate_pass)
    pipeline.add_pass("monomorphize", monomorphize_pass)
    pipeline.add_pass("ast_transform", ast_transform_pass)
    pipeline.add_pass("hash_registration", hash_registration_pass)
    pipeline.add_pass("extension_monomorphize", extension_monomorphize_pass)
    pipeline.add_pass("scope", scope_pass)
    pipeline.add_pass("types", type_pass)
    pipeline.add_pass("borrow", borrow_pass)

    return pipeline
