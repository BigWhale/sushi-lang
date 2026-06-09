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
