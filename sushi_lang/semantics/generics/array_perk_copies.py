"""The one seam that cuts the copy of an array-template perk implementation (#699).

`extend T[] with P` covers every dynamic array. A struct template is cut once per
instantiation the program names (`for_each_instantiation`), but an array type has no
instantiation set: `i32[]` is written, inferred and nested anywhere. So the copy for one
array type is cut when a lookup of the perk-implementation table misses for it
(`PerkImplementationTable.on_array_miss`), and every reader of the table -- a method
call, a constraint, a contract walk, the hash override -- agrees with no edit.

A cut copy is registered at once, so the lookup that asked answers. Its BODY waits on
`pending` until the analyzer places it in the home unit of its template and checks it,
after the per-unit loop, so no body is checked twice.
"""
from __future__ import annotations

from typing import Any, List, Optional, Set, Tuple

from sushi_lang.semantics.typesys import DynamicArrayType


class ArrayPerkCopies:
    """The `on_array_miss` hook: cut the array templates that answer one question."""

    def __init__(self, tables: Any, substitutor: Any) -> None:
        self.tables = tables
        self.substitutor = substitutor
        # (template, copy) pairs whose body is not placed and checked yet.
        self.pending: List[Tuple[Any, Any]] = []
        # The (array type, perk) pairs already asked about: a second miss is a real miss.
        self._asked: Set[Tuple[str, str]] = set()

    def __call__(self, array_type: DynamicArrayType, *, perk: Optional[str] = None,
                 method: Optional[str] = None) -> bool:
        """Cut the copy of each template of `perk`, or of each that gives `method`.

        Only a template that answers the question is cut: a method call on a `u8[]` in
        a stdlib body asks about `len`, and no template's body is checked for a type no
        question needed.
        """
        from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY
        from sushi_lang.semantics.generics.extensions import monomorphize_perk_impl
        from sushi_lang.semantics.passes.collect.perks import _get_type_name

        type_name = _get_type_name(array_type)
        if type_name is None:
            return False

        perk_impls = self.tables.perk_impls
        cut = False
        for template in self.tables.generic_perk_impls.templates(ARRAY_BASE_KEY):
            perk_name = template.impl.perk_name
            if perk is not None and perk_name != perk:
                continue
            if method is not None and all(m.name != method for m in template.impl.methods):
                continue
            if (type_name, perk_name) in self._asked:
                continue
            self._asked.add((type_name, perk_name))
            if perk_impls.implements(type_name, perk_name):
                continue
            copy = monomorphize_perk_impl(template, array_type, (array_type.base_type,),
                                          substitutor=self.substitutor)
            if perk_impls.register(copy, type_name, unit_name=template.unit_name):
                self.pending.append((template, copy))
                cut = True
        return cut

    def take_pending(self) -> List[Tuple[Any, Any]]:
        """Hand over the copies cut since the last call."""
        pending, self.pending = self.pending, []
        return pending
