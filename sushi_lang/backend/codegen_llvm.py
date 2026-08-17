"""LLVM backend orchestrator for the Sushi language compiler."""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from llvmlite import ir, binding as llvm

if TYPE_CHECKING:
    from sushi_lang.backend.library_paths import LibraryResolver
    from sushi_lang.semantics.ast import ExtendWithDef, FuncDef
    from sushi_lang.semantics.typesys import Type
    from sushi_lang.semantics.passes.collect import FunctionTable, PerkImplementationTable, ConstantTable

from sushi_lang.semantics.ast import ConstDef, ExtendDef
from sushi_lang.semantics.units import Unit
from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.semantics.library_registry import LibraryRegistry
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.backend.llvm_types import LLVMTypeSystem
from sushi_lang.backend.llvm_utils import LLVMUtils
from sushi_lang.backend.runtime import LLVMRuntime
from sushi_lang.backend.memory.scopes import ScopeManager
from sushi_lang.backend.memory.dynamic_arrays import DynamicArrayManager
from sushi_lang.backend.memory.moves import MoveTracker
from sushi_lang.backend.expressions import ExpressionEmitter
from sushi_lang.backend.statements import StatementEmitter
from sushi_lang.backend.functions import LLVMFunctionManager
from sushi_lang.backend.llvm_optimization import LLVMOptimizer
from sushi_lang.backend.string_constants import StringConstantManager
from sushi_lang.backend.stdlib_linker import StdlibLinker
# Registers the hash() emitter factories that semantics/generics/hashing.py
# resolves when it emits an auto-derived hash(). Pass 1.8 registers the method
# itself without knowing anything about LLVM.
import sushi_lang.backend.types  # noqa: F401


def _perk_method_to_extend_def(perk_impl, method) -> ExtendDef:
    """Wrap a perk-impl method as a synthetic ExtendDef."""
    return ExtendDef(
        target_type=perk_impl.target_type,
        name=method.name,
        params=method.params,
        ret=method.ret,
        body=method.body,
        loc=method.loc,
        name_span=method.name_span,
        ret_span=method.ret_span,
        # The receiver mode MUST ride along (#327): dropping it here would compile the
        # impl with a by-value receiver against a poke-self declaration -- the silently
        # lost write of #326, reintroduced through the perk door.
        self_mode=getattr(method, "self_mode", None),
        self_mode_span=getattr(method, "self_mode_span", None),
    )


class LLVMCodegen:
    """Main LLVM backend orchestrator for the Sushi language compiler."""

    def __init__(self, module_name: str = "lang_module", struct_table: Optional[StructTable] = None, enum_table: Optional[EnumTable] = None, func_table: Optional['FunctionTable'] = None, perk_impl_table: Optional['PerkImplementationTable'] = None, const_table: Optional['ConstantTable'] = None) -> None:
        """Initialize the LLVM code generator with all specialized subsystems."""
        # Our OWN context, not llvmlite's process-wide global_context: identified types
        # (#257) register per Context, so on the global one a second `struct Tree` would
        # find the first compilation's type and inherit its layout. Every module shares
        # this one, because the type cache is whole-program and not reset per unit.
        self.llvm_context: ir.Context = ir.Context()
        self.module: ir.Module = ir.Module(name=module_name, context=self.llvm_context)
        self.struct_table = struct_table or StructTable()
        self.enum_table = enum_table or EnumTable()
        from sushi_lang.semantics.passes.collect import FunctionTable, PerkImplementationTable, ConstantTable
        self.func_table = func_table or FunctionTable()
        self.perk_impl_table = perk_impl_table or PerkImplementationTable()
        self.const_table = const_table or ConstantTable()
        from sushi_lang.semantics.passes.collect import ExternalTable
        self.external_table = ExternalTable()
        self.external_funcs = {}
        self.external_sigs = {}

        self.types = LLVMTypeSystem(struct_table=self.struct_table, enum_table=self.enum_table,
                                    context=self.llvm_context)
        self.utils = LLVMUtils(self)
        self.runtime = LLVMRuntime(self)
        self.memory = ScopeManager(self)
        self.moves = MoveTracker()  # Unified move tracking (arrays, lists, structs, Own<T>)
        self.dynamic_arrays: Optional[DynamicArrayManager] = None  # Will be initialized when builder is available
        self.expressions = ExpressionEmitter(self)
        self.statements = StatementEmitter(self)
        self.functions = LLVMFunctionManager(self)
        self.optimizer = LLVMOptimizer(self)

        self.string_manager = StringConstantManager(self)
        self.stdlib = StdlibLinker(self)

        self.i32 = self.types.i32
        self.i8 = self.types.i8
        self.i1 = self.types.i1
        self.str_ptr = self.types.str_ptr
        self.void = self.types.void

        self.builder: Optional[ir.IRBuilder] = None
        self.alloca_builder: Optional[ir.IRBuilder] = None
        self.func: Optional[ir.Function] = None
        self.entry_block: Optional[ir.Block] = None
        self.entry_branch: Optional[ir.Instruction] = None
        self.in_extension_method: bool = False  # Track if compiling extension method

        # Loop context tracking for break/continue statements. Each entry is
        # (continue-target block, break-target block, loop-body scope index); the scope
        # index bounds break/continue RAII cleanup to the loop's own scopes.
        self.loop_stack: list[tuple[ir.Block, ir.Block, int]] = []

        self.funcs: Dict[str, ir.Function] = {}

        self.constants: Dict[str, ir.GlobalVariable] = {}

        self._malloc_func: Optional[ir.Function] = None
        self._free_func: Optional[ir.Function] = None
        self._realloc_func: Optional[ir.Function] = None

        # Print-argument string-temp registry (#141): heap data pointers allocated while a
        # print argument is emitted. The statement pushes a frame, prints, then frees it.
        # Only real allocations register, so a literal or a plain load registers nothing.
        self._string_temp_stack: List[List[ir.Value]] = []

        # The same frame for whole string fat VALUES, freed through the string destructor
        # rather than unconditionally -- so a literal element (owned=0) is a no-op and a
        # heap one is freed once. A `let` store happens outside any print frame, so its new
        # owner frees it instead (#145).
        self._string_value_temp_stack: List[List[ir.Value]] = []

        self.main_expects_args: bool = False

        self.is_library_mode: bool = False

        self.library_linker: Optional['LibraryResolver'] = None

        self.library_registry: Optional[LibraryRegistry] = None

        self.monomorphized_extensions: list['ExtendDef'] = []

        # Library-shipped concrete perk impls (C4a): declared only, never
        # defined - the bodies link in from the library bitcode.
        self.library_perk_impls: list['ExtendWithDef'] = []

        self.variable_types: Dict[str, 'Type'] = {}

        self.stdlib_units: set[str] = set()

        # Function return type tracking (Sushi language types, not LLVM types)
        # Maps function name to its return type (pre-Result wrapping)
        # Used for inferring Result<T> types from function call expressions
        self.function_return_types: Dict[str, 'Type'] = {}

        self.current_function_ast: Optional['FuncDef'] = None

        self.ast_constants: Dict[str, ConstDef] = {}

        # Recursive-destructor state, declared here rather than conjured on at first use:
        # `_dtor_inprogress` is the stack of type keys being inlined, so a re-entry means
        # the type is self-referential and gets a call to its out-of-line destructor.
        self._dtor_inprogress: list[str] = []
        self._dtor_funcs: Dict[str, ir.Function] = {}

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

    # Centralized memory management function declarations.
    # Private: allocate through backend.memory.heap.emit_malloc, which null-checks
    # and traps RE2021. Emitting a bare malloc call must not recur.
    def _get_malloc_func(self) -> ir.Function:
        """Get or declare malloc function."""
        if self._malloc_func is None:
            existing = self.module.globals.get("malloc")
            if isinstance(existing, ir.Function):
                self._malloc_func = existing
                return self._malloc_func
            malloc_type = ir.FunctionType(
                ir.PointerType(ir.IntType(INT8_BIT_WIDTH)),  # void*
                [ir.IntType(INT64_BIT_WIDTH)]                # size_t
            )
            self._malloc_func = ir.Function(self.module, malloc_type, name="malloc")
        return self._malloc_func

    def declare_user_externs(self) -> None:
        """Declare user-declared foreign functions (FFI) into this module."""
        from sushi_lang.backend.runtime.externs.user_externs import declare_user_externs
        declare_user_externs(self, self.external_table)

    def get_free_func(self) -> ir.Function:
        """Get or declare free function."""
        if self._free_func is None:
            existing = self.module.globals.get("free")
            if isinstance(existing, ir.Function):
                self._free_func = existing
                return self._free_func
            free_type = ir.FunctionType(
                ir.VoidType(),                   # void
                [ir.PointerType(ir.IntType(INT8_BIT_WIDTH))]  # void*
            )
            self._free_func = ir.Function(self.module, free_type, name="free")
        return self._free_func

    def push_string_temp_scope(self) -> None:
        """Open a print-argument string-temp frame (#141). See `_string_temp_stack`."""
        self._string_temp_stack.append([])
        self._string_value_temp_stack.append([])

    def register_string_temp(self, data_ptr: ir.Value) -> None:
        """Register a freshly heap-allocated string buffer if a print-arg frame is open."""
        if self._string_temp_stack:
            self._string_temp_stack[-1].append(data_ptr)

    def register_string_value_temp(self, fat_value: ir.Value) -> None:
        """Register a whole string fat VALUE for an owned-bit-guarded free after output."""
        if self._string_value_temp_stack:
            self._string_value_temp_stack[-1].append(fat_value)

    def pop_and_free_string_temp_scope(self) -> None:
        """Free every buffer registered in the current print-arg frame and pop it."""
        if not self._string_temp_stack:
            return
        temps = self._string_temp_stack.pop()
        value_temps = self._string_value_temp_stack.pop() if self._string_value_temp_stack else []
        if self.builder is None or self.builder.block is None or self.builder.block.is_terminated:
            return
        from sushi_lang.backend.memory.heap import emit_free
        for data_ptr in temps:
            emit_free(self.builder, self, data_ptr)
        from sushi_lang.backend.destructors import emit_string_destructor_from_value
        for fat_value in value_temps:
            emit_string_destructor_from_value(self, fat_value)

    def emit_string_temp_frame_cleanup_all(self) -> None:
        """Free every open print-arg frame's temporaries on an EARLY-EXIT path (#295)."""
        if not self._string_temp_stack:
            return
        if self.builder is None or self.builder.block is None or self.builder.block.is_terminated:
            return
        from sushi_lang.backend.memory.heap import emit_free
        from sushi_lang.backend.destructors import emit_string_destructor_from_value
        for frame in self._string_temp_stack:
            for data_ptr in frame:
                emit_free(self.builder, self, data_ptr)
        for frame in self._string_value_temp_stack:
            for fat_value in frame:
                emit_string_destructor_from_value(self, fat_value)

    def get_realloc_func(self) -> ir.Function:
        """Get or declare realloc function."""
        if self._realloc_func is None:
            existing = self.module.globals.get("realloc")
            if isinstance(existing, ir.Function):
                self._realloc_func = existing
                return self._realloc_func
            realloc_type = ir.FunctionType(
                ir.PointerType(ir.IntType(INT8_BIT_WIDTH)),  # void*
                [ir.PointerType(ir.IntType(INT8_BIT_WIDTH)), ir.IntType(INT64_BIT_WIDTH)]  # void*, size_t
            )
            self._realloc_func = ir.Function(self.module, realloc_type, name="realloc")
        return self._realloc_func

    def create_string_constant(self, name: str, value: str) -> ir.GlobalVariable:
        """Create a global string constant without requiring a builder context."""
        return self.string_manager.create_string_constant(name, value)

    def _generate_argc_argv_conversion(self, argc: ir.Value, argv: ir.Value) -> ir.Value:
        """Convert C-style argc/argv to Sushi string[] dynamic array."""
        from sushi_lang.backend.runtime.args import generate_argc_argv_conversion
        return generate_argc_argv_conversion(self, argc, argv)

    def build_module_multi_unit(self, units: list[Unit]) -> ir.Module:
        """Generate LLVM IR for multiple compilation units and return the module."""
        for unit in units:
            if unit.ast is not None:
                self.stdlib.extract_stdlib_units(unit.ast)

        self.runtime.declare_externs()
        self.declare_user_externs()
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
        library_linker: 'LibraryResolver' = None,
        library_registry: Optional[LibraryRegistry] = None,
    ) -> Path:
        """Complete multi-unit compilation pipeline from multiple ASTs to native executable."""
        self.main_expects_args = main_expects_args

        self.monomorphized_extensions = monomorphized_extensions or []

        self.library_linker = library_linker

        self.library_registry = library_registry

        mod_ir: ir.Module = self.build_module_multi_unit(units)

        if debug:
            print(";; Multi-unit IR (pre-opt)")
            ir_text = str(mod_ir)
            for i, line in enumerate(ir_text.splitlines(), 1):
                print(f"{i:4} {line}")

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
                bc_paths = self.stdlib._resolve_stdlib_unit(stdlib_path)
                for bc_path in bc_paths:
                    with open(bc_path, 'rb') as f:
                        stdlib_mod = llvm.parse_bitcode(f.read())
                        two_phase.add_stdlib_module(stdlib_mod, stdlib_path)

            llmod = two_phase.link()

        else:
            for unit in units:
                if unit.ast is not None:
                    self.stdlib.link_stdlib_modules(llmod, unit.ast)

        self.optimizer.ensure_target(llmod)

        if verify:
            self.optimizer.verify(llmod, "pre-optimization")

        if opt != "none":
            self.optimizer.optimize(llmod, opt)

        if verify:
            self.optimizer.verify(llmod, "post-optimization")

        self.module = llvm.parse_assembly(str(llmod))

        out_path = out or Path("a.out")
        return self._link_executable(llmod, out_path, cc, debug, keep_object=keep_object)

    def compile_to_bitcode(
        self,
        units: list[Unit],
        debug: bool = False,
        opt: str = "mem2reg",
        verify: bool = True,
        monomorphized_extensions: list['ExtendDef'] = None,
        exported_private_functions: set[str] = frozenset(),
    ) -> bytes:
        """Compile units to LLVM bitcode without linking to executable."""
        self.monomorphized_extensions = monomorphized_extensions or []

        self.is_library_mode = True

        mod_ir: ir.Module = self.build_module_multi_unit(units)

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
            print(";; Library IR (pre-opt)")
            ir_text = str(mod_ir)
            for i, line in enumerate(ir_text.splitlines(), 1):
                print(f"{i:4} {line}")

        llmod = llvm.parse_assembly(str(mod_ir))

        for unit in units:
            if unit.ast is not None:
                self.stdlib.link_stdlib_modules(llmod, unit.ast)

        self.optimizer.ensure_target(llmod)

        if verify:
            self.optimizer.verify(llmod, "pre-optimization")

        if opt != "none":
            self.optimizer.optimize(llmod, opt)

        if verify:
            self.optimizer.verify(llmod, "post-optimization")

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
        """Emit object file and link to native executable."""
        self.optimizer.ensure_llvm()

        if tm is None:
            tm = self.optimizer.ensure_target(llmod)

        obj_bytes = tm.emit_object(llmod)

        obj_path = out.with_suffix(".o")
        obj_path.write_bytes(obj_bytes)

        cmd = [cc, str(obj_path)]
        cmd.extend(["-o", str(out)])

        from sushi_lang.backend.platform_detect import get_current_platform
        platform = get_current_platform()
        if platform.is_linux:
            cmd.append("-lm")

        if debug:
            cmd.insert(1, "-g")
        subprocess.run(cmd, check=True)

        if not keep_object:
            obj_path.unlink()
        return out

    def build_module_single_unit(self, target_unit: Unit, all_units: list[Unit]) -> ir.Module:
        """Generate LLVM IR for a single compilation unit."""
        saved_module = self.module
        saved_funcs = self.funcs.copy()
        saved_constants = self.constants.copy()
        saved_ast_constants = self.ast_constants.copy()

        # Same context as every other module of this compilation -- the type cache persists
        # across units, so this module must be able to declare the identified struct types
        # the cache already handed out (#257).
        self.module = ir.Module(name=f"unit_{target_unit.name}", context=self.llvm_context)
        self.funcs = {}
        self.constants = {}
        self.ast_constants = {}
        self._malloc_func = None
        self._free_func = None
        self._realloc_func = None
        self._string_temp_stack = []
        self.string_manager = StringConstantManager(self)

        self.runtime = LLVMRuntime(self)

        for unit in all_units:
            if unit.ast is not None:
                self.stdlib.extract_stdlib_units(unit.ast)

        self.runtime.declare_externs()
        self.declare_user_externs()

        for unit in all_units:
            if unit.ast is None:
                continue
            for const in unit.ast.constants:
                self.ast_constants[const.name] = const

        for unit in all_units:
            if unit.ast is None:
                continue
            for const in unit.ast.constants:
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
                    synthetic_ext = _perk_method_to_extend_def(perk_impl, method)
                    self.functions.emit_extension_method_decl(synthetic_ext)

        for ext in self.monomorphized_extensions:
            self.functions.emit_extension_method_decl(ext)

        if hasattr(self, 'library_linker') and self.library_linker is not None:
            self._declare_library_functions()
            self._declare_library_perk_impl_methods()

        if target_unit.ast is not None:
            for fn in target_unit.ast.functions:
                if hasattr(fn, 'type_params') and fn.type_params:
                    continue
                self.functions.emit_func_def(fn)

            for ext in target_unit.ast.extensions:
                self.functions.emit_extension_method_def(ext)

            for perk_impl in target_unit.ast.perk_impls:
                for method in perk_impl.methods:
                    synthetic_ext = _perk_method_to_extend_def(perk_impl, method)
                    self.functions.emit_extension_method_def(synthetic_ext)

        for ext in self.monomorphized_extensions:
            self.functions.emit_extension_method_def(ext)

        _set_linkonce_odr_on_inline_runtime(self.module)

        result_module = self.module

        self.module = saved_module
        self.funcs = saved_funcs
        self.constants = saved_constants
        self.ast_constants = saved_ast_constants
        self._malloc_func = None
        self._free_func = None
        self._realloc_func = None
        self._string_temp_stack = []
        self.string_manager = StringConstantManager(self)
        self.runtime = LLVMRuntime(self)

        return result_module

    def compile_single_unit_to_object(self, target_unit: Unit, all_units: list[Unit],
                                      opt: str = "mem2reg", verify: bool = True) -> bytes:
        """Compile a single unit to an object file (bytes)."""
        mod_ir = self.build_module_single_unit(target_unit, all_units)
        llmod = llvm.parse_assembly(str(mod_ir))

        tm = self.optimizer.ensure_target(llmod)

        if verify:
            self.optimizer.verify(llmod, f"pre-optimization ({target_unit.name})")

        if opt != "none":
            self.optimizer.optimize(llmod, opt)

        if verify:
            self.optimizer.verify(llmod, f"post-optimization ({target_unit.name})")

        return tm.emit_object(llmod)

    def compile_stdlib_to_object(self, stdlib_unit: str, opt: str = "mem2reg") -> bytes:
        """Compile stdlib bitcode files to a single object file."""
        bc_paths = self.stdlib._resolve_stdlib_unit(stdlib_unit)
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
        """Compile a library .slib to an object file."""
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
        """Link multiple .o files into a native executable."""
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
        """Check if a stdlib unit has been imported."""
        return self.stdlib.has_stdlib_unit(unit_path)

    def _emit_multi_unit_program(self, units: list[Unit]) -> None:
        """Emit LLVM IR for multiple compilation units."""
        for unit in units:
            if unit.ast is None:
                continue

            for const in unit.ast.constants:
                self.ast_constants[const.name] = const

            for const in unit.ast.constants:
                self._emit_global_constant(const)

        for unit in units:
            if unit.ast is None:
                continue

            for fn in unit.ast.functions:
                if hasattr(fn, 'type_params') and fn.type_params:
                    continue
                self.functions.emit_func_decl(fn)

            for ext in unit.ast.extensions:
                self.functions.emit_extension_method_decl(ext)

            for perk_impl in unit.ast.perk_impls:
                for method in perk_impl.methods:
                    synthetic_ext = _perk_method_to_extend_def(perk_impl, method)
                    self.functions.emit_extension_method_decl(synthetic_ext)

        for ext in self.monomorphized_extensions:
            self.functions.emit_extension_method_decl(ext)

        if hasattr(self, 'library_linker') and self.library_linker is not None:
            self._declare_library_functions()
            self._declare_library_perk_impl_methods()

        for unit in units:
            if unit.ast is None:
                continue

            for fn in unit.ast.functions:
                if hasattr(fn, 'type_params') and fn.type_params:
                    continue
                self.functions.emit_func_def(fn)

            for ext in unit.ast.extensions:
                self.functions.emit_extension_method_def(ext)

            for perk_impl in unit.ast.perk_impls:
                for method in perk_impl.methods:
                    synthetic_ext = _perk_method_to_extend_def(perk_impl, method)
                    self.functions.emit_extension_method_def(synthetic_ext)

        for ext in self.monomorphized_extensions:
            self.functions.emit_extension_method_def(ext)

    def _declare_library_perk_impl_methods(self) -> None:
        """Declare (never define) library-shipped perk-impl methods (C4a)."""
        from sushi_lang.semantics.ast import ExtendDef
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.backend.types.core.resolution import resolve_unknown_type

        struct_table = self.struct_table.by_name if self.struct_table else {}
        enum_table = self.enum_table.by_name if self.enum_table else {}

        def _resolved(ty):
            # Templates are re-parsed source, so user-type references arrive
            # as UnknownType; resolve against the consumer's tables (which
            # include library-registered concrete types).
            if isinstance(ty, UnknownType):
                return resolve_unknown_type(ty, struct_table, enum_table)
            return ty

        for perk_impl in self.library_perk_impls:
            target_type = _resolved(perk_impl.target_type)
            for method in perk_impl.methods:
                for param in method.params:
                    if param.ty is not None:
                        param.ty = _resolved(param.ty)
                synthetic_ext = ExtendDef(
                    target_type=target_type,
                    name=method.name,
                    params=method.params,
                    ret=_resolved(method.ret) if method.ret is not None else None,
                    body=method.body,
                    loc=method.loc,
                    name_span=method.name_span,
                    ret_span=method.ret_span,
                )
                self.functions.emit_extension_method_decl(synthetic_ext)

    def _declare_library_functions(self) -> None:
        """Declare library function prototypes for external library functions."""

        if self.library_registry is not None:
            self._declare_library_functions_from_registry()
            return

        if self.library_linker is None:
            return

        from sushi_lang.semantics.type_resolution import parse_type_string

        for _lib_name, manifest in self.library_linker.loaded_libraries.items():
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

                from sushi_lang.backend.generics.result_builder import intern_result
                std_error = self.enum_table.by_name.get("StdError") if self.enum_table else None
                result_type = intern_result(self, ret_type, std_error if std_error else ret_type)
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

        all_sigs = dict(self.library_registry.get_all_functions())
        for name, (_lib, sig) in self.library_registry.get_all_private_functions().items():
            all_sigs.setdefault(name, sig)

        for func_name, func_sig in all_sigs.items():
            if func_name in self.funcs:
                continue

            param_types = [self.types.ll_type(p.ty) for p in func_sig.params]
            ret_type = func_sig.ret_type

            from sushi_lang.backend.generics.result_builder import intern_result
            std_error = self.enum_table.by_name.get("StdError") if self.enum_table else None
            result_type = intern_result(self, ret_type, std_error if std_error else ret_type)
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
        """Emit a global constant definition."""
        from sushi_lang.semantics.ast import StringLit

        if const.ty is None:
            return  # Skip constants with no type (should be caught in semantic analysis)

        if isinstance(const.value, StringLit):
            string_data = const.value.value.encode('utf-8')
            size = len(string_data)

            array_type = ir.ArrayType(self.i8, size)
            data_global = ir.GlobalVariable(self.module, array_type, name=f".str_data.{const.name}")
            data_global.linkage = 'internal'
            data_global.global_constant = True
            data_global.initializer = ir.Constant(array_type, bytearray(string_data))
            data_global.unnamed_addr = True

            zero = ir.Constant(self.i32, 0)
            data_ptr = data_global.gep([zero, zero])

            # Create fat pointer struct constant {i8*, i32, i8 owned}; a const string is
            # backed by a global -> owned=0 (RAII must never free it) (#145).
            string_struct_type = self.types.string_struct
            size_value = ir.Constant(self.i32, size)
            struct_value = ir.Constant.literal_struct([data_ptr, size_value, ir.Constant(self.i8, 0)])

            struct_global = ir.GlobalVariable(self.module, string_struct_type, name=const.name)
            struct_global.linkage = 'internal'
            struct_global.global_constant = True
            struct_global.initializer = struct_value
            struct_global.unnamed_addr = True

            self.constants[const.name] = struct_global
            return

        llvm_type = self.types.ll_type(const.ty)
        if llvm_type is None:
            return  # Skip unsupported types

        # Evaluate the constant value expression at compile time, materializing the
        # value at the declared type's width (so a context-typed literal such as
        # `const u8 MAX = 200` yields a u8 initializer, not an i32 one).
        const_value = self._evaluate_constant_expression(const.value, const.ty)
        if const_value is None:
            return  # Skip non-constant expressions

        global_const = ir.GlobalVariable(self.module, llvm_type, name=const.name)

        # Internal linkage: cross-unit visibility comes from sharing one LLVM module. True
        # separate compilation would need external linkage for a public constant.
        global_const.linkage = 'internal'

        global_const.global_constant = True
        global_const.initializer = const_value
        global_const.unnamed_addr = True  # Allow merging identical constants

        self.constants[const.name] = global_const

    def _evaluate_constant_expression(self, expr, expected_type=None) -> Optional[ir.Constant]:
        """Evaluate a constant expression at compile time."""
        from sushi_lang.semantics.ast import StringLit
        from sushi_lang.semantics.passes.const_eval import ConstantEvaluator
        from sushi_lang.internals.report import Reporter

        if isinstance(expr, StringLit):
            return None

        # Use constant evaluator to get Python value
        # Create a silent reporter since we're in the backend (errors should have been caught in Pass 2)
        silent_reporter = Reporter()
        evaluator = ConstantEvaluator(silent_reporter, self.const_table, self.ast_constants)
        const_value = evaluator.evaluate(expr, expected_type, None)

        if const_value is None:
            return None

        llvm_const = const_value.to_llvm_constant(self.types)
        return llvm_const


_INLINE_RUNTIME_FUNCTIONS = frozenset({
    "llvm_strlen",
    "llvm_strcmp",
    "utf8_char_count",
})


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


def _set_linkonce_odr_on_inline_runtime(module: ir.Module) -> None:
    """Set linkonce_odr linkage on inline-defined runtime functions."""
    for name in _INLINE_RUNTIME_FUNCTIONS:
        fn = module.globals.get(name)
        if fn is not None and isinstance(fn, ir.Function) and not fn.is_declaration:
            fn.linkage = "linkonce_odr"
