"""The opaque type parameter of a template check: what a type holds, and the note (#1070).

A template body is checked once, where it is written, with each type parameter OPAQUE:
the body knows what the constraints of the parameter promise, and nothing more. A
diagnostic about an operation on such a type points at the parameter and its
constraints, because the fix is a constraint there.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from sushi_lang.semantics.generics.types import TypeParameter

if TYPE_CHECKING:
    from sushi_lang.internals.report import DiagnosticBuilder
    from sushi_lang.semantics.passes.collect.perks import PerkTable
    from sushi_lang.semantics.typesys import Type


def opaque_parameters(ty: Optional['Type']) -> List[TypeParameter]:
    """Every opaque type parameter that `ty` holds, in walk order, each one time."""
    from sushi_lang.semantics.type_walk import walk_named_types
    found: List[TypeParameter] = []
    for held in walk_named_types(ty, struct_type_args=True):
        if isinstance(held, TypeParameter) and held.is_opaque and held not in found:
            found.append(held)
    return found


def holds_opaque(ty: Optional['Type']) -> bool:
    """Does `ty` hold an opaque type parameter anywhere?"""
    return bool(opaque_parameters(ty))


def note_opaque(builder: 'DiagnosticBuilder',
                *types: Optional['Type']) -> 'DiagnosticBuilder':
    """A note at the declaration of each opaque parameter that the types hold, one time."""
    params: List[TypeParameter] = []
    for ty in types:
        params += [p for p in opaque_parameters(ty) if p not in params]
    for param in params:
        if param.span is None:
            continue
        if param.constraints:
            text = (f"'{param.name}' is declared here with the constraints "
                    f"{' + '.join(param.constraints)}")
        else:
            text = f"'{param.name}' is declared here with no constraint"
        builder = builder.note_at(text, param.span)
    return builder


def providing_perks(perks: 'PerkTable', method: str) -> List[str]:
    """The perks that declare a method of this name, in declaration order."""
    return [name for name in perks.order
            if any(m.name == method for m in perks.by_name[name].methods)]


def explain_unpromised(builder: 'DiagnosticBuilder', ty: Optional['Type'],
                       perk: str, at_fault: Optional[List['Type']] = None
                       ) -> 'DiagnosticBuilder':
    """The note and the help of a refusal that a constraint `perk` would lift (#1070).

    Nothing is added when `ty` holds no opaque parameter that lacks the promise: a
    concrete type keeps the refusal as it is. `at_fault` is the answer of the predicate
    that refused, when it names the types at fault: `Clone` reads
    `holds_declared_resource`, which stops where a type holds nothing by value, so a
    parameter that only a function value names is not at fault.
    """
    held = opaque_parameters(ty) if at_fault is None else [
        t for t in at_fault if isinstance(t, TypeParameter) and t.is_opaque]
    lacking = [p for p in held if not p.promises(perk)]
    if not lacking:
        return builder
    name = lacking[0].name
    return note_opaque(builder, lacking[0]).help(
        f"add '{perk}' to the constraints of '{name}': '@({name}: {perk})'")


def explain_no_arithmetic(builder: 'DiagnosticBuilder',
                          ty: Optional['Type']) -> 'DiagnosticBuilder':
    """The note and the help of arithmetic on a type parameter (#1070, R2)."""
    params = opaque_parameters(ty)
    if not params:
        return builder
    return note_opaque(builder, params[0]).help(
        "a type parameter has no arithmetic, and no constraint promises one; write the "
        "function for a numeric type")
