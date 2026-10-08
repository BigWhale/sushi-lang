"""`Result<T, E>` and `Maybe<T>` must intern into the enum table on identical terms.

`ensure_result_type_in_table` is the single seam that interns a `Result@(T, E)`
(docs/design/type-identity.md: never build one structurally). It resolves a payload
RECURSIVELY: a bare `UnknownType` payload, and a name NESTED in one -- `IpAddr[]`, a
`DynamicArrayType` whose ELEMENT is a name. The interned NAME is the same either way, so
an instance interned with the element unresolved collides on the next intern and the
guard fires CE0126 with two identical spellings.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.errors import InternalCompilerError
from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
from sushi_lang.semantics.generics.results import (
    ensure_result_type_in_table,
    is_result_enum,
    result_ok_err,
)
from sushi_lang.semantics.generics.types import TypeParameter
from sushi_lang.semantics.passes.collect.enums import EnumTable
from sushi_lang.semantics.typesys import (
    BuiltinType,
    DynamicArrayType,
    EnumType,
    EnumVariantInfo,
    FunctionType,
    StructType,
    UnknownType,
)


class FakeEnumTable:
    """The duck-typed shape both ensure_* helpers consume.

    `.by_name`, `.order`, and `.derived` -- the compilation's auto-derived methods, which
    the seams write a hash into the moment they intern an enum (#601) -- and
    `.admits_opaque`, which only the overlay of a template check sets (#1070).
    """

    def __init__(self, by_name=None):
        self.by_name = dict(by_name or {})
        self.order = list(self.by_name)
        self.derived = DerivedMethodTable()
        # A program table holds no instance over an opaque type parameter (#1070).
        self.admits_opaque = False


STD_ERROR = EnumType(
    name="StdError",
    variants=(EnumVariantInfo(name="Error", associated_types=()),),
)


def table_with_std_error() -> FakeEnumTable:
    return FakeEnumTable({"StdError": STD_ERROR})


# --- the invariant -------------------------------------------------------------------

def test_unresolved_error_payload_is_resolved_before_interning():
    """`UnknownType("StdError")` must not reach the table as a payload."""
    table = table_with_std_error()

    interned = ensure_result_type_in_table(table, BuiltinType.STRING, UnknownType("StdError"))

    _, err = result_ok_err(interned)
    assert err == STD_ERROR
    assert not isinstance(err, UnknownType)


def test_poisoned_intern_raises_rather_than_silently_missing():
    """An entry interned with the wrong payload must fail loudly (CE0126), not decay."""
    poisoned = EnumType(
        name="Result<string, StdError>",
        variants=(
            EnumVariantInfo(name="Ok", associated_types=(BuiltinType.STRING,)),
            EnumVariantInfo(name="Err", associated_types=(UnknownType("StdError"),)),
        ),
    )
    table = table_with_std_error()
    table.by_name[poisoned.name] = poisoned
    table.order.append(poisoned.name)

    with pytest.raises(InternalCompilerError) as excinfo:
        ensure_result_type_in_table(table, BuiltinType.STRING, STD_ERROR)

    assert "CE0126" in str(excinfo.value)


def test_interning_is_idempotent():
    table = table_with_std_error()

    first = ensure_result_type_in_table(table, BuiltinType.I32, STD_ERROR)
    second = ensure_result_type_in_table(table, BuiltinType.I32, STD_ERROR)

    assert first is second
    assert table.order.count("Result<i32, StdError>") == 1


def test_maybe_unresolved_payload_is_resolved_before_interning():
    """The invariant is not Result-specific: Maybe<T> mangles its name the same way."""
    point = StructType(name="Point", fields=(("x", BuiltinType.I32),))
    table = FakeEnumTable({"Point": point})

    interned = ensure_maybe_type_in_table(table, UnknownType("Point"))

    some = interned.get_variant("Some")
    assert some.associated_types[0] == point
    assert not isinstance(some.associated_types[0], UnknownType)


def test_maybe_poisoned_intern_raises_rather_than_silently_missing():
    poisoned = EnumType(
        name="Maybe<Point>",
        variants=(
            EnumVariantInfo(name="Some", associated_types=(UnknownType("Point"),)),
            EnumVariantInfo(name="None", associated_types=()),
        ),
    )
    point = StructType(name="Point", fields=(("x", BuiltinType.I32),))
    table = FakeEnumTable({"Point": point})
    table.by_name[poisoned.name] = poisoned
    table.order.append(poisoned.name)

    with pytest.raises(InternalCompilerError) as excinfo:
        ensure_maybe_type_in_table(table, point)

    assert "CE0126" in str(excinfo.value)


# --- generic metadata (the CE2060 hole) ----------------------------------------------

def test_on_demand_maybe_carries_generic_metadata():
    """`unify.py` matches a `Maybe<T>` parameter by reading generic_base/generic_args."""
    table = FakeEnumTable()

    maybe_i32 = ensure_maybe_type_in_table(table, BuiltinType.I32)

    assert maybe_i32.generic_base == "Maybe"
    assert maybe_i32.generic_args == (BuiltinType.I32,)


def test_on_demand_result_carries_generic_metadata():
    table = table_with_std_error()

    result = ensure_result_type_in_table(table, BuiltinType.I32, STD_ERROR)

    assert result.generic_base == "Result"
    assert result.generic_args == (BuiltinType.I32, STD_ERROR)


# --- the helpers ---------------------------------------------------------------------

def test_is_result_enum_discriminates_result_from_other_enums():
    table = table_with_std_error()
    result = ensure_result_type_in_table(table, BuiltinType.I32, STD_ERROR)
    maybe = ensure_maybe_type_in_table(table, BuiltinType.I32)

    assert is_result_enum(result)
    assert not is_result_enum(maybe)
    assert not is_result_enum(STD_ERROR)
    assert not is_result_enum(BuiltinType.I32)
    assert not is_result_enum(None)


def test_result_ok_err_recovers_both_payloads():
    table = table_with_std_error()
    result = ensure_result_type_in_table(table, BuiltinType.STRING, STD_ERROR)

    ok, err = result_ok_err(result)

    assert ok == BuiltinType.STRING
    assert err == STD_ERROR


# --- a name NESTED in a payload -------------------------------------------------------

def _tables():
    """An enum table holding one named enum, plus the empty struct table."""
    ip_addr = EnumType(
        name="IpAddr",
        variants=(EnumVariantInfo(name="V4", associated_types=(BuiltinType.U32,)),),
    )
    enums = EnumTable()
    enums.by_name["IpAddr"] = ip_addr
    enums.order.append("IpAddr")
    return enums, {}, ip_addr


def test_a_nested_name_in_an_array_payload_is_resolved():
    """`Result@(IpAddr[], E)` interns with the element resolved to its table entry."""
    enums, structs, ip_addr = _tables()
    interned = ensure_result_type_in_table(
        enums, DynamicArrayType(UnknownType("IpAddr")), BuiltinType.I32, structs
    )
    assert interned is not None
    ok_payload = interned.variants[0].associated_types[0]
    assert isinstance(ok_payload, DynamicArrayType)
    assert ok_payload.base_type == ip_addr, (
        "the element stayed an UnknownType, so a later intern of the same Result "
        "rebuilds different variants and the guard fires CE0126"
    )


def test_a_name_with_no_table_entry_is_left_alone():
    """An unknown name is not invented: it stays unresolved and interns as itself."""
    enums, structs, _ = _tables()
    interned = ensure_result_type_in_table(
        enums, DynamicArrayType(UnknownType("Nowhere")), BuiltinType.I32, structs
    )
    assert interned is not None
    ok_payload = interned.variants[0].associated_types[0]
    assert isinstance(ok_payload, DynamicArrayType)
    assert isinstance(ok_payload.base_type, UnknownType)


def test_a_stored_instance_with_an_unresolved_nested_name_is_not_a_divergence():
    """An instance interned with a nested name left bare is the SAME type as a resolved one.

    This is the real shape, and it is why the guard used to fire on two identical
    spellings. `resolve_unknown_type` resolved the top-level payload, so the Result counted
    as concrete and reached the table -- while a name NESTED inside that payload, in a
    `FunctionType`'s error arm here, stayed bare because the walk did not recurse. A later
    intern resolves it, and depth is not divergence: same name, same meaning, one type.
    """
    enums, structs, _ = _tables()
    std_error = EnumType(
        name="StdError",
        variants=(EnumVariantInfo(name="Error", associated_types=()),),
    )
    enums.by_name["StdError"] = std_error
    enums.order.append("StdError")

    # Seed the table the way the shallow walk left it: the Result's own error arm resolved,
    # the one inside the function payload still a bare name.
    shallow_fn = FunctionType(
        param_types=(BuiltinType.I32,),
        ok_type=BuiltinType.I32,
        err_type=UnknownType("StdError"),
    )
    name = "Result<fn(i32) -> i32 | StdError, StdError>"
    enums.by_name[name] = EnumType(
        name=name,
        variants=(
            EnumVariantInfo(name="Ok", associated_types=(shallow_fn,)),
            EnumVariantInfo(name="Err", associated_types=(std_error,)),
        ),
        generic_base="Result",
        generic_args=(shallow_fn, std_error),
    )
    enums.order.append(name)

    interned = ensure_result_type_in_table(enums, shallow_fn, std_error, structs)
    assert interned is not None
    assert interned.name == name


def test_a_real_divergence_still_fires_the_guard():
    """The guard keeps its job: two DIFFERENT payloads under one name are still a bug."""
    enums, structs, _ = _tables()
    ensure_result_type_in_table(enums, BuiltinType.I32, BuiltinType.I32, structs)
    # Reach into the table and corrupt the stored Ok payload, which is the shape a
    # structurally-built Result produces.
    name = "Result<i32, i32>"
    stored = enums.by_name[name]
    enums.by_name[name] = EnumType(
        name=name,
        variants=(
            EnumVariantInfo(name="Ok", associated_types=(BuiltinType.STRING,)),
            stored.variants[1],
        ),
        generic_base="Result",
        generic_args=(BuiltinType.STRING, BuiltinType.I32),
    )
    with pytest.raises(InternalCompilerError):
        ensure_result_type_in_table(enums, BuiltinType.I32, BuiltinType.I32, structs)


def test_the_maybe_intern_resolves_a_nested_name_too():
    """`Maybe@(IpAddr[])` shares the seam: both spellings, either order, one instance (#793)."""
    enums, structs, ip_addr = _tables()
    first = ensure_maybe_type_in_table(enums, DynamicArrayType(ip_addr), structs)
    second = ensure_maybe_type_in_table(
        enums, DynamicArrayType(UnknownType("IpAddr")), structs
    )
    assert first is second
    assert first is not None
    some_payload = first.variants[0].associated_types[0]
    assert isinstance(some_payload, DynamicArrayType)
    assert some_payload.base_type == ip_addr


def test_an_abstract_maybe_stays_out_of_the_table():
    """A `Maybe@(T)` over an enclosing template's parameter is handed back, not stored."""
    enums, structs, _ = _tables()
    interned = ensure_maybe_type_in_table(enums, TypeParameter("T"), structs)
    assert interned is not None
    assert interned.name not in enums.by_name


# --- one type, whatever the spelling ---------------------------------------------------

def _bare(ip_addr):
    return UnknownType("IpAddr"), ip_addr


def _nested(ip_addr):
    return DynamicArrayType(UnknownType("IpAddr")), DynamicArrayType(ip_addr)


def _nested_resolved_first(ip_addr):
    unresolved, resolved = _nested(ip_addr)
    return resolved, unresolved


@pytest.mark.parametrize("spellings", [_bare, _nested, _nested_resolved_first],
                         ids=["bare", "nested", "nested-resolved-first"])
def test_both_spellings_intern_to_one_object(spellings):
    """The #184 repro, at the type level: a payload by name and by table entry intern
    to the SAME enum, in either order, and never to a second table entry (CE0126)."""
    enums, structs, ip_addr = _tables()
    first_payload, second_payload = spellings(ip_addr)

    first = ensure_result_type_in_table(enums, first_payload, BuiltinType.I32, structs)
    second = ensure_result_type_in_table(enums, second_payload, BuiltinType.I32, structs)

    assert first is second
    assert len([n for n in enums.order if n.startswith("Result<")]) == 1
