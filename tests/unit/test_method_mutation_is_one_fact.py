""""This method changes its receiver" is ONE fact with ONE source (#791 row 5).

The array table in the typecheck pass kept its own `mutates` flag to refuse an in-place
method on a constant (CE2096), and the borrow pass kept `METHOD_EFFECTS` for the same
fact. The two disagreed on `push`, `pop`, `destroy` and `free`, and the disagreement WAS
reachable: `clear()` on a constant fixed array read CE2096 while `push(4)` read CE2023.
The table now lives in `semantics/method_effects.py`, outside both passes, and the array
check reads it.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sushi_lang.semantics.method_effects import METHOD_EFFECTS, effect_of
from sushi_lang.semantics.passes.types import arrays

ARRAYS = Path(arrays.__file__)


def test_the_array_row_carries_no_flag_of_its_own():
    assert "mutates" not in {f.name for f in dataclasses.fields(arrays.ArraySpec)}


def test_the_typecheck_pass_does_not_import_the_borrow_pass():
    assert "passes.borrow" not in ARRAYS.read_text()


def test_the_borrow_pass_reads_the_same_table():
    from sushi_lang.semantics.passes.borrow import methods

    assert methods.METHOD_EFFECTS is METHOD_EFFECTS


def test_every_array_method_the_table_marks_is_measured():
    """The control: the array rows the table marks as mutating are not an empty set."""
    marked = {name for name in arrays._ARRAY_METHODS if effect_of(name).mutates}
    assert {"clear", "push", "pop", "fill", "reverse", "extend",
            "extend_back"} <= marked


#: The families with no receiver rule yet, at the day of the pin (ruling R-V). A family may
#: leave the set and the pin together; none may join.
PINNED_NO_RECEIVER_RULE = frozenset({
    "iterator", "foreign_ptr", "array_hash", "derived_hash", "derived_clone", "contract",
    "function",
})


def _receiver_sets():
    from sushi_lang.semantics import method_effects as me
    return {"only reads": me.RECEIVER_READ_FAMILIES, "effect": me.EFFECT_FAMILIES,
            "consumes": me.CONSUMING_RECEIVER_FAMILIES,
            "no rule yet": me.NO_RECEIVER_RULE_FAMILIES}


def test_each_registry_family_is_in_exactly_one_receiver_set():
    """The receiver sets are a TOTAL partition of the families of `METHOD_TYPE_REGISTRY`
    (ruling R-V, docs/design/ir.md 8.3)."""
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY

    registry = [family.name for family in METHOD_TYPE_REGISTRY.families]
    sets = _receiver_sets()
    for name in registry:
        holders = [label for label, families in sets.items() if name in families]
        assert len(holders) == 1, f"the family {name!r} is in {holders}"
    unknown = frozenset().union(*sets.values()) - set(registry)
    assert not unknown, f"no family of METHOD_TYPE_REGISTRY is named {sorted(unknown)}"
    assert all(sets.values()), "a receiver set is empty"


def test_the_no_rule_set_only_shrinks():
    from sushi_lang.semantics.method_effects import NO_RECEIVER_RULE_FAMILIES

    grown = sorted(NO_RECEIVER_RULE_FAMILIES - PINNED_NO_RECEIVER_RULE)
    assert not grown, f"a family may leave the no-rule set and never join it: {grown}"
    stale = sorted(PINNED_NO_RECEIVER_RULE - NO_RECEIVER_RULE_FAMILIES)
    assert not stale, f"delete the pin row together with the family: {stale}"


def test_the_borrow_pass_and_slir_lowering_read_the_one_receiver_convention():
    from sushi_lang.semantics import method_effects
    from sushi_lang.semantics.passes.borrow import calls
    from sushi_lang.semantics.slir import lower_calls

    assert calls.receiver_convention is method_effects.receiver_convention
    assert lower_calls.receiver_convention is method_effects.receiver_convention


def test_a_read_family_gives_a_borrow_and_a_family_with_no_answer_gives_none():
    from sushi_lang.semantics import method_effects as me
    from sushi_lang.semantics.param_modes import Convention
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY

    for name in me.RECEIVER_READ_FAMILIES:
        callee = METHOD_TYPE_REGISTRY.family(name).callee("probe")
        assert me.receiver_convention(callee) is Convention.GUARANTEED
    for name in (me.EFFECT_FAMILIES - me.RULED_EFFECT_FAMILIES) | me.NO_RECEIVER_RULE_FAMILIES:
        callee = METHOD_TYPE_REGISTRY.family(name).callee("probe")
        assert me.receiver_convention(callee) is None, name


def test_a_ruled_effect_family_writes_a_receiver_that_its_row_mutates():
    """The array family (group A6): a method whose row `mutates` takes its receiver
    `Inout`, and every other method of the family reads it (docs/design/ir.md 8.3)."""
    from sushi_lang.semantics import method_effects as me
    from sushi_lang.semantics.param_modes import Convention
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY

    assert me.RULED_EFFECT_FAMILIES == {"array", "list"}
    assert me.RULED_EFFECT_FAMILIES <= me.EFFECT_FAMILIES
    family = METHOD_TYPE_REGISTRY.family("array")
    answers = {name: me.receiver_convention(family.callee(name))
               for name in arrays._ARRAY_METHODS}
    written = {name for name, answer in answers.items() if answer is Convention.INOUT}
    assert written == {name for name in arrays._ARRAY_METHODS if effect_of(name).mutates}
    assert {"push", "insert", "pop", "fill", "swap", "extend"} <= written
    assert all(answer is Convention.GUARANTEED for name, answer in answers.items()
               if name not in written)


def test_the_list_family_writes_a_receiver_that_its_row_mutates():
    """The `List@(T)` family (group B2) has the rule of the array family: `push`,
    `insert`, `pop`, `clear`, ... take the receiver `Inout`, and each other method reads it.
    The slot of `push` and of `insert` is `Owned`, and each other argument is `Guaranteed`,
    the index of `insert` too."""
    from sushi_lang.semantics import method_effects as me
    from sushi_lang.semantics.generics.list import LIST_METHOD_ARITY
    from sushi_lang.semantics.param_modes import Convention
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY

    family = METHOD_TYPE_REGISTRY.family(me.LIST_FAMILY)
    answers = {name: me.receiver_convention(family.callee(name)) for name in LIST_METHOD_ARITY}
    written = {name for name, answer in answers.items() if answer is Convention.INOUT}
    assert written == {name for name in LIST_METHOD_ARITY if effect_of(name).mutates}
    assert {"push", "insert", "pop", "remove", "clear", "reserve", "free"} <= written
    assert all(answer is Convention.GUARANTEED for name, answer in answers.items()
               if name not in written)
    owned = {name: me.argument_conventions(me.LIST_FAMILY, name, count)
             for name, count in LIST_METHOD_ARITY.items()
             if Convention.OWNED in me.argument_conventions(me.LIST_FAMILY, name, count)}
    assert owned == {"push": (Convention.OWNED,),
                     "insert": (Convention.GUARANTEED, Convention.OWNED)}


def test_the_array_slot_is_the_one_owned_argument_of_its_conventions():
    """The `slot` of an array row and `argument_conventions` of the array family give one
    answer: the slot is `Owned`, and each other argument (the index of `insert`) borrows."""
    from sushi_lang.semantics import method_effects as me
    from sushi_lang.semantics.param_modes import Convention
    from sushi_lang.semantics.passes.types.arrays import _ARRAY_METHODS

    for name, spec in _ARRAY_METHODS.items():
        conventions = me.argument_conventions(me.ARRAY_FAMILY, name, spec.arity)
        owned = {index for index, convention in enumerate(conventions)
                 if convention is Convention.OWNED}
        assert owned == ({spec.slot} if spec.slot is not None else set()), name


def test_a_get_out_answers_a_view_by_its_family_and_its_name():
    """`get`, `first` and `last` of a List and of an array answer a view (`answers_view`,
    docs/design/ir.md 7.9). The same name of another family, and another method of the
    family, do not."""
    from sushi_lang.semantics import method_effects as me
    from sushi_lang.semantics.generics.list import LIST_METHOD_ARITY
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY

    for family_name, names in ((me.LIST_FAMILY, LIST_METHOD_ARITY),
                               (me.ARRAY_FAMILY, arrays.ARRAY_METHOD_ARITY)):
        family = METHOD_TYPE_REGISTRY.family(family_name)
        views = {name for name in names if me.answers_view(family.callee(name))}
        assert views == {"get", "first", "last"}
    assert set(me.VIEW_ANSWERS) <= me.EFFECT_FAMILIES
    for name in me.RECEIVER_READ_FAMILIES:
        assert not me.answers_view(METHOD_TYPE_REGISTRY.family(name).callee("get"))


@pytest.mark.parametrize("family, arity", [
    ("result", "RESULT_METHOD_ARITY"), ("maybe", "MAYBE_METHOD_ARITY")])
def test_a_wrapper_method_takes_its_receiver_only_when_it_declares_nom_self(family, arity):
    """The answer is per method, from the signature row of the method: `map_err` and
    `or_err` take their receiver. `realise` and `expect` read it, as on `main`."""
    from sushi_lang.semantics.generics import maybe, results
    from sushi_lang.semantics.method_effects import receiver_convention
    from sushi_lang.semantics.param_modes import Convention
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY

    methods = getattr(results if family == "result" else maybe, arity)
    answers = {name: receiver_convention(METHOD_TYPE_REGISTRY.family(family).callee(name))
               for name in methods}
    owned = {name for name, answer in answers.items() if answer is Convention.OWNED}
    assert owned == ({"map_err"} if family == "result" else {"or_err"})
    assert all(answer is Convention.GUARANTEED for name, answer in answers.items()
               if name not in owned)
    assert {"realise", "expect"} <= set(answers) - owned


def test_slir_lowering_refuses_a_receiver_with_no_convention():
    """A family with no answer stops in `typecheck` or `borrow` first. A call of it in SLIR
    lowering is an internal error, never a `Guaranteed` receiver by default."""
    from sushi_lang.internals.diagnostics import InternalCompilerError
    from sushi_lang.internals.report import Span
    from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
    from sushi_lang.semantics.shir import exprs as e
    from sushi_lang.semantics.shir.callee import Builtin
    from sushi_lang.semantics.shir.ids import LocalId, ShirId
    from sushi_lang.semantics.slir import lower_calls

    span = Span(1, 1, 1, 2)
    recv = e.LocalRef(ShirId(1), span, LocalId(0))
    callee = Builtin(recv, METHOD_TYPE_REGISTRY.family("hashmap").callee("len"))
    with pytest.raises(InternalCompilerError) as caught:
        lower_calls._receiver_convention(callee, span)
    assert caught.value.code == "CE0000"


def test_a_family_in_no_receiver_set_is_an_internal_error():
    from sushi_lang.internals.diagnostics import InternalCompilerError
    from sushi_lang.semantics.method_effects import receiver_convention
    from sushi_lang.semantics.shir.callee import BuiltinMethod

    with pytest.raises(InternalCompilerError) as caught:
        receiver_convention(BuiltinMethod("unheard_of", "probe", nom_self=False))
    assert caught.value.code == "CE0000"
