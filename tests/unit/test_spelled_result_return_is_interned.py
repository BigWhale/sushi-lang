"""A spelled `Result@(T, E)` return reaches the backend as the INTERNED enum (#857).

The `resolve` pass interns the spelled return through `intern_wrapper_enum` and stamps
it on `FuncDef.resolved_result`. The declaration keeps the type as written, because the
typecheck pass still has to rule on a qualified name in it. The backend then has one
reader, `declared_result_of`, and the prototype has no structural arm of its own.
"""
from __future__ import annotations

import dataclasses
import inspect
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sushi_lang.backend.functions import declarations, helpers
from sushi_lang.internals.errors import InternalCompilerError
from sushi_lang.semantics.ast import FuncDef
from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import BuiltinType, EnumType, UnknownType

TESTS_DIR = Path(__file__).resolve().parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_metadata import collect_fixtures  # noqa: E402

SPELLED_HEADER = re.compile(r"^\s*(public\s+)?fn\s+\w+\(.*\)\s+Result@\(", re.MULTILINE)


def _spells_result(fn) -> bool:
    return isinstance(fn.ret, GenericTypeRef) and fn.ret.base_name == "Result"


def _spelled_functions(analysis):
    """Every function of every unit whose return is a spelled `Result@(T, E)`."""
    found = []
    for unit in analysis.analyzer.unit_manager.units.values():
        if unit.ast is None:
            continue
        found.extend(fn for fn in unit.ast.functions if _spells_result(fn))
    return found


def _assert_stamped(analysis) -> int:
    enums = analysis.analyzer.tables.enums.by_name
    spelled = _spelled_functions(analysis)
    for fn in spelled:
        stamp = fn.resolved_result
        assert isinstance(stamp, EnumType), f"{fn.name}: no interned Result, got {stamp!r}"
        assert enums.get(stamp.name) is stamp, f"{fn.name}: {stamp.name} is not the table entry"
    return len(spelled)


def test_the_prototype_has_no_spelled_result_arm():
    source = inspect.getsource(declarations)
    assert "GenericTypeRef" not in source
    assert "prototype_result_of" not in source


def test_the_one_reader_has_no_spelled_result_arm():
    assert "GenericTypeRef" not in inspect.getsource(helpers.declared_result_of)


@pytest.mark.parametrize("node", [FuncDef, GenericFuncDef])
def test_every_function_node_the_backend_declares_carries_the_stamp(node):
    # A monomorphized instance is a copy of a GenericFuncDef, and the backend declares
    # it through the same reader.
    assert "resolved_result" in {f.name for f in dataclasses.fields(node)}


def test_the_reader_answers_the_stamp():
    interned = EnumType(name="Result<i32, E>", variants=(), generic_base="Result")
    spelled = GenericTypeRef(base_name="Result",
                             type_args=(BuiltinType.I32, UnknownType(name="E")))
    fn = SimpleNamespace(name="f", ret=spelled, err_type=None, resolved_result=interned)
    assert helpers.declared_result_of(object(), fn) is interned


def test_an_unstamped_spelled_return_is_an_internal_error():
    spelled = GenericTypeRef(base_name="Result",
                             type_args=(BuiltinType.I32, UnknownType(name="E")))
    fn = SimpleNamespace(name="f", ret=spelled, err_type=None, resolved_result=None)
    with pytest.raises(InternalCompilerError) as caught:
        helpers.declared_result_of(object(), fn)
    assert "CE0015" in str(caught.value)


SOURCES = {
    "plain": """
enum MyErr:
    Bad

fn spelled(i32 x) Result@(i32, MyErr):
    return Result.Ok(x)

fn main() i32:
    println("{spelled(1).realise(0)}")
    return Result.Ok(0)
""",
    "nested": """
enum Inner:
    A
enum Outer:
    B

fn inner() Result@(i32, Inner):
    return Result.Ok(1)

fn outer() Result@(Result@(i32, Inner), Outer):
    return Result.Ok(inner())

fn main() i32:
    let Result@(i32, Inner) r = outer().realise(Result.Err(Inner.A))
    println("{r.realise(0)}")
    return Result.Ok(0)
""",
    "generic_payloads": """
struct Pair@(T, U):
    T first
    U second

enum MyErr@(T):
    Bad(T)

fn pair() Result@(Pair@(i32, string), MyErr@(i32)):
    return Result.Ok(Pair(1, "one"))

fn find() Result@(Maybe@(i32), MyErr@(i32)):
    return Result.Ok(Maybe.Some(3))

fn main() i32:
    let Pair@(i32, string) p = pair().realise(Pair(0, "none"))
    println("{p.first} {find().realise(Maybe.None).realise(0)}")
    return Result.Ok(0)
""",
}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_a_spelled_return_is_stamped_with_the_table_entry(analyze_program, name):
    analysis = analyze_program(SOURCES[name])
    assert not analysis.reporter.has_errors, analysis.reporter.items
    assert _assert_stamped(analysis) >= 1


def test_a_library_build_stamps_its_spelled_return(analyze_program):
    analysis = analyze_program("""
public enum MyErr:
    Bad

public fn spelled(i32 x) Result@(i32, MyErr):
    return Result.Ok(x)
""", name="lib", is_library=True)
    assert not analysis.reporter.has_errors, analysis.reporter.items
    assert _assert_stamped(analysis) == 1


def _single_unit_fixtures_that_spell_a_result_return():
    for fixture in collect_fixtures(TESTS_DIR):
        if fixture.name.startswith(("test_err_", "test_warn_")):
            continue
        text = fixture.read_text(encoding="utf-8")
        if 'use "' in text or not SPELLED_HEADER.search(text):
            continue
        yield fixture


CORPUS = list(_single_unit_fixtures_that_spell_a_result_return())


def test_the_corpus_has_spelled_returns_to_check():
    assert len(CORPUS) >= 5


@pytest.mark.parametrize("fixture", CORPUS, ids=lambda p: str(p.relative_to(TESTS_DIR)))
def test_every_spelled_return_in_the_corpus_is_stamped(analyze_program, fixture):
    analysis = analyze_program(fixture.read_text(encoding="utf-8"), name=fixture.stem)
    assert not analysis.reporter.has_errors, analysis.reporter.items
    assert _assert_stamped(analysis) >= 1
