"""LLVM backend orchestrator for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.library_paths import LibraryResolver
    from sushi_lang.backend.slir.bodies import SlirBodies
    from sushi_lang.semantics.ast import ExtendWithDef
    from sushi_lang.semantics.passes.collect import FunctionTable, PerkImplementationTable, ConstantTable
    from sushi_lang.semantics.const_eval import ConstantValue

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import ConstDef, ExtendDef, FuncDef, VarDef
from sushi_lang.semantics.units import Unit
from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.semantics.library_registry import LibraryRegistry
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc
from sushi_lang.backend.llvm_types import LLVMTypeSystem
from sushi_lang.backend.llvm_utils import LLVMUtils
from sushi_lang.backend.runtime import LLVMRuntime
from sushi_lang.backend.runtime.strings import literal_fat_pointer
from sushi_lang.backend.functions import LLVMFunctionManager
from sushi_lang.semantics.unit_symbols import (
    UnitKeyedSymbols, emitted_symbol, global_symbol, mangle_unit_symbol,
)
from sushi_lang.backend.llvm_optimization import LLVMOptimizer
from sushi_lang.backend.string_constants import StringConstantManager
from sushi_lang.backend.stdlib_linker import StdlibLinker
from sushi_lang.backend.slir import emit as slir_emit
from sushi_lang.backend.slir.state import stop
from sushi_lang.semantics.externs_manifest import USER_MAIN_SYMBOL
# Registers the hash() emitter factories that semantics/generics/hashing.py
# resolves when it emits an auto-derived hash(). The derive pass registers the method
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
        # And so must the error channel (ruling R1): `channel_result_of` reads it for
        # the declaration ABI, and a dropped one would compile a channel body against a
        # bare signature.
        err_type=getattr(method, "err_type", None),
        err_span=getattr(method, "err_span", None),
        written_channel=getattr(method, "written_channel", None),
        # And the scope its names resolve in (#1120).
        scope_unit=getattr(method, "scope_unit", None),
    )


class LLVMCodegen:
    """Main LLVM backend orchestrator for the Sushi language compiler."""

    def __init__(self, module_name: str = "lang_module", *, perk_impl_table: 'PerkImplementationTable', struct_table: Optional[StructTable] = None, enum_table: Optional[EnumTable] = None, func_table: Optional['FunctionTable'] = None, const_table: Optional['ConstantTable'] = None) -> None:
        """Initialize the LLVM code generator with all specialized subsystems."""
        # Our OWN context, not llvmlite's process-wide global_context: identified types
        # (#257) register per Context, so on the global one a second `struct Tree` would
        # find the first compilation's type and inherit its layout. Every module shares
        # this one, because the type cache is whole-program and not reset per unit.
        self.llvm_context: ir.Context = ir.Context()
        self.module: ir.Module = ir.Module(name=module_name, context=self.llvm_context)
        self.struct_table = struct_table or StructTable()
        self.enum_table = enum_table or EnumTable()
        # The hash() and clone() the `derive` pass wrote for THIS program, and the
        # process-wide primitive built-ins behind them (#601). It travels on the enum
        # table, so the analyser's tables carry it here with no wiring of their own.
        self.derived_methods = self.enum_table.derived
        from sushi_lang.semantics.passes.collect import FunctionTable, ConstantTable
        self.func_table = func_table or FunctionTable()
        # The unit whose bodies are being emitted, or None outside a multi-unit walk.
        self.emitting_unit: Optional[str] = None
        # What each unit may write, bare and behind a dot, handed over by the semantic
        # analyser: a constant's initializer is folded as the unit that wrote it reads
        # (#561).
        self.unit_namespaces: Dict[str, Any] = {}
        # REQUIRED (#780): an empty default answers "nothing implements Drop", and
        # every handle is then copied and closed twice.
        self.perk_impl_table = perk_impl_table
        self.const_table = const_table or ConstantTable()
        from sushi_lang.semantics.passes.collect import ExternalTable
        self.external_table = ExternalTable()
        self.external_funcs = {}
        self.external_sigs = {}
        self.external_vars = {}

        self.types = LLVMTypeSystem(struct_table=self.struct_table, enum_table=self.enum_table,
                                    context=self.llvm_context)
        self.utils = LLVMUtils(self)
        self.runtime = LLVMRuntime(self)
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
        self.func: Optional[ir.Function] = None

        # Two views: the unit that declared a name, and the flat one.
        self.funcs: UnitKeyedSymbols[ir.Function] = UnitKeyedSymbols()

        self.constants: UnitKeyedSymbols[ir.GlobalVariable] = UnitKeyedSymbols()

        self._malloc_func: Optional[ir.Function] = None
        self._free_func: Optional[ir.Function] = None
        self._realloc_func: Optional[ir.Function] = None

        self.main_expects_args: bool = False

        self.is_library_mode: bool = False

        self.library_linker: Optional['LibraryResolver'] = None

        self.library_registry: Optional[LibraryRegistry] = None

        self.monomorphized_extensions: list['ExtendDef'] = []

        # The SLIR of the program (`codegen_for`): every body that the module walk defines
        # comes from it. None in a codegen that defines no body.
        self.slir_bodies: Optional['SlirBodies'] = None

        # Library-shipped concrete perk impls (C4a): declared only, never
        # defined - the bodies link in from the library bitcode.
        self.library_perk_impls: list['ExtendWithDef'] = []
        self.library_extensions: list[ExtendDef] = []

        self.stdlib_units: set[str] = set()


        # Recursive-lifecycle state, declared here rather than conjured on at first use:
        # each `_inprogress` is the stack of type keys being inlined, so a re-entry means
        # the type is self-referential and gets a call to its out-of-line function.
        self._dtor_inprogress: list[str] = []
        self._dtor_funcs: Dict[str, ir.Function] = {}
        self._clone_inprogress: list[str] = []
        self._clone_funcs: Dict[str, ir.Function] = {}
        # The out-of-line functions of the derived contracts (`Eq`, `Ord`, `Display`),
        # by symbol. Per module, for the lifecycle caches' reason.
        self._contract_funcs: Dict[str, ir.Function] = {}

    def namespaces_of(self, unit_name: Optional[str]):
        """The namespace table of `unit_name`, or None for a unit the analyser never saw."""
        return self.unit_namespaces.get(unit_name)

    def constant_evaluator(self, unit_name: Optional[str] = None):
        """A silent evaluator reading as `unit_name` -- the emitting unit by default.

        Silent because the typecheck pass has already reported anything wrong with the
        expression; the back end only wants the value.
        """
        from sushi_lang.internals.report import Reporter
        from sushi_lang.semantics.const_eval import ConstantEvaluator
        return ConstantEvaluator(Reporter(), self.const_table,
                                 unit_name or self.emitting_unit, self.namespaces_of,
                                 self.struct_table, self.enum_table)

    # Centralized memory management function declarations.
    # Private: allocate through backend.memory.heap.emit_malloc, which null-checks
    # and traps RE2021. Emitting a bare malloc call must not recur.
    def _get_malloc_func(self) -> ir.Function:
        """Get or declare malloc function."""
        if self._malloc_func is None:
            self._malloc_func = declare_libc(self.module, "malloc")
        return self._malloc_func

    def declare_user_externs(self) -> None:
        """Declare user-declared foreign functions (FFI) into this module."""
        from sushi_lang.backend.runtime.externs.user_externs import declare_user_externs
        declare_user_externs(self, self.external_table)

    def get_free_func(self) -> ir.Function:
        """Get or declare free function."""
        if self._free_func is None:
            self._free_func = declare_libc(self.module, "free")
        return self._free_func

    def get_realloc_func(self) -> ir.Function:
        """Get or declare realloc function."""
        if self._realloc_func is None:
            self._realloc_func = declare_libc(self.module, "realloc")
        return self._realloc_func

    def create_string_constant(self, name: str, value: str) -> ir.GlobalVariable:
        """Create a global string constant without requiring a builder context."""
        return self.string_manager.create_string_constant(name, value)

    def _generate_argc_argv_conversion(self, argc: ir.Value, argv: ir.Value) -> ir.Value:
        """Convert C-style argc/argv to Sushi string[] dynamic array."""
        from sushi_lang.backend.runtime.args import generate_argc_argv_conversion
        return generate_argc_argv_conversion(self, argc, argv)

    def build_module_multi_unit(self, units: list[Unit],
                                weak_units: frozenset[str] = frozenset()) -> ir.Module:
        """Generate LLVM IR for multiple compilation units and return the module.

        `weak_units` names the units whose definitions the consumer of this module may
        also hold -- a library build's bundled stdlib modules (#594). Empty for a
        program, which is the only build that owns every symbol it emits.
        """
        for unit in units:
            if unit.ast is not None:
                self.stdlib.extract_stdlib_units(unit.ast)

        self.runtime.declare_externs()
        self.declare_user_externs()
        self._emit_multi_unit_program(units, weak_units=weak_units)
        return self.module

    def build_module_single_unit(self, target_unit: Unit, all_units: list[Unit]) -> ir.Module:
        """Generate LLVM IR for a single compilation unit."""
        saved_module = self.module
        saved_funcs = self.funcs.copy()
        saved_constants = self.constants.copy()
        # The lifecycle caches hold ir.Function handles, which belong to ONE module. A
        # cached handle carried into the next unit's module emits a call with no
        # declaration there ('use of undefined value @__sushi_dtor_...'). First hit by
        # <encoding/msgpack>: a source-stdlib import makes every consumer multi-unit.
        saved_dtor_funcs = self._dtor_funcs
        saved_clone_funcs = self._clone_funcs
        saved_dtor_inprogress = self._dtor_inprogress
        saved_clone_inprogress = self._clone_inprogress
        saved_contract_funcs = self._contract_funcs

        # Same context as every other module of this compilation -- the type cache persists
        # across units, so this module must be able to declare the identified struct types
        # the cache already handed out (#257).
        self.module = ir.Module(name=f"unit_{target_unit.name}", context=self.llvm_context)
        self.funcs = UnitKeyedSymbols()
        self.constants = UnitKeyedSymbols()
        self._malloc_func = None
        self._free_func = None
        self._realloc_func = None
        self._dtor_funcs = {}
        self._clone_funcs = {}
        self._dtor_inprogress = []
        self._clone_inprogress = []
        self._contract_funcs = {}
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
                # A unit variable is defined by its own unit's module only.
                self._emit_global_constant(const, unit.name,
                                           defining=unit.name == target_unit.name,
                                           filename=str(unit.file_path))

        # The target unit declares ALL its functions, public and private, because this
        # module holds their bodies; another unit declares only its PUBLIC functions,
        # because a private one cannot be named from here.
        #
        # THE TARGET UNIT IS DECLARED FIRST. One symbol name holds one declaration in a
        # module, so where a unit shadows a name another unit exports, whichever is
        # declared first takes the symbol and the other reuses it. This module emits the
        # target unit's BODIES, so the target unit's own declaration is the one that has
        # to win -- a private one keeps internal linkage, which is what makes the
        # consumer's call bind to its own definition (`visibility.md` decision 10). It
        # used to be decided by the compilation order, which put the consumer first.
        self._declare_and_define(
            sorted(all_units, key=lambda u: u.name != target_unit.name), [target_unit],
            declares_private=lambda unit: unit.name == target_unit.name)

        _set_linkonce_odr_on_inline_runtime(self.module)

        result_module = self.module

        self.module = saved_module
        self.funcs = saved_funcs
        self.constants = saved_constants
        self._malloc_func = None
        self._free_func = None
        self._realloc_func = None
        self._dtor_funcs = saved_dtor_funcs
        self._clone_funcs = saved_clone_funcs
        self._dtor_inprogress = saved_dtor_inprogress
        self._clone_inprogress = saved_clone_inprogress
        self._contract_funcs = saved_contract_funcs
        self.string_manager = StringConstantManager(self)
        self.runtime = LLVMRuntime(self)

        return result_module

    def _emit_multi_unit_program(self, units: list[Unit],
                                 weak_units: frozenset[str] = frozenset()) -> None:
        """Emit LLVM IR for multiple compilation units.

        A unit named in `weak_units` is not this module's to own: its definitions are
        weakened so a consumer that compiles the same unit keeps one copy of each
        (`backend/library_linkage.py`). The module is asked what appeared while the
        unit was emitted, so no symbol name is re-derived here.
        """
        from sushi_lang.backend.library_linkage import weaken_foreign_globals

        for unit in units:
            if unit.ast is None:
                continue

            before = set(self.module.globals) if unit.name in weak_units else None
            for const in unit.ast.constants:
                self._emit_global_constant(const, unit.name, filename=str(unit.file_path))
            if before is not None:
                weaken_foreign_globals(self.module, before)

        self._declare_and_define(units, units, declares_private=lambda unit: True,
                                 weak_units=weak_units)

    def _declare_and_define(self, declared: list[Unit], defined: list[Unit], *,
                            declares_private: Callable[[Unit], bool],
                            weak_units: frozenset[str] = frozenset()) -> None:
        """Declare the functions of `declared`, then define the bodies of `defined`.

        ONE walk for both build paths: every function, extension and perk-impl method
        is declared before any body is emitted, so a body may call a function that a
        later unit defines. The declarations come from the AST; each body comes from the
        `SlirFunction` of the analysis (`slir_bodies`), through the SLIR emitter. A
        monomorphized extension is declared in every module and defined in the module of
        its HOME unit alone, the unit that declared its template, like a perk-impl copy
        (#1064): its body names that unit's private functions and aliases, and one
        definition is one symbol (#404). A unit named in `weak_units` has each of its
        bodies weakened (`backend/library_linkage.py`).
        """
        from sushi_lang.backend.library_linkage import weaken_all

        for unit in declared:
            if unit.ast is None:
                continue
            private = declares_private(unit)
            for fn in unit.ast.functions:
                if fn.type_params or not (private or fn.is_public):
                    continue
                self.functions.emit_func_decl(fn, unit.name)

            for ext in unit.ast.extensions:
                self.functions.emit_extension_method_decl(ext, unit.name)

            for perk_impl in unit.ast.perk_impls:
                for method in perk_impl.methods:
                    synthetic_ext = _perk_method_to_extend_def(perk_impl, method)
                    self.functions.emit_extension_method_decl(synthetic_ext)

        for ext in self.monomorphized_extensions:
            self.functions.emit_extension_method_decl(ext)

        if self.library_linker is not None:
            self._declare_library_functions()
            self._declare_library_perk_impl_methods()
            for ext in self.library_extensions:
                self.functions.emit_extension_method_decl(ext)

        for unit in defined:
            if unit.ast is None:
                continue

            self.emitting_unit = unit.name
            # Each body as the emitter named it. A snapshot cannot find these: the
            # declaration round above already put every function in the module.
            emitted: list[ir.Function] = []
            bodies: list[FuncDef | ExtendDef] = [
                fn for fn in unit.ast.functions if not fn.type_params]
            bodies.extend(unit.ast.extensions)
            bodies.extend(method for perk_impl in unit.ast.perk_impls
                          for method in perk_impl.methods)
            bodies.extend(ext for ext in self.monomorphized_extensions
                          if ext.home_unit == unit.name)
            for decl in bodies:
                emitted.extend(self._define(decl, str(unit.file_path)))

            if unit.name in weak_units:
                weaken_all(emitted)
        self.emitting_unit = None

    def _define(self, decl: FuncDef | ExtendDef, filename: str) -> list[ir.Function]:
        """Define the body of `decl` from its `SlirFunction`. The body of `main` is
        `user_main`, and the C `main` that calls it comes after it."""
        if self.slir_bodies is None:
            raise_internal_error("CE0000", detail="the backend defines a body with no SLIR "
                                                  "of the program")
        function = self.slir_bodies.function_of(decl)
        defined = [slir_emit.emit_function(self, function, filename=filename)]
        if function.symbol == USER_MAIN_SYMBOL:
            defined.append(self.functions.main_wrapper.emit_main())
        return defined

    def _declare_library_perk_impl_methods(self) -> None:
        """Declare (never define) library-shipped perk-impl methods (C4a).

        The one wrapper `_perk_method_to_extend_def` builds the declaration, so the
        receiver mode and the channel ride along as they do for a local implementation;
        only the types are resolved here, against the consumer's tables.
        """
        from dataclasses import replace
        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.backend.types.core.resolution import require_named_type

        struct_table = self.struct_table.by_name if self.struct_table else {}
        enum_table = self.enum_table.by_name if self.enum_table else {}

        def _resolved(ty):
            # Templates are re-parsed source, so user-type references arrive
            # as UnknownType; resolve against the consumer's tables (which
            # include library-registered concrete types).
            if isinstance(ty, UnknownType):
                return require_named_type(ty, struct_table, enum_table)
            return ty

        for perk_impl in self.library_perk_impls:
            target_type = _resolved(perk_impl.target_type)
            for method in perk_impl.methods:
                for param in method.params:
                    if param.ty is not None:
                        param.ty = _resolved(param.ty)
                synthetic_ext = replace(
                    _perk_method_to_extend_def(perk_impl, method),
                    target_type=target_type,
                    ret=_resolved(method.ret) if method.ret is not None else None,
                    err_type=_resolved(method.err_type) if method.err_type is not None else None,
                )
                self.functions.emit_extension_method_decl(synthetic_ext)

    def _declare_library_functions(self) -> None:
        """Declare every loaded library's functions from the registry's signatures.

        The registry is the ONE reader of a manifest signature; a second builder here
        read `return_type` alone and declared every `| E` as StdError (#541). A PUBLIC
        record declares under its bare name. A PRIVATE record declares under ITS unit
        -- two library units may each ship a `helper` (#494) -- and also under its LINK
        SYMBOL, which is the callee a template body's bindings rewrite produced (D4).
        """
        if self.library_registry is None:
            return

        def _declare_one(func_sig, name: str, unit: str | None):
            from sushi_lang.backend.generics.result_builder import declared_return_ll

            param_types = [self.types.ll_type(p.ty) for p in func_sig.params]
            # The typecheck pass's derivation, so the call and the prototype agree on the
            # channel: `| E` is the Err arm, an explicit Result is not wrapped twice
            # (#541), and a bare signature is its return.
            ll_ret = declared_return_ll(self, func_sig)

            symbol = emitted_symbol(name, None, getattr(func_sig, "link_symbol", None))
            llvm_fn = self.module.globals.get(symbol)
            if llvm_fn is None:
                llvm_fn = ir.Function(self.module, ir.FunctionType(ll_ret, param_types),
                                      name=symbol)
                llvm_fn.linkage = 'external'
                for i, p in enumerate(func_sig.params):
                    if i < len(llvm_fn.args):
                        llvm_fn.args[i].name = p.name

            self.funcs.declare(name, llvm_fn, unit=unit)
            if symbol != name:
                self.funcs.declare(symbol, llvm_fn)

        for func_name, func_sig in self.library_registry.get_all_functions().items():
            if func_name in self.funcs:
                continue
            _declare_one(func_sig, func_name, None)

        for (unit, name), (_lib, sig) in \
                self.library_registry.get_all_private_functions().items():
            if self.funcs.declared(name, unit) is not None:
                continue
            _declare_one(sig, name, unit)

    def _constant_string_value(self, text: str, data_name: str) -> ir.Constant:
        """A `{i8*, i32, i8 owned}` constant, with its backing byte array global.

        `owned = 0` on every one: the bytes live in a read-only global, so RAII must
        never free them (#145).
        """
        string_data = text.encode('utf-8')
        size = len(string_data)

        array_type = ir.ArrayType(self.i8, size)
        data_global = ir.GlobalVariable(self.module, array_type, name=data_name)
        data_global.linkage = 'internal'
        data_global.global_constant = True
        data_global.initializer = ir.Constant(array_type, bytearray(string_data))
        data_global.unnamed_addr = True
        return literal_fat_pointer(data_global)

    def _register_global_constant(self, name: str, llvm_type: ir.Type,
                                  value: Optional[ir.Constant],
                                  unit_name: str | None, *, symbol: str,
                                  storage: bool = False) -> None:
        """Publish an evaluated constant as a read-only global, or a `var` as storage.

        The global's NAME (`symbol`, from `global_symbol`) carries the declaring unit,
        exactly as a function symbol does: two units may each declare a private `SCRATCH`,
        and the monolithic path puts both into one module
        (`docs/design/unit-namespaces.md` section 9).

        A constant is `internal` and `.rodata`: every module that reads it gets its own
        copy, which is harmless for a value. A unit variable (`storage`) is ONE storage
        per program (docs/design/unit-storage.md), so it is writable and externally
        linked: the declaring unit's module defines it, and every other module -- and a
        consumer of a binary library, under the manifest's `symbol` -- declares it with
        no initializer.
        """
        global_var = ir.GlobalVariable(self.module, llvm_type, name=symbol)
        if storage:
            if value is None:
                global_var.linkage = 'external'
            else:
                global_var.initializer = value
        else:
            global_var.linkage = 'internal'
            global_var.global_constant = True
            global_var.initializer = value
            global_var.unnamed_addr = True  # Allow merging identical constants
        self.constants.declare(name, global_var, unit=unit_name)

    def _emit_unit_variable(self, var: VarDef, unit_name: str | None,
                            defining: bool, filename: Optional[str]) -> None:
        """Define a `var`'s storage in its own unit's module; declare it everywhere else."""
        ll_type = self.types.ll_type(var.ty)
        if var.link_symbol is not None or not defining:
            self._register_global_constant(var.name, ll_type, None, unit_name,
                                           symbol=global_symbol(var, unit_name), storage=True)
            return
        from sushi_lang.semantics.const_eval import allocates_nothing
        if allocates_nothing(var.value):
            # The empty descriptor `{0, 0, null}` is the zero value of its type.
            initializer = ir.Constant(ll_type, None)
        else:
            const_value = self._value_of_global(var, unit_name, filename)
            initializer = self._materialize_constant(
                const_value, mangle_unit_symbol(unit_name, f".str_data.{var.name}"),
                var.loc, filename)
        self._register_global_constant(var.name, ll_type, initializer, unit_name,
                                       symbol=global_symbol(var, unit_name), storage=True)

    def _emit_global_constant(self, const: ConstDef,
                              unit_name: str | None = None, *,
                              defining: bool = True,
                              filename: Optional[str] = None) -> None:
        """Emit a global constant definition, or a unit variable's storage.

        The route is the VALUE the evaluator produced, never the shape of the
        expression that produced it. Matching a string constant syntactically meant
        `const string ALIAS = GREETING` registered no global at all and every use said
        CE0055, an internal error, on a valid program (#441; #260 was the same hazard
        for a literal array). The round makes a global or stops the compile; it never
        drops one (docs/design/ir.md 8.2).
        """
        if const.ty is None:
            raise_internal_error("CE0000", span=const.loc, filename=filename,
                                 detail=f"the constant '{const.name}' has no type")

        # A compiled library's constant is a name of the library unit, and its
        # initializer reads that unit's scope (#1120).
        unit_name = const.scope_unit or unit_name

        if isinstance(const, VarDef):
            self._emit_unit_variable(const, unit_name, defining, filename)
            return

        # Materializing at the DECLARED type keeps a context-typed literal at its own
        # width: `const u8 MAX = 200` yields a u8 initializer, not an i32 one.
        const_value = self._value_of_global(const, unit_name, filename)

        # A library constant's copy takes the symbol of the unit that declared it, so
        # it never takes the name of a function in the unit that holds the copy (#1101).
        symbol_unit = const.home_unit or unit_name
        initializer = self._materialize_constant(
            const_value, mangle_unit_symbol(symbol_unit, f".str_data.{const.name}"),
            const.loc, filename)

        self._register_global_constant(
            const.name, self.types.ll_type(const.ty), initializer, unit_name,
            symbol=global_symbol(const, unit_name))

    def _evaluate_constant_expression(self, expr, expected_type=None,
                                      unit_name=None) -> Optional["ConstantValue"]:
        """Evaluate a constant expression at compile time, as a value not an initializer."""
        return self.constant_evaluator(unit_name).evaluate(expr, expected_type, None)

    def _value_of_global(self, const: ConstDef, unit_name: str | None,
                         filename: Optional[str]) -> "ConstantValue":
        """The value of the initializer of a constant or a `var`. A constant with no value (a
        function-typed constant) stops (`emit, constant with no value`)."""
        value = self._evaluate_constant_expression(const.value, const.ty, unit_name)
        if value is None:
            stop("constant with no value", const.loc, filename)
        return value

    def _materialize_constant(self, value, data_name: str, span: Optional[Span],
                              filename: Optional[str]) -> ir.Constant:
        """An evaluated constant as an LLVM initializer. A struct, a tuple, an array and an
        enum value go through the one converter of an aggregate constant.

        A string is finished here rather than in the evaluator: its bytes need a module
        to live in, and the evaluator has none, which is why `const_value_to_llvm` hands
        back None for one.
        """
        from sushi_lang.semantics.typesys import BuiltinType
        from sushi_lang.semantics.const_eval import AggregateConstant

        if isinstance(value, AggregateConstant):
            from sushi_lang.backend.constants.enum_initializers import (
                constant_initializer, constant_of)
            return constant_initializer(constant_of(value, span, filename), self.types,
                                        self._constant_string_value, data_name,
                                        span=span, filename=filename)

        if value.semantic_type == BuiltinType.STRING:
            return self._constant_string_value(value.value, data_name)

        from sushi_lang.backend.constants.llvm_values import const_value_to_llvm
        scalar = const_value_to_llvm(value, self.types)
        if scalar is None:
            raise_internal_error("CE0000", span=span, filename=filename,
                                 detail=f"a constant of {value.semantic_type} has no LLVM constant")
        return scalar




def _set_linkonce_odr_on_inline_runtime(module: ir.Module) -> None:
    """Set linkonce_odr linkage on inline-defined runtime functions.

    The set is `semantics/externs_manifest.py`'s: one list, read here for linkage and
    by CE5013 to refuse an `unsafe external` that names one (#472).
    """
    from sushi_lang.semantics.externs_manifest import INLINE_RUNTIME_SYMBOLS

    for name in INLINE_RUNTIME_SYMBOLS:
        fn = module.globals.get(name)
        if fn is not None and isinstance(fn, ir.Function) and not fn.is_declaration:
            fn.linkage = "linkonce_odr"
