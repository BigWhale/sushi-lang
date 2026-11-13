"""The expression arms of the typecheck pass over SHIR (docs/design/ir.md 7.3, 7.4).

An arm takes the pass, one node and the type of its position, or None when the position
gives no type. It answers the type of the node, or None when the node has an error or
reads a node that has one. The dispatch (`visit.check_expr`) writes the answer as the type
row of the node.

A literal takes the type of its position when the position gives one of its kind, else
its default (`int_literal_default`). An integer literal holds a value at that type
(`held_value`, ir.md 8.5). A local answers the type that the table holds for it. A constant
answers its declared type, and its value, from the constant table (Q5). A constant with no
value is an internal error: after an error of its initializer, that error is the
diagnostic. A `var` answers its declared type, and the symbol of its global.

A struct construction is an instance of the struct that its written type names: behind an
alias, the struct that the namespace holds. Its fields are checked at the types of the
fields (`calls/structs.py`). A private struct of another unit behind an alias is CE3005,
and CE6104 when it is named, as the old pipeline reports it. A type name that is no struct is CE2008, with CE6104 for a named argument. A name whose
function the collect pass refused (CE1005) gives no second error, and the body holds that
error (`refused_before`). A generic struct of the program, and a struct whose name the unit
lost (D2), are not typed yet, and stop the walk of their body.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Union

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics import integer_ops
from sushi_lang.semantics.library_registry import KEPT_STORAGE_KINDS
from sushi_lang.semantics.passes.collect.constants import ConstSig
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.calls.structs import check_struct_fields
from sushi_lang.semantics.passes.types.inference import (
    float_literal_fits, int_literal_default, int_literal_fits)
from sushi_lang.semantics.passes.types.resolution import resolve_variable_type
from sushi_lang.semantics.passes.types.utils import reject_named_argument
from sushi_lang.semantics.passes.types.visibility import (
    name_was_refused, reject_ambiguous_name, reject_private_kept, reject_private_name,
    reject_private_type, type_name_is_contested)
from sushi_lang.semantics.passes.types.visit.calls import reject_undefined_function
from sushi_lang.semantics.passes.types.visit.constructions import (
    Constructed, generic_struct_instance)
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.type_predicates import is_float_type, is_integer_type
from sushi_lang.semantics.typesys import BuiltinType, StructType, Type
from sushi_lang.semantics.unit_symbols import global_symbol

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.types import GenericStructType
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


def held_value(const: e.IntConst, ty: Type, wraps: bool, negated: bool = False) -> int:
    """The value that an integer literal holds at its integer type `ty` (ir.md 8.5), and
    with `negated` the value of its negation `-N`, which is one value with its sign.

    A literal that spells bits (a radix other than 10) holds the value that its type holds,
    and so does the operand of a cast or of a negation (`wraps`), where the range check of
    the literal is not the range of its type. Any other literal is in the range of its type.
    """
    if negated:
        return integer_ops.cast(-const.value, ty)
    if wraps or const.radix != 10:
        return integer_ops.cast(const.value, ty)
    return const.value


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
    """The type of the local; none when its binder has an error. A value pack of a template
    check has no type and no use outside `expand` (CE0144)."""
    ty = validator.body_answers().local_type(node.local)
    if ty is None and node.local in validator.value_packs:
        name = validator.body_locals[node.local].name
        er.emit_with(validator.reporter, er.ERR.CE0144, node.loc, name=name).help(
            f"walk a pack with `expand(a in {name}):`; pack forwarding and pack "
            "indexing are not supported").emit()
    return ty


def check_const_ref(validator: TypeValidator, node: e.ConstRef,
                    _expected: Optional[Type]) -> Optional[Type]:
    """The declared type of the constant. Another unit's private constant is CE3005, bare
    or behind an alias, and a bare name that two imports bring is CE3012."""
    written = validator.decl_name(node.const_id)
    record = constant_record(validator, written)
    if record is None:
        if reject_private_kept(validator, written, node.loc, kinds=KEPT_STORAGE_KINDS):
            return None
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"the constant '{written}' has no record")
    if not isinstance(record, ConstSig):
        _record_constant_value(validator, node, record)
        return record.get_return_type()
    if reject_private_name(validator, "constant", record, node.loc):
        return None
    if "." not in written and reject_ambiguous_name(validator, "constant", written, node.loc):
        return None
    if record.const_type is None:
        return None
    _record_constant_value(validator, node, record)
    return resolve_variable_type(validator, record.const_type, record.type_span)


def check_storage_ref(validator: TypeValidator, node: e.StorageRef,
                      _expected: Optional[Type]) -> Optional[Type]:
    """The declared type of the `var`, and the symbol of its global. Another unit's private
    `var` is CE3005, bare or behind an alias, and a bare name that two imports bring is
    CE3012."""
    written = validator.decl_name(node.decl)
    record = constant_record(validator, written)
    if not isinstance(record, ConstSig) or not record.is_var or record.decl is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"the unit variable '{written}' has no record")
    if reject_private_name(validator, "variable", record, node.loc):
        return None
    if "." not in written and reject_ambiguous_name(validator, "variable", written, node.loc):
        return None
    if record.const_type is None:
        return None
    validator.body_answers().record_global(node.shir_id,
                                           global_symbol(record.decl, record.unit_name))
    return resolve_variable_type(validator, record.const_type, record.type_span)


def _record_constant_value(validator: TypeValidator, node: e.ConstRef,
                           record: Union[ConstSig, StdlibFunction]) -> None:
    """The value row of `node`. A constant with no value after an error of the program
    (its initializer has one) gets no row: the walk goes on, and the body holds that error
    (`refused_before`). With no error, a constant with no value is an internal error."""
    value = validator.constant_value(record)
    if value is None:
        if not validator.errors_offered():
            visit.unhandled_with_no_value(validator, node)
        validator.refused_before = True
        return
    validator.body_answers().record_value(node.shir_id, value)


def constant_record(validator: TypeValidator,
                    written: str) -> Optional[Union[ConstSig, StdlibFunction]]:
    """The record of the constant that the written name of a `ConstRef` names in this body
    (`NamespaceTable.constant_record`)."""
    return validator.namespaces.constant_record(written, validator.const_table)


def check_struct_init(validator: TypeValidator, node: e.StructInit,
                      expected: Optional[Type]) -> Optional[Type]:
    """The struct that the construction builds, with its fields checked; none after an
    error of the construction or of a field."""
    offered = validator.errors_offered()
    built = _constructed_struct(validator, node, expected)
    struct = built.instance
    if isinstance(struct, StructType):
        check_struct_fields(validator, node, struct, built.typed)
    if struct is None or validator.errors_offered() != offered:
        return None
    validator.body_answers().record_instance(node.shir_id, struct)
    return struct


def _constructed_struct(validator: TypeValidator, node: e.StructInit,
                        expected: Optional[Type]) -> Constructed:
    """The struct that the written type of a construction names, or no instance after its
    error; a contested name is an internal error."""
    written = validator.decl_name(node.decl)
    alias, dot, name = written.rpartition(".")
    if dot:
        binding = validator.namespaces.lookup(alias, name)
        if binding is None or binding.kind != "struct":
            visit.unhandled(validator, node)
        name = binding.name
    if type_name_is_contested(validator, name):
        visit.unhandled(validator, node)
    generic = name in validator.generic_struct_table.by_name
    struct = None if generic else validator.struct_table.by_name.get(name)
    if struct is None and not generic:
        reject_undefined_function(validator, name, node.decl_loc)
        if node.names:
            reject_named_argument(validator, node.names[0], node.loc)
        if name_was_refused(validator, name):
            validator.refused_before = True
        for arg in node.fields:
            visit.check_expr(validator, arg.value)
        return Constructed(None)
    if dot and reject_private_type(validator, name, node.decl_loc):
        if node.named:
            declared = struct or validator.generic_struct_table.by_name[name]
            reject_named_argument(validator, _first_written_name(node, declared), node.loc)
        return Constructed(None)
    if generic:
        return generic_struct_instance(validator, node, name, written, expected)
    return Constructed(struct)


def _first_written_name(node: e.StructInit, struct: StructType | GenericStructType) -> str:
    """The name of the field that a named construction writes first."""
    if node.names is not None:
        return node.names[0]
    return struct.fields[node.written_order[0]][0]
