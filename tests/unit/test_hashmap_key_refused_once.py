"""The HashMap key rules are read once, at every WRITTEN `HashMap@(K, V)` type (#773).

CE2054, CE2055 and CE2058 are rules on a KEY TYPE, and a key type is written in a type:
a `let`, a `var`, a field, a payload, a parameter, a return type, and anywhere inside
another written type. They are read there, over the one check every written type takes
(`validate_type_name`), and never at `HashMap.new()`. One fault gives one diagnostic by
construction, and a field or a parameter that no construction reaches is refused too.

A `.sushi` fixture cannot count a code (`EXPECT_ERROR_CODE` is a substring test), so the
count and the span are asserted here, on the analyze path.
"""
from __future__ import annotations

import pytest

_KEY_CODES = frozenset({"CE2054", "CE2055", "CE2058"})

_POSITIONS = {
    "a let over a construction": ("""use <collections/hashmap>

fn main() i32:
    let HashMap@(i32[], string) m = HashMap.new()
    return Result.Ok(0)
""", [("CE2058", 4, 9)]),
    "a let over a call": ("""use <collections/hashmap>

fn mk() HashMap@(i32[], string):
    return Result.Ok(HashMap.new())

fn take() i32:
    let HashMap@(i32[], string) m = mk()??
    return Result.Ok(m.len())

fn main() i32:
    return Result.Ok(0)
""", [("CE2058", 3, 9), ("CE2058", 7, 9)]),
    "a rebind": ("""use <collections/hashmap>

fn main() i32:
    let HashMap@(i32[], string) m = HashMap.new()
    m := HashMap.new()
    return Result.Ok(0)
""", [("CE2058", 4, 9)]),
    "a unit variable": ("""use <collections/hashmap>

var HashMap@(i32[], string) cache = HashMap.new()

fn main() i32:
    return Result.Ok(0)
""", [("CE2058", 3, 5)]),
    "a struct field and its construction": ("""use <collections/hashmap>

struct Holder:
    HashMap@(i32[], string) m

fn main() i32:
    let Holder h = Holder(HashMap.new())
    return Result.Ok(0)
""", [("CE2058", 4, 5)]),
    "a struct field no construction reaches": ("""use <collections/hashmap>

struct Holder:
    HashMap@(i32[], string) m

fn main() i32:
    return Result.Ok(0)
""", [("CE2058", 4, 5)]),
    "an enum payload": ("""use <collections/hashmap>

enum Slot:
    Full(HashMap@(i32[], string))
    Empty

fn main() i32:
    return Result.Ok(0)
""", [("CE2058", 4, 5)]),
    "a parameter": ("""use <collections/hashmap>

fn look(HashMap@(i32[], string) m) i32:
    return Result.Ok(m.len())

fn main() i32:
    let i32 n = look(HashMap.new()).realise(0)
    return Result.Ok(n)
""", [("CE2058", 3, 9)]),
    "a return type": ("""use <collections/hashmap>

fn mk() HashMap@(i32[], string):
    return Result.Ok(HashMap.new())

fn main() i32:
    return Result.Ok(0)
""", [("CE2058", 3, 9)]),
    "a perk contract": ("""use <collections/hashmap>

perk Keeps:
    fn keep(HashMap@(i32[], string) m) i32

fn main() i32:
    return Result.Ok(0)
""", [("CE2058", 4, 13)]),
    "an extension method parameter": ("""use <collections/hashmap>

extend i32 count(HashMap@(i32[], string) m) i32:
    return m.len()

fn main() i32:
    return Result.Ok(0)
""", [("CE2058", 3, 18)]),
    "nested in a Maybe": ("""use <collections/hashmap>

fn main() i32:
    let Maybe@(HashMap@(i32[], string)) m = Maybe.None
    return Result.Ok(0)
""", [("CE2058", 4, 9)]),
    "nested in a List": ("""use <collections/hashmap>

fn look(List@(HashMap@(i32[], string)) l) i32:
    return Result.Ok(l.len())

fn main() i32:
    return Result.Ok(0)
""", [("CE2058", 3, 9)]),
    "nested in a function type": ("""use <collections/hashmap>

fn apply(fn(HashMap@(i32[], string)) -> i32 f) i32:
    return Result.Ok(0)

fn main() i32:
    return Result.Ok(0)
""", [("CE2058", 3, 10)]),
    "a generic struct instantiated at an array key": ("""use <collections/hashmap>

struct Box@(T):
    HashMap@(T, string) m

fn main() i32:
    let Box@(i32[]) b = Box(HashMap.new())
    return Result.Ok(0)
""", [("CE2058", 7, 9)]),
    "a key with no equality": ("""use <collections/hashmap>

fn main() i32:
    let HashMap@(List@(i32), i32) m = HashMap.new()
    return Result.Ok(0)
""", [("CE2055", 4, 9)]),
    "a key with no hash": ("""use <collections/hashmap>

fn look(HashMap@(HashMap@(i32, i32), i32) m) i32:
    return Result.Ok(0)

fn main() i32:
    return Result.Ok(0)
""", [("CE2054", 3, 9)]),
}


@pytest.mark.parametrize("position", list(_POSITIONS))
def test_one_bad_key_answers_once_at_the_written_type(analyze, position):
    source, expected = _POSITIONS[position]
    found = [(item.code, item.span.line, item.span.col)
             for item in analyze(source, name="m").items if item.code in _KEY_CODES]
    assert found == expected
