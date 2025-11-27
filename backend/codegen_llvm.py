"""
LLVM backend orchestrator for the Sushi language compiler.

This module provides the main LLVM compilation interface, coordinating
between specialized subsystems for type mapping, memory management,
code emission, and optimization.

API:
    from backend.codegen_llvm import LLVMCodegen
    cg = LLVMCodegen()
    exe_path = cg.compile(program_ast, out=Path("a.out"), cc="clang")

If you only want the LLVM IR string without linking, call `build_module()`
then `str(cg.module)`.
"""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Dict, Optional

from llvmlite import ir, binding as llvm

from semantics.ast import Program, ConstDef
from semantics.units import Unit
from semantics.passes.collect import StructTable, EnumTable
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from backend.llvm_types import LLVMTypeSystem
from backend.llvm_utils import LLVMUtils
from backend.runtime import LLVMRuntime
from backend.memory.scopes import ScopeManager
from backend.memory.dynamic_arrays import DynamicArrayManager
from backend.expressions import ExpressionEmitter
from backend.statements import StatementEmitter
from backend.llvm_functions import LLVMFunctionManager
from backend.llvm_optimization import LLVMOptimizer
from backend.string_constants import StringConstantManager
from backend.stdlib_linker import StdlibLinker


class LLVMCodegen:
    """Main LLVM backend orchestrator for the Sushi language compiler."""

    def __init__(self, module_name: str = "lang_module", struct_table: Optional[StructTable] = None, enum_table: Optional[EnumTable] = None, func_table: Optional['FunctionTable'] = None, perk_impl_table: Optional['PerkImplementationTable'] = None, const_table: Optional['ConstantTable'] = None) -> None:
        """Initialize the LLVM code generator with all specialized subsystems.

        Args:
            module_name: Name for the LLVM module.
            struct_table: Optional struct table for resolving struct types.
            enum_table: Optional enum table for resolving enum types.
            func_table: Optional function table for stdlib function lookup.
            perk_impl_table: Optional perk implementation table for method resolution.
            const_table: Optional constant table for constant evaluation.
        """
        self.module: ir.Module = ir.Module(name=module_name)
        self.struct_table = struct_table or StructTable()
        self.enum_table = enum_table or EnumTable()
        from semantics.passes.collect import FunctionTable, PerkImplementationTable, ConstantTable
        self.func_table = func_table or FunctionTable()
        self.perk_impl_table = perk_impl_table or PerkImplementationTable()
        self.const_table = const_table or ConstantTable()

        # Initialize specialized subsystems following SOLID principles
        self.types = LLVMTypeSystem(struct_table=self.struct_table, enum_table=self.enum_table)
        self.utils = LLVMUtils(self)
        self.runtime = LLVMRuntime(self)
        self.memory = ScopeManager(self)
        self.dynamic_arrays: Optional[DynamicArrayManager] = None  # Will be initialized when builder is available
        self.expressions = ExpressionEmitter(self)
        self.statements = StatementEmitter(self)
        self.functions = LLVMFunctionManager(self)
        self.optimizer = LLVMOptimizer(self)

        # Specialized managers for common operations
        self.string_manager = StringConstantManager(self)
        self.stdlib = StdlibLinker(self)

        # Type properties for convenient access
        self.i32 = self.types.i32
        self.i8 = self.types.i8
        self.i1 = self.types.i1
        self.str_ptr = self.types.str_ptr
        self.void = self.types.void

        # Per-function compilation state
        self.builder: Optional[ir.IRBuilder] = None
        self.alloca_builder: Optional[ir.IRBuilder] = None
        self.func: Optional[ir.Function] = None
        self.entry_block: Optional[ir.Block] = None
        self.entry_branch: Optional[ir.Instruction] = None
        self.in_extension_method: bool = False  # Track if compiling extension method

        # Loop context tracking for break/continue statements
        self.loop_stack: list[tuple[ir.Block, ir.Block]] = []

        # Function registry for declared functions
        self.funcs: Dict[str, ir.Function] = {}

        # Global constants registry
        self.constants: Dict[str, ir.GlobalVariable] = {}

        # Centralized memory management function declarations
        self._malloc_func: Optional[ir.Function] = None
        self._free_func: Optional[ir.Function] = None
        self._realloc_func: Optional[ir.Function] = None

        # Command line arguments support
        self.main_expects_args: bool = False

        # Monomorphized generic extension methods (for codegen)
        self.monomorphized_extensions: list['ExtendDef'] = []

        # Variable type tracking (Sushi language types, not LLVM types)
        # Maps variable name to its Sushi Type for struct member access resolution
        self.variable_types: Dict[str, 'Type'] = {}

        # Track which stdlib units are imported (for conditional code generation)
        self.stdlib_units: set[str] = set()

        # Function return type tracking (Sushi language types, not LLVM types)
        # Maps function name to its return type (pre-Result wrapping)
        # Used for inferring Result<T> types from function call expressions
        self.function_return_types: Dict[str, 'Type'] = {}

        # Current function being compiled (AST node)
        # Used by ?? operator to get the enclosing function's return type
        self.current_function_ast: Optional['FuncDef'] = None

        # AST constant definitions (for constant evaluation in backend)
        self.ast_constants: Dict[str, ConstDef] = {}

    # Properties for runtime function access
    @property
    def printf(self) -> ir.Function | None:
        """Access to printf runtime function."""
        return self.runtime.libc_stdio.printf

    @property
    def strcmp(self) -> ir.Function | None:
        """Access to strcmp runtime function."""
        return self.runtime.libc_strings.strcmp

    @property
    def fmt_i32(self) -> ir.GlobalVariable | None:
        """Access to integer format string."""
        return self.runtime.formatting.fmt_i32

    @property
    def fmt_str(self) -> ir.GlobalVariable | None:
        """Access to string format string."""
        return self.runtime.formatting.fmt_str

    @property
    def fmt_f32(self) -> ir.GlobalVariable | None:
        """Access to f32 format string."""
        return self.runtime.formatting.fmt_f32

    @property
    def fmt_f64(self) -> ir.GlobalVariable | None:
        """Access to f64 format string."""
        return self.runtime.formatting.fmt_f64

    # Centralized memory management function declarations
    def get_malloc_func(self) -> ir.Function:
        """Get or declare malloc function."""
        if self._malloc_func is None:
            # void* malloc(size_t size)
            malloc_type = ir.FunctionType(
                ir.PointerType(ir.IntType(INT8_BIT_WIDTH)),  # void*
                [ir.IntType(INT64_BIT_WIDTH)]                # size_t
            )
            self._malloc_func = ir.Function(self.module, malloc_type, name="malloc")
        return self._malloc_func

    def get_free_func(self) -> ir.Function:
        """Get or declare free function."""
        if self._free_func is None:
            # void free(void* ptr)
            free_type = ir.FunctionType(
                ir.VoidType(),                   # void
                [ir.PointerType(ir.IntType(INT8_BIT_WIDTH))]  # void*
            )
            self._free_func = ir.Function(self.module, free_type, name="free")
        return self._free_func

    def get_realloc_func(self) -> ir.Function:
        """Get or declare realloc function."""
        if self._realloc_func is None:
            # void* realloc(void* ptr, size_t size)
            realloc_type = ir.FunctionType(
                ir.PointerType(ir.IntType(INT8_BIT_WIDTH)),  # void*
                [ir.PointerType(ir.IntType(INT8_BIT_WIDTH)), ir.IntType(INT64_BIT_WIDTH)]  # void*, size_t
            )
            self._realloc_func = ir.Function(self.module, realloc_type, name="realloc")
        return self._realloc_func

    def create_string_constant(self, name: str, value: str) -> ir.GlobalVariable:
        """Create a global string constant without requiring a builder context.

        Args:
            name: Name of the constant.
            value: String value.

        Returns:
            The global variable containing the string array.
        """
        return self.string_manager.create_string_constant(name, value)

    def _generate_argc_argv_conversion(self, argc: ir.Value, argv: ir.Value) -> ir.Value:
        """Convert C-style argc/argv to Sushi string[] dynamic array.

        Uses helper functions from backend.runtime.args for cleaner implementation.

        Args:
            argc: LLVM value representing argc (i32)
            argv: LLVM value representing argv (char**)

        Returns:
            LLVM value representing the Sushi string[] dynamic array struct
        """
        from backend.runtime.args import generate_argc_argv_conversion
        return generate_argc_argv_conversion(self, argc, argv)

    def build_module_multi_unit(self, units: list[Unit]) -> ir.Module:
        """Generate LLVM IR for multiple compilation units and return the module.

        This method compiles all units together into a single LLVM module,
        handling cross-unit symbol resolution and visibility rules.

        Args:
            units: List of compilation units in dependency order.

        Returns:
            The completed LLVM module containing all units.
        """
        # Extract stdlib unit imports from all units for conditional code generation
        for unit in units:
            if unit.ast is not None:
                self.stdlib.extract_stdlib_units(unit.ast)

        self.runtime.declare_externs()
        self._emit_multi_unit_program(units)
        return self.module

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
        monomorphized_extensions: list['ExtendDef'] = None,
    ) -> Path:
        """Complete multi-unit compilation pipeline from multiple ASTs to native executable.

        Args:
            units: List of compilation units in dependency order.
            out: Output executable path.
            cc: C compiler for linking.
            debug: Enable debug information.
            opt: Optimization level (none/mem2reg/o1/o2/o3).
            verify: Enable IR verification.
            keep_object: Retain object files after linking.

        Returns:
            Path to the generated executable.
        """
        # Store command line arguments information
        self.main_expects_args = main_expects_args

        # Store monomorphized extensions for emission
        self.monomorphized_extensions = monomorphized_extensions or []

        # Build high-level IR for all units
        mod_ir: ir.Module = self.build_module_multi_unit(units)

        if debug:
            print(";; Multi-unit IR (pre-opt)")
            ir_text = str(mod_ir)
            for i, line in enumerate(ir_text.splitlines(), 1):
                print(f"{i:4} {line}")

        # Convert to binding ModuleRef
        llmod = llvm.parse_assembly(str(mod_ir))

        # Link stdlib modules if any units import them
        for unit in units:
            if unit.ast is not None:
                self.stdlib.link_stdlib_modules(llmod, unit.ast)

        # Set up target information
        self.optimizer.ensure_target(llmod)

        if verify:
            self.optimizer.verify(llmod, "pre-optimization")

        # Optimize if requested
        if opt != "none":
            self.optimizer.optimize(llmod, opt)

        if verify:
            self.optimizer.verify(llmod, "post-optimization")

        # Update self.module with optimized IR (for --emit-ll to work correctly)
        self.module = llvm.parse_assembly(str(llmod))

        # Generate native executable
        out_path = out or Path("a.out")
        return self._link_executable(llmod, out_path, cc, debug, keep_object=keep_object)

    def _link_executable(
        self,
        llmod: llvm.ModuleRef,
        out: Path,
        cc: str,
        debug: bool,
        tm: Optional[llvm.TargetMachine] = None,
        keep_object: bool = False,
    ) -> Path:
        """Emit object file and link to native executable.

        Args:
            llmod: The LLVM module to compile.
            out: Output executable path.
            cc: C compiler for linking.
            debug: Enable debug information.
            tm: Target machine (auto-created if None).
            keep_object: Retain object files after linking.

        Returns:
            Path to the generated executable.
        """
        self.optimizer.ensure_llvm()

        # Get target machine
        if tm is None:
            tm = self.optimizer.ensure_target(llmod)

        # Emit object bytes
        obj_bytes = tm.emit_object(llmod)

        # Write temporary object file
        obj_path = out.with_suffix(".o")
        obj_path.write_bytes(obj_bytes)

        # Link to native executable
        cmd = [cc, str(obj_path)]
        cmd.extend(["-o", str(out)])

        # Add platform-specific linker flags
        from backend.platform_detect import get_current_platform
        platform = get_current_platform()
        if platform.is_linux:
            # Link math library on Linux (required for pow, sqrt, etc.)
            cmd.append("-lm")

        if debug:
            cmd.insert(1, "-g")
        subprocess.run(cmd, check=True)

        # Clean up object file unless requested to keep it
        if not keep_object:
            obj_path.unlink()
        return out

    def has_stdlib_unit(self, unit_path: str) -> bool:
        """Check if a stdlib unit has been imported.

        Args:
            unit_path: Unit path like "core/primitives" or "collections/strings"

        Returns:
            True if the unit was imported via use <unit> syntax

        Note:
            Supports directory imports. If "collections" is imported,
            then has_stdlib_unit("collections/strings") returns True.
        """
        return self.stdlib.has_stdlib_unit(unit_path)

    def _emit_multi_unit_program(self, units: list[Unit]) -> None:
        """Emit LLVM IR for multiple compilation units.

        Multi-unit compilation strategy:
        1. Emit global constants from all units (public only for cross-unit access)
        2. Declare function prototypes from all units (handles forward references)
        3. Emit function bodies from all units

        Args:
            units: List of compilation units in dependency order.
        """
        # Pass 0: Build AST constant map and emit global constants from all units
        for unit in units:
            if unit.ast is None:
                continue

            # Build AST constant map for constant evaluator
            for const in unit.ast.constants:
                self.ast_constants[const.name] = const

            # Emit all constants (both public and private)
            # Each unit needs access to its own constants
            for const in unit.ast.constants:
                self._emit_global_constant(const)

        # Pass 1: Declare all function prototypes from all units
        for unit in units:
            if unit.ast is None:
                continue

            # Declare regular functions (both public and private)
            for fn in unit.ast.functions:
                # Skip generic functions in Phase 1 (not monomorphized yet)
                if hasattr(fn, 'type_params') and fn.type_params:
                    continue
                self.functions.emit_func_decl(fn)

            # Declare non-generic extension methods (always global)
            # Generic extension methods are handled by monomorphization
            for ext in unit.ast.extensions:
                self.functions.emit_extension_method_decl(ext)

            # Declare perk implementation methods
            # Perk methods are declared as extension methods
            for perk_impl in unit.ast.perk_impls:
                for method in perk_impl.methods:
                    # Create synthetic ExtendDef for declaration
                    from semantics.ast import ExtendDef
                    synthetic_ext = ExtendDef(
                        target_type=perk_impl.target_type,
                        name=method.name,
                        params=method.params,
                        ret=method.ret,
                        body=method.body,
                        loc=method.loc,
                        name_span=method.name_span,
                        ret_span=method.ret_span
                    )
                    self.functions.emit_extension_method_decl(synthetic_ext)

        # Declare monomorphized generic extension methods
        for ext in self.monomorphized_extensions:
            self.functions.emit_extension_method_decl(ext)

        # Pass 2: Emit function bodies from all units
        for unit in units:
            if unit.ast is None:
                continue

            # Emit regular function bodies
            for fn in unit.ast.functions:
                # Skip generic functions in Phase 1 (not monomorphized yet)
                if hasattr(fn, 'type_params') and fn.type_params:
                    continue
                self.functions.emit_func_def(fn)

            # Emit non-generic extension method bodies
            # Generic extension methods are handled by monomorphization
            for ext in unit.ast.extensions:
                self.functions.emit_extension_method_def(ext)

            # Emit perk implementation methods
            # Perk methods are emitted as extension methods (bare return types)
            for perk_impl in unit.ast.perk_impls:
                for method in perk_impl.methods:
                    # Convert perk method to extension-like structure for emission
                    # Create a synthetic ExtendDef with the perk method
                    from semantics.ast import ExtendDef
                    synthetic_ext = ExtendDef(
                        target_type=perk_impl.target_type,
                        name=method.name,
                        params=method.params,
                        ret=method.ret,
                        body=method.body,
                        loc=method.loc,
                        name_span=method.name_span,
                        ret_span=method.ret_span
                    )
                    self.functions.emit_extension_method_def(synthetic_ext)

        # Emit monomorphized generic extension method bodies
        for ext in self.monomorphized_extensions:
            self.functions.emit_extension_method_def(ext)

    def _emit_global_constant(self, const: ConstDef) -> None:
        """Emit a global constant definition.

        Creates a global constant value that can be referenced throughout
        the program. Constants are evaluated at compile time.

        Args:
            const: The constant definition to emit.
        """
        from semantics.ast import StringLit

        # Map Sushi type to LLVM type
        if const.ty is None:
            return  # Skip constants with no type (should be caught in semantic analysis)

        # Handle string constants specially - create fat pointer struct {i8*, i32}
        if isinstance(const.value, StringLit):
            string_data = const.value.value.encode('utf-8')
            size = len(string_data)

            # Create global array for string data (without null terminator)
            array_type = ir.ArrayType(self.i8, size)
            data_global = ir.GlobalVariable(self.module, array_type, name=f".str_data.{const.name}")
            data_global.linkage = 'internal'
            data_global.global_constant = True
            data_global.initializer = ir.Constant(array_type, bytearray(string_data))
            data_global.unnamed_addr = True

            # Create GEP constant to get pointer to first element
            zero = ir.Constant(self.i32, 0)
            data_ptr = data_global.gep([zero, zero])

            # Create fat pointer struct constant {i8*, i32}
            string_struct_type = self.types.string_struct
            size_value = ir.Constant(self.i32, size)
            struct_value = ir.Constant.literal_struct([data_ptr, size_value])

            # Create global variable to hold the fat pointer struct
            struct_global = ir.GlobalVariable(self.module, string_struct_type, name=const.name)
            struct_global.linkage = 'internal'
            struct_global.global_constant = True
            struct_global.initializer = struct_value
            struct_global.unnamed_addr = True

            # Register in constants dict (not string_constants)
            self.constants[const.name] = struct_global
            return

        llvm_type = self.types.ll_type(const.ty)
        if llvm_type is None:
            return  # Skip unsupported types

        # Evaluate the constant value expression at compile time
        # For now, we only support literal expressions in constants
        const_value = self._evaluate_constant_expression(const.value)
        if const_value is None:
            return  # Skip non-constant expressions

        # Create global constant with appropriate linkage based on visibility
        global_const = ir.GlobalVariable(self.module, llvm_type, name=const.name)

        # For single-module compilation (our current approach), all constants use internal linkage
        # Cross-unit visibility is already handled by being in the same LLVM module
        # Future consideration: True separate compilation (compiling units to separate .o files)
        #   would require external linkage for public constants. However, current single-module
        #   approach works perfectly for multi-file projects and simplifies the compilation model.
        global_const.linkage = 'internal'

        global_const.global_constant = True
        global_const.initializer = const_value
        global_const.unnamed_addr = True  # Allow merging identical constants

        # Register the constant for later reference
        self.constants[const.name] = global_const

    def _evaluate_constant_expression(self, expr, expected_type=None) -> Optional[ir.Constant]:
        """Evaluate a constant expression at compile time.

        Returns an LLVM constant value or None if the expression
        cannot be evaluated at compile time.

        Args:
            expr: The expression to evaluate.
            expected_type: Expected Sushi type for the constant.

        Returns:
            The LLVM constant value or None.
        """
        from semantics.ast import StringLit
        from semantics.passes.const_eval import ConstantEvaluator
        from internals.report import Reporter

        # String constants require special handling - fall back to old behavior
        if isinstance(expr, StringLit):
            return None

        # Use constant evaluator to get Python value
        # Create a silent reporter since we're in the backend (errors should have been caught in Pass 2)
        silent_reporter = Reporter()
        evaluator = ConstantEvaluator(silent_reporter, self.const_table, self.ast_constants)
        const_value = evaluator.evaluate(expr, expected_type, None)

        if const_value is None:
            # Not a compile-time constant or evaluation failed
            return None

        # Convert to LLVM constant
        llvm_const = const_value.to_llvm_constant(self.types)
        return llvm_const
