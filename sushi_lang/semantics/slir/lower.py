"""SHIR to SLIR: the SLIR stage and the lowering of one body (docs/design/ir.md 8.2, 8.9, 8.10).

`SlirStage` is the stage of one unit, after `borrow`. The borrow pass gives it each body
that it finished with no error, with the tables that typecheck read the body in; a template
check never comes (ir.md 4.3). The stage lowers no initializer body of a constant or a
`var`: the global takes the value of the constant evaluator (ir.md 8.2). `lower_function`
makes the `SlirFunction` of the body from the body and its side tables. A construct that the
lowering does not handle yet stops the body with the stop code CE0155 (`slir, <construct>`):
the stage records the stop (`Stops.slir_stopped`) and the construct (`SlirStopped`, for the
dump), and the analysis goes on with the next body. The verifier (`verify.py`, ir.md 8.13)
checks a body that the lowering finished, and `SlirProgram` keeps it for the emitter
(`backend/slir/`).

The lowering handles the parameters, a `let`, a rebind (`lower_rebind.py`, the concat
peephole included), a call statement, a `return`, an
`if`, a `match`, a `while` and a counted loop over a range with their `break` and
`continue`, an `assert`, over literals, names of constants, locals, the operators, `and`
and `or` as control flow, the construction of an enum variant, of a struct, of a tuple and
of an array, a field read and write, an element read and write with its bounds check, a
slice, a destructure, a call, an interpolation, a `??`, `clone`, `expect`, `realise` and
`err` (`lower_copies.py`), a borrowed view in a view local with its region, and a take of an
owning value (`lower_takes.py`). The
operand of a value is the answer of `borrow` and the shape of the value alone (ir.md 7.9):
a `move` row gives `Move`; an `adopt` row gives
`Copy` of a place, a constant for a literal or a name of a constant, and `Move` of the
temporary of a new value. A value with no row is read with `Copy`. A literal and a name
of a constant take the value that typecheck wrote for them (ir.md 8.5). A new value goes
into the place that takes it (the local of a `let`, `_0`), else into a temporary of its
statement. The store that consumes a `move` operand marks its local moved, not the place
where the operand is built. A `var` is a `Global` place (`lower_rebind.global_place`), and a
`var` that owns stops (`StorageRef owning`). A read of a `var` by value goes into a temporary of the
statement first, because any call of the statement can change a `var`; a by-address
argument, a receiver and a value that its statement stores at once read it where it is.
Drop placement (`drops.py`) places every drop, every flag write
and every `StorageDead`, and joins the state of the locals at a join.

A local whose state differs between the paths of a join gets a drop flag right after it
(ir.md 8.10, rule 4), and so does a local whose state at a loop header is not the state
that a back edge brings. The walk finds such a local only at the join, after the point
that must make the flag. So a walk records what it finds (`_Learned`) and goes on, and the
body is lowered again with what the walks found, until a walk finds nothing new.

The construct of a stop is the SHIR class of the node, or one of these names: `generic
instance`, `conversion` and `perk method` for the body of a monomorphized generic instance,
of a conversion and of a perk method; `conversion` also for a `??` that calls a
declared conversion,
`library function` for a function, a method or a static whose declaring unit the typecheck
row does not name, `bloom` for a bloom argument, `StructInit reference field`
(`lower_aggregate.py`), `Match owning alternatives` (`lower_match.py`), and `ConstRef out of range` for a name
of a constant whose value its type cannot hold. The symbol, the unit and the return type of the function are facts of its declaration
(ir.md 6.4); the body of an extension method or of a static takes the symbol of the method
(`extension_decl_symbol`), and its receiver is `_1`. The unit is the declaring unit; a
function that has none (a lifted lambda, the instance of a template of a binary library)
goes into the module of the unit that holds its body.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span, display_filename
from sushi_lang.semantics.drop_set import drop_type_names
from sushi_lang.semantics.integer_width import fits_integer_type
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import Initializer, ShirBody, ShirLocal, TemplateCheck
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.slir import lower_arrays as arrays
from sushi_lang.semantics.slir import lower_calls as calls
from sushi_lang.semantics.slir import lower_control as ctl
from sushi_lang.semantics.slir import lower_match
from sushi_lang.semantics.slir import lower_rebind as rebinds
from sushi_lang.semantics.slir import lower_takes as takes
from sushi_lang.semantics.slir import lower_views as views
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.drops import DropPlacement, FlowState
from sushi_lang.semantics.slir.function import (
    STAGE, SlirBlock, SlirFunction, SlirLocal, SlirProgram, SlirStopped,
)
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.slir.lower_control import LoopFrame
from sushi_lang.semantics.slir.lower_signature import drop_impl_of, return_type_of, symbol_of
from sushi_lang.semantics.slir.lower_values import CallValue, ShortCircuit, Value, ViewValue
from sushi_lang.semantics.slir.verify import SlirVerifier
from sushi_lang.semantics.typesys import ArrayType, BuiltinType, Type, owns_resource
from sushi_lang.semantics.unit_symbols import declaring_unit

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.decls import DeclNames
    from sushi_lang.semantics.shir.lowering import BodyTables, LoweredProgram
    from sushi_lang.semantics.shir.stop import Stops
    from sushi_lang.semantics.tables import SymbolTables

#: The return slot of every function (ir.md 8.2).
RETURN_SLOT = LocalId(0)


@dataclass(slots=True)
class SlirStage:
    """The SLIR stage of one unit: it lowers each body that the borrow pass finished, while
    the program has no error (`failed`)."""
    shir: LoweredProgram
    slir: SlirProgram
    stops: Stops
    unit: str
    failed: Callable[[], bool]
    verifier: SlirVerifier = field(default_factory=SlirVerifier)

    def lower_body(self, decl: object, body: ShirBody, *, tables: SymbolTables,
                   filename: Optional[str]) -> None:
        """Lower the SHIR body of `decl` and record the outcome. An initializer body has no
        outcome, and neither has a body of a program that has an error (docs/design/ir.md
        7.9).

        A stop ends the lowering of the body: the stage records it with the file of the
        body. The verifier checks a function that the lowering finished (ir.md 8.13), and
        the program keeps it for the emitter.
        """
        if isinstance(body.origin, Initializer) or self.failed():
            return
        name = self.shir.decls.name_of(body.fn_id)
        drops = drop_type_names(tables.perk_impls)
        try:
            function = lower_function(decl, self.shir.tables_of(body), decls=self.shir.decls,
                                      name=name, unit=self.unit, tables=tables, drops=drops,
                                      label=display_filename(filename or "<input>"))
        except StageStop as stop:
            stop.filename = filename
            self.stops.slir_stopped(stop)
            self.slir.record(body.fn_id, SlirStopped(name, str(stop.params["construct"])))
            return
        self.verifier.verify(function, funcs=tables.funcs, extensions=tables.extensions,
                             drops=drops, filename=filename)
        self.slir.record(body.fn_id, function)


@dataclass(slots=True)
class Block:
    """A block while lowering fills it: its statements, and its terminator at the end."""
    id: BlockId
    statements: list[st.SlirStatement] = field(default_factory=list)
    terminator: Optional[tm.SlirTerminator] = None

    def closed(self) -> SlirBlock:
        if self.terminator is None:
            er.raise_internal_error("CE0000", detail=f"the SLIR block bb{self.id.index} has no "
                                                     f"terminator")
        return SlirBlock(self.id, tuple(self.statements), self.terminator)


#: An edge into a join: the block that jumps to it (None when its terminator names the
#: join already), and the state of the locals on that path.
Arrival: TypeAlias = tuple[Optional[Block], FlowState]


@dataclass(frozen=True, slots=True)
class _Learned:
    """What a walk found at a join for an earlier point: the locals that get a drop flag,
    and the locals whose state a loop header does not know, by the loop."""
    flags: frozenset[ShirLocalId] = frozenset()
    unknown_at: frozenset[tuple[ShirId, ShirLocalId]] = frozenset()


def lower_function(decl: object, answers: BodyTables, *, decls: DeclNames, name: str,
                   unit: str, tables: SymbolTables, drops: frozenset[str],
                   label: str) -> SlirFunction:
    """The `SlirFunction` of one body that `borrow` finished, from the body and its tables.
    `label` names the file of the body in the position of a trap.

    Raises `StageStop` at the first construct that the lowering does not handle yet.
    """
    body = answers.body
    if isinstance(body.origin, TemplateCheck):
        er.raise_internal_error("CE0000", detail=f"the template check '{name}' reached SLIR")
    home = declaring_unit(decl, unit)
    symbol = symbol_of(decl, home, body, drop_impl_of(decl, tables.perk_impls, drops))
    ret = return_type_of(decl, tables, body)
    borrows = getattr(decl, "borrowed_return", None) is not None
    shir_locals = {local.id: local for local in body.locals}
    learned = _Learned()
    while True:
        lowering = Lowering(answers, decls, drops, shir_locals, learned, label)
        lowering.new_local(ret, None, None, view=borrows and lowering.owns(ret))
        params = lowering.parameters()
        lowering.lower_block(ret)
        found = _Learned(learned.flags | lowering.found_flags,
                         learned.unknown_at | lowering.found_unknown)
        if found != learned:
            learned = found
            continue
        blocks = tuple(block.closed() for block in lowering.blocks)
        return SlirFunction(symbol, name, unit if home is None else home, params,
                            tuple(lowering.locals), blocks, blocks[0].id)


@dataclass(slots=True)
class Lowering:
    """The lowering of one body: its SLIR locals, its block and its drop placement."""
    answers: BodyTables
    decls: DeclNames
    drops: frozenset[str]
    shir_locals: dict[ShirLocalId, ShirLocal]
    learned: _Learned
    label: str
    locals: list[SlirLocal] = field(default_factory=list)
    ids: dict[ShirLocalId, LocalId] = field(default_factory=dict)
    shir_of: dict[LocalId, ShirLocalId] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=lambda: [Block(BlockId(0))])
    block: Block = field(init=False)
    placement: DropPlacement = field(default_factory=DropPlacement)
    loops: list[LoopFrame] = field(default_factory=list)
    declared: Optional[LocalId] = None
    holder: Optional[ShirId] = None
    found_flags: set[ShirLocalId] = field(default_factory=set)
    found_unknown: set[tuple[ShirId, ShirLocalId]] = field(default_factory=set)
    takes: dict[p.Place, tuple[p.Field, ...]] = field(default_factory=dict)
    calls: dict[ShirId, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.block = self.blocks[0]

    @property
    def typeck(self) -> TypeckResults:
        return self.answers.typeck

    # -- locals --------------------------------------------------------------------------

    def new_local(self, ty: Type, name: Optional[str], loc: Optional[Span],
                  view: bool = False) -> LocalId:
        """The next SLIR local (L4): ids in the order that lowering makes the locals."""
        ident = LocalId(len(self.locals))
        self.locals.append(SlirLocal(ident, ty, name, loc, view))
        return ident

    def owns(self, ty: Type) -> bool:
        return owns_resource(ty, self.drops)

    def bind(self, local: ShirLocalId, span: Span, view: bool = False) -> tuple[LocalId, Type]:
        """The SLIR local of a parameter or of a `let`, with its type."""
        ty = self.typeck.local_type(local)
        if ty is None:
            er.raise_internal_error("CE0000", span=span, detail="a local with no type reached SLIR")
        shir_local = self.shir_locals.get(local)
        ident = self.new_local(ty, None if shir_local is None else shir_local.name,
                               None if shir_local is None else shir_local.loc, view)
        self.ids[local] = ident
        self.shir_of[ident] = local
        return ident, ty

    def give_flag(self, ident: LocalId) -> None:
        """The drop flag of `ident` as the next local, if a walk found that it needs one."""
        if self.shir_of[ident] in self.learned.flags:
            self.placement.flag(ident, self.new_local(BuiltinType.BOOL, None, None))

    def local_of(self, local: ShirLocalId, span: Span) -> LocalId:
        ident = self.ids.get(local)
        if ident is None:
            er.raise_internal_error("CE0000", span=span,
                                    detail="SLIR lowering reads a local before its declaration")
        return ident

    def type_of(self, node: e.ShirExpr) -> Type:
        ty = self.typeck.type_of(node.shir_id)
        if ty is None:
            er.raise_internal_error("CE0000", span=node.loc,
                                    detail=f"a {type(node).__name__} with no type reached SLIR")
        return ty

    def parameters(self) -> tuple[tuple[LocalId, Convention], ...]:
        """`_1` to `_n`, each with its convention (L2, L4); an `Owned` one owns (L15)."""
        params = []
        for param in self.answers.body.params:
            if param.pack:
                raise StageStop(STAGE, "ShirParam", param.loc)
            ident, ty = self.bind(param.local, param.loc)
            self.placement.parameter(ident, param.convention, self.owns(ty))
            params.append((ident, param.convention))
        for param, (ident, _) in zip(self.answers.body.params, params, strict=True):
            self.give_flag(ident)
            self.emit(self.placement.flag_declared(ident, param.loc))
        return tuple(params)

    # -- statements ----------------------------------------------------------------------

    def emit(self, statements: list[st.SlirStatement]) -> None:
        for statement in statements:
            self.block.statements += [*self.placement.end_before_change(statement), statement]

    @property
    def alive(self) -> bool:
        """Whether the path that lowering is on goes on: its block has no terminator."""
        return self.block.terminator is None

    def new_block(self) -> Block:
        """The next block: ids in the order that lowering makes the blocks (L5)."""
        block = Block(BlockId(len(self.blocks)))
        self.blocks.append(block)
        return block

    def jump(self, terminator: tm.SlirTerminator) -> None:
        """End the block with a terminator that goes on to other blocks. A flag of a local
        that the path moved is written first, because a join can read it (L13)."""
        self.emit(self.placement.flush())
        self.block.terminator = terminator

    def lower_block(self, ret: Type) -> None:
        """The body block, then the end of a bare `~` body that reaches it."""
        block = self.answers.body.block
        self.placement.open_scope()
        for stmt in block.statements:
            self.statement(stmt)
        if self.alive:
            if ret != BuiltinType.BLANK:
                er.raise_internal_error("CE0000", span=block.loc,
                                        detail="a body that answers a value reaches its end")
            self.finish(r.Use(p.Const(k.Blank())), block.loc)

    def statement(self, stmt: s.ShirStmt) -> None:
        """One statement, in a scope of its own that holds its temporaries (L12). The flags
        of the locals that it moved are written before its temporaries die, and the flag of
        the local that it declares after them (L13)."""
        if not self.alive:
            er.raise_internal_error("CE0000", span=stmt.loc,
                                    detail="a SHIR statement after the end of its block")
        arm = _STATEMENTS.get(type(stmt))
        if arm is None:
            dispatch.stop(STAGE, stmt)
        self.placement.open_statement(stmt.shir_id)
        arm(self, stmt)
        if self.alive:
            self.emit(views.after(self, stmt))
            self.emit(self.placement.flush())
            self.emit(self.placement.close_scope(stmt.loc))
            if self.declared is not None:
                self.emit(self.placement.flag_declared(self.declared, stmt.loc))
        else:
            self.placement.leave_scope()
        self.declared = None

    def assign(self, stmt: s.Assign) -> None:
        target = stmt.target
        if isinstance(target, (e.Field, e.Index)) and not stmt.is_init:
            agg.field_write(self, stmt, target)
            return
        if isinstance(target, e.StorageRef) and not stmt.is_init:
            rebinds.rebind_global(self, stmt, target)
            return
        if not isinstance(target, e.LocalRef):
            dispatch.stop(STAGE, target)
        if stmt.is_init and isinstance(stmt.value, e.Borrow):
            views.let_reference(self, stmt, target)
        elif stmt.is_init and self.answers.ownership.ownership_of(
                stmt.value.shir_id) is Ownership.REJECT:
            views.let_view(self, stmt, target)
        elif stmt.is_init:
            value = self.value(stmt.value)
            ident, ty = self.bind(target.local, stmt.loc)
            self.give_flag(ident)
            self.placement.declare(ident, self.owns(ty))
            self.emit([st.StorageLive(ident, stmt.loc)])
            self.write(p.Local(ident), value, stmt.loc)
            self.declared = ident
        else:
            rebinds.rebind(self, stmt, target)

    def expr_stmt(self, stmt: s.ExprStmt) -> None:
        """A value that nobody reads still writes a destination local (L16)."""
        self.temporary(self.value(stmt.value), self.type_of(stmt.value), stmt.value.loc)

    def ret(self, stmt: s.Return) -> None:
        self.finish(self.value(stmt.value), stmt.loc)

    def finish(self, value: Value, span: Span) -> None:
        """`_0` first, then the drops of the exit, then the `return` (L14)."""
        self.write(p.Local(RETURN_SLOT), value, span)
        self.emit(self.placement.exit_function(span))
        self.block.terminator = tm.Return(span)

    def write(self, place: p.Place, value: Value, span: Span) -> None:
        """Store `value` into `place`. The store is the statement that consumes each `move`
        operand of the value, so it marks the local of each one moved (ir.md 8.10, rule 3),
        and the parts that a field take leaves (`takes`) drop right before it (ir.md 8.9).
        Two takes of one root move the root one time, and a part that a take of the value
        moves does not drop."""
        if isinstance(value, ShortCircuit):
            ctl.short_circuit(self, place, value)
            return
        if isinstance(value, ViewValue):
            if not (isinstance(place, p.Local) and self.locals[place.id.index].view):
                er.raise_internal_error("CE0000", span=span,
                                        detail="a view into an owning place reached SLIR")
            value = value.value
        moves = _moves(value)
        left = dict.fromkeys(part for moved in moves for part in self.takes.pop(moved, ()))
        for part in left:
            if part not in moves:
                self.emit(self.placement.drop_part(part, span))
        if isinstance(value, CallValue):
            self.emit([st.Call(place, value.callee, value.args, span)])
        else:
            self.emit([st.Assign(place, value, span)])
        for root in dict.fromkeys(_root(moved) for moved in moves):
            self.placement.move(root, span)

    def temporary(self, value: Value, ty: Type, span: Span, held: bool = False) -> LocalId:
        """A new temporary of the statement that holds `value`, or with `held` a hidden
        owner local of the block. A view goes into a view local, in a region over its owners
        that ends with the storage of the temporary."""
        view = isinstance(value, ViewValue)
        ident = self.new_local(ty, None, None, view)
        (self.placement.declare if held else self.placement.temporary)(
            ident, self.owns(ty) and not view)
        self.emit([st.StorageLive(ident, span)])
        if isinstance(value, ViewValue):
            self.emit(self.placement.begin_region(ident, value.owners, frozenset(), span))
        self.write(p.Local(ident), value, span)
        return ident

    # -- values and operands -------------------------------------------------------------

    def value(self, expr: e.ShirExpr) -> Value:
        arm = _VALUES.get(type(expr))
        if arm is None:
            dispatch.stop(STAGE, expr)
        return arm(self, expr)

    def operand(self, expr: e.ShirExpr, *, in_place: bool = False) -> p.Operand:
        """The operand of `expr`, from its `Ownership` row and its shape (ir.md 7.9). A
        literal and a name of a constant are a constant with any other row: an `adopt` of a
        string constant takes a literal, which owns no heap. A read of a `var` goes into a
        temporary of the statement, unless `in_place` reads it where it is. A slice of a
        `var` is a view, which owns nothing: it gets no temporary."""
        row = self.answers.ownership.ownership_of(expr.shir_id)
        if row is Ownership.REJECT:
            er.raise_internal_error("CE0000", span=expr.loc,
                                    detail="a reject row out of a borrowed let reached SLIR")
        if isinstance(expr, (e.LocalRef, e.StorageRef)) or (
                isinstance(expr, (e.Field, e.Index, e.Slice)) and row is not Ownership.MOVE):
            place = agg.place_of(self, expr)
            if row is Ownership.MOVE:
                return p.Move(place)
            root = walk_place(expr, Step.MEMBER | Step.INDEX).node
            if in_place or isinstance(expr, e.Slice) or not isinstance(root, e.StorageRef):
                return p.Copy(place)
            return p.Copy(p.Local(self.temporary(r.Use(p.Copy(place)), self.type_of(expr),
                                                 expr.loc)))
        if row is Ownership.MOVE and isinstance(expr, e.Field):
            return takes.take_field(self, expr)
        if row is Ownership.MOVE:
            er.raise_internal_error("CE0000", span=expr.loc,
                                    detail=f"a move row on a {type(expr).__name__}, which is no place")
        if isinstance(expr, e.Lit):
            return p.Const(k.of_literal(expr, self.typeck))
        if isinstance(expr, e.ConstRef):
            return p.Const(self.constant(expr))
        temp = self.temporary(self.value(expr), self.type_of(expr), expr.loc,
                              held=expr.shir_id == self.holder)
        moves = row is Ownership.ADOPT and not self.locals[temp.index].view
        return p.Move(p.Local(temp)) if moves else p.Copy(p.Local(temp))

    def constant(self, ref: e.ConstRef) -> k.Constant:
        """The value of the constant that `ref` names, from its typecheck row. A value that its
        type cannot hold stops (`ConstRef out of range`): the constant evaluator does not
        wrap a bit pattern, and it gives a value to an initializer with a range error or with
        a count of elements that its `T[N]` does not hold."""
        value = self.typeck.value_of(ref.shir_id)
        if value is None:
            er.raise_internal_error("CE0000", span=ref.loc,
                                    detail="a constant with no value reached SLIR")
        constant = k.of_value(value)
        if not _holds(constant):
            dispatch.stop(STAGE, ref, state="out of range")
        return constant

    def leaf(self, expr: e.Lit | e.LocalRef | e.ConstRef | e.StorageRef) -> Value:
        """A value that its statement stores at once, so a `var` is read where it is."""
        return r.Use(self.operand(expr, in_place=True))

    def binary(self, node: e.Binary) -> Value:
        if node.op in ("and", "or"):
            return ShortCircuit(node.op, self.operand(node.lhs), node.rhs, node.loc)
        lhs = agg.settled(self, node.lhs, self.operand(node.lhs), (node.rhs,))
        return r.Binary(node.op, lhs, self.operand(node.rhs))

    def unary(self, node: e.Unary) -> Value:
        return r.Unary(node.op, self.operand(node.operand))

    def cast(self, node: e.Cast) -> Value:
        """A numeric cast, or a cast to the type of its operand. A cast that calls a
        conversion stops in `typecheck`, so no row here calls one."""
        return r.Cast(self.operand(node.operand), self.type_of(node))

    def enum_init(self, node: e.EnumInit) -> Value:
        """An enum variant as an aggregate of its instance type (L10)."""
        ty = self.typeck.instance_of(node.shir_id)
        if ty is None:
            er.raise_internal_error("CE0000", span=node.loc,
                                    detail="an enum construction with no type reached SLIR")
        values = [arg.value for arg in node.payload]
        return r.Aggregate(r.AggVariant(ty, node.variant),
                           agg.in_written_order(self, values, range(len(values))))

_STATEMENTS: dict[type, Callable[..., None]] = {
    s.Assign: Lowering.assign,
    s.Destructure: agg.destructure,
    s.ExprStmt: Lowering.expr_stmt,
    s.Return: Lowering.ret,
    s.If: ctl.if_,
    s.Match: lower_match.match,
    s.Loop: ctl.loop,
    s.Break: ctl.break_,
    s.Continue: ctl.continue_,
    s.Assert: ctl.assert_,
}

_VALUES: dict[type, Callable[..., Value]] = {
    e.Lit: Lowering.leaf,
    e.LocalRef: Lowering.leaf,
    e.ConstRef: Lowering.leaf,
    e.StorageRef: Lowering.leaf,
    e.Binary: Lowering.binary,
    e.Unary: Lowering.unary,
    e.Cast: Lowering.cast,
    e.EnumInit: Lowering.enum_init,
    e.StructInit: agg.struct_init,
    e.TupleInit: agg.tuple_init,
    e.Field: agg.field,
    e.Index: agg.field,
    e.Slice: agg.field,
    e.ArrayInit: arrays.array_init,
    e.Call: calls.call,
    e.Interp: calls.interp,
    e.Try: ctl.try_,
    e.Take: takes.take_value,
}


def _holds(constant: k.Constant) -> bool:
    """Whether the type of `constant` holds it, at every depth: each integer is in the
    range of its type, and each array has the length of its type."""
    if isinstance(constant, k.Int):
        return fits_integer_type(constant.value, constant.ty)
    if not isinstance(constant, k.Aggregate):
        return True
    if isinstance(constant.ty, ArrayType) and len(constant.parts) != constant.ty.size:
        return False
    return all(_holds(part) for part in constant.parts)


def _moves(value: r.Rvalue | CallValue) -> list[p.Place]:
    """The place of each `move` operand of a value."""
    parts = (tuple(operand for operand, _ in value.args) if isinstance(value, CallValue)
             else r.operands_of(value))
    return [part.place for part in parts if isinstance(part, p.Move)]


def _root(place: p.Place) -> LocalId:
    """The local that a move takes: a local, or a part of a local (the payload of a
    variant, `move ((_3 as Ok).0)`, or a field that a take moves), which moves the whole
    local."""
    root = p.root_of(place)
    if not isinstance(root, p.Local):
        er.raise_internal_error("CE0000", detail=f"SLIR lowering moves out of {place}")
    return root.id
