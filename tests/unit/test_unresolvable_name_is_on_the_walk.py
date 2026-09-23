"""`contains_unresolvable_unknown_type` asks its question over the one type walk (#724).

It used to recurse by hand, with no arm for a reference, a pointer, an iterator or a
pack, so a name that reaches no declaration was invisible behind any of the four.
"""
from __future__ import annotations

import inspect

from sushi_lang.semantics import type_resolution
from sushi_lang.semantics.type_resolution import contains_unresolvable_unknown_type
from sushi_lang.semantics.typesys import (
    ArrayType, BorrowMode, DynamicArrayType, FunctionType, IteratorType, PointerType,
    ReferenceType, StructType, UnknownType, BuiltinType,
)
from sushi_lang.semantics.generics.types import GenericTypeRef, TypePack


def _tables() -> tuple[dict, dict]:
    node = StructType(name="Node", fields=(("next", UnknownType("Node")),))
    return {"Node": node}, {}


def _holders():
    return {
        "ArrayType": lambda t: ArrayType(base_type=t, size=2),
        "DynamicArrayType": lambda t: DynamicArrayType(base_type=t),
        "ReferenceType": lambda t: ReferenceType(t, BorrowMode.PEEK),
        "PointerType": lambda t: PointerType(pointee_type=t),
        "IteratorType": lambda t: IteratorType(element_type=t),
        "TypePack": lambda t: TypePack(types=(t,)),
        "GenericTypeRef": lambda t: GenericTypeRef(base_name="Box", type_args=(t,)),
        "FunctionType": lambda t: FunctionType(
            param_types=(t,), ok_type=BuiltinType.I32, err_type=BuiltinType.I32),
    }


def test_an_unknown_name_is_found_behind_every_kind():
    structs, enums = _tables()
    missed = [kind for kind, build in _holders().items()
              if not contains_unresolvable_unknown_type(build(UnknownType("Nope")),
                                                        structs, enums)]
    assert not missed, f"a name that reaches no declaration was missed behind: {missed}"


def test_a_known_name_is_resolved_behind_every_kind():
    structs, enums = _tables()
    flagged = [kind for kind, build in _holders().items()
               if contains_unresolvable_unknown_type(build(UnknownType("Node")),
                                                     structs, enums)]
    assert not flagged, f"a declared name was called unresolvable behind: {flagged}"


def test_the_predicate_reads_the_shared_walk():
    source = inspect.getsource(type_resolution.contains_unresolvable_unknown_type)
    body = source.split('"""', 2)[-1]
    assert "walk_named_types" in body
    assert "contains_unresolvable_unknown_type(" not in body, (
        "the predicate recurses by hand again; the shared walk owns the recursion"
    )
