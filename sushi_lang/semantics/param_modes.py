"""Parameter modes: the declared convention for one value crossing a call boundary."""
from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional, Sequence, Tuple

from sushi_lang.semantics.typesys import ReferenceType, Type


class ParamMode(Enum):
    """How ONE parameter takes its argument."""

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
    STATIC_METHOD = "static_method"  # `Vec.at(3, 4)`: a method with NO receiver (#542)
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

# EMPTY, and that is the whole flip. An unmarked by-value parameter is a BORROW for
# every kind of callee, so the declared mode is the entire answer and `effective_modes`
# differs from `declared_modes` only at the two positional sinks above.
_UNMARKED_STILL_CONSUMES: frozenset[CalleeKind] = frozenset()


def mode_of_type(ty: Optional[Type], is_nom: bool = False) -> ParamMode:
    """THE derivation of a declared mode. Nothing else may compute one."""
    if isinstance(ty, ReferenceType):
        return ParamMode.POKE if ty.is_poke() else ParamMode.PEEK
    return ParamMode.NOM if is_nom else ParamMode.BORROW


def receiver_mode(self_mode: Optional[str]) -> ParamMode:
    """THE reading of a declared receiver mode. Nothing else may interpret one.

    An unmarked receiver is a BORROW, exactly as an unmarked parameter is; `peek self`
    and `poke self` arrive by pointer; `nom self` CONSUMES, so the method owns what it
    was called on and the caller's binding is spent (HANDLES.md ruling R25).
    """
    if self_mode is None:
        return ParamMode.BORROW
    if self_mode == "nom":
        return ParamMode.NOM
    return ParamMode.POKE if self_mode == "poke" else ParamMode.PEEK


def param_mode(param) -> ParamMode:
    """The declared mode of one parameter, from either `Param` dataclass."""
    return mode_of_type(getattr(param, "ty", None),
                        bool(getattr(param, "is_nom", False)))


def declared_modes(params: Optional[Iterable]) -> Tuple[ParamMode, ...]:
    """The declared modes of a parameter list."""
    return tuple(param_mode(p) for p in (params or ()))


def normalize_modes(param_types: Sequence[Type],
                    param_modes: Optional[Sequence[ParamMode]]) -> Tuple[ParamMode, ...]:
    """The modes of a `FunctionType`, with invariant 1 enforced by construction."""
    raw = tuple(param_modes) if param_modes is not None else ()
    out = []
    for i, ty in enumerate(param_types):
        declared = raw[i] if i < len(raw) else None
        out.append(mode_of_type(ty, declared is ParamMode.NOM))
    return tuple(out)


def effective_modes(modes: Sequence[ParamMode], kind: CalleeKind) -> Tuple[ParamMode, ...]:
    """What the declared modes MEAN at a call to this kind of callee."""
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
    """THE resolver: "what are the modes of the callee named here?"."""

    def __init__(self, *, func_sigs=None, struct_names=None, stdlib_sigs=None):
        self._func_sigs = func_sigs or {}
        self._struct_names = frozenset(struct_names or ())
        self._stdlib_sigs = stdlib_sigs or {}

    def kind_of(self, name: str, local_type: Optional[Type] = None) -> CalleeKind:
        """Which kind of callee `name` denotes at a call site."""
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

    def signature_of(self, name: str):
        """The declared signature of `name`, from whichever table carries it."""
        return self._func_sigs.get(name) or self._stdlib_sigs.get(name)

    def variadic_from(self, name: str) -> Optional[int]:
        """The index at which trailing arguments collect into a `...T` array, or None."""
        sig = self._func_sigs.get(name)
        params = getattr(sig, "params", None) if sig is not None else None
        if params and getattr(params[-1], "is_variadic", False):
            return len(params) - 1
        std = self._stdlib_sigs.get(name)
        if std is not None and getattr(std, "is_variadic", False):
            params = getattr(std, "params", None) or ()
            return max(len(params) - 1, 0)
        return None

    def variadic_callee_owns(self, name: str) -> bool:
        """Does the CALLEE free the collected `...T` array, or does the caller keep it?"""
        return name not in self._stdlib_sigs

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
        """The mode of argument `index`, for a callee whose arity may not be known."""
        if index < len(modes):
            return modes[index]
        return effective_modes((ParamMode.BORROW,), kind)[0]
