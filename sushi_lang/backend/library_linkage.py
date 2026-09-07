"""What a compiled library's bitcode may define STRONGLY (#594).

A binary or hybrid `.slib` is self-contained: every stdlib module it imports is
compiled into its own bitcode, so a consumer that names nothing of that module still
links. The same copy is a SECOND definition for a consumer that DOES import the module,
and `ld` refuses a program built over two strong ones -- 39 duplicate symbols for a
library over `<collections/strings>` alone, 90 for one over `<io/fs>`. The monolithic
consumer path never saw it, because `TwoPhaseLinker` resolves a duplicate by symbol
source; the per-unit incremental path hands `ld` two objects and `ld` has no such rule.

The answer is the one this backend already gives every definition a consumer may also
hold: `weak_odr` (`_set_weak_odr_on_perk_impls`, and the monomorphized extensions of
 #404). The linker keeps one copy, a consumer's own strong definition wins where there
is one, and the definition survives optimization while nothing in the library
references it -- which `linkonce_odr` would not guarantee.

Two producers of such a definition, and ONE rule for both: a bundled unit's body,
emitted beside the library's own; and every symbol a stdlib `.bc` brings in. Neither
re-derives a symbol NAME -- one asks the module what appeared while a foreign unit was
emitted, the other asks the `.bc` what it defines -- so the naming rules stay in the
one place that owns them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from llvmlite import ir

if TYPE_CHECKING:
    import llvmlite.binding as llvm

#: The linkage a definition the consumer may also hold takes.
WEAK = "weak_odr"

#: A strong definition's linkage before it is weakened. `ir` spells the default as the
#: empty string; a private or internal symbol is invisible to `ld` and is left alone,
#: and anything already weak needs nothing.
_STRONG = ("", "external")


def _is_definition(value) -> bool:
    """True for a global that CARRIES a definition rather than a declaration.

    `weak_odr` on a declaration is not valid IR: the linkage says "one of these
    identical bodies wins", and a declaration has no body to offer.
    """
    if isinstance(value, ir.Function):
        return bool(value.blocks)
    if isinstance(value, ir.GlobalVariable):
        return value.initializer is not None
    return False


def weaken(value) -> None:
    """Weaken one definition the emitter just produced. A no-op for anything else.

    The guard is what makes the call safe to make unconditionally: a private helper is
    `internal` and invisible to `ld`, and a declaration has no body to offer.
    """
    if value is not None and value.linkage in _STRONG and _is_definition(value):
        value.linkage = WEAK


def weaken_all(values: Iterable) -> None:
    """Weaken every definition in `values`. The caller decides once, per unit."""
    for value in values:
        weaken(value)


def weaken_foreign_globals(module: ir.Module, already_present: set[str]) -> None:
    """Weaken every global `module` gained since `already_present` was taken.

    The storage half: a `var` global is created while the declaration is emitted, so a
    snapshot around that loop names exactly one unit's, whatever the emitter chose to
    call it. A function cannot be found this way -- the declaration round puts every
    one of them in the module before any body is emitted -- so `weaken` takes those
    from the emitter's own return value.
    """
    for name, value in module.globals.items():
        if name not in already_present:
            weaken(value)


def weaken_linked_symbols(llmod: 'llvm.ModuleRef', symbols: Iterable[str]) -> None:
    """Weaken the named symbols in a LINKED module -- the stdlib `.bc` half.

    Binding level, because the `.bc` files are linked into the parsed module and never
    reach the `ir` layer. A name that is not in the module, or arrived as a
    declaration, is skipped: the set says what the `.bc` offered, not what survived.
    """
    wanted = set(symbols)
    if not wanted:
        return
    for value in list(llmod.functions) + list(llmod.global_variables):
        if value.name not in wanted or value.is_declaration:
            continue
        if value.linkage.name in ("external",):
            value.linkage = WEAK


def defined_symbol_names(module: 'llvm.ModuleRef') -> set[str]:
    """The externally visible symbols `module` DEFINES -- what a `.bc` brings in."""
    return {
        value.name
        for value in list(module.functions) + list(module.global_variables)
        if not value.is_declaration and value.linkage.name == "external"
    }
