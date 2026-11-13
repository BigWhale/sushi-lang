"""The answers of the `typecheck` pass for one body (docs/design/ir.md 7.3, ruling S5).

No SHIR node holds a fact that `typecheck` finds. Each answer is a row of the
`TypeckResults` of the body: one table for each `ShirBody`, one row kind for each decision
of the table in ir.md 7.3. Lowering lists each body with its table
(`LoweredProgram.typeck_of`), and the typecheck pass is the one writer (gate:
`tests/unit/test_result_tables_have_one_writer.py`). A key has one answer: a second write
is an internal error. When the pass ends, `typeck_check.py` finds each answer that a node
needs and that the table does not hold.

A row for a position is keyed by the node and the position: a call argument and a
`print` argument by their index in the arguments, a part of an `Interp` by its index in
the parts, a capture of a lambda by the captured local, and a target of a `Destructure` by
its path, the index of the target and then its index in each `Nested` level.

The value row holds a value of the constant evaluator, one shape for two keys: the value
that an integer literal holds at its type (ir.md 8.5, ruling R-W), and the value of the
constant that a `ConstRef` names. An initializer has no value row (ir.md 7.3). The global
row holds the symbol of the global of the `var` that a `StorageRef` names. The source row
of a call of a callable with a `peek` return holds the position of each argument in the
source set of the declaration, and -1 for the receiver (`RECEIVER_POSITION`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, TypeAlias

from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.callee import Callee, Conversion
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.loop_kind import LoopKind
from sushi_lang.semantics.shir.side_table import SideTable, row_table, write_once
from sushi_lang.semantics.typesys import DynamicArrayType, FunctionType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.shir import exprs as e
    from sushi_lang.semantics.const_eval import ConstantValue


@dataclass(frozen=True, slots=True)
class Tail:
    """The variadic tail of a call: the index of its first argument, the type `T[]` of the
    tail parameter, and the convention of that parameter (ir.md 7.4, S22)."""
    position: int
    ty: DynamicArrayType
    convention: Convention


@dataclass(frozen=True, slots=True)
class ResolvedCall:
    """The callee of a call, with the type arguments of a generic function or method and,
    for a free function or an extension method, the name of the unit that declares it (C5).
    SLIR lowering makes the symbol of a free function from it. A function, a method or a
    static of a binary or a hybrid library has no unit: its symbol is the `link_symbol` of
    the manifest (ir.md 8.6, S26). A callee with a variadic tail has its `Tail`."""
    callee: Callee
    type_args: tuple[Type, ...] = ()
    unit: Optional[str] = None
    link_symbol: Optional[str] = None
    tail: Optional[Tail] = None


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


def lambda_conventions(typeck: TypeckResults, lam: e.Lambda) -> tuple[Convention, ...]:
    """The convention of each parameter of `lam`. The modes of its function type answer,
    because a bare parameter takes its mode from the type of its position."""
    ty = typeck.type_of(lam.shir_id)
    if not isinstance(ty, FunctionType):
        return tuple(param.convention for param in lam.params)
    return tuple(mode.convention for mode in ty.modes)


@dataclass(slots=True)
class TypeckResults(SideTable):
    """The answers of `typecheck` for one body, one row kind for each decision of ir.md 7.3."""
    _node_types: dict[ShirId, Type] = row_table()
    _local_types: dict[LocalId, Type] = row_table()
    _capture_types: dict[tuple[ShirId, LocalId], Type] = row_table()
    _callees: dict[ShirId, ResolvedCall] = row_table()
    _arg_conventions: dict[tuple[ShirId, int], Convention] = row_table()
    _instance_types: dict[ShirId, Type] = row_table()
    _field_indices: dict[ShirId, int] = row_table()
    _loop_kinds: dict[ShirId, LoopKind] = row_table()
    _range_values: dict[ShirId, bool] = row_table()
    _conversions: dict[ShirId, ConversionChoice] = row_table()
    _try_facts: dict[ShirId, TryFacts] = row_table()
    _display_types: dict[tuple[ShirId, int], Type] = row_table()
    _match_facts: dict[ShirId, MatchFacts] = row_table()
    _element_types: dict[tuple[ShirId, tuple[int, ...]], Type] = row_table()
    _values: dict[ShirId, ConstantValue] = row_table()
    _globals: dict[ShirId, str] = row_table()
    _source_rows: dict[ShirId, tuple[int, ...]] = row_table()
    _iterator_nexts: dict[ShirId, ResolvedCall] = row_table()
    _index_positions: dict[ShirId, bool] = row_table()

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

    def is_index_position(self, node: ShirId) -> bool:
        """Whether an expression is the value of an index position, which SLIR widens to
        i64 (docs/adr/0001)."""
        return self._index_positions.get(node, False)

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

    def value_of(self, node: ShirId) -> Optional[ConstantValue]:
        """The value of an integer literal, of a negated integer literal or of a `ConstRef`."""
        return self._values.get(node)

    def global_of(self, node: ShirId) -> Optional[str]:
        """The symbol of the global of the `var` that a `StorageRef` names."""
        return self._globals.get(node)

    def global_type(self, symbol: str) -> Optional[Type]:
        """The type of the `var` whose global is `symbol`, from a `StorageRef` of the body
        that names it; None when the body names no such `var`."""
        return next((self._node_types.get(node) for node, held in self._globals.items()
                     if held == symbol), None)

    def sources_of(self, call: ShirId) -> Optional[tuple[int, ...]]:
        """The source positions of a call of a callable with a `peek` return: the index of
        each argument in the source set, and -1 for the receiver. None for any other call."""
        return self._source_rows.get(call)

    def iterator_next_of(self, call: ShirId) -> Optional[ResolvedCall]:
        """The `next()` that the state of an `Iterator.new` calls."""
        return self._iterator_nexts.get(call)

    # -- the write API: the typecheck pass alone calls it --------------------------------

    def record_type(self, node: ShirId, ty: Type) -> None:
        write_once(self._node_types, node, ty, "typecheck", "type")

    def record_local(self, local: LocalId, ty: Type) -> None:
        write_once(self._local_types, local, ty, "typecheck", "local type")

    def record_capture(self, lam: ShirId, local: LocalId, ty: Type) -> None:
        write_once(self._capture_types, (lam, local), ty, "typecheck", "capture type")

    def record_callee(self, call: ShirId, answer: ResolvedCall) -> None:
        write_once(self._callees, call, answer, "typecheck", "callee")

    def record_convention(self, call: ShirId, position: int, convention: Convention) -> None:
        write_once(self._arg_conventions, (call, position), convention, "typecheck", "convention")

    def record_instance(self, init: ShirId, ty: Type) -> None:
        write_once(self._instance_types, init, ty, "typecheck", "instance type")

    def record_field_index(self, node: ShirId, index: int) -> None:
        write_once(self._field_indices, node, index, "typecheck", "field index")

    def record_loop_kind(self, loop: ShirId, kind: LoopKind) -> None:
        write_once(self._loop_kinds, loop, kind, "typecheck", "loop kind")

    def record_range_value(self, node: ShirId, is_value: bool) -> None:
        write_once(self._range_values, node, is_value, "typecheck", "range value")

    def record_index_position(self, node: ShirId) -> None:
        self._index_positions[node] = True

    def record_conversion(self, node: ShirId, conversion: ConversionChoice) -> None:
        write_once(self._conversions, node, conversion, "typecheck", "conversion")

    def record_try(self, node: ShirId, facts: TryFacts) -> None:
        write_once(self._try_facts, node, facts, "typecheck", "try facts")

    def record_display(self, node: ShirId, part: int, ty: Type) -> None:
        write_once(self._display_types, (node, part), ty, "typecheck", "display type")

    def record_match(self, match: ShirId, facts: MatchFacts) -> None:
        write_once(self._match_facts, match, facts, "typecheck", "match facts")

    def record_element(self, node: ShirId, target: tuple[int, ...], ty: Type) -> None:
        write_once(self._element_types, (node, target), ty, "typecheck", "element type")

    def record_value(self, node: ShirId, value: ConstantValue) -> None:
        write_once(self._values, node, value, "typecheck", "value")

    def record_global(self, node: ShirId, symbol: str) -> None:
        write_once(self._globals, node, symbol, "typecheck", "global")

    def record_iterator_next(self, call: ShirId, answer: ResolvedCall) -> None:
        write_once(self._iterator_nexts, call, answer, "typecheck", "iterator next")

    def record_sources(self, call: ShirId, positions: tuple[int, ...]) -> None:
        write_once(self._source_rows, call, positions, "typecheck", "sources")
