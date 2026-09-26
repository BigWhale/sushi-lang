"""A built-in method called with the wrong number of arguments is CE2009, like every callee.

One fault answered four codes in three text shapes: CE2053 for a `List@(T)`, CE2016 for
a `HashMap`, an `Own` and a `Maybe`, CE2502 for `Result.realise()`, and CE2009 for an
array, a string, a derived method and a function (#799). The count of a built-in family
is one table on the family (`MethodFamily.arity`, `passes/types/method_registry.py`), read
before the family's own check.

`EXPECT_ERROR_CODE` is a substring test and cannot count, so the code SET is asserted
here, on the analyze path: one miscount answers exactly one CE2009.
"""
from __future__ import annotations

import pytest

_PRELUDE = """use <collections/hashmap>
use <collections/strings>

struct Point:
    i32 x

enum Colour:
    Red
    Blue

fn main() i32:
    let List@(i32) l = List.new()
    let HashMap@(i32, i32) m = HashMap.new()
    let Own@(i32) o = Own.alloc(1)
    let Maybe@(i32) mb = Maybe.Some(1)
    let Result@(i32, StdError) r = Result.Ok(1)
    let i32[] arr = from([1, 2])
    let string s = "Mostly Harmless"
    let Point p = Point(1)
    let Colour c = Colour.Red
    let fn(i32) -> i32 f = |i32 x| x
"""

_TAIL = """    return Result.Ok(0)
"""

#: One miscount per family, and the callee the text names.
_MISCOUNTS = {
    "a List method": ("let i32 a = l.len(3)", "List@(i32).len"),
    "a List method with an element": ("l.push()", "List@(i32).push"),
    "a List static": ("let List@(i32) a = List.with_capacity()", "List.with_capacity"),
    "a List static with no argument": ("let List@(i32) a = List.new(3)", "List.new"),
    "a HashMap method": ("let i32 a = m.len(3)", "HashMap@(i32, i32).len"),
    "a HashMap method with a key": ("m.insert(1)", "HashMap@(i32, i32).insert"),
    "a HashMap static": ("let HashMap@(i32, i32) a = HashMap.new(3)", "HashMap.new"),
    "an Own method": ("let i32 a = o.get(3)", "Own@(i32).get"),
    "a Maybe method": ("let i32 a = mb.realise(1, 2)", "Maybe@(i32).realise"),
    "a Maybe predicate": ("let bool a = mb.is_some(1)", "Maybe@(i32).is_some"),
    "a Result realise": ("let i32 a = r.realise(1, 2)", "Result@(i32, StdError).realise"),
    "a Result predicate": ("let bool a = r.is_ok(1)", "Result@(i32, StdError).is_ok"),
    "an array method": ("let i32 a = arr.len(3)", "i32[].len"),
    "a string method": ("let i32 a = s.len(3)", "string.len"),
    "a derived struct hash": ("let u64 a = p.hash(3)", "Point.hash"),
    "a derived struct clone": ("let Point a = p.clone(3)", "Point.clone"),
    "a derived enum clone": ("let Colour a = c.clone(3)", "Colour.clone"),
}


@pytest.mark.parametrize("case", list(_MISCOUNTS))
def test_a_builtin_miscount_answers_one_ce2009(analyze, case):
    line, callee = _MISCOUNTS[case]
    items = analyze(_PRELUDE + "    " + line + "\n" + _TAIL, name="ar").items
    errors = [item for item in items if item.code.startswith("CE")]
    assert [item.code for item in errors] == ["CE2009"], [
        (item.code, item.message) for item in errors]
    assert f"'{callee}'" in errors[0].message, errors[0].message


def test_the_retired_codes_are_not_registered():
    from sushi_lang.internals.errors import REGISTRY
    assert not {"CE2016", "CE2053", "CE2502"} & set(REGISTRY)
