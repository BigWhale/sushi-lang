"""The answers of the `typecheck` pass for one body (docs/design/ir.md 7.3, ruling S5).

No SHIR node holds a fact that `typecheck` finds. Each answer is a row of the
`TypeckResults` of the body: one table for each `ShirBody`, one row kind for each decision
of the table in ir.md 7.3. Lowering lists each body with its table
(`LoweredProgram.typeck_of`), and the typecheck pass is the one writer (gate:
`tests/unit/test_typeck_results_have_one_writer.py`). A key has one answer: a second write
is an internal error. When the pass ends, `typeck_check.py` finds each answer that a node
needs and that the table does not hold.

A row for a position is keyed by the node and the position: a call argument and a
`print` argument by their index in the arguments, a part of an `Interp` by its index in
the parts, a capture of a lambda by the captured local, and a target of a `Destructure` by
its path, the index of the target and then its index in each `Nested` level.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, TypeAlias, TypeVar

from sushi_lang.internals import errors as er
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.callee import Callee, Conversion
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.loop_kind import LoopKind
from sushi_lang.semantics.typesys import Type


@dataclass(frozen=True, slots=True)
class ResolvedCall:
    """The callee of a call, with the type arguments of a generic method and, for an
    extension method, the name of the unit that declares it (C5)."""
    callee: Callee
    method_type_args: tuple[Type, ...] = ()
    unit: Optional[str] = None


@dataclass(frozen=True, slots=True)
class NoConversion:
    """A numeric cast, or a `??` whose error type is the error type of the function."""


#: What a `Cast` or a `Try` calls to change its operand.
ConversionChoice: TypeAlias = Conversion | NoConversion


@dataclass(frozen=True, slots=True)
class TryFacts:
    """A `??`: the tag of its success variant, its two payload types, and the return type
    of the function that the error leaves through."""
    success_tag: int
    ok: Type
    err: Type
    returns: Type


@dataclass(frozen=True, slots=True)
class MatchFacts:
    """The type of the scrutinee, and the integer type that a literal arm and a range arm
    are read at (None when the scrutinee is not an integer)."""
    scrutinee: Type
    integer: Optional[Type]


KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


def _write(table: dict[KeyT, ValueT], key: KeyT, value: ValueT, row: str) -> None:
    if key in table:
        er.raise_internal_error("CE0000", detail=f"the typecheck pass answered the {row} of "
                                                 f"{key} twice")
    table[key] = value


def _table() -> Any:
    """A table of one row kind: closed to the constructor, as a pass writes it."""
    return field(default_factory=dict, init=False, repr=False)


@dataclass(slots=True)
class TypeckResults:
    """The answers of `typecheck` for one body, one row kind for each decision of ir.md 7.3."""
    _node_types: dict[ShirId, Type] = _table()
    _local_types: dict[LocalId, Type] = _table()
    _capture_types: dict[tuple[ShirId, LocalId], Type] = _table()
    _callees: dict[ShirId, ResolvedCall] = _table()
    _arg_conventions: dict[tuple[ShirId, int], Convention] = _table()
    _instance_types: dict[ShirId, Type] = _table()
    _field_indices: dict[ShirId, int] = _table()
    _loop_kinds: dict[ShirId, LoopKind] = _table()
    _range_values: dict[ShirId, bool] = _table()
    _conversions: dict[ShirId, ConversionChoice] = _table()
    _try_facts: dict[ShirId, TryFacts] = _table()
    _display_types: dict[tuple[ShirId, int], Type] = _table()
    _match_facts: dict[ShirId, MatchFacts] = _table()
    _element_types: dict[tuple[ShirId, tuple[int, ...]], Type] = _table()

    def __copy__(self) -> TypeckResults:
        er.raise_internal_error("CE0000", detail="a copy of a TypeckResults is a second table "
                                                 "of one body")

    def __deepcopy__(self, memo: dict[int, object]) -> TypeckResults:
        return self.__copy__()

    # -- the read API: None when the table holds no answer ------------------------------

    def type_of(self, node: ShirId) -> Optional[Type]:
        """The type of an expression node."""
        return self._node_types.get(node)

    def local_type(self, local: LocalId) -> Optional[Type]:
        """The type of a local or a parameter, written or inferred."""
        return self._local_types.get(local)

    def capture_type(self, lam: ShirId, local: LocalId) -> Optional[Type]:
        """The type of the capture of `local` by the lambda `lam`."""
        return self._capture_types.get((lam, local))

    def callee_of(self, call: ShirId) -> Optional[ResolvedCall]:
        return self._callees.get(call)

    def convention_of(self, call: ShirId, position: int) -> Optional[Convention]:
        """The resolved convention of the argument at `position` of a call."""
        return self._arg_conventions.get((call, position))

    def instance_of(self, init: ShirId) -> Optional[Type]:
        """The instance type of a `StructInit`, an `EnumInit` or a `TupleInit`."""
        return self._instance_types.get(init)

    def field_index_of(self, node: ShirId) -> Optional[int]:
        return self._field_indices.get(node)

    def loop_kind_of(self, loop: ShirId) -> Optional[LoopKind]:
        return self._loop_kinds.get(loop)

    def range_value_of(self, node: ShirId) -> Optional[bool]:
        """Whether a `Range` is an `Iterator@(i32)` value."""
        return self._range_values.get(node)

    def conversion_of(self, node: ShirId) -> Optional[ConversionChoice]:
        """What a `Cast` or a `Try` calls."""
        return self._conversions.get(node)

    def try_facts_of(self, node: ShirId) -> Optional[TryFacts]:
        return self._try_facts.get(node)

    def display_type_of(self, node: ShirId, part: int) -> Optional[Type]:
        """The display type of a part of an `Interp`, or of an argument of `print`."""
        return self._display_types.get((node, part))

    def match_facts_of(self, match: ShirId) -> Optional[MatchFacts]:
        return self._match_facts.get(match)

    def element_type_of(self, node: ShirId, target: tuple[int, ...]) -> Optional[Type]:
        """The element type of the target of a `Destructure` at the path `target`."""
        return self._element_types.get((node, target))

    # -- the write API: the typecheck pass alone calls it --------------------------------

    def record_type(self, node: ShirId, ty: Type) -> None:
        _write(self._node_types, node, ty, "type")

    def record_local(self, local: LocalId, ty: Type) -> None:
        _write(self._local_types, local, ty, "local type")

    def record_capture(self, lam: ShirId, local: LocalId, ty: Type) -> None:
        _write(self._capture_types, (lam, local), ty, "capture type")

    def record_callee(self, call: ShirId, answer: ResolvedCall) -> None:
        _write(self._callees, call, answer, "callee")

    def record_convention(self, call: ShirId, position: int, convention: Convention) -> None:
        _write(self._arg_conventions, (call, position), convention, "convention")

    def record_instance(self, init: ShirId, ty: Type) -> None:
        _write(self._instance_types, init, ty, "instance type")

    def record_field_index(self, node: ShirId, index: int) -> None:
        _write(self._field_indices, node, index, "field index")

    def record_loop_kind(self, loop: ShirId, kind: LoopKind) -> None:
        _write(self._loop_kinds, loop, kind, "loop kind")

    def record_range_value(self, node: ShirId, is_value: bool) -> None:
        _write(self._range_values, node, is_value, "range value")

    def record_conversion(self, node: ShirId, conversion: ConversionChoice) -> None:
        _write(self._conversions, node, conversion, "conversion")

    def record_try(self, node: ShirId, facts: TryFacts) -> None:
        _write(self._try_facts, node, facts, "try facts")

    def record_display(self, node: ShirId, part: int, ty: Type) -> None:
        _write(self._display_types, (node, part), ty, "display type")

    def record_match(self, match: ShirId, facts: MatchFacts) -> None:
        _write(self._match_facts, match, facts, "match facts")

    def record_element(self, node: ShirId, target: tuple[int, ...], ty: Type) -> None:
        _write(self._element_types, (node, target), ty, "element type")
