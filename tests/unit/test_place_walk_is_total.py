"""One walk decides the root of a place (#784): `semantics/places.py:walk_place`.

Two gates. The walk is TOTAL over each tree: it reads a node through the adapter of its
tree (`_ast_step`, `_shir_step`), and every node of the AST `Expr` union and of the SHIR
`ShirExpr` union is either a step that the adapter crosses or a kind that ends the walk,
never both and never neither. And the walk is ONE: a loop or a recursion anywhere else
under `semantics/` that steps through `.receiver` / `.array`, or through the `.base` of a
SHIR `Field`, `Index` or `Slice`, the `.inner` of a SHIR `Try` or the `.recv` of a SHIR
dotted call, is a second root walk, and it is refused.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import typing
from pathlib import Path

import pytest

from sushi_lang.internals.report import Span
from sushi_lang.semantics import ast as sushi_ast
from sushi_lang.semantics import places
from sushi_lang.semantics.places import NOT_A_STEP, SHIR_NOT_A_STEP, Step, walk_place
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import IdAllocator, LocalId, ShirId

SEMANTICS = Path(places.__file__).resolve().parent
REPO = SEMANTICS.parent.parent

#: The fields that a place chain steps through: the AST `receiver` and `array`, and the
#: `base` of a SHIR place, the `inner` of a SHIR `??` and the `recv` of a SHIR dotted call
#: (docs/design/ir.md 7.4).
_STEP_FIELDS = {"receiver", "array", "base", "inner", "recv"}

# A root walk this gate may not remove, with the reason. The list may only shrink: an
# entry that is no longer found fails the gate too, so it cannot outlive its walk.
_KNOWN_WALKS: set[tuple[str, str]] = set()

# A recursion through `.receiver` / `.array` that answers another question than the root:
# it visits every node, so it recurses through a receiver like through any other child.
_NOT_A_ROOT_WALK = {
    ("sushi_lang/semantics/ast_walk.py", "_expr_types"),              # every type a node names
    # A walk over the items of a SHIR pattern: `Own.inner` is a pattern, not a receiver.
    ("sushi_lang/semantics/passes/types/matching.py", "_bindings_of"),
    ("sushi_lang/semantics/passes/types/matching.py", "_item_signature"),
    ("sushi_lang/semantics/passes/types/matching.py", "_poke_bindings"),
    # Every local that a read of a SLIR place reads: its root and the local of each index.
    # A SLIR place is no expression, so `walk_place` does not read it.
    ("sushi_lang/semantics/slir/places.py", "locals_read"),
}


def _union(union: object) -> set[str]:
    return {t.__name__ for t in typing.get_args(union)}


def _stepped_kinds(adapter: object) -> set[str]:
    """Every class a `case` arm of the adapter `adapter` crosses."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(adapter)))  # type: ignore[arg-type]
    names: set[str] = set()
    for case in (n for n in ast.walk(tree) if isinstance(n, ast.match_case)):
        # The class of the case, or of each alternative: a class in a keyword pattern
        # (`Call(target=Dotted())`) narrows the case and is no step of its own.
        tops = case.pattern.patterns if isinstance(case.pattern, ast.MatchOr) else [case.pattern]
        for node in tops:
            if isinstance(node, ast.MatchClass) and isinstance(node.cls, ast.Name):
                names.add(node.cls.id)
            elif isinstance(node, ast.MatchClass) and isinstance(node.cls, ast.Attribute):
                names.add(node.cls.attr)
    return names


#: The adapter of each tree, the kinds that end a walk in it, and its expression union.
_TREES = [
    pytest.param(places._ast_step, NOT_A_STEP, sushi_ast.Expr, id="ast"),
    pytest.param(places._shir_step, SHIR_NOT_A_STEP, e.ShirExpr, id="shir"),
]


@pytest.mark.parametrize("adapter,not_a_step,union", _TREES)
def test_every_expression_kind_is_a_step_or_an_end(adapter, not_a_step, union):
    ends = {t.__name__ for t in not_a_step}
    steps = _stepped_kinds(adapter)
    assert steps, "the gate read no arm from the adapter -- it is reading the wrong source"
    assert not steps & ends, f"a kind is both a step and an end: {sorted(steps & ends)}"
    missing = sorted(_union(union) - steps - ends)
    assert not missing, (
        f"walk_place does not classify {missing}. Add the kind to the kinds that end a "
        "walk, or give it an arm if a place chain continues through it.")
    stray = sorted((steps | ends) - _union(union))
    assert not stray, f"walk_place names a kind outside the expression union: {stray}"


def test_every_step_kind_has_a_flag():
    """One flag per arm, so a caller can cross or refuse each kind of step."""
    assert {flag.name for flag in Step if flag.name != "NONE"} == {
        "MEMBER", "INDEX", "CALL", "TRY"}


def _chain():
    """`a.f[0].m()??` -- one node of each step kind, off the root `a`."""
    a = sushi_ast.Name(id="a", loc=None)
    member = sushi_ast.MemberAccess(receiver=a, member="f", loc=None)
    index = sushi_ast.IndexAccess(array=member,
                                  index=sushi_ast.IntLit(value=0, loc=None), loc=None)
    call = sushi_ast.MethodCall(receiver=index, method="m", args=[], loc=None)
    return a, member, index, call, sushi_ast.TryExpr(expr=call, loc=None)


def test_each_flag_crosses_its_own_step_only():
    a, member, index, call, tried = _chain()
    every = Step.MEMBER | Step.INDEX | Step.CALL | Step.TRY
    assert walk_place(tried, every).node is a
    assert walk_place(tried, every).path == (tried, call, index, member)
    assert walk_place(tried, every & ~Step.TRY).node is tried
    assert walk_place(tried, every & ~Step.CALL).node is call
    assert walk_place(tried, every & ~Step.INDEX).node is index
    assert walk_place(tried, every & ~Step.MEMBER).node is member
    assert walk_place(None, every).node is None


_SPAN = Span(1, 1, 1, 2)


def _shir_chain():
    """`a.f[0].m()??` over SHIR -- one node of each step kind, off the local `a`."""
    ids: IdAllocator[ShirId] = IdAllocator(ShirId)
    a = e.LocalRef(ids.allocate(), _SPAN, LocalId(0))
    member = e.Field(ids.allocate(), _SPAN, a, "f", _SPAN)
    index = e.Index(ids.allocate(), _SPAN, member,
                    e.Lit(ids.allocate(), _SPAN, e.IntConst(0)), False)
    call = e.Call(ids.allocate(), _SPAN, e.Dotted(index, "m"), [])
    return a, member, index, call, e.Try(ids.allocate(), _SPAN, call)


def test_each_flag_crosses_its_own_shir_step_only():
    a, member, index, call, tried = _shir_chain()
    every = Step.MEMBER | Step.INDEX | Step.CALL | Step.TRY
    assert walk_place(tried, every).node is a
    assert walk_place(tried, every).local == LocalId(0)
    assert walk_place(tried, every).path == (tried, call, index, member)
    assert walk_place(tried, every & ~Step.TRY).node is tried
    assert walk_place(tried, every & ~Step.CALL).node is call
    assert walk_place(tried, every & ~Step.INDEX).node is index
    assert walk_place(tried, every & ~Step.MEMBER).node is member
    assert walk_place(tried, Step.TRY).local is None


def test_a_shir_call_with_no_receiver_ends_the_walk():
    """Only a dotted call has a receiver: a call of a name ends the walk, as an AST `Call`."""
    ids: IdAllocator[ShirId] = IdAllocator(ShirId)
    named = e.Call(ids.allocate(), _SPAN, e.BuiltinFn("println"), [])
    assert walk_place(named, Step.CALL).node is named


def test_the_early_stops_are_parameters():
    a, member, index, call, tried = _chain()
    every = Step.MEMBER | Step.INDEX | Step.CALL | Step.TRY
    stopped = walk_place(tried, every, stop=lambda m: "alias" if m is member else None)
    assert (stopped.node, stopped.stop) == (member, "alias")
    refused = walk_place(tried, every, crosses_call=lambda c: False)
    assert refused.node is call and refused.name is None


# ------------------------------------------------------------------ the ratchet


def _steps_through(value: ast.AST, var: str) -> bool:
    """Does `value` read `<var>.receiver` or `<var>.array`, directly or in a branch?"""
    return any(isinstance(n, ast.Attribute) and n.attr in _STEP_FIELDS
               and isinstance(n.value, ast.Name) and n.value.id == var
               for n in ast.walk(value))


def _hand_rolled_walks(source: str) -> set[str]:
    """The functions of `source` that walk a place chain by hand.

    Two shapes: a `while` loop that rebinds a name to its own `.receiver` / `.array`, and
    a function that calls itself on a `.receiver` / `.array`.
    """
    found: set[str] = set()
    tree = ast.parse(source)
    for fn in (n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for node in ast.walk(fn):
            if isinstance(node, ast.While):
                for stmt in ast.walk(node):
                    if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                            and isinstance(stmt.targets[0], ast.Name)
                            and _steps_through(stmt.value, stmt.targets[0].id)):
                        found.add(fn.name)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == fn.name
                    and any(isinstance(arg, ast.Attribute) and arg.attr in _STEP_FIELDS
                            for arg in node.args)):
                found.add(fn.name)
    return found


def _walks_in_the_tree() -> set[tuple[str, str]]:
    walks: set[tuple[str, str]] = set()
    for path in sorted(SEMANTICS.rglob("*.py")):
        if path == Path(places.__file__).resolve():
            continue
        rel = path.relative_to(REPO).as_posix()
        walks |= {(rel, fn) for fn in _hand_rolled_walks(path.read_text(encoding="utf-8"))}
    return walks


def test_the_detector_fires_on_both_shapes():
    """The always-fires control: without it, a zero below proves nothing."""
    looped = textwrap.dedent("""
        def root(expr):
            while isinstance(expr, (MemberAccess, IndexAccess)):
                expr = expr.array if isinstance(expr, IndexAccess) else expr.receiver
            return expr
    """)
    recursive = textwrap.dedent("""
        def spell(expr):
            if isinstance(expr, MemberAccess):
                return spell(expr.receiver) + "." + expr.member
            return expr.id
    """)
    assert _hand_rolled_walks(looped) == {"root"}
    assert _hand_rolled_walks(recursive) == {"spell"}


def test_the_adapters_step_through_the_step_fields():
    """The fields of `_STEP_FIELDS` are the fields that the two adapters step through, so the
    ratchet below looks for a second walk through each of them."""
    read: set[str] = set()
    for adapter in (places._ast_step, places._shir_step):
        tree = ast.parse(textwrap.dedent(inspect.getsource(adapter)))
        read |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                 and n.attr in _STEP_FIELDS}
    assert read == _STEP_FIELDS


def test_no_second_root_walk_under_semantics():
    walks = _walks_in_the_tree()
    new = sorted(walks - _KNOWN_WALKS - _NOT_A_ROOT_WALK)
    assert not new, (
        f"a hand-rolled place walk: {new}. Ask `semantics/places.py:walk_place` for the "
        "root, and pass an early stop as its `stop` / `crosses_call` parameter.")
    gone = sorted((_KNOWN_WALKS | _NOT_A_ROOT_WALK) - walks)
    assert not gone, f"{gone} no longer recurses through a receiver: remove the entry"
