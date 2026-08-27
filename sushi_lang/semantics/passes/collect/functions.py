"""Function and extension method collection."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from sushi_lang.internals.report import Origin, Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import ERR

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.structs import StructTable, GenericStructTable
    from sushi_lang.semantics.passes.collect.enums import EnumTable, GenericEnumTable
from sushi_lang.semantics.ast import (
    Program,
    FuncDef,
    ExtendDef,
    BoundedTypeParam,
    Block,
)
from sushi_lang.semantics.typesys import (
    Type,
    BuiltinType,
    UnknownType,
    ArrayType,
    StructType,
    EnumType,
    DynamicArrayType,
    ReferenceType,
)
from sushi_lang.semantics.generics.types import (
    TypeParameter,
    GenericTypeRef,
    TypeParam,
)

from sushi_lang.semantics.visibility import (
    VisibilityTable,
    library_clash_origin,
    reject_library_clash,
    reject_private_perk_constraints,
)

from .utils import (extract_type_param_names, param_from_node, reject_reference_in,
                    reject_try_in_body)
from sushi_lang.semantics.generics.extension_targets import classify_extension_target
from sushi_lang.semantics.generics.type_display import display_type


def is_explicit_result_type(ty: Optional[Type]) -> bool:
    """Check if a type is an explicit Result<T, E>."""
    if ty is None:
        return False
    from sushi_lang.semantics.generics.results import is_result_enum
    if is_result_enum(ty):
        return True
    if isinstance(ty, GenericTypeRef) and ty.base_name == "Result":
        return True
    return False


def validate_variadic_params(reporter: 'Reporter', params: List['Param']) -> None:
    """Validate native variadic '...T' parameter placement and element type."""
    variadic_indices = [i for i, p in enumerate(params) if getattr(p, "is_variadic", False)]
    if not variadic_indices:
        return

    if len(variadic_indices) > 1:
        second = params[variadic_indices[1]]
        er.emit(reporter, ERR.CE0114, second.name_span,
                message="a function may declare at most one variadic '...T' parameter")
        return

    idx = variadic_indices[0]
    vparam = params[idx]
    if idx != len(params) - 1:
        er.emit(reporter, ERR.CE0114, vparam.name_span,
                message="a variadic '...T' parameter must be the last parameter")
        return

    # Reject a reference element type (T cannot be peek/poke). A borrow cannot be
    # owned or moved into the callee-owned collected array.
    element_ty = vparam.ty.base_type if isinstance(vparam.ty, DynamicArrayType) else vparam.ty
    if isinstance(element_ty, ReferenceType):
        er.emit(reporter, ERR.CE0114, vparam.type_span or vparam.name_span,
                message="a variadic '...T' element type cannot be a reference")
        return

    # A dynamic-array element type (`...T[]`) is allowed: the call site MOVES each
    # move-managed source array into the collected array (bloom semantics per element),
    # so the callee owns and recursively destroys them exactly once with no double-free.


def validate_type_pack_params(
    reporter: 'Reporter',
    type_params_raw: Optional[List],
    params: List['Param'],
    fallback_span: Optional[Span],
) -> None:
    """Validate v2 type-pack parameter placement, count, and consistency."""
    type_params = type_params_raw if isinstance(type_params_raw, list) else []
    pack_type_param_indices = [
        i for i, tp in enumerate(type_params)
        if isinstance(tp, BoundedTypeParam) and getattr(tp, "is_pack", False)
    ]
    pack_type_param_names = {
        type_params[i].name for i in pack_type_param_indices
    }

    if len(pack_type_param_indices) > 1:
        offending = type_params[pack_type_param_indices[1]]
        er.emit(reporter, ERR.CE0117, getattr(offending, "loc", None) or fallback_span,
                message=f"a function may declare at most one type-pack parameter '...{offending.name}'")
    elif len(pack_type_param_indices) == 1:
        idx = pack_type_param_indices[0]
        if idx != len(type_params) - 1:
            offending = type_params[idx]
            er.emit(reporter, ERR.CE0117, getattr(offending, "loc", None) or fallback_span,
                    message=f"a type-pack parameter '...{offending.name}' must be the last type parameter")

    pack_value_indices = [
        i for i, p in enumerate(params) if getattr(p, "is_pack", False)
    ]

    if len(pack_value_indices) > 1:
        offending = params[pack_value_indices[1]]
        er.emit(reporter, ERR.CE0117, offending.name_span or fallback_span,
                message=f"a function may declare at most one type-pack value parameter '...{offending.name}'")
    elif len(pack_value_indices) == 1:
        idx = pack_value_indices[0]
        pack_param = params[idx]

        if idx != len(params) - 1:
            er.emit(reporter, ERR.CE0117, pack_param.name_span or fallback_span,
                    message=f"a type-pack value parameter '...{pack_param.name}' must be the last parameter")

        # No mixing with a v1 native variadic (CE0118).
        if any(getattr(p, "is_variadic", False) for p in params):
            er.emit(reporter, ERR.CE0118, pack_param.name_span or fallback_span,
                    message="a type-pack parameter '...Ts' cannot be combined with a native variadic '...T'")

        pack_elem_name = getattr(pack_param.ty, "name", None)
        if pack_elem_name not in pack_type_param_names:
            er.emit(reporter, ERR.CE0117, pack_param.type_span or pack_param.name_span or fallback_span,
                    message=f"type-pack value parameter '...{pack_param.name}' has no matching type-pack type parameter '...{pack_elem_name}'")


@dataclass
class Param:
    """Function parameter with type information."""
    name: str
    ty: Optional[Type]
    name_span: Optional[Span]
    type_span: Optional[Span]
    index: int
    is_variadic: bool = False         # True for a trailing native variadic ...T param;
    is_pack: bool = False             # True for a v2 type-pack value-param (...Ts args);
    is_nom: bool = False              # `nom T name`: the CALLEE takes ownership. Read it
                                      # through semantics/param_modes.py, never directly.


@dataclass
class FuncSig:
    """A collected function signature."""
    name: str
    loc: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_type: Optional[Type] = None
    ret_span: Optional[Span] = None
    params: List[Param] = field(default_factory=list)
    is_public: bool = False              # True if declared with 'public' keyword
    unit_name: Optional[str] = None      # Which unit this function belongs to (for multi-file)
    filename: Optional[str] = None       # The file it was declared in. This pass walks every
                                         # unit through ONE reporter (unlike the per-unit passes,
                                         # which build their own), so a cross-unit duplicate has
                                         # to name its file explicitly or it renders against
                                         # whichever file the reporter happens to be pointing at.
    err_type: Optional[Type] = None      # Error type for Result<T, E> (None = StdError default)


@dataclass
class GenericFuncDef:
    """Generic function definition with type parameters."""
    name: str                                    # Function name (e.g., "compute_hash")
    type_params: tuple[TypeParam, ...]           # Type parameters (TypeParameter or BoundedTypeParam)
    params: List[Param]                          # Parameters (may contain TypeParameter in types)
    ret: Optional[Type]                          # Return type (may be TypeParameter)
    body: Block                                  # Function body (not monomorphized yet)
    is_public: bool = False
    loc: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_span: Optional[Span] = None
    err_type: Optional[Type] = None              # Error type for Result<T, E> (None = StdError default)
    is_library_template: bool = False            # True if registered from a consumed library's .slib templates
    library_origin: Optional[Origin] = None      # Set with the mark: how to render a diagnostic from this body
    unit_name: Optional[str] = None              # Unit that declared it; a monomorphized instance goes home to it
    filename: Optional[str] = None               # The file it was declared in, for the same reason `FuncSig`
                                                 # carries one: this pass shares ONE reporter across units


@dataclass
class FunctionTable:
    """Table of function signatures collected by the collect pass."""
    by_name: Dict[str, FuncSig] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    _stdlib_functions: Dict[Tuple[str, str], Any] = field(default_factory=dict)

    def register_stdlib_function(self, module_path: str, stdlib_func: Any) -> None:
        """Register a stdlib function."""
        key = (module_path, stdlib_func.name)
        self._stdlib_functions[key] = stdlib_func

    def lookup_stdlib_function(self, module_path: str, function_name: str) -> Optional[Any]:
        """Lookup a stdlib function by module and name."""
        return self._stdlib_functions.get((module_path, function_name))

    def is_stdlib_function(self, module_path: str, function_name: str) -> bool:
        """Check if a function is a stdlib function."""
        return (module_path, function_name) in self._stdlib_functions

    def stdlib_by_name(self) -> Dict[str, Any]:
        """Every imported stdlib function, keyed by its BARE name."""
        return {name: func for (_module, name), func in self._stdlib_functions.items()}


@dataclass
class GenericFunctionTable:
    """Table of generic function definitions collected by the collect pass."""
    by_name: Dict[str, GenericFuncDef] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

    def has_function(self, name: str) -> bool:
        """Check if generic function exists."""
        return name in self.by_name

    def get_function(self, name: str) -> Optional[GenericFuncDef]:
        """Lookup generic function by name."""
        return self.by_name.get(name)


@dataclass
class ExtensionMethod:
    """A collected extension method signature."""
    target_type: Optional[Type]  # Type being extended (i8, i16, i32, i64, u8, u16, u32, u64, f32, f64, bool, string)
    name: str                    # Method name (add, multiply, etc.)
    loc: Optional[Span] = None
    target_type_span: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_type: Optional[Type] = None
    ret_span: Optional[Span] = None
    params: List[Param] = field(default_factory=list)  # Parameters excluding implicit 'self'
    self_mode: Optional[str] = None  # "peek"/"poke" for a `poke self` receiver (#327);
    filename: Optional[str] = None   # The file it was declared in; #473 missed this record
    unit_name: Optional[str] = None  # The unit that declared it


@dataclass
class ExtensionTable:
    """Table of extension methods organized by target type."""
    by_type: Dict[Type, Dict[str, ExtensionMethod]] = field(default_factory=dict)

    def add_method(self, method: ExtensionMethod) -> None:
        """Add a method to the table, creating type entry if needed."""
        if method.target_type is not None:
            if method.target_type not in self.by_type:
                self.by_type[method.target_type] = {}
            self.by_type[method.target_type][method.name] = method

    def get_method(self, target_type: Type, method_name: str) -> Optional[ExtensionMethod]:
        """Get a specific extension method."""
        return self.by_type.get(target_type, {}).get(method_name)


@dataclass
class GenericExtensionMethod:
    """A collected generic extension method signature."""
    base_type_name: str              # Generic type name (e.g., "HashMap", "Box")
    type_params: Tuple[str, ...]     # Type parameter names (e.g., ("K", "V")), () if concrete
    name: str                        # Method name (get, insert, etc.)
    # The instantiation a concrete target constrains ("Box<i32>"), "" for a template (#393).
    target_key: str = ""
    loc: Optional[Span] = None
    target_type_span: Optional[Span] = None
    name_span: Optional[Span] = None
    ret_type: Optional[Type] = None  # May contain TypeParameter instances
    ret_span: Optional[Span] = None
    params: List[Param] = field(default_factory=list)  # May contain TypeParameter in param types
    body: Optional[Any] = None       # Method body (Block AST node)
    self_mode: Optional[str] = None  # "peek"/"poke" for a `poke self` receiver (#327)
    filename: Optional[str] = None   # The file it was declared in; #473 missed this record
    unit_name: Optional[str] = None  # The unit that declared it


@dataclass
class GenericExtensionTable:
    """Generic extension methods by base type name, then by (method, target key).

    The target key is what makes `extend Box@(i32)` and `extend Box@(string)` two methods
    rather than one template declared twice (#393). Keying on the method name alone had
    nowhere to put the arguments, so the second declaration was a duplicate function and the
    message elided the target as `Box@(...)`.
    """
    by_type: Dict[str, Dict[Tuple[str, str], GenericExtensionMethod]] = field(default_factory=dict)

    def add_method(self, method: GenericExtensionMethod) -> None:
        """Add a generic extension method to the table."""
        methods = self.by_type.setdefault(method.base_type_name, {})
        methods[(method.name, method.target_key)] = method

    def declarations(self, base_type_name: str, method_name: str) -> List[GenericExtensionMethod]:
        """Every declaration of one method name on one base type."""
        return [
            method for (name, _key), method in self.by_type.get(base_type_name, {}).items()
            if name == method_name
        ]

    def find_applicable(self, base_type_name: str, method_name: str,
                        instantiation: str) -> Optional[GenericExtensionMethod]:
        """The declaration that applies to one instantiation of the base type.

        A concrete target applies to its own instantiation; a template applies to all. The
        two cannot coexist for one method name -- that overlap is CE0101 -- so at most one
        declaration answers.
        """
        methods = self.by_type.get(base_type_name, {})
        return (methods.get((method_name, instantiation))
                or methods.get((method_name, "")))


class FunctionCollector:
    """Collector for function and extension method definitions."""

    def __init__(
        self,
        reporter: Reporter,
        funcs: FunctionTable,
        generic_funcs: GenericFunctionTable,
        extensions: ExtensionTable,
        generic_extensions: GenericExtensionTable,
        structs: 'StructTable',
        enums: 'EnumTable',
        generic_structs: 'GenericStructTable',
        generic_enums: 'GenericEnumTable'
    ) -> None:
        """Initialize function collector."""
        self.r = reporter
        self.current_unit_file: Optional[str] = None  # File of the unit being collected
        self.current_unit_name: Optional[str] = None
        # Unit names that came from a source library. A consumer definition that
        # collides with one of theirs SHADOWS it silently, which is the rule a binary
        # library already follows (docs/design/libraries.md section 7). Without this,
        # `--lib-kind` would change program semantics rather than just distribution.
        self.library_units: Set[str] = set()
        # Who declared what, across the whole program: the one reader for the question
        # "did a library take this name already?" A struct table carries a file and not
        # a unit, so every collector that refuses a redeclaration asks this table.
        self.visibility: Optional[VisibilityTable] = None
        self.funcs = funcs
        self.generic_funcs = generic_funcs
        self.extensions = extensions
        self.generic_extensions = generic_extensions
        self.structs = structs
        self.enums = enums
        self.generic_structs = generic_structs
        self.generic_enums = generic_enums

    def collect_functions(self, root: Program) -> None:
        """Collect all function definitions from program AST."""
        funcs = getattr(root, "functions", None)
        if isinstance(funcs, list):
            for fn in funcs:
                if isinstance(fn, FuncDef):
                    self._collect_function_def(fn)

    def collect_extensions(self, root: Program) -> None:
        """Collect all extension method definitions from program AST."""
        extensions = getattr(root, "extensions", None)
        if isinstance(extensions, list):
            for ext in extensions:
                if isinstance(ext, ExtendDef):
                    self._collect_extension_def(ext)

        generic_extensions = getattr(root, "generic_extensions", None)
        if isinstance(generic_extensions, list):
            for ext in generic_extensions:
                if isinstance(ext, ExtendDef):
                    self._collect_extension_def(ext)

    def register_stdlib_functions(self, root: Program) -> None:
        """Register stdlib functions from imported modules into the function table."""
        from sushi_lang.semantics.stdlib_registry import get_stdlib_registry

        registry = get_stdlib_registry()

        uses = getattr(root, "uses", None)
        if not isinstance(uses, list):
            return

        for use_stmt in uses:
            if not use_stmt.is_stdlib:
                continue  # Skip user modules

            module_path = use_stmt.path

            module = registry.get_module(module_path)
            if module is None:
                continue

            for _func_name, stdlib_func in module.functions.items():
                self.funcs.register_stdlib_function(module_path, stdlib_func)

            for _const_name, stdlib_const in module.constants.items():
                self.funcs.register_stdlib_function(module_path, stdlib_const)

    def _reject_redeclaration(self, name: str, name_span: Optional[Span],
                              prev) -> bool:
        """A name already taken. True when this declaration must be dropped.

        Three answers, and who owns the previous declaration decides which. Another of
        the program's own units is the plain duplicate. A library's PUBLIC name may be
        replaced -- symbol priority puts the program's own declaration first, and
        `tests/libs/test_warn_lib_override.sushi` is that contract -- so this returns
        False, warns with CW3002, and the caller completes the replacement. A library's PRIVATE name may not be
        replaced (CE3011): one namespace means the library's own bodies would start
        calling the consumer's function, and the consumer cannot even see the name it
        collides with.
        """
        clash = library_clash_origin(
            self.visibility, "function", name,
            current_unit=self.current_unit_name, library_units=self.library_units)
        if clash is not None:
            if clash.is_public:
                self._warn_shadowed_export(name, name_span, clash)
                return False
            reject_library_clash(self.r, clash, name_span, kind="function", name=name,
                                 filename=self.current_unit_file)
            return True
        er.emit_with(self.r, ERR.CE0101, name_span,
                     filename=self.current_unit_file, name=name) \
            .note("first defined here", prev.name_span,
                  getattr(prev, "filename", None)).emit()
        return True

    def _warn_shadowed_export(self, name: str, name_span: Optional[Span],
                              clash) -> None:
        """CW3002: the consumer takes a name the library exports (decision 10).

        Legal, and rarely intended. The reader of the call site cannot see which of the
        two declarations answers it, so the compiler says which one does.
        """
        diagnostic = er.emit_with(
            self.r, ERR.CW3002, name_span,
            filename=self.current_unit_file,
            name=name, kind=clash.kind, owner=clash.unit_name,
        )
        if clash.name_span is not None and clash.filename is not None:
            diagnostic = diagnostic.note("exported here", clash.name_span, clash.filename)
        diagnostic.emit()

    @staticmethod
    def _drop(table, name: str) -> None:
        """Forget a registration, so the caller's own can take the name.

        The branch this replaces dropped the previous entry and returned without
        registering anything, so the consumer lost its own declaration as well and the
        library's came back through the `libraries` pass.
        """
        table.order.remove(name)
        del table.by_name[name]

    def _collect_function_def(self, fn: FuncDef) -> None:
        """Dispatch function collection based on whether it's generic."""
        name = getattr(fn, "name", None)
        if not isinstance(name, str):
            return

        # A receiver parameter has no meaning on a plain top-level function (#327):
        # there is no receiver. The builder lifts the marker onto the FuncDef, so this
        # is the one place the plain-function context can say no.
        if getattr(fn, "self_mode", None) is not None:
            er.emit(self.r, ERR.CE2425, fn.self_mode_span or fn.name_span)
            return

        type_params_raw = getattr(fn, "type_params", None)
        type_params = extract_type_param_names(type_params_raw)

        if type_params and len(type_params) > 0:
            self._collect_generic_function_def(fn, type_params_raw)
        else:
            self._collect_concrete_function_def(fn)

    def _collect_concrete_function_def(self, fn: FuncDef) -> None:
        """Collect concrete (non-generic) function definition."""
        name = getattr(fn, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(fn, "name_span", None) or getattr(
            fn, "loc", None
        )
        ret_ty: Optional[Type] = getattr(fn, "ret", None)
        ret_span: Optional[Span] = getattr(fn, "ret_span", None) or name_span
        is_public: bool = getattr(fn, "is_public", False)

        if ret_ty is None:
            er.emit(self.r, ERR.CE0103, name_span, name=name)

        # Returning a borrow lets a function hand out a view of its own frame (CE2417,
        # #314). Checked on the DECLARED return type, before `resolve_return_type_to_result`
        # wraps it: after the wrap the reference sits inside an interned `Result<T, E>`,
        # which is built structurally and never passes the enum-payload check.
        reject_reference_in(self.r, ret_ty, ret_span, ERR.CE2417)

        err_ty: Optional[Type] = getattr(fn, "err_type", None)
        if is_explicit_result_type(ret_ty) and err_ty is not None:
            # User wrote: fn foo() Result<T, E1> | E2
            # This is an error because it's ambiguous and implies nesting
            err_type_name = getattr(err_ty, "name", str(err_ty))
            er.emit(self.r, ERR.CE2085, ret_span, err_type=err_type_name)

        params: List[Param] = []
        param_names: Set[str] = set()
        for idx, p in enumerate(getattr(fn, "params", []) or []):
            param = param_from_node(p, idx)

            if param.name in param_names:
                er.emit(self.r, ERR.CE0102, param.name_span, name=param.name)
            else:
                param_names.add(param.name)

            params.append(param)

        # Validate native variadic parameter placement / element type (CE0114).
        validate_variadic_params(self.r, params)

        # Validate v2 type-pack parameter placement / count / consistency
        # (CE0117/CE0118). A concrete (non-generic) function has no type-pack
        # type-params, so this fires only if a pack value-param leaked in here
        # without a matching type-pack type-param (malformed -> CE0117).
        validate_type_pack_params(self.r, getattr(fn, "type_params", None), params, name_span)

        if name in self.funcs.by_name:
            if self._reject_redeclaration(name, name_span, self.funcs.by_name[name]):
                return
            self._drop(self.funcs, name)

        if name in self.generic_funcs.by_name:
            if self._reject_redeclaration(name, name_span,
                                          self.generic_funcs.by_name[name]):
                return
            self._drop(self.generic_funcs, name)

        sig = FuncSig(
            name=name,
            filename=self.current_unit_file,
            name_span=name_span,
            ret_type=ret_ty,
            ret_span=ret_span,
            params=params,
            is_public=is_public,
            unit_name=self.current_unit_name,
            err_type=fn.err_type,
        )

        if name == "main" and ret_ty is not None:
            valid_integer_types = {
                BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64
            }
            if ret_ty not in valid_integer_types:
                er.emit(self.r, ERR.CE0106, ret_span, type=display_type(ret_ty))

        self.funcs.order.append(name)
        self.funcs.by_name[name] = sig

    def _collect_generic_function_def(
        self,
        fn: FuncDef,
        type_params_raw: List,
    ) -> None:
        """Collect generic function definition."""
        name = fn.name
        name_span = getattr(fn, "name_span", None) or getattr(fn, "loc", None)

        if name in self.generic_funcs.by_name:
            if self._reject_redeclaration(name, name_span,
                                          self.generic_funcs.by_name[name]):
                return
            self._drop(self.generic_funcs, name)

        if name in self.funcs.by_name:
            if self._reject_redeclaration(name, name_span, self.funcs.by_name[name]):
                return
            self._drop(self.funcs, name)

        type_param_instances = tuple(
            tp if isinstance(tp, BoundedTypeParam)
            else BoundedTypeParam(name=tp, constraints=[], loc=None)
            for tp in type_params_raw
        )

        reject_private_perk_constraints(
            self.r, self.visibility, type_param_instances, name_span,
            current_unit=self.current_unit_name, filename=self.current_unit_file)

        params = []
        param_names = set()
        for idx, p in enumerate(getattr(fn, "params", []) or []):
            param = param_from_node(p, idx)

            if param.name in param_names:
                er.emit(self.r, ERR.CE0102, param.name_span, name=param.name)
            else:
                param_names.add(param.name)

            params.append(param)

        # Generic variadics are out of scope for v1: reject a variadic parameter
        # in a generic function (also covers misplacement) with CE0114.
        if any(getattr(p, "is_variadic", False) for p in params):
            vparam = next(p for p in params if getattr(p, "is_variadic", False))
            er.emit(self.r, ERR.CE0114, vparam.name_span,
                    message="variadic '...T' parameters are not supported in generic functions")

        # Validate v2 type-pack parameter placement / count / consistency
        # (CE0117/CE0118). Well-formed pack functions reach this path (they carry
        # a type-pack type-param). Keys on `is_pack`, disjoint from the CE0114
        # blanket above (which keys on `is_variadic`).
        validate_type_pack_params(self.r, type_params_raw, params, name_span)

        ret_ty = getattr(fn, "ret", None)
        ret_span = getattr(fn, "ret_span", None) or name_span

        if ret_ty is None:
            er.emit(self.r, ERR.CE0103, name_span, name=name)

        reject_reference_in(self.r, ret_ty, ret_span, ERR.CE2417)

        err_ty = getattr(fn, "err_type", None)
        if is_explicit_result_type(ret_ty) and err_ty is not None:
            # User wrote: fn foo<T>() Result<T, E1> | E2
            # This is an error because it's ambiguous and implies nesting
            err_type_name = getattr(err_ty, "name", str(err_ty))
            er.emit(self.r, ERR.CE2085, ret_span, err_type=err_type_name)

        body = getattr(fn, "body", None)
        if body is None:
            return

        generic_func = GenericFuncDef(
            name=name,
            type_params=type_param_instances,
            params=params,
            ret=ret_ty,
            body=body,
            is_public=getattr(fn, "is_public", False),
            loc=getattr(fn, "loc", None),
            name_span=name_span,
            ret_span=ret_span,
            err_type=fn.err_type,
            unit_name=self.current_unit_name,
            filename=self.current_unit_file,
        )

        self.generic_funcs.order.append(name)
        self.generic_funcs.by_name[name] = generic_func

    def _collect_extension_def(self, ext: ExtendDef) -> None:
        """Collect extension method definition (both regular and generic)."""
        name = getattr(ext, "name", None)
        if not isinstance(name, str):
            return

        target_type: Optional[Type] = getattr(ext, "target_type", None)
        name_span: Optional[Span] = getattr(ext, "name_span", None) or getattr(ext, "loc", None)
        target_type_span: Optional[Span] = getattr(ext, "target_type_span", None)
        ret_ty: Optional[Type] = getattr(ext, "ret", None)
        ret_span: Optional[Span] = getattr(ext, "ret_span", None) or name_span
        body = getattr(ext, "body", None)

        if ret_ty is None:
            er.emit(self.r, ERR.CE0103, name_span, name=f"extension method '{name}'")

        reject_reference_in(self.r, ret_ty, ret_span, ERR.CE2417)

        # A reference TARGET falls through both isinstance filters below, so the method is
        # collected and then unreachable: every call reports "no such method" and the body
        # is dead code (CE2420, #319).
        reject_reference_in(self.r, target_type, target_type_span or name_span, ERR.CE2420)

        params: List[Param] = []
        param_names: Set[str] = set()
        for idx, p in enumerate(getattr(ext, "params", []) or []):
            param = param_from_node(p, idx)

            if param.name == "self":
                er.emit(self.r, ERR.CE0102, param.name_span, name=param.name)
            elif param.name in param_names:
                er.emit(self.r, ERR.CE0102, param.name_span, name=param.name)
            else:
                param_names.add(param.name)

            params.append(param)

        # Variadic parameters are not allowed in extension methods (CE0115).
        # The pack half is unreachable today, but the guard must match its
        # documented contract and stay correct by construction (#246).
        for p in params:
            if getattr(p, "is_variadic", False) or getattr(p, "is_pack", False):
                er.emit(self.r, ERR.CE0115, p.name_span, context="an extension method")
                break

        # A `??` has no error channel in an extension body (CE0131, #398).
        reject_try_in_body(self.r, body, "an extension method")

        if target_type is not None and isinstance(target_type, GenericTypeRef):
            base_type_name = target_type.base_name

            # A concrete argument is a CONSTRAINT, a bare name is a type PARAMETER, and a mix
            # of the two is partial specialization, which Sushi does not have (#393). The collect pass
            # is the pass that can tell them apart, because the struct and enum tables say
            # which names are declared types -- so the answer is decided here and carried.
            shape = classify_extension_target(target_type, self._is_declared_type)
            ext.target_shape = shape
            if shape.is_mixed:
                er.emit_with(self.r, ERR.CE2098, target_type_span or name_span,
                             target=display_type(target_type)) \
                    .help("name every type parameter, or make every argument concrete -- "
                          "there is no partial specialization").emit()
                return

            type_param_names = set(shape.param_names)

            def convert_unknown_to_typeparam(ty: Optional[Type]) -> Optional[Type]:
                """Convert UnknownType to TypeParameter if it matches a type parameter name."""
                if ty is None:
                    return None
                if isinstance(ty, UnknownType) and ty.name in type_param_names:
                    return TypeParameter(name=ty.name)
                return ty

            concrete_ret_ty = convert_unknown_to_typeparam(ret_ty)
            concrete_params = []
            for param in params:
                concrete_param_ty = convert_unknown_to_typeparam(param.ty)
                concrete_params.append(Param(
                    name=param.name,
                    ty=concrete_param_ty,
                    name_span=param.name_span,
                    type_span=param.type_span,
                    index=param.index,
                    is_variadic=getattr(param, "is_variadic", False),
                    is_nom=getattr(param, "is_nom", False),
                ))

            generic_method = GenericExtensionMethod(
                base_type_name=base_type_name,
                type_params=shape.param_names,
                target_key=shape.target_key,
                name=name,
                loc=getattr(ext, "loc", None),
                target_type_span=target_type_span,
                name_span=name_span,
                ret_type=concrete_ret_ty,
                ret_span=ret_span,
                params=concrete_params,
                body=body,
                self_mode=getattr(ext, "self_mode", None),
                filename=self.current_unit_file,
                unit_name=self.current_unit_name,
            )

            if self._reject_overlapping_target(generic_method, target_type, name_span):
                return

            self.generic_extensions.add_method(generic_method)
        else:
            resolved_type = target_type
            if target_type is not None and isinstance(target_type, UnknownType):
                type_name = target_type.name
                if type_name in self.structs.by_name:
                    resolved_type = self.structs.by_name[type_name]
                elif type_name in self.enums.by_name:
                    resolved_type = self.enums.by_name[type_name]

            method = ExtensionMethod(
                target_type=resolved_type,
                name=name,
                loc=getattr(ext, "loc", None),
                target_type_span=target_type_span,
                name_span=name_span,
                ret_type=ret_ty,
                ret_span=ret_span,
                params=params,
                self_mode=getattr(ext, "self_mode", None),
                filename=self.current_unit_file,
                unit_name=self.current_unit_name,
            )

            if resolved_type is not None and isinstance(resolved_type, (BuiltinType, ArrayType, StructType, EnumType)):
                existing = self.extensions.get_method(resolved_type, name)
                if existing is not None:
                    er.emit_with(self.r, ERR.CE0101, name_span,
                           filename=self.current_unit_file,
                           name=f"extension method '{name}' for '{display_type(resolved_type)}'") \
                        .note("first defined here", existing.name_span,
                              existing.filename).emit()
                    return

            if resolved_type is not None and isinstance(resolved_type, (BuiltinType, ArrayType, StructType, EnumType)):
                self.extensions.add_method(method)

    def _is_declared_type(self, name: str) -> bool:
        """Whether a bare name in a type position names a declared type.

        The tables accumulate in compilation order, and library symbols register after the collect pass,
        so a name this cannot see yet is read as a type PARAMETER. That is the safe direction:
        a name IN the tables is certainly a type, so the only reachable mistake is the old
        behaviour (the declaration applies to every instantiation), never a false rejection.
        """
        return (name in self.structs.by_name
                or name in self.enums.by_name
                or name in self.generic_structs.by_name
                or name in self.generic_enums.by_name)

    def _reject_overlapping_target(self, method: GenericExtensionMethod,
                                   target_type: GenericTypeRef,
                                   name_span: Optional[Span]) -> bool:
        """Reject a second declaration of one method name that covers the same type (#393).

        Two fully-concrete targets never overlap, so they are two methods. A template and a
        concrete target for one name both claim that instantiation, and Sushi resolves the
        overlap by rejecting it rather than by letting the most specific win: under
        specialization, whether the template's body is dead code would depend on which
        instantiations exist ELSEWHERE in the program, and `docs/design/method-resolution.md`
        rules that an unreachable declaration is a diagnostic.
        """
        for existing in self.generic_extensions.declarations(method.base_type_name, method.name):
            same_target = existing.target_key == method.target_key
            if not same_target and existing.target_key and method.target_key:
                continue  # two distinct concrete targets: two types, two methods

            diag = er.emit_with(
                self.r, ERR.CE0101, name_span,
                name=f"extension method '{method.name}' for '{display_type(target_type)}'")
            if same_target:
                diag.note("first defined here", existing.name_span)
            else:
                diag.note("this declaration already covers that target",
                          existing.name_span)
                diag.help("Sushi has no specialization: make both targets fully concrete, "
                          "or implement a perk on the concrete target -- a perk "
                          "implementation outranks an extension method by design.")
            diag.emit()
            return True

        return False
