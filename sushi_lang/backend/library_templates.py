"""Serialization codec for public generic templates shipped in .slib files.

Phase 2 of the cross-library generics feature ships *instantiable* generic
function bodies inside the .slib manifest so the consumer can monomorphize them
locally. The chosen (locked) design reconstructs an imported generic by
**re-parsing its source text** through the existing frontend - not by a typed-AST
node codec and not by IR. This keeps the serialized form trivially stable and
sidesteps the cycles/spans/type-ref hazards of pickling a typed AST.

This module is the producer/consumer codec for a single generic function record:

    serialize_generic_function(func, source_text) -> dict   (producer side)
    deserialize_generic_function(record) -> FuncDef          (consumer side)

The record schema (one entry in manifest["templates"]["generic_functions"]):

    {
        "name": str,                       # function name
        "type_params": [                   # ordered, authoritative for constraints
            {"name": str, "constraints": [str, ...]},
            ...
        ],
        "source": str,                     # self-contained, re-parsable decl text
        "free_perks": [str, ...],          # sorted perk names from type-param bounds
    }

The ``source`` slice is the crux: it covers the WHOLE declaration, from the
``fn`` / ``public fn`` keyword through the end of the indented body, and ends in
a trailing newline so it re-parses on its own.

Parser/AST imports are done lazily inside functions to keep this module
dependency-light and avoid import cycles with the frontend.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import FuncDef, PerkDef


def _free_perks_of(func: "FuncDef") -> List[str]:
    """Collect the sorted, de-duplicated set of perk names named in the
    type-parameter constraints of ``func``."""
    perks: set[str] = set()
    for tp in (func.type_params or []):
        for c in (getattr(tp, "constraints", None) or []):
            perks.add(c)
    return sorted(perks)


def slice_decl_source(node, source_text: str) -> str:
    """Slice the full, self-contained source text of one top-level declaration.

    Works for any node carrying a line-based ``loc`` ``Span`` (functions AND
    perks - both are top-level decls whose body is the last thing they contain).

    Strategy (verified empirically against the Lark frontend with
    ``propagate_positions=True``):

    - ``node.loc`` is a line/column ``Span`` (NOT a character offset). Its
      ``line`` is the line of the ``fn`` / ``public fn`` / ``perk`` keyword
      (always at column 1 for a top-level declaration) and its ``end_line`` is
      the line on which the *next* token begins. Because the body is the last
      thing in the decl, ``end_line`` overshoots into the blank-line gap before
      the next declaration (or one line past EOF for the final declaration).

    - We therefore take the 1-based inclusive line range
      ``[loc.line, loc.end_line)`` (i.e. up to but excluding ``end_line``),
      then strip trailing blank lines and guarantee a single trailing newline.

    This yields exactly the declaration header plus its indented body, with no
    leading indentation (top-level decls start at column 1), so the slice
    re-parses as a standalone program.
    """
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
    """Produce the manifest record for a single public generic function.

    Args:
        func: The generic ``FuncDef`` to export (must have non-empty type_params).
        source_text: Full source text of the unit the function lives in.

    Returns:
        A msgpack-safe dict matching the record schema documented above.
    """
    type_params = [
        {"name": tp.name, "constraints": list(getattr(tp, "constraints", None) or [])}
        for tp in (func.type_params or [])
    ]
    return {
        "name": func.name,
        "type_params": type_params,
        "source": slice_decl_source(func, source_text),
        "free_perks": _free_perks_of(func),
    }


def deserialize_generic_function(record: dict) -> "FuncDef":
    """Reconstruct a ``FuncDef`` from a manifest record by re-parsing its source.

    The record's ``type_params`` are authoritative for constraints: after
    parsing, each rebuilt ``BoundedTypeParam``'s constraints are reconciled
    against the record (the parsed source already carries them, but the record
    is the source of truth and guards against any future divergence).

    Args:
        record: A record produced by ``serialize_generic_function``.

    Returns:
        The single ``FuncDef`` parsed from ``record["source"]``.
    """
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

    # Reconcile constraints against the authoritative record.
    rec_tps = record.get("type_params") or []
    parsed_tps = func.type_params or []
    if len(rec_tps) == len(parsed_tps):
        for parsed_tp, rec_tp in zip(parsed_tps, rec_tps):
            parsed_tp.constraints = list(rec_tp.get("constraints") or [])

    return func


def serialize_perk(perk: "PerkDef", source_text: str) -> dict:
    """Produce the manifest record for a single perk DEFINITION (the contract).

    Only the perk's definition (its method signatures) is shipped - never its
    implementations (``extend T with Perk``). The consumer still provides impls
    for its own instantiation types; shipping the definition merely frees the
    consumer from having to redeclare a perk a library's exported generic
    constrains on.

    Args:
        perk: The ``PerkDef`` to export.
        source_text: Full source text of the unit the perk lives in.

    Returns:
        A msgpack-safe dict: ``{"name": str, "source": str}``.
    """
    return {
        "name": perk.name,
        "source": slice_decl_source(perk, source_text),
    }


def deserialize_perk(record: dict) -> "PerkDef":
    """Reconstruct a ``PerkDef`` from a manifest record by re-parsing its source.

    Mirrors ``deserialize_generic_function``.

    Args:
        record: A record produced by ``serialize_perk``.

    Returns:
        The single ``PerkDef`` parsed from ``record["source"]``.
    """
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
