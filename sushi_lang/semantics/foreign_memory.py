"""The closed set of methods on a foreign `ptr` (#1086, docs/design/ffi-memory.md).

A load or a store of one width at a byte offset, a pointer read and a pointer store, an
address at an offset, and a copy of a C string. Every other method on a `ptr` stays
CE5011. The `ptr` type itself is confined to a unit that declares an `unsafe external`
block (CE5009), so these methods are too, with no gate of their own.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional

from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType, Type

#: The widths a load and a store name, by the suffix of the method.
FOREIGN_PTR_WIDTHS: Mapping[str, BuiltinType] = MappingProxyType({
    ty.value: ty for ty in (
        BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
        BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
        BuiltinType.F32, BuiltinType.F64,
    )
})

#: The answer of `load_ptr`: the interned `Maybe@(ptr)`, built where the enum table is.
MAYBE_PTR = "Maybe@(ptr)"


@dataclass(frozen=True)
class PtrMethod:
    """One method: its parameters after the receiver, and what it answers."""
    params: tuple[Type, ...]
    returns: Type | str


def _table() -> Mapping[str, PtrMethod]:
    offset = BuiltinType.I32
    rows: dict[str, PtrMethod] = {}
    for suffix, ty in FOREIGN_PTR_WIDTHS.items():
        rows[f"load_{suffix}"] = PtrMethod((offset,), ty)
        rows[f"store_{suffix}"] = PtrMethod((offset, ty), BuiltinType.BLANK)
    rows["load_ptr"] = PtrMethod((offset,), MAYBE_PTR)
    rows["store_ptr"] = PtrMethod((offset, ForeignPtrType()), BuiltinType.BLANK)
    rows["offset"] = PtrMethod((offset,), ForeignPtrType())
    rows["to_string"] = PtrMethod((offset,), BuiltinType.STRING)
    return MappingProxyType(rows)


FOREIGN_PTR_METHODS = _table()

FOREIGN_PTR_METHOD_ARITY: Mapping[str, int] = MappingProxyType(
    {name: len(row.params) for name, row in FOREIGN_PTR_METHODS.items()})


def foreign_ptr_return_type(name: str, enums: Any, structs: Optional[dict] = None
                            ) -> Optional[Type]:
    """What `p.<name>(...)` answers, with `Maybe@(ptr)` interned in `enums`."""
    row = FOREIGN_PTR_METHODS.get(name)
    if row is None:
        return None
    if isinstance(row.returns, str):
        from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
        return ensure_maybe_type_in_table(enums, ForeignPtrType(), struct_table=structs)
    return row.returns
