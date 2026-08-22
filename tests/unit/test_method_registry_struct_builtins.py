"""The auto-derived struct/enum builtins must be type-inferable (issue #239)."""
from __future__ import annotations

import pytest

from sushi_lang.semantics.passes.types.method_registry import (
    check_struct_enum_builtin_methods,
)
from sushi_lang.semantics.typesys import BuiltinType, EnumType, StructType


STRUCT_SRC = """
struct P:
    i32 x
    i32 y

fn main() i32:
    let P p = P(1, 2)
    println(p.hash())
    return Result.Ok(0)
"""

ENUM_SRC = """
enum Colour:
    Red
    Green

fn main() i32:
    let Colour c = Colour.Red
    println(c.hash())
    return Result.Ok(0)
"""

PERK_SRC = """
perk Hashable:
    fn hash() u64

struct Point:
    i32 x
    i32 y

extend Point with Hashable:
    fn hash() u64:
        return self.x as u64

fn main() i32:
    let Point p = Point(10, 20)
    println(p.hash())
    return Result.Ok(0)
"""

CONTAINER_SRC = """
fn main() i32:
    let List@(i32) xs = List.new()
    xs.push(1)
    let Own@(i32) o = Own.alloc(5)
    println(xs.len())
    println(o.get())
    o.destroy()
    xs.destroy()
    return Result.Ok(0)
"""


class _FakeValidator:
    """Minimal stand-in carrying only what the checker's guards consult."""

    def __init__(self, perk_impl_table=None):
        self.perk_impl_table = perk_impl_table or _EmptyPerkTable()


class _EmptyPerkTable:
    def get_method(self, target_type, method_name):  # noqa: ARG002
        return None


class _PerkTableWith:
    def __init__(self, names):
        self._names = set(names)

    def get_method(self, target_type, method_name):  # noqa: ARG002
        return object() if method_name in self._names else None


def _infer(receiver_type, method_name, validator=None):
    """Run the checker and, if it claims the call, its inferrer."""
    inferrer = check_struct_enum_builtin_methods(
        receiver_type, method_name, validator or _FakeValidator()
    )
    if inferrer is None:
        return None
    return inferrer.infer_return_type()


@pytest.fixture
def struct_p(analyze):
    """The StructType `P` after a full analysis (so the derive pass has run)."""
    analyze(STRUCT_SRC)
    return StructType(name="P", fields=())


@pytest.fixture
def enum_colour(analyze):
    analyze(ENUM_SRC)
    return EnumType(name="Colour", variants=())


def test_hash_infers_u64(struct_p):
    assert _infer(struct_p, "hash") is BuiltinType.U64


def test_clone_infers_the_receiver_type(struct_p):
    assert _infer(struct_p, "clone") == struct_p


def test_enum_hash_infers_u64(enum_colour):
    assert _infer(enum_colour, "hash") is BuiltinType.U64


def test_enum_clone_infers_the_receiver_type(enum_colour):
    assert _infer(enum_colour, "clone") == enum_colour


def test_declines_an_unregistered_method(struct_p):
    """Only a registered (type, name) may be claimed -- otherwise the extension table and the
    CE2008 unknown-method path would be shadowed.
    """
    assert check_struct_enum_builtin_methods(
        struct_p, "not_a_builtin", _FakeValidator()
    ) is None


def test_declines_a_type_with_no_builtins_at_all():
    assert check_struct_enum_builtin_methods(
        StructType(name="NeverAnalysed", fields=()), "hash", _FakeValidator()
    ) is None


def test_declines_a_non_struct_receiver():
    assert check_struct_enum_builtin_methods(
        BuiltinType.I32, "hash", _FakeValidator()
    ) is None


@pytest.mark.parametrize("name", ["Own<i32>", "List<i32>", "HashMap<i32, i32>"])
def test_declines_container_receivers(analyze, name):
    """Own/List/HashMap are named StructTypes but keep their own method paths."""
    analyze(CONTAINER_SRC)
    assert check_struct_enum_builtin_methods(
        StructType(name=name, fields=()), "hash", _FakeValidator()
    ) is None
    assert check_struct_enum_builtin_methods(
        StructType(name=name, fields=()), "clone", _FakeValidator()
    ) is None


def test_list_monomorph_really_does_carry_a_registered_hash(analyze):
    """The premise behind the container guard, pinned so it cannot silently change."""
    from sushi_lang.sushi_stdlib.src.common import get_builtin_method

    analyze(CONTAINER_SRC)
    assert get_builtin_method(StructType(name="List<i32>", fields=()), "hash") is not None


def test_declines_when_a_perk_impl_of_that_name_exists(analyze):
    """Perk methods win at codegen (dispatcher step 12, before step 13), so inference must let them
    win too -- otherwise the typecheck pass would type the call as the auto-derived u64 while the backend
    emitted the perk body.
    """
    analyze(PERK_SRC)
    point = StructType(name="Point", fields=())
    validator = _FakeValidator(_PerkTableWith({"hash"}))
    assert check_struct_enum_builtin_methods(point, "hash", validator) is None
    # A name the perk does NOT implement is still claimed.
    assert _infer(point, "clone", validator) == point


def test_the_inferrer_emits_no_diagnostics(analyze):
    """infer_expression_type runs many times per node; a diagnostic here duplicates."""
    reporter = analyze("""
struct P:
    i32 x

fn main() i32:
    let P p = P(1)
    let u64 h = p.hash()
    let P q = p.clone()
    println(h)
    println(q.x)
    return Result.Ok(0)
""")
    assert [item.code for item in reporter.items] == []


def test_a_wrong_annotation_is_now_a_normal_ce2002(analyze):
    """The end-to-end point of #239: the CE0017 ICE becomes a user-level diagnostic."""
    reporter = analyze("""
struct P:
    i32 x

fn main() i32:
    let P p = P(1)
    let string h = p.hash()
    println(h)
    return Result.Ok(0)
""")
    assert "CE2002" in [item.code for item in reporter.items]


def test_an_extension_shadowing_an_auto_derived_builtin_is_ce2097(analyze):
    """The auto-derived method wins every dispatch, so the extension can never run."""
    reporter = analyze("""
struct P:
    i32 x

extend P hash() u64:
    return self.x as u64

fn main() i32:
    let P p = P(1)
    println(p.hash())
    return Result.Ok(0)
""")
    assert "CE2097" in [item.code for item in reporter.items]


def test_ce2097_covers_a_primitive_receiver(analyze):
    """CE2097 reaches every shadowable built-in, not just the struct/enum pair."""
    reporter = analyze("""
extend i32 hash() u64:
    return 1 as u64

fn main() i32:
    let i32 n = 10
    println(n.hash())
    return Result.Ok(0)
""")
    assert "CE2097" in [item.code for item in reporter.items]


def test_ce2097_covers_a_monomorphized_generic_extension(analyze):
    """The ordering hole: these enter the extension table after the derive pass, so the check has to run
    after the merge loop, not immediately after 1.8.
    """
    reporter = analyze("""
struct Box@(T):
    T value

extend Box@(i32) hash() u64:
    return 1 as u64

fn main() i32:
    let Box@(i32) b = Box(7)
    println(b.hash())
    return Result.Ok(0)
""")
    assert "CE2097" in [item.code for item in reporter.items]


def test_ce2097_leaves_a_non_colliding_extension_alone(analyze):
    """The corpus shape: a name no built-in family carries stays perfectly legal."""
    reporter = analyze("""
struct Point:
    i32 x

extend Point describe() i32:
    return self.x

extend i32 squared() i32:
    return self * self

fn main() i32:
    let Point p = Point(3)
    println(p.describe())
    println(p.x.squared())
    return Result.Ok(0)
""")
    assert "CE2097" not in [item.code for item in reporter.items]


def test_a_perk_impl_of_hash_is_not_ce2097(analyze):
    """`extend T with Hashable` is a perk implementation, held in a different table entirely
    (PerkImplementationTable, never ExtensionTable). Perk impls legitimately override the
    auto-derived method and must stay legal.
    """
    reporter = analyze(PERK_SRC)
    assert "CE2097" not in [item.code for item in reporter.items]
