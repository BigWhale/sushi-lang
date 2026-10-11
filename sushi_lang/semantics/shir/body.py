"""The SHIR unit: one body, its parameters and its locals (docs/design/ir.md 6.3, 7.2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TypeAlias

from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.types import TemplateId
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.ids import DeclId, LocalId
from sushi_lang.semantics.shir.nodes import ShirBlock, TypeRef


@dataclass(frozen=True, slots=True)
class Instance:
    """The body of a function or a method, concrete or a monomorphized copy."""


@dataclass(frozen=True, slots=True)
class TemplateCheck:
    """The check copy of one template, at opaque type arguments (ir.md 4.3)."""
    template: TemplateId


@dataclass(frozen=True, slots=True)
class Initializer:
    """The one-expression body of a constant or a `var` initializer (ir.md 6.3)."""


BodyOrigin: TypeAlias = Instance | TemplateCheck | Initializer


@dataclass(slots=True)
class ShirParam:
    local: LocalId
    ty: TypeRef
    convention: Convention
    loc: Span


@dataclass(slots=True)
class ShirLocal:
    """`name` is None for a hidden local. `declared` is the type as WRITTEN, if any."""
    id: LocalId
    name: Optional[str]
    declared: Optional[TypeRef]
    loc: Optional[Span]


@dataclass(slots=True)
class ShirBody:
    fn_id: DeclId
    origin: BodyOrigin
    params: list[ShirParam]
    locals: list[ShirLocal]
    block: ShirBlock
