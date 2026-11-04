"""The construction arm of an enum value in the typecheck pass over SHIR (docs/design/ir.md 7.3, 7.4).

A variant of a `Result` or a `Maybe` takes the instance of `Result@(T, E)` or `Maybe@(T)`
from the type of its position: the return type of the body, the written type of a `let`,
the type of a parameter. A variant of an enum that is not generic (`StdError.Error`, a
variant of an enum or an `error` of the program) is an instance of that enum, also behind
an alias (`geo.Sign.Plus`): the namespace holds the enum. The instance is an answer, and
the payload is checked against the types of the variant. A variant of a private enum of
another unit is CE3005. A node that has an error has no answer.

Lowering makes `T.m(...)` a `Static` call when the enum `T` has no variant `m`. When no
unit declares a method `m`, the dot of the enum holds no such member: CE2045. The bare
`T.m` is an error hole that keeps the member, and it is CE2045 too. The hole of a bare
call with written type arguments on a type name (`Pair@(i32, i32)(1, 2)`) is CE6102.

A wrapper variant whose position gives no instance, a variant of a generic enum of the
program, and a variant of an enum whose name the unit lost (D2) are not typed yet, and stop
the walk of their body.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.call_facts import TypecheckCallFacts
from sushi_lang.semantics.passes.types.calls.enums import (
    reject_missing_variant, validate_constructor_arguments)
from sushi_lang.semantics.passes.types.utils import reject_unpromised_arguments
from sushi_lang.semantics.passes.types.visibility import name_is_contested, reject_private_type
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.nodes import Hole
from sushi_lang.semantics.source_text import source_text
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import EnumType, Type
from sushi_lang.semantics.written_types import written_name

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator

#: The predefined generic enums whose variant takes the instance of its position.
WRAPPERS = ("Result", "Maybe")


def check_enum_init(validator: TypeValidator, node: e.EnumInit,
                    expected: Optional[Type]) -> Optional[Type]:
    offered = validator.errors_offered()
    instance = _instance(validator, node, expected)
    variant = instance.get_variant(node.variant)
    if variant is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"lowering made a construction of '{node.variant}', "
                                       f"which '{instance.name}' does not declare")
    reject_unpromised_arguments(validator, instance, node.loc)
    validate_constructor_arguments(validator, node, variant, instance)
    if validator.errors_offered() != offered:
        return None
    validator.body_answers().record_instance(node.shir_id, instance)
    return instance


def reject_unknown_variant(validator: TypeValidator, node: e.Call, target: e.Static,
                           expected: Optional[Type]) -> bool:
    """CE2045 for `T.m(...)` on an enum `T` whose dot holds no variant `m` and no method
    `m`. True when it was reported."""
    if TypecheckCallFacts(validator).declaring_units(target.name):
        return False
    return _reject_member(validator, written_name(target.ty), target.name, node.loc,
                          expected)


def check_hole(validator: TypeValidator, node: Hole,
               expected: Optional[Type] = None) -> None:
    """The error hole of a member of an enum that is no variant (`Sign.Nope`) is CE2045 at
    the member, the `Call` hole of written type arguments on a type name
    (`Pair@(i32, i32)(1, 2)`) is CE6102 at the hole, and the `RangeExpr` hole of a slice
    with a reversed range is CE2122 at the range (ir.md 7.4). Every other hole stops the
    walk of its body."""
    if node.ast_class == "Call" and "CE6102" in node.codes:
        _reject_construction_type_args(validator, node)
        return
    if node.ast_class == "RangeExpr" and "CE2122" in node.codes:
        er.emit_with(validator.reporter, er.ERR.CE2122, node.loc) \
            .help("a slice takes its range in order: write `data[a..b]`, and walk the view "
                  "with an index that counts down") \
            .emit()
        return
    member = node.member
    if member is None or not _reject_member(validator, member.written, member.name,
                                            member.name_loc, expected):
        visit.stop_at(validator, node)


def _reject_construction_type_args(validator: TypeValidator, node: Hole) -> None:
    """CE6102 at a construction with written type arguments. The help moves them to the
    type of the position, with the construction as the source writes it."""
    report = er.emit_with(validator.reporter, er.ERR.CE6102, node.loc)
    parts = _split_type_args(source_text(validator.reporter.body_source(), node.loc))
    if parts is None:
        report.help("write the type arguments on the type of the position").emit()
        return
    name, type_args, args = parts
    report.help(f"write the type arguments on the type of the position, e.g. "
                f"`let {name}{type_args} v = {name}{args}`").emit()


def _split_type_args(text: Optional[str]) -> Optional[tuple[str, str, str]]:
    """`Pair@(i32, i32)(1, 2)` as the name, the type arguments and the arguments, or None
    when the text has no such shape."""
    if text is None or "@(" not in text:
        return None
    name, _, rest = text.partition("@(")
    depth = 1
    for index, char in enumerate(rest):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0:
            return name.strip(), "@(" + rest[:index + 1], rest[index + 1:].strip()
    return None


def _reject_member(validator: TypeValidator, written: str, member: str, loc: Span,
                   expected: Optional[Type]) -> bool:
    """CE2045 for the member `member` of the written type `written` when it names an enum
    whose name the unit did not lose. True when it was reported. The diagnostic names the
    instance that the position gives to a generic enum, else the enum."""
    name = _enum_name(validator, written)
    if name is None or name_is_contested(validator, "enum", name):
        return False
    generic = name in validator.generic_enum_table.by_name
    if not generic and name not in validator.enum_table.by_name:
        return False
    shown = display_type(expected) if generic and is_instance_of(expected, name) else name
    reject_missing_variant(validator, loc, member, shown)
    return True


def _instance(validator: TypeValidator, node: e.EnumInit,
              expected: Optional[Type]) -> EnumType:
    """The enum that the construction builds; the walk stops where it is not typed yet."""
    written = validator.decl_name(node.decl)
    if written in WRAPPERS:
        if isinstance(expected, EnumType) and is_instance_of(expected, written):
            return expected
        visit.stop_at(validator, node)
    name = _enum_name(validator, written)
    if (name is None or name in validator.generic_enum_table.by_name
            or name_is_contested(validator, "enum", name)):
        visit.stop_at(validator, node)
    instance = validator.enum_table.by_name.get(name)
    if instance is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"the enum '{written}' of a construction has no record")
    if name != written:
        reject_private_type(validator, name, node.decl_loc)
    return instance


def _enum_name(validator: TypeValidator, written: str) -> Optional[str]:
    """The declared name of the enum that a written type names, or None when it names no
    enum: behind an alias (`geo.Sign`), the enum that the namespace holds."""
    alias, dot, name = written.rpartition(".")
    if not dot:
        return written
    binding = validator.namespaces.lookup(alias, name)
    return binding.name if binding is not None and binding.kind == "enum" else None
