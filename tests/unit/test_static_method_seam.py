"""One namespace behind a type's dot, and one seam that answers for it (#542).

Three gates:
  - the DECLARATION carries the marker and no receiver, for every target kind,
  - the shared predicates answer for every type kind, the built-ins included,
  - every refusal fires as ONE diagnostic, and no cell reports anything else.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.statics import (
    BUILTIN_STATICS,
    BUILTIN_TYPE_NAMES,
    builtin_type_named,
    is_builtin_static,
    names_a_type,
)
from sushi_lang.semantics.typesys import BuiltinType


# 1. The declaration

TARGETS = {
    "struct": "struct Box:\n    i32 n\n\nextend Box static make() Box:\n    return Box(1)\n",
    "enum": "enum Sign:\n    Plus\n\nextend Sign static up() Sign:\n    return Sign.Plus\n",
    "primitive": "extend f64 static of_int(i32 v) f64:\n    return v as f64\n",
    "generic": ("struct Cage@(T):\n    T item\n\n"
                "extend Cage@(T) static holding(T item) Cage@(T):\n    return Cage(item)\n"),
}


@pytest.mark.parametrize("kind", sorted(TARGETS))
def test_the_marker_reaches_the_declaration(kind):
    program, _tree = parse_to_ast(TARGETS[kind])
    ext = (program.extensions + program.generic_extensions)[0]
    assert ext.is_static
    assert ext.static_span is not None
    assert ext.self_mode is None


def test_an_unmarked_extension_is_not_static():
    program, _tree = parse_to_ast(
        "struct Box:\n    i32 n\n\nextend Box doubled() i32:\n    return self.n * 2\n")
    assert not program.extensions[0].is_static
    assert program.extensions[0].static_span is None


def test_the_declaration_slot_admits_a_keyword_method_name():
    """`new` is the idiomatic static name (R3), so the slot had to widen."""
    program, _tree = parse_to_ast(
        "struct Box:\n    i32 n\n\nextend Box static new(i32 n) Box:\n    return Box(n)\n")
    assert program.extensions[0].name == "new"


def test_static_is_a_reserved_word_and_not_a_method_name():
    """The cost of the marker, measured: `v.static()` is no longer writable."""
    from sushi_lang.internals.diagnostics import SyntaxDiagnostic

    with pytest.raises(SyntaxDiagnostic):
        parse_to_ast("fn main() i32:\n    let i32 n = 1\n    n.static()\n"
                     "    return Result.Ok(0)\n")


# 2. The shared type-name predicate

def test_every_primitive_but_blank_is_a_type_name():
    assert BUILTIN_TYPE_NAMES == {ty.value for ty in BuiltinType} - {"~"}
    assert builtin_type_named("f64") is BuiltinType.F64
    assert builtin_type_named("Box") is None


def test_a_declared_name_of_any_kind_is_a_type_name():
    assert names_a_type("i32")
    assert names_a_type("Box", structs={"Box"})
    assert names_a_type("Sign", enums={"Sign"})
    assert names_a_type("Cage", generic_structs={"Cage"})
    assert names_a_type("Maybe", generic_enums={"Maybe"})
    assert not names_a_type("Box")


def test_the_builtin_statics_are_named_in_one_table():
    """Ruling R3: one table, so the general path DEFERS rather than refusing."""
    assert is_builtin_static("List", "new")
    assert is_builtin_static("List", "with_capacity")
    assert is_builtin_static("HashMap", "new")
    assert is_builtin_static("Own", "alloc")
    assert is_builtin_static("f64", "from_bits")
    assert not is_builtin_static("List", "push")
    assert not is_builtin_static("Box", "new")
    assert not is_builtin_static(None, "new")
    assert set(BUILTIN_STATICS) == {"List", "HashMap", "Own", "f64", "f32"}


# 3. The refusals, through the real compiler

def _codes(analyze, source: str) -> set[str]:
    return {item.code for item in analyze(source).items if item.code.startswith("CE")}


RECEIVER_MODE = """\
struct Vec:
    i32 x

extend Vec static at(poke self) Vec:
    return Vec(1)
"""

SELF_IN_BODY = """\
struct Vec:
    i32 x

extend Vec static at() Vec:
    return Vec(self.x)
"""

IN_PERK_IMPL = """\
perk Named:
    fn name() string

struct Vec:
    i32 x

extend Vec with Named:
    static fn name() string:
        return "vec"
"""

VARIANT_COLLISION = """\
enum Shape:
    Circle

extend Shape static Circle() Shape:
    return Shape.Circle
"""

NO_SUCH_STATIC = """\
struct Box:
    i32 x

fn main() i32:
    let Box b = Box.build()
    println("{b.x}")
    return Result.Ok(0)
"""

INSTANCE_ON_THE_TYPE = """\
struct Vec:
    i32 x

extend Vec doubled() i32:
    return self.x * 2

fn main() i32:
    let i32 n = Vec.doubled()
    println("{n}")
    return Result.Ok(0)
"""

STATIC_ON_A_VALUE = """\
struct Vec:
    i32 x

extend Vec static at(i32 x) Vec:
    return Vec(x)

fn main() i32:
    let Vec a = Vec.at(1)
    let Vec b = a.at(2)
    println("{b.x}")
    return Result.Ok(0)
"""

ARRAY_TARGET = """\
extend i32[] static two() i32[]:
    return from([1, 2])
"""

ARRAY_TEMPLATE_TARGET = """\
extend T[] static two() T[]:
    return from([])
"""

UNSTAMPED_GENERIC = """\
struct Cage@(T):
    T item

extend Cage@(T) static holding(T item) Cage@(T):
    return Cage(item)

fn main() i32:
    println("{Cage.holding(9).item}")
    return Result.Ok(0)
"""

UNSTAMPED_GENERIC_FOREIGN_RETURN = """\
struct Cage@(T):
    T item

extend Cage@(T) static describing(T item) i32:
    return 1

fn main() i32:
    println("{Cage.describing(9)}")
    return Result.Ok(0)
"""

REFUSALS = {
    "CE2060": (UNSTAMPED_GENERIC, UNSTAMPED_GENERIC_FOREIGN_RETURN),
    "CE0134": (RECEIVER_MODE, SELF_IN_BODY),
    "CE2104": (ARRAY_TARGET, ARRAY_TEMPLATE_TARGET),
    "CE4014": (IN_PERK_IMPL,),
    "CE2103": (VARIANT_COLLISION,),
    "CE2102": (NO_SUCH_STATIC, INSTANCE_ON_THE_TYPE),
    "CE2008": (STATIC_ON_A_VALUE,),
}


@pytest.mark.parametrize("code,sources", sorted(REFUSALS.items()))
def test_each_refusal_is_the_only_diagnostic(analyze, code, sources):
    """One fault, one code. A cascade here means a path did not stop."""
    for source in sources:
        assert _codes(analyze, source) == {code}, source


ACCEPTED = """\
struct Vec:
    i32 x
    i32 y

extend Vec static at(i32 x, i32 y) Vec:
    return Vec(x, y)

extend Vec sum() i32:
    return self.x + self.y

fn main() i32:
    let Vec v = Vec.at(3, 4)
    println("{v.sum()}")
    return Result.Ok(0)
"""


def test_a_static_beside_an_instance_method_is_accepted(analyze):
    """Different names, one type: both shapes coexist and neither is refused."""
    assert _codes(analyze, ACCEPTED) == set()


UNSTAMPED_VARIANT = """\
fn make() i32:
    return Result.Ok(0)

fn main() i32:
    println("{make().realise(-1)}")
    return Result.Ok(0)
"""


def test_a_variant_in_an_unstamped_position_is_untouched(analyze):
    """CE2060 fires only when the base name declares a static of THAT name.

    `Result.Ok(0)` in a return is a generic enum with the stamp supplied by the
    surrounding statement; reading it as a static with no instantiation would refuse
    every one of the corpus's 6,559 sites.
    """
    assert _codes(analyze, UNSTAMPED_VARIANT) == set()
