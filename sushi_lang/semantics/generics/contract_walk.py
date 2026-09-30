"""The one walk over a type that a derived contract reads: its path, its memo, its override.

A derived method answers from what a type HOLDS. The hash walk (`hashing.py`) and the
walks of the three contracts `Eq`, `Ord` and `Display` (`contracts.py`) share the
mechanics here: the DFS path that detects recursion, the memo that makes the walk
linear, and the predicate that says a held type carries an implementation of the
contract and is terminal.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from sushi_lang.semantics.typesys import Type


#: "Does this type carry an implementation of the contract?" -- the override, read at
#: every held position of the walk.
Override = Callable[[Type], bool]


def perk_override_of(perk_name: str, perk_impls: Any,
                     generic_perk_impls: Any = None) -> Override:
    """The override predicate of one perk, over the perk-implementation tables (#891).

    An explicit implementation answers, and so does a generic-target template whose
    copy for this instantiation is not cut yet. The tables fill in place, so the
    predicate reads them when it is asked and not when it is built.
    """
    from sushi_lang.semantics.passes.collect.perks import _get_type_name

    def overridden(ty: Type) -> bool:
        type_name = _get_type_name(ty)
        if type_name is not None and perk_impls.implements(type_name, perk_name):
            return True
        base = getattr(ty, "generic_base", None)
        args = getattr(ty, "generic_args", None)
        if not generic_perk_impls or not base or not args:
            return False
        return any(template.impl.perk_name == perk_name
                   and len(template.type_params) == len(args)
                   for template in generic_perk_impls.templates(base))

    return overridden


@dataclass
class Walk:
    """One walk: where it is, and what it has already decided.

    `on_path` is the DFS path and nothing else. It is what detects RECURSION, and it
    has to be the path: a type reached twice through two different fields is not
    recursive, and the copied-per-field set the walk used to carry could not tell the
    two apart without multiplying the work by the fan-out at every link (#598).

    `decided` is the memo that makes the walk linear. It holds only an answer no cycle
    took part in -- a verdict reached through a cycle is true of the PATH that found it
    and of nothing else, so remembering one would answer for a type that a different
    reader walks differently. `cycles` counts the hits, and an unchanged count over a
    subtree is what says its answer is the type's own.

    `resolve` maps a WRITTEN name to its table entry, for a reader that runs before the
    resolve pass: the constraint check in monomorphize asks about a struct whose
    fields still spell their types (#696).

    `overridden` is the contract's override (#891): a held type that implements it is
    terminal, and its fields are not read.

    `cycle_answer` is what a recursive type answers. A derived hash is emitted inline and
    cannot reach itself, so the hash walk refuses one; the contract walks emit one
    function per type, and a call of the function it is in is legal.
    """

    path: List[str] = field(default_factory=list)
    on_path: Set[str] = field(default_factory=set)
    decided: Dict[Tuple[str, str], Tuple[bool, str]] = field(default_factory=dict)
    cycles: int = 0
    resolve: Optional[Callable[[Type], Type]] = None
    overridden: Optional[Override] = None
    cycle_answer: bool = False


@contextmanager
def walking(walk: Walk, name: str) -> Iterator[None]:
    """`name` is on the path for the length of this block, and off it after."""
    walk.on_path.add(name)
    walk.path.append(name)
    try:
        yield
    finally:
        walk.path.pop()
        walk.on_path.discard(name)


def decide(walk: Walk, kind: str, name: str,
           verdict: Callable[[], tuple[bool, str]]) -> tuple[bool, str]:
    """One NAMED type's answer, walked at most once per walk.

    Type identity is nominal, so one name is one type and one answer serves every field
    that reaches it.
    """
    key = (kind, name)
    remembered = walk.decided.get(key)
    if remembered is not None:
        return remembered

    if name in walk.on_path:
        walk.cycles += 1
        return walk.cycle_answer, f"recursive {kind} type: {' -> '.join(walk.path + [name])}"

    hits = walk.cycles
    with walking(walk, name):
        answer = verdict()
    if walk.cycles == hits:
        walk.decided[key] = answer
    return answer
