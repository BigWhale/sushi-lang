"""E3: the `E` of every `Result@(T, E)` is an error type.

docs/design/error-conversion.md sections 2.4 and 2.5. The predicate is `is_error_type`
(`type_predicates.py`). Three callers judge with it, and each one refuses through
`reject_non_error_type`, so every refusal is CE2084:

1. `validate_error_channel` (`passes/types/signatures.py`): the `| E` of a signature.
2. `validate_type_name` (`passes/types/utils.py`): every `E` that a written type holds.
3. The monomorphizer: a type parameter in an `E` position, at each instance.

The `Result` interning seam (`intern_wrapper_enum`) does not judge, because it interns
types that the compiler makes too, and it has no location.
"""
from __future__ import annotations

from typing import Iterator, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.generics.interned import interned_name
from sushi_lang.semantics.generics.tuples import TUPLE_BASE
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.generics.types import GenericTypeRef, TypeParameter
from sushi_lang.semantics.type_predicates import is_error_type, is_instance_of
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, FunctionType, ReferenceType,
    StructType, Type, UnknownType,
)

_CONTAINERS = ("List", "HashMap", "Own")


def error_positions(ty: Optional[Type]) -> Iterator[Type]:
    """Every type that a type holds in an `E` position, at any depth.

    An `E` position is the second argument of a `Result`, written or interned, and the
    `| E` of a function type. The walk does not enter a named declaration: its fields and
    payloads are judged where they are written.
    """
    from sushi_lang.semantics.type_walk import walk_named_types

    for inner in walk_named_types(ty, through_declarations=False):
        if isinstance(inner, GenericTypeRef):
            if inner.base_name == "Result" and len(inner.type_args) == 2:
                yield inner.type_args[1]
        elif isinstance(inner, FunctionType):
            if inner.err_type is not None:
                yield inner.err_type
        elif isinstance(inner, (StructType, EnumType)) and inner.generic_args:
            if is_instance_of(inner, "Result") and len(inner.generic_args) == 2:
                yield inner.generic_args[1]
            for arg in inner.generic_args:
                yield from error_positions(arg)


def _named_type(ty: Type, structs: dict, enums: dict) -> Optional[Type]:
    """The type an `E` position names, or None when this position cannot judge it.

    A type parameter is judged at each instance. A name that no table holds, and an
    instance that was not built, are each the CE2001 of the written type.
    """
    if isinstance(ty, TypeParameter):
        return None
    if isinstance(ty, UnknownType):
        return structs.get(ty.name) or enums.get(ty.name)
    if isinstance(ty, GenericTypeRef):
        name = interned_name(ty.base_name, ty.type_args)
        return enums.get(name) or structs.get(name)
    return ty


def non_error_type(ty: Type, structs: dict, enums: dict) -> Optional[Type]:
    """The type an `E` position names when it is NOT an error type; else None (E3)."""
    named = _named_type(ty, structs, enums)
    if named is None or is_error_type(named):
        return None
    return named


def kind_of(ty: Type) -> str:
    """What the refused type is, in the words of the message."""
    if isinstance(ty, EnumType):
        if is_instance_of(ty, "Maybe", "Result"):
            return f"a {ty.generic_base}"
        return "a plain enum"
    if isinstance(ty, StructType):
        if is_instance_of(ty, TUPLE_BASE):
            return "a tuple"
        if is_instance_of(ty, *_CONTAINERS):
            return "a container"
        return "a struct"
    if isinstance(ty, BuiltinType):
        return "a primitive type"
    if isinstance(ty, (ArrayType, DynamicArrayType)):
        return "an array"
    if isinstance(ty, FunctionType):
        return "a function type"
    if isinstance(ty, ReferenceType):
        return "a borrow"
    return "not an enum"


def reject_non_error_type(reporter: Reporter, ty: Type, span: Optional[Span],
                          structs: dict, enums: dict, *,
                          filename: Optional[str] = None,
                          note: Optional[tuple] = None) -> bool:
    """CE2084 when `ty`, in an `E` position, is not an error type. Answers whether it refused.

    `note` is `(message, span, filename)` of a relational note: the template, when the
    caller judges a generic instance. A plain enum that a unit declares gets the help to
    declare it with `error`; a predefined plain enum (`FileMode`, `SeekFrom`) does not.
    """
    named = non_error_type(ty, structs, enums)
    if named is None:
        return False
    diagnostic = er.emit_with(reporter, er.ERR.CE2084, span, filename=filename,
                              type_name=display_type(named), kind=kind_of(named))
    if (isinstance(named, EnumType) and not is_instance_of(named, "Maybe", "Result")
            and named.home_module is None):
        declared = named.generic_base or named.name
        diagnostic = diagnostic.help(
            f"declare '{declared}' with 'error' in place of 'enum'")
    if note is not None:
        message, note_span, note_file = note
        if note_span is not None:
            diagnostic = diagnostic.note_at(message, note_span, note_file)
    diagnostic.emit()
    return True


def error_parameters(written, names) -> dict:
    """The type parameters of a template that stand in an `E` position, each with a span.

    `written` is `(type, span)` for every type the template writes, and `names` is its
    type parameters. A parameter in an `E` position is judged at each instance, because
    the template names no type there (docs/design/error-conversion.md section 2.5, item
    2). The span is the first position that holds the parameter, for the note.
    """
    found: dict = {}
    for ty, span in written:
        for err_type in error_positions(ty):
            name = getattr(err_type, "name", None)
            if (isinstance(err_type, (TypeParameter, UnknownType)) and name in names
                    and getattr(err_type, "namespace", None) is None):
                found.setdefault(name, span)
    return found


def as_written_result(ret: Optional[Type], err_type: Optional[Type]) -> Optional[Type]:
    """A signature's return as the `Result@(T, E)` it is sugar for, when it spells `| E`."""
    if err_type is None:
        return ret
    return GenericTypeRef(base_name="Result", type_args=(ret, err_type))
