"""One answer to what a WRITTEN type names (#755).

The typecheck pass asked the same question in five places, and every one of them ended at
`semantics/type_resolution` by a route of its own. Two questions hide in those five:

* **what does this spelling NAME?** -- a lookup, and the table is its authority.
  `utils.resolve_declared_type` answers it, over `resolve_unknown_type` for a name and
  `resolve_type_recursively` for a type that CONTAINS one.
* **what does this spelling name, INTERNED?** -- a written `Result@(T, E)` or `Maybe@(T)`
  can name an entry nobody has built yet, so it goes through the one seam that may build
  one. `utils.intern_declared_wrapper` answers it, and a lookup answers the rest.

The routing rows below are what stops a sixth answer being written. The characterization
rows under them PIN the answer each site gives today: this ticket promises no behaviour
change, so each of them reads the same before the refactor and after it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_PASS_DIR = Path(__file__).resolve().parents[2] / "sushi_lang/semantics/passes/types"


def _items(reporter) -> list[tuple[str, str]]:
    return [(item.code, item.message) for item in reporter.items]


# --------------------------------------------------------------------------------------
# The routing: every WRITTEN wrapper is interned through one function.
# --------------------------------------------------------------------------------------

_WRAPPER_ROUTES = {
    "a let annotation": """
fn main() i32:
    let Result@(i32, StdError) r = true
    println("{r.is_ok()}")
    return Result.Ok(0)
""",
    "a stdlib function's return type": """
use <sys/env>

fn main() i32:
    let i32 v = getenv("HOME")
    println("{v}")
    return Result.Ok(0)
""",
    "a stdlib return type behind an alias": """
use <sys/env> as env

fn main() i32:
    let i32 v = env.getenv("HOME")
    println("{v}")
    return Result.Ok(0)
""",
    "a struct field's declared type": """
struct Holder:
    Result@(Maybe@(string), StdError) r

fn mk() Maybe@(string):
    return Result.Ok(Maybe.Some("hi"))

fn main() i32:
    let Holder h = Holder(mk())
    println("{h.r.is_ok()}")
    return Result.Ok(0)
""",
}


@pytest.mark.parametrize("position", sorted(_WRAPPER_ROUTES))
def test_every_written_wrapper_interns_through_one_function(analyze, monkeypatch, position):
    """Each position that interns a written wrapper reaches the one seam."""
    from sushi_lang.semantics.passes.types import utils

    seen: list[object] = []
    original = utils.intern_declared_wrapper

    def counted(validator, ty):
        seen.append(ty)
        return original(validator, ty)

    monkeypatch.setattr(utils, "intern_declared_wrapper", counted)
    analyze(_WRAPPER_ROUTES[position], name="m")
    assert seen, f"{position} interned a written wrapper without the seam"


def test_the_seam_answers_none_for_everything_that_is_not_a_written_wrapper():
    """The control: the seam is a FILTER, so a caller can tell a miss from an answer."""
    from sushi_lang.semantics.passes.types.utils import intern_declared_wrapper
    from sushi_lang.semantics.typesys import BuiltinType

    assert intern_declared_wrapper(None, BuiltinType.I32) is None
    assert intern_declared_wrapper(None, None) is None


def test_the_pass_holds_no_type_resolver_of_its_own():
    """`TypeResolver` is a wrapper over two functions the pass already calls directly."""
    scanned = sorted(_PASS_DIR.rglob("*.py"))
    assert len(scanned) > 30, "the scan found no pass modules -- it would pass vacuously"

    holders = [p.relative_to(_PASS_DIR).as_posix()
               for p in scanned if "TypeResolver" in p.read_text(encoding="utf-8")]
    assert holders == []


def test_the_type_resolver_name_is_still_findable():
    """The control for the row above: the spelling it looks for still exists."""
    home = _PASS_DIR.parents[1] / "type_resolution.py"
    assert "class TypeResolver" in home.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# The characterization: what each home answers today, read off the diagnostic that
# renders the resolved type.
# --------------------------------------------------------------------------------------

def test_a_written_result_annotation_resolves_to_its_interned_enum(analyze):
    source = """
fn main() i32:
    let Result@(i32, StdError) r = true
    println("{r.is_ok()}")
    return Result.Ok(0)
"""
    assert _items(analyze(source, name="m")) == [
        ("CE2002", "type mismatch: cannot assign bool to Result@(i32, StdError)")]


def test_a_written_maybe_annotation_resolves_to_its_interned_enum(analyze):
    source = """
fn main() i32:
    let Maybe@(i32) m = true
    println("{m.is_some()}")
    return Result.Ok(0)
"""
    assert _items(analyze(source, name="m")) == [
        ("CE2002", "type mismatch: cannot assign bool to Maybe@(i32)")]


def test_a_written_generic_struct_annotation_resolves_to_its_instance(analyze):
    source = """
struct Pair@(T, U):
    T first
    U second

fn main() i32:
    let Pair@(i32, string) p = true
    println("{p.first}")
    return Result.Ok(0)
"""
    assert _items(analyze(source, name="m")) == [
        ("CE2002", "type mismatch: cannot assign bool to Pair@(i32, string)")]


def test_an_array_of_a_written_name_resolves_its_element(analyze):
    """#284: `P[]` used to keep an UnknownType element and read `expected P, got P`."""
    source = """
struct P:
    i32 x

fn look(P[] arr) i32:
    return Result.Ok(arr.len())

fn main() i32:
    let i32 n = look(true).realise(0)
    println("{n}")
    return Result.Ok(0)
"""
    assert _items(analyze(source, name="m")) == [
        ("CE2006", "argument type mismatch at position 1: expected P[], got bool")]


def test_an_array_hashmap_key_is_still_read_at_the_annotation(analyze):
    """CE2058 is the `let`'s own question, not the resolver's: a key is WRITTEN there."""
    source = """
use <collections/hashmap>

fn main() i32:
    let HashMap@(i32[], string) m = HashMap.new()
    m.free()
    return Result.Ok(0)
"""
    codes = [code for code, _ in _items(analyze(source, name="m"))]
    assert codes == ["CE2058"]


def test_a_stdlib_return_type_resolves_to_its_interned_wrapper(analyze):
    source = """
use <sys/env>

fn main() i32:
    let i32 v = getenv("HOME")
    println("{v}")
    return Result.Ok(0)
"""
    assert _items(analyze(source, name="m")) == [
        ("CE2002", "type mismatch: cannot assign Maybe@(string) to i32")]


def test_a_stdlib_return_type_behind_an_alias_resolves_the_same(analyze):
    source = """
use <sys/env> as env

fn main() i32:
    let i32 v = env.getenv("HOME")
    println("{v}")
    return Result.Ok(0)
"""
    assert _items(analyze(source, name="m")) == [
        ("CE2002", "type mismatch: cannot assign Maybe@(string) to i32")]


def test_two_instances_of_one_generic_struct_compare_as_types(analyze):
    """The compatibility seam's own question: a written generic, looked up, and no more."""
    source = """
struct Pair@(T, U):
    T first
    U second

fn take(Pair@(i32, i32) p) i32:
    return Result.Ok(p.first)

fn main() i32:
    let Pair@(i32, string) q = Pair(1, "a")
    let i32 n = take(q).realise(0)
    println("{n}")
    return Result.Ok(0)
"""
    assert _items(analyze(source, name="m")) == [
        ("CE2006", "argument type mismatch at position 1: "
                   "expected Pair@(i32, i32), got Pair@(i32, string)")]


def test_a_match_binding_resolves_the_name_its_payload_carries(analyze):
    source = """
struct Point:
    i32 x

enum Shape:
    Circle(Point)
    Empty

fn main() i32:
    let Shape s = Shape.Circle(Point(3))
    match s:
        Shape.Circle(p) -> println("{p.zzz}")
        Shape.Empty -> println("none")
    return Result.Ok(0)
"""
    assert _items(analyze(source, name="m")) == [
        ("CE2106", "'Point' has no field 'zzz'")]


def test_a_match_binding_keeps_an_array_payload_whole(analyze):
    """The depth a pattern binding reads: the element is NOT walked into a field."""
    source = """
enum Bag:
    Items(i32[])
    Empty

fn main() i32:
    match Bag.Items(from([1, 2])):
        Bag.Items(nom xs) -> println("{xs.zzz}")
        Bag.Empty -> println("none")
    return Result.Ok(0)
"""
    assert _items(analyze(source, name="m")) == [
        ("CE2106", "'i32[]' has no field 'zzz'")]


def test_a_nested_written_wrapper_field_takes_the_call_that_fills_it(analyze):
    """#184 / #745: a `Result@(Maybe@(string), E)` field is interned, not rebuilt."""
    source = """
struct Holder:
    Result@(Maybe@(string), StdError) r

fn mk() Maybe@(string):
    return Result.Ok(Maybe.Some("hi"))

fn main() i32:
    let Holder h = Holder(mk())
    println("{h.r.is_ok()}")
    return Result.Ok(0)
"""
    assert _items(analyze(source, name="m")) == []
