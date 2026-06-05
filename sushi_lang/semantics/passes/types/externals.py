"""Semantic validation for FFI `unsafe external` blocks.

Performs two things over `program.externals`:

1. ABI-subset enforcement (CE5003): every parameter and return type in a foreign
   declaration must be C-representable. Strict allowlist - everything outside it,
   including named user types (UnknownType), is rejected. A non-"C" ABI string is
   also reported via CE5003 (keeps the four-code budget).
2. The four-guarantee block warning (CW5001), emitted once per block that has no
   `because "<reason>"` clause, carrying signature-driven per-declaration notes.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, List

from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Program, ExternalBlock, ExternalDecl


# C-ABI representable builtin types (allowlist).
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


def _validate_block_abi(reporter: Reporter, block: 'ExternalBlock') -> None:
    """Reject any ABI string other than "C" (reuses CE5003)."""
    if block.abi != "C":
        er.emit(reporter, er.ERR.CE5003, block.abi_span or block.loc,
                type=f'ABI "{block.abi}"')


def _validate_block_signatures(reporter: Reporter, block: 'ExternalBlock') -> None:
    """Validate every param and return type against the C-ABI allowlist."""
    for decl in block.decls:
        # A variadic external needs at least one fixed parameter: the C ABI's
        # va_start requires a named argument to anchor the variadic list.
        if getattr(decl, "is_variadic", False) and len(decl.params) == 0:
            er.emit(reporter, er.ERR.CE5004, decl.name_span or decl.loc,
                    name=decl.name)
        for param in decl.params:
            if param.ty is not None and not _is_c_abi_type(param.ty):
                er.emit(reporter, er.ERR.CE5003, param.type_span or decl.loc,
                        type=str(param.ty))
        if decl.ret is not None and not _is_c_abi_type(decl.ret):
            er.emit(reporter, er.ERR.CE5003, decl.ret_span or decl.loc,
                    type=str(decl.ret))


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
    # Four suspended guarantees.
    builder.note("guarantee 1/4 suspended: borrow checking (&peek/&poke) - aliasing not tracked")
    builder.note("guarantee 2/4 suspended: RAII / move semantics - foreign `ptr` is unmanaged")
    builder.note("guarantee 3/4 suspended: Result / Maybe - externals return raw C values")
    builder.note("guarantee 4/4 suspended: bounds / null safety - a returned `ptr` may be null")
    # Signature-driven per-declaration notes.
    for decl in block.decls:
        for note in _signature_notes(decl):
            builder.note(note)
    builder.help("see docs/ffi.md - acknowledge with `because \"<reason>\"` and use a safe wrapper")
    builder.emit()
