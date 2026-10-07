"""The overlay of a template check reads the program tables and never writes them (#1070).

A check copy names instances over an opaque type parameter, and a program table must
never hold one. The overlay is the guarantee: a read goes through to the program table,
and a write stays in the overlay. These read tables and types; they start no compiler.
"""
from __future__ import annotations

from sushi_lang.semantics.generics.types import TemplateId, TypeParameter
from sushi_lang.semantics.tables import SymbolTables
from sushi_lang.semantics.template_scope import template_scope
from sushi_lang.semantics.typesys import BuiltinType, EnumType, StructType

_POINT = StructType(name="Point", fields=(("x", BuiltinType.I32),))
_SIGN = EnumType(name="Sign", variants=())


class _Derived:
    def __init__(self, name: str) -> None:
        self.name = name


def _program() -> SymbolTables:
    tables = SymbolTables()
    tables.structs.by_name["Point"] = _POINT
    tables.structs.order.append("Point")
    tables.enums.by_name["Sign"] = _SIGN
    tables.enums.order.append("Sign")
    tables.enums.derived.register_method(_POINT, _Derived("hash"))
    return tables


def test_a_read_goes_through_and_a_write_stays_in_the_overlay():
    program = _program()
    overlay = template_scope(program).tables
    assert overlay.structs.by_name["Point"] is _POINT
    assert overlay.enums.by_name["Sign"] is _SIGN

    opaque = StructType(name="Box<T#main.f>", fields=())
    overlay.structs.by_name[opaque.name] = opaque
    overlay.structs.order.append(opaque.name)
    assert opaque.name in overlay.structs.by_name
    assert opaque.name not in program.structs.by_name
    assert program.structs.order == ["Point"]
    assert overlay.structs.order == [opaque.name]


def test_only_the_overlay_admits_an_opaque_instance():
    program = _program()
    overlay = template_scope(program).tables
    assert overlay.structs.admits_opaque and overlay.enums.admits_opaque
    assert not program.structs.admits_opaque and not program.enums.admits_opaque


def test_the_derived_methods_of_the_overlay_are_its_own():
    program = _program()
    overlay = template_scope(program).tables
    assert overlay.derived_methods.get_method(_POINT, "hash") is not None
    opaque = StructType(name="Box<T#main.f>", fields=())
    overlay.derived_methods.register_method(opaque, _Derived("clone"))
    assert overlay.derived_methods.get_method(opaque, "clone") is not None
    assert program.derived_methods.get_method(opaque, "clone") is None


def test_the_overlay_wires_the_overrides_and_shares_the_refusals():
    program = _program()
    overlay = template_scope(program).tables
    promised = TypeParameter("T", owner=TemplateId("main", "f"), constraints=("Hashable",))
    assert overlay.derived_methods.hash_override(promised)
    assert overlay.refused_templates is program.refused_templates
    assert overlay.pending_extension_instantiations is not (
        program.pending_extension_instantiations)
    assert overlay.request_function_instance is None


def test_the_scratch_monomorphizer_builds_in_the_overlay():
    """A `Maybe@(T)` over an opaque `T` is built in the overlay and nowhere else."""
    from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
    program = _program()
    scope = template_scope(program)
    opaque = TypeParameter("T", owner=TemplateId("main", "f"))
    maybe = ensure_maybe_type_in_table(scope.tables.enums, opaque,
                                       scope.tables.structs.by_name)
    assert maybe is not None and maybe.name in scope.tables.enums.by_name
    assert maybe.name not in program.enums.by_name
    assert scope.monomorphizer.struct_table is scope.tables.structs
