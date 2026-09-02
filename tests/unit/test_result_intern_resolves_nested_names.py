"""The one Result intern must resolve a name NESTED in a payload, not only a bare one.

`ensure_result_type_in_table` is the single seam that interns a `Result@(T, E)`
(docs/design/type-identity.md: never build one structurally). It resolved a bare
`UnknownType` payload and stopped there, so `IpAddr[]` -- a `DynamicArrayType` whose
ELEMENT is a name -- was interned with the unresolved element. The interned NAME is the
same either way, so the two instances collide on the next intern and the guard fires
CE0126 with two identical spellings, which says nothing about what actually differs.
"""
import pytest

from sushi_lang.semantics.generics.results import ensure_result_type_in_table
from sushi_lang.semantics.passes.collect.enums import EnumTable
from sushi_lang.semantics.typesys import (
    BuiltinType,
    DynamicArrayType,
    EnumType,
    EnumVariantInfo,
    UnknownType,
)


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


def test_the_unresolved_and_resolved_spellings_intern_to_one_type():
    """Interning by name and by table entry answers the SAME instance, not CE0126."""
    enums, structs, ip_addr = _tables()
    first = ensure_result_type_in_table(
        enums, DynamicArrayType(UnknownType("IpAddr")), BuiltinType.I32, structs
    )
    second = ensure_result_type_in_table(
        enums, DynamicArrayType(ip_addr), BuiltinType.I32, structs
    )
    assert first is second


def test_the_order_of_the_two_spellings_does_not_matter():
    """The resolved spelling first, then the bare name: still one instance."""
    enums, structs, ip_addr = _tables()
    first = ensure_result_type_in_table(
        enums, DynamicArrayType(ip_addr), BuiltinType.I32, structs
    )
    second = ensure_result_type_in_table(
        enums, DynamicArrayType(UnknownType("IpAddr")), BuiltinType.I32, structs
    )
    assert first is second


def test_a_bare_name_payload_still_resolves():
    """The case that already worked keeps working: a payload that IS the name."""
    enums, structs, ip_addr = _tables()
    interned = ensure_result_type_in_table(
        enums, UnknownType("IpAddr"), BuiltinType.I32, structs
    )
    assert interned is not None
    assert interned.variants[0].associated_types[0] == ip_addr


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
    from sushi_lang.semantics.typesys import FunctionType

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
    name = "Result<fn(i32) -> i32, StdError>"
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
    from sushi_lang.internals.diagnostics import InternalCompilerError

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
