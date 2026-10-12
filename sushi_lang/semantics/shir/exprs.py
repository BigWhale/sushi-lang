"""The SHIR expression nodes and the parts that they hold (docs/design/ir.md 7.4).

Data only. A node holds what the source says; what an expression IS (its type, its
callee, the index of a field) is an answer of the `typecheck` pass, in its own table
(ruling S5). The union `ShirExpr` is every expression node and the hole, and a
dispatch reads it (gate: `tests/unit/test_shir_node_unions_are_total.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TypeAlias

from sushi_lang.semantics.operators import BinOp, UnOp
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.ids import DeclId, LocalId
from sushi_lang.semantics.shir.nodes import Hole, ShirBlock, ShirNode, TypeRef
from sushi_lang.semantics.typesys import BorrowMode

# -- a literal value --------------------------------------------------------------
# The value as the source spells it, with no type: the type of a literal comes from
# its position, and that is an answer of `typecheck`.


@dataclass(frozen=True, slots=True)
class IntConst:
    """`radix` and `byte_spelling` are the written form; a diagnostic shows them."""
    value: int
    radix: int = 10
    byte_spelling: Optional[str] = None


@dataclass(frozen=True, slots=True)
class FloatConst:
    value: float


@dataclass(frozen=True, slots=True)
class BoolConst:
    value: bool


@dataclass(frozen=True, slots=True)
class BlankConst:
    """`~`."""


@dataclass(frozen=True, slots=True)
class StrConst:
    value: str


Constant: TypeAlias = IntConst | FloatConst | BoolConst | BlankConst | StrConst


# -- the base of every expression --------------------------------------------------


@dataclass(slots=True)
class ExprNode(ShirNode):
    """`readable` is the value of the expression when the compiler can read it.

    Lowering records it where `typecheck` needs a value: a divisor, a shift count, the
    operands of an overflow fold, the count of an array run, a range bound and a
    constant index (ruling E3). It is syntax, so `typecheck` reads it and does not call
    the constant evaluator.
    """
    readable: Optional[Constant] = field(default=None, kw_only=True)


# -- names -------------------------------------------------------------------------


@dataclass(slots=True)
class Lit(ExprNode):
    value: Constant


@dataclass(slots=True)
class LocalRef(ExprNode):
    local: LocalId


@dataclass(slots=True)
class ConstRef(ExprNode):
    """A `const`. Its value is computed on the AST (ruling Q5)."""
    const_id: DeclId


@dataclass(slots=True)
class StorageRef(ExprNode):
    """A unit-level `var`: a place with an address (docs/design/unit-storage.md)."""
    decl: DeclId


@dataclass(slots=True)
class GlobalRef(ExprNode):
    """A function used as a value."""
    decl: DeclId


# -- places and views --------------------------------------------------------------


@dataclass(slots=True)
class Field(ExprNode):
    """A struct field, or a tuple element (`"0"`). The index is `typecheck`'s."""
    base: ShirExpr
    name: str


@dataclass(slots=True)
class Index(ExprNode):
    """`unchecked` is true under a `dont_panic` header (ir.md 4.9)."""
    base: ShirExpr
    index: ShirExpr
    unchecked: bool


@dataclass(slots=True)
class Slice(ExprNode):
    """`data[a..b]`: a borrowed view (docs/design/slices.md)."""
    base: ShirExpr
    start: ShirExpr
    end: ShirExpr
    inclusive: bool


# -- calls -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Named:
    """A name that `scope` resolved, or any expression that can be called.

    `type_args` are the type arguments that the source writes on the call, as written
    (`identity@(i32)(5)`, `geo.nothing@(i32)()`); empty when it writes none.
    """
    ref: ShirExpr
    type_args: tuple[TypeRef, ...] = ()


@dataclass(frozen=True, slots=True)
class Dotted:
    """`x.f(...)`: a built-in, an extension, a perk method or an adapter."""
    recv: ShirExpr
    name: str


@dataclass(frozen=True, slots=True)
class Static:
    """A call on a type name: `Vec.at`, `List.new`, `hm.HashMap.new`. No receiver."""
    ty: TypeRef
    name: str


@dataclass(frozen=True, slots=True)
class BuiltinFn:
    """A function that the language gives with no declaration: `print`, `println`."""
    name: str


CallTarget: TypeAlias = Named | Dotted | Static | BuiltinFn


@dataclass(frozen=True, slots=True)
class Arg:
    """`marker` is the WRITTEN call-site mode, or None. `bloom` is `arr...`."""
    value: ShirExpr
    marker: Optional[Convention]
    bloom: bool = False


@dataclass(slots=True)
class Call(ExprNode):
    """What the source calls. The resolved `Callee` is `typecheck`'s (ir.md 7.6)."""
    target: CallTarget
    args: list[Arg]


# -- operators ---------------------------------------------------------------------


@dataclass(slots=True)
class Unary(ExprNode):
    op: UnOp
    operand: ShirExpr


@dataclass(slots=True)
class Binary(ExprNode):
    op: BinOp
    lhs: ShirExpr
    rhs: ShirExpr


@dataclass(slots=True)
class Cast(ExprNode):
    """A numeric cast or a conversion (ir.md 4.8)."""
    operand: ShirExpr
    target: TypeRef


# -- ownership ---------------------------------------------------------------------


@dataclass(slots=True)
class Borrow(ExprNode):
    place: ShirExpr
    mode: BorrowMode


@dataclass(slots=True)
class Take(ExprNode):
    """A marked field take, `nom s.out`, in a `let` or a `return`."""
    place: ShirExpr


@dataclass(slots=True)
class Try(ExprNode):
    """`??`, kept whole."""
    inner: ShirExpr


# -- a lambda ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LambdaParam:
    """The name, the written type and the span are on the `ShirLocal` of `local`."""
    local: LocalId
    convention: Convention


@dataclass(slots=True)
class Lambda(ExprNode):
    """`ret` and `err` are the written return type and error type, if any.

    `expr_body` is true when the source writes the body as one expression: `body` is then
    a block of one `Return` of it. The captures are in `Bindings`. The node stays in the
    tree through `borrow` (ruling Q8).
    """
    params: list[LambdaParam]
    ret: Optional[TypeRef]
    err: Optional[TypeRef]
    body: ShirBlock
    expr_body: bool


# -- constructions -----------------------------------------------------------------


@dataclass(slots=True)
class StructInit(ExprNode):
    """`fields` is in the declaration order. `written_order` is the evaluation order.

    It gives the positions of `fields` in the order that the source writes them
    (ruling R1): a named construction evaluates its fields as written. A field is an
    `Arg`, because a constructor argument keeps its written marker (`Box(nom s)`).

    `names` is None, except for a named construction whose names are not the declared
    fields (an unknown, a repeated or a missing name) or whose name has no struct. Then
    `fields` is in the written order, `written_order` is the identity, and `names` holds
    the written name of each field, for the typecheck pass to report.
    """
    decl: DeclId
    fields: list[Arg]
    written_order: tuple[int, ...]
    names: Optional[tuple[str, ...]]


@dataclass(slots=True)
class EnumInit(ExprNode):
    """A payload value is an `Arg` with its written marker (`Result.Ok(nom s)`)."""
    decl: DeclId
    variant: str
    payload: list[Arg]


@dataclass(slots=True)
class TupleInit(ExprNode):
    parts: list[ShirExpr]


@dataclass(frozen=True, slots=True)
class Fixed:
    """`[...]`: a fixed array."""


@dataclass(frozen=True, slots=True)
class Dynamic:
    """`from([...])` or `new()`: a dynamic array."""


ArrayKind: TypeAlias = Fixed | Dynamic


@dataclass(frozen=True, slots=True)
class Single:
    value: ShirExpr


@dataclass(frozen=True, slots=True)
class Repeat:
    """`[v; n]`: the value is evaluated first, then the count (ruling R2)."""
    value: ShirExpr
    count: ShirExpr


@dataclass(frozen=True, slots=True)
class RangeRun:
    """`[0..=5]`: a range fills the slots that it spans."""
    range: Range


ArrayRun: TypeAlias = Single | Repeat | RangeRun


@dataclass(slots=True)
class ArrayInit(ExprNode):
    kind: ArrayKind
    runs: list[ArrayRun]


# -- ranges and text ---------------------------------------------------------------


@dataclass(slots=True)
class Range(ExprNode):
    """`reverse` is true after an odd count of `.rev()` calls."""
    start: ShirExpr
    end: ShirExpr
    inclusive: bool
    reverse: bool


@dataclass(slots=True)
class Interp(ExprNode):
    """A text part is a `str`. Kept whole, so `typecheck` reports per part."""
    parts: list[str | ShirExpr]


ShirExpr: TypeAlias = (
    Lit | LocalRef | ConstRef | StorageRef | GlobalRef | Field | Index | Slice | Call
    | Unary | Binary | Cast | Borrow | Take | Try | Lambda | StructInit | EnumInit
    | TupleInit | ArrayInit | Range | Interp | Hole
)
