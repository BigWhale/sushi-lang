"""The auto-derived struct/enum builtins must be type-inferable (issue #239)."""
from __future__ import annotations


from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
from sushi_lang.semantics.typesys import BuiltinType, StructType

#: The derived family's two rows in the one family table (#751).
_DERIVED = ("derived_hash", "derived_clone")


def check_struct_enum_builtin_methods(receiver_type, method_name, validator):
    """The derived struct/enum family's claim, asked through the one table (#751).

    The claim used to be a checker function of its own. It is a row of
    `METHOD_TYPE_REGISTRY` now, which the validation half reads too; every case below
    asks the same question of the same predicate.
    """
    family = METHOD_TYPE_REGISTRY.claim(receiver_type, method_name, validator)
    if family is None or family.name not in _DERIVED:
        return None
    return family.infer(receiver_type, method_name, validator)








class _FakeValidator:
    """Minimal stand-in carrying only what the checker's guards consult.

    `derived_methods` is one compilation's auto-derived pair (#601), so a case that
    asserts a claim hands over the table its own analysis produced; the default empty
    one is what a compilation that declared nothing looks like.
    """

    def __init__(self, perk_impl_table=None, derived_methods=None):
        self.perk_impl_table = perk_impl_table or _EmptyPerkTable()
        self.derived_methods = derived_methods or DerivedMethodTable()


class _EmptyPerkTable:
    def get_method(self, target_type, method_name):  # noqa: ARG002
        return None






















def test_declines_a_type_with_no_builtins_at_all():
    assert check_struct_enum_builtin_methods(
        StructType(name="NeverAnalysed", fields=()), "hash", _FakeValidator()
    ) is None


def test_declines_a_non_struct_receiver():
    assert check_struct_enum_builtin_methods(
        BuiltinType.I32, "hash", _FakeValidator()
    ) is None
























