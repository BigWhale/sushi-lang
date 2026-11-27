"""What a registry stdlib function takes and answers, written ONCE per layer (#550).

A registry module used to spell its own function names in four other Python places: the
registry's parameter specs, the semantic return type, the arity check, the Result the
instantiate pass interns, and the backend's emission cases. A name missing from one of
them answered CE2008 for a function the compiler can emit.

A `Signature` is that row. Every reader takes what it needs from it and nothing spells a
name twice; `tests/unit/test_stdlib_signature_tables.py` is the gate that says so.

A parameter carries its Sushi type AND how it crosses the boundary, because one type
crosses two ways: `sock_dns_resolve` takes a HOST, which is marshalled to a
NUL-terminated `i8*` through `emit_cstr_arg`, and `fd_write_str` takes a string VALUE,
which crosses as its own fat pointer. The type alone cannot tell those apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, Union

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
    enum in the other arm.
    """
    params: Tuple[Param, ...]
    ok: SushiType
    error: str

    @property
    def arity(self) -> int:
        return len(self.params)

    def return_type(self) -> SushiType:
        """The declared return type: `Result@(ok, error)`."""
        return GenericTypeRef("Result", (self.ok, UnknownType(self.error)))


def params_of(*types: Union[SushiType, Param]) -> Tuple[Param, ...]:
    """A parameter list from bare types, leaving an explicit `Param` as written."""
    return tuple(t if isinstance(t, Param) else Param(t) for t in types)


def param_specs(module: str, table: Dict[str, Signature]) -> Dict[Tuple[str, str], list]:
    """The registry's `(module, name) -> [Sushi type]` view of a whole layer."""
    return {(module, name): [param.ty for param in sig.params]
            for name, sig in table.items()}
