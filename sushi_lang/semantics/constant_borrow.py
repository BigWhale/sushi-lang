"""THE gate: may a borrow take the address of what this name denotes? (#685)

A `const` is a folded value with no address, so nothing can point at it -- a `peek` or
`poke` argument, a `let peek` / `let poke` binding, a `poke self` call, a `poke` pattern
binding and a `poke` foreach item all ask the same question, and CE2400 is the one
answer. A unit `var` is storage in the data segment and HAS an address, which is the
line `docs/design/unit-storage.md` draws, so it is borrowable like a local.

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


def has_an_address(sig: Optional['ConstSig']) -> bool:
    """Does the record this name reaches denote storage a borrow can point at?

    One predicate, and it is `is_var` alone. A `const` is folded into every reader, so
    there is nothing to point at; a `var` is one object in the data segment.
    """
    return sig is not None and bool(sig.is_var)


def reject_borrow_of_constant(err: 'PassErrorReporter', name: str,
                              sig: Optional['ConstSig'], span: Optional[Span],
                              *, no_frame_slot: bool = False) -> bool:
    """Report CE2400 when `name` has no address here. True when it was reported.

    `sig` is what the SCOPED constant lookup answered for `name`. `no_frame_slot` is for
    a caller that has already read the name ladder and knows the name reaches something
    with no frame slot even though no constant record holds it -- a top-level function,
    a registry stdlib constant, an FFI namespace. Only the scope pass can tell those
    apart, so only the scope pass passes it.
    """
    if has_an_address(sig):
        return False
    if sig is None and not no_frame_slot:
        return False
    err.emit(er.ERR.CE2400, span, name=name)
    return True
