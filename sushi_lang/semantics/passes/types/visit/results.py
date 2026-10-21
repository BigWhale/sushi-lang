"""The construction arm of an enum value in the typecheck pass over SHIR (docs/design/ir.md 7.3, 7.4).

`Result.Ok(v)` and `Result.Err(e)` take the instance of `Result@(T, E)` from the type of
their position: the return type of a body with a channel, the written type of a `let`, the
type of a parameter. A variant of an enum that is not generic (`StdError.Error`, a
variant of an `error` of the program) is an instance of that enum. The instance is an
answer, and the payload is checked against the types of the variant. A node that has an
error has no answer.

A `Result` variant whose position gives no `Result` type, a variant of another generic
enum (`Maybe`), and a variant behind an alias are not typed yet, and stop the walk of their
body.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.results import is_result_enum
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.calls.enums import validate_constructor_arguments
from sushi_lang.semantics.passes.types.utils import reject_unpromised_arguments
from sushi_lang.semantics.passes.types.visibility import name_is_contested
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import EnumType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_enum_init(validator: TypeValidator, node: e.EnumInit,
                    expected: Optional[Type]) -> Optional[Type]:
    instance = _instance(validator, node, expected)
    variant = instance.get_variant(node.variant)
    if variant is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"lowering made a construction of '{node.variant}', "
                                       f"which '{instance.name}' does not declare")
    reject_unpromised_arguments(validator, instance, node.loc)
    offered = validator.errors_offered()
    validate_constructor_arguments(validator, node, variant, instance)
    if validator.errors_offered() != offered:
        return None
    validator.body_answers().record_instance(node.shir_id, instance)
    return instance


def _instance(validator: TypeValidator, node: e.EnumInit,
              expected: Optional[Type]) -> EnumType:
    """The enum that the construction builds; the walk stops where it is not typed yet."""
    written = validator.decl_name(node.decl)
    if written == "Result":
        if isinstance(expected, EnumType) and is_result_enum(expected):
            return expected
        visit.stop_at(validator, node)
    if ("." in written or written in validator.generic_enum_table.by_name
            or name_is_contested(validator, "enum", written)):
        visit.stop_at(validator, node)
    instance = validator.enum_table.by_name.get(written)
    if instance is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"the enum '{written}' of a construction has no record")
    return instance
