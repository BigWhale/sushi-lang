"""The derive pass's table belongs to one compilation, not to the process (#601).

Type identity is nominal, so two programs that each declare a `Point` name one key. A
module-level table therefore handed the second program the first one's method -- an
emitter closed over the first one's fields, and a membership answer the second program
never gave.
"""
from __future__ import annotations

from sushi_lang.semantics.typesys import StructType
from sushi_lang.sushi_stdlib.src.common import builtin_registry

ONE_FIELD = """
struct Point:
    i32 x

fn main() i32:
    let Point p = Point(1)
    println("{p.hash()}")
    return Result.Ok(0)
"""

THREE_FIELDS = """
struct Point:
    i32 x
    i32 y
    i32 z

fn main() i32:
    let Point p = Point(1, 2, 3)
    println("{p.hash()}")
    return Result.Ok(0)
"""

UNHASHABLE = """
unsafe external "C" as libc because "a foreign handle is unhashable":
    fn malloc(i64 n) ptr                  = "malloc"

struct Point:
    ptr handle

fn main() i32:
    let Point p = Point(libc.malloc(8 as i64))
    return Result.Ok(0)
"""

POINT = StructType(name="Point", fields=[])


def _derived(analysis, name: str):
    """The method one analysis derived for `Point`."""
    return analysis.analyzer.tables.derived_methods.get_method(POINT, name)


def test_each_compilation_derives_its_own_method(analyze_program):
    """Two `Point` declarations in one process get two methods."""
    one = analyze_program(ONE_FIELD, name="one")
    three = analyze_program(THREE_FIELDS, name="three")

    assert _derived(one, "hash") is not None
    assert _derived(three, "hash") is not None
    assert _derived(one, "hash") is not _derived(three, "hash")
    assert _derived(one, "clone") is not _derived(three, "clone")


def test_a_derived_method_never_reaches_the_process_table(analyze_program):
    """The process-wide table holds the primitive families and nothing a program declares."""
    analyze_program(ONE_FIELD, name="one")

    assert builtin_registry.get_method(POINT, "hash") is None
    assert builtin_registry.get_method(POINT, "clone") is None


def test_an_earlier_compilation_does_not_make_a_type_hashable(analyze_program):
    """A `Point` of a foreign handle has no hash, however many hashable ones came first."""
    analyze_program(ONE_FIELD, name="one")
    unhashable = analyze_program(UNHASHABLE, name="two")

    assert _derived(unhashable, "hash") is None
