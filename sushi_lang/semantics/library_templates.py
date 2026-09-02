"""The parts of a .slib manifest that come from the AST: generic templates, and docs.

Two producers write a manifest -- this module and `backend/library_manifest.py` -- and
both build a `doc` record through `doc_record` here. One function, so the record shape
cannot differ between a concrete symbol and a template.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import (
        DocBlock, FuncDef, PerkDef, StructDef, EnumDef, ExtendWithDef,
    )

# The two tags that are singletons, and the only two that reach a record as their own
# key. `- Parameter` rides in the `params` map, and an `- Example:` in `examples`.
_SINGLETON_KINDS = ("returns", "errors")


def doc_record(doc: Optional["DocBlock"]) -> Optional[dict]:
    """One doc block as a manifest record, or None when it says nothing.

    Every field is optional and an EMPTY one is omitted, so a reader that has only
    `ml_get_str` cannot mistake an absent field for an empty string. The whole block
    (`DocBlock.text`) is deliberately not carried: the index would then hold its own
    input, duplicating text a source library already ships verbatim
    (`docs/design/documentation.md` section 8).
    """
    if doc is None:
        return None

    params: Dict[str, str] = {}
    singletons: Dict[str, str] = {}
    for tag in doc.tags:
        if tag.kind == "parameter" and tag.name and tag.text:
            params.setdefault(tag.name, tag.text)
        elif tag.kind in _SINGLETON_KINDS and tag.text:
            singletons.setdefault(tag.kind, tag.text)

    # The CAPTION and the CODE of each example, in source order, and not its fence
    # attributes: an attribute is an instruction to the doc-test harness and not
    # documentation (documentation.md section 10, R23). A defective one carries a
    # diagnostic of its own and nothing a consumer could render.
    #
    # The caption is the tag's own text, and it pairs with the code by POSITION: both
    # lists walk the block's `- Example:` items in source order and neither filters, so
    # the i-th example tag introduces the i-th example. The filter below happens after
    # the pairing, for that reason.
    captions = [tag.text for tag in doc.tags if tag.kind == "example"]
    examples = []
    for index, example in enumerate(doc.examples):
        if example.defect is not None or not example.code.strip():
            continue
        entry = {"code": example.code}
        caption = captions[index] if index < len(captions) else ""
        if caption:
            entry["caption"] = caption
        examples.append(entry)

    record: dict = {}
    if doc.summary:
        record["summary"] = doc.summary
    if doc.body:
        record["body"] = doc.body
    if params:
        record["params"] = params
    for kind in _SINGLETON_KINDS:
        if kind in singletons:
            record[kind] = singletons[kind]
    if examples:
        record["examples"] = examples

    return record or None


def with_doc(record: dict, node) -> dict:
    """`record` plus the `doc` key, when `node` carries a block that says something.

    The key is ABSENT otherwise, which is what makes an undocumented library grow by
    nothing. Every producer adds a doc through here, so no record can grow a second way.
    """
    doc = doc_record(getattr(node, "doc", None))
    if doc is not None:
        record["doc"] = doc
    return record


def _free_perks_of(node) -> List[str]:
    """Collect the sorted, de-duplicated set of perk names named in the type-parameter constraints
    of ``node``.
    """
    perks: set[str] = set()
    for tp in (node.type_params or []):
        for c in (getattr(tp, "constraints", None) or []):
            perks.add(c)
    return sorted(perks)


def type_string(ty) -> str:
    """One type, as the manifest spells it. `~` for none.

    The INTERNAL identity spelling, `List<i32>` and not `List@(i32)`: a consumer reads
    these back through `parse_type_string`, so this is a wire format and not display
    text. Rendering `@(...)` is the report's job (`docs/design/type-identity.md`).
    """
    return "~" if ty is None else str(ty)


def signature_record(func: "FuncDef") -> dict:
    """The signature half of a function record: the parameters, the return, the error arm.

    ONE builder, for the concrete record and the generic one alike. A generic used to
    carry no parameter list at all, so its `- Parameter` tags named nothing a renderer
    could print them against (`docs/design/documentation.md`, R46).
    """
    from sushi_lang.semantics.param_modes import param_mode

    record: dict = {
        # The MODE is its own field, not part of the type string. A `nom` cannot be
        # spelled in a type at all, and reading peek / poke back out of a type string
        # was the half that was missing (docs/design/borrow-model.md S10).
        #
        # A parameter record carries no `doc`: per-parameter text lives in the
        # enclosing function's `doc.params`, keyed by name.
        "params": [
            {"name": p.name, "type": type_string(p.ty), "mode": param_mode(p).value}
            for p in func.params
        ],
        "return_type": type_string(func.ret),
    }
    # Absent when the signature does not say one: the default is StdError, and a record
    # that spelled the default would claim the author wrote it.
    if getattr(func, "err_type", None) is not None:
        record["error_type"] = type_string(func.err_type)
    return record


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
    return with_doc({
        "name": func.name,
        "type_params": _type_param_records(func),
        **signature_record(func),
        "source": slice_decl_source(func, source_text),
        "free_perks": _free_perks_of(func),
    }, func)


def deserialize_generic_function(record: dict) -> "FuncDef":
    """Reconstruct a ``FuncDef`` from a manifest record by re-parsing its source."""
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
    return with_doc({
        "name": struct.name,
        "type_params": _type_param_records(struct),
        "source": slice_decl_source(struct, source_text),
        "free_perks": _free_perks_of(struct),
    }, struct)


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
    return with_doc({
        "name": enum.name,
        "type_params": _type_param_records(enum),
        "source": slice_decl_source(enum, source_text),
        "free_perks": _free_perks_of(enum),
    }, enum)


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
    from sushi_lang.semantics.generics.name_mangling import sanitize_extension_receiver
    return f"{sanitize_extension_receiver(type_name)}_{method_name}"


def serialize_perk_impl(impl: "ExtendWithDef", source_text: str) -> dict:
    """Produce the manifest record for one concrete perk IMPLEMENTATION."""
    from sushi_lang.semantics.passes.collect.perks import _get_type_name

    type_name = _get_type_name(impl.target_type)
    return with_doc({
        "type": type_name,
        "perk": impl.perk_name,
        "source": slice_decl_source(impl, source_text),
        # The method records are where a perk method's own block lands. A perk
        # DEFINITION has no such array, so its methods' blocks travel only inside the
        # source slice (documentation.md section 8, R3).
        "methods": [
            with_doc({"name": m.name, "symbol": impl_method_symbol(type_name, m.name)}, m)
            for m in impl.methods
        ],
    }, impl)


def serialize_generic_perk_impl(impl: "ExtendWithDef", source_text: str) -> dict:
    """The manifest record for a GENERIC-target perk implementation: a template (#543).

    `extend Box@(T) with Show` names no instantiation, so there is no symbol to declare
    and link: it ships as source alone, and the consumer cuts one copy per instantiation
    of `Box` it names, exactly as it does for its own template. `type` is the target's
    BASE name and `type_args` the parameters as written, so a reader can list it without
    a parser; `deserialize_perk_impl` reads the source back.
    """
    target = impl.target_type
    return with_doc({
        "type": target.base_name,
        "type_args": [str(a) for a in target.type_args],
        "perk": impl.perk_name,
        "source": slice_decl_source(impl, source_text),
    }, impl)


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
    return with_doc({
        "name": perk.name,
        "source": slice_decl_source(perk, source_text),
    }, perk)


def deserialize_perk(record: dict) -> "PerkDef":
    """Reconstruct a ``PerkDef`` from a manifest record by re-parsing its source."""
    from sushi_lang.internals.parser import parse_to_ast

    program, _tree = parse_to_ast(record["source"])

    perks = program.perks or []
    if len(perks) != 1:
        raise ValueError(
            f"template source for perk '{record.get('name')}' parsed to "
            f"{len(perks)} perks, expected exactly 1"
        )
    return perks[0]


def apply_template_bindings(body, bindings: dict) -> None:
    """Rewrite a re-parsed template body's calls to the producer's symbols (D4).

    A binary library has no `Unit` at the consumer, so a free name in a template
    body cannot resolve through a scope. The producer resolved each one and wrote
    the map down; this binds every named call the map covers to that symbol. The
    symbol is registered as an alias key beside the record it names, and `$` lies
    outside every user name's alphabet, so the rewritten callee can mean nothing
    else. Only a CALL's callee is rewritten: the closure ships concrete private
    FUNCTIONS in the map, and a bare reference to one is not expressible for a
    library-private name.
    """
    import dataclasses
    from sushi_lang.semantics import ast as A

    def _walk(node) -> None:
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)
            return
        if isinstance(node, A.Call) and isinstance(node.callee, A.Name):
            symbol = bindings.get(node.callee.id)
            if symbol is not None:
                node.callee.id = symbol
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            for f in dataclasses.fields(node):
                _walk(getattr(node, f.name, None))

    _walk(body)
