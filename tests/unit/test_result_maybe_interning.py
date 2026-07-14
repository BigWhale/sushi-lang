"""`Result<T, E>` and `Maybe<T>` must intern into the enum table on identical terms.

`Maybe<T>` has always been an ordinary interned `EnumType`. `Result<T, E>` additionally has a
bespoke `ResultType` dataclass, and that dual representation is the root of #179 (RAII leak)
and #184 (type identity). These tests pin the properties the consolidation depends on, so a
regression turns the suite red rather than decaying into a silent cache miss.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.errors import InternalCompilerError
from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
from sushi_lang.semantics.generics.results import (
    ensure_result_type_in_table,
    is_result_enum,
    result_ok_err,
)
from sushi_lang.semantics.typesys import (
    BuiltinType,
    EnumType,
    EnumVariantInfo,
    StructType,
    UnknownType,
)


class FakeEnumTable:
    """The duck-typed shape both ensure_* helpers consume: `.by_name` and `.order`."""

    def __init__(self, by_name=None):
        self.by_name = dict(by_name or {})
        self.order = list(self.by_name)


STD_ERROR = EnumType(
    name="StdError",
    variants=(EnumVariantInfo(name="Error", associated_types=()),),
)


def table_with_std_error() -> FakeEnumTable:
    return FakeEnumTable({"StdError": STD_ERROR})


# --- the invariant -------------------------------------------------------------------

def test_unresolved_error_payload_is_resolved_before_interning():
    """`UnknownType("StdError")` must not reach the table as a payload.

    `str(UnknownType("StdError")) == str(EnumType("StdError")) == "StdError"`, so both spellings
    mangle to the SAME enum name. `EnumType` hashes on the name but compares on the variants, so
    an unresolved payload would hash-match and compare unequal -- a silent cache miss. The
    resolution happens inside ensure_result_type_in_table so a caller cannot opt out.
    """
    table = table_with_std_error()

    interned = ensure_result_type_in_table(table, BuiltinType.STRING, UnknownType("StdError"))

    _, err = result_ok_err(interned)
    assert err == STD_ERROR
    assert not isinstance(err, UnknownType)


def test_resolved_and_unresolved_spellings_intern_to_one_object():
    """The #184 repro, at the type level: both spellings must be the SAME interned enum."""
    table = table_with_std_error()

    from_registry = ensure_result_type_in_table(table, BuiltinType.STRING, UnknownType("StdError"))
    from_annotation = ensure_result_type_in_table(table, BuiltinType.STRING, STD_ERROR)

    assert from_registry is from_annotation
    assert from_registry == from_annotation
    assert len([n for n in table.order if n.startswith("Result<")]) == 1


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
    """The invariant is not Result-specific: Maybe<T> mangles its name the same way.

    `str(UnknownType("Point"))` and `str(StructType(name="Point"))` are both "Point", so a Maybe
    interned with an unresolved payload lands under the SAME name as the monomorphized one while
    carrying different variants -- it hash-matches and compares unequal. Silent, exactly like the
    Result case (CE0126).
    """
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
    """`unify.py` matches a `Maybe<T>` parameter by reading generic_base/generic_args.

    Without them an on-demand `Maybe` (the return of `List.get` and friends) unified against
    nothing and generic inference died with CE2060.
    """
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
