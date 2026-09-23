"""One walk decides the root of a place (#784): `semantics/places.py:walk_place`.

Two gates. The walk is TOTAL: every node of the `Expr` union is either a step it crosses
or a kind that ends it, never both and never neither. And the walk is ONE: a loop or a
recursion anywhere else under `semantics/` that steps through `.receiver` / `.array` is a
second root walk, and it is refused.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import typing
from pathlib import Path

from sushi_lang.semantics import ast as sushi_ast
from sushi_lang.semantics import places
from sushi_lang.semantics.places import NOT_A_STEP, Step, walk_place

SEMANTICS = Path(places.__file__).resolve().parent
REPO = SEMANTICS.parent.parent

_STEP_FIELDS = {"receiver", "array"}

# A root walk this gate may not remove, with the reason. The list may only shrink: an
# entry that is no longer found fails the gate too, so it cannot outlive its walk.
_KNOWN_WALKS: set[tuple[str, str]] = set()

# A recursion through `.receiver` / `.array` that answers another question than the root:
# it visits every node, so it recurses through a receiver like through any other child.
_NOT_A_ROOT_WALK = {
    ("sushi_lang/semantics/ast_walk.py", "_expr_types"),              # every type a node names
    ("sushi_lang/semantics/passes/borrow/diagnostics.py", "expr_to_string"),  # the source text
    ("sushi_lang/semantics/passes/borrow/expressions.py", "check_expr"),  # the borrow dispatch
    ("sushi_lang/semantics/passes/borrow/reads.py", "read_type"),     # the type a read yields
}


def _expr_union() -> set[str]:
    return {t.__name__ for t in typing.get_args(sushi_ast.Expr)}


def _stepped_kinds() -> set[str]:
    """Every class a `case` arm of `walk_place` crosses."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(walk_place)))
    names: set[str] = set()
    for case in (n for n in ast.walk(tree) if isinstance(n, ast.match_case)):
        for node in ast.walk(case.pattern):
            if isinstance(node, ast.MatchClass) and isinstance(node.cls, ast.Name):
                names.add(node.cls.id)
    return names


def test_every_expression_kind_is_a_step_or_an_end():
    ends = {t.__name__ for t in NOT_A_STEP}
    steps = _stepped_kinds()
    assert steps, "the gate read no arm from walk_place -- it is reading the wrong source"
    assert not steps & ends, f"a kind is both a step and an end: {sorted(steps & ends)}"
    missing = sorted(_expr_union() - steps - ends)
    assert not missing, (
        f"walk_place does not classify {missing}. Add the kind to NOT_A_STEP, or give it "
        "an arm if a place chain continues through it.")
    stray = sorted((steps | ends) - _expr_union())
    assert not stray, f"walk_place names a kind outside the Expr union: {stray}"


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
    assert _hand_rolled_walks(inspect.getsource(places)) == {"walk_place"}


def test_no_second_root_walk_under_semantics():
    walks = _walks_in_the_tree()
    new = sorted(walks - _KNOWN_WALKS - _NOT_A_ROOT_WALK)
    assert not new, (
        f"a hand-rolled place walk: {new}. Ask `semantics/places.py:walk_place` for the "
        "root, and pass an early stop as its `stop` / `crosses_call` parameter.")
    gone = sorted((_KNOWN_WALKS | _NOT_A_ROOT_WALK) - walks)
    assert not gone, f"{gone} no longer recurses through a receiver: remove the entry"
