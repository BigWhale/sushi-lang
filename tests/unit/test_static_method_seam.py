"""One namespace behind a type's dot, and one seam that answers for it (#542).

Three gates:
  - the DECLARATION carries the marker and no receiver, for every target kind,
  - the shared predicates answer for every type kind, the built-ins included,
  - every refusal fires as ONE diagnostic, and no cell reports anything else.
"""
from __future__ import annotations


from sushi_lang.semantics.statics import (
    BUILTIN_STATICS,
    BUILTIN_TYPE_NAMES,
    builtin_type_named,
    is_builtin_static,
    names_a_type,
)
from sushi_lang.semantics.typesys import BuiltinType


# 1. The declaration











# 2. The shared type-name predicate

def test_every_primitive_but_blank_is_a_type_name():
    assert BUILTIN_TYPE_NAMES == {ty.value for ty in BuiltinType} - {"~"}
    assert builtin_type_named("f64") is BuiltinType.F64
    assert builtin_type_named("Box") is None


def test_a_declared_name_of_any_kind_is_a_type_name():
    assert names_a_type("i32")
    assert names_a_type("Box", structs={"Box"})
    assert names_a_type("Sign", enums={"Sign"})
    assert names_a_type("Cage", generic_structs={"Cage"})
    assert names_a_type("Maybe", generic_enums={"Maybe"})
    assert not names_a_type("Box")


def test_the_builtin_statics_are_named_in_one_table():
    """Ruling R3: one table, so the general path DEFERS rather than refusing."""
    assert is_builtin_static("List", "new")
    assert is_builtin_static("List", "with_capacity")
    assert is_builtin_static("HashMap", "new")
    assert is_builtin_static("Own", "alloc")
    assert is_builtin_static("f64", "from_bits")
    assert not is_builtin_static("List", "push")
    assert not is_builtin_static("Box", "new")
    assert not is_builtin_static(None, "new")
    assert set(BUILTIN_STATICS) == {"List", "HashMap", "Own", "f64", "f32"}


# 3. The refusals, through the real compiler












# No parameter names T and the position declares no type: neither source reaches it
# (#573). `Cage.holding(9)` was this shape until the argument became the first source.












