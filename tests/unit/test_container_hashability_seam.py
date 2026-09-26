"""A container's hashability answers from what it HOLDS, and the map is refused (#628).

`hashability_of` used to walk a container's backing FIELDS, where `List@(T).data` and
`Own@(T).value` are raw pointers. A pointer was let through, so the container carried a
derived hash the backend could never emit: `Own@(i32).hash()` read CE0052, `List@(i32)`
read CE0042 and `HashMap@(i32, i32)` crashed with a raw AttributeError as CE0000.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.generics import hashing
from sushi_lang.semantics.typesys import BuiltinType, PointerType, StructType

SRC = """
use <collections/hashmap>

struct Point:
    i32 x
    i32 y

fn main() i32:
    let List@(i32) l = List.new()
    let List@(Point) lp = List.new()
    let Own@(i32) o = Own.alloc(5)
    let HashMap@(i32, i32) m = HashMap.new()
    return Result.Ok(0)
"""


@pytest.fixture
def containers(analyze_program):
    analysis = analyze_program(SRC)
    return analysis, analysis.analyzer.tables.structs.by_name


def test_nothing_is_let_through():
    """The hole is closed: no kind is read as hashable that the backend cannot hash."""
    assert set(hashing.LET_THROUGH_KINDS) == set()


def test_a_raw_pointer_refuses():
    """It has no Sushi spelling, so only a unit test can reach it."""
    can_hash, reason = hashing.hashability_of(PointerType(pointee_type=BuiltinType.I32))
    assert not can_hash
    assert reason


@pytest.mark.parametrize("name", ["List<i32>", "List<Point>", "Own<i32>"])
def test_a_container_hashes_what_it_holds(containers, name):
    _analysis, by_name = containers
    can_hash, reason = hashing.hashability_of(by_name[name])
    assert can_hash, f"{name} was refused: {reason}"


def test_a_hash_map_is_refused(containers):
    _analysis, by_name = containers
    can_hash, reason = hashing.hashability_of(by_name["HashMap<i32, i32>"])
    assert not can_hash
    assert reason


@pytest.mark.parametrize("name", ["List<i32>", "Own<i32>"])
def test_a_container_carries_a_registered_hash(containers, name):
    analysis, by_name = containers
    derived = analysis.analyzer.tables.derived_methods
    method = derived.get_method(by_name[name], "hash")
    assert method is not None, f"{name} carries no derived hash"
    assert method.return_type == BuiltinType.U64


def test_a_hash_map_carries_no_registered_hash(containers):
    analysis, by_name = containers
    derived = analysis.analyzer.tables.derived_methods
    assert derived.get_method(by_name["HashMap<i32, i32>"], "hash") is None


class _NoPerks:
    def get_method(self, target_type, method_name):  # noqa: ARG002
        return None


class _Validator:
    """Only what the `hash` checker consults: the perk table and the derived pair."""

    def __init__(self, derived_methods):
        self.perk_impl_table = _NoPerks()
        self.derived_methods = derived_methods


def _infer(containers, name):
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
    analysis, by_name = containers
    validator = _Validator(analysis.analyzer.tables.derived_methods)
    return METHOD_TYPE_REGISTRY.infer_method_type(by_name[name], "hash", validator)


@pytest.mark.parametrize("name", ["List<i32>", "Own<i32>"])
def test_the_call_site_types_a_container_hash(containers, name):
    """A call with NO inferred type is never compared against its declared one.

    While the container guard declined `hash`, the container method inferrers answered
    None for it, and `let i32 h = l.hash()` was accepted in silence.
    """
    assert _infer(containers, name) == BuiltinType.U64


def test_a_hash_map_hash_types_as_nothing(containers):
    """No derived hash, so no inferred type, so the call site says undefined."""
    assert _infer(containers, "HashMap<i32, i32>") is None


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
