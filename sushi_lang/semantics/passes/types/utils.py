"""Shared utilities for type validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, AbstractSet, List, Optional

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.generics.interned import interned_name
from sushi_lang.internals.report import Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.typesys import Type, BuiltinType, UnknownType, ArrayType, DynamicArrayType, StructType, EnumType, ReferenceType
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.passes.types.visibility import (
    reject_private_kept, reject_private_type)

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Param, Expr
    from . import TypeValidator


# What a kept NAME may be, when it is written where a type belongs.
_KEPT_TYPE_KINDS = frozenset({"struct", "enum"})


def validate_type_name(validator: 'TypeValidator', type_obj: Optional[Type], span: Optional[Span]) -> None:
    """Validate a WRITTEN type: every name in it, then every HashMap key it holds.

    The key rules are asked once per written type, over the whole of it, so a
    `HashMap@(K, V)` nested in a `Maybe@(...)` is read exactly once (#773).
    """
    _check_type_names(validator, type_obj, span)
    reject_unusable_hashmap_keys(validator, type_obj, span)


def reject_unusable_hashmap_keys(validator: 'TypeValidator', type_obj: Optional[Type],
                                 span: Optional[Span]) -> None:
    """CE2054 / CE2055 / CE2058 for every HashMap a written type holds (#773).

    A key type is written in a type, so the rules are read where the type is written and
    not at `HashMap.new()`. The walk enters a generic INSTANCE, whose fields exist only
    with this position's type arguments, and stops at a written declaration: that
    declaration's own fields are read where they are written.
    """
    if type_obj is None:
        return
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.generics.hashmap import reject_unusable_key
    from sushi_lang.semantics.type_walk import walk_named_types

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name

    def instance(ty: Type) -> Optional[Type]:
        if not isinstance(ty, GenericTypeRef):
            return None
        name = interned_name(ty.base_name, ty.type_args)
        return structs.get(name) or enums.get(name)

    def is_hashmap(ty: Type) -> bool:
        return isinstance(ty, StructType) and is_instance_of(ty, "HashMap")

    def stop(ty: Type) -> bool:
        return is_hashmap(ty) or (isinstance(ty, (StructType, EnumType))
                                  and not getattr(ty, "generic_args", None))

    for reached in walk_named_types(type_obj, structs, enums, stop=stop,
                                    resolve=instance, struct_type_args=True):
        if is_hashmap(reached):
            reject_unusable_key(reached, validator, span)


def _check_type_names(validator: 'TypeValidator', type_obj: Optional[Type], span: Optional[Span]) -> None:
    """Validate that every type name in a written type is known and may be named here."""
    if type_obj is None:
        return

    # A name written behind an alias answers to the namespace that holds it before it
    # answers to anything else (`docs/design/unit-namespaces.md` section 5). What
    # survives carries the bare name and every rule below reads it unchanged.
    from .qualified import reject_qualified_type
    if reject_qualified_type(validator, type_obj, span):
        return

    # A name this unit did not import is not a type here (section 6.1). Checked once,
    # for the two shapes a written type name takes, and never for a QUALIFIED one: the
    # namespace seam above has already said where that name may be written.
    from .visibility import reject_out_of_scope_type
    written = getattr(type_obj, "name", None) or getattr(type_obj, "base_name", None)
    if (getattr(type_obj, "namespace", None) is None and isinstance(written, str)
            and reject_out_of_scope_type(validator, written, span)):
        return

    from sushi_lang.semantics.generics.types import GenericTypeRef
    if isinstance(type_obj, GenericTypeRef):
        # CE2419. Checked FIRST and with NO Maybe/Result exemption, unlike the `ptr` gate
        # below -- a `ptr` has no lifetime to outlive, while `Maybe@(peek T)` is precisely
        # how a returned borrow escapes into a `match` (#314/#316). Also pre-empts the
        # backend's CE0022 on `List@(peek T)` (#318).
        from sushi_lang.semantics.type_predicates import contains_reference
        if any(contains_reference(arg) for arg in type_obj.type_args):
            offender = next(arg for arg in type_obj.type_args if contains_reference(arg))
            er.emit(validator.reporter, er.ERR.CE2419, span, ty=display_type(offender))
            return

        if type_obj.base_name == "Result" and len(type_obj.type_args) == 2:
            for type_arg in type_obj.type_args:
                _check_type_names(validator, type_arg, span)
            return

        # CE5012: foreign `ptr` as a generic type argument is only supported by
        # the built-in Result/Maybe enum machinery. Other containers (HashMap,
        # List, user generics) cannot carry an opaque handle - check BEFORE the
        # existence checks so the user sees the real reason, not a missing
        # monomorphization (CE2001).
        if type_obj.base_name != "Maybe":
            from sushi_lang.semantics.type_predicates import contains_foreign_ptr
            if any(contains_foreign_ptr(arg) for arg in type_obj.type_args):
                er.emit(validator.reporter, er.ERR.CE5012, span, base=type_obj.base_name)
                return

        is_generic_enum = type_obj.base_name in validator.generic_enum_table.by_name
        is_generic_struct = type_obj.base_name in validator.generic_struct_table.by_name

        if not is_generic_enum and not is_generic_struct:
            er.emit(validator.reporter, er.ERR.CE2001, span, name=type_obj.base_name)
            return

        for type_arg in type_obj.type_args:
            _check_type_names(validator, type_arg, span)

        concrete_name = interned_name(type_obj.base_name, type_obj.type_args)

        if concrete_name not in validator.enum_table.by_name and concrete_name not in validator.struct_table.by_name:
            # Monomorphized type should exist after monomorphization pass
            # If not, it means this instantiation wasn't collected. `concrete_name`
            # stays `<>` (it is the table lookup key above); the user sees `@()`.
            er.emit(validator.reporter, er.ERR.CE2001, span, name=display_type(type_obj))
        return

    if isinstance(type_obj, UnknownType):
        if type_obj.name in validator.struct_table.by_name:
            reject_private_type(validator, type_obj.name, span)
            return
        if type_obj.name in validator.enum_table.by_name:
            reject_private_type(validator, type_obj.name, span)
            return
        # A binary library's kept type reaches no table at all, so "unknown" was the
        # wrong word for it (#469, the type half).
        if reject_private_kept(validator, type_obj.name, span,
                               kinds=_KEPT_TYPE_KINDS):
            return
        er.emit(validator.reporter, er.ERR.CE2001, span, name=display_type(type_obj))
    elif isinstance(type_obj, BuiltinType) and type_obj not in validator.known_types:
        er.emit(validator.reporter, er.ERR.CE2001, span, name=display_type(type_obj))
    elif isinstance(type_obj, ArrayType):
        # Blank type cannot be used as array base type
        if type_obj.base_type == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2032, span)
            return
        _check_type_names(validator, type_obj.base_type, span)
        # Validate array size (CE2010: Array size must be positive integer literal)
        if type_obj.size <= 0:
            er.emit(validator.reporter, er.ERR.CE2010, span, size=type_obj.size)
    elif isinstance(type_obj, DynamicArrayType):
        # Blank type cannot be used as dynamic array base type
        if type_obj.base_type == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2032, span)
            return
        _check_type_names(validator, type_obj.base_type, span)
    elif isinstance(type_obj, ReferenceType):
        # A borrow of a type is not a different type (#305), so the REFERENT is checked the
        # same way. Without this arm a `peek Nope` fell through and reached the backend as
        # CE0020, telling the user their program was a compiler bug.
        _check_type_names(validator, type_obj.referenced_type, span)
    elif isinstance(type_obj, (StructType, EnumType)):
        # A named type that the resolve pass already interned. It exists by construction,
        # so the only question left is whether this unit may name it.
        reject_private_type(validator, type_obj.name, span)
    else:
        from sushi_lang.semantics.typesys import FunctionType, IteratorType, PointerType
        if isinstance(type_obj, FunctionType):
            for param_type in type_obj.param_types or ():
                _check_type_names(validator, param_type, span)
            _check_type_names(validator, type_obj.ok_type, span)
            _check_type_names(validator, type_obj.err_type, span)
        elif isinstance(type_obj, PointerType):
            _check_type_names(validator, type_obj.pointee_type, span)
        elif isinstance(type_obj, IteratorType):
            _check_type_names(validator, type_obj.element_type, span)


def reject_unknown_template_name(validator: 'TypeValidator', type_obj: Type,
                                 span: Optional[Span], known: AbstractSet[str]) -> bool:
    """CE2001 for one name in a TEMPLATE signature that names no type here (#859).

    `validate_type_name` reads an instance, and a template with no instance has none, so
    the template's written names are read here. The rule is the same one: an alias that
    does not hold the name, a type this unit did not import, and a name that no table
    holds. `known` is the declaration's own type parameters. Only the unknown name is
    refused here: every other rule reads the instance, and one fault is reported once.
    """
    from sushi_lang.semantics.generics.types import GenericTypeRef

    generics = (validator.generic_struct_table.by_name, validator.generic_enum_table.by_name)
    if isinstance(type_obj, GenericTypeRef):
        name = type_obj.base_name
        tables = generics
        declared = name == "Result"
    elif isinstance(type_obj, UnknownType):
        name = type_obj.name
        tables = (validator.struct_table.by_name, validator.enum_table.by_name, *generics)
        declared = False
    else:
        return False
    declared = declared or any(name in table for table in tables)

    namespace = getattr(type_obj, "namespace", None)
    if namespace is not None:
        namespaces = validator.namespaces
        if (namespaces.is_namespace(namespace)
                and namespaces.lookup(namespace, name) is not None):
            return False
        from .qualified import reject_qualified_name
        return reject_qualified_name(validator, namespace, name, span, kind="type")

    if name in known:
        return False
    from .visibility import reject_out_of_scope_type
    if reject_out_of_scope_type(validator, name, span):
        return True
    if declared:
        return False
    if reject_private_kept(validator, name, span, kinds=_KEPT_TYPE_KINDS):
        return True
    er.emit(validator.reporter, er.ERR.CE2001, span, name=name)
    return True


def read_constant_index(expr: 'Expr') -> Optional[int]:
    """The value of a literal array index, a negated one included.

    A `-1` parses as a UnaryOp over an IntLit and not as an IntLit, so a guard that
    asks only for IntLit never sees a negative index and the check behind it is dead
    code (#450).
    """
    from sushi_lang.semantics.ast import IntLit, UnaryOp

    if isinstance(expr, IntLit):
        return expr.value
    if isinstance(expr, UnaryOp) and expr.op == 'neg' and isinstance(expr.expr, IntLit):
        return -expr.expr.value
    return None


def validate_constant_array_index(expr: 'Expr', array_size: int, reporter) -> None:
    """Report a literal array index that cannot be in range.

    One reader and one pair of codes for every indexing form -- `a[i]`, `a.get(i)` --
    so a new form cannot inherit half the rule.
    """
    index_value = read_constant_index(expr)
    if index_value is None:
        return

    if index_value < 0:
        er.emit(reporter, er.ERR.CE2056, expr.loc, index=index_value)
    elif index_value >= array_size:
        er.emit(reporter, er.ERR.CE2012, expr.loc, index=index_value, size=array_size)


def resolve_declared_type(validator: 'TypeValidator', ty: Optional[Type]) -> Optional[Type]:
    """The concrete type that a DECLARED type names -- the pass's ONE answer (#755).

    A LOOKUP, and the table is its authority: nothing is built here, so a spelling that
    names no entry comes back as it was written.

    Two depths under it, and the split is deliberate. A NAME stops at name level, because
    a named type's identity IS its spelling: walking what it holds cannot change the
    answer, but it can cycle (#240). A type that CONTAINS others is walked, or the name
    inside `P[]` comes back unresolved and the message reads `expected P, got P` (#284).
    """
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.type_resolution import resolve_type_recursively
    from sushi_lang.semantics.typesys import FunctionType

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name

    if isinstance(ty, (UnknownType, GenericTypeRef)):
        return resolve_unknown_type(ty, structs, enums)
    if isinstance(ty, (FunctionType, ArrayType, DynamicArrayType)):
        return resolve_type_recursively(ty, structs, enums)
    return ty


def intern_declared_wrapper(validator: 'TypeValidator',
                            ty: Optional[Type]) -> Optional[Type]:
    """The interned `Result@(T, E)` or `Maybe@(T)` a WRITTEN type names, else None (#755).

    `resolve_declared_type` reads the table. A written wrapper is the one spelling that
    may name an entry nobody has built yet, because nothing instantiates a `Result@(T, E)`
    until a declaration asks for it, so it goes through the seam that alone may build one
    -- a structural build poisons the table (CE0126). Both seams resolve their own
    payloads recursively, so nothing is resolved before the call.

    None means "not a written wrapper". That is what lets a caller fall through to the
    lookup instead of reading the answer to tell a miss from a hit.
    """
    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if not isinstance(ty, GenericTypeRef):
        return None

    structs = validator.struct_table.by_name
    if ty.base_name == "Result" and len(ty.type_args) == 2:
        return ensure_result_type_in_table(validator.enum_table, ty.type_args[0],
                                           ty.type_args[1], struct_table=structs)
    if ty.base_name == "Maybe" and len(ty.type_args) == 1:
        return ensure_maybe_type_in_table(validator.enum_table, ty.type_args[0],
                                          struct_table=structs)
    return None


def validate_and_register_parameters(validator: 'TypeValidator', params: List['Param']) -> None:
    """Validate parameter types and register them in the variable_types table."""
    for param in params:
        validate_type_name(validator, param.ty, param.type_span)

        # Blank type cannot be used for parameters
        if param.ty == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2032, param.type_span)
            continue

        if isinstance(param.ty, ReferenceType):
            # The REFERENT gets the same resolution as a by-value parameter of that type
            # (#305). A borrow of a type is not a different type.
            resolved_ref = ReferenceType(
                referenced_type=resolve_declared_type(validator, param.ty.referenced_type),
                mutability=param.ty.mutability
            )
            validator.variable_types[param.name] = resolved_ref
            continue

        from sushi_lang.semantics.typesys import FunctionType
        if isinstance(param.ty, (FunctionType, ArrayType, DynamicArrayType)):
            # A container spelling: the WRAPPER is concrete but its members may not be.
            # A function type binds its implicit UnknownType("StdError"); an array binds
            # its element, which `P[]` leaves unresolved (#284).
            validator.variable_types[param.name] = resolve_declared_type(validator, param.ty)
            continue

        if isinstance(param.ty, (BuiltinType, StructType, EnumType)):
            validator.variable_types[param.name] = param.ty
        elif isinstance(param.ty, UnknownType):
            resolved_type = resolve_declared_type(validator, param.ty)
            if resolved_type != param.ty:
                validator.variable_types[param.name] = resolved_type
        else:
            from sushi_lang.semantics.generics.types import GenericTypeRef
            if isinstance(param.ty, GenericTypeRef):
                resolved_type = resolve_declared_type(validator, param.ty)
                if isinstance(resolved_type, GenericTypeRef):
                    resolved_type = None

                if resolved_type is not None:
                    validator.variable_types[param.name] = resolved_type
                    param.ty = resolved_type  # Update AST node for backend

                    func_sig = (validator.func_sig(validator.current_function.name)
                                if validator.current_function else None)
                    if func_sig is not None:
                        for sig_param in func_sig.params:
                            if sig_param.name == param.name:
                                sig_param.ty = resolved_type
                                break


def reject_spread_args(validator: 'TypeValidator', args: List) -> bool:
    """Reject any bloom spread `arr...` argument in a context that is never variadic."""
    from sushi_lang.semantics.ast import Spread
    found = False
    for arg in args:
        if isinstance(arg, Spread):
            er.emit(validator.reporter, er.ERR.CE0120, arg.loc,
                    message="bloom argument 'arr...' is only allowed as the last argument "
                            "of a call to a variadic '...T' function")
            validator.validate_expression(arg)
            found = True
    return found


def reject_named_args(validator: 'TypeValidator', node) -> None:
    """Refuse a named argument on a call that is not a struct construction (CE6104).

    One rule, read after the call is validated, because only then is the callee known.
    A struct construction SPENDS its names -- the field matcher puts the arguments in
    declaration order and clears them -- so names that are still here belong to a
    callee that has no field names to match (#563).
    """
    names = getattr(node, "field_names", None)
    if not names:
        return
    er.emit_with(validator.reporter, er.ERR.CE6104, node.loc, name=names[0]) \
        .help("pass the arguments in declaration order") \
        .emit()
    node.field_names = None


def mark_array_destroyed(validator: 'TypeValidator', name: str) -> None:
    """Mark a dynamic array as destroyed in the current scope."""
    if validator.destroyed_arrays:
        validator.destroyed_arrays[-1].add(name)


def is_array_destroyed(validator: 'TypeValidator', name: str) -> bool:
    """Check if a dynamic array has been destroyed in any current scope."""
    for destroyed_set in validator.destroyed_arrays:
        if name in destroyed_set:
            return True
    return False

