"""The binding table of one body, which the `scope` pass writes (docs/design/ir.md 7.1, S3).

For each name occurrence, the declaration or the local that it denotes. For each binder,
its `LocalId`. For each lambda, the names that it captures. The `scope` pass is the one
writer (gate: `tests/unit/test_bindings_have_one_writer.py`).

The key is the NODE, never its span: a hidden binder has no span, and two nodes that the
AST builder makes can share the span of their cause. An AST node compares by value and
has no hash, so a table keys on `id(node)` and keeps the node beside the answer. A read
gives the answer only for the node that it was recorded for.

A declaration is named by its written name until the declaration table exists (ir.md
6.3); the `DeclId` of each kind replaces the name then.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TypeAlias

from sushi_lang.semantics.shir.ids import LocalId


@dataclass(frozen=True, slots=True)
class Local:
    """A binder of this body: a parameter, a `let`, a pattern binding, a loop item."""
    local: LocalId


@dataclass(frozen=True, slots=True)
class Const:
    """A `const`: of this unit, of an imported unit, or of a stdlib module."""
    name: str


@dataclass(frozen=True, slots=True)
class Storage:
    """A unit-level `var` (docs/design/unit-storage.md)."""
    name: str


@dataclass(frozen=True, slots=True)
class Function:
    """A function: a function value, or the callee of a call."""
    name: str


@dataclass(frozen=True, slots=True)
class Namespace:
    """A `use ... as` alias, or the namespace of an `unsafe external` block."""
    name: str


@dataclass(frozen=True, slots=True)
class TypeName:
    """A type: the receiver of a variant or of a static, or a type written as a value."""
    name: str


@dataclass(frozen=True, slots=True)
class Unresolved:
    """No declaration has this name here, and a diagnostic tells it.

    The scope pass gives CE1001 or CE1002. A type pack name is the one exception that it
    lets through: the typecheck pass gives CE0144 at its use.
    """
    name: str


Resolution: TypeAlias = Local | Const | Storage | Function | Namespace | TypeName | Unresolved


@dataclass(frozen=True, slots=True)
class Capture:
    """A name that a lambda reads from an enclosing scope, and the local that it reads."""
    name: str
    local: LocalId


@dataclass(slots=True)
class Bindings:
    """The table of one body: `body` is the declaration that owns the body."""
    body: object
    _resolutions: dict[int, tuple[object, Resolution]] = field(
        default_factory=dict, init=False, repr=False)
    _binder_locals: dict[tuple[int, str], tuple[object, LocalId]] = field(
        default_factory=dict, init=False, repr=False)
    _lambda_captures: dict[int, tuple[object, tuple[Capture, ...]]] = field(
        default_factory=dict, init=False, repr=False)

    # -- the read API ---------------------------------------------------------------

    def resolution_of(self, node: object) -> Optional[Resolution]:
        """What the name occurrence `node` denotes, or None when nothing is recorded."""
        entry = self._resolutions.get(id(node))
        return entry[1] if entry is not None and entry[0] is node else None

    def local_of(self, owner: object, name: str) -> Optional[LocalId]:
        """The local of the binder `name` that `owner` declares, or None."""
        entry = self._binder_locals.get((id(owner), name))
        return entry[1] if entry is not None and entry[0] is owner else None

    def captures_of(self, lam: object) -> Optional[tuple[Capture, ...]]:
        """The captures of the lambda `lam`, in the order of their first use, or None."""
        entry = self._lambda_captures.get(id(lam))
        return entry[1] if entry is not None and entry[0] is lam else None

    # -- the write API: the scope pass alone calls it --------------------------------

    def record_resolution(self, node: object, answer: Resolution) -> None:
        self._resolutions[id(node)] = (node, answer)

    def record_binder(self, owner: object, name: str, local: LocalId) -> None:
        self._binder_locals[(id(owner), name)] = (owner, local)

    def record_captures(self, lam: object, captures: tuple[Capture, ...]) -> None:
        self._lambda_captures[id(lam)] = (lam, captures)
