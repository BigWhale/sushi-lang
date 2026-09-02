"""A generic-target perk implementation travels through a binary .slib as a template (#543)."""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.library_templates import (
    deserialize_perk_impl,
    serialize_generic_perk_impl,
)
from sushi_lang.semantics.passes.collect import CollectorPass, EnumTable, StructTable
from sushi_lang.semantics.units import Unit

LIB_SRC = (
    "public perk Show:\n"
    "    fn show() string\n"
    "\n"
    "public struct Box@(T):\n"
    "    T item\n"
    "\n"
    "extend Box@(T) with Show:\n"
    "    fn show() string:\n"
    "        return \"box\"\n"
    "\n"
    "perk Quiet:\n"
    "    fn hush() i32\n"
    "\n"
    "public fn make_box(i32 v) Box@(i32):\n"
    "    return Result.Ok(Box(v))\n"
)


def _collected_program():
    program, _tree = parse_to_ast(LIB_SRC)
    CollectorPass(Reporter(source=LIB_SRC, filename="lib")).run(program, unit_name="lib")
    return program


def test_the_collect_pass_files_the_template_beside_the_concrete_impls():
    program = _collected_program()
    assert program.perk_impls == []
    assert [i.perk_name for i in program.generic_perk_impls] == ["Show"]


def test_the_record_names_the_base_type_its_parameters_and_the_perk():
    program = _collected_program()
    record = serialize_generic_perk_impl(program.generic_perk_impls[0], LIB_SRC)
    assert record["type"] == "Box"
    assert record["type_args"] == ["T"]
    assert record["perk"] == "Show"
    assert record["source"].startswith("extend Box@(T) with Show:")
    assert "return \"box\"" in record["source"]
    # A template has no symbol to declare: one copy per instantiation is cut at the consumer.
    assert "methods" not in record


def test_the_record_reads_back_as_a_generic_target_implementation():
    program = _collected_program()
    record = serialize_generic_perk_impl(program.generic_perk_impls[0], LIB_SRC)
    impl = deserialize_perk_impl(record)
    assert isinstance(impl.target_type, GenericTypeRef)
    assert impl.target_type.base_name == "Box"
    assert impl.perk_name == "Show"
    assert [m.name for m in impl.methods] == ["show"]


class _StubAnalyzer:
    def __init__(self, reporter):
        self.reporter = reporter
        self.structs = StructTable()
        self.enums = EnumTable()


def test_the_manifest_ships_every_public_perk_and_the_template(tmp_path):
    """A public perk is API whether or not a constraint names it; the template ships as
    source; a private perk nothing names stays home."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator

    program = _collected_program()
    file_path = tmp_path / "lib.sushi"
    file_path.write_text(LIB_SRC, encoding="utf-8")
    unit = Unit(name="lib", file_path=file_path, ast=program, dependencies=[], public_symbols={})
    reporter = Reporter(source="", filename="lib")

    templates = LibraryManifestGenerator(_StubAnalyzer(reporter))._extract_templates([unit])

    assert [p["name"] for p in templates["perks"]] == ["Show"]
    assert templates["perk_impls"] == []
    assert [(r["type"], r["perk"], r["unit"]) for r in templates["generic_perk_impls"]] == [
        ("Box", "Show", "lib")]
