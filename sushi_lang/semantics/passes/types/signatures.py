"""Declaration signature validation for type validation (the typecheck pass)."""
from __future__ import annotations

from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import FuncDef, ExtendDef, ExtendWithDef
from sushi_lang.semantics.typesys import (
    BuiltinType, UnknownType, ArrayType, DynamicArrayType, StructType, EnumType
)
from sushi_lang.semantics.type_resolution import resolve_unknown_type

from .utils import validate_type_name, validate_and_register_parameters
from .perks import validate_perk_implementation, check_no_conflicts_with_regular_methods
from sushi_lang.semantics.generics.type_display import display_type


# The positions `run()`'s per-declaration loop never reaches. A function, an
# extension and a perk implementation are all walked for their bodies, and a struct
# or an enum has none, so its written types were checked nowhere (#504).
_DECLARED_TYPE_POSITIONS = frozenset({"field", "variant"})


def validate_declared_types(self, program) -> None:
    """Check every type a declaration WITHOUT a body writes down.

    Reads `signature_types()`, the one walk over a unit's declared type positions, so a
    position added there is policed here without a second walk. A GENERIC declaration is
    skipped for the reason the function loop skips one: its fields name type parameters,
    which are in no table until the instance is monomorphized -- and the instance is an
    ordinary declaration that this walk reaches.
    """
    from sushi_lang.semantics.ast_walk import signature_types

    # Not a LIBRARY unit, for `check_public_signatures`' reason: its declarations were
    # checked when the library was built, and here they carry the consumer's substitutions.
    if self.in_library_unit:
        return

    for site in signature_types(program):
        if site.position not in _DECLARED_TYPE_POSITIONS or site.ty is None:
            continue
        if getattr(site.decl, "type_params", None):
            continue
        validate_type_name(self, site.ty, site.span)


def validate_function(self, func: FuncDef) -> None:
    """Validate types within a function."""
    self.current_function = func
    self.in_extension_context = False  # Normal functions are never extension/perk bodies
    self.extension_channel_result = None
    self.in_library_body = (self.in_library_unit
                            or bool(getattr(func, "is_library_template", False)))
    self.in_synthesized_body = bool(getattr(func, "is_synthesized", False))
    # And whose file it is. Set on every entry, so an ordinary body clears what a
    # transplanted one set (#471).
    self.reporter.origin = getattr(func, "library_origin", None)
    self.extension_method_name = None
    self.extension_return_type = None
    self.variable_types = {}  # Reset for each function
    self.destroyed_arrays = [set()]  # Reset for each function with initial scope

    validate_and_register_parameters(self, func.params)

    validate_type_name(self, func.ret, func.ret_span)

    if func.err_type is not None:
        validate_type_name(self, func.err_type, func.ret_span)  # Use ret_span since we don't have err_span

        resolved_err_type = func.err_type

        if isinstance(func.err_type, UnknownType):
            resolved_err_type = resolve_unknown_type(
                func.err_type,
                self.struct_table.by_name,
                self.enum_table.by_name
            )

        if not isinstance(resolved_err_type, EnumType):
            self.err.emit(er.ERR.CE2084, func.ret_span,
                         type_name=display_type(func.err_type))

    self._validate_block(func.body)

    if func.ret != BuiltinType.BLANK:
        if not self._block_always_returns(func.body):
            self.err.emit(er.ERR.CE0107, func.name_span, name=func.name)

    self.current_function = None


def _self_registration_type(target_type, self_mode):
    """The type `self` registers under: the bare target, or its reference (#327)."""
    if self_mode is None:
        return target_type
    from sushi_lang.semantics.typesys import BorrowMode, ReferenceType
    mode = BorrowMode.POKE if self_mode == "poke" else BorrowMode.PEEK
    return ReferenceType(target_type, mode)


def _validate_target_type(self, target_type, span) -> None:
    """The target type of an extension or a perk implementation, checked ONCE.

    It belongs to the header, not to a method, so a perk implementation with three
    methods and one bad target says so once.
    """
    # An extension body is never a transplanted library body, so the origin a previously
    # validated function set must not colour this diagnostic (#471).
    self.reporter.origin = None
    validate_type_name(self, target_type, span)
    if target_type == BuiltinType.BLANK:
        self.err.emit(er.ERR.CE2032, span)


def _register_self(self, target_type, self_mode) -> None:
    """Register `self` under the target type, when the target names a type at all.

    A `poke self` / `peek self` receiver (#327) registers its full `ReferenceType`, so
    every consumer that asks "is this name a borrow?" answers truthfully and inference
    auto-dereferences.
    """
    self_type = None
    if isinstance(target_type,
                  (BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType)):
        self_type = target_type
    elif isinstance(target_type, UnknownType):
        resolved = resolve_unknown_type(
            target_type, self.struct_table.by_name, self.enum_table.by_name)
        if resolved != target_type:
            self_type = resolved
    if self_type is not None:
        self.variable_types["self"] = _self_registration_type(self_type, self_mode)


def _validate_method_body(self, target_type, method) -> None:
    """One method body, in the state a BARE-return body validates under.

    An extension method and a perk-implementation method differ in one thing only: where
    the target type comes from -- the declaration itself, or the `extend X with P` header.
    """
    self.current_function = None  # A method is not a function, but the logic is shared.
    self.in_extension_context = True  # Dedicated flag: this body returns a bare value.
    self.in_library_body = self.in_library_unit
    self.in_synthesized_body = False
    self.reporter.origin = None
    self.extension_method_name = method.name
    self.extension_return_type = method.ret  # Checked in validate_return_statement.
    self.variable_types = {}
    self.destroyed_arrays = [set()]

    # A `| E` extension (ruling 1) validates under its CHANNEL: the interned
    # Result@(ret, E) that `??` propagates into and that `Result.Err(e)` constructs.
    # The success still returns bare against `extension_return_type` (ruling 6).
    self.extension_channel_result = None
    err_ty = getattr(method, "err_type", None)
    if err_ty is not None:
        from sushi_lang.semantics.generics.results import ensure_result_type_in_table
        from sushi_lang.semantics.type_resolution import resolve_unknown_type
        validate_type_name(self, err_ty, getattr(method, "err_span", None))
        resolved_err = resolve_unknown_type(
            err_ty, self.struct_table.by_name, self.enum_table.by_name)
        self.extension_channel_result = ensure_result_type_in_table(
            self.enum_table, method.ret, resolved_err,
            struct_table=self.struct_table.by_name)

    _register_self(self, target_type, getattr(method, "self_mode", None))

    validate_and_register_parameters(self, method.params)

    validate_type_name(self, method.ret, method.ret_span)

    self._validate_block(method.body)

    if method.ret != BuiltinType.BLANK and not self._block_always_returns(method.body):
        self.err.emit(er.ERR.CE0107, method.name_span, name=method.name)

    self.in_extension_context = False
    self.extension_method_name = None
    self.extension_channel_result = None


def validate_extension_method(self, ext: ExtendDef) -> None:
    """Validate types within an extension method. The declaration IS the method."""
    _validate_target_type(self, ext.target_type, ext.target_type_span)
    _validate_method_body(self, ext.target_type, ext)


def validate_perk_implementation_method(self, impl: ExtendWithDef) -> None:
    """Validate a perk implementation: the contract, the header, then each method."""
    perk_def = self.perk_table.by_name.get(impl.perk_name)
    if not perk_def:
        self.err.emit(er.ERR.CE4003, impl.perk_name_span, perk=impl.perk_name)
        return

    validate_perk_implementation(impl, perk_def, self.reporter)

    resolved_type = impl.target_type
    if isinstance(impl.target_type, UnknownType):
        resolved_type = resolve_unknown_type(
            impl.target_type, self.struct_table.by_name, self.enum_table.by_name)
    if resolved_type is not None:
        check_no_conflicts_with_regular_methods(
            resolved_type, impl, self.extension_table, self.reporter)

    _validate_target_type(self, impl.target_type, impl.target_type_span)

    for method in impl.methods:
        _validate_method_body(self, impl.target_type, method)
