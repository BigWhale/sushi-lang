"""Phase 3: a `.slib` carries the doc text of every symbol an author can document.

`docs/design/documentation.md` section 8 is the authority for the record and for what
deliberately has none. The rulings locked here are R2 (one builder), R3 (the key goes
where a record already exists), R4 (a private record carries no doc) and R9 (`unit_docs`
names our own units only).
"""
from __future__ import annotations

import re
import subprocess

import pytest

from sushi_lang.backend.library_format import LibraryFormat
from sushic_path import SUSHIC, SUSHIC_AVAILABLE

# Every position section 2 allows, in one library: the unit block, a constant, a struct
# and its fields, an enum and its variants, a concrete function with all four tags, a
# `nom` parameter, a perk and its method, a perk implementation and its method, and a
# generic function, struct and enum. `checked_jump` declares a custom error arm, which is
# the one thing a signature can say that the record used not to carry, and `spin_up`
# carries two examples and every inline mark the rendered subset knows.
#
# `hyperspace_jump` declares (b, a) while its tags document a then b: the report must
# print them in DECLARATION order, and the render test reads that from here.
DOC_LIB = """\
##:
A library that documents every position phase 3 can carry.

The unit block stands first in the file and documents no declaration.
:##

use <collections/strings>

##: The answer to life, the universe and everything. :##
public const i32 ANSWER = 42

##:
A point in the plane.

Two coordinates, and nothing else.
- Parameter x: a struct declares no parameters, so this is stored and never printed.
:##
public struct Point:
    ##: The distance along x. :##
    i32 x
    ##: The distance along y. :##
    i32 y

##:
How bright a shade is.

Every variant carries its own block.
:##
public enum Shade:
    ##: No data at all. :##
    Plain()
    ##: A brightness from 0 to 255. :##
    Custom(i32)

##:
Jumps through hyperspace.

The drive needs a warm coil.

The second paragraph of the body.
- Parameter a: The incoming argument.
- Parameter b: The second one, documented
  over two lines.
- Returns: The jump distance in parsecs.
- Errors: When the drive is cold, this fails.
:##
public fn hyperspace_jump(i32 b, i32 a) i32:
    return Result.Ok(a + b)

public fn plain_add(i32 a, i32 b) i32:
    return Result.Ok(a + b)

##: Hands a string back, and takes it over. :##
public fn shout(nom string s) string:
    return Result.Ok(s)

##: Doubles a number, and says so. :##
public perk Doubler:
    ##: Twice the receiver. :##
    fn doubled() i32

##: The i32 side of the doubler contract. :##
extend i32 with Doubler:
    ##: Twice the receiver, by multiplication. :##
    fn doubled() i32:
        return self * 2

##:
Picks the bigger of two doublers.

- Parameter a: The first candidate.
- Returns: Whichever doubles larger.
:##
public fn pick_bigger@(T: Doubler)(T a, T b) T:
    if (a.doubled() > b.doubled()):
        return Result.Ok(a)
    return Result.Ok(b)

##:
A box that holds one value.

The field block travels only inside the source slice.
:##
public struct Box@(T):
    ##: The value inside. :##
    T value

##: Either one thing or the other. :##
public enum Either@(T):
    ##: The left side. :##
    Left(T)
    ##: The right side. :##
    Right(T)

##: How a jump can fail. :##
public enum JumpError:
    ColdCoil

##:
Jumps, and says how it failed.

- Returns: The distance in parsecs.
- Errors: `JumpError.ColdCoil` when the coil is cold.
:##
public fn checked_jump(i32 factor) i32 | JumpError:
    if (factor < 1):
        return Result.Err(JumpError.ColdCoil)
    return Result.Ok(factor)

##:
Spins the drive up, and reports the `heat` it took.

The word **must** be read as a promise, and *not* as a suggestion. A lone * is
prose, and so is 2 * 3 * 4.

- Parameter turns: How many turns, at `10` each.
- Example: the everyday call.
```sushi
let i32 heat = spin_up(3).realise(0)
println("{heat}")
```
- Example: and a second one, whose caption runs over two lines and mentions
  `spin_up` on the second.
```sushi
println("{spin_up(1).realise(0)}")
```
:##
public fn spin_up(i32 turns) i32:
    return Result.Ok(turns * 10)
"""

_BLOCK = re.compile(r"^[ \t]*##:.*?:##[ \t]*\n", re.DOTALL | re.MULTILINE)


def strip_doc_blocks(source: str) -> str:
    """The same declarations with every doc block removed -- the undocumented twin.

    Derived rather than written out twice, so the two libraries cannot drift apart:
    what the size measurement compares has to be the same set of declarations.
    """
    plain = _BLOCK.sub("", source)
    assert "##:" not in plain, plain
    return plain


def build_library(tmp_path, name: str, source: str, kind: str = "source"):
    """Build one `.slib` and read its manifest back."""
    if not SUSHIC_AVAILABLE:
        pytest.skip("no compiler driver in this checkout")
    src = tmp_path / f"{name}.sushi"
    src.write_text(source, encoding="utf-8")
    out = tmp_path / f"{name}.slib"
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", kind, "--lib-version", "1.2.3",
         str(src), "-o", str(out)],
        cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    metadata, _bitcode = LibraryFormat.read(out)
    return out, metadata


@pytest.fixture(scope="module")
def documented(tmp_path_factory):
    _path, metadata = build_library(tmp_path_factory.mktemp("doclib"), "doclib", DOC_LIB)
    return metadata


@pytest.fixture(scope="module")
def undocumented(tmp_path_factory):
    _path, metadata = build_library(tmp_path_factory.mktemp("plainlib"), "plainlib",
                                    strip_doc_blocks(DOC_LIB))
    return metadata


def _named(records: list, key: str, name: str) -> dict:
    for record in records:
        if record.get(key) == name:
            return record
    raise AssertionError(f"no record with {key}={name!r} in {records}")


def _every_record(node):
    """Every dict anywhere in the manifest tree."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _every_record(value)
    elif isinstance(node, list):
        for item in node:
            yield from _every_record(item)


# -- the concrete records -------------------------------------------------------

def test_a_public_function_carries_every_field(documented):
    doc = _named(documented["public_functions"], "name", "hyperspace_jump")["doc"]
    assert doc["summary"] == "Jumps through hyperspace."
    assert doc["body"] == "The drive needs a warm coil.\n\nThe second paragraph of the body."
    assert doc["params"] == {
        "a": "The incoming argument.",
        "b": "The second one, documented\nover two lines.",
    }
    assert doc["returns"] == "The jump distance in parsecs."
    assert doc["errors"] == "When the drive is cold, this fails."


def test_a_public_constant_carries_its_block(documented):
    doc = _named(documented["public_constants"], "name", "ANSWER")["doc"]
    assert doc["summary"] == "The answer to life, the universe and everything."


def test_a_struct_and_its_fields_each_carry_one(documented):
    struct = _named(documented["structs"], "name", "Point")
    assert struct["doc"]["summary"] == "A point in the plane."
    assert _named(struct["fields"], "name", "x")["doc"]["summary"] == "The distance along x."
    assert _named(struct["fields"], "name", "y")["doc"]["summary"] == "The distance along y."


def test_an_enum_and_its_variants_each_carry_one(documented):
    enum = _named(documented["enums"], "name", "Shade")
    assert enum["doc"]["summary"] == "How bright a shade is."
    assert _named(enum["variants"], "name", "Plain")["doc"]["summary"] == "No data at all."
    assert _named(enum["variants"], "name", "Custom")["doc"]["summary"] == \
        "A brightness from 0 to 255."


# -- the template records -------------------------------------------------------

def test_a_generic_function_carries_its_block(documented):
    templates = documented["templates"]
    doc = _named(templates["generic_functions"], "name", "pick_bigger")["doc"]
    assert doc["summary"] == "Picks the bigger of two doublers."
    assert doc["params"] == {"a": "The first candidate."}
    assert doc["returns"] == "Whichever doubles larger."


def test_a_generic_struct_and_a_generic_enum_carry_theirs(documented):
    templates = documented["templates"]
    assert _named(templates["generic_structs"], "name", "Box")["doc"]["summary"] == \
        "A box that holds one value."
    assert _named(templates["generic_enums"], "name", "Either")["doc"]["summary"] == \
        "Either one thing or the other."


def test_a_perk_carries_its_block(documented):
    perk = _named(documented["templates"]["perks"], "name", "Doubler")
    assert perk["doc"]["summary"] == "Doubles a number, and says so."


def test_a_perk_implementation_and_each_method_carry_one(documented):
    impl = _named(documented["templates"]["perk_impls"], "perk", "Doubler")
    assert impl["doc"]["summary"] == "The i32 side of the doubler contract."
    method = _named(impl["methods"], "name", "doubled")
    assert method["doc"]["summary"] == "Twice the receiver, by multiplication."


# -- the unit map ---------------------------------------------------------------

def test_unit_docs_is_keyed_by_unit_name(documented):
    unit_docs = documented["unit_docs"]
    assert list(unit_docs) == ["doclib"]
    assert unit_docs["doclib"]["summary"] == \
        "A library that documents every position phase 3 can carry."
    assert unit_docs["doclib"]["body"] == \
        "The unit block stands first in the file and documents no declaration."


def test_every_unit_docs_key_is_in_the_unit_index(documented):
    """R9: the same `own_units` filter as the source section, so no bundled module leaks."""
    assert set(documented["unit_docs"]) <= set(documented["units"])


# -- what is absent -------------------------------------------------------------

def test_an_undocumented_symbol_in_a_documented_library_has_no_key(documented):
    assert "doc" not in _named(documented["public_functions"], "name", "plain_add")


def test_an_absent_field_is_absent_and_not_empty(documented):
    doc = _named(documented["public_functions"], "name", "shout")["doc"]
    assert doc == {"summary": "Hands a string back, and takes it over."}


def test_no_record_carries_the_whole_block(documented):
    """`DocBlock.text` is not serialized: the index must not carry its own input."""
    for record in _every_record(documented):
        assert "text" not in record, record


def test_a_private_closure_record_carries_no_doc(tmp_path):
    """R4: a closure-shipped generic is a private symbol, so it loses the key."""
    source = """\
##: The public entry point. :##
public fn twice@(T)(nom T x) T:
    return Result.Ok(helper(nom x)??)

##: A private helper, documented and never exported. :##
fn helper@(T)(nom T x) T:
    return Result.Ok(x)
"""
    _path, metadata = build_library(tmp_path, "privlib", source, kind="binary")
    private = _named(metadata["templates"]["generic_functions"], "name", "helper")
    assert private["private"] is True
    assert "doc" not in private
    assert _named(metadata["templates"]["generic_functions"], "name", "twice")["doc"]


def test_an_undocumented_library_carries_no_doc_key_anywhere(undocumented):
    assert "unit_docs" not in undocumented
    for record in _every_record(undocumented):
        assert "doc" not in record, record


# -- the examples key (phase 4, R23) --------------------------------------------

EXAMPLE_LIB = """\
##:
Doubles a number.

- Example: the plain one.
```sushi
let i32 d = doubled(21)??
println("{d}")
```
- Example: and one the harness must not run.
```sushi no_run
let i32 d = doubled(1)??
println("{d}")
```
:##
public fn doubled(i32 n) i32:
    return Result.Ok(n * 2)

##: Triples a number, and shows nothing. :##
public fn tripled(i32 n) i32:
    return Result.Ok(n * 3)
"""


@pytest.fixture(scope="module")
def exampled(tmp_path_factory):
    _path, metadata = build_library(tmp_path_factory.mktemp("examplelib"),
                                    "examplelib", EXAMPLE_LIB)
    return metadata


def test_the_examples_travel_in_source_order(exampled):
    """R48 gave the record the CAPTION as well, so an entry is a pair and not a string."""
    doc = _named(exampled["public_functions"], "name", "doubled")["doc"]
    assert [e["code"] for e in doc["examples"]] == [
        'let i32 d = doubled(21)??\nprintln("{d}")',
        'let i32 d = doubled(1)??\nprintln("{d}")',
    ]


def test_the_attributes_are_not_carried(exampled):
    """An attribute is a harness instruction, and a harness instruction is not docs."""
    doc = _named(exampled["public_functions"], "name", "doubled")["doc"]
    assert not any("no_run" in e["code"] for e in doc["examples"])


def test_a_block_with_no_example_has_no_examples_key(exampled):
    doc = _named(exampled["public_functions"], "name", "tripled")["doc"]
    assert doc == {"summary": "Triples a number, and shows nothing."}
