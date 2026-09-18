"""THE gate: what may a borrow of this name do? (#685, #713)

A `const` is READ-ONLY storage. It is one object in `.rodata`, so a pointer into it
exists and a `peek` reads through it, while a `poke` writes and a write there is
undefined behaviour and not a diagnostic. A unit `var` is storage in the data segment
and takes both modes, which is the line `docs/design/unit-storage.md` draws. A name that
reaches storage of NO kind -- a top-level function, a registry stdlib constant, an FFI
namespace -- refuses both. CE2400 is the one answer for all of them.

A TAKE is not this gate's question. `nom` consumes, so it is the borrow pass's rule and
it answers CE2436 for a constant and a unit variable alike (#726). This module held a
`nom` arm that nothing ever reached, because a consuming use never asks here.

The DECLARATION decides, never the position. Until #713 a `peek` was refused in a `let`
binding and in an argument and allowed in a match arm, while a bare pattern binding --
which the ownership model also calls a borrow -- was allowed everywhere. The reason line
said a constant has no FRAME slot, which is true and is the wrong test: nothing here
needs a frame slot, and the match arm already took the address.

The rule was written five times with four predicates, and one of them read the FLAT
constant table. `ConstantTable.by_name` holds one record per NAME over the whole
program and is first-wins, so a unit whose own `var` shared a name with an earlier
unit's `const` was refused, and a unit whose own `const` shared a name with an earlier
unit's `var` was let through to write read-only memory. Every caller passes what the
SCOPED lookup answered -- `TypeValidator.const_sig`, or
`ConstantTable.lookup(name, unit, scope)` -- because the question is per unit.

The module sits beside the passes rather than inside one: the scope pass and the
typecheck pass both ask, and neither owns the other, which is the shape
`semantics/name_ladder.py` and `semantics/param_modes.py` already have.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span

if TYPE_CHECKING:
    from sushi_lang.semantics.error_reporter import PassErrorReporter
    from sushi_lang.semantics.passes.collect import ConstSig

# The mode that only READS through the pointer. The other one writes through it.
READ_ONLY_MODE = "peek"

_READ_ONLY_HELP = (
    "a constant is read-only storage: `peek` reads it, and `poke` writes it; "
    "declare a `var` for storage that you can write")
_NO_STORAGE_HELP = (
    "only storage can be borrowed, and this name has none; read the value, or copy "
    "it into a local first")


def has_an_address(sig: Optional['ConstSig']) -> bool:
    """Does the record this name reaches denote storage a borrow can point at?

    Both kinds do: a `const` is one object in `.rodata` and a `var` is one object in the
    data segment. What separates them is the WRITE, and `may_be_written` answers that.
    """
    return sig is not None


def may_be_written(sig: Optional['ConstSig']) -> bool:
    """May a borrow of this record write through the pointer? A `var` alone."""
    return sig is not None and bool(sig.is_var)


def reject_borrow_of_constant(err: 'PassErrorReporter', name: str,
                              sig: Optional['ConstSig'], span: Optional[Span],
                              *, mode: str, no_frame_slot: bool = False) -> bool:
    """Report CE2400 when a `mode` borrow of `name` is refused. True when it was.

    `sig` is what the SCOPED constant lookup answered for `name`. `mode` is the word the
    position writes -- `peek` or `poke` -- because the record permits a read and refuses
    a write. `no_frame_slot` is for a caller that has already read the name ladder and
    knows the name reaches something with no storage even though no constant record
    holds it -- a top-level function, a registry stdlib constant, an FFI
    namespace. Only the scope pass can tell those apart, so only the scope pass passes
    it.
    """
    if has_an_address(sig):
        if mode == READ_ONLY_MODE or may_be_written(sig):
            return False
        help_line = _READ_ONLY_HELP
    elif no_frame_slot:
        help_line = _NO_STORAGE_HELP
    else:
        return False
    err.emit_with(er.ERR.CE2400, span, name=name, mode=mode).help(help_line).emit()
    return True
