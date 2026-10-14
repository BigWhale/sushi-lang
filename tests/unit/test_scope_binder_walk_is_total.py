"""Gate: the binder walk of the `scope` pass covers every binder form of the AST (ir.md 7.1).

`Bindings` gives each binder of a body its `LocalId`. The scope pass declares the binders
arm by arm, and at the end of each body a backstop (CE0153) walks the body again and
asks `binders_of` for every binder it holds. So a binder form that `binders_of` does not
know is a local with no `LocalId`, and nothing tells it.

The candidates come from the AST classes, not from a list: every class that a body can
hold, and every field of it that can hold a binder. A field can hold a binder when it
can hold a `Param` or a `DestructureTarget`, when it is a field of one of those two
records that can hold a `str`, or when it can hold a `str` and has no default. A name
that the user writes is a required field, because the builder always gives it; a field
with a default is a marker or a stamp of a later pass. Each candidate is a binder, read
by a form of `BINDER_FORMS`, or it is in `NOT_A_BINDER` with the reason. These tests
read the AST classes and hand-built nodes; they do not start the compiler.
"""
from __future__ import annotations

import dataclasses
import sys
import typing

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics import ast as a
from sushi_lang.semantics.name_ladder import BareName
from sushi_lang.semantics.passes.scope_bindings import (
    BINDER_FORMS, DECLARED_ANSWERS, BindingsWriter, binders_of)
from sushi_lang.semantics.shir.bindings import Capture, Local, TypeName
from sushi_lang.semantics.shir.ids import LocalId

# The binder fields, each with the forms of `BINDER_FORMS` that read it.
BINDER_FIELDS: dict[str, tuple[type, ...]] = {
    "FuncDef.params": (a.FuncDef,),
    "ExtendDef.params": (a.ExtendDef,),
    "Lambda.params": (a.Lambda,),
    "Param.name": (a.FuncDef, a.ExtendDef, a.Lambda),
    "Let.name": (a.Let,),
    "Let.targets": (a.Let,),
    "DestructureTarget.name": (a.Let,),
    "DestructureTarget.nested": (a.Let,),
    "Foreach.item_name": (a.Foreach,),
    "Expand.var": (a.Expand,),
    "Pattern.bindings": (a.MatchArm,),
    "TuplePattern.elements": (a.MatchArm,),
    "OwnPattern.inner_pattern": (a.MatchArm,),
    "OrPattern.alternatives": (a.MatchArm,),
    "RefBinding.name": (a.MatchArm,),
    "NomBinding.name": (a.MatchArm,),
}

_DECLARATION = "the name of the declaration itself: a global, and no local of its body"
_MEMBER = "a member name behind a dot, which the typecheck pass resolves"
_REFERENCE = "a name that refers to a declaration; the scope pass records its answer"
_TEXT = "text of the source, and no name"

NOT_A_BINDER: dict[str, str] = {
    "FuncDef.name": _DECLARATION,
    "ExtendDef.name": _DECLARATION,
    "ConstDef.name": _DECLARATION,
    "VarDef.name": _DECLARATION,
    "BoundedTypeParam.name": "a type parameter: a name of a type, and no local",
    "DontPanicMarker.reason": _TEXT,
    "DotCall.method": _MEMBER,
    "MethodCall.method": _MEMBER,
    "MemberAccess.member": _MEMBER,
    "EnumConstructor.variant_name": _MEMBER,
    "Pattern.variant_name": _MEMBER,
    "Name.id": _REFERENCE,
    "EnumConstructor.enum_name": _REFERENCE,
    "Pattern.enum_name": _REFERENCE,
    "InterpolatedString.parts": "the text between the holes",
    "StringLit.value": _TEXT,
    "LiteralPattern.value": "the value of a literal arm",
    "LiteralPattern.display": _TEXT,
    "RefBinding.mode": "a borrow marker",
    "Param.self_mode": "a receiver marker",
    "Lambda.captures": "names that bind in an enclosing scope; Bindings holds them as "
                       "Capture records",
}

_ROOTS = (a.FuncDef, a.ExtendDef, a.ConstDef)
# Doc text holds no code, so a body walk never enters it.
_NO_CODE = (a.DocBlock,)
_RECORDS = (a.Param, a.DestructureTarget)
_HINT_NAMES = {"ParamMode", "Provenance", "BorrowedReturn", "Conversion",
               "ExtensionTarget", "TemplateId", "NamespaceRef"}


def _declared(cls: type) -> bool:
    return getattr(sys.modules.get(cls.__module__), cls.__name__, None) is cls


def _hints(cls: type) -> dict[str, object]:
    return typing.get_type_hints(cls, globalns=vars(a),
                                 localns={name: object for name in _HINT_NAMES})


def _ast_classes(hint: object) -> set[type]:
    """The AST classes that a field with this type can hold, subclasses included."""
    found: set[type] = set()
    if isinstance(hint, type) and hint.__module__ == a.__name__ and dataclasses.is_dataclass(hint):
        found.add(hint)
        found |= {sub for sub in hint.__subclasses__() if _declared(sub)}
    for arg in typing.get_args(hint):
        found |= _ast_classes(arg)
    return found


def _holds(hint: object, kind: type) -> bool:
    if isinstance(hint, type) and issubclass(hint, kind):
        return True
    return any(_holds(arg, kind) for arg in typing.get_args(hint))


def _body_classes() -> set[type]:
    """Every AST class that a body can hold, from the declarations that own a body."""
    seen: set[type] = set()
    todo: list[type] = [cls for root in _ROOTS for cls in _ast_classes(root)]
    while todo:
        cls = todo.pop()
        if cls in seen or cls in _NO_CODE:
            continue
        seen.add(cls)
        for field in dataclasses.fields(cls):
            todo.extend(_ast_classes(_hints(cls)[field.name]))
    return seen


def _required(field: dataclasses.Field) -> bool:
    return (field.default is dataclasses.MISSING
            and field.default_factory is dataclasses.MISSING)


def _candidates() -> set[str]:
    found: set[str] = set()
    for cls in _body_classes():
        hints = _hints(cls)
        for field in dataclasses.fields(cls):
            hint = hints[field.name]
            if (any(_holds(hint, record) for record in _RECORDS)
                    or (_holds(hint, str) and (_required(field) or cls in _RECORDS))):
                found.add(f"{cls.__name__}.{field.name}")
    return found


# --- The classification of the AST fields --------------------------------------------


def test_the_candidates_are_found():
    # Guards the tests below against a pass on an empty set.
    assert {"Let.name", "Foreach.item_name", "Name.id", "Param.name"} <= _candidates()


def test_every_candidate_field_is_classified():
    missing = sorted(_candidates() - set(BINDER_FIELDS) - set(NOT_A_BINDER))
    assert not missing, (
        f"AST field(s) that can hold a binder and that the gate does not know: {missing}.\n"
        "A field that DECLARES a local goes in BINDER_FIELDS, with the form of "
        "scope_bindings.BINDER_FORMS that reads it, and the scope pass declares it. "
        "Any other field goes in NOT_A_BINDER, with the reason."
    )


def test_no_entry_names_a_field_that_is_gone():
    stray = sorted((set(BINDER_FIELDS) | set(NOT_A_BINDER)) - _candidates())
    assert not stray, f"entries for fields that are not candidates: {stray}"


def test_no_field_is_a_binder_and_not_a_binder():
    assert not set(BINDER_FIELDS) & set(NOT_A_BINDER)


def test_every_binder_field_is_read_by_a_form_of_the_walk():
    stray = sorted(f"{field} -> {form.__name__}" for field, forms in BINDER_FIELDS.items()
                   for form in forms if form not in BINDER_FORMS)
    assert not stray, (
        f"binder field(s) whose form is not in BINDER_FORMS: {stray}. The backstop asks "
        "BINDER_FORMS for the binders of a node; a form that it does not know is a local "
        "with no LocalId."
    )


def test_every_form_of_the_walk_reads_a_binder_field():
    named = {form for forms in BINDER_FIELDS.values() for form in forms}
    stray = sorted(form.__name__ for form in BINDER_FORMS if form not in named)
    assert not stray, f"BINDER_FORMS rows that no binder field names: {stray}"


# --- What `binders_of` answers, on hand-built nodes ----------------------------------


def _block() -> a.Block:
    return a.Block(None, [])


def _names(node: object) -> list[tuple[str, str]]:
    return [(type(owner).__name__, name) for owner, name in binders_of(node)]


def test_a_let_binds_its_name_then_each_destructure_target_in_source_order():
    inner = [a.DestructureTarget(name="c"), a.DestructureTarget()]
    targets = [a.DestructureTarget(name="a"), a.DestructureTarget(nested=inner),
               a.DestructureTarget(name="b")]
    let = a.Let(None, "#whole", None, a.IntLit(None, 0), targets=targets)
    assert _names(let) == [("Let", "#whole"), ("DestructureTarget", "a"),
                           ("DestructureTarget", "c"), ("DestructureTarget", "b")]


def test_a_foreach_binds_its_item():
    loop = a.Foreach(None, "x", None, a.Name(None, "xs"), _block())
    assert _names(loop) == [("Foreach", "x")]


def test_an_expand_binds_its_variable():
    assert _names(a.Expand(None, "p", a.Name(None, "args"), _block())) == [("Expand", "p")]


@pytest.mark.parametrize("node", [
    a.Lambda(None, [a.Param("p", None), a.Param("q", None)], a.Name(None, "p")),
    a.FuncDef(None, "f", [a.Param("p", None), a.Param("q", None)], None, _block()),
    a.ExtendDef(None, None, "m", [a.Param("p", None), a.Param("q", None)], None, _block()),
], ids=lambda n: type(n).__name__)
def test_a_callable_binds_each_parameter(node):
    assert _names(node) == [("Param", "p"), ("Param", "q")]


def test_an_arm_binds_every_name_of_every_alternative():
    first = a.Pattern(None, "Shape", "Pair", [
        "x", a.RefBinding(None, "y", "poke"), a.NomBinding(None, "z"), "_"])
    second = a.Pattern(None, "Shape", "Trio", [
        "x", a.TuplePattern(None, ["y", a.OwnPattern(None, "z")])])
    arm = a.MatchArm(None, a.OrPattern(None, [first, second]), _block())
    assert _names(arm) == [
        ("Pattern", "x"), ("Pattern", "y"), ("Pattern", "z"),
        ("Pattern", "x"), ("TuplePattern", "y"), ("OwnPattern", "z")]


def test_a_node_of_no_form_binds_nothing():
    assert _names(a.Name(None, "x")) == []


# --- The answer of each rung of the bare-name ladder ---------------------------------


def test_every_rung_has_an_answer():
    """The kinds of `Bindings` cover the ladder (PLAN.md, the port of the ladder gate).

    The scope pass answers LOCAL and CONSTANT itself: a local needs its `LocalId`, and a
    constant is a `Const` or, for a unit `var`, a `Storage`.
    """
    answered = set(DECLARED_ANSWERS) | {BareName.LOCAL, BareName.CONSTANT}
    assert answered == set(BareName)
    assert not set(DECLARED_ANSWERS) & {BareName.LOCAL, BareName.CONSTANT}


# --- The backstop at the end of each body --------------------------------------------


def _body() -> tuple[a.FuncDef, a.Param, a.Let, a.Name, a.Lambda]:
    use = a.Name(None, "n")
    lam = a.Lambda(None, [], a.IntLit(None, 0))
    let = a.Let(None, "n", None, a.IntLit(None, 1))
    param = a.Param("p", None)
    body = a.Block(None, [let, a.ExprStmt(None, use), a.ExprStmt(None, lam)])
    return a.FuncDef(None, "f", [param], None, body), param, let, use, lam


def _complete(receiver: bool = False) -> tuple[BindingsWriter, tuple]:
    func, param, let, use, lam = _body()
    writer = BindingsWriter(func, receiver=receiver)
    if receiver:
        writer.binder(func, "self")
    writer.binder(param, "p")
    local = writer.binder(let, "n")
    writer.resolve(use, Local(local))
    writer.captures(lam, ())
    return writer, (func, param, let, use, lam)


def test_a_complete_body_passes_the_backstop():
    writer, (func, param, let, use, _lam) = _complete()
    bindings = writer.finish()
    assert bindings.local_of(param, "p") == LocalId(0)
    assert bindings.local_of(let, "n") == LocalId(1)
    assert bindings.resolution_of(use) == Local(LocalId(1))


def test_the_backstop_refuses_a_name_with_no_answer():
    func, param, let, _use, lam = _body()
    writer = BindingsWriter(func)
    writer.binder(param, "p")
    writer.binder(let, "n")
    writer.captures(lam, ())
    with pytest.raises(InternalCompilerError) as caught:
        writer.finish()
    assert caught.value.code == "CE0153"
    assert caught.value.params.get("name") == "n"


def test_the_backstop_refuses_a_binder_with_no_local():
    func, param, _let, use, lam = _body()
    writer = BindingsWriter(func)
    writer.binder(param, "p")
    writer.resolve(use, TypeName("n"))
    writer.captures(lam, ())
    with pytest.raises(InternalCompilerError) as caught:
        writer.finish()
    assert caught.value.code == "CE0153"
    assert caught.value.params.get("what") == "binder"


def test_the_backstop_refuses_a_lambda_with_no_captures():
    func, param, let, use, _lam = _body()
    writer = BindingsWriter(func)
    writer.binder(param, "p")
    writer.resolve(use, Local(writer.binder(let, "n")))
    with pytest.raises(InternalCompilerError) as caught:
        writer.finish()
    assert caught.value.code == "CE0153"
    assert caught.value.params.get("what") == "lambda"


def test_the_backstop_refuses_a_method_with_no_receiver_local():
    func, param, let, use, lam = _body()
    writer = BindingsWriter(func, receiver=True)
    writer.binder(param, "p")
    writer.resolve(use, Local(writer.binder(let, "n")))
    writer.captures(lam, ())
    with pytest.raises(InternalCompilerError) as caught:
        writer.finish()
    assert caught.value.params.get("name") == "self"


def test_a_method_with_its_receiver_local_passes():
    writer, _nodes = _complete(receiver=True)
    assert writer.finish().local_of(writer.bindings.body, "self") == LocalId(0)


def test_a_binder_met_again_keeps_its_local():
    writer, (_func, param, _let, _use, _lam) = _complete()
    assert writer.binder(param, "p") == LocalId(0)


def test_a_later_alternative_binds_the_local_of_the_first():
    first = a.Pattern(None, "S", "A", ["x"])
    second = a.Pattern(None, "S", "B", ["x"])
    writer = BindingsWriter(a.ConstDef(None, "C", None, a.IntLit(None, 0)))
    local = writer.binder(first, "x")
    writer.same_local(second, "x", local)
    assert writer.bindings.local_of(second, "x") == local


def test_an_answer_belongs_to_its_node_and_to_no_equal_node():
    """An AST node compares by value, so the table keys on identity (ir.md 7.1)."""
    writer, (_func, _param, _let, use, lam) = _complete()
    assert writer.bindings.resolution_of(a.Name(None, "n")) is None
    assert writer.bindings.captures_of(lam) == ()
    writer.captures(lam, (Capture("n", LocalId(1)),))
    assert writer.bindings.captures_of(lam) == (Capture("n", LocalId(1)),)
    assert writer.bindings.resolution_of(use) is not None
