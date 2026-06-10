"""Unit tests for the .slib generic-template codec (Phase 2, P2-T1 + P2-T2).

The locked design reconstructs an imported generic at the consumer by
**re-parsing its source text** through the existing frontend. These tests are
the spike that proves the producer-side source slice is self-contained and
re-parses into a structurally-identical generic, and that the producer's
export-closure walk ships the library-private symbols a generic references
(rejecting only genuinely un-shippable ones with CE5006).
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
    assert record["type_params"] == [{"name": "T", "constraints": ["Ord"], "is_pack": False}]
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

    assert templates["version"] == 4
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


PACK_SRC = (
    "perk Display:\n"
    "    fn display() string\n"
    "\n"
    "public fn show_all<...Ts: Display>(...Ts args) ~:\n"
    "    expand(a in args):\n"
    "        println(a.display())\n"
    "    return Result.Ok(~)\n"
)


def test_pack_type_param_carries_is_pack_across_round_trip():
    """Phase 3 (G2): a '...Ts' type-pack survives serialize -> deserialize.

    The decl source re-parses the '...' marker on its own, but the record also
    records `is_pack` explicitly. Both the type-param and the value parameter
    must come back as packs.
    """
    program, _ = parse_to_ast(PACK_SRC)
    func = next(f for f in program.functions if f.name == "show_all")

    record = serialize_generic_function(func, PACK_SRC)
    assert record["type_params"] == [
        {"name": "Ts", "constraints": ["Display"], "is_pack": True}
    ]

    rebuilt = deserialize_generic_function(record)
    assert rebuilt.type_params[-1].is_pack is True
    # The value pack parameter ('...Ts args') is reconstructed as a pack too.
    assert rebuilt.params[-1].is_pack is True


def test_v2_pack_public_function_allowed_as_template(tmp_path):
    """Phase 3 (G1): a public '...Ts' pack exports as a template, not CE0116.

    CE0116 blocks only v1 native '...T' (is_variadic). A v2 type pack carries
    type_params, so it is routed to templates.generic_functions and never hits
    the CE0116 check -- the producer must NOT raise.
    """
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    unit = _make_unit(tmp_path, PACK_SRC)
    reporter = Reporter(source="", filename="lib")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter))

    public_funcs = gen._extract_public_functions([unit])
    templates = gen._extract_templates([unit])

    assert "show_all" not in [f["name"] for f in public_funcs]
    assert any(t["name"] == "show_all" for t in templates["generic_functions"])
    assert not any(item.code == "CE0116" for item in reporter.items)


def test_closure_ships_private_helper_reference(tmp_path):
    """C4b/C5: a public generic calling a private helper SHIPS the helper as
    a signature record instead of rejecting the export (the old CE5006)."""
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

    templates = gen._extract_templates([unit])

    assert not any(item.code == "CE5006" for item in reporter.items)
    assert [f["name"] for f in templates["private_functions"]] == ["secret"]
    assert templates["private_functions"][0]["params"] == [
        {"name": "x", "type": "i32"}
    ]
    assert templates["closure_summary"]["private_functions"] == ["secret"]


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
    # This library defines no `extend ... with` blocks, so nothing ships.
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


# ---------------------------------------------------------------------------
# P2-5 Phase 1 (C3): generic STRUCT / ENUM templates.
# ---------------------------------------------------------------------------

from sushi_lang.backend.library_templates import (
    serialize_generic_struct,
    deserialize_generic_struct,
    serialize_generic_enum,
    deserialize_generic_enum,
)
from sushi_lang.semantics.passes.collect import (
    GenericStructTable,
    GenericEnumTable,
)

BOX_SRC = (
    "struct Box<T>:\n"
    "    T value\n"
)

RANKED_SRC = (
    "struct Ranked<T: Ord>:\n"
    "    T value\n"
)

OPT_SRC = (
    "enum Opt<T>:\n"
    "    Nope\n"
    "    Yep(T)\n"
)


def test_serialize_generic_struct_record_shape():
    """A generic struct serializes to the uniform template record schema."""
    program, _ = parse_to_ast(RANKED_SRC)
    struct = program.structs[0]

    record = serialize_generic_struct(struct, RANKED_SRC)

    assert record["name"] == "Ranked"
    assert record["type_params"] == [{"name": "T", "constraints": ["Ord"], "is_pack": False}]
    assert record["free_perks"] == ["Ord"]
    assert record["source"].startswith("struct Ranked<T: Ord>")
    assert record["source"].endswith("\n")


def test_generic_struct_round_trip_structural_equality():
    """The struct source slice re-parses into a structurally identical struct."""
    program, _ = parse_to_ast(BOX_SRC)
    struct = program.structs[0]

    rebuilt = deserialize_generic_struct(serialize_generic_struct(struct, BOX_SRC))

    assert rebuilt.name == "Box"
    assert [tp.name for tp in rebuilt.type_params] == ["T"]
    assert [f.name for f in rebuilt.fields] == ["value"]


def test_generic_enum_round_trip_structural_equality():
    """The enum source slice re-parses into a structurally identical enum."""
    program, _ = parse_to_ast(OPT_SRC)
    enum = program.enums[0]

    rebuilt = deserialize_generic_enum(serialize_generic_enum(enum, OPT_SRC))

    assert rebuilt.name == "Opt"
    assert [tp.name for tp in rebuilt.type_params] == ["T"]
    assert [v.name for v in rebuilt.variants] == ["Nope", "Yep"]


def test_generic_types_route_to_templates_only(tmp_path):
    """A generic struct/enum ships ONLY as a template, never as a concrete entry.

    Concrete structs/enums still populate the concrete section; the generic ones
    land solely in templates.generic_structs / generic_enums.
    """
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    src = (
        "struct Box<T>:\n"
        "    T value\n"
        "\n"
        "struct Point:\n"
        "    i32 x\n"
        "\n"
        "enum Opt<T>:\n"
        "    Nope\n"
        "    Yep(T)\n"
        "\n"
        "enum Color:\n"
        "    Red\n"
    )
    unit = _make_unit(tmp_path, src)
    reporter = Reporter(source="", filename="lib")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter))

    concrete_structs = gen._extract_structs([unit])
    concrete_enums = gen._extract_enums([unit])
    templates = gen._extract_templates([unit])

    # Concrete section: only the non-generic Point / Color.
    assert [s["name"] for s in concrete_structs] == ["Point"]
    assert [e["name"] for e in concrete_enums] == ["Color"]
    # Template section: only the generic Box / Opt.
    assert [t["name"] for t in templates["generic_structs"]] == ["Box"]
    assert [t["name"] for t in templates["generic_enums"]] == ["Opt"]


def test_closure_check_allows_co_shipped_generic_type_field(tmp_path):
    """Outer<T> with a field of another exported generic Inner<T> is allowed."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    src = (
        "struct Inner<T>:\n"
        "    T val\n"
        "\n"
        "struct Outer<T>:\n"
        "    Inner<T> inner\n"
    )
    unit = _make_unit(tmp_path, src)
    reporter = Reporter(source="", filename="lib")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter))

    templates = gen._extract_templates([unit])

    names = sorted(t["name"] for t in templates["generic_structs"])
    assert names == ["Inner", "Outer"]
    assert not any(item.code == "CE5006" for item in reporter.items)


def test_closure_allows_concrete_type_field(tmp_path):
    """C4b/C5: a generic struct with a concrete-type field exports fine - the
    concrete struct already ships in the manifest's type sections and is
    registered at the consumer (the old behavior rejected this with CE5006)."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    src = (
        "struct Secret:\n"
        "    i32 x\n"
        "\n"
        "struct Leaky<T>:\n"
        "    Secret hidden\n"
        "    T value\n"
    )
    unit = _make_unit(tmp_path, src)
    reporter = Reporter(source="", filename="lib")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter))

    templates = gen._extract_templates([unit])

    assert not any(item.code == "CE5006" for item in reporter.items)
    assert [t["name"] for t in templates["generic_structs"]] == ["Leaky"]
    # Secret is not a closure record - it rides the concrete struct section.
    assert templates["closure_summary"]["private_functions"] == []


def test_register_library_generic_struct_lands_in_table():
    """A generic-struct template is rebuilt into the consumer's struct table."""
    record = serialize_generic_struct(parse_to_ast(BOX_SRC)[0].structs[0], BOX_SRC)
    analyzer = _analyzer_with_loaded_libraries(
        {"mathlib": {"templates": {"version": 2, "generic_structs": [record],
                                   "generic_enums": [], "generic_functions": [],
                                   "perks": [], "perk_impls": []}}}
    )
    analyzer.generic_structs = GenericStructTable()

    analyzer._register_library_generic_structs()

    assert "Box" in analyzer.generic_structs.by_name
    assert "Box" in analyzer.generic_structs.order


def test_register_library_generic_enum_lands_in_table():
    """A generic-enum template is rebuilt into the consumer's enum table."""
    record = serialize_generic_enum(parse_to_ast(OPT_SRC)[0].enums[0], OPT_SRC)
    analyzer = _analyzer_with_loaded_libraries(
        {"mathlib": {"templates": {"version": 2, "generic_structs": [],
                                   "generic_enums": [record], "generic_functions": [],
                                   "perks": [], "perk_impls": []}}}
    )
    analyzer.generic_enums = GenericEnumTable()

    analyzer._register_library_generic_enums()

    assert "Opt" in analyzer.generic_enums.by_name
    assert "Opt" in analyzer.generic_enums.order


def test_register_library_generic_struct_respects_local_definition():
    """A locally-defined generic struct of the same name wins; template ignored."""
    record = serialize_generic_struct(parse_to_ast(BOX_SRC)[0].structs[0], BOX_SRC)
    analyzer = _analyzer_with_loaded_libraries(
        {"mathlib": {"templates": {"version": 2, "generic_structs": [record],
                                   "generic_enums": [], "generic_functions": [],
                                   "perks": [], "perk_impls": []}}}
    )
    analyzer.generic_structs = GenericStructTable()
    sentinel = object()
    analyzer.generic_structs.by_name["Box"] = sentinel
    analyzer.generic_structs.order.append("Box")

    analyzer._register_library_generic_structs()

    assert analyzer.generic_structs.by_name["Box"] is sentinel
    assert analyzer.generic_structs.order.count("Box") == 1


# ---------------------------------------------------------------------------
# C4a: concrete perk-impl shipping (templates.perk_impls)
# ---------------------------------------------------------------------------

from sushi_lang.backend.library_templates import (
    serialize_perk_impl,
    deserialize_perk_impl,
    impl_method_symbol,
)
from sushi_lang.semantics.passes.collect import (
    PerkTable,
    PerkImplementationTable,
    ExtensionTable,
)


IMPL_LIB_SRC = (
    "perk Doubler:\n"
    "    fn doubled() i32\n"
    "\n"
    "extend i32 with Doubler:\n"
    "    fn doubled() i32:\n"
    "        return self * 2\n"
    "\n"
    "public fn pick_bigger<T: Doubler>(T a, T b) T:\n"
    "    if (a.doubled() > b.doubled()):\n"
    "        return Result.Ok(a)\n"
    "    return Result.Ok(b)\n"
)


def test_serialize_perk_impl_record_shape():
    """The impl record carries type, perk, source, and per-method symbols."""
    program, _ = parse_to_ast(IMPL_LIB_SRC)
    impl = program.perk_impls[0]

    record = serialize_perk_impl(impl, IMPL_LIB_SRC)

    assert record["type"] == "i32"
    assert record["perk"] == "Doubler"
    assert record["source"].startswith("extend i32 with Doubler:")
    assert record["source"].endswith("\n")
    assert record["methods"] == [{"name": "doubled", "symbol": "i32_doubled"}]


def test_perk_impl_round_trip():
    """serialize -> deserialize yields the same impl signatures."""
    program, _ = parse_to_ast(IMPL_LIB_SRC)
    record = serialize_perk_impl(program.perk_impls[0], IMPL_LIB_SRC)

    rebuilt = deserialize_perk_impl(record)

    assert rebuilt.perk_name == "Doubler"
    assert [m.name for m in rebuilt.methods] == ["doubled"]


def test_impl_method_symbol_matches_extension_naming():
    """The manifest symbol matches the backend's extension-method mangling
    (get_extension_method_name): sanitized type name + "_" + method name."""
    assert impl_method_symbol("i32", "doubled") == "i32_doubled"
    assert impl_method_symbol("Box<i32>", "unwrap") == "Box__i32_unwrap"
    assert impl_method_symbol("HashMap<string, i32>", "get") == "HashMap__string_i32_get"


def test_extract_templates_ships_impl_of_referenced_perk(tmp_path):
    """An impl of a constraint-referenced perk ships with symbol metadata."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    unit = _make_unit(tmp_path, IMPL_LIB_SRC)
    reporter = Reporter(source="", filename="lib")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter))

    templates = gen._extract_templates([unit])

    assert templates["version"] == 4
    assert [p["name"] for p in templates["perks"]] == ["Doubler"]
    impls = templates["perk_impls"]
    assert len(impls) == 1
    assert impls[0]["type"] == "i32"
    assert impls[0]["perk"] == "Doubler"
    assert impls[0]["methods"][0]["symbol"] == "i32_doubled"


def test_extract_templates_skips_impl_of_unreferenced_perk(tmp_path):
    """An impl whose perk no exported generic constrains on stays internal."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    src = (
        "perk Internal:\n"
        "    fn hidden() i32\n"
        "\n"
        "extend i32 with Internal:\n"
        "    fn hidden() i32:\n"
        "        return self\n"
        "\n"
        "public fn id<T>(T a) T:\n"
        "    return Result.Ok(a)\n"
    )
    unit = _make_unit(tmp_path, src)
    reporter = Reporter(source="", filename="lib")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter))

    templates = gen._extract_templates([unit])

    assert templates["perks"] == []
    assert templates["perk_impls"] == []


def _impl_consumer_analyzer(record, perk_record) -> SemanticAnalyzer:
    """Analyzer wired for _register_library_perk_impls in isolation."""
    analyzer = _analyzer_with_loaded_libraries(
        {"impllib": {"templates": {
            "version": 3, "generic_functions": [], "generic_structs": [],
            "generic_enums": [], "perks": [perk_record],
            "perk_impls": [record],
        }}}
    )
    analyzer.perks = PerkTable()
    analyzer.perk_impls = PerkImplementationTable()
    analyzer.extensions = ExtensionTable()
    analyzer._seed_library_perks(analyzer.perks)
    return analyzer


def _impl_records():
    program, _ = parse_to_ast(IMPL_LIB_SRC)
    impl_record = serialize_perk_impl(program.perk_impls[0], IMPL_LIB_SRC)
    perk_record = serialize_perk(program.perks[0], IMPL_LIB_SRC)
    return impl_record, perk_record


def test_register_library_perk_impl_lands_in_table():
    """A shipped impl registers for constraint checks and dispatch."""
    impl_record, perk_record = _impl_records()
    analyzer = _impl_consumer_analyzer(impl_record, perk_record)

    analyzer._register_library_perk_impls()

    assert analyzer.perk_impls.implements("i32", "Doubler")
    assert len(analyzer.library_perk_impls) == 1


def test_register_library_perk_impl_respects_local_impl():
    """A consumer's own impl of the same (type, perk) wins silently."""
    impl_record, perk_record = _impl_records()
    analyzer = _impl_consumer_analyzer(impl_record, perk_record)
    local_program, _ = parse_to_ast(IMPL_LIB_SRC)
    analyzer.perk_impls.register(local_program.perk_impls[0], "i32")

    analyzer._register_library_perk_impls()

    # Registered (the local one), but nothing queued for declare-only codegen.
    assert analyzer.perk_impls.implements("i32", "Doubler")
    assert analyzer.library_perk_impls == []


def test_register_library_perk_impl_skips_on_extension_clash():
    """A local extension method named like an impl method skips the library
    impl entirely (the CE4007 ambiguity is never created)."""
    impl_record, perk_record = _impl_records()
    analyzer = _impl_consumer_analyzer(impl_record, perk_record)
    analyzer.extensions.by_type.setdefault("i32", {})["doubled"] = object()

    analyzer._register_library_perk_impls()

    assert not analyzer.perk_impls.implements("i32", "Doubler")
    assert analyzer.library_perk_impls == []
