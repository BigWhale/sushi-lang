"""P1-T7b: compile-time unrolling of ``expand(...)`` over parameter packs.

Drives the dedicated unroll post-pass run during monomorphization. The contract:

* ``expand(a in args): BODY`` is replaced, in place, by N independent deep copies
  of BODY spliced in pack order, with every free reference to the binding var
  renamed to the fan-out parameter ``args_i`` (0-based).
* No ``Expand`` node survives.
* Arity 0 -> zero copies (the Expand vanishes).
* Each copy is an independent object (no shared AST nodes across copies).
* Nesting and shadowing behave as documented.

These mirror the synthetic-fixture style of test_p1t3_builder_pack /
test_monomorphize_pack: a pack ``GenericFuncDef`` is driven end-to-end through
the REAL ``monomorphize_function`` (so the wiring of fan-out-name derivation +
unroll is exercised), plus a few direct calls to ``unroll_expands`` for the
local properties (rename, nesting, shadowing).
"""
import copy

import pytest

from sushi_lang.semantics.generics.types import TypeParameter, TypePack
from sushi_lang.semantics.generics.monomorphize.unroll import unroll_expands
from sushi_lang.semantics.typesys import BuiltinType, UnknownType
from sushi_lang.semantics.ast import (
    Block, Param, Expand, Name, DotCall, PrintLn, ExprStmt, Let, Return,
    If, BoolLit,
)

I32 = BuiltinType.I32
STR = BuiltinType.STRING
BOOL = BuiltinType.BOOL


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _param(name, is_pack=False):
    tp = TypeParameter(name)
    if is_pack:
        object.__setattr__(tp, "is_pack", True)
    return tp


def _make_mono():
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.generics.monomorphize import Monomorphizer

    return Monomorphizer(reporter=Reporter())


def _expand_body(var="a", pack="args"):
    """expand(a in args): println(a.display())"""
    return Block(loc=None, statements=[
        Expand(
            loc=None,
            var=var,
            iterable=Name(loc=None, id=pack),
            body=Block(loc=None, statements=[
                PrintLn(loc=None, value=DotCall(
                    loc=None,
                    receiver=Name(loc=None, id=var),
                    method="display",
                    args=[],
                )),
            ]),
        ),
    ])


def _pack_generic_with_expand():
    """fn print_all<...Ts>(...Ts args): expand(a in args): println(a.display())"""
    from sushi_lang.semantics.passes.collect.functions import GenericFuncDef

    return GenericFuncDef(
        name="print_all",
        type_params=(_param("Ts", is_pack=True),),
        params=[Param(loc=None, name="args", ty=UnknownType("Ts"))],
        ret=None,
        body=_expand_body(),
    )


def _names_in(node):
    """Collect all Name.id occurrences reachable from node (deep)."""
    import dataclasses
    out = []

    def walk(o):
        if isinstance(o, Name):
            out.append(o.id)
            return
        if isinstance(o, (list, tuple)):
            for x in o:
                walk(x)
            return
        if dataclasses.is_dataclass(o):
            for f in dataclasses.fields(o):
                walk(getattr(o, f.name))

    walk(node)
    return out


def _expands_in(node):
    import dataclasses
    out = []

    def walk(o):
        if isinstance(o, Expand):
            out.append(o)
        if isinstance(o, (list, tuple)):
            for x in o:
                walk(x)
            return
        if dataclasses.is_dataclass(o):
            for f in dataclasses.fields(o):
                walk(getattr(o, f.name))

    walk(node)
    return out


# ---------------------------------------------------------------------------
# direct unroll_expands properties
# ---------------------------------------------------------------------------

def test_unroll_arity_2_produces_two_renamed_copies():
    body = _expand_body()
    unroll_expands(body, {"args": ["args_0", "args_1"]})

    # Two top-level statements (the two PrintLn copies), no Expand left.
    assert len(body.statements) == 2
    assert _expands_in(body) == []
    assert all(isinstance(s, PrintLn) for s in body.statements)

    # The two receivers were renamed to args_0 / args_1 respectively, in order.
    recv0 = body.statements[0].value.receiver
    recv1 = body.statements[1].value.receiver
    assert isinstance(recv0, Name) and recv0.id == "args_0"
    assert isinstance(recv1, Name) and recv1.id == "args_1"
    # The original binding name 'a' no longer appears anywhere.
    assert "a" not in _names_in(body)


def test_unroll_arity_0_removes_expand():
    body = _expand_body()
    unroll_expands(body, {"args": []})
    assert body.statements == []
    assert _expands_in(body) == []


def test_unroll_arity_3_order_and_names():
    body = _expand_body()
    unroll_expands(body, {"args": ["args_0", "args_1", "args_2"]})
    assert len(body.statements) == 3
    ids = [s.value.receiver.id for s in body.statements]
    assert ids == ["args_0", "args_1", "args_2"]


def test_copies_are_independent_objects():
    body = _expand_body()
    unroll_expands(body, {"args": ["args_0", "args_1"]})
    s0, s1 = body.statements
    # No shared mutable AST nodes across copies (so later passes can annotate
    # each copy without cross-contamination).
    assert s0 is not s1
    assert s0.value is not s1.value
    assert s0.value.receiver is not s1.value.receiver


def test_nested_expand_inside_if_is_unrolled():
    inner = Expand(
        loc=None, var="a", iterable=Name(loc=None, id="args"),
        body=Block(loc=None, statements=[
            PrintLn(loc=None, value=Name(loc=None, id="a")),
        ]),
    )
    body = Block(loc=None, statements=[
        If(
            loc=None,
            arms=[(BoolLit(loc=None, value=True),
                   Block(loc=None, statements=[inner]))],
            else_block=None,
        ),
    ])
    unroll_expands(body, {"args": ["args_0", "args_1"]})
    # The if survives; its block now holds two unrolled prints, no Expand.
    assert _expands_in(body) == []
    if_block = body.statements[0].arms[0][1]
    assert len(if_block.statements) == 2
    assert [s.value.id for s in if_block.statements] == ["args_0", "args_1"]


def test_shadowing_let_suppresses_rename_in_tail():
    # expand(a in args): let a = args  ; println(a)
    # The inner `let a` re-binds `a`; the subsequent println(a) must NOT be
    # renamed to args_i (it refers to the shadowing local).
    body = Block(loc=None, statements=[
        Expand(
            loc=None, var="a", iterable=Name(loc=None, id="args"),
            body=Block(loc=None, statements=[
                Let(loc=None, name="a", ty=I32, value=Name(loc=None, id="x")),
                PrintLn(loc=None, value=Name(loc=None, id="a")),
            ]),
        ),
    ])
    unroll_expands(body, {"args": ["args_0"]})
    # One copy: a Let and a PrintLn. The loop-var rename stops at `let a`
    # (shadowed), so the println does NOT become args_0. The top-level local
    # `a` is then alpha-renamed to its copy-unique name, and the println follows.
    let_stmt, print_stmt = body.statements
    assert isinstance(let_stmt, Let) and let_stmt.name == "a__x0"
    assert print_stmt.value.id == "a__x0"
    # crucially, it is NOT the fan-out param.
    assert print_stmt.value.id != "args_0"


# ---------------------------------------------------------------------------
# hygienic per-copy local renaming (the P1-T7b local-collision fix)
# ---------------------------------------------------------------------------

def _expand_body_with_local(var="a", pack="args"):
    """expand(a in args): let string s = a.display(); println(s)"""
    return Block(loc=None, statements=[
        Expand(
            loc=None,
            var=var,
            iterable=Name(loc=None, id=pack),
            body=Block(loc=None, statements=[
                Let(loc=None, name="s", ty=STR, value=DotCall(
                    loc=None,
                    receiver=Name(loc=None, id=var),
                    method="display",
                    args=[],
                )),
                PrintLn(loc=None, value=Name(loc=None, id="s")),
            ]),
        ),
    ])


def test_unroll_renames_toplevel_locals_per_copy():
    body = _expand_body_with_local()
    unroll_expands(body, {"args": ["args_0", "args_1"]})

    # Two copies, each a (Let, PrintLn) pair -> 4 top-level statements, no Expand.
    assert _expands_in(body) == []
    assert len(body.statements) == 4
    lets = [s for s in body.statements if isinstance(s, Let)]
    prints = [s for s in body.statements if isinstance(s, PrintLn)]
    assert len(lets) == 2 and len(prints) == 2

    # Each copy's declared local has a DISTINCT name (so they don't collide in
    # the shared callee scope).
    let_names = [l.name for l in lets]
    assert len(set(let_names)) == 2, let_names
    assert let_names == ["s__x0", "s__x1"]

    # References are consistent within each copy: copy i's println uses copy i's
    # renamed local, and the let value uses the matching fan-out receiver.
    let0, print0, let1, print1 = body.statements
    assert let0.name == "s__x0"
    assert print0.value.id == "s__x0"
    assert let0.value.receiver.id == "args_0"
    assert let1.name == "s__x1"
    assert print1.value.id == "s__x1"
    assert let1.value.receiver.id == "args_1"
    # The original local name 's' no longer appears anywhere.
    assert "s" not in _names_in(body)


def test_unroll_nested_block_locals_left_alone():
    # A `let` inside a nested `if` is in that block's own scope; each duplicated
    # if-block is a distinct backend scope, so the local need NOT be renamed.
    body = Block(loc=None, statements=[
        Expand(
            loc=None, var="a", iterable=Name(loc=None, id="args"),
            body=Block(loc=None, statements=[
                If(
                    loc=None,
                    arms=[(BoolLit(loc=None, value=True),
                           Block(loc=None, statements=[
                               Let(loc=None, name="s", ty=STR,
                                   value=Name(loc=None, id="a")),
                               PrintLn(loc=None, value=Name(loc=None, id="s")),
                           ]))],
                    else_block=None,
                ),
            ]),
        ),
    ])
    unroll_expands(body, {"args": ["args_0", "args_1"]})
    assert _expands_in(body) == []
    assert len(body.statements) == 2  # two If copies
    for idx, fanout in enumerate(["args_0", "args_1"]):
        if_block = body.statements[idx].arms[0][1]
        let_stmt, print_stmt = if_block.statements
        # Nested-block local keeps its original name (separate scope per copy).
        assert let_stmt.name == "s"
        assert print_stmt.value.id == "s"
        # Its initializer still references the fan-out receiver.
        assert let_stmt.value.id == fanout


# ---------------------------------------------------------------------------
# end-to-end through monomorphize_function
# ---------------------------------------------------------------------------

def test_monomorphize_unrolls_expand_end_to_end():
    mono = _make_mono()
    generic = _pack_generic_with_expand()

    # arity 2: pack (i32, string)
    fn = mono.function_monomorphizer.monomorphize_function(generic, (I32, STR))

    # Signature fanned out to args_0 (i32), args_1 (string).
    assert [p.name for p in fn.params] == ["args_0", "args_1"]
    assert [p.ty for p in fn.params] == [I32, STR]

    # Body has two unrolled prints, no Expand, receivers renamed in order.
    assert _expands_in(fn.body) == []
    prints = [s for s in fn.body.statements if isinstance(s, PrintLn)]
    assert len(prints) == 2
    assert [p.value.receiver.id for p in prints] == ["args_0", "args_1"]


def test_monomorphize_arity_zero_removes_expand_end_to_end():
    mono = _make_mono()
    generic = _pack_generic_with_expand()

    # arity 0: empty pack -> no fan-out params, expand vanishes.
    fn = mono.function_monomorphizer.monomorphize_function(generic, ())
    assert [p.name for p in fn.params] == []
    assert _expands_in(fn.body) == []
    assert [s for s in fn.body.statements if isinstance(s, PrintLn)] == []
