"""Semantic validation for FFI `unsafe external` blocks."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional

from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType
from sushi_lang.semantics.externs_manifest import GENERATED_INLINE_SYMBOLS
from sushi_lang.semantics.ffi_boundary import (
    is_c_abi_param, is_c_abi_scalar, is_c_abi_type as _is_c_abi_type, is_c_abi_variable,
    nullable_payload,
)
from sushi_lang.semantics.generics.type_display import display_type

from .arguments import check_arguments

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Program, ExternalBlock, ExternalDecl
    from . import TypeValidator


def validate_external_signatures(reporter: Reporter, program: 'Program') -> None:
    """Validate ABI-subset (CE5003) and emit the four-guarantee warning (CW5001)."""
    externals = getattr(program, "externals", None)
    if not externals:
        return

    for block in externals:
        _validate_block_abi(reporter, block)
        _validate_block_signatures(reporter, block)
        _emit_block_warning(reporter, block)


def _defining_site(symbol: str, tables, registry, generated=frozenset()) -> Optional[tuple]:
    """Where this build defines `symbol`, as (note, span, filename). None if nowhere.

    Ordered by how much the answer can say. A library record carries no span, so its
    note is a plain fact; a declaration this program holds carries its own file and
    line, which makes the diagnostic relational.
    """
    if registry is not None:
        kept = registry.get_all_not_exported().get(symbol)
        if kept is not None:
            return (f"library '{kept[0]}' declares it and does not export it", None, None)

        shipped = registry.get_all_private_functions().get(symbol)
        if shipped is not None:
            return (f"library '{shipped[0]}' ships it in its export closure", None, None)

        for lib in registry.get_all_libraries().values():
            if symbol in lib.functions:
                return (f"library '{lib.name}' exports it", None, None)

    sig = tables.funcs.by_name.get(symbol)
    if sig is not None:
        if sig.name_span is not None:
            return ("defined here", sig.name_span, sig.filename)
        # A monomorphized instance carries no span of its own: it is a body the
        # compiler synthesized, not one the user wrote.
        return ("this program defines it", None, None)

    if symbol in tables.constants.by_name:
        return ("this program defines a constant of that name", None, None)

    # The generated half: a symbol the stdlib generators emit, or one the backend
    # emits inline. No semantic table holds either, so both arrive as names (#472).
    if symbol in GENERATED_INLINE_SYMBOLS:
        return ("the compiler generates a symbol of that name", None, None)

    if symbol in generated:
        return ("the standard library defines it", None, None)

    return None


def fold_link_names(reporter: Reporter, program: 'Program', tables, unit_name) -> None:
    """Fold each link name written as a string constant, in the declaring unit (#1089).

    It runs where every unit's constants and aliases exist and before CE5013 reads the
    names. The folded name is written to the declaration and to its collected signature,
    which is what the backend declares.
    """
    from sushi_lang.semantics.ast import MemberAccess
    from sushi_lang.semantics.const_eval import ConstantEvaluator, is_string_constant
    from sushi_lang.semantics.passes.collect.externals import reject_reserved_clash

    evaluator = ConstantEvaluator(reporter, tables.constants, unit_name,
                                  tables.namespaces.get, tables.structs,
                                  tables.enums).silent()
    for block in getattr(program, "externals", None) or ():
        for decl in [*block.decls, *block.variables]:
            link = decl.link_expr
            if link is None:
                continue
            value = evaluator.evaluate(link, BuiltinType.STRING, link.loc)
            if value is None:
                written = (f"{link.receiver.id}.{link.member}"
                           if isinstance(link, MemberAccess) else link.id)
                er.emit(reporter, er.ERR.CE1001, link.loc, name=written)
                continue
            if not is_string_constant(value):
                er.emit(reporter, er.ERR.CE5015, link.loc, name=decl.name,
                        type=display_type(value.semantic_type))
                continue
            decl.link_name = str(value.value)
            var = tables.externals.lookup_variable(block.namespace, decl.name)
            if var is not None:
                var.link_name = decl.link_name
            sig = tables.externals.lookup(block.namespace, decl.name)
            if sig is not None:
                sig.link_name = decl.link_name
                reject_reserved_clash(reporter, decl, sig)


def reject_external_naming_a_defined_symbol(
    reporter: Reporter, program: 'Program', tables, registry=None,
    generated_symbols=frozenset(),
) -> None:
    """CE5013: an `unsafe external` may name a FOREIGN symbol, never one this build defines.

    The declaration and the definition share one module and unify, so this is not a name
    clash the linker would catch -- the call simply enters the program's own body with no
    ABI check (#470). The link-name is what collides, not the Sushi name beside it.

    Needs the whole program's symbols, the linked libraries included, so it runs after
    the `libraries` step and not with the per-unit extern validation above.

    `generated_symbols` is what the stdlib generators define, read from the manifest
    the stdlib build writes. It is reserved whether this program links the unit or
    not: otherwise adding a `use` line breaks a build that compiled a minute ago.
    """
    externals = getattr(program, "externals", None)
    if not externals:
        return

    for block in externals:
        for decl in [*block.decls, *block.variables]:
            found = _defining_site(decl.link_name, tables, registry, generated_symbols)
            if found is None:
                continue
            note, span, filename = found
            diagnostic = er.emit_with(reporter, er.ERR.CE5013,
                                      decl.name_span or decl.loc,
                                      symbol=decl.link_name)
            if span is None:
                diagnostic.note(note)
            else:
                diagnostic.note_at(note, span, filename)
            diagnostic.emit()


def validate_ptr_unit_gate(reporter: Reporter, program: 'Program') -> None:
    """CE5009: `ptr` may only be NAMED in a unit that declares an `unsafe external` block.

    Both walks, because both are naming positions: a signature hands the type across a
    boundary, a body merely spells it, and neither is writable without a danger zone.
    """
    if getattr(program, "externals", None):
        return  # The unit declares a danger zone; ptr is legal here.

    from sushi_lang.semantics.ast_walk import body_types, signature_types
    from sushi_lang.semantics.type_predicates import contains_foreign_ptr

    sites = [(site.ty, site.span) for site in signature_types(program)]
    sites.extend(body_types(program))

    for ty, span in sites:
        if ty is not None and contains_foreign_ptr(ty):
            er.emit(reporter, er.ERR.CE5009, span)


def validate_external_call_args(validator: 'TypeValidator', node) -> None:
    """Validate argument count and types for a resolved foreign call."""
    sig = validator.external_table.lookup(node.external_ref[0], node.external_ref[1])
    if sig is None:
        for arg in node.args:
            validator.validate_expression(arg)
        return
    expected = sig.param_types
    is_variadic = getattr(sig, "is_variadic", False)
    fq_name = f"{node.external_ref[0]}.{node.external_ref[1]}"
    # The check walks each fixed argument against its declared type, so a
    # `Maybe.None` takes the parameter's `Maybe@(ptr)` (#1085).
    if not check_arguments(validator, fq_name, expected, node.args, node.loc,
                           mismatch_code=er.ERR.CE2006, arity_code=er.ERR.CE2009,
                           minimum_arity=is_variadic, stop_on_arity=True):
        for arg in node.args:
            validator.validate_expression(arg)
        return
    # Trailing variadic args: each must be C-ABI representable (CE5005).
    # Record the inferred types so the backend can apply C promotion.
    if is_variadic:
        variadic_types = []
        for arg in node.args[len(expected):]:
            got_ty = validator.validate_expression(arg)
            variadic_types.append(got_ty)
            if got_ty is not None and not is_c_abi_scalar(got_ty):
                er.emit(validator.reporter, er.ERR.CE5005, arg.loc,
                        type=display_type(got_ty), name=fq_name)
        node.variadic_arg_types = variadic_types


def _validate_block_abi(reporter: Reporter, block: 'ExternalBlock') -> None:
    """Reject any ABI string other than "C" (reuses CE5003)."""
    if block.abi != "C":
        er.emit(reporter, er.ERR.CE5003, block.abi_span or block.loc,
                type=f'ABI "{block.abi}"')


def _validate_block_signatures(reporter: Reporter, block: 'ExternalBlock') -> None:
    """Validate every param and return type against the C-ABI allowlist."""
    for decl in block.decls:
        if getattr(decl, "is_variadic", False) and len(decl.params) == 0:
            er.emit(reporter, er.ERR.CE5004, decl.name_span or decl.loc,
                    name=decl.name)
        for param in decl.params:
            # FFI is outside the mode system: the C callee never receives a Sushi value,
            # so there is nothing for it to take ownership of (borrow-model.md S5).
            if getattr(param, "is_nom", False):
                er.emit(reporter, er.ERR.CE2428,
                        getattr(param, "nom_span", None) or param.name_span or decl.loc,
                        name=param.name)
            if param.ty is not None and not is_c_abi_param(param.ty):
                er.emit(reporter, er.ERR.CE5003, param.type_span or decl.loc,
                        type=display_type(param.ty))
        if decl.ret is not None and not _is_c_abi_type(decl.ret):
            er.emit(reporter, er.ERR.CE5003, decl.ret_span or decl.loc,
                    type=display_type(decl.ret))
    for var in block.variables:
        if var.ty is not None and not is_c_abi_variable(var.ty):
            er.emit(reporter, er.ERR.CE5003, var.type_span or var.loc,
                    type=display_type(var.ty))


def _signature_notes(decl: 'ExternalDecl') -> List[str]:
    """Build signature-driven notes for a single declaration."""
    notes: List[str] = []
    ret = decl.ret
    if isinstance(nullable_payload(ret) or ret, ForeignPtrType):
        notes.append(
            f"'{decl.name}' returns `ptr`: unmanaged (RAII will not free this; "
            f"call the matching C free)"
        )
    elif isinstance(ret, BuiltinType) and ret != BuiltinType.BLANK:
        notes.append(
            f"'{decl.name}' returns raw `{ret}`, not `Result@({ret}, StdError)` - "
            f"check the C error convention by hand"
        )

    has_string = any(
        (nullable_payload(ty) or ty) == BuiltinType.STRING
        for ty in [p.ty for p in decl.params] + [ret]
    )
    if has_string:
        notes.append(
            f"'{decl.name}' uses `string`: UTF-8 Sushi string <-> C null-terminated; "
            f"marshalling required (freed at scope exit)"
        )

    if any(isinstance(p.ty, ForeignPtrType) for p in decl.params):
        notes.append(f"'{decl.name}' takes a `ptr`: aliasing is not tracked through this pointer")

    return notes


def _emit_block_warning(reporter: Reporter, block: 'ExternalBlock') -> None:
    """Emit CW5001 for a block without a `because` reason."""
    if block.reason is not None:
        return  # Silenced by an explicit acknowledgment.

    builder = er.emit_with(reporter, er.ERR.CW5001, block.loc)
    builder.note("guarantee 1/4 suspended: borrow checking (peek/poke) - aliasing not tracked")
    builder.note("guarantee 2/4 suspended: RAII / move semantics - foreign `ptr` is unmanaged")
    builder.note("guarantee 3/4 suspended: Result / Maybe - externals return raw C values")
    builder.note("guarantee 4/4 suspended: bounds safety - the memory behind a `ptr` is not checked")
    for decl in block.decls:
        for note in _signature_notes(decl):
            builder.note(note)
    for var in block.variables:
        builder.note(f"'{var.name}' is a C global: loaded at each read, read-only here")
    builder.help("see docs/ffi.md - acknowledge with `because \"<reason>\"` and use a safe wrapper")
    builder.emit()
