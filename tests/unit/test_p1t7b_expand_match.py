"""P1-T7b: ``expand(...)`` unroll robustness around frozen Type nodes and
``match`` arm pattern-binding shadowing.

Two regressions are covered here:

BUG 1 (frozen Type nodes): ``_rename_walk`` used to ``setattr`` unconditionally
for every dataclass field it reached. The ``typesys`` Type nodes
(``StructType``, ``EnumType``, ...) are ``@dataclass(frozen=True)``; when the
walk descended into one (e.g. a resolved type carried on a ``let`` or a ``match``
scrutinee) the write-back raised ``FrozenInstanceError`` -- an uncaught compiler
crash. The fix makes the rename write-only-if-changed and skip frozen
dataclasses entirely.

BUG 2 (match-arm shadowing): a ``match`` arm whose pattern binds the loop var
introduces a shadow scope for THAT arm's body; occurrences of the var inside the
arm body refer to the pattern binding (a distinct variable) and must NOT be
renamed to a fan-out param. The scrutinee and other arms are renamed normally.
"""
from sushi_lang.semantics.generics.monomorphize.unroll import unroll_expands
from sushi_lang.semantics.typesys import BuiltinType, StructType
from sushi_lang.semantics.ast import (
    Block, Expand, Name, Let, PrintLn, Match, MatchArm, Pattern,
)

I32 = BuiltinType.I32


def _frozen_struct():
    """A frozen typesys Type node (StructType) to bury in an expand body."""
    return StructType(name="Box", fields=(("v", I32),))


# ---------------------------------------------------------------------------
# BUG 1: frozen Type nodes reachable from the expand body must not crash
# ---------------------------------------------------------------------------

def test_rename_does_not_crash_on_frozen_type_on_let():
    # expand(a in args): let b: Box = a   (the `let` carries a FROZEN StructType
    # as its declared type; the rename walk must not try to mutate it).
    body = Block(loc=None, statements=[
        Expand(
            loc=None, var="a", iterable=Name(loc=None, id="args"),
            body=Block(loc=None, statements=[
                Let(loc=None, name="b", ty=_frozen_struct(),
                    value=Name(loc=None, id="a")),
                PrintLn(loc=None, value=Name(loc=None, id="b")),
            ]),
        ),
    ])
    # Must not raise FrozenInstanceError.
    unroll_expands(body, {"args": ["args_0"]})

    let_stmt, _print = body.statements
    assert isinstance(let_stmt, Let)
    # The frozen type is untouched and still the same object.
    assert isinstance(let_stmt.ty, StructType) and let_stmt.ty.name == "Box"
    # The `let`'s VALUE (the free `a`) was renamed to the fan-out param.
    assert isinstance(let_stmt.value, Name) and let_stmt.value.id == "args_0"


def test_rename_does_not_crash_on_frozen_scrutinee_type():
    # A Match whose scrutinee is a Name referencing the loop var, plus a frozen
    # type buried on a sibling statement. The renamed copy must be produced
    # without touching the frozen node.
    body = Block(loc=None, statements=[
        Expand(
            loc=None, var="a", iterable=Name(loc=None, id="args"),
            body=Block(loc=None, statements=[
                Let(loc=None, name="t", ty=_frozen_struct(),
                    value=Name(loc=None, id="a")),
                Match(
                    loc=None,
                    scrutinee=Name(loc=None, id="a"),
                    arms=[MatchArm(
                        loc=None,
                        pattern=Pattern(loc=None, enum_name="Box",
                                        variant_name="Val", bindings=["x"]),
                        body=Block(loc=None, statements=[
                            PrintLn(loc=None, value=Name(loc=None, id="x")),
                        ]),
                    )],
                ),
            ]),
        ),
    ])
    unroll_expands(body, {"args": ["args_0"]})

    let_stmt, match_stmt = body.statements
    assert isinstance(let_stmt.ty, StructType)
    assert isinstance(let_stmt.value, Name) and let_stmt.value.id == "args_0"
    # The scrutinee (free `a`) is renamed; the arm binding `x` is untouched.
    assert isinstance(match_stmt, Match)
    assert match_stmt.scrutinee.id == "args_0"
    assert match_stmt.arms[0].body.statements[0].value.id == "x"


# ---------------------------------------------------------------------------
# BUG 2: a match arm whose pattern binds the loop var shadows it in the arm body
# ---------------------------------------------------------------------------

def test_match_arm_pattern_binding_shadows_loop_var():
    # expand(a in args):
    #     match a:                         # scrutinee `a` -> renamed
    #         Box.Val(a) -> println(a)     # pattern binds `a` -> NOT renamed
    body = Block(loc=None, statements=[
        Expand(
            loc=None, var="a", iterable=Name(loc=None, id="args"),
            body=Block(loc=None, statements=[
                Match(
                    loc=None,
                    scrutinee=Name(loc=None, id="a"),
                    arms=[MatchArm(
                        loc=None,
                        pattern=Pattern(loc=None, enum_name="Box",
                                        variant_name="Val", bindings=["a"]),
                        body=Block(loc=None, statements=[
                            PrintLn(loc=None, value=Name(loc=None, id="a")),
                        ]),
                    )],
                ),
            ]),
        ),
    ])
    unroll_expands(body, {"args": ["args_0"]})

    match_stmt = body.statements[0]
    assert isinstance(match_stmt, Match)
    # Scrutinee renamed to the fan-out param...
    assert match_stmt.scrutinee.id == "args_0"
    # ...but the arm body's `a` is the pattern binding and stays `a`.
    arm_print = match_stmt.arms[0].body.statements[0]
    assert arm_print.value.id == "a"


def test_match_arm_without_binding_is_renamed():
    # expand(a in args):
    #     match a:
    #         Box.Val(x) -> println(a)    # arm does NOT bind `a` -> body renamed
    body = Block(loc=None, statements=[
        Expand(
            loc=None, var="a", iterable=Name(loc=None, id="args"),
            body=Block(loc=None, statements=[
                Match(
                    loc=None,
                    scrutinee=Name(loc=None, id="a"),
                    arms=[MatchArm(
                        loc=None,
                        pattern=Pattern(loc=None, enum_name="Box",
                                        variant_name="Val", bindings=["x"]),
                        body=Block(loc=None, statements=[
                            PrintLn(loc=None, value=Name(loc=None, id="a")),
                        ]),
                    )],
                ),
            ]),
        ),
    ])
    unroll_expands(body, {"args": ["args_0"]})

    match_stmt = body.statements[0]
    assert match_stmt.scrutinee.id == "args_0"
    # No shadowing binding here, so the free `a` in the arm body IS renamed.
    assert match_stmt.arms[0].body.statements[0].value.id == "args_0"
