"""Constant definition collection."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

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
    filename: Optional[str] = None  # The file it was declared in (#473)
    unit_name: Optional[str] = None  # The unit that declared it, for the visibility gate
    is_public: bool = True           # Every constant is public until the default flips
    # Note: value is validated later in type checking pass


@dataclass
class ConstantTable:
    """Registry of all constants collected by the collect pass.

    Two views, the same pair and the same rule as `FunctionTable`: `by_name` is the FLAT
    one, and `by_unit` keeps every declaration under the unit that wrote it. Two units
    may each declare a private `SCRATCH`, so a bare name is no longer an answer on its
    own (`docs/design/unit-namespaces.md` section 9).
    """
    by_name: Dict[str, ConstSig] = field(default_factory=dict)
    by_unit: Dict[str, Dict[str, ConstSig]] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

    def declare(self, name: str, sig: ConstSig) -> None:
        """Register one declaration in both views. The ONE insert."""
        if name not in self.by_name:
            self.order.append(name)
            self.by_name[name] = sig
        unit = getattr(sig, "unit_name", None)
        if unit is not None:
            self.by_unit.setdefault(unit, {})[name] = sig

    def lookup(self, name: str, unit_name: Optional[str] = None,
               scope: object = None) -> Optional[ConstSig]:
        """What the name means inside `unit_name`. One name, no dict built."""
        from sushi_lang.semantics.unit_symbols import lookup_in_unit
        return lookup_in_unit(name, self.by_unit, self.by_name, unit_name, scope)


class ConstantCollector:
    """Collector for constant definitions."""

    def __init__(self, reporter: Reporter, constants: ConstantTable) -> None:
        """Initialize constant collector."""
        self.r = reporter
        # The unit being collected. This pass shares one reporter across every
        # unit, so a record it stores has to remember its own file (#473).
        self.current_unit_file: Optional[str] = None
        self.current_unit_name: Optional[str] = None
        # Which units came from a source library. A library clash is not this epic's to
        # lift: the consumer cannot see the library's private, and CE3011 for a function
        # narrows at the same phase as this would (section 7's table).
        self.library_units: Set[str] = set()
        self.constants = constants

    def collect(self, root: Program) -> None:
        """Collect all constant definitions from program AST."""
        constants = getattr(root, "constants", None)
        if isinstance(constants, list):
            for const in constants:
                if isinstance(const, ConstDef):
                    self._collect_constant_def(const)

    def _collides_with_a_library_export(self, prev: ConstSig, sig: ConstSig) -> bool:
        """Is either declaration a name a source library EXPORTS?

        A library's PUBLIC constant is a name the consumer can see and read, so taking
        it again is a duplicate. Its PRIVATE one is not: the consumer cannot see it, and
        section 9 gives each declaration its own `<unit>$<name>` global exactly as it
        does for a function -- the shape decision F made legal there and left refused
        here (#507).

        Both directions, because nothing fixes which of the two units is collected
        first, and each direction reads the LIBRARY declaration's own marker.
        """
        if prev.unit_name in self.library_units:
            return bool(prev.is_public)
        if sig.unit_name in self.library_units:
            return bool(sig.is_public)
        return False

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
            unit_name=self.current_unit_name,
            is_public=getattr(const, "is_public", True),
        )

        prev = self.constants.by_name.get(name)
        if prev is not None:
            # Another unit's declaration COEXISTS: each takes its own `<unit>$<name>`
            # global, so neither has to lose. The same name twice inside ONE unit is
            # the duplicate CE0105 still answers.
            prev_unit = getattr(prev, "unit_name", None)
            if (prev_unit is None
                    or prev_unit == self.current_unit_name
                    or self._collides_with_a_library_export(prev, sig)):
                er.emit_with(self.r, ERR.CE0105, name_span, name=name) \
                    .note("first defined here", prev.name_span, prev.filename).emit()
                return

        self.constants.declare(name, sig)
