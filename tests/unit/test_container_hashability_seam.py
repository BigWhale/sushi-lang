"""A container's hashability answers from what it HOLDS, and the map is refused (#628).

`hashability_of` used to walk a container's backing FIELDS, where `List@(T).data` and
`Own@(T).value` are raw pointers. A pointer was let through, so the container carried a
derived hash the backend could never emit: `Own@(i32).hash()` read CE0052, `List@(i32)`
read CE0042 and `HashMap@(i32, i32)` crashed with a raw AttributeError as CE0000.
"""
from __future__ import annotations


from sushi_lang.semantics.generics import hashing
from sushi_lang.semantics.typesys import BuiltinType, PointerType, StructType





def test_nothing_is_let_through():
    """The hole is closed: no kind is read as hashable that the backend cannot hash."""
    assert set(hashing.LET_THROUGH_KINDS) == set()


def test_a_raw_pointer_refuses():
    """It has no Sushi spelling, so only a unit test can reach it."""
    can_hash, reason = hashing.hashability_of(PointerType(pointee_type=BuiltinType.I32))
    assert not can_hash
    assert reason




















def test_a_container_element_that_cannot_hash_refuses_the_container():
    """The container inherits its element's refusal -- one walk, not a special case."""
    from sushi_lang.semantics.typesys import FunctionType
    unhashable = FunctionType(param_types=(BuiltinType.I32,),
                              ok_type=BuiltinType.I32, err_type=BuiltinType.I32)
    a_list = StructType(name="List<fn>", fields=(), generic_base="List",
                        generic_args=(unhashable,))
    can_hash, reason = hashing.hashability_of(a_list)
    assert not can_hash
    assert reason


def test_a_container_of_an_overridden_element_registers_its_held_array():
    """`List@(F[])` hashes each `F[]`, and no field or payload names that array.

    The backend registers a missing array hash on demand and has no override predicate,
    so an array of an overridden F read CE0052 there. The container registers it (#891).
    """
    from sushi_lang.semantics.derived_methods import DerivedMethodTable
    from sushi_lang.semantics.typesys import DynamicArrayType, FunctionType
    fn_type = FunctionType(param_types=(BuiltinType.I32,),
                           ok_type=BuiltinType.I32, err_type=BuiltinType.I32)
    f = StructType(name="F", fields=(("f", fn_type), ("n", BuiltinType.I32)))
    cells = DynamicArrayType(base_type=f)
    a_list = StructType(name="List<F[]>", fields=(), generic_base="List",
                        generic_args=(cells,))
    derived = DerivedMethodTable()
    derived.hash_override = lambda ty: ty == f
    assert hashing.register_hash_if_hashable(a_list, derived)
    assert derived.get_method(a_list, "hash") is not None
    assert derived.get_method(cells, "hash") is not None
