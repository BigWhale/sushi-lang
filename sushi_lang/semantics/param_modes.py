"""Parameter modes: the declared convention for one value crossing a call boundary.

THE authority for "who frees this argument?". Before this module the compiler read the
convention off the callee's *implementation*, so six kinds of callee gave four different
answers and two of them disagreed with each other -- a false CE2405 at every stdlib call
site that passed an owning value. The mode is now a property of the DECLARATION, and both
Pass 3 and the backend read it from here.

The normative spec is `docs/design/borrow-model.md`.

Two invariants hold this together, and both are pinned by
`tests/unit/test_param_mode_invariant.py`:

1. **The mode is `PEEK` or `POKE` if and only if the parameter's type is a
   `ReferenceType` with that mutability.** There is one derivation (`mode_of_type`) and
   nothing else may compute a mode. Two spellings of one fact drift; one does not.
2. **`CalleeKind` is CLOSED.** A member with no row in `_UNMARKED_STILL_CONSUMES`'s
   coverage test is a red test, the same property that makes `ConsumingUse` work
   (`docs/design/ownership-conventions.md` S3.1).
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional, Sequence, Tuple

from sushi_lang.semantics.typesys import ReferenceType, Type


class ParamMode(Enum):
    """How ONE parameter takes its argument.

    `BORROW` is the unmarked mode: the caller keeps the value and frees it. It is
    written at neither end.
    """

    BORROW = "borrow"   # unmarked      -- caller frees; the argument stays usable
    NOM = "nom"         # `nom T x`     -- callee frees; a later use is CE2405
    PEEK = "peek"       # `peek T x`    -- by pointer, read only
    POKE = "poke"       # `poke T x`    -- by pointer, read/write

    @property
    def consumes(self) -> bool:
        """True when the CALLEE becomes the owner."""
        return self is ParamMode.NOM

    @property
    def by_pointer(self) -> bool:
        """True when the argument crosses as a pointer, not as a value."""
        return self in (ParamMode.PEEK, ParamMode.POKE)

    @property
    def marker(self) -> Optional[str]:
        """The word written at both ends, or None for the unmarked mode."""
        return None if self is ParamMode.BORROW else self.value


class CalleeKind(Enum):
    """Every kind of thing a call argument can be handed to. A CLOSED set."""

    FUNCTION = "function"        # a named function: user, library, or monomorphized generic
    METHOD = "method"            # an extension or perk method (the receiver included)
    STDLIB = "stdlib"            # a stdlib function from the registry
    FFI_EXTERN = "ffi_extern"    # a bodyless `unsafe external "C"` declaration
    INDIRECT = "indirect"        # a call through a function value (a closure or a fn reference)
    CONSTRUCTOR = "constructor"  # a struct or enum constructor -- a field takes ownership
    CONTAINER = "container"      # List.push / HashMap.insert / Own.alloc


# A struct field, an enum payload and a container slot take ownership by position: they
# are not declared parameters, so they have no mode to read. They consume, always.
_ALWAYS_CONSUMES: frozenset[CalleeKind] = frozenset({
    CalleeKind.CONSTRUCTOR,
    CalleeKind.CONTAINER,
})

# PHASE 3 SCAFFOLD -- delete in phase 4 (the flip).
#
# An unmarked by-value parameter of these kinds still CONSUMES, which is what the
# compiler did before the mode existed. Keeping it means phase 3 changes exactly one
# thing: a stdlib parameter is a borrow, so the phantom consume at every stdlib call
# site is gone. Phase 4 empties this set, and then the declared mode is the whole
# answer for every kind.
_UNMARKED_STILL_CONSUMES: frozenset[CalleeKind] = frozenset({
    CalleeKind.FUNCTION,
    CalleeKind.INDIRECT,
})


def mode_of_type(ty: Optional[Type], is_nom: bool = False) -> ParamMode:
    """THE derivation of a declared mode. Nothing else may compute one.

    `peek` / `poke` ride on the type as a `ReferenceType`, so they are read off it and
    can never disagree with it. `nom` is the one mode bit a type cannot carry, so it
    arrives as a flag.
    """
    if isinstance(ty, ReferenceType):
        return ParamMode.POKE if ty.is_poke() else ParamMode.PEEK
    return ParamMode.NOM if is_nom else ParamMode.BORROW


def param_mode(param) -> ParamMode:
    """The declared mode of one parameter, from either `Param` dataclass."""
    return mode_of_type(getattr(param, "ty", None),
                        bool(getattr(param, "is_nom", False)))


def declared_modes(params: Optional[Iterable]) -> Tuple[ParamMode, ...]:
    """The declared modes of a parameter list."""
    return tuple(param_mode(p) for p in (params or ()))


def normalize_modes(param_types: Sequence[Type],
                    param_modes: Optional[Sequence[ParamMode]]) -> Tuple[ParamMode, ...]:
    """The modes of a `FunctionType`, with invariant 1 enforced by construction.

    A function type built with no modes and one built with all-default modes are the
    same type, which is what keeps `FunctionType.__eq__` from splitting on how the type
    happened to be constructed.
    """
    raw = tuple(param_modes) if param_modes is not None else ()
    out = []
    for i, ty in enumerate(param_types):
        declared = raw[i] if i < len(raw) else None
        out.append(mode_of_type(ty, declared is ParamMode.NOM))
    return tuple(out)


def effective_modes(modes: Sequence[ParamMode], kind: CalleeKind) -> Tuple[ParamMode, ...]:
    """What the declared modes MEAN at a call to this kind of callee.

    The identity function once the phase-3 scaffold above is gone, except for the two
    kinds that consume by position and have nothing declared.
    """
    if kind in _ALWAYS_CONSUMES or kind in _UNMARKED_STILL_CONSUMES:
        # Only the UNMARKED mode is reinterpreted. A by-pointer mode is never turned
        # into a consume -- it does not even pass the value -- and an explicit `nom` is
        # already the answer.
        return tuple(ParamMode.NOM if m is ParamMode.BORROW else m for m in modes)
    return tuple(modes)


def modes_for(params: Optional[Iterable], kind: CalleeKind) -> Tuple[ParamMode, ...]:
    """The effective modes of a callee's parameter list. The common entry point."""
    return effective_modes(declared_modes(params), kind)


class CalleeModes:
    """THE resolver: "what are the modes of the callee named here?".

    A `Call` node is one shape for six different things, and only this class knows which
    is which. It is built from tables, not from AST, so both halves of the compiler can
    hold one and reach the same answer -- which is the property the six-conventions bug
    did not have.
    """

    def __init__(self, *, func_sigs=None, struct_names=None, stdlib_sigs=None):
        self._func_sigs = func_sigs or {}
        self._struct_names = frozenset(struct_names or ())
        self._stdlib_sigs = stdlib_sigs or {}

    def kind_of(self, name: str, local_type: Optional[Type] = None) -> CalleeKind:
        """Which kind of callee `name` denotes at a call site.

        `local_type` is the type of a LOCAL of that name, if one is in scope; a local
        function value shadows a same-named top-level function, so it is asked first.
        """
        from sushi_lang.semantics.typesys import FunctionType
        if isinstance(local_type, FunctionType):
            return CalleeKind.INDIRECT
        if name in self._struct_names:
            return CalleeKind.CONSTRUCTOR
        if name in self._func_sigs:
            return CalleeKind.FUNCTION
        if name in self._stdlib_sigs:
            return CalleeKind.STDLIB
        # A built-in (`open`) or a name this table does not carry. FUNCTION is the
        # conservative answer: it is what the compiler applied to every call before the
        # mode existed.
        return CalleeKind.FUNCTION

    def variadic_from(self, name: str) -> Optional[int]:
        """The index at which trailing arguments collect into a `...T` array, or None.

        A trailing variadic argument is not a call argument at all: it becomes an
        ELEMENT of the array the CALLER synthesizes, which the callee then owns. So it
        transfers whatever the parameter's own mode is. Both a user variadic and a
        stdlib one (`run`) answer here -- the caller-side collection is the same code.
        """
        sig = self._func_sigs.get(name)
        params = getattr(sig, "params", None) if sig is not None else None
        if params and getattr(params[-1], "is_variadic", False):
            return len(params) - 1
        std = self._stdlib_sigs.get(name)
        if std is not None and getattr(std, "is_variadic", False):
            params = getattr(std, "params", None) or ()
            return max(len(params) - 1, 0)
        return None

    def for_name(self, name: str, local_type: Optional[Type] = None
                 ) -> Tuple[CalleeKind, Tuple[ParamMode, ...]]:
        """The callee's kind and the effective mode of each declared parameter."""
        from sushi_lang.semantics.typesys import FunctionType
        kind = self.kind_of(name, local_type)
        if kind is CalleeKind.INDIRECT and isinstance(local_type, FunctionType):
            return kind, effective_modes(local_type.modes, kind)
        sig = self._func_sigs.get(name)
        return kind, modes_for(getattr(sig, "params", None) if sig else None, kind)

    def mode_at(self, modes: Sequence[ParamMode], index: int,
                kind: CalleeKind) -> ParamMode:
        """The mode of argument `index`, for a callee whose arity may not be known.

        An argument past the declared list belongs to a callee this resolver could not
        find. Answering with the kind's unmarked meaning keeps such a call behaving the
        way it did before the mode existed.
        """
        if index < len(modes):
            return modes[index]
        return effective_modes((ParamMode.BORROW,), kind)[0]
