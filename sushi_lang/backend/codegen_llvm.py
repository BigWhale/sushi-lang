"""LLVM backend orchestrator for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Dict, NoReturn, Optional

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.library_paths import LibraryResolver
    from sushi_lang.backend.slir.bodies import SlirBodies
    from sushi_lang.semantics.decl_table import DeclTable, FunctionDecl, GlobalDecl, MethodDecl
    from sushi_lang.semantics.passes.collect import PerkImplementationTable
    from sushi_lang.semantics.const_eval import ConstantValue
    from sushi_lang.semantics.typesys import Type

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.internals.report import Span
from sushi_lang.semantics.units import Unit
from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.semantics.library_registry import LibraryRegistry
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc
from sushi_lang.backend.llvm_types import LLVMTypeSystem
from sushi_lang.backend.llvm_utils import LLVMUtils
from sushi_lang.backend.runtime import LLVMRuntime
from sushi_lang.backend.runtime.strings import literal_fat_pointer
from sushi_lang.backend.functions import LLVMFunctionManager
from sushi_lang.semantics.unit_symbols import UnitKeyedSymbols, emitted_symbol
from sushi_lang.backend.llvm_optimization import LLVMOptimizer
from sushi_lang.backend.string_constants import StringConstantManager, text_global
from sushi_lang.backend.stdlib_linker import StdlibLinker
from sushi_lang.backend.slir import emit as slir_emit
from sushi_lang.backend.slir.closures import declare_lifted
from sushi_lang.semantics.externs_manifest import USER_MAIN_SYMBOL
# Registers the hash() emitter factories that semantics/generics/hashing.py
# resolves when it emits an auto-derived hash(). The derive pass registers the method
# itself without knowing anything about LLVM.
import sushi_lang.backend.types  # noqa: F401


class LLVMCodegen:
    """Main LLVM backend orchestrator for the Sushi language compiler."""

    def __init__(self, module_name: str = "lang_module", *, perk_impl_table: 'PerkImplementationTable', struct_table: Optional[StructTable] = None, enum_table: Optional[EnumTable] = None) -> None:
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
        # The unit whose bodies are being emitted, or None outside a multi-unit walk.
        self.emitting_unit: Optional[str] = None
        # REQUIRED (#780): an empty default answers "nothing implements Drop", and
        # every handle is then copied and closed twice.
        self.perk_impl_table = perk_impl_table
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

        # The declaration table of the program (`codegen_for`, docs/design/ir.md 6.3): the
        # declaration round reads each declaration from it. None in a codegen that
        # declares nothing.
        self.decls: Optional['DeclTable'] = None

        # The SLIR of the program (`codegen_for`): every body that the module walk defines
        # comes from it. None in a codegen that defines no body.
        self.slir_bodies: Optional['SlirBodies'] = None

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

        self.runtime.declare_externs()
        self.declare_user_externs()

        def emit_constants() -> None:
            for unit in all_units:
                decls = self.declaration_table().unit(unit.name)
                if decls is None:
                    continue
                for const in decls.globals:
                    # A unit variable is defined by its own unit's module only.
                    self._emit_global_constant(const, defining=unit.name == target_unit.name,
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
            declares_private=lambda unit: unit.name == target_unit.name,
            emit_constants=emit_constants)

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

        def emit_constants() -> None:
            for unit in units:
                decls = self.declaration_table().unit(unit.name)
                if decls is None:
                    continue

                before = set(self.module.globals) if unit.name in weak_units else None
                for const in decls.globals:
                    self._emit_global_constant(const, filename=str(unit.file_path))
                if before is not None:
                    weaken_foreign_globals(self.module, before)

        self._declare_and_define(units, units, declares_private=lambda unit: True,
                                 emit_constants=emit_constants, weak_units=weak_units)

    def _declare_and_define(self, declared: list[Unit], defined: list[Unit], *,
                            declares_private: Callable[[Unit], bool],
                            emit_constants: Callable[[], None],
                            weak_units: frozenset[str] = frozenset()) -> None:
        """Declare the functions of `declared`, emit the constants, then define the bodies of
        `defined`.

        ONE walk for both build paths: every function, extension and perk-impl method
        is declared before a constant, which can name a function, and before any body is
        emitted, so a body may call a function that a later unit defines. The declarations
        come from the declaration table (`decls`); each body comes from the `SlirFunction`
        of the analysis (`slir_bodies`), through the SLIR emitter. A monomorphized
        extension is declared in every module and defined in the module of its HOME unit
        alone, the unit that declared its template, like a perk-impl copy (#1064): its body
        names that unit's private functions and aliases, and one definition is one symbol
        (#404). A unit named in `weak_units` has each of its bodies weakened
        (`backend/library_linkage.py`).
        """
        from sushi_lang.backend.library_linkage import weaken_all

        table = self.declaration_table()
        for unit in declared:
            decls = table.unit(unit.name)
            if decls is None:
                continue
            private = declares_private(unit)
            for fn in decls.functions:
                if not (private or fn.is_public):
                    continue
                self.functions.emit_func_decl(fn)

            for method in decls.methods:
                self.functions.emit_extension_method_decl(method)

        for method in table.copies:
            self.functions.emit_extension_method_decl(method)

        if self.library_linker is not None:
            self._declare_library_functions()
            # Library-shipped perk impls and extensions (C4a): declared only, never
            # defined - the bodies link in from the library bitcode.
            for method in (*table.shipped_perks, *table.shipped_methods):
                self.functions.emit_extension_method_decl(method)

        emit_constants()

        for unit in defined:
            decls = table.unit(unit.name)
            if decls is None:
                continue

            self.emitting_unit = unit.name
            # Each body as the emitter named it. A snapshot cannot find these: the
            # declaration round above already put every function in the module.
            emitted: list[ir.Function] = []
            bodies: list[FunctionDecl | MethodDecl] = [*decls.functions, *decls.methods]
            bodies.extend(method for method in table.copies if method.home_unit == unit.name)
            for decl in bodies:
                emitted.extend(self._define(decl, str(unit.file_path)))

            if unit.name in weak_units:
                weaken_all(emitted)
        self.emitting_unit = None

    def declaration_table(self) -> 'DeclTable':
        """The declaration table of the program. A codegen with no table declares nothing:
        that is an internal error."""
        if self.decls is None:
            raise_internal_error("CE0000", detail="the backend declares a module with no "
                                                  "declaration table of the program")
        return self.decls

    def _define(self, decl: 'FunctionDecl | MethodDecl', filename: str) -> list[ir.Function]:
        """Define the body of `decl` from its `SlirFunction`. The body of `main` is
        `user_main`, and the C `main` that calls it comes after it."""
        if self.slir_bodies is None:
            raise_internal_error("CE0000", detail="the backend defines a body with no SLIR "
                                                  "of the program")
        function = self.slir_bodies.function_of(decl)
        for lifted in function.lifted:
            declare_lifted(self, lifted)
        for lifted in function.lifted:
            slir_emit.emit_function(self, lifted, filename=filename)
        defined = [slir_emit.emit_function(self, function, filename=filename)]
        if function.symbol == USER_MAIN_SYMBOL:
            defined.append(self.functions.main_wrapper.emit_main())
        return defined

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
            from sushi_lang.backend.functions.signature import call_signature
            from sushi_lang.backend.generics.result_builder import declared_return_ll

            param_types = [self.types.ll_type(p.ty) for p in func_sig.params]
            # The typecheck pass's derivation, so the call and the prototype agree on the
            # channel: `| E` is the Err arm, an explicit Result is not wrapped twice
            # (#541), and a bare signature is its return.
            ll_ret = declared_return_ll(self, func_sig)

            symbol = emitted_symbol(name, None, getattr(func_sig, "link_symbol", None))
            llvm_fn = self.module.globals.get(symbol)
            if llvm_fn is None:
                llvm_fn = ir.Function(self.module,
                                      call_signature(ll_ret, param_types).function_type,
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
        return literal_fat_pointer(text_global(self.module, data_name, text, nul=False))

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

    def _emit_unit_variable(self, var: 'GlobalDecl', ty: Type, defining: bool,
                            filename: Optional[str]) -> None:
        """Define a `var`'s storage in its own unit's module; declare it everywhere else."""
        ll_type = self.types.ll_type(ty)
        if var.link_symbol is not None or not defining:
            self._register_global_constant(var.name, ll_type, None, var.unit,
                                           symbol=var.symbol, storage=True)
            return
        if var.empty:
            # The empty descriptor `{0, 0, null}` is the zero value of its type.
            initializer = ir.Constant(ll_type, None)
        else:
            value = self._value_of_global(var, filename)
            initializer = self._materialize_constant(value, var.data_name, var.loc, filename)
        self._register_global_constant(var.name, ll_type, initializer, var.unit,
                                       symbol=var.symbol, storage=True)

    def _emit_global_constant(self, const: 'GlobalDecl', *, defining: bool = True,
                              filename: Optional[str] = None) -> None:
        """Emit a global constant definition, or a unit variable's storage.

        The route is the VALUE the evaluator produced, never the shape of the
        expression that produced it. Matching a string constant syntactically meant
        `const string ALIAS = GREETING` registered no global at all and every use said
        CE0055, an internal error, on a valid program (#441; #260 was the same hazard
        for a literal array). The round makes a global or raises an internal error; it never
        drops one (docs/design/ir.md 8.2). The record holds the folded value (ir.md 6.3).
        """
        ty = self._declared_type(const, filename)

        if const.is_var:
            self._emit_unit_variable(const, ty, defining, filename)
            return

        value = self._value_of_global(const, filename)
        initializer = self._materialize_constant(value, const.data_name, const.loc, filename)

        self._register_global_constant(
            const.name, self.types.ll_type(ty), initializer, const.unit, symbol=const.symbol)

    @staticmethod
    def _declared_type(const: 'GlobalDecl', filename: Optional[str]) -> Type:
        """The declared type of a constant or a `var`. A global with no type is an internal
        error."""
        if const.ty is None:
            raise_internal_error("CE0000", span=const.loc, filename=filename,
                                 detail=f"the constant '{const.name}' has no type")
        return const.ty

    @staticmethod
    def _value_of_global(const: 'GlobalDecl', filename: Optional[str]) -> "ConstantValue":
        """The folded value of the initializer of a constant or a `var`. A constant with no
        value is an internal error."""
        if const.value is None:
            raise_internal_error("CE0000", span=const.loc, filename=filename,
                                 detail="a constant with no value reached the backend")
        return const.value

    def _materialize_constant(self, value, data_name: str, span: Optional[Span],
                              filename: Optional[str]) -> ir.Constant:
        """An evaluated constant as an LLVM initializer. A struct, a tuple, an array and an
        enum value go through the one converter of an aggregate constant.

        A string is finished here rather than in the evaluator: its bytes need a module
        to live in, and the evaluator has none. Any other scalar goes through the one
        converter of a scalar constant (`scalar_initializer`), which writes an integer at the
        width of its type.
        """
        from sushi_lang.backend.runtime.closures import closure_constant
        from sushi_lang.semantics.typesys import BuiltinType, FunctionType
        from sushi_lang.semantics.type_predicates import BUILTIN_NUMERIC_TYPES
        from sushi_lang.semantics.const_eval import AggregateConstant

        if isinstance(value, AggregateConstant):
            from sushi_lang.backend.constants.enum_initializers import (
                constant_initializer, constant_of)
            return constant_initializer(constant_of(value, span, filename), self.types,
                                        self._constant_string_value, data_name,
                                        span=span, filename=filename,
                                        finish_function=lambda symbol: closure_constant(self, symbol))

        if isinstance(value.semantic_type, FunctionType):
            return closure_constant(self, str(value.value))

        if value.semantic_type == BuiltinType.STRING:
            return self._constant_string_value(value.value, data_name)

        from sushi_lang.backend.constants.enum_initializers import scalar_initializer
        from sushi_lang.semantics.slir.constants import of_scalar

        def fault(detail: str) -> NoReturn:
            raise_internal_error("CE0000", span=span, filename=filename,
                                 detail=f"the constant {value}: {detail}")

        if value.semantic_type not in (BuiltinType.BOOL, *BUILTIN_NUMERIC_TYPES):
            fault(f"a constant of {value.semantic_type} has no LLVM constant")
        return scalar_initializer(of_scalar(value), self.types, fault)




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
