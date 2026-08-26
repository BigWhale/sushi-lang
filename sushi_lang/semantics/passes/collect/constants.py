"""Constant definition collection."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import ERR
from sushi_lang.semantics.ast import ConstDef, Program
from sushi_lang.semantics.typesys import Type


@dataclass
class ConstSig:
    """A collected constant signature."""
    name: str
    loc: Optional[Span] = None
    name_span: Optional[Span] = None
    const_type: Optional[Type] = None
    type_span: Optional[Span] = None
    filename: Optional[str] = None  # The unit it was declared in (#473)
    # Note: value is validated later in type checking pass


@dataclass
class ConstantTable:
    """Registry of all constants collected by the collect pass."""
    by_name: Dict[str, ConstSig] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)


class ConstantCollector:
    """Collector for constant definitions."""

    def __init__(self, reporter: Reporter, constants: ConstantTable) -> None:
        """Initialize constant collector."""
        self.r = reporter
        # The unit being collected. This pass shares one reporter across every
        # unit, so a record it stores has to remember its own file (#473).
        self.current_unit_file: Optional[str] = None
        self.constants = constants

    def collect(self, root: Program) -> None:
        """Collect all constant definitions from program AST."""
        constants = getattr(root, "constants", None)
        if isinstance(constants, list):
            for const in constants:
                if isinstance(const, ConstDef):
                    self._collect_constant_def(const)

    def _collect_constant_def(self, const: ConstDef) -> None:
        """Collect a single constant definition."""
        name = getattr(const, "name", None)
        if not isinstance(name, str):
            return

        name_span: Optional[Span] = getattr(const, "name_span", None) or getattr(
            const, "loc", None
        )
        const_type: Optional[Type] = getattr(const, "ty", None)
        type_span: Optional[Span] = getattr(const, "type_span", None) or name_span

        if const_type is None:
            er.emit(self.r, ERR.CE0104, name_span, name=name)

        sig = ConstSig(
            name=name,
            name_span=name_span,
            const_type=const_type,
            type_span=type_span,
            filename=self.current_unit_file,
        )

        if name in self.constants.by_name:
            prev = self.constants.by_name[name]
            er.emit_with(self.r, ERR.CE0105, name_span, name=name) \
                .note("first defined here", prev.name_span, prev.filename).emit()
            return

        self.constants.order.append(name)
        self.constants.by_name[name] = sig
