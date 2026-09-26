"""One predicate says which bare names in a target's argument position are declared (#653).

`extend Box@(T)` and `extend Box@(Point)` are spelled alike. Two collectors answered the
question from two different sets -- the perk collector from the perk table and a
hand-kept set of plain types, the function collector from the four type tables and no
perk -- so `extend Box@(Cage) with Show` bound a type PARAMETER named after a declared
generic while `extend Box@(Cage) g()` constrained, and a perk name went the other way.

Three gates:
  - the seam answers for every declared kind, the built-ins included, and for nothing
    else, and it reads the tables live (#690 retired the hand-kept set),
  - both collectors hold the ONE seam and every classifier call hands it in; no second
    predicate is defined anywhere under `sushi_lang/`,
  - the two paths classify one spelling alike, through the real compiler.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.passes.collect import CollectorPass

SUSHI_LANG = Path(__file__).resolve().parents[2] / "sushi_lang"
CLASSIFIERS = {"classify_extension_target", "classify_array_extension_target"}
RETIRED_PREDICATES = {"_is_declared_type", "is_declared_type", "_is_known_type"}
RETIRED_SEED = "KNOWN_BUILTIN_TYPES"


def _table(*names: str) -> SimpleNamespace:
    return SimpleNamespace(by_name={name: object() for name in names})


def _namer(**tables):
    from sushi_lang.semantics.generics.extension_targets import DeclaredTypeNamer

    kinds = ("structs", "enums", "generic_structs", "generic_enums", "perks")
    return DeclaredTypeNamer(**{kind: tables.get(kind, _table()) for kind in kinds})


# 1. The seam

def test_the_seam_answers_every_declared_kind():
    namer = _namer(structs=_table("Point"), enums=_table("Sign"),
                   generic_structs=_table("Cage"), generic_enums=_table("Opt"),
                   perks=_table("Named"))
    assert namer("Point")
    assert namer("Sign")
    assert namer("Cage")
    assert namer("Opt")
    assert namer("Named")
    assert namer("i32")
    assert not namer("Zzz")
    assert not namer("T")


def test_the_seam_reads_the_tables_live():
    """A name collected after construction is seen: nothing is copied out of a table."""
    structs = _table()
    namer = _namer(structs=structs)
    assert not namer("Late")
    structs.by_name["Late"] = object()
    assert namer("Late")


# 2. One seam, held by both collectors, handed to every classifier call

def test_both_collectors_hold_the_one_seam():
    from sushi_lang.semantics.generics.extension_targets import DeclaredTypeNamer

    collect = CollectorPass(Reporter())
    assert isinstance(collect.is_declared_type, DeclaredTypeNamer)
    assert collect.perk_collector.is_declared_type is collect.is_declared_type
    assert collect.function_collector.is_declared_type is collect.is_declared_type
    assert collect.is_declared_type("Drop")
    assert collect.is_declared_type("List")
    assert collect.is_declared_type("Maybe")
    assert not collect.is_declared_type("T")


def _modules():
    for path in sorted(SUSHI_LANG.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_second_predicate_is_defined():
    offenders = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name in RETIRED_PREDICATES:
                offenders.append(f"{path.relative_to(SUSHI_LANG)}:{node.lineno} {node.name}")
            if isinstance(node, ast.Name) and node.id == RETIRED_SEED:
                offenders.append(f"{path.relative_to(SUSHI_LANG)}:{node.lineno} {node.id}")
    assert offenders == [], "\n".join(offenders)


def _classifier_calls():
    for path, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name in CLASSIFIERS:
                yield path, node


def test_every_classifier_call_hands_in_the_seam():
    """The second argument is `self.is_declared_type` -- never a lambda, never a method."""
    calls = list(_classifier_calls())
    assert len(calls) >= 3, "the perk path, the extension path and the array path"
    for path, call in calls:
        assert len(call.args) == 2 and not call.keywords, ast.unparse(call)
        predicate = call.args[1]
        assert isinstance(predicate, ast.Attribute), f"{path}: {ast.unparse(call)}"
        assert isinstance(predicate.value, ast.Name) and predicate.value.id == "self", \
            f"{path}: {ast.unparse(call)}"
        assert predicate.attr == "is_declared_type", f"{path}: {ast.unparse(call)}"


# 3. The two paths, through the real compiler



# kind -> (the bare name written, whether it is a declared name)


