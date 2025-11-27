# semantics/passes/collect/constants.py
"""Constant definition collection for Phase 0."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from internals.report import Reporter, Span
from internals import errors as er
from internals.errors import ERR
from semantics.ast import ConstDef, Program
from semantics.typesys import Type

from .utils import format_location


@dataclass
class ConstSig:
    """Phase 0 constant signature.

    Types are Optional to allow defensive collection before full typing.
    """
    name: str
    loc: Optional[Span] = None
    name_span: Optional[Span] = None
    const_type: Optional[Type] = None
    type_span: Optional[Span] = None
    # Note: value is validated later in type checking pass


@dataclass
class ConstantTable:
    """Registry of all constants collected in Phase 0."""
    by_name: Dict[str, ConstSig] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)


class ConstantCollector:
    """Collector for constant definitions.

    Collects constant signatures during Phase 0, validating:
    - Explicit type annotations (constants must be typed)
    - No duplicate names

    Value validation is deferred to type checking pass.
    """

    def __init__(self, reporter: Reporter, constants: ConstantTable) -> None:
        """Initialize constant collector.

        Args:
            reporter: Error reporter
            constants: Shared constant table to populate
        """
        self.r = reporter
        self.constants = constants

    def collect(self, root: Program) -> None:
        """Collect all constant definitions from program AST.

        Args:
            root: Program AST node
        """
        constants = getattr(root, "constants", None)
        if isinstance(constants, list):
            for const in constants:
                if isinstance(const, ConstDef):
                    self._collect_constant_def(const)

    def _collect_constant_def(self, const: ConstDef) -> None:
        """Collect a single constant definition.

        Args:
            const: Constant definition AST node
        """
        name = getattr(const, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(const, "name_span", None) or getattr(
            const, "loc", None
        )
        const_type: Optional[Type] = getattr(const, "ty", None)
        type_span: Optional[Span] = getattr(const, "type_span", None) or name_span

        # Check for missing type annotation (constants must be explicitly typed)
        if const_type is None:
            er.emit(self.r, ERR.CE0104, name_span, name=name)

        sig = ConstSig(
            name=name,
            name_span=name_span,
            const_type=const_type,
            type_span=type_span,
        )

        # Check for duplicate constant names
        if name in self.constants.by_name:
            prev = self.constants.by_name[name]
            prev_loc = format_location(self.r, prev.name_span)
            er.emit(self.r, ERR.CE0105, name_span, name=name, prev_loc=prev_loc)
            return

        self.constants.order.append(name)
        self.constants.by_name[name] = sig
