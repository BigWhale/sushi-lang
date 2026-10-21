"""SHIR to SLIR: the SLIR stage and the lowering of one body (docs/design/ir.md 8.2, 8.9, 8.10).

`SlirStage` is the stage of one unit, after `borrow`. The borrow pass gives it each body
that it finished with no error, with the tables that typecheck read the body in; a
template check never comes (ir.md 4.3). `lower_function` makes the `SlirFunction` of the
body from the body and its side tables. A construct that the lowering does not handle yet
stops the body with the stop code CE0155 (`slir, <construct>`): the stage records the stop
(`Stops.slir_stopped`) and the construct (`SlirStopped`, for the dump), and the analysis
goes on with the next body. The verifier (`verify.py`, ir.md 8.13) checks a body that the
lowering finished, and the body meets the next stage, which is not built yet
(`Stops.finished`).

The lowering handles the parameters, a `let`, a rebind, a call statement, a `return`, an
`if`, a `while` with its `break` and `continue`, over literals, locals, the operators,
`and` and `or` as control flow, the construction of an enum variant, a call of a free
function, `print`, `println`, an interpolation and a `??`. The operand of a value is the
answer of `borrow` and the shape of the value alone (ir.md 7.9): a `move` row gives
`Move`; an `adopt` row gives `Copy` of a place, a constant for a literal, and `Move` of the
temporary of a new value. A value with no row is read with `Copy`. A new value goes into
the place that takes it (the local of a `let`, `_0`), else into a temporary of its
statement. The store that consumes a `move` operand marks its local moved, not the place
where the operand is built. Drop placement (`drops.py`) places every drop, every flag
write and every `StorageDead`, and it joins the state of the locals where paths meet.

A local whose state differs between the paths of a join gets a drop flag right after it
(ir.md 8.10, rule 4), and so does a local whose state at a loop header is not the state
that a back edge brings. The walk finds such a local only at the join, after the point
that must make the flag. So a walk records what it finds (`_Learned`) and goes on, and the
body is lowered again with what the walks found, until a walk finds nothing new.

The construct of a stop is the SHIR class of the node, or one of these names: `Initializer`
for the body of a constant; `generic instance`, `conversion`, `perk method` and `method`
for the body of a monomorphized generic instance, of a conversion, of a perk method and of
an extension method; `borrowed view` for a `reject` row, `borrowed rebind` for a rebind of
a borrowed parameter that owns, `borrowed try` for a `??` over an owning wrapper that the
body only reads, `conversion` also for a `??` that calls a declared conversion, `library
function` for a callee whose declaring unit the typecheck row does not name, and `bloom`
for a bloom argument. The
symbol, the unit and the return type of the function are facts of its declaration (ir.md
6.4). The unit is the declaring unit; a function that has none (a lifted lambda, the
instance of a template of a binary library) goes into the module of the unit that holds
its body.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import ExtendDef, FuncDef
from sushi_lang.semantics.channel import channel_result, has_channel
from sushi_lang.semantics.drop_set import drop_type_names
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import Initializer, ShirBody, ShirLocal, TemplateCheck
from sushi_lang.semantics.shir.callee import Conversion, Free, Intrinsic
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.loop_kind import While
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.typeck import ResolvedCall, TypeckResults
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.drops import DropPlacement, FlowState
from sushi_lang.semantics.slir.function import (
    STAGE, SlirBlock, SlirFunction, SlirLocal, SlirProgram, SlirStopped,
)
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.slir.verify import SlirVerifier
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.typesys import BuiltinType, EnumType, Type, owns_resource
from sushi_lang.semantics.unit_symbols import declaring_unit, function_symbol

if TYPE_CHECKING:
    from sushi_lang.semantics.shir.decls import DeclNames
    from sushi_lang.semantics.shir.lowering import BodyTables, LoweredProgram
    from sushi_lang.semantics.shir.stop import Stops
    from sushi_lang.semantics.tables import SymbolTables

#: The return slot of every function (ir.md 8.2).
RETURN_SLOT = LocalId(0)


@dataclass(slots=True)
class SlirStage:
    """The SLIR stage of one unit: it lowers each body that the borrow pass finished."""
    shir: LoweredProgram
    slir: SlirProgram
    stops: Stops
    unit: str
    verifier: SlirVerifier = field(default_factory=SlirVerifier)

    def lower_body(self, decl: object, body: ShirBody, *, tables: SymbolTables,
                   filename: Optional[str]) -> None:
        """Lower the SHIR body of `decl` and record the outcome.

        A stop ends the lowering of the body: the stage records it with the file of the
        body. The verifier checks a function that the lowering finished (ir.md 8.13), and
        the function meets the next stage.
        """
        name = self.shir.decls.name_of(body.fn_id)
        drops = drop_type_names(tables.perk_impls)
        try:
            function = lower_function(decl, self.shir.tables_of(body), decls=self.shir.decls,
                                      name=name, unit=self.unit, tables=tables, drops=drops)
        except StageStop as stop:
            stop.filename = filename
            self.stops.slir_stopped(stop)
            self.slir.record(body.fn_id, SlirStopped(name, str(stop.params["construct"])))
            return
        self.verifier.verify(function, funcs=tables.funcs, drops=drops, filename=filename)
        self.slir.record(body.fn_id, function)
        self.stops.finished(name, body.block.loc, filename)


@dataclass(slots=True)
class _Block:
    """A block while lowering fills it: its statements, and its terminator at the end."""
    id: BlockId
    statements: list[st.SlirStatement] = field(default_factory=list)
    terminator: Optional[tm.SlirTerminator] = None

    def closed(self) -> SlirBlock:
        if self.terminator is None:
            er.raise_internal_error("CE0000", detail=f"the SLIR block bb{self.id.index} has no "
                                                     f"terminator")
        return SlirBlock(self.id, tuple(self.statements), self.terminator)


@dataclass(frozen=True, slots=True)
class _CallValue:
    """A call, before lowering knows the place that takes its value."""
    callee: st.SlirCallee
    args: tuple[tuple[p.Operand, Convention], ...]


@dataclass(frozen=True, slots=True)
class _ShortCircuit:
    """`and` or `or`, before lowering knows the place that takes its value: the left operand,
    and the right operand that a block of its own evaluates (ir.md 8.9)."""
    op: str
    lhs: p.Operand
    rhs: e.ShirExpr
    span: Span


_Value: TypeAlias = r.Rvalue | _CallValue | _ShortCircuit
#: An edge into a join: the block that jumps to it (None when its terminator names the
#: join already), and the state of the locals on that path.
_Arrival: TypeAlias = tuple[Optional[_Block], FlowState]


@dataclass(frozen=True, slots=True)
class _Learned:
    """What a walk found at a join for an earlier point: the locals that get a drop flag,
    and the locals whose state a loop header does not know, by the loop."""
    flags: frozenset[ShirLocalId] = frozenset()
    unknown_at: frozenset[tuple[ShirId, ShirLocalId]] = frozenset()


@dataclass(slots=True)
class _LoopFrame:
    """The loop that the walk is in: its header and exit blocks, the count of the scopes
    outside its body, and the state of each `break` and of each path to the header."""
    header: BlockId
    exit: BlockId
    depth: int
    breaks: list[FlowState] = field(default_factory=list)
    continues: list[FlowState] = field(default_factory=list)


def lower_function(decl: object, answers: BodyTables, *, decls: DeclNames, name: str,
                   unit: str, tables: SymbolTables, drops: frozenset[str]) -> SlirFunction:
    """The `SlirFunction` of one body that `borrow` finished, from the body and its tables.

    Raises `StageStop` at the first construct that the lowering does not handle yet.
    """
    body = answers.body
    if isinstance(body.origin, TemplateCheck):
        er.raise_internal_error("CE0000", detail=f"the template check '{name}' reached SLIR")
    if isinstance(body.origin, Initializer):
        raise StageStop(STAGE, "Initializer", body.block.loc)
    home = declaring_unit(decl, unit)
    symbol = _symbol(decl, home, body)
    ret = _return_type(decl, tables, body)
    shir_locals = {local.id: local for local in body.locals}
    learned = _Learned()
    while True:
        lowering = _Lowering(answers, decls, drops, shir_locals, learned)
        lowering.new_local(ret, None, None)
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
class _Lowering:
    """The lowering of one body: its SLIR locals, its block and its drop placement."""
    answers: BodyTables
    decls: DeclNames
    drops: frozenset[str]
    shir_locals: dict[ShirLocalId, ShirLocal]
    learned: _Learned
    locals: list[SlirLocal] = field(default_factory=list)
    ids: dict[ShirLocalId, LocalId] = field(default_factory=dict)
    shir_of: dict[LocalId, ShirLocalId] = field(default_factory=dict)
    blocks: list[_Block] = field(default_factory=lambda: [_Block(BlockId(0))])
    block: _Block = field(init=False)
    placement: DropPlacement = field(default_factory=DropPlacement)
    loops: list[_LoopFrame] = field(default_factory=list)
    declared: Optional[LocalId] = None
    found_flags: set[ShirLocalId] = field(default_factory=set)
    found_unknown: set[tuple[ShirId, ShirLocalId]] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.block = self.blocks[0]

    @property
    def typeck(self) -> TypeckResults:
        return self.answers.typeck

    # -- locals --------------------------------------------------------------------------

    def new_local(self, ty: Type, name: Optional[str], loc: Optional[Span]) -> LocalId:
        """The next SLIR local (L4): ids in the order that lowering makes the locals."""
        ident = LocalId(len(self.locals))
        self.locals.append(SlirLocal(ident, ty, name, loc))
        return ident

    def owns(self, ty: Type) -> bool:
        return owns_resource(ty, self.drops)

    def bind(self, local: ShirLocalId, span: Span) -> tuple[LocalId, Type]:
        """The SLIR local of a parameter or of a `let`, with its type."""
        ty = self.typeck.local_type(local)
        if ty is None:
            er.raise_internal_error("CE0000", span=span, detail="a local with no type reached SLIR")
        shir_local = self.shir_locals.get(local)
        ident = self.new_local(ty, None if shir_local is None else shir_local.name,
                               None if shir_local is None else shir_local.loc)
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
        self.block.statements.extend(statements)

    @property
    def alive(self) -> bool:
        """Whether the path that lowering is on goes on: its block has no terminator."""
        return self.block.terminator is None

    def new_block(self) -> _Block:
        """The next block: ids in the order that lowering makes the blocks (L5)."""
        block = _Block(BlockId(len(self.blocks)))
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
        self.placement.open_statement()
        arm(self, stmt)
        if self.alive:
            self.emit(self.placement.flush())
            self.emit(self.placement.close_scope(stmt.loc))
            if self.declared is not None:
                self.emit(self.placement.flag_declared(self.declared, stmt.loc))
        else:
            self.placement.leave_scope()
        self.declared = None

    def arm(self, block: _Block, body: ShirBlock) -> list[_Arrival]:
        """The statements of `body` in a scope of their own, from `block` on. The answer is
        the edge into the join when the path reaches the end of `body`."""
        self.block = block
        self.placement.open_scope()
        for stmt in body.statements:
            self.statement(stmt)
        if not self.alive:
            self.placement.leave_scope()
            return []
        self.emit(self.placement.flush())
        self.emit(self.placement.close_scope(body.loc))
        return [(self.block, self.placement.state())]

    def meet(self, join: _Block, arrivals: list[_Arrival], among: tuple[LocalId, ...],
             span: Span) -> None:
        """The paths `arrivals` meet at `join`, and lowering goes on from it."""
        for block, _ in arrivals:
            if block is not None:
                block.terminator = tm.Goto(join.id, span)
        self.block = join
        self.learn(self.placement.join([state for _, state in arrivals], among), span)

    def learn(self, locals_: tuple[LocalId, ...], span: Span,
              loop: Optional[ShirId] = None) -> None:
        """Record a local that needs a flag (rule 4), found at a join, and with `loop` that
        the header of the loop does not know its state. Such a local must be marked
        conditionally moved by `borrow` (rule 3). The walk goes on; its output is not used,
        and the body is lowered again with what it found."""
        for local in locals_:
            shir = self.shir_of.get(local)
            if shir is None:
                er.raise_internal_error(
                    "CE0000", span=span,
                    detail=f"two paths give the temporary _{local.index} two states")
            if not self.answers.ownership.is_conditionally_moved(shir):
                er.raise_internal_error(
                    "CE0000", span=span,
                    detail=f"two paths give _{local.index} two states, and borrow does not "
                           f"mark it conditionally moved")
            self.found_flags.add(shir)
            if loop is not None:
                self.found_unknown.add((loop, shir))

    def condition(self, expr: e.ShirExpr) -> tuple[p.Operand, Optional[LocalId]]:
        """The operand that a switch reads, and the local that holds it, if any. The
        temporaries of the condition die before the switch. A condition that is no local or
        constant goes into a local of its own: it is live before its first write, an exit
        inside the condition ends it, and the caller ends it at the top of each block that
        the switch goes to (L12)."""
        self.placement.open_statement()
        value = self.value(expr)
        local: Optional[LocalId] = None
        if isinstance(value, r.Use) and isinstance(value.operand, (p.Const, p.Copy)):
            operand = value.operand
        else:
            local = self.temporary(value, BuiltinType.BOOL, expr.loc)
            self.placement.detach(local)
            operand = p.Copy(p.Local(local))
        self.emit(self.placement.close_scope(expr.loc))
        return operand, local

    def targets(self, local: Optional[LocalId], blocks: tuple[_Block, ...], span: Span) -> None:
        """The storage of the condition local `local` ends at the top of each target block."""
        if local is not None:
            for block in blocks:
                block.statements.append(st.StorageDead(local, span))

    def if_(self, stmt: s.If) -> None:
        """`switchInt(c, [(0, else)], otherwise: then)`; with no `else`, the zero edge goes to
        the join, or to a block of its own when a condition local must end there (ir.md
        8.9). The blocks: `then`, `else`, then the join (L5)."""
        cond, local = self.condition(stmt.cond)
        then, other = self.new_block(), self.new_block()
        join = self.new_block() if stmt.otherwise is None and local is not None else None
        self.jump(tm.SwitchInt(cond, ((0, other.id),), then.id, stmt.loc))
        self.targets(local, (then, other), stmt.cond.loc)
        among, at_branch = self.placement.in_scope(), self.placement.state()
        arrivals = self.arm(then, stmt.then)
        if stmt.otherwise is None:
            zero: _Arrival = (None, at_branch) if join is None else (other, at_branch)
            self.meet(other if join is None else join, [*arrivals, zero], among, stmt.loc)
            return
        self.placement.restore(at_branch)
        arrivals += self.arm(other, stmt.otherwise)
        if arrivals:
            self.meet(self.new_block(), arrivals, among, stmt.loc)

    def loop(self, stmt: s.Loop) -> None:
        """A `while`: a header block that evaluates the condition and switches, the body,
        which goes back to the header, and the exit block (ir.md 8.9). When a condition
        local must end on the zero edge, that edge goes to a block of its own, which goes
        to the exit, because a `break` goes to the exit too."""
        source = stmt.source
        if not (isinstance(source, s.Cond)
                and isinstance(self.typeck.loop_kind_of(stmt.shir_id), While)):
            dispatch.stop(STAGE, stmt)
        header = self.new_block()
        self.jump(tm.Goto(header.id, stmt.loc))
        self.block = header
        among = self.placement.in_scope()
        for kept in among:
            if (stmt.shir_id, self.shir_of.get(kept)) in self.learned.unknown_at:
                self.placement.mark_unknown(kept)
        at_header = self.placement.state()
        cond, local = self.condition(source.cond)
        body = self.new_block()
        done = None if local is None else self.new_block()
        exit_ = self.new_block()
        self.jump(tm.SwitchInt(cond, ((0, (done or exit_).id),), body.id, stmt.loc))
        self.targets(local, (body,) if done is None else (body, done), source.cond.loc)
        after_cond = self.placement.state()
        frame = _LoopFrame(header.id, exit_.id, self.placement.depth())
        self.loops.append(frame)
        for block, state in self.arm(body, stmt.body):
            if block is not None:
                block.terminator = tm.Goto(header.id, stmt.loc)
            frame.continues.append(state)
        self.loops.pop()
        self.learn(at_header.changed_by(frame.continues, among), stmt.loc, stmt.shir_id)
        self.meet(exit_, [(done, after_cond), *((None, state) for state in frame.breaks)],
                  among, stmt.loc)

    def break_(self, stmt: s.Break) -> None:
        self.leave_round(stmt.loc, breaks=True)

    def continue_(self, stmt: s.Continue) -> None:
        self.leave_round(stmt.loc, breaks=False)

    def leave_round(self, span: Span, *, breaks: bool) -> None:
        """A `break` or a `continue`: the drops of each scope of the loop body that it
        leaves (rule 2), then the jump to the exit or to the header."""
        if not self.loops:
            er.raise_internal_error("CE0000", span=span,
                                    detail="a jump out of no loop reached SLIR")
        frame = self.loops[-1]
        self.emit(self.placement.exit_to(frame.depth, span))
        (frame.breaks if breaks else frame.continues).append(self.placement.state())
        self.jump(tm.Goto(frame.exit if breaks else frame.header, span))

    def assign(self, stmt: s.Assign) -> None:
        target = stmt.target
        if not isinstance(target, e.LocalRef):
            dispatch.stop(STAGE, target)
        if stmt.is_init:
            value = self.value(stmt.value)
            ident, ty = self.bind(target.local, stmt.loc)
            self.give_flag(ident)
            self.placement.declare(ident, self.owns(ty))
            self.emit([st.StorageLive(ident, stmt.loc)])
            self.write(p.Local(ident), value, stmt.loc)
            self.declared = ident
        else:
            self.rebind(stmt, target)

    def rebind(self, stmt: s.Assign, target: e.LocalRef) -> None:
        """A rebind: the new value first, then the drop of the old one, then the store, then
        the flag (rules 4 and 6). An old value that a move took drops nothing. A new value
        that may still read the old one (a live owning value, `and`, `or`) goes into a
        temporary first."""
        ident = self.local_of(target.local, target.loc)
        owning = self.owns(self.locals[ident.index].ty)
        if owning and self.placement.is_borrowed(ident):
            raise StageStop(STAGE, "borrowed rebind", stmt.loc)
        value = self.value(stmt.value)
        if isinstance(value, _ShortCircuit) or (self.placement.holds_live_value(ident) and not (
                isinstance(value, r.Use) and isinstance(value.operand, p.Const))):
            ty = self.type_of(stmt.value)
            temp = p.Local(self.temporary(value, ty, stmt.value.loc))
            value = r.Use(p.Move(temp) if self.owns(ty) else p.Copy(temp))
        self.emit(self.placement.drop_old_value(ident, stmt.loc))
        self.write(p.Local(ident), value, stmt.loc)
        self.emit(self.placement.flag_stored(ident, stmt.loc))

    def expr_stmt(self, stmt: s.ExprStmt) -> None:
        """A value that nobody reads still writes a destination local (L16)."""
        self.temporary(self.value(stmt.value), self.type_of(stmt.value), stmt.value.loc)

    def ret(self, stmt: s.Return) -> None:
        self.finish(self.value(stmt.value), stmt.loc)

    def finish(self, value: _Value, span: Span) -> None:
        """`_0` first, then the drops of the exit, then the `return` (L14)."""
        self.write(p.Local(RETURN_SLOT), value, span)
        self.emit(self.placement.exit_function(span))
        self.block.terminator = tm.Return(span)

    def write(self, place: p.Place, value: _Value, span: Span) -> None:
        """Store `value` into `place`. The store is the statement that consumes each `move`
        operand of the value, so it marks the local of each one moved (ir.md 8.10, rule 3)."""
        if isinstance(value, _ShortCircuit):
            self.short_circuit(place, value)
            return
        if isinstance(value, _CallValue):
            self.emit([st.Call(place, value.callee, value.args, span)])
        else:
            self.emit([st.Assign(place, value, span)])
        for moved in _moves(value):
            self.placement.move(_root(moved), span)

    def short_circuit(self, place: p.Place, value: _ShortCircuit) -> None:
        """`and` and `or` as two blocks (ir.md 8.9): the left operand goes into `place`, a
        switch on it skips the right block, and the right block writes `place` again. The
        temporaries of the right operand die in its block."""
        span = value.span
        self.write(place, r.Use(value.lhs), span)
        right, join = self.new_block(), self.new_block()
        skip, other = (join, right) if value.op == "and" else (right, join)
        self.jump(tm.SwitchInt(p.Copy(place), ((0, skip.id),), other.id, span))
        among, at_branch = self.placement.in_scope(), self.placement.state()
        self.block = right
        self.placement.open_statement()
        self.write(place, self.value(value.rhs), span)
        self.emit(self.placement.flush())
        self.emit(self.placement.close_scope(span))
        self.meet(join, [(None, at_branch), (self.block, self.placement.state())], among, span)

    def temporary(self, value: _Value, ty: Type, span: Span) -> LocalId:
        """A new temporary of the statement that holds `value`."""
        ident = self.new_local(ty, None, None)
        self.placement.temporary(ident, self.owns(ty))
        self.emit([st.StorageLive(ident, span)])
        self.write(p.Local(ident), value, span)
        return ident

    # -- values and operands -------------------------------------------------------------

    def value(self, expr: e.ShirExpr) -> _Value:
        arm = _VALUES.get(type(expr))
        if arm is None:
            dispatch.stop(STAGE, expr)
        return arm(self, expr)

    def operand(self, expr: e.ShirExpr) -> p.Operand:
        """The operand of `expr`, from its `Ownership` row and its shape (ir.md 7.9)."""
        row = self.answers.ownership.ownership_of(expr.shir_id)
        if row is Ownership.REJECT:
            raise StageStop(STAGE, "borrowed view", expr.loc)
        if isinstance(expr, e.Lit):
            return p.Const(_constant(expr, self.typeck))
        if isinstance(expr, e.LocalRef):
            place = p.Local(self.local_of(expr.local, expr.loc))
            return p.Move(place) if row is Ownership.MOVE else p.Copy(place)
        if row is Ownership.MOVE:
            er.raise_internal_error("CE0000", span=expr.loc,
                                    detail=f"a move row on a {type(expr).__name__}, which is no place")
        temp = self.temporary(self.value(expr), self.type_of(expr), expr.loc)
        return p.Move(p.Local(temp)) if row is Ownership.ADOPT else p.Copy(p.Local(temp))

    def leaf(self, expr: e.Lit | e.LocalRef) -> _Value:
        return r.Use(self.operand(expr))

    def binary(self, node: e.Binary) -> _Value:
        if node.op in ("and", "or"):
            return _ShortCircuit(node.op, self.operand(node.lhs), node.rhs, node.loc)
        return r.Binary(node.op, self.operand(node.lhs), self.operand(node.rhs))

    def unary(self, node: e.Unary) -> _Value:
        return r.Unary(node.op, self.operand(node.operand))

    def enum_init(self, node: e.EnumInit) -> _Value:
        """An enum variant as an aggregate of its instance type (L10)."""
        ty = self.typeck.instance_of(node.shir_id)
        if ty is None:
            er.raise_internal_error("CE0000", span=node.loc,
                                    detail="an enum construction with no type reached SLIR")
        return r.Aggregate(r.AggVariant(ty, node.variant),
                           tuple(self.operand(arg.value) for arg in node.payload))

    def call(self, node: e.Call) -> _Value:
        resolved = self.typeck.callee_of(node.shir_id)
        if resolved is None:
            er.raise_internal_error("CE0000", span=node.loc,
                                    detail="a call with no callee reached SLIR")
        callee = self.callee(node, resolved)
        return _CallValue(callee, tuple(self.argument(node, index, arg)
                                        for index, arg in enumerate(node.args)))

    def callee(self, node: e.Call, resolved: ResolvedCall) -> st.SlirCallee:
        """A free function by its symbol and its source name; an intrinsic with the display
        type of each argument (L7)."""
        callee = resolved.callee
        if isinstance(callee, Free):
            if resolved.unit is None:
                raise StageStop(STAGE, "library function", node.loc)
            name = self.decls.name_of(callee.decl).rpartition(".")[2]
            return st.Direct(function_symbol(name, resolved.unit), name)
        if isinstance(callee, Intrinsic):
            return st.Builtin(callee, tuple(self.display_type(node.shir_id, index, node.loc)
                                            for index in range(len(node.args))))
        raise StageStop(STAGE, type(callee).__name__, node.loc)

    def argument(self, node: e.Call, index: int,
                 arg: e.Arg) -> tuple[p.Operand, Convention]:
        """An argument with the convention of its typecheck row (L8)."""
        if arg.bloom:
            raise StageStop(STAGE, "bloom", arg.value.loc)
        convention = self.typeck.convention_of(node.shir_id, index)
        if convention is None:
            er.raise_internal_error("CE0000", span=arg.value.loc,
                                    detail="an argument with no convention reached SLIR")
        return self.operand(arg.value), convention

    def interp(self, node: e.Interp) -> _Value:
        """ONE call of the intrinsic `interp`: a `Guaranteed` operand for each part, with
        its display type; an empty text part gives none (L17)."""
        args: list[tuple[p.Operand, Convention]] = []
        types: list[Type] = []
        for index, part in enumerate(node.parts):
            if isinstance(part, str):
                if not part:
                    continue
                args.append((p.Const(_text(part)), Convention.GUARANTEED))
                types.append(BuiltinType.STRING)
            else:
                args.append((self.operand(part), Convention.GUARANTEED))
                types.append(self.display_type(node.shir_id, index, part.loc))
        return _CallValue(st.Builtin(Intrinsic("interp"), tuple(types)), tuple(args))

    def try_(self, node: e.Try) -> _Value:
        """`??` (ir.md 8.9): the operand into a temporary, a switch on its discriminant with
        no other value, an `Err` block that builds the `Err` into `_0`, leaves every scope
        and returns, and the `Ok` block, where the payload moves into the place that takes
        the value; each store moves the temporary. A `??` that calls a declared conversion
        stops (`slir, conversion`), and a `??` over an owning wrapper that the body only reads
        stops (`slir, borrowed try`)."""
        facts = self.typeck.try_facts_of(node.shir_id)
        choice = self.typeck.conversion_of(node.shir_id)
        if isinstance(choice, Conversion):
            raise StageStop(STAGE, "conversion", node.loc)
        if facts is None or choice is None:
            er.raise_internal_error("CE0000", span=node.loc,
                                    detail="a ?? with no facts reached SLIR")
        ty = self.type_of(node.inner)
        value = self.value(node.inner)
        if self.owns(ty) and isinstance(value, r.Use) and isinstance(value.operand, p.Copy):
            raise StageStop(STAGE, "borrowed try", node.loc)
        (ok_tag, ok), (err_tag, err) = _arms(ty, facts.success_tag, node.loc)
        temp = self.temporary(value, ty, node.inner.loc)
        tag = self.new_local(BuiltinType.I32, None, None)
        self.emit([st.Assign(p.Local(tag), r.Discriminant(p.Local(temp)), node.loc)])
        ok_block, err_block = self.new_block(), self.new_block()
        targets = tuple(sorted([(ok_tag, ok_block.id), (err_tag, err_block.id)]))
        self.jump(tm.SwitchInt(p.Copy(p.Local(tag)), targets, None, node.loc))
        at_switch = self.placement.state()
        self.block = err_block
        payload = p.Move(p.Field(p.Downcast(p.Local(temp), err), 0))
        self.finish(r.Aggregate(r.AggVariant(facts.returns, err), (payload,)), node.loc)
        self.placement.restore(at_switch)
        self.block = ok_block
        return r.Use(p.Move(p.Field(p.Downcast(p.Local(temp), ok), 0)))

    def display_type(self, node: ShirId, part: int, span: Span) -> Type:
        ty = self.typeck.display_type_of(node, part)
        if ty is None:
            er.raise_internal_error("CE0000", span=span,
                                    detail="a value with no display type reached SLIR")
        return ty


_STATEMENTS: dict[type, Callable[..., None]] = {
    s.Assign: _Lowering.assign,
    s.ExprStmt: _Lowering.expr_stmt,
    s.Return: _Lowering.ret,
    s.If: _Lowering.if_,
    s.Loop: _Lowering.loop,
    s.Break: _Lowering.break_,
    s.Continue: _Lowering.continue_,
}

_VALUES: dict[type, Callable[..., _Value]] = {
    e.Lit: _Lowering.leaf,
    e.LocalRef: _Lowering.leaf,
    e.Binary: _Lowering.binary,
    e.Unary: _Lowering.unary,
    e.EnumInit: _Lowering.enum_init,
    e.Call: _Lowering.call,
    e.Interp: _Lowering.interp,
    e.Try: _Lowering.try_,
}


def _moves(value: r.Rvalue | _CallValue) -> list[p.Place]:
    """The place of each `move` operand of a value."""
    parts = (tuple(operand for operand, _ in value.args) if isinstance(value, _CallValue)
             else r.operands_of(value))
    return [part.place for part in parts if isinstance(part, p.Move)]


def _root(place: p.Place) -> LocalId:
    """The local that a move takes: a local, or the payload of a variant of a local
    (`move ((_3 as Ok).0)`), which moves the whole local."""
    if isinstance(place, p.Field) and isinstance(place.base, p.Downcast):
        place = place.base.base
    if not isinstance(place, p.Local):
        er.raise_internal_error("CE0000", detail=f"SLIR lowering moves out of {place}")
    return place.id


def _arms(ty: Type, success: int, span: Span) -> tuple[tuple[int, str], tuple[int, str]]:
    """The tag and the name of the success variant and of the error variant of a `Result`."""
    if not isinstance(ty, EnumType) or len(ty.variants) != 2:
        er.raise_internal_error("CE0000", span=span, detail="a ?? over no Result reached SLIR")
    return (success, ty.variants[success].name), (1 - success, ty.variants[1 - success].name)


def _text(value: str) -> k.Str:
    return k.Str(value.encode("utf-8", "surrogatepass"))


def _constant(lit: e.Lit, typeck: TypeckResults) -> k.Constant:
    """The SLIR constant of a literal, at the type that typecheck gave it."""
    value = lit.value
    if isinstance(value, e.BoolConst):
        return k.Bool(value.value)
    if isinstance(value, e.BlankConst):
        return k.Blank()
    if isinstance(value, e.StrConst):
        return _text(value.value)
    ty = typeck.type_of(lit.shir_id)
    if ty is None:
        er.raise_internal_error("CE0000", span=lit.loc, detail="a literal with no type reached SLIR")
    if isinstance(value, e.IntConst):
        return k.Int(value.value, ty)
    return k.Float(value.value, ty)


def _symbol(decl: object, home: Optional[str], body: ShirBody) -> str:
    """The symbol of the function in its module (`function_symbol`). A body that is not a
    free function stops with the name of its kind."""
    if not isinstance(decl, FuncDef) or len(body.params) > len(decl.params):
        raise StageStop(STAGE, _kind(decl, body), body.block.loc)
    return function_symbol(decl.name, home)


def _kind(decl: object, body: ShirBody) -> str:
    """The kind of a body that is not a free function. A perk method is a `FuncDef` whose
    body has a receiver before its declared parameters."""
    if isinstance(decl, FuncDef):
        return "perk method"
    if isinstance(decl, GenericFuncDef):
        return "generic instance"
    if isinstance(decl, ExtendDef):
        return "conversion" if decl.is_conversion else "method"
    er.raise_internal_error("CE0000", span=body.block.loc,
                            detail=f"a body of {type(decl).__name__} reached SLIR")


def _return_type(decl: object, tables: SymbolTables, body: ShirBody) -> Type:
    """The type of the return slot: the `Result` of a channel, else the declared type."""
    ret = getattr(decl, "ret", None)
    if ret is None:
        er.raise_internal_error("CE0000", span=body.block.loc,
                                detail="a function with no return type reached SLIR")
    if has_channel(decl):
        result = channel_result(tables.structs, tables.enums, ret, getattr(decl, "err_type", None))
        if result is None:
            er.raise_internal_error("CE0000", span=body.block.loc,
                                    detail="a function with a channel has no Result type")
        return result
    return resolve_unknown_type(ret, tables.structs.by_name, tables.enums.by_name)
