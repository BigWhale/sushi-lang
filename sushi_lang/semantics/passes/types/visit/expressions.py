"""The expression arms of the typecheck pass over SHIR (docs/design/ir.md 7.3, 7.4).

An arm takes the pass, one node and the type of its position, or None when the position
gives no type. It answers the type of the node, or None when the node has an error or
reads a node that has one. The dispatch (`visit.check_expr`) writes the answer as the type
row of the node.

A literal takes the type of its position when the position gives one of its kind, else
its default (`int_literal_default`). A local answers the type that the table holds for it.
A constant answers its declared type, from the constant table.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Union

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.collect.constants import ConstSig
from sushi_lang.semantics.passes.types.inference import (
    float_literal_fits, int_literal_default, int_literal_fits)
from sushi_lang.semantics.passes.types.resolution import resolve_variable_type
from sushi_lang.semantics.passes.types.visibility import (
    reject_ambiguous_name, reject_private_kept, reject_private_name)
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.stdlib_registry import lookup_stdlib_constant
from sushi_lang.semantics.type_predicates import is_float_type, is_integer_type
from sushi_lang.semantics.typesys import BuiltinType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator
    from sushi_lang.semantics.stdlib_registry import StdlibFunction

#: The name of a base in the text of CE2070.
_RADIX_NAMES = {2: "binary", 8: "octal", 10: "decimal", 16: "hexadecimal"}


def check_lit(validator: TypeValidator, node: e.Lit, expected: Optional[Type]) -> Optional[Type]:
    match node.value:
        case e.IntConst() as const:
            return type_int_literal(validator, const, expected, node.loc)
        case e.FloatConst(value):
            return type_float_literal(validator, value, expected, node.loc)
        case e.BoolConst():
            return BuiltinType.BOOL
        case e.StrConst():
            return BuiltinType.STRING
        case e.BlankConst():
            return BuiltinType.BLANK
    return None


def type_int_literal(validator: TypeValidator, const: e.IntConst, expected: Optional[Type],
                     loc: Span, negated: Optional[Span] = None) -> Type:
    """An integer literal at the integer type of its position, else at its default.

    A value that the type of its position cannot hold is CE2073, and one that its default
    cannot hold is CE2070. The type stays the answer: the value is wrong, not the type.
    A negated literal is one value: `negated` is the span of its negation, and the sign is
    part of the range check, so -128 is an `i8` and 128 is not. A negated byte literal with
    no type of its position is CE2073 at the negation, because no negative value fits its
    `u8`.
    """
    written = const.value
    value = -written if negated is not None else written
    radix = 10 if negated is not None else const.radix
    if isinstance(expected, BuiltinType) and is_integer_type(expected):
        if not int_literal_fits(value, radix, expected):
            spelling = const.byte_spelling if negated is None else None
            er.emit(validator.reporter, er.ERR.CE2073, loc,
                    literal=spelling or str(value), type=expected.value)
        return expected
    default = int_literal_default(const)
    if int_literal_fits(value, radix, default):
        return default
    if negated is not None and const.byte_spelling is not None:
        er.emit(validator.reporter, er.ERR.CE2073, negated, literal=str(value),
                type=default.value)
        return default
    value = written
    er.emit(validator.reporter, er.ERR.CE2070, loc,
            radix=_RADIX_NAMES.get(const.radix, "integer"), literal=str(value),
            type=default.value)
    return default


def type_float_literal(validator: TypeValidator, value: float, expected: Optional[Type],
                       loc: Span) -> Type:
    if isinstance(expected, BuiltinType) and is_float_type(expected):
        if not float_literal_fits(value, expected):
            er.emit(validator.reporter, er.ERR.CE2073, loc, literal=str(value),
                    type=expected.value)
        return expected
    return BuiltinType.F64


def check_local_ref(validator: TypeValidator, node: e.LocalRef,
                    _expected: Optional[Type]) -> Optional[Type]:
    """The type of the local; none when its binder has an error."""
    return validator.body_answers().local_type(node.local)


def check_const_ref(validator: TypeValidator, node: e.ConstRef,
                    _expected: Optional[Type]) -> Optional[Type]:
    """The declared type of the constant. Another unit's private constant is CE3005, bare
    or behind an alias, and a bare name that two imports bring is CE3012."""
    written = validator.decl_name(node.const_id)
    record = constant_record(validator, written)
    if record is None:
        if reject_private_kept(validator, written, node.loc, kinds={"constant"}):
            return None
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"the constant '{written}' has no record")
    if not isinstance(record, ConstSig):
        return record.get_return_type()
    if reject_private_name(validator, "constant", record, node.loc):
        return None
    if "." not in written and reject_ambiguous_name(validator, "constant", written, node.loc):
        return None
    if record.const_type is None:
        return None
    return resolve_variable_type(validator, record.const_type, record.type_span)


def constant_record(validator: TypeValidator,
                    written: str) -> Optional[Union[ConstSig, StdlibFunction]]:
    """The record of the constant that the written name of a `ConstRef` names in this body.

    Behind an alias (`geo.SIZE`), what the namespace holds; a bare name, the constant of the
    unit's scope, else the stdlib constant that a flat import brings.
    """
    alias, dot, name = written.rpartition(".")
    if dot:
        binding = validator.namespaces.lookup(alias, name)
        return None if binding is None else binding.record
    sig = validator.const_sig(written)
    return sig if sig is not None else lookup_stdlib_constant(written, validator.scope)
