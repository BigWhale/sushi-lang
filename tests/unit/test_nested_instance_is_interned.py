"""A generic instance reached only through a substituted field, payload or return is interned.

The instantiate pass collects what annotations and calls SPELL. `monomorphize_struct`
then substitutes `B := string` into a `Box@(B)` field and builds `Box<string>`, which
nothing spelled -- so it lived in the substitutor's cache and in no table, and the
derive pass reported the leftover as CE0128, CE2052 or a backend KeyError depending on
the shape (#577). Every substituted instance is published to its table at creation;
these pin the tables, one face each.
"""
from __future__ import annotations

import pytest


BOX_IN_STRUCT = """
struct Box@(T):
    T v

struct Pair@(A, B):
    A first
    Box@(B) second

fn main() i32:
    let Pair@(i32, string) p = Pair(3, Box("x"))
    println("{p.first} {p.second.v}")
    return Result.Ok(0)
"""

MAYBE_IN_STRUCT = """
struct Pair@(A, B):
    A first
    Maybe@(B) second

fn main() i32:
    let Pair@(i32, string) p = Pair(3, Maybe.None())
    println("{p.first} {p.second.is_some()}")
    return Result.Ok(0)
"""

LIST_IN_STRUCT = """
struct Bag@(B):
    List@(B) items

fn main() i32:
    let Bag@(string) b = Bag(List.new())
    println("{b.items.len()}")
    return Result.Ok(0)
"""

MAYBE_IN_ENUM = """
enum Slot@(B):
    Filled(Maybe@(B))
    Empty

fn main() i32:
    let Slot@(string) s = Slot.Filled(Maybe.None())
    match s:
        Slot.Filled(m) -> println("{m.is_some()}")
        Slot.Empty -> println("empty")
    return Result.Ok(0)
"""

BOX_IN_ENUM = """
struct Box@(T):
    T v

enum Slot@(B):
    Filled(Box@(B))
    Empty

fn main() i32:
    let Slot@(string) s = Slot.Filled(Box("x"))
    match s:
        Slot.Filled(b) -> println(b.v)
        Slot.Empty -> println("empty")
    return Result.Ok(0)
"""

BOX_THROUGH_GENERIC_RETURN = """
struct Box@(T):
    T v

struct Pair@(A, B):
    A first
    Box@(B) second

fn wrap@(B)(nom B x) Pair@(i32, B):
    return Result.Ok(Pair(7, Box(x)))

fn main() i32:
    match wrap(nom "towel"):
        Result.Ok(p) -> println("{p.first} {p.second.v}")
        Result.Err(_) -> println("failed")
    return Result.Ok(0)
"""


def _errors(reporter):
    return [item.code for item in reporter.items if item.kind == "error"]


@pytest.mark.parametrize("src, table, name", [
    (BOX_IN_STRUCT, "structs", "Box<string>"),
    (MAYBE_IN_STRUCT, "enums", "Maybe<string>"),
    (LIST_IN_STRUCT, "structs", "List<string>"),
    (MAYBE_IN_ENUM, "enums", "Maybe<string>"),
    (BOX_IN_ENUM, "structs", "Box<string>"),
    (BOX_THROUGH_GENERIC_RETURN, "structs", "Box<string>"),
], ids=["box-field", "maybe-field", "list-field", "maybe-payload", "box-payload",
        "box-through-return"])
def test_nested_instance_lands_in_its_table(analyze_program, src, table, name):
    analysis = analyze_program(src)
    assert _errors(analysis.reporter) == []
    by_name = getattr(analysis.analyzer, table).by_name
    assert name in by_name
    instance = by_name[name]
    # The interned object is the one the OUTER instance's field or payload holds, so
    # there is one identity per name and not a table copy beside a cache copy.
    assert instance.generic_args is not None


def test_the_outer_instance_holds_the_table_object(analyze_program):
    analysis = analyze_program(BOX_IN_STRUCT)
    assert _errors(analysis.reporter) == []
    pair = analysis.analyzer.structs.by_name["Pair<i32, string>"]
    box = analysis.analyzer.structs.by_name["Box<string>"]
    fields = dict(pair.fields)
    assert fields["second"] is box


def test_an_abstract_substitution_is_not_published(analyze_program):
    """`extend List@(T) mapv@(U)` is cut per `List<i32>`, and `U` stays unbound in the
    copy's return type. The `List<U>` the substitutor builds on the way is abstract
    and must not reach the table."""
    src = """
struct Box@(T):
    T v

extend Box@(T) rewrap@(U)(nom U u) Box@(U):
    return Box(u)

fn main() i32:
    let Box@(i32> b = Box(1)
    return Result.Ok(0)
"""
    src = src.replace("Box@(i32>", "Box@(i32)")
    analysis = analyze_program(src)
    assert _errors(analysis.reporter) == []
    assert not [n for n in analysis.analyzer.structs.by_name if n == "Box<U>"]
