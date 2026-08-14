"""Pass 2 stamps the return type of a method call, and the backend may rely on it.

This is the premise the chained-receiver fix stands on. `o.get().clone()` used to be
**CE0019 -- cannot determine language type for LLVM type**, because
`emit_receiver_value` (backend/expressions/calls/utils.py) resolved a semantic type
for a `Name` receiver and a `MemberAccess` receiver and for nothing else. The answer
was never missing; the backend was reading the wrong place. Pass 2 already computes it
and writes it onto the node as `inferred_return_type`
(semantics/passes/types/visitor.py, `visit_methodcall` / `visit_dotcall`).

Two properties matter, and only the second is obvious:

1. the stamp EXISTS on the receiver node after analysis, and
2. it IS the interned table entry, not a structurally equal rebuild.

(2) is the nominal-identity rule (#240): a `StructType`/`EnumType` is identified by its
name, the table is the sole authority for what that name means, and a rebuilt type is
the bug class that produced `CE2002: cannot assign Own@(T) to Own@(T)`. The backend
hands this stamp straight to `try_emit_struct_clone` / `try_emit_enum_clone`, which look
the builtin method up on the type object they are given, so a lookalike would silently
find nothing.

This test pins both properties so a later Pass 2 refactor cannot quietly drop the stamp
and reintroduce CE0019 in the backend, far from the cause. The `.sushi` corpus covers the
end-to-end behaviour (`tests/memory/test_own_get_*.sushi`,
`tests/memory/test_chained_clone_on_getout.sushi`).

`HashMap` is deliberately absent here: it is a virtual unit registered by
compiler/pipeline.py, which the semantics-only fixture does not replicate (the same
reason test_docs_stdlib_smoke.py skips it). `List` exercises the identical path.
"""
from __future__ import annotations

import dataclasses

import pytest

from sushi_lang.semantics.ast import DotCall, Expr, MethodCall
from sushi_lang.semantics.typesys import EnumType, StructType


OWN_GET = """\
struct Holder:
    i32 value

fn takes(Holder h) i32:
    return Result.Ok(h.value)

fn run() i32:
    let Holder h = Holder(1)
    let Own@(Holder) o = Own.alloc(h)
    let i32 v = takes(o.get().clone())??
    return Result.Ok(v)

fn main() i32:
    return Result.Ok(run().realise(99))
"""

LIST_GET = """\
fn run() i32:
    let List@(i32) xs = List.new()
    xs.push(7)
    let Maybe@(i32) got = xs.get(0).clone()
    return Result.Ok(got.realise(-1))

fn main() i32:
    return Result.Ok(run().realise(99))
"""


def _walk(node):
    """Every AST node reachable from `node`, parents before children."""
    if isinstance(node, Expr):
        yield node
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        children = [getattr(node, f.name) for f in dataclasses.fields(node)]
    elif isinstance(node, (list, tuple)):
        children = list(node)
    else:
        return
    for child in children:
        if isinstance(child, (list, tuple)):
            for item in child:
                yield from _walk(item)
        else:
            yield from _walk(child)


def _find_get_call(program):
    """The single `<receiver>.get()` call in the program."""
    calls = [n for n in _walk(program)
             if isinstance(n, (MethodCall, DotCall)) and n.method == "get"]
    assert len(calls) == 1, f"expected exactly one .get() call, found {len(calls)}"
    return calls[0]


def _assert_clean(analysis):
    errors = [d for d in analysis.reporter.items if getattr(d, "kind", None) == "error"]
    assert not errors, f"analysis reported {[getattr(d, 'code', '?') for d in errors]}"


def test_own_get_receiver_carries_its_struct_type(analyze_program):
    """`o.get()` on an `Own@(Holder)` stamps the interned `Holder`."""
    analysis = analyze_program(OWN_GET)
    _assert_clean(analysis)

    stamped = _find_get_call(analysis.program).inferred_return_type

    assert stamped is not None, "Pass 2 left the receiver of .clone() untyped"
    assert isinstance(stamped, StructType), f"expected a StructType, got {type(stamped)}"
    assert stamped.name == "Holder"
    # Nominal identity (#240): the stamp must BE the table entry.
    assert stamped is analysis.analyzer.structs.by_name["Holder"]


def test_list_get_receiver_carries_its_interned_maybe(analyze_program):
    """`xs.get(0)` on a `List@(i32)` stamps the interned `Maybe<i32>`.

    The enum half of the same rule. `try_emit_enum_clone` requires a real `EnumType`,
    so a `GenericTypeRef` spelling here would leave the backend with nothing to look a
    builtin `clone` up on.
    """
    analysis = analyze_program(LIST_GET)
    _assert_clean(analysis)

    stamped = _find_get_call(analysis.program).inferred_return_type

    assert stamped is not None, "Pass 2 left the receiver of .clone() untyped"
    assert isinstance(stamped, EnumType), f"expected an EnumType, got {type(stamped)}"
    assert stamped.name == "Maybe<i32>"
    assert stamped is analysis.analyzer.enums.by_name["Maybe<i32>"]


@pytest.mark.parametrize("src", [OWN_GET, LIST_GET], ids=["own", "list"])
def test_stamp_survives_to_the_end_of_analysis(analyze_program, src):
    """The stamp is on the tree the BACKEND receives, not on a discarded copy.

    Monomorphization (Pass 1.6) runs before Pass 2 and deep-copies expression nodes
    precisely so that later-pass annotations survive
    (semantics/generics/monomorphize/transformer.py). If that order ever inverts, the
    backend would read a stamp the monomorphizer had already thrown away, and the
    symptom would be CE0019 again.
    """
    analysis = analyze_program(src)
    _assert_clean(analysis)
    assert _find_get_call(analysis.program).inferred_return_type is not None
