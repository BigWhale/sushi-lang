"""Pass 2 stamps the return type of a method call, and the backend may rely on it."""
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
    """`xs.get(0)` on a `List@(i32)` stamps the interned `Maybe<i32>`."""
    analysis = analyze_program(LIST_GET)
    _assert_clean(analysis)

    stamped = _find_get_call(analysis.program).inferred_return_type

    assert stamped is not None, "Pass 2 left the receiver of .clone() untyped"
    assert isinstance(stamped, EnumType), f"expected an EnumType, got {type(stamped)}"
    assert stamped.name == "Maybe<i32>"
    assert stamped is analysis.analyzer.enums.by_name["Maybe<i32>"]


@pytest.mark.parametrize("src", [OWN_GET, LIST_GET], ids=["own", "list"])
def test_stamp_survives_to_the_end_of_analysis(analyze_program, src):
    """The stamp is on the tree the BACKEND receives, not on a discarded copy."""
    analysis = analyze_program(src)
    _assert_clean(analysis)
    assert _find_get_call(analysis.program).inferred_return_type is not None


# ---------------------------------------------------------------------------
# The INDEXED receiver (#286). The same premise, one node type over: the backend
# reads a stamp rather than re-deriving, so the stamp has to be there.
# ---------------------------------------------------------------------------

INDEX_STRUCT = """\
struct Row:
    i32 n

fn run() u64:
    let Row[] rows = from([Row(1), Row(2)])
    return Result.Ok(rows[0].hash())

fn main() i32:
    return Result.Ok(0)
"""

INDEX_ENUM = """\
enum E:
    A
    B(i32)

fn run() u64:
    let E[] es = from([E.A, E.B(2)])
    return Result.Ok(es[1].hash())

fn main() i32:
    return Result.Ok(0)
"""


def _find_index_access(program):
    """The single `<array>[i]` expression in the program."""
    from sushi_lang.semantics.ast import IndexAccess

    found = [n for n in _walk(program) if isinstance(n, IndexAccess)]
    assert len(found) == 1, f"expected exactly one index access, found {len(found)}"
    return found[0]


def test_indexed_struct_receiver_carries_its_element_type(analyze_program):
    """`rows[0]` on a `Row[]` stamps the interned `Row`."""
    analysis = analyze_program(INDEX_STRUCT)
    _assert_clean(analysis)

    stamped = _find_index_access(analysis.program).inferred_element_type

    assert stamped is not None, "Pass 2 left the indexed receiver untyped"
    assert isinstance(stamped, StructType), f"expected a StructType, got {type(stamped)}"
    assert stamped.name == "Row"
    assert stamped is analysis.analyzer.structs.by_name["Row"]


def test_indexed_enum_receiver_carries_its_element_type(analyze_program):
    """The enum half. Both element kinds failed; both are pinned."""
    analysis = analyze_program(INDEX_ENUM)
    _assert_clean(analysis)

    stamped = _find_index_access(analysis.program).inferred_element_type

    assert stamped is not None, "Pass 2 left the indexed receiver untyped"
    assert isinstance(stamped, EnumType), f"expected an EnumType, got {type(stamped)}"
    assert stamped.name == "E"
    assert stamped is analysis.analyzer.enums.by_name["E"]
