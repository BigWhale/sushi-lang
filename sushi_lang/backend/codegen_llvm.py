"""
LLVM backend orchestrator for the Sushi language compiler.

This module provides the main LLVM compilation interface, coordinating
between specialized subsystems for type mapping, memory management,
code emission, and optimization.

API:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
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

from sushi_lang.semantics.ast import Program, ConstDef
from sushi_lang.semantics.units import Unit
from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.backend.llvm_types import LLVMTypeSystem
from sushi_lang.backend.llvm_utils import LLVMUtils
from sushi_lang.backend.runtime import LLVMRuntime
from sushi_lang.backend.memory.scopes import ScopeManager
from sushi_lang.backend.memory.dynamic_arrays import DynamicArrayManager
from sushi_lang.backend.expressions import ExpressionEmitter
from sushi_lang.backend.statements import StatementEmitter
from sushi_lang.backend.llvm_functions import LLVMFunctionManager
from sushi_lang.backend.llvm_optimization import LLVMOptimizer
from sushi_lang.backend.string_constants import StringConstantManager
from sushi_lang.backend.stdlib_linker import StdlibLinker


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
        from sushi_lang.semantics.passes.collect import FunctionTable, PerkImplementationTable, ConstantTable
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

        # Library compilation mode (no main() wrapper)
        self.is_library_mode: bool = False

        # Library linker for custom library functions
        self.library_linker: Optional['LibraryLinker'] = None

        # Library registry for pre-parsed library metadata
        self.library_registry: Optional['LibraryRegistry'] = None

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

        Uses helper functions from sushi_lang.backend.runtime.args for cleaner implementation.

        Args:
            argc: LLVM value representing argc (i32)
            argv: LLVM value representing argv (char**)

        Returns:
            LLVM value representing the Sushi string[] dynamic array struct
        """
        from sushi_lang.backend.runtime.args import generate_argc_argv_conversion
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
        library_linker: 'LibraryLinker' = None,
        library_registry: 'LibraryRegistry' = None,
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
            main_expects_args: Whether main() expects command line args.
            monomorphized_extensions: List of monomorphized extension methods.
            library_linker: LibraryLinker instance with loaded libraries.
            library_registry: LibraryRegistry with pre-parsed library metadata.

        Returns:
            Path to the generated executable.
        """
        # Store command line arguments information
        self.main_expects_args = main_expects_args

        # Store monomorphized extensions for emission
        self.monomorphized_extensions = monomorphized_extensions or []

        # Store library linker for function declarations
        self.library_linker = library_linker

        # Store library registry for pre-parsed metadata
        self.library_registry = library_registry

        # Build high-level IR for all units
        mod_ir: ir.Module = self.build_module_multi_unit(units)

        if debug:
            print(";; Multi-unit IR (pre-opt)")
            ir_text = str(mod_ir)
            for i, line in enumerate(ir_text.splitlines(), 1):
                print(f"{i:4} {line}")

        # Convert to binding ModuleRef
        llmod = llvm.parse_assembly(str(mod_ir))

        # Collect all library and stdlib modules to link
        library_paths = set()
        stdlib_units = set()

        for unit in units:
            if unit.ast is not None:
                for use_stmt in unit.ast.uses:
                    if use_stmt.is_library:
                        library_paths.add(use_stmt.path)
                    elif use_stmt.is_stdlib:
                        stdlib_units.add(use_stmt.path)

        # Use two-phase linking if we have libraries to link
        if library_linker is not None and library_paths:
            from sushi_lang.backend.library_linker import TwoPhaseLinker

            # Get target info for the linker
            target_triple = llmod.triple if hasattr(llmod, 'triple') else ""
            data_layout = llmod.data_layout if hasattr(llmod, 'data_layout') else ""

            two_phase = TwoPhaseLinker(target_triple, data_layout, verbose=False)

            # Add main module
            two_phase.add_main_module(llmod, "main")

            # Add library modules
            from sushi_lang.backend.library_format import LibraryFormat
            for lib_path in library_paths:
                try:
                    slib_path = library_linker.resolve_library(lib_path)
                    metadata, bitcode = LibraryFormat.read(slib_path)
                    library_linker.loaded_libraries[metadata["library_name"]] = metadata

                    lib_mod = llvm.parse_bitcode(bitcode)
                    two_phase.add_library_module(lib_mod, metadata["library_name"])
                except Exception as e:
                    raise RuntimeError(f"Failed to load library {lib_path}: {e}")

            # Add stdlib modules
            for stdlib_path in stdlib_units:
                bc_paths = self.stdlib._resolve_stdlib_unit(stdlib_path)
                for bc_path in bc_paths:
                    with open(bc_path, 'rb') as f:
                        stdlib_mod = llvm.parse_bitcode(f.read())
                        two_phase.add_stdlib_module(stdlib_mod, stdlib_path)

            # Perform two-phase linking with full symbol deduplication
            llmod = two_phase.link()

        else:
            # No libraries - just link stdlib directly
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

    def compile_to_bitcode(
        self,
        units: list[Unit],
        debug: bool = False,
        opt: str = "mem2reg",
        verify: bool = True,
        monomorphized_extensions: list['ExtendDef'] = None,
    ) -> bytes:
        """Compile units to LLVM bitcode without linking to executable.

        Used for library compilation (--lib flag). Does not require main() function.

        Args:
            units: List of compilation units.
            debug: Enable debug information.
            opt: Optimization level.
            verify: Enable IR verification.
            monomorphized_extensions: List of monomorphized extension methods.

        Returns:
            LLVM bitcode as bytes.
        """
        # Store monomorphized extensions for emission
        self.monomorphized_extensions = monomorphized_extensions or []

        # Mark as library mode to skip main() wrapper
        self.is_library_mode = True

        # Build high-level IR for all units
        mod_ir: ir.Module = self.build_module_multi_unit(units)

        if debug:
            print(";; Library IR (pre-opt)")
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

        # Update self.module with optimized IR (for --write-ll to work correctly)
        self.module = llvm.parse_assembly(str(llmod))

        return llmod.as_bitcode()

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
        from sushi_lang.backend.platform_detect import get_current_platform
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

    def build_module_single_unit(self, target_unit: Unit, all_units: list[Unit]) -> ir.Module:
        """Generate LLVM IR for a single compilation unit.

        Emits full definitions for symbols owned by *target_unit* and external
        declarations for symbols from other units that this unit references.
        Monomorphized generics consumed by this unit use ``linkonce_odr`` linkage
        so the system linker can deduplicate across object files.

        Args:
            target_unit: The unit to compile.
            all_units: All compilation units (for cross-unit declaration context).

        Returns:
            A fresh LLVM module containing this unit's code.
        """
        # Create a fresh module for this unit
        saved_module = self.module
        saved_funcs = self.funcs.copy()
        saved_constants = self.constants.copy()
        saved_ast_constants = self.ast_constants.copy()

        self.module = ir.Module(name=f"unit_{target_unit.name}")
        self.funcs = {}
        self.constants = {}
        self.ast_constants = {}
        self._malloc_func = None
        self._free_func = None
        self._realloc_func = None
        self.string_manager = StringConstantManager(self)

        # Rebuild runtime-formatted strings for this fresh module
        self.runtime = LLVMRuntime(self)

        # Extract stdlib unit imports from all units for conditional code generation
        for unit in all_units:
            if unit.ast is not None:
                self.stdlib.extract_stdlib_units(unit.ast)

        self.runtime.declare_externs()

        # Pass 0: Build AST constant map from ALL units (needed for const evaluation)
        for unit in all_units:
            if unit.ast is None:
                continue
            for const in unit.ast.constants:
                self.ast_constants[const.name] = const

        # Emit constants: own unit gets full definitions, others get external declarations
        for unit in all_units:
            if unit.ast is None:
                continue
            for const in unit.ast.constants:
                if unit.name == target_unit.name:
                    self._emit_global_constant(const)
                else:
                    self._emit_global_constant(const)

        # Pass 1: Declare function prototypes
        # For the target unit: declare ALL functions (public + private, they'll get bodies)
        # For other units: only declare PUBLIC functions (private ones can't be cross-referenced)
        for unit in all_units:
            if unit.ast is None:
                continue
            for fn in unit.ast.functions:
                if hasattr(fn, 'type_params') and fn.type_params:
                    continue
                if unit.name != target_unit.name and not fn.is_public:
                    continue
                self.functions.emit_func_decl(fn)

            for ext in unit.ast.extensions:
                self.functions.emit_extension_method_decl(ext)

            for perk_impl in unit.ast.perk_impls:
                for method in perk_impl.methods:
                    from sushi_lang.semantics.ast import ExtendDef
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

        # Declare library function prototypes
        if hasattr(self, 'library_linker') and self.library_linker is not None:
            self._declare_library_functions()

        # Pass 2: Emit bodies ONLY for the target unit
        if target_unit.ast is not None:
            for fn in target_unit.ast.functions:
                if hasattr(fn, 'type_params') and fn.type_params:
                    continue
                self.functions.emit_func_def(fn)

            for ext in target_unit.ast.extensions:
                self.functions.emit_extension_method_def(ext)

            for perk_impl in target_unit.ast.perk_impls:
                for method in perk_impl.methods:
                    from sushi_lang.semantics.ast import ExtendDef
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

        # Emit monomorphized generic extension method bodies for all units
        # These use linkonce_odr linkage so the linker deduplicates
        for ext in self.monomorphized_extensions:
            self.functions.emit_extension_method_def(ext)

        # Set linkonce_odr on inline-defined runtime functions to avoid
        # duplicate symbol errors when linking multiple .o files
        _set_linkonce_odr_on_inline_runtime(self.module)

        result_module = self.module

        # Restore the original module state
        self.module = saved_module
        self.funcs = saved_funcs
        self.constants = saved_constants
        self.ast_constants = saved_ast_constants
        self._malloc_func = None
        self._free_func = None
        self._realloc_func = None
        self.string_manager = StringConstantManager(self)
        self.runtime = LLVMRuntime(self)

        return result_module

    def compile_single_unit_to_object(self, target_unit: Unit, all_units: list[Unit],
                                      opt: str = "mem2reg", verify: bool = True) -> bytes:
        """Compile a single unit to an object file (bytes).

        Does NOT link stdlib or library modules -- those are compiled to
        separate .o files and linked at the final step.

        Args:
            target_unit: The unit to compile.
            all_units: All units for cross-reference context.
            opt: Optimization level.
            verify: Whether to verify LLVM IR.

        Returns:
            Object file bytes.
        """
        mod_ir = self.build_module_single_unit(target_unit, all_units)
        llmod = llvm.parse_assembly(str(mod_ir))

        # Target setup, verify, optimize
        tm = self.optimizer.ensure_target(llmod)

        if verify:
            self.optimizer.verify(llmod, f"pre-optimization ({target_unit.name})")

        if opt != "none":
            self.optimizer.optimize(llmod, opt)

        if verify:
            self.optimizer.verify(llmod, f"post-optimization ({target_unit.name})")

        return tm.emit_object(llmod)

    def compile_stdlib_to_object(self, stdlib_unit: str, opt: str = "mem2reg") -> bytes:
        """Compile stdlib bitcode files to a single object file.

        Args:
            stdlib_unit: Stdlib unit path (e.g. "io/stdio").
            opt: Optimization level.

        Returns:
            Object file bytes.
        """
        bc_paths = self.stdlib._resolve_stdlib_unit(stdlib_unit)
        # Read and link all bitcode files for this stdlib unit
        first = True
        llmod = None
        for bc_path in bc_paths:
            with open(bc_path, 'rb') as f:
                mod = llvm.parse_bitcode(f.read())
                if first:
                    llmod = mod
                    first = False
                else:
                    llmod.link_in(mod)

        if llmod is None:
            raise RuntimeError(f"No bitcode files found for stdlib unit: {stdlib_unit}")

        tm = self.optimizer.ensure_target(llmod)

        if opt != "none":
            self.optimizer.optimize(llmod, opt)

        return tm.emit_object(llmod)

    def compile_library_to_object(self, lib_path: str, library_linker,
                                  opt: str = "mem2reg") -> bytes:
        """Compile a library .slib to an object file.

        Args:
            lib_path: Library path as used in use statements.
            library_linker: LibraryLinker with resolved libraries.
            opt: Optimization level.

        Returns:
            Object file bytes.
        """
        from sushi_lang.backend.library_format import LibraryFormat

        slib_path = library_linker.resolve_library(lib_path)
        _, bitcode = LibraryFormat.read(slib_path)
        llmod = llvm.parse_bitcode(bitcode)

        tm = self.optimizer.ensure_target(llmod)

        if opt != "none":
            self.optimizer.optimize(llmod, opt)

        return tm.emit_object(llmod)

    def link_object_files(self, obj_paths: list[Path], out: Path, cc: str = "cc",
                          debug: bool = False) -> Path:
        """Link multiple .o files into a native executable.

        Args:
            obj_paths: List of object file paths to link.
            out: Output executable path.
            cc: C compiler for linking.
            debug: Enable debug information.

        Returns:
            Path to the generated executable.
        """
        cmd = [cc] + [str(p) for p in obj_paths]
        cmd.extend(["-o", str(out)])

        from sushi_lang.backend.platform_detect import get_current_platform
        platform = get_current_platform()
        if platform.is_linux:
            cmd.append("-lm")

        if debug:
            cmd.insert(1, "-g")
        subprocess.run(cmd, check=True)
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
                    from sushi_lang.semantics.ast import ExtendDef
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

        # Declare library function prototypes if any libraries are loaded
        if hasattr(self, 'library_linker') and self.library_linker is not None:
            self._declare_library_functions()

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
                    from sushi_lang.semantics.ast import ExtendDef
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

    def _declare_library_functions(self) -> None:
        """Declare library function prototypes for external library functions.

        This creates LLVM function declarations (prototypes without bodies) for
        public functions from loaded libraries. The actual function bodies will
        be linked in from the library bitcode during the linking phase.

        Uses pre-parsed FuncSig objects from LibraryRegistry when available,
        eliminating duplicate manifest parsing.
        """
        from sushi_lang.semantics.typesys import ResultType

        # Use library_registry if available (pre-parsed metadata)
        if self.library_registry is not None:
            self._declare_library_functions_from_registry()
            return

        # Fallback to manual parsing from library_linker
        if self.library_linker is None:
            return

        from sushi_lang.semantics.type_resolution import parse_type_string

        for lib_name, manifest in self.library_linker.loaded_libraries.items():
            for func_info in manifest.get("public_functions", []):
                func_name = func_info["name"]
                if func_name in self.funcs:
                    continue

                param_types = []
                for p in func_info.get("params", []):
                    param_type = parse_type_string(
                        p["type"],
                        self.struct_table.by_name if self.struct_table else {},
                        self.enum_table.by_name if self.enum_table else {}
                    )
                    param_types.append(self.types.ll_type(param_type))

                ret_type_str = func_info.get("return_type", "~")
                ret_type = parse_type_string(
                    ret_type_str,
                    self.struct_table.by_name if self.struct_table else {},
                    self.enum_table.by_name if self.enum_table else {}
                )

                std_error = self.enum_table.by_name.get("StdError") if self.enum_table else None
                result_type = ResultType(ok_type=ret_type, err_type=std_error if std_error else ret_type)
                ll_ret = self.types.ll_type(result_type)

                fnty = ir.FunctionType(ll_ret, param_types)
                llvm_fn = ir.Function(self.module, fnty, name=func_name)
                llvm_fn.linkage = 'external'

                for i, p in enumerate(func_info.get("params", [])):
                    if i < len(llvm_fn.args):
                        llvm_fn.args[i].name = p["name"]

                self.funcs[func_name] = llvm_fn
                self.function_return_types[func_name] = result_type

    def _declare_library_functions_from_registry(self) -> None:
        """Declare library functions using pre-parsed FuncSig from registry."""
        from sushi_lang.semantics.typesys import ResultType

        for func_name, func_sig in self.library_registry.get_all_functions().items():
            if func_name in self.funcs:
                continue

            param_types = [self.types.ll_type(p.ty) for p in func_sig.params]
            ret_type = func_sig.ret_type

            std_error = self.enum_table.by_name.get("StdError") if self.enum_table else None
            result_type = ResultType(ok_type=ret_type, err_type=std_error if std_error else ret_type)
            ll_ret = self.types.ll_type(result_type)

            fnty = ir.FunctionType(ll_ret, param_types)
            llvm_fn = ir.Function(self.module, fnty, name=func_name)
            llvm_fn.linkage = 'external'

            for i, p in enumerate(func_sig.params):
                if i < len(llvm_fn.args):
                    llvm_fn.args[i].name = p.name

            self.funcs[func_name] = llvm_fn
            self.function_return_types[func_name] = result_type

    def _emit_global_constant(self, const: ConstDef) -> None:
        """Emit a global constant definition.

        Creates a global constant value that can be referenced throughout
        the program. Constants are evaluated at compile time.

        Args:
            const: The constant definition to emit.
        """
        from sushi_lang.semantics.ast import StringLit

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
        from sushi_lang.semantics.ast import StringLit
        from sushi_lang.semantics.passes.const_eval import ConstantEvaluator
        from sushi_lang.internals.report import Reporter

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


# Known inline-defined runtime functions that appear in every module.
# These need linkonce_odr linkage for separate compilation.
_INLINE_RUNTIME_FUNCTIONS = frozenset({
    "llvm_strlen",
    "llvm_strcmp",
    "utf8_char_count",
})


def _set_linkonce_odr_on_inline_runtime(module: ir.Module) -> None:
    """Set linkonce_odr linkage on inline-defined runtime functions.

    These functions are emitted with full bodies into every compilation
    unit's module. Without linkonce_odr, linking multiple .o files together
    produces duplicate symbol errors.
    """
    for name in _INLINE_RUNTIME_FUNCTIONS:
        fn = module.globals.get(name)
        if fn is not None and isinstance(fn, ir.Function) and not fn.is_declaration:
            fn.linkage = "linkonce_odr"
