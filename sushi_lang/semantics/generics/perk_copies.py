"""The one seam that cuts the copy of a perk implementation on a miss (#699).

`extend T[] with P` covers every dynamic array. An array type has no instantiation set:
`i32[]` is written, inferred and nested anywhere. `extend Box@(T) with P` covers every
instance of `Box`, and the instance that only the typecheck pass names (the return of a
copy that a call cut) comes after the cut per instantiation (`for_each_instantiation`). So
the copy for one type is cut when a lookup of the perk-implementation table misses for it
(`PerkImplementationTable.on_perk_miss`), and every reader of the table -- a method call, a
constraint, a contract walk, the hash override -- agrees with no edit.

A cut copy is registered at once, so the lookup that asked answers. Its BODY waits on
`pending` until the analyzer places it in the home unit of its template and checks it,
after the per-unit loop, so no body is checked twice.
"""
from __future__ import annotations

from typing import Any, List, Optional, Set, Tuple

from sushi_lang.semantics.typesys import DynamicArrayType, Type


class PerkCopies:
    """The `on_perk_miss` hook: cut the templates that answer one question."""

    def __init__(self, tables: Any, substitutor: Any) -> None:
        self.tables = tables
        self.substitutor = substitutor
        # (template, copy) pairs whose body is not placed and checked yet.
        self.pending: List[Tuple[Any, Any]] = []
        # The (type, perk) pairs already asked about: a second miss is a real miss.
        self._asked: Set[Tuple[str, str]] = set()

    def __call__(self, target: Type, *, perk: Optional[str] = None,
                 method: Optional[str] = None) -> bool:
        """Cut the copy of each template of `perk`, or of each that gives `method`.

        Only a template that answers the question is cut: a method call on a `u8[]` in
        a stdlib body asks about `len`, and no template's body is checked for a type no
        question needed.
        """
        from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY
        from sushi_lang.semantics.generics.extensions import (
            bounds_hold_for, monomorphize_perk_impl)
        from sushi_lang.semantics.passes.collect.perks import _get_type_name

        type_name = _get_type_name(target)
        if type_name is None:
            return False
        if isinstance(target, DynamicArrayType):
            base, type_args = ARRAY_BASE_KEY, (target.base_type,)
        else:
            base = getattr(target, "generic_base", None)
            type_args = tuple(getattr(target, "generic_args", None) or ())
        if base is None:
            return False

        perk_impls = self.tables.perk_impls
        cut = False
        for template in self.tables.generic_perk_impls.templates(base):
            perk_name = template.impl.perk_name
            if perk is not None and perk_name != perk:
                continue
            if method is not None and all(m.name != method for m in template.impl.methods):
                continue
            if (type_name, perk_name) in self._asked:
                continue
            self._asked.add((type_name, perk_name))
            if (perk_impls.implements(type_name, perk_name)
                    or not bounds_hold_for(template, type_args, self.tables)):
                continue
            copy = monomorphize_perk_impl(template, target, type_args,
                                          substitutor=self.substitutor)
            if perk_impls.register(copy, type_name, unit_name=template.unit_name):
                self.pending.append((template, copy))
                cut = True
        return cut

    def take_pending(self) -> List[Tuple[Any, Any]]:
        """Hand over the copies cut since the last call."""
        pending, self.pending = self.pending, []
        return pending
