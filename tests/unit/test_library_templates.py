"""Unit tests for the .slib generic-template codec (Phase 2, P2-T1 + P2-T2).

The locked design reconstructs an imported generic at the consumer by
**re-parsing its source text** through the existing frontend. These tests are
the spike that proves the producer-side source slice is self-contained and
re-parses into a structurally-identical generic, and that the producer closure
check (CE5006) rejects a generic whose body references a library-private symbol.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.units import Unit
from sushi_lang.semantics.passes.collect import CollectorPass, StructTable, EnumTable
from sushi_lang.backend.library_templates import (
    serialize_generic_function,
    deserialize_generic_function,
    serialize_perk,
    deserialize_perk,
)


MAX_SRC = (
    "public fn max<T: Ord>(T a, T b) T:\n"
    "    if (a > b):\n"
    "        return Result.Ok(a)\n"
    "    return Result.Ok(b)\n"
)


def _ensure_newline(src: str) -> str:
    return src if src.endswith("\n") else src + "\n"


def _make_unit(tmp_path, src: str, name: str = "lib") -> Unit:
    text = _ensure_newline(src)
    file_path = tmp_path / f"{name}.sushi"
    file_path.write_text(text, encoding="utf-8")
    program, _tree = parse_to_ast(text)
    return Unit(name=name, file_path=file_path, ast=program,
                dependencies=[], public_symbols={})


class _StubAnalyzer:
    """Minimal analyzer surface for LibraryManifestGenerator."""
    def __init__(self, reporter):
        self.reporter = reporter
        self.structs = StructTable()
        self.enums = EnumTable()


def _collect_generic(program, name: str):
    """Run the collector and return the named GenericFuncDef."""
    reporter = Reporter(source="", filename="lib")
    tables = CollectorPass(reporter).run(program, unit_name="lib")
    generic_funcs = tables[-1]  # GenericFunctionTable is the last element
    return generic_funcs.by_name[name]


def test_serialize_record_shape():
    """The serialized record carries name, type-params, source, free_perks."""
    program, _ = parse_to_ast(MAX_SRC)
    func = program.functions[0]

    record = serialize_generic_function(func, MAX_SRC)

    assert record["name"] == "max"
    assert record["type_params"] == [{"name": "T", "constraints": ["Ord"]}]
    assert record["free_perks"] == ["Ord"]
    assert record["source"].endswith("\n")
    # The slice must cover the whole declaration, header through body.
    assert record["source"].startswith("public fn max<T: Ord>")
    assert "return Result.Ok(b)" in record["source"]


def test_slice_reparses_standalone():
    """The source slice re-parses on its own to a single FuncDef."""
    program, _ = parse_to_ast(MAX_SRC)
    record = serialize_generic_function(program.functions[0], MAX_SRC)

    reparsed, _ = parse_to_ast(record["source"])
    assert len(reparsed.functions) == 1
    assert reparsed.functions[0].name == "max"


def test_slice_survives_surrounding_declarations():
    """Slicing picks out exactly the target decl even amid private neighbours."""
    src = (
        "fn helper() i32:\n"
        "    return Result.Ok(7)\n"
        "\n"
        "public fn max<T: Ord>(T a, T b) T:\n"
        "    if (a > b):\n"
        "        return Result.Ok(a)\n"
        "    return Result.Ok(b)\n"
        "\n"
        "fn after() i32:\n"
        "    return Result.Ok(1)\n"
    )
    program, _ = parse_to_ast(src)
    max_fn = next(f for f in program.functions if f.name == "max")

    record = serialize_generic_function(max_fn, src)

    assert "helper" not in record["source"]
    assert "after" not in record["source"]
    reparsed, _ = parse_to_ast(record["source"])
    assert [f.name for f in reparsed.functions] == ["max"]


def test_round_trip_structural_equality():
    """A serialized + re-parsed generic collects identically to the original.

    This is the spike: it proves the source slice is self-contained and rebuilds
    a structurally-equal GenericFuncDef (name, type-params, params, return type,
    body shape).
    """
    orig_program, _ = parse_to_ast(MAX_SRC)
    direct = _collect_generic(orig_program, "max")

    record = serialize_generic_function(orig_program.functions[0], MAX_SRC)
    rebuilt_func = deserialize_generic_function(record)

    # Wrap the rebuilt FuncDef back into a Program and collect it.
    rebuilt_program, _ = parse_to_ast(record["source"])
    rebuilt = _collect_generic(rebuilt_program, "max")

    # Same name.
    assert rebuilt.name == direct.name

    # Same type-params (names + constraints).
    assert len(rebuilt.type_params) == len(direct.type_params)
    for r_tp, d_tp in zip(rebuilt.type_params, direct.type_params):
        assert r_tp.name == d_tp.name
        assert list(r_tp.constraints or []) == list(d_tp.constraints or [])

    # Same params (names + type strings).
    assert len(rebuilt.params) == len(direct.params)
    for r_p, d_p in zip(rebuilt.params, direct.params):
        assert r_p.name == d_p.name
        assert str(r_p.ty) == str(d_p.ty)

    # Same return type string.
    assert str(rebuilt.ret) == str(direct.ret)

    # Same body shape: same number/kind of top-level statements.
    direct_stmts = [type(s).__name__ for s in direct.body.statements]
    rebuilt_stmts = [type(s).__name__ for s in rebuilt.body.statements]
    assert rebuilt_stmts == direct_stmts

    # The standalone deserialize path returns the same single FuncDef.
    assert rebuilt_func.name == "max"


def test_closure_check_accepts_self_contained_generic(tmp_path):
    """max<T: Ord> references only params + builtin `>` -> export succeeds."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    unit = _make_unit(tmp_path, MAX_SRC)
    reporter = Reporter(source="", filename="lib")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter))

    templates = gen._extract_templates([unit])

    assert templates["version"] == 2
    assert templates["perks"] == []
    assert templates["perk_impls"] == []
    names = [g["name"] for g in templates["generic_functions"]]
    assert names == ["max"]
    assert not any(item.code == "CE5006" for item in reporter.items)


def test_generics_route_to_templates_only(tmp_path):
    """E3: a public generic ships ONLY as a template, never in public_functions.

    The producer must keep concrete publics (area) in public_functions while a
    generic (first_of) lands solely in templates.generic_functions. No leaked
    public_functions entry, and no residual `is_generic` flag on any concrete
    function record.
    """
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    src = (
        "public fn first_of<T>(T a, T b) T:\n"
        "    return Result.Ok(a)\n"
        "\n"
        "public fn area(i32 w) i32:\n"
        "    return Result.Ok(w * w)\n"
    )
    unit = _make_unit(tmp_path, src)
    reporter = Reporter(source="", filename="lib")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter))

    public_funcs = gen._extract_public_functions([unit])
    templates = gen._extract_templates([unit])

    public_names = [f["name"] for f in public_funcs]
    assert "first_of" not in public_names
    assert "area" in public_names
    assert any(t["name"] == "first_of" for t in templates["generic_functions"])
    # No concrete function record carries the legacy is_generic flag anymore.
    assert all("is_generic" not in f for f in public_funcs)
    assert all("type_params" not in f for f in public_funcs)


def test_closure_check_rejects_private_helper_reference(tmp_path):
    """A public generic calling a private helper aborts the export with CE5006."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    src = (
        "fn secret(i32 x) i32:\n"
        "    return Result.Ok(x)\n"
        "\n"
        "public fn wrap<T: Ord>(T a) i32:\n"
        "    return Result.Ok(secret(1)??)\n"
    )
    unit = _make_unit(tmp_path, src)
    reporter = Reporter(source="", filename="lib")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter))

    with pytest.raises(ValueError):
        gen._extract_templates([unit])

    assert any(item.code == "CE5006" for item in reporter.items)


# ---------------------------------------------------------------------------
# P2-T4: consumer-side registration of library generic templates.
# ---------------------------------------------------------------------------

from types import SimpleNamespace

from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.passes.collect import GenericFunctionTable


def _analyzer_with_loaded_libraries(loaded: dict) -> SemanticAnalyzer:
    """Build a bare analyzer wired with a fake library_linker + empty tables.

    Exercises _register_library_generic_functions in isolation: the analyzer
    only needs a generic_funcs table and a library_linker exposing
    loaded_libraries.
    """
    reporter = Reporter(source="", filename="consumer")
    fake_linker = SimpleNamespace(loaded_libraries=loaded)
    analyzer = SemanticAnalyzer(reporter, filename="consumer", library_linker=fake_linker)
    analyzer.generic_funcs = GenericFunctionTable()
    return analyzer


def _templates_manifest(*records) -> dict:
    return {
        "library_name": "mathlib",
        "templates": {
            "version": 2,
            "generic_functions": list(records),
            "perks": [],
            "perk_impls": [],
        },
    }


def test_register_library_generic_function_lands_in_table():
    """A templates record is rebuilt into generic_funcs with the flag set."""
    record = serialize_generic_function(parse_to_ast(MAX_SRC)[0].functions[0], MAX_SRC)
    analyzer = _analyzer_with_loaded_libraries(
        {"mathlib": _templates_manifest(record)}
    )

    analyzer._register_library_generic_functions()

    assert "max" in analyzer.generic_funcs.by_name
    assert "max" in analyzer.generic_funcs.order
    gfd = analyzer.generic_funcs.by_name["max"]
    assert gfd.is_library_template is True
    # Constraints reconciled from the authoritative record.
    assert len(gfd.type_params) == 1
    assert list(gfd.type_params[0].constraints or []) == ["Ord"]
    # Body and signature survived the re-parse.
    assert [p.name for p in gfd.params] == ["a", "b"]
    assert len(gfd.body.statements) == 2


def test_register_library_generic_function_respects_local_definition():
    """A locally-defined generic of the same name wins; the template is ignored."""
    record = serialize_generic_function(parse_to_ast(MAX_SRC)[0].functions[0], MAX_SRC)
    analyzer = _analyzer_with_loaded_libraries(
        {"mathlib": _templates_manifest(record)}
    )
    # Pre-seed a local definition (a different sentinel object).
    local = _collect_generic(parse_to_ast(MAX_SRC)[0], "max")
    local.is_library_template = False
    analyzer.generic_funcs.by_name["max"] = local
    analyzer.generic_funcs.order.append("max")

    analyzer._register_library_generic_functions()

    # Still the local object, untouched (local definitions win).
    assert analyzer.generic_funcs.by_name["max"] is local
    assert analyzer.generic_funcs.by_name["max"].is_library_template is False
    assert analyzer.generic_funcs.order.count("max") == 1


def test_register_library_generic_function_guards_missing_templates():
    """A manifest without a templates section registers nothing and does not crash."""
    analyzer = _analyzer_with_loaded_libraries(
        {"mathlib": {"library_name": "mathlib"}}
    )

    analyzer._register_library_generic_functions()

    assert analyzer.generic_funcs.by_name == {}


# ---------------------------------------------------------------------------
# Phase 2 Step A: perk DEFINITION shipping (definitions only, no impls).
# ---------------------------------------------------------------------------

PERK_SRC = (
    "perk Ord:\n"
    "    fn gt(i32 other) bool\n"
)

LIB_WITH_PERK_SRC = (
    "perk Ord:\n"
    "    fn gt(i32 other) bool\n"
    "\n"
    "perk Unused:\n"
    "    fn nope() bool\n"
    "\n"
    "public fn max_of<T: Ord>(T a, T b) T:\n"
    "    if (a > b):\n"
    "        return Result.Ok(a)\n"
    "    return Result.Ok(b)\n"
)


def test_serialize_perk_record_shape():
    """The serialized perk record carries name and a re-parsable source slice."""
    program, _ = parse_to_ast(PERK_SRC)
    perk = program.perks[0]

    record = serialize_perk(perk, PERK_SRC)

    assert record["name"] == "Ord"
    assert record["source"].startswith("perk Ord:")
    assert "fn gt(i32 other) bool" in record["source"]
    assert record["source"].endswith("\n")
    # Definitions only: no implementation is ever serialized.
    assert "extend" not in record["source"]


def test_perk_round_trip_reparses_to_single_perk():
    """serialize_perk -> deserialize_perk yields the same PerkDef contract."""
    program, _ = parse_to_ast(PERK_SRC)
    record = serialize_perk(program.perks[0], PERK_SRC)

    rebuilt = deserialize_perk(record)

    assert rebuilt.name == "Ord"
    assert [m.name for m in rebuilt.methods] == ["gt"]


def test_extract_templates_ships_only_referenced_perks(tmp_path):
    """Only perks named by an exported generic's constraints are shipped."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    unit = _make_unit(tmp_path, LIB_WITH_PERK_SRC)
    reporter = Reporter(source="", filename="lib")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter))

    templates = gen._extract_templates([unit])

    # max_of<T: Ord> references Ord, so Ord ships; Unused is never referenced.
    perk_names = [p["name"] for p in templates["perks"]]
    assert perk_names == ["Ord"]
    # Perk implementations remain out of scope.
    assert templates["perk_impls"] == []
    # The shipped perk round-trips back to its contract.
    rebuilt = deserialize_perk(templates["perks"][0])
    assert rebuilt.name == "Ord"
    assert [m.name for m in rebuilt.methods] == ["gt"]


def test_extract_templates_perks_empty_without_constrained_generics(tmp_path):
    """A library whose exported generic has no constraints ships no perks."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    src = (
        "perk Ord:\n"
        "    fn gt(i32 other) bool\n"
        "\n"
        "public fn id<T>(T a) T:\n"
        "    return Result.Ok(a)\n"
    )
    unit = _make_unit(tmp_path, src)
    reporter = Reporter(source="", filename="lib")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter))

    templates = gen._extract_templates([unit])

    assert templates["perks"] == []


def _manifest_with_perks(*perk_records) -> dict:
    return {
        "library_name": "mathlib",
        "templates": {
            "version": 2,
            "generic_functions": [],
            "perks": list(perk_records),
            "perk_impls": [],
        },
    }


def test_seed_library_perks_lands_in_table():
    """A shipped perk record is rebuilt into a perk table."""
    record = serialize_perk(parse_to_ast(PERK_SRC)[0].perks[0], PERK_SRC)
    analyzer = _analyzer_with_loaded_libraries(
        {"mathlib": _manifest_with_perks(record)}
    )
    from sushi_lang.semantics.passes.collect import PerkTable
    table = PerkTable()

    analyzer._seed_library_perks(table)

    assert "Ord" in table.by_name
    assert "Ord" in table.order
    assert [m.name for m in table.by_name["Ord"].methods] == ["gt"]


def test_seed_library_perks_respects_local_definition():
    """A locally-defined perk of the same name wins; the shipped one is ignored."""
    record = serialize_perk(parse_to_ast(PERK_SRC)[0].perks[0], PERK_SRC)
    analyzer = _analyzer_with_loaded_libraries(
        {"mathlib": _manifest_with_perks(record)}
    )
    from sushi_lang.semantics.passes.collect import PerkTable
    table = PerkTable()
    local = parse_to_ast(PERK_SRC)[0].perks[0]
    table.by_name["Ord"] = local
    table.order.append("Ord")

    analyzer._seed_library_perks(table)

    assert table.by_name["Ord"] is local
    assert table.order.count("Ord") == 1


def test_seed_library_perks_guards_missing_templates():
    """A manifest without a templates section seeds nothing and does not crash."""
    analyzer = _analyzer_with_loaded_libraries(
        {"mathlib": {"library_name": "mathlib"}}
    )
    from sushi_lang.semantics.passes.collect import PerkTable
    table = PerkTable()

    analyzer._seed_library_perks(table)

    assert table.by_name == {}
