"""The parts of a .slib manifest that come from the AST: generic templates, and docs.

Two producers write a manifest -- this module and `compiler/library_manifest.py` -- and
both build a `doc` record through `doc_record` here. One function, so the record shape
cannot differ between a concrete symbol and a template.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import (
        DocBlock, FuncDef, PerkDef, StructDef, EnumDef, ExtendDef, ExtendWithDef,
    )

# The two tags that are singletons, and the only two that reach a record as their own
# key. `- Parameter` rides in the `params` map, and an `- Example:` in `examples`.
_SINGLETON_KINDS = ("returns", "errors")


def doc_record(doc: Optional["DocBlock"]) -> Optional[dict]:
    """One doc block as a manifest record, or None when it says nothing.

    Every field is optional and an EMPTY one is omitted, so a reader that has only
    `get_str` cannot mistake an absent field for an empty string. The whole block
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


def signature_record(func: "FuncDef | ExtendDef") -> dict:
    """The signature half of a function record: the parameters, the return, the error arm.

    ONE builder, for the concrete record and the generic one alike. A generic used to
    carry no parameter list at all, so its `- Parameter` tags named nothing a renderer
    could print them against (`docs/design/documentation.md`, R46).
    """
    from sushi_lang.semantics.channel import has_channel
    from sushi_lang.semantics.param_modes import ReturnMode, param_mode

    record: dict = {
        # The MODE is its own field, not part of the type string. A `nom` cannot be
        # spelled in a type at all, and reading peek / poke back out of a type string
        # was the half that was missing (docs/design/borrow-model.md S10).
        #
        # A parameter record carries no `doc`: per-parameter text lives in the
        # enclosing function's `doc.params`, keyed by name.
        #
        # `is_pack` is written for the parameter that takes a type pack (`...Ts args`)
        # alone: its type string is the pack's bare name, and the report needs the `...`.
        # `variadic` is written for a native `...T` parameter alone (a source library):
        # its type string is the element type `T` as written, not the `T[]` of the body.
        "params": [
            {"name": p.name, "type": type_string(p.ty.base_type if p.is_variadic else p.ty),
             "mode": param_mode(p).value,
             **({"is_pack": True} if p.is_pack else {}),
             **({"variadic": True} if p.is_variadic else {})}
            for p in func.params
        ],
        "return_type": type_string(func.ret),
        # Stated for every callable, so a reader never guesses a channel from an absent
        # key (docs/design/error-channel.md). An explicit `Result@(T, E)` return has
        # one and carries its arms in `return_type`.
        "has_channel": has_channel(func),
    }
    if getattr(func, "err_type", None) is not None:
        record["error_type"] = type_string(func.err_type)
    # Written for a borrowed return alone (#1229): an absent field is an owned return.
    if getattr(func, "borrowed_return", None) is not None:
        record["return_mode"] = ReturnMode.PEEK.value
    return record


def _type_param_records(node) -> List[dict]:
    """Serialize a declaration's bounded type parameters to msgpack-safe dicts."""
    return [
        {
            "name": tp.name,
            "constraints": list(getattr(tp, "constraints", None) or []),
            "is_pack": bool(tp.is_pack),
        }
        for tp in (node.type_params or [])
    ]


def target_bound_records(node) -> List[dict]:
    """The bounds a target writes, for the report alone (#1070).

    The source section carries the bounds, and a consumer compiles it. The report reads
    this record, so a reader lists `List@(T: Clone)` without a parser. It is empty when
    no argument has a bound; otherwise it holds each parameter of the target in order.
    """
    params = [param for param in node.target_params if param is not None]
    if not any(param.constraints for param in params):
        return []
    return [{"param": param.name, "perks": param.written_constraints()}
            for param in params]


def serialize_generic_function(func: "FuncDef") -> dict:
    """Produce the manifest record for a single public generic function."""
    return with_doc({
        "name": func.name,
        "type_params": _type_param_records(func),
        **signature_record(func),
        "free_perks": _free_perks_of(func),
    }, func)


def serialize_generic_struct(struct: "StructDef") -> dict:
    """Produce the manifest record for a single public generic struct."""
    return with_doc({
        "name": struct.name,
        "type_params": _type_param_records(struct),
        "free_perks": _free_perks_of(struct),
    }, struct)


def serialize_generic_enum(enum: "EnumDef") -> dict:
    """Produce the manifest record for a single public generic enum."""
    return with_doc({
        "name": enum.name,
        "is_error": enum.is_error,
        "type_params": _type_param_records(enum),
        "free_perks": _free_perks_of(enum),
    }, enum)


def impl_method_symbol(type_name: str, method_name: str) -> str:
    """The LLVM symbol of a perk-impl method: the extension symbol, which declares it."""
    from sushi_lang.semantics.generics.name_mangling import extension_symbol
    return extension_symbol(type_name, method_name)


def method_record(method) -> dict:
    """The record of one perk method: its name, its signature, its receiver mode, its block.

    ONE builder for the contract's `PerkMethodSignature` and an implementation's
    `FuncDef` alike, so `--lib-info` prints a contract method exactly as it prints the
    implementation of it (#537). `self_mode` is present only when the method declares
    `peek self` / `poke self`: a type string cannot spell the receiver's mode, and a
    record that spelled the default would claim the author wrote it.
    """
    record = {"name": method.name, **signature_record(method)}
    self_mode = getattr(method, "self_mode", None)
    if self_mode is not None:
        record["self_mode"] = self_mode
    return with_doc(record, method)


def serialize_perk_impl(impl: "ExtendWithDef") -> dict:
    """Produce the manifest record for one concrete perk IMPLEMENTATION."""
    from sushi_lang.semantics.passes.collect.perks import perk_type_name

    type_name = perk_type_name(impl.target_type)
    return with_doc({
        "type": type_name,
        "perk": impl.perk_name,
        "methods": [method_record(m) for m in impl.methods],
    }, impl)


def serialize_generic_perk_impl(impl: "ExtendWithDef") -> dict:
    """The manifest record for a GENERIC-target perk implementation: a template (#543).

    `extend Box@(T) with Show` names no instantiation. `type` is the target's BASE name
    and `type_args` the parameters as written, so a reader can list it without a parser.
    An array template (`extend T[] with P`, #699) has the array base and its element
    parameter.
    """
    from sushi_lang.semantics.passes.collect.perks import covering_base
    from sushi_lang.semantics.typesys import DynamicArrayType
    target = impl.target_type
    type_args = ([target.base_type] if isinstance(target, DynamicArrayType)
                 else target.type_args)
    return with_doc({
        "type": covering_base(target),
        "type_args": [str(a) for a in type_args],
        "target_bounds": target_bound_records(impl),
        "perk": impl.perk_name,
        # The signatures are written in the target's type parameters, as the
        # template's source spells them.
        "methods": [method_record(m) for m in impl.methods],
    }, impl)


def method_receiver_record(record: dict, method) -> dict:
    """`record` plus the receiver fields a method declares: `self_mode` and `static`.

    Each key is present only when the declaration writes it, so a record never claims
    a marker the author did not write.
    """
    self_mode = getattr(method, "self_mode", None)
    if self_mode is not None:
        record["self_mode"] = self_mode
    if getattr(method, "is_static", False):
        record["static"] = True
    return record


def serialize_extension(ext: "ExtendDef") -> dict:
    """The manifest record of one CONCRETE extension method: its target and signature.

    `public` is the marker that R4 to R6 of `docs/design/extension-visibility.md` read.
    """
    return with_doc(method_receiver_record({
        "type": type_string(ext.target_type),
        "name": ext.name,
        "public": ext.is_public,
        **signature_record(ext),
    }, ext), ext)


def serialize_generic_extension(ext: "ExtendDef") -> dict:
    """The manifest record of one extension TEMPLATE: the record, and its type parameters.

    A generic target (`Box@(T)`, `Box@(i32)`), an array target (`T[]`) and a method
    type parameter (`pick@(U)`) all name no single instance.
    """
    record = serialize_extension(ext)
    record["type_params"] = _type_param_records(ext)
    record["target_bounds"] = target_bound_records(ext)
    return record


def serialize_perk(perk: "PerkDef") -> dict:
    """Produce the manifest record for a single perk DEFINITION (the contract).

    The methods travel as records (#537): a contract is what a consumer can be asked to
    satisfy, and `--lib-info` prints each method's signature and block from here.
    """
    return with_doc({
        "name": perk.name,
        "methods": [method_record(m) for m in perk.methods],
    }, perk)
