"""What each type predicate answers for an opaque type parameter (#1070).

An opaque `T` is the type parameter of one template, in the body as the template check
reads it. It stands for some type that satisfies its constraints, and for nothing more,
so every predicate answers from the constraints alone. These read types and tables; they
start no compiler.
"""
from __future__ import annotations

from sushi_lang.semantics.generics.types import TemplateId, TypeParameter
from sushi_lang.semantics.tables import SymbolTables
from sushi_lang.semantics.type_predicates import is_abstract_type
from sushi_lang.semantics.typesys import (
    BuiltinType, DynamicArrayType, ReceiverType, holds_declared_resource, owns_resource)


def _opaque(*constraints: str, template: str = "f") -> TypeParameter:
    return TypeParameter("T", owner=TemplateId("main", template), constraints=constraints)


def test_identity_is_the_name_and_the_template():
    assert _opaque() == _opaque("Eq")
    assert _opaque(template="f") != _opaque(template="g")
    assert _opaque() != TypeParameter("T")
    assert str(_opaque()) == "T#main.f"


def test_an_opaque_parameter_moves_and_a_bare_one_does_not():
    assert owns_resource(_opaque("Clone"), set())
    assert not owns_resource(TypeParameter("T"), set())


def test_clone_is_the_promise_of_no_resource():
    assert holds_declared_resource(_opaque(), set())
    assert holds_declared_resource(DynamicArrayType(_opaque("Eq")), set())
    assert not holds_declared_resource(_opaque("Clone"), set())


def test_an_opaque_parameter_is_bound_only_where_the_table_admits_it():
    assert is_abstract_type(_opaque(), {}, {})
    assert not is_abstract_type(_opaque(), {}, {}, opaque_bound=True)
    assert is_abstract_type(TypeParameter("T"), {}, {}, opaque_bound=True)


def test_the_perk_table_answers_the_promise():
    tables = SymbolTables()
    from sushi_lang.semantics.passes.collect.perks import PerkCollector
    PerkCollector(None, tables.perks, tables.perk_impls,
                  is_declared_type=lambda name: False).register_predefined_perks()
    impls = tables.perk_impls
    assert impls.implements_type(_opaque("Hashable"), "Hashable")
    assert not impls.implements_type(_opaque("Eq"), "Hashable")
    hash_method = impls.get_method(_opaque("Hashable"), "hash")
    assert hash_method is not None and hash_method.ret == BuiltinType.U64
    clone = impls.get_method(_opaque("Clone"), "clone")
    assert clone is not None and clone.ret == _opaque("Clone")
    assert not isinstance(clone.ret, ReceiverType)
    assert impls.get_method(_opaque("Eq"), "hash") is None


def _element(element: int, template: str = "f") -> TypeParameter:
    return TypeParameter("Ts", owner=TemplateId("u", template), constraints=("Eq",),
                         element=element, binder="a")


def test_two_elements_of_one_pack_are_two_types():
    assert _element(0) != _element(1)
    assert _element(0) == _element(0)
    assert hash(_element(0)) == hash(_element(0))
    assert _element(0) != TypeParameter("Ts", owner=TemplateId("u", "f"))
    assert TypeParameter("T", owner=TemplateId("u", "f"), element=None) == \
        TypeParameter("T", owner=TemplateId("u", "f"))


def test_an_element_spells_its_owner_and_its_number():
    assert str(_element(0)) == "Ts#u.f#0"
    assert _element(0).written() == "...Ts"
    assert TypeParameter("T", owner=TemplateId("u", "f")).written() == "T"


def test_an_element_renders_as_the_pack_name():
    from sushi_lang.semantics.generics.type_display import display_type, display_type_name
    assert display_type_name("List<Ts#u.f#0>") == "List@(Ts)"
    assert display_type(_element(0)) == "Ts"


def test_an_element_is_an_opaque_parameter_to_every_predicate():
    assert _element(0).is_opaque
    assert _element(0).promises("Eq")
    assert not _element(0).is_pack
    assert owns_resource(_element(0), set())
    assert holds_declared_resource(_element(0), set())
