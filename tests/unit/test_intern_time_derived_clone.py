"""Every interned enum carries a derived clone, and the intern is what supplies it (#720).

The `derive` pass runs before `typecheck`, so an instance the typecheck pass interns is
behind it. The interning seams for `Result` and `Maybe` therefore derive their own
methods, and a clone that arrives instead from a later whole-table walk is a second
supplier for one fact -- a supplier a program that drives no late intern never runs.

`List`, `Own` and `HashMap` are the deliberate absence, at both ends: `cloning.py` returns
early on `CONTAINER_BASES` and the backend passes `exclude_containers=True`, because
each keeps its own method path. They are structs, so the invariant below reads the enums.
"""
from __future__ import annotations

from sushi_lang.semantics.derived_methods import BuiltinMethodRegistry

# The wrapper is NAMED, so the annotation interns `Maybe<Cell>` before the derive pass.
NAMED_WRAPPER = """
struct Cell:
    string tag

fn main() i32:
    let List@(Cell) a = List.new()
    a.push(Cell("one"))
    let Maybe@(Cell) m = a.get(0)
    let Cell c = m.realise(Cell('-'))
    println("{c.tag}")
    return Result.Ok(0)
"""

# The same wrapper, spelled nowhere: the typecheck pass interns it, after the derive pass.
CHAINED_WRAPPER = """
struct Cell:
    string tag

fn main() i32:
    let List@(Cell) a = List.new()
    a.push(Cell("one"))
    println("{a.get(0)??.tag}")
    return Result.Ok(0)
"""

# The channel of a method-level type parameter: the call site solves `U` to `Cell`, so
# `Result<Cell, StdError>` is spelled nowhere and the copy interns it after derive ran.
LATE_RESULT = """
struct Cell:
    string tag

extend List@(T) grab@(U)(fn(T) -> U f) U | StdError:
    foreach(x in self.iter()):
        return f(x)??
    return Result.Err(StdError.Error)

fn main() i32:
    let List@(i32) a = List.new()
    a.push(1)
    let Cell c = a.grab(|i32 n| Cell("x{n}"))??
    println("{c.tag}")
    return Result.Ok(0)
"""


def _own(analysis, enum_type, method: str):
    """This compilation's derived method, past the process-wide built-ins."""
    derived = analysis.analyzer.tables.derived_methods
    return BuiltinMethodRegistry.get_method(derived, enum_type, method)


def _enums(analysis):
    """Every enum the analysis interned, in table order."""
    enums = analysis.analyzer.tables.enums
    return [(name, enums.by_name[name]) for name in enums.order]


def _without_clone(analysis):
    """The interned enums this compilation derived no clone for."""
    return [name for name, ty in _enums(analysis) if _own(analysis, ty, "clone") is None]


def test_a_named_wrapper_carries_both(analyze_program):
    """The control: an annotation interns before the derive pass, and both arrive."""
    analysis = analyze_program(NAMED_WRAPPER, name="named")
    cell = analysis.analyzer.tables.enums.by_name["Maybe<Cell>"]

    assert _own(analysis, cell, "hash") is not None
    assert _own(analysis, cell, "clone") is not None


def test_a_chained_wrapper_carries_both(analyze_program):
    """The same `Maybe<Cell>`, interned during typecheck, is not allowed to differ."""
    analysis = analyze_program(CHAINED_WRAPPER, name="chained")
    cell = analysis.analyzer.tables.enums.by_name["Maybe<Cell>"]

    assert _own(analysis, cell, "hash") is not None
    assert _own(analysis, cell, "clone") is not None


def test_a_late_solved_result_carries_a_clone(analyze_program):
    """A Result the intern seam builds after the derive pass carries its own clone."""
    analysis = analyze_program(LATE_RESULT, name="late")

    assert "Result<Cell, StdError>" in analysis.analyzer.tables.enums.by_name
    late = analysis.analyzer.tables.enums.by_name["Result<Cell, StdError>"]
    assert _own(analysis, late, "clone") is not None


def test_no_interned_enum_is_left_without_a_clone(analyze_program):
    """The invariant: a clone is total over enums, whenever the intern happened."""
    for source, name in ((NAMED_WRAPPER, "named"), (CHAINED_WRAPPER, "chained"),
                         (LATE_RESULT, "late")):
        analysis = analyze_program(source, name=name)
        assert _without_clone(analysis) == []
