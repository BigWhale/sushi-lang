"""The one prologue of the container arms of the typecheck pass over SHIR: `List@(T)`,
`HashMap@(K, V)` and `Own@(T)` (docs/design/ir.md 7.3, 8.3).

Each arm asks the same questions in the same order: the method is a row of the arity table
of its family, the receiver is an instance of the family, the call answers the type of its
row, a `clone` of a value that holds a resource is refused, a wrong count is CE2009. Only
then does the arm read its arguments, and each argument gets the convention of
`argument_conventions`. The call answers the type of its row after each fault.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, NamedTuple, Optional

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.builtin_methods import reject_builtin_miscount
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.method_effects import argument_conventions
from sushi_lang.semantics.passes.types.arguments import record_builtin_conventions
from sushi_lang.semantics.passes.types.calls.methods import reject_uncloneable
from sushi_lang.semantics.passes.types.method_registry import MethodFamily
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import StructType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


class ContainerArm(NamedTuple):
    """The facts of one container family that its arm reads: the internal code of a
    method that is no row, the name of the type, the inferrer of the answer, and the parse
    of the receiver (None for a receiver that is no instance)."""

    missing_code: str
    kind: str
    inferrer: Callable[[StructType, str, 'TypeValidator'], Any]
    parse: Callable[[StructType, 'TypeValidator'], Any]


def check_container_method(validator: TypeValidator, node: e.Call, receiver: Type,
                           name: str, family: MethodFamily, arm: ContainerArm,
                           check_arguments: Callable[[Any], None]) -> Optional[Type]:
    """Check one call of a container method (see the module docstring). `check_arguments`
    reads the arguments, with the parse of the receiver."""
    if name not in family.arity:
        raise_internal_error(arm.missing_code, method=name)
    parsed = arm.parse(receiver, validator) if isinstance(receiver, StructType) else None
    if parsed is None or not isinstance(receiver, StructType):
        raise_internal_error("CE0000", span=node.loc,
                             detail=f"the {family.name} family answered a receiver that is "
                                    f"no {arm.kind}")
    answer = arm.inferrer(receiver, name, validator).infer_return_type()
    if name == "clone" and reject_uncloneable(validator, node, receiver):
        return answer
    if reject_builtin_miscount(validator.reporter, node.loc, name, len(node.args),
                               f"{display_type(receiver)}.{name}", family.arity):
        return answer
    check_arguments(parsed)
    record_builtin_conventions(validator, node.shir_id, node.args,
                               argument_conventions(family.name, name, len(node.args)))
    return answer
