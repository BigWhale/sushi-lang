"""Function and extension method collection."""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

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
    ArrayType,
    DynamicArrayType,
    FunctionType,
    ReferenceType,
)
from sushi_lang.semantics.generics.types import (
    TypeParameter,
    GenericTypeRef,
    TypeParam,
)

from sushi_lang.semantics.unit_symbols import UnitOwnedSymbols
from sushi_lang.semantics.visibility import (
    VisibilityTable,
    library_clash_origin,
    record_declaration,
)

from .utils import (extract_type_param_names, param_from_node, reject_reference_in,
                    reject_self_in_body, reject_try_in_body)
from sushi_lang.semantics.generics.extension_targets import (
    CONCRETE_EXTENSION_TARGETS, classify_extension_target, reject_unwritable_target)
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.generics.type_display import display_type


def deep_type_params(ty: Optional[Type], names) -> Optional[Type]:
    """Convert every UnknownType naming one of `names` into a TypeParameter, THROUGH
    the whole signature type -- nested GenericTypeRef arguments, function types and
    array elements included, where the old top-level convert stopped."""
    if ty is None or not names:
        return ty
    from sushi_lang.semantics.generics.types import substitute_type_params
    return substitute_type_params(ty, {n: TypeParameter(name=n) for n in names})


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
    variadic_indices = [i for i, p in enumerate(params) if p.is_variadic]
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
        if isinstance(tp, BoundedTypeParam) and tp.is_pack
    ]
    pack_type_param_names = {
        type_params[i].name for i in pack_type_param_indices
    }

    if len(pack_type_param_indices) > 1:
        offending = type_params[pack_type_param_indices[1]]
        er.emit(reporter, ERR.CE0117, offending.loc or fallback_span,
                message=f"a function may declare at most one type-pack parameter '...{offending.name}'")
    elif len(pack_type_param_indices) == 1:
        idx = pack_type_param_indices[0]
        if idx != len(type_params) - 1:
            offending = type_params[idx]
            er.emit(reporter, ERR.CE0117, offending.loc or fallback_span,
                    message=f"a type-pack parameter '...{offending.name}' must be the last type parameter")

    pack_value_indices = [
        i for i, p in enumerate(params) if p.is_pack
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
        if any(p.is_variadic for p in params):
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


def convert_param_types(
        params: List['Param'],
        convert: Callable[[Optional[Type]], Optional[Type]]) -> List['Param']:
    """Rebuild each parameter with its type converted and every other field kept.

    `dataclasses.replace` and not a field list: a rebuild that spells out what it
    copies drops what it forgets, and that is how `is_pack` was lost (#694).
    """
    return [replace(p, ty=convert(p.ty)) for p in params]


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
    link_symbol: Optional[str] = None    # The symbol a BINARY .slib's bitcode gave this
                                         # function. Read from the manifest and never
                                         # derived: a producer's `<unit>$<name>` is not a
                                         # name the consumer can compute (section 9).


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
    err_span: Optional[Span] = None              # Where the channel is written (#662); an
                                                 # instance is a copy of this node and the
                                                 # signature walk reads the span off it
    is_library_template: bool = False            # True if registered from a consumed library's .slib templates
    library_origin: Optional[Origin] = None      # Set with the mark: how to render a diagnostic from this body
    unit_name: Optional[str] = None              # Unit that declared it; a monomorphized instance goes home to it
    filename: Optional[str] = None               # The file it was declared in, for the same reason `FuncSig`
                                                 # carries one: this pass shares ONE reporter across units


class Redeclaration(Enum):
    """What a name already taken means for the declaration that takes it again."""

    REFUSED = "refused"   # dropped; a diagnostic named why
    REPLACE = "replace"   # this one takes the flat name from the other (CW3002)
    COEXIST = "coexist"   # both stand: two units, two symbols


@dataclass(eq=False)
class FunctionTable(UnitOwnedSymbols[FuncSig]):
    """Table of function signatures collected by the collect pass.

    The two views are `UnitOwnedSymbols`'. A name a consumer shadows leaves the flat
    view (`_drop`), so without the second index a library's own body has no way back
    to its own signature -- issue #487 (`docs/design/unit-namespaces.md` section 13.1).
    """

    _stdlib_functions: Dict[Tuple[str, str], Any] = field(default_factory=dict)

    def register_stdlib_function(self, module_path: str, stdlib_func: Any) -> None:
        """Register a stdlib function."""
        key = (module_path, stdlib_func.name)
        self._stdlib_functions[key] = stdlib_func

    def lookup_stdlib_function(self, module_path: str, function_name: str) -> Optional[Any]:
        """Lookup a stdlib function by module and name."""
        return self._stdlib_functions.get((module_path, function_name))

    def lookup_stdlib_by_name(self, function_name: str,
                              scope: object = None) -> Optional[Tuple[str, Any]]:
        """The registry stdlib FUNCTION a bare name reaches, with its module path.

        The table holds what every unit's `use` registered, so the asking unit's scope
        is what narrows it to the modules THAT unit imported: a registry module is a
        flat import like any other and reaches no further (section 6). Without a scope
        the whole table answers, which is what a reader with no unit gets.
        """
        for (module_path, name), func in self._stdlib_functions.items():
            if name != function_name or getattr(func, "is_constant", False):
                continue
            if scope is not None and not scope.holds_module(module_path):
                continue
            return module_path, func
        return None

    def stdlib_by_name(self) -> Dict[str, Any]:
        """Every imported stdlib function, keyed by its BARE name."""
        return {name: func for (_module, name), func in self._stdlib_functions.items()}


@dataclass(eq=False)
class GenericFunctionTable(UnitOwnedSymbols[GenericFuncDef]):
    """Table of generic function definitions collected by the collect pass.

    The same two views `FunctionTable` carries, for the same reason: two units may
    each declare `twin@(T)` (#495).
    """


def _target_base_name(target_type: Optional[Type]) -> Optional[str]:
    """The declared NAME an extension target names, before instantiation.

    `Shape` for a bare name and for a resolved enum or struct, `Shape` for
    `Shape@(T)`, None for a builtin or an array target -- neither has variants.
    """
    if isinstance(target_type, GenericTypeRef):
        return target_type.base_name
    name = getattr(target_type, "name", None)
    return name if isinstance(name, str) else None


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
    err_type: Optional[Type] = None  # `| E`: the method returns Result@(ret, E) (ruling 1)
    err_span: Optional[Span] = None
    # A `static` method has no receiver and is called on the type name (#542). The flag
    # is what keeps the two callable shapes apart: an instance call may not reach a
    # static, and a type-name call may not reach an instance method.
    is_static: bool = False


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
    err_type: Optional[Type] = None  # `| E`: instances return Result@(ret, E) (ruling 1)
    err_span: Optional[Span] = None
    # Method-level type parameters (`name@(U)`): SEPARATE from the receiver-derived
    # `type_params`, whose CE0096 strict zip stays untouched. Solved at the call site.
    method_type_params: Tuple[str, ...] = ()
    is_static: bool = False          # no receiver, called on the type name (#542)
    # The declaration as written. A copy is this node with its types substituted, so a
    # field the record does not spell is not lost (#803).
    decl: Optional[ExtendDef] = None


@dataclass
class GenericExtensionTable:
    """Generic extension methods by base type name, then by (method, target key).

    The target key is what makes `extend Box@(i32)` and `extend Box@(string)` two methods
    rather than one template declared twice (#393). Keying on the method name alone had
    nowhere to put the arguments, so the second declaration was a duplicate function and the
    message elided the target as `Box@(...)`.
    """
    by_type: Dict[str, Dict[Tuple[str, str], GenericExtensionMethod]] = field(default_factory=dict)
    # The `(base type name, method name)` pairs whose declaration the collect pass
    # refused. A call of one is not an undefined name: the declaration carries the one
    # diagnostic (#808).
    refused: Set[Tuple[str, str]] = field(default_factory=set)

    def refuse(self, base_type_name: str, method_name: str) -> None:
        """Record a refused declaration, so that its calls add no diagnostic."""
        self.refused.add((base_type_name, method_name))

    def was_refused(self, base_type_name: str, method_name: str) -> bool:
        """Did the collect pass refuse a declaration of this method on this base?"""
        return (base_type_name, method_name) in self.refused

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



@dataclass
class _ExtensionHeader:
    """What one `extend` declaration says, read once (#693).

    The three per-target collectors used to take twelve parameters each and to read
    the same fifteen fields off the node again. `target_type` is the one field that
    moves: the array arm answers the element-resolved target, and the concrete arm
    files under it.
    """

    ext: ExtendDef
    name: str
    params: List[Param]
    target_type: Optional[Type]
    ret_ty: Optional[Type]
    err_ty: Optional[Type]
    body: Optional[Block]
    name_span: Optional[Span]
    target_type_span: Optional[Span]
    ret_span: Optional[Span]
    err_span: Optional[Span]
    # Method-level type parameters (`name@(U)`, ruling on identity). Their names join
    # the receiver-derived ones in the deep signature conversion; a name that repeats a
    # receiver parameter is CE2064, refused where the receiver's own names are known.
    method_type_params: Tuple[str, ...]
    is_static: bool


def _read_extension_header(ext: ExtendDef) -> Optional[_ExtensionHeader]:
    """Read one `extend` declaration. None when it names no method to collect."""
    if not isinstance(ext.name, str):
        return None

    name_span = ext.name_span or ext.loc
    return _ExtensionHeader(
        ext=ext,
        name=ext.name,
        params=[param_from_node(p, idx)
                for idx, p in enumerate(ext.params or [])],
        target_type=ext.target_type,
        ret_ty=ext.ret,
        err_ty=ext.err_type,
        body=ext.body,
        name_span=name_span,
        target_type_span=ext.target_type_span,
        ret_span=ext.ret_span or name_span,
        err_span=ext.err_span,
        method_type_params=tuple(tp.name for tp in (ext.type_params or ())),
        is_static=ext.is_static,
    )


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
        generic_enums: 'GenericEnumTable',
        is_declared_type: Callable[[str], bool],
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
        self.is_declared_type = is_declared_type

    def collect_functions(self, root: Program) -> None:
        """Collect all function definitions from program AST."""
        funcs = root.functions
        if isinstance(funcs, list):
            for fn in funcs:
                if isinstance(fn, FuncDef):
                    self._collect_function_def(fn)

    def collect_extensions(self, root: Program) -> None:
        """Collect all extension method definitions from program AST."""
        generic_extensions = root.generic_extensions
        if isinstance(generic_extensions, list):
            for ext in list(generic_extensions):
                if isinstance(ext, ExtendDef):
                    self._collect_extension_def(ext)

        extensions = root.extensions
        if isinstance(extensions, list):
            for ext in extensions:
                if isinstance(ext, ExtendDef):
                    self._collect_extension_def(ext)

            # Re-file what classification found to be no concrete extension after all.
            # The AST builder cannot tell `extend T[]` from `extend Crate[]` -- the
            # element is a bare name either way -- so both land in `extensions` and the
            # template (or a CE2101-rejected target) moves over here, out of the walks
            # that assume a concrete `self`.
            moved = [e for e in extensions
                     if isinstance(e, ExtendDef)
                     and (e.type_params
                          or isinstance(e.target_type, FunctionType)
                          or (isinstance(e.target_type, DynamicArrayType)
                              and (e.target_shape is None or e.target_shape.param_names)))]
            if moved:
                moved_ids = {id(e) for e in moved}
                root.extensions[:] = [e for e in extensions if id(e) not in moved_ids]
                root.generic_extensions.extend(moved)

    def register_stdlib_functions(self, root: Program) -> None:
        """Register stdlib functions from imported modules into the function table."""
        from sushi_lang.semantics.stdlib_registry import get_stdlib_registry

        registry = get_stdlib_registry()

        uses = root.uses
        if not isinstance(uses, list):
            return

        for use_stmt in uses:
            if not use_stmt.is_stdlib:
                continue  # Skip user modules
            if use_stmt.alias is not None:
                # `as` decides WHERE the names land, and an aliased import puts nothing
                # in the flat scope (`unit-namespaces.md` Ruling 1). This table IS the
                # flat scope for a registry module, and skipping it here is what lets a
                # unit declare `sin` beside `use <math> as std_math` (section 1.3).
                continue

            module_path = use_stmt.path

            module = registry.get_module(module_path)
            if module is None:
                continue

            for _func_name, stdlib_func in module.functions.items():
                self.funcs.register_stdlib_function(module_path, stdlib_func)

            for _const_name, stdlib_const in module.constants.items():
                self.funcs.register_stdlib_function(module_path, stdlib_const)

    def _redeclaration(self, name: str, name_span: Optional[Span],
                       prev: 'FuncSig | GenericFuncDef',
                       *, may_coexist: bool) -> Redeclaration:
        """A name already taken. What that means for the declaration taking it again.

        Three answers, and who owns the previous declaration decides which. A library's
        PUBLIC name may be replaced -- symbol priority puts the program's own
        declaration first, and `tests/libs/test_warn_lib_override.sushi` is that
        contract -- so this warns with CW3002 and the caller completes the replacement.
        Any other unit's declaration COEXISTS, a library's own private one included:
        each takes its own `<unit>$<name>` symbol and each unit's scope reads its own
        (`docs/design/unit-namespaces.md` sections 9 and 6). The same name twice inside
        ONE unit is the duplicate CE0101 still answers.

        CE3011 was the fourth answer and it keeps the TYPE arms alone, because a type is
        one name for the whole program until `docs/design/type-identity.md`'s phase 2.

        A GENERIC coexists exactly as a concrete function does: `GenericFunctionTable`
        carries the same two views since #495, so each unit's call resolves against its
        own declaration through the one ladder. Within ONE unit a generic beside a
        generic, or beside a concrete, of the same name stays CE0101 -- the same rule
        concrete functions follow.
        """
        clash = library_clash_origin(
            self.visibility, "function", name,
            current_unit=self.current_unit_name, library_units=self.library_units)
        if clash is not None and clash.is_public:
            self._warn_shadowed_export(name, name_span, clash)
            return Redeclaration.REPLACE
        prev_unit = prev.unit_name
        if may_coexist and prev_unit is not None and prev_unit != self.current_unit_name:
            return Redeclaration.COEXIST
        diag = er.emit_with(self.r, ERR.CE0101, name_span,
                            filename=self.current_unit_file, name=name)
        if prev.name_span is not None:
            diag.note_at("first defined here", prev.name_span, prev.filename)
        diag.emit()
        return Redeclaration.REFUSED

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
            diagnostic = diagnostic.note_at("exported here", clash.name_span, clash.filename)
        diagnostic.emit()

    @staticmethod
    def _drop(table, name: str) -> None:
        """Forget a registration, so the caller's own can take the name.

        The branch this replaces dropped the previous entry and returned without
        registering anything, so the consumer lost its own declaration as well and the
        library's came back through the `libraries` pass.

        Only the FLAT view is dropped. A unit does not stop having declared what it
        declared, and `view_for` is how its own body still reads it (#487).
        """
        table.order.remove(name)
        del table.by_name[name]

    def _collect_function_def(self, fn: FuncDef) -> None:
        """Dispatch function collection based on whether it's generic."""
        name = fn.name
        if not isinstance(name, str):
            return
        record_declaration(self.visibility, "function", fn,
                           unit_name=self.current_unit_name,
                           filename=self.current_unit_file)

        # A receiver parameter has no meaning on a plain top-level function (#327):
        # there is no receiver. The builder lifts the marker onto the FuncDef, so this
        # is the one place the plain-function context can say no.
        if fn.self_mode is not None:
            er.emit(self.r, ERR.CE2425, fn.self_mode_span or fn.name_span)
            return

        type_params_raw = fn.type_params
        type_params = extract_type_param_names(type_params_raw)

        if type_params and len(type_params) > 0:
            self._collect_generic_function_def(fn, type_params_raw)
        else:
            self._collect_concrete_function_def(fn)

    def _collect_concrete_function_def(self, fn: FuncDef) -> None:
        """Collect concrete (non-generic) function definition."""
        name = fn.name
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = fn.name_span or fn.loc
        ret_ty: Optional[Type] = fn.ret
        ret_span: Optional[Span] = fn.ret_span or name_span
        is_public: bool = fn.is_public

        if ret_ty is None:
            er.emit(self.r, ERR.CE0103, name_span, name=name)

        # Returning a borrow lets a function hand out a view of its own frame (CE2417,
        # #314). Checked on the DECLARED return type, before `resolve_return_type_to_result`
        # wraps it: after the wrap the reference sits inside an interned `Result<T, E>`,
        # which is built structurally and never passes the enum-payload check.
        reject_reference_in(self.r, ret_ty, ret_span, ERR.CE2417)

        err_ty: Optional[Type] = fn.err_type
        if is_explicit_result_type(ret_ty) and err_ty is not None:
            # User wrote: fn foo() Result<T, E1> | E2
            # This is an error because it's ambiguous and implies nesting
            err_type_name = getattr(err_ty, "name", str(err_ty))
            er.emit(self.r, ERR.CE2085, ret_span, err_type=err_type_name)

        params: List[Param] = []
        param_names: Set[str] = set()
        for idx, p in enumerate(fn.params or []):
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
        validate_type_pack_params(self.r, fn.type_params, params, name_span)

        if name in self.funcs.by_name:
            verdict = self._redeclaration(name, name_span, self.funcs.by_name[name],
                                          may_coexist=True)
            if verdict is Redeclaration.REFUSED:
                return
            if verdict is Redeclaration.REPLACE:
                self._drop(self.funcs, name)

        if name in self.generic_funcs.by_name:
            verdict = self._redeclaration(name, name_span,
                                          self.generic_funcs.by_name[name],
                                          may_coexist=True)
            if verdict is Redeclaration.REFUSED:
                return
            if verdict is Redeclaration.REPLACE:
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

        self.funcs.declare(name, sig)

    def _collect_generic_function_def(
        self,
        fn: FuncDef,
        type_params_raw: List,
    ) -> None:
        """Collect generic function definition."""
        name = fn.name
        name_span = fn.name_span or fn.loc

        if name in self.generic_funcs.by_name:
            verdict = self._redeclaration(name, name_span,
                                          self.generic_funcs.by_name[name],
                                          may_coexist=True)
            if verdict is Redeclaration.REFUSED:
                return
            if verdict is Redeclaration.REPLACE:
                self._drop(self.generic_funcs, name)

        if name in self.funcs.by_name:
            verdict = self._redeclaration(name, name_span, self.funcs.by_name[name],
                                          may_coexist=True)
            if verdict is Redeclaration.REFUSED:
                return
            if verdict is Redeclaration.REPLACE:
                self._drop(self.funcs, name)

        type_param_instances = tuple(type_params_raw)

        params = []
        param_names = set()
        for idx, p in enumerate(fn.params or []):
            param = param_from_node(p, idx)

            if param.name in param_names:
                er.emit(self.r, ERR.CE0102, param.name_span, name=param.name)
            else:
                param_names.add(param.name)

            params.append(param)

        # Generic variadics are out of scope for v1: reject a variadic parameter
        # in a generic function (also covers misplacement) with CE0114.
        if any(p.is_variadic for p in params):
            vparam = next(p for p in params if p.is_variadic)
            er.emit(self.r, ERR.CE0114, vparam.name_span,
                    message="variadic '...T' parameters are not supported in generic functions")

        # Validate v2 type-pack parameter placement / count / consistency
        # (CE0117/CE0118). Well-formed pack functions reach this path (they carry
        # a type-pack type-param). Keys on `is_pack`, disjoint from the CE0114
        # blanket above (which keys on `is_variadic`).
        validate_type_pack_params(self.r, type_params_raw, params, name_span)

        ret_ty = fn.ret
        ret_span = fn.ret_span or name_span

        if ret_ty is None:
            er.emit(self.r, ERR.CE0103, name_span, name=name)

        reject_reference_in(self.r, ret_ty, ret_span, ERR.CE2417)

        err_ty = fn.err_type
        if is_explicit_result_type(ret_ty) and err_ty is not None:
            # User wrote: fn foo<T>() Result<T, E1> | E2
            # This is an error because it's ambiguous and implies nesting
            err_type_name = getattr(err_ty, "name", str(err_ty))
            er.emit(self.r, ERR.CE2085, ret_span, err_type=err_type_name)

        body = fn.body
        if body is None:
            return

        generic_func = GenericFuncDef(
            name=name,
            type_params=type_param_instances,
            params=params,
            ret=ret_ty,
            body=body,
            is_public=fn.is_public,
            loc=fn.loc,
            name_span=name_span,
            ret_span=ret_span,
            err_type=fn.err_type,
            err_span=fn.err_span,
            unit_name=self.current_unit_name,
            filename=self.current_unit_file,
        )

        self.generic_funcs.declare(name, generic_func)

    def _collect_extension_def(self, ext: ExtendDef) -> None:
        """Collect one `extend` declaration: read it, refuse it, then file it.

        The three steps stay apart (#693). The header reads what the declaration says,
        once; the refusals that hold whatever the target is come next, in source order;
        and one collector per target KIND -- array, generic, concrete -- files the rest.
        """
        header = _read_extension_header(ext)
        if header is None:
            return

        self._reject_signature_faults(header)
        if header.is_static and self._reject_static_faults(header):
            return

        # A `??` has no error channel in a BARE extension body (CE0131, #398). A
        # declared `| E` IS the channel (ruling 1), so the reject does not apply there.
        if header.err_ty is None:
            reject_try_in_body(self.r, header.body, "an extension method")

        self._collect_for_target(header)

    def _reject_signature_faults(self, h: '_ExtensionHeader') -> None:
        """Every refusal the signature carries, whatever the target kind is."""
        if h.ret_ty is None:
            er.emit(self.r, ERR.CE0103, h.name_span,
                    name=f"extension method '{h.name}'")

        reject_reference_in(self.r, h.ret_ty, h.ret_span, ERR.CE2417)

        # A reference TARGET falls through every target filter below, so the method is
        # collected and then unreachable: every call reports "no such method" and the
        # body is dead code (CE2420, #319).
        reject_reference_in(self.r, h.target_type,
                            h.target_type_span or h.name_span, ERR.CE2420)

        seen: Set[str] = set()
        for param in h.params:
            if param.name == "self" or param.name in seen:
                er.emit(self.r, ERR.CE0102, param.name_span, name=param.name)
            else:
                seen.add(param.name)

        # Variadic parameters are not allowed in extension methods (CE0115).
        # The pack half is unreachable today, but the guard must match its
        # documented contract and stay correct by construction (#246).
        for param in h.params:
            if param.is_variadic or param.is_pack:
                er.emit(self.r, ERR.CE0115, param.name_span,
                        context="an extension method")
                break

    def _reject_static_faults(self, h: '_ExtensionHeader') -> bool:
        """The refusals a `static` marker brings; True when the declaration is dropped.

        A static has no receiver, so neither position may name one (CE0134, #542): a
        receiver MODE in the signature, or a `self` in the body. One fault, one code,
        and the caret sits on whichever was written.
        """
        if h.ext.self_mode is not None:
            er.emit_with(self.r, ERR.CE0134,
                         h.ext.self_mode_span or h.name_span, name=h.name) \
                .help("a static is called on the type name; drop the receiver, "
                      "or drop the `static` marker to get an instance method") \
                .emit()
        reject_self_in_body(self.r, h.body, h.name)

        if self._reject_variant_collision(h.target_type, h.name, h.name_span):
            return True

        # An array type has no spelling in an expression position, so a static on one
        # could never be called (CE2104). An unreachable declaration is a diagnostic,
        # exactly as CE2097 rules for a colliding built-in.
        if isinstance(h.target_type, (ArrayType, DynamicArrayType)):
            er.emit_with(self.r, ERR.CE2104,
                         h.target_type_span or h.name_span) \
                .help("write a free function, or a static on a struct that holds "
                      "the array").emit()
            return True

        return False

    def _collect_for_target(self, h: '_ExtensionHeader') -> None:
        """File the declaration with the collector its target KIND names."""
        if isinstance(h.target_type, DynamicArrayType):
            concrete_array = self._collect_array_extension(h, h.target_type)
            if concrete_array is None:
                return              # filed as a template, or refused
            h.target_type = concrete_array

        if isinstance(h.target_type, GenericTypeRef):
            self._collect_generic_extension(h, h.target_type)
        else:
            self._collect_concrete_extension(h)

    def _collect_generic_extension(self, h: '_ExtensionHeader',
                                   target_type: GenericTypeRef) -> None:
        """A `@(...)` target: a template, or the one instantiation it constrains.

        A concrete argument is a CONSTRAINT, a bare name is a type PARAMETER, and a mix
        of the two is partial specialization, which Sushi does not have (#393). The
        collect pass is the pass that can tell them apart, because the struct and enum
        tables say which names are declared types -- so the answer is decided here and
        carried.
        """
        shape = classify_extension_target(target_type, self.is_declared_type)
        h.ext.target_shape = shape
        if self._reject_generic_header(h, target_type, shape):
            self.generic_extensions.refuse(target_type.base_name, h.name)
            return

        method = self._generic_method(
            h, base_type_name=target_type.base_name,
            type_params=shape.param_names, target_key=shape.target_key,
            type_param_names=(*shape.param_names, *h.method_type_params))

        if self._reject_overlapping_target(method, target_type, h.name_span):
            return

        self.generic_extensions.add_method(method)

    def _reject_generic_header(self, h: '_ExtensionHeader', target_type: GenericTypeRef,
                               shape) -> bool:
        """CE2098, CE2001, CE2062 or CE2064 for a `@(...)` header. Answers whether it refused."""
        if shape.is_mixed:
            er.emit_with(self.r, ERR.CE2098, h.target_type_span or h.name_span,
                         target=display_type(target_type)) \
                .help("name every type parameter, or make every argument concrete -- "
                      "there is no partial specialization").emit()
            return True

        if reject_unwritable_target(self.r, shape, self.is_declared_type,
                                    h.target_type_span or h.name_span):
            return True

        shadowed = [m for m in h.method_type_params if m in shape.param_names]
        if shadowed:
            er.emit(self.r, ERR.CE2064, h.name_span, name=shadowed[0])
            return True
        return False

    def _collect_array_extension(self, h: '_ExtensionHeader',
                                 target_type: DynamicArrayType) -> Optional[Type]:
        """Classify a dynamic-array target (ruling 3): template, concrete, or CE2101.

        Answers the element-resolved concrete target type, or None when the declaration
        is fully handled here -- filed as a template, or refused.
        """
        from sushi_lang.semantics.generics.extension_targets import (
            ARRAY_BASE_KEY, classify_array_extension_target)

        element = target_type.base_type
        shape = classify_array_extension_target(element, self.is_declared_type)
        h.ext.target_shape = shape
        if shape is None:
            er.emit_with(self.r, ERR.CE2101, h.target_type_span or h.name_span,
                         element=display_type(element)) \
                .help("write a bare type-parameter name ('extend T[]') or a plain "
                      "declared type ('extend i32[]')").emit()
            return None

        if not shape.param_names:
            resolved = resolve_unknown_type(
                element, self.structs.by_name, self.enums.by_name)
            if resolved is not element:
                target_type = DynamicArrayType(base_type=resolved)
                h.ext.target_type = target_type
            return target_type

        param_name = shape.param_names[0]

        if param_name in h.method_type_params:
            er.emit(self.r, ERR.CE2064, h.name_span, name=param_name)
            return None

        for existing in self.generic_extensions.declarations(ARRAY_BASE_KEY, h.name):
            self._emit_duplicate_extension(
                f"extension method '{h.name}' for an array target",
                h.name_span, existing.unit_name, existing.name_span,
                existing.filename)
            return None

        self.generic_extensions.add_method(self._generic_method(
            h, base_type_name=ARRAY_BASE_KEY, type_params=(param_name,),
            target_key="", type_param_names=(param_name, *h.method_type_params)))
        return None

    def _collect_concrete_extension(self, h: '_ExtensionHeader') -> None:
        """A target that names ONE type: the extension table keys on the type itself."""
        resolved_type = resolve_unknown_type(
            h.target_type, self.structs.by_name, self.enums.by_name)

        # A function type is not an extension target (#771): refused at the target,
        # and recorded so that a call of the method adds no CE2008.
        if isinstance(resolved_type, FunctionType):
            er.emit(self.r, ERR.CE2110, h.target_type_span or h.name_span,
                    target=display_type(resolved_type))
            self.generic_extensions.refuse(display_type(resolved_type), h.name)
            return

        if h.method_type_params and resolved_type is not None:
            self._collect_method_generic(h, resolved_type)
            return

        if not isinstance(resolved_type, CONCRETE_EXTENSION_TARGETS):
            return

        existing = self.extensions.get_method(resolved_type, h.name)
        if existing is not None:
            self._emit_duplicate_extension(
                f"extension method '{h.name}' for '{display_type(resolved_type)}'",
                h.name_span, existing.unit_name, existing.name_span,
                existing.filename)
            return

        self.extensions.add_method(ExtensionMethod(
            target_type=resolved_type,
            name=h.name,
            loc=h.ext.loc,
            target_type_span=h.target_type_span,
            name_span=h.name_span,
            ret_type=h.ret_ty,
            ret_span=h.ret_span,
            params=h.params,
            self_mode=h.ext.self_mode,
            filename=self.current_unit_file,
            unit_name=self.current_unit_name,
            err_type=h.err_ty,
            err_span=h.err_span,
            is_static=h.is_static,
        ))

    def _collect_method_generic(self, h: '_ExtensionHeader',
                                resolved_type: Type) -> None:
        """A method-generic on a CONCRETE receiver (`extend i32 pick@(U)`).

        The margs dimension makes it a template: it files under the receiver's display
        name in the GenericExtensionTable -- never the ExtensionTable, which keys on
        (type, name) alone and gets no third dimension -- and instantiates at the call
        site, like the array binder does.
        """
        base = display_type(resolved_type)

        for existing in self.generic_extensions.declarations(base, h.name):
            self._emit_duplicate_extension(
                f"extension method '{h.name}' for '{base}'",
                h.name_span, existing.unit_name, existing.name_span,
                existing.filename)
            return

        self.generic_extensions.add_method(self._generic_method(
            h, base_type_name=base, type_params=(), target_key="",
            type_param_names=h.method_type_params))
        # The declaration itself must not be walked as a concrete extension: stash the
        # receiver so the drain can rebuild the target, and let collect_extensions
        # re-file the node under generic_extensions.
        h.ext.target_type = resolved_type

    def _generic_method(self, h: '_ExtensionHeader', *, base_type_name: str,
                        type_params: Tuple[str, ...], target_key: str,
                        type_param_names: Tuple[str, ...]) -> GenericExtensionMethod:
        """The ONE `GenericExtensionMethod` build, for all three template shapes.

        `type_param_names` are the names the signature converts into a `TypeParameter`:
        the receiver's, the method's own, or both.
        """
        def convert(ty: Optional[Type]) -> Optional[Type]:
            return deep_type_params(ty, type_param_names)

        return GenericExtensionMethod(
            base_type_name=base_type_name,
            type_params=type_params,
            target_key=target_key,
            name=h.name,
            loc=h.ext.loc,
            target_type_span=h.target_type_span,
            name_span=h.name_span,
            ret_type=convert(h.ret_ty),
            ret_span=h.ret_span,
            params=convert_param_types(h.params, convert),
            body=h.body,
            self_mode=h.ext.self_mode,
            filename=self.current_unit_file,
            unit_name=self.current_unit_name,
            err_type=convert(h.err_ty),
            err_span=h.err_span,
            method_type_params=h.method_type_params,
            is_static=h.is_static,
            decl=h.ext,
        )

    def _reject_variant_collision(self, target_type: Optional[Type], name: str,
                                  name_span: Optional[Span]) -> bool:
        """CE2103: a static may not spell a variant of the enum it extends (#542, Q1).

        One namespace sits behind a type's dot, and a variant always wins at the call
        site, so a static of that name would be compiled and never called. The base
        name is what is looked up: `extend Shape@(T) static Circle()` is the same
        collision one instantiation down.
        """
        base = _target_base_name(target_type)
        if base is None:
            return False
        enum_ty = self.enums.by_name.get(base) or self.generic_enums.by_name.get(base)
        if enum_ty is None:
            return False
        if not any(v.name == name for v in enum_ty.variants):
            return False
        diag = er.emit_with(self.r, ERR.CE2103, name_span, method=name, enum=base)
        declared_at = self.enums.spans.get(base) or self.generic_enums.spans.get(base)
        if declared_at is not None:
            diag.note_at("the variant is declared here", declared_at,
                         filename=(self.enums.files.get(base)
                                   or self.generic_enums.files.get(base)))
        diag.help("a name behind a type's dot is a variant or a static, never both -- "
                  "rename the static").emit()
        return True

    def _emit_duplicate_extension(self, name_text, name_span: Optional[Span],
                                  other_unit, other_span: Optional[Span],
                                  other_filename) -> None:
        """CE0101 for a second extension of one method name on one target.

        One unit wrote both: the second is the duplicate, and the note points
        at the first. Two units wrote them: neither author is at fault and a
        consumer can edit neither, so the diagnostic is relational -- it names
        both units and it blames no side (`unit-namespaces.md` section 8).
        """
        diag = er.emit_with(self.r, ERR.CE0101, name_span,
                            filename=self.current_unit_file, name=name_text)
        if (other_unit and self.current_unit_name
                and other_unit != self.current_unit_name):
            if other_span is not None:
                diag.note_at(f"unit '{other_unit}' declares it here",
                             other_span, other_filename)
            if name_span is not None:
                diag.note_at(f"unit '{self.current_unit_name}' declares it here",
                             name_span, self.current_unit_file)
            diag.help("a method is found on the receiver's type, so no alias "
                      "can choose between the two; rename one of them, or put "
                      "the method behind a perk")
        elif other_span is not None:
            diag.note_at("first defined here", other_span, other_filename)
        diag.emit()

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

            if same_target:
                self._emit_duplicate_extension(
                    f"extension method '{method.name}' for '{display_type(target_type)}'",
                    name_span, existing.unit_name, existing.name_span,
                    existing.filename)
                return True
            diag = er.emit_with(
                self.r, ERR.CE0101, name_span,
                name=f"extension method '{method.name}' for '{display_type(target_type)}'")
            if existing.name_span is not None:
                diag.note_at("this declaration already covers that target",
                             existing.name_span)
            diag.help("Sushi has no specialization: make both targets fully concrete, "
                      "or implement a perk on the concrete target -- a perk "
                      "implementation outranks an extension method by design.")
            diag.emit()
            return True

        return False
