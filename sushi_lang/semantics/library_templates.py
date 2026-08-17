"""Serialization codec for public generic templates shipped in .slib files."""
from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import (
        FuncDef, PerkDef, StructDef, EnumDef, ExtendWithDef,
    )


def _free_perks_of(node) -> List[str]:
    """Collect the sorted, de-duplicated set of perk names named in the type-parameter constraints
    of ``node``.
    """
    perks: set[str] = set()
    for tp in (node.type_params or []):
        for c in (getattr(tp, "constraints", None) or []):
            perks.add(c)
    return sorted(perks)


def _type_param_records(node) -> List[dict]:
    """Serialize a declaration's bounded type parameters to msgpack-safe dicts."""
    return [
        {
            "name": tp.name,
            "constraints": list(getattr(tp, "constraints", None) or []),
            "is_pack": bool(getattr(tp, "is_pack", False)),
        }
        for tp in (node.type_params or [])
    ]


def _reconcile_type_params(parsed_node, record: dict) -> None:
    """Reconcile a re-parsed declaration's type-param constraints / pack marker against the
    authoritative manifest record (the source of truth).
    """
    rec_tps = record.get("type_params") or []
    parsed_tps = parsed_node.type_params or []
    if len(rec_tps) == len(parsed_tps):
        for parsed_tp, rec_tp in zip(parsed_tps, rec_tps, strict=False):
            parsed_tp.constraints = list(rec_tp.get("constraints") or [])
            if "is_pack" in rec_tp:
                parsed_tp.is_pack = bool(rec_tp["is_pack"])


def slice_decl_source(node, source_text: str) -> str:
    """Slice the full, self-contained source text of one top-level declaration."""
    loc = getattr(node, "loc", None)
    name = getattr(node, "name", "<decl>")
    if loc is None:
        raise ValueError(
            f"cannot slice source for '{name}': missing location span"
        )

    lines = source_text.splitlines(keepends=True)
    n = len(lines)

    start = loc.line - 1          # 0-based, inclusive
    # end_line points at the line where the next token begins; the decl's own
    # content ends on the previous line. Clamp to the file length for the final
    # declaration (whose end_line can be one past EOF).
    end = (loc.end_line - 1) if loc.end_line is not None else n
    if end > n:
        end = n
    if end <= start:
        end = start + 1

    decl_lines = lines[start:end]

    # Strip trailing blank lines that the span overshot into.
    while decl_lines and decl_lines[-1].strip() == "":
        decl_lines.pop()

    if not decl_lines:
        raise ValueError(
            f"cannot slice source for '{name}': empty declaration range"
        )

    slice_text = "".join(decl_lines)
    if not slice_text.endswith("\n"):
        slice_text += "\n"
    return slice_text


def serialize_generic_function(func: "FuncDef", source_text: str) -> dict:
    """Produce the manifest record for a single public generic function."""
    return {
        "name": func.name,
        "type_params": _type_param_records(func),
        "source": slice_decl_source(func, source_text),
        "free_perks": _free_perks_of(func),
    }


def deserialize_generic_function(record: dict) -> "FuncDef":
    """Reconstruct a ``FuncDef`` from a manifest record by re-parsing its source."""
    # Lazy import to avoid frontend import cycles.
    from sushi_lang.internals.parser import parse_to_ast

    program, _tree = parse_to_ast(record["source"])

    funcs = program.functions or []
    if len(funcs) != 1:
        raise ValueError(
            f"template source for '{record.get('name')}' parsed to "
            f"{len(funcs)} functions, expected exactly 1"
        )
    func = funcs[0]
    _reconcile_type_params(func, record)
    return func


def serialize_generic_struct(struct: "StructDef", source_text: str) -> dict:
    """Produce the manifest record for a single public generic struct."""
    return {
        "name": struct.name,
        "type_params": _type_param_records(struct),
        "source": slice_decl_source(struct, source_text),
        "free_perks": _free_perks_of(struct),
    }


def deserialize_generic_struct(record: dict) -> "StructDef":
    """Reconstruct a ``StructDef`` from a manifest record by re-parsing source."""
    from sushi_lang.internals.parser import parse_to_ast

    program, _tree = parse_to_ast(record["source"])

    structs = program.structs or []
    if len(structs) != 1:
        raise ValueError(
            f"template source for struct '{record.get('name')}' parsed to "
            f"{len(structs)} structs, expected exactly 1"
        )
    struct = structs[0]
    _reconcile_type_params(struct, record)
    return struct


def serialize_generic_enum(enum: "EnumDef", source_text: str) -> dict:
    """Produce the manifest record for a single public generic enum."""
    return {
        "name": enum.name,
        "type_params": _type_param_records(enum),
        "source": slice_decl_source(enum, source_text),
        "free_perks": _free_perks_of(enum),
    }


def deserialize_generic_enum(record: dict) -> "EnumDef":
    """Reconstruct an ``EnumDef`` from a manifest record by re-parsing source."""
    from sushi_lang.internals.parser import parse_to_ast

    program, _tree = parse_to_ast(record["source"])

    enums = program.enums or []
    if len(enums) != 1:
        raise ValueError(
            f"template source for enum '{record.get('name')}' parsed to "
            f"{len(enums)} enums, expected exactly 1"
        )
    enum = enums[0]
    _reconcile_type_params(enum, record)
    return enum


def impl_method_symbol(type_name: str, method_name: str) -> str:
    """Compute the LLVM symbol name of a perk-impl method."""
    sanitized = type_name.replace("<", "__").replace(">", "").replace(", ", "_")
    return f"{sanitized}_{method_name}"


def serialize_perk_impl(impl: "ExtendWithDef", source_text: str) -> dict:
    """Produce the manifest record for one concrete perk IMPLEMENTATION."""
    from sushi_lang.semantics.passes.collect.perks import _get_type_name

    type_name = _get_type_name(impl.target_type)
    return {
        "type": type_name,
        "perk": impl.perk_name,
        "source": slice_decl_source(impl, source_text),
        "methods": [
            {"name": m.name, "symbol": impl_method_symbol(type_name, m.name)}
            for m in impl.methods
        ],
    }


def deserialize_perk_impl(record: dict) -> "ExtendWithDef":
    """Reconstruct an ``ExtendWithDef`` from a manifest record by re-parsing."""
    from sushi_lang.internals.parser import parse_to_ast

    program, _tree = parse_to_ast(record["source"])

    impls = program.perk_impls or []
    if len(impls) != 1:
        raise ValueError(
            f"template source for perk impl '{record.get('type')} with "
            f"{record.get('perk')}' parsed to {len(impls)} impls, expected exactly 1"
        )
    return impls[0]


def serialize_perk(perk: "PerkDef", source_text: str) -> dict:
    """Produce the manifest record for a single perk DEFINITION (the contract)."""
    return {
        "name": perk.name,
        "source": slice_decl_source(perk, source_text),
    }


def deserialize_perk(record: dict) -> "PerkDef":
    """Reconstruct a ``PerkDef`` from a manifest record by re-parsing its source."""
    # Lazy import to avoid frontend import cycles.
    from sushi_lang.internals.parser import parse_to_ast

    program, _tree = parse_to_ast(record["source"])

    perks = program.perks or []
    if len(perks) != 1:
        raise ValueError(
            f"template source for perk '{record.get('name')}' parsed to "
            f"{len(perks)} perks, expected exactly 1"
        )
    return perks[0]
