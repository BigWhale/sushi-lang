"""Semantic validation for FFI `unsafe external` blocks."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional

from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType
from sushi_lang.semantics.externs_manifest import GENERATED_INLINE_SYMBOLS
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Program, ExternalBlock, ExternalDecl


_C_ABI_BUILTINS = {
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
    BuiltinType.F32, BuiltinType.F64,
    BuiltinType.BOOL, BuiltinType.STRING, BuiltinType.BLANK,
}


def _is_c_abi_type(ty) -> bool:
    """Strict allowlist: only C-representable types pass."""
    if isinstance(ty, ForeignPtrType):
        return True
    if isinstance(ty, BuiltinType) and ty in _C_ABI_BUILTINS:
        return True
    return False


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
        for decl in block.decls:
            found = _defining_site(decl.link_name, tables, registry, generated_symbols)
            if found is None:
                continue
            note, span, filename = found
            er.emit_with(reporter, er.ERR.CE5013,
                         decl.name_span or decl.loc,
                         symbol=decl.link_name) \
                .note(note, span, filename) \
                .emit()


def validate_ptr_unit_gate(reporter: Reporter, program: 'Program') -> None:
    """CE5009: `ptr` may only be NAMED in a unit that declares an `unsafe external` block."""
    if getattr(program, "externals", None):
        return  # The unit declares a danger zone; ptr is legal here.

    from sushi_lang.semantics.type_predicates import contains_foreign_ptr

    def check(ty, span) -> None:
        if ty is not None and contains_foreign_ptr(ty):
            er.emit(reporter, er.ERR.CE5009, span)

    def walk_block(block) -> None:
        import dataclasses
        from sushi_lang.semantics.ast import Node, Block
        if block is None:
            return
        for stmt in getattr(block, "stmts", ()):
            check(getattr(stmt, "ty", None),
                  getattr(stmt, "type_span", None) or getattr(stmt, "loc", None))
            check(getattr(stmt, "item_type", None),
                  getattr(stmt, "item_type_span", None) or getattr(stmt, "loc", None))
            if dataclasses.is_dataclass(stmt):
                for f in dataclasses.fields(stmt):
                    value = getattr(stmt, f.name, None)
                    if isinstance(value, Block):
                        walk_block(value)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, Block):
                                walk_block(item)
                            elif isinstance(item, Node) and isinstance(getattr(item, "body", None), Block):
                                walk_block(item.body)

    def walk_signature(fn) -> None:
        span = getattr(fn, "name_span", None) or getattr(fn, "loc", None)
        check(getattr(fn, "ret", None), getattr(fn, "ret_span", None) or span)
        check(getattr(fn, "err_type", None), span)
        for p in getattr(fn, "params", ()):
            check(getattr(p, "ty", None), span)
        walk_block(getattr(fn, "body", None))

    for func in program.functions:
        walk_signature(func)
    for ext in program.extensions + program.generic_extensions:
        check(getattr(ext, "target_type", None),
              getattr(ext, "target_type_span", None) or ext.loc)
        walk_signature(ext)
    for impl in program.perk_impls:
        check(getattr(impl, "target_type", None),
              getattr(impl, "target_type_span", None) or impl.loc)
        for method in impl.methods:
            walk_signature(method)
    for struct in program.structs:
        for field in struct.fields:
            if contains_foreign_ptr(getattr(field, "ty", None)):
                er.emit(reporter, er.ERR.CE5009,
                        getattr(struct, "name_span", None) or struct.loc)
    for enum in program.enums:
        for variant in enum.variants:
            for ty in getattr(variant, "associated_types", ()) or ():
                check(ty, getattr(enum, "name_span", None) or enum.loc)
    for const in program.constants:
        check(getattr(const, "ty", None),
              getattr(const, "type_span", None) or const.loc)


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
            if param.ty is not None and not _is_c_abi_type(param.ty):
                er.emit(reporter, er.ERR.CE5003, param.type_span or decl.loc,
                        type=display_type(param.ty))
        if decl.ret is not None and not _is_c_abi_type(decl.ret):
            er.emit(reporter, er.ERR.CE5003, decl.ret_span or decl.loc,
                    type=display_type(decl.ret))


def _signature_notes(decl: 'ExternalDecl') -> List[str]:
    """Build signature-driven notes for a single declaration."""
    notes: List[str] = []
    ret = decl.ret
    if isinstance(ret, ForeignPtrType):
        notes.append(
            f"'{decl.name}' returns `ptr`: unmanaged (RAII will not free this; "
            f"call the matching C free) and may be null"
        )
    elif isinstance(ret, BuiltinType) and ret != BuiltinType.BLANK:
        notes.append(
            f"'{decl.name}' returns raw `{ret}`, not `Result<{ret}>` - "
            f"check the C error convention by hand"
        )

    has_string = any(
        isinstance(p.ty, BuiltinType) and p.ty == BuiltinType.STRING for p in decl.params
    ) or (isinstance(ret, BuiltinType) and ret == BuiltinType.STRING)
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
    builder.note("guarantee 4/4 suspended: bounds / null safety - a returned `ptr` may be null")
    for decl in block.decls:
        for note in _signature_notes(decl):
            builder.note(note)
    builder.help("see docs/ffi.md - acknowledge with `because \"<reason>\"` and use a safe wrapper")
    builder.emit()
