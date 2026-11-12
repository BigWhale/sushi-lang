"""The arms of a field read and of a tuple in the typecheck pass over SHIR (docs/design/ir.md
7.3, 7.4).

A field read checks its base at no type of its position. The type of the base with no
`peek` or `poke` decides the field: a field of a struct, or an element of a tuple, answers
the type that the struct declares for it, with its index as the field index row. A name
that the type does not declare is CE2106 at the member. A base of a type whose name the
unit lost (D2), and a base of a type that has no rule here yet, stop the walk.

A tuple checks each part at the element type of its position, and answers the tuple of
the part types. A `Borrow` in a part is CE2002 at the part (ruling R-I): a tuple holds
values. A destructure gives a tuple what each target wants of its part
(`check_tuple_at`), so a literal part takes the type of its binder or of its place.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, TypeAlias, Union

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.generics.tuples import intern_tuple, is_tuple_type, tuple_elements
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.calls.structs import resolve_field_type
from sushi_lang.semantics.passes.types.compatibility import reject_mismatch
from sushi_lang.semantics.passes.types.expressions import take_the_value
from sushi_lang.semantics.passes.types.visibility import type_is_contested
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, ForeignPtrType, FunctionType,
    PointerType, StructType, Type, deref_type)

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_field(validator: TypeValidator, node: e.Field,
                _expected: Optional[Type]) -> Optional[Type]:
    """The type of the field that `node` reads; none after an error of its base or of its
    name."""
    base = visit.check_expr(validator, node.base)
    if base is None:
        return None
    ty = deref_type(base)
    if type_is_contested(validator, ty):
        visit.unhandled(validator, node)
    if isinstance(ty, StructType):
        for index, (name, field_type) in enumerate(ty.fields):
            if name == node.name:
                validator.body_answers().record_field_index(node.shir_id, index)
                return resolve_field_type(validator, field_type)
    names = _field_names_of(ty)
    if names is None:
        visit.unhandled(validator, node)
    _reject_unknown_field(validator, node, ty, names)
    return None


def check_take(validator: TypeValidator, node: e.Take,
               expected: Optional[Type]) -> Optional[Type]:
    """`nom s.out` in a `let` or a `return`: the type of its place. Whether the place is a
    take is a rule of the borrow pass (docs/design/ir.md 7.4)."""
    return visit.check_expr(validator, node.place, expected)


def _field_names_of(ty: Optional[Type]) -> Optional[list[str]]:
    """The fields of a type, or None when the pass has no rule for the type yet.

    A struct answers its fields. The kinds below have no field, so each answers the empty
    list. An enum is one of them: it carries variants, and a pattern reads a variant. An
    opaque type parameter has the methods that its constraints give, and no field.
    """
    from sushi_lang.semantics.generics.types import TypeParameter

    if isinstance(ty, StructType):
        return [name for name, _ in ty.fields]
    if isinstance(ty, (ArrayType, DynamicArrayType, BuiltinType, EnumType, FunctionType,
                       PointerType, ForeignPtrType)):
        return []
    if isinstance(ty, TypeParameter) and ty.is_opaque:
        return []
    return None


def _is_a_method(validator: TypeValidator, ty: Type, name: str) -> bool:
    """Does the type have a METHOD of this name: a built-in one, an extension, or a method
    of a template that a call would copy (#1196)?"""
    from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
    from sushi_lang.semantics.generics.extensions import target_methods_of

    if validator.extension_table.get_method(ty, name) is not None:
        return True
    if target_methods_of(validator.generic_extension_table, ty, name, validator.tables):
        return True
    return builtin_method_exists(ty, name, validator.derived_methods)


def _reject_unknown_field(validator: TypeValidator, node: e.Field, ty: Type,
                          names: list[str]) -> None:
    """CE2106 at the member: the type `ty` does not declare the field `node.name` (#630,
    #661, #666). A note says when the name is a method or the type is an enum, and a help
    says how to read the value, or names the near field or the fields of the type."""
    from sushi_lang.semantics.generics.opaque import note_opaque
    from sushi_lang.semantics.generics.results import is_builtin_wrapper_enum
    from sushi_lang.semantics.namespaces import suggest_member

    shown = display_type(ty)
    builder = er.emit_with(validator.reporter, er.ERR.CE2106, node.member_loc,
                           type=shown, field=node.name)
    if _is_a_method(validator, ty, node.name):
        builder.note(f"'{shown}.{node.name}()' is a method, not a field").help(
            "call it: write the parentheses")
    elif isinstance(ty, EnumType):
        builder.note(f"'{shown}' is an enum: it carries variants, not fields")
        if is_builtin_wrapper_enum(ty):
            builder.help(f"take the value first with {take_the_value(ty.generic_base)}")
        else:
            builder.help("read a payload with 'match'")
    else:
        close = suggest_member(names, node.name)
        if close is not None:
            builder.help(f"did you mean '{close}'?")
        elif names:
            builder.help(f"'{shown}' declares {', '.join(repr(n) for n in names)}")
    note_opaque(builder, ty).emit()


#: What a position wants of a part of a tuple: a type, no type (None), or, for a part that
#: is a tuple too, what it wants of each part of that tuple. A destructure gives the
#: wants of its targets (ir.md 7.5).
Want: TypeAlias = Union[Type, None, list["Want"]]


def check_tuple_init(validator: TypeValidator, node: e.TupleInit,
                     expected: Optional[Type]) -> Optional[Type]:
    """The tuple of the part types, each part checked at the element type of its position;
    none after an error of a part."""
    wants: list[Want] = list(_position_elements(deref_type(expected), len(node.parts)))
    return _tuple_of_parts(validator, node, wants)


def check_tuple_at(validator: TypeValidator, node: e.ShirExpr,
                   wants: list[Want]) -> Optional[Type]:
    """The type of `node` at a position that wants `wants` of the parts of a tuple. A
    tuple with one part for each want checks each part at its want, and any other value
    is checked at no type."""
    if isinstance(node, e.TupleInit) and len(node.parts) == len(wants):
        return visit.answer(validator, node, _tuple_of_parts(validator, node, wants))
    return visit.check_expr(validator, node)


def _tuple_of_parts(validator: TypeValidator, node: e.TupleInit,
                    wants: list[Want]) -> Optional[Type]:
    parts = [_check_part(validator, part, want)
             for part, want in zip(node.parts, wants, strict=True)]
    if any(ty is None for ty in parts):
        return None
    tuple_type = intern_tuple(validator.struct_table, validator.enum_table, parts)
    validator.body_answers().record_instance(node.shir_id, tuple_type)
    return tuple_type


def _position_elements(expected: Optional[Type], count: int) -> list[Optional[Type]]:
    """The element types of the tuple of the position, when it has `count` elements."""
    if is_tuple_type(expected) and expected is not None:
        elements = tuple_elements(expected)
        if len(elements) == count:
            return list(elements)
    return [None] * count


def _check_part(validator: TypeValidator, part: e.ShirExpr, want: Want) -> Optional[Type]:
    """The type of one part with no `peek` or `poke`. A `Borrow` is CE2002 at the part
    (ruling R-I), against the element type of the position, or the type of the borrowed
    place where the position gives none."""
    if isinstance(want, list):
        ty = check_tuple_at(validator, part, want)
        want = None
    else:
        ty = visit.check_expr(validator, part, want)
    if ty is None:
        return None
    if isinstance(part, e.Borrow):
        value = deref_type(ty)
        reject_mismatch(validator, ty, want if want is not None else value, part.loc, None)
        return None
    return deref_type(ty)
