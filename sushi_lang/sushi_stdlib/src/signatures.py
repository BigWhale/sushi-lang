"""What a registry stdlib function takes and answers, written ONCE per layer (#550).

A registry module used to spell its own function names in four other Python places: the
registry's parameter specs, the semantic return type, the arity check, the Result the
instantiate pass interns, and the backend's emission cases. A name missing from one of
them answered CE2008 for a function the compiler can emit.

A `Signature` is that row. Every reader takes what it needs from it and nothing spells a
name twice; `tests/unit/test_stdlib_signature_tables.py` is the gate that says so.

A parameter carries its Sushi type AND how it crosses the boundary, because one type
crosses two ways: `fd_open` takes a PATH, which is marshalled to a NUL-terminated `i8*`
through `emit_cstr_arg`, and `fd_write_str` takes a string VALUE, which crosses as its
own fat pointer. The type alone cannot tell those apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Union

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import BuiltinType, Type, UnknownType

SushiType = Union[Type, GenericTypeRef]


@dataclass(frozen=True)
class Param:
    """One parameter: its Sushi type, and whether it crosses as a C string."""
    ty: SushiType
    as_cstr: bool = False


def cstr() -> Param:
    """A `string` argument the call site marshals to `i8*` -- a path, a host name."""
    return Param(BuiltinType.STRING, as_cstr=True)


@dataclass(frozen=True)
class Signature:
    """One function's row: what it takes, what it answers, and in which channel.

    `ok` is the payload of the `Result` the function answers, and `error` names the
    enum in the other arm. A function that answers its value BARE -- `fd_isatty`, which
    cannot fail in a way a caller can act on -- sets `ok` to None and puts the type in
    `bare` instead.
    """
    params: Tuple[Param, ...] = ()
    ok: Optional[SushiType] = None
    error: Optional[str] = None
    bare: Optional[SushiType] = None

    @property
    def arity(self) -> int:
        return len(self.params)

    def return_type(self) -> SushiType:
        """The declared return type: a `Result@(ok, error)`, or the bare type."""
        if self.ok is None:
            if self.bare is None:
                raise ValueError("a signature answers either a Result or a bare type")
            return self.bare
        return GenericTypeRef("Result", (self.ok, UnknownType(self.error or "StdError")))


def params_of(*types: Union[SushiType, Param]) -> Tuple[Param, ...]:
    """A parameter list from bare types, leaving an explicit `Param` as written."""
    return tuple(t if isinstance(t, Param) else Param(t) for t in types)


def param_specs(module: str, table: Dict[str, Signature]) -> Dict[Tuple[str, str], list]:
    """The registry's `(module, name) -> [Sushi type]` view of a whole layer."""
    return {(module, name): [param.ty for param in sig.params]
            for name, sig in table.items()}


def validate_arity(name: str, table: Dict[str, Signature], args: list,
                   reporter, loc) -> None:
    """CE2009 against the row's own length, for every function of a layer."""
    from sushi_lang.internals import errors as er

    sig = table.get(name)
    if sig is None or len(args) == sig.arity:
        return
    er.emit(reporter, er.ERR.CE2009, loc, name=name, expected=sig.arity, got=len(args))
