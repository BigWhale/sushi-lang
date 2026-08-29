"""The extension claims a library makes on types it does not declare.

One predicate, two consumers: the pipeline emits CW3003 from these records at
`--lib` build time, and the manifest generator writes them as the
`foreign_extensions` key that `--lib-info` prints. The predicate reads the
TARGET TYPE and never the perk (`docs/design/unit-namespaces.md` section 8):
a perk implementation makes no record, because the consumer's own
implementation is the sanctioned override.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from sushi_lang.semantics.units import Unit


@dataclass(frozen=True)
class ForeignExtensionClaim:
    """One extension method a library declares on a type it does not declare."""

    unit_name: str
    filename: str
    target: str
    method: str
    span: Optional[Any]


def _target_name(target_type: Any) -> Optional[str]:
    """The base name a target type answers the declared-set lookup with.

    A type with no name, an array target for one, can never be declared by a
    unit, so the lookup misses. The name is the INTERNAL one; the record's
    `target` field is the surface spelling, and `display_type` owns that.
    """
    return getattr(target_type, "name", None)


def foreign_extension_claims(units: List['Unit']) -> List[ForeignExtensionClaim]:
    """Every extension method the given units declare on a type none of them declares.

    The caller passes the library's OWN units: a sibling unit's type is the
    library's own, and a bundled stdlib unit is not the library's to speak for.
    Records keep declaration order, so the manifest and the diagnostics are
    deterministic.
    """
    from sushi_lang.semantics.generics.type_display import display_type

    declared: set[str] = set()
    for unit in units:
        if unit.ast is None:
            continue
        for struct_def in unit.ast.structs:
            declared.add(struct_def.name)
        for enum_def in unit.ast.enums:
            declared.add(enum_def.name)

    claims: List[ForeignExtensionClaim] = []
    for unit in units:
        if unit.ast is None:
            continue
        for ext in [*unit.ast.extensions, *unit.ast.generic_extensions]:
            if ext.target_type is None:
                continue
            name = _target_name(ext.target_type)
            if name is not None and name in declared:
                continue
            claims.append(ForeignExtensionClaim(
                unit_name=unit.name,
                filename=str(unit.file_path),
                target=display_type(ext.target_type),
                method=ext.name,
                span=ext.target_type_span,
            ))
    return claims
