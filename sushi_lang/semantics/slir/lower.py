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

The lowering handles the parameters, a `let`, a rebind, a call statement, a `return`, an
`if`, a `while` with its `break` and `continue`, over literals, names of constants, locals,
the operators, `and` and `or` as control flow, the construction of an enum variant, a call
of a free function, `print`, `println`, an interpolation and a `??`. The operand of a value
is the answer of `borrow` and the shape of the value alone (ir.md 7.9): a `move` row gives
`Move`; an `adopt` row gives `Copy` of a place, a constant for a literal or a name of a
constant, and `Move` of the temporary of a new value. A value with no row is read with
`Copy`. A literal and a name of a constant take the value that typecheck wrote for them
(ir.md 8.5). A new value goes into the place that takes it (the local of a `let`, `_0`),
else into a temporary of its statement. The store that consumes a `move` operand marks its
local moved, not the place where the operand is built. Drop placement (`drops.py`) places
every drop, every flag write and every `StorageDead`, and it joins the state of the locals
where paths meet.

A local whose state differs between the paths of a join gets a drop flag right after it
(ir.md 8.10, rule 4), and so does a local whose state at a loop header is not the state
that a back edge brings. The walk finds such a local only at the join, after the point
that must make the flag. So a walk records what it finds (`_Learned`) and goes on, and the
body is lowered again with what the walks found, until a walk finds nothing new.

The construct of a stop is the SHIR class of the node, or one of these names: `generic
instance`, `conversion`, `perk method` and `method` for the body of a monomorphized generic
instance, of a conversion, of a perk method and of an extension method; `borrowed view` for
a `reject` row, `borrowed rebind` for a rebind of a borrowed parameter that owns, `borrowed
try` for a `??` over an owning wrapper that the body only reads, `conversion` also for a
`??` that calls a declared conversion, `library function` for a callee whose declaring unit
the typecheck row does not name, `bloom` for a bloom argument, `aggregate constant` for a
name of a struct, an enum or an array constant, and `ConstRef out of range` for a name of a
constant whose value its type cannot hold. The symbol, the unit and the return type of the
function are facts of its declaration (ir.md 6.4). The unit is the declaring unit; a
function that has none (a lifted lambda, the instance of a template of a binary library)
goes into the module of the unit that holds its body.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.const_eval import AggregateConstant
from sushi_lang.semantics.drop_set import drop_type_names
from sushi_lang.semantics.integer_width import fits_integer_type
from sushi_lang.semantics.method_effects import RECEIVER_READ_FAMILIES
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import Initializer, ShirBody, ShirLocal, TemplateCheck
from sushi_lang.semantics.shir.callee import Builtin, Free, Intrinsic
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.typeck import ResolvedCall, TypeckResults
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import lower_control as ctl
from sushi_lang.semantics.slir.lower_control import LoopFrame
from sushi_lang.semantics.slir.lower_signature import return_type_of, symbol_of
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
from sushi_lang.semantics.typesys import BuiltinType, Type, owns_resource
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
        """Lower the SHIR body of `decl` and record the outcome. An initializer body has no
        outcome.

        A stop ends the lowering of the body: the stage records it with the file of the
        body. The verifier checks a function that the lowering finished (ir.md 8.13), and
        the program keeps it for the emitter.
        """
        if isinstance(body.origin, Initializer):
            return
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


def lower_function(decl: object, answers: BodyTables, *, decls: DeclNames, name: str,
                   unit: str, tables: SymbolTables, drops: frozenset[str]) -> SlirFunction:
    """The `SlirFunction` of one body that `borrow` finished, from the body and its tables.

    Raises `StageStop` at the first construct that the lowering does not handle yet.
    """
    body = answers.body
    if isinstance(body.origin, TemplateCheck):
        er.raise_internal_error("CE0000", detail=f"the template check '{name}' reached SLIR")
    home = declaring_unit(decl, unit)
    symbol = symbol_of(decl, home, body)
    ret = return_type_of(decl, tables, body)
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
    loops: list[LoopFrame] = field(default_factory=list)
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
            ctl.short_circuit(self, place, value)
            return
        if isinstance(value, _CallValue):
            self.emit([st.Call(place, value.callee, value.args, span)])
        else:
            self.emit([st.Assign(place, value, span)])
        for moved in _moves(value):
            self.placement.move(_root(moved), span)

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
        """The operand of `expr`, from its `Ownership` row and its shape (ir.md 7.9). A
        literal and a name of a constant are a constant with any other row: an `adopt` of a
        string constant takes a literal, which owns no heap."""
        row = self.answers.ownership.ownership_of(expr.shir_id)
        if row is Ownership.REJECT:
            raise StageStop(STAGE, "borrowed view", expr.loc)
        if isinstance(expr, e.LocalRef):
            place = p.Local(self.local_of(expr.local, expr.loc))
            return p.Move(place) if row is Ownership.MOVE else p.Copy(place)
        if row is Ownership.MOVE:
            er.raise_internal_error("CE0000", span=expr.loc,
                                    detail=f"a move row on a {type(expr).__name__}, which is no place")
        if isinstance(expr, e.Lit):
            return p.Const(k.of_literal(expr, self.typeck))
        if isinstance(expr, e.ConstRef):
            return p.Const(self.constant(expr))
        temp = self.temporary(self.value(expr), self.type_of(expr), expr.loc)
        return p.Move(p.Local(temp)) if row is Ownership.ADOPT else p.Copy(p.Local(temp))

    def constant(self, ref: e.ConstRef) -> k.Constant:
        """The value of the constant that `ref` names, from its typecheck row. A struct, an
        enum or an array value has no constant kind yet (A4, A5, A6). An integer that its
        type cannot hold stops (`ConstRef out of range`): the constant evaluator does not
        wrap a bit pattern, and it gives a value to an initializer with a range error."""
        value = self.typeck.value_of(ref.shir_id)
        if isinstance(value, AggregateConstant):
            raise StageStop(STAGE, "aggregate constant", ref.loc)
        if value is None:
            er.raise_internal_error("CE0000", span=ref.loc,
                                    detail="a constant with no value reached SLIR")
        constant = k.of_scalar(value)
        if isinstance(constant, k.Int) and not fits_integer_type(constant.value, constant.ty):
            dispatch.stop(STAGE, ref, state="out of range")
        return constant

    def leaf(self, expr: e.Lit | e.LocalRef | e.ConstRef) -> _Value:
        return r.Use(self.operand(expr))

    def binary(self, node: e.Binary) -> _Value:
        if node.op in ("and", "or"):
            return _ShortCircuit(node.op, self.operand(node.lhs), node.rhs, node.loc)
        return r.Binary(node.op, self.operand(node.lhs), self.operand(node.rhs))

    def unary(self, node: e.Unary) -> _Value:
        return r.Unary(node.op, self.operand(node.operand))

    def cast(self, node: e.Cast) -> _Value:
        """A numeric cast, or a cast to the type of its operand. A cast that calls a
        conversion stops in `typecheck`, so no row here calls one."""
        return r.Cast(self.operand(node.operand), self.type_of(node))

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
        receiver = ((self.operand(resolved.callee.recv),
                     receiver_convention(resolved.callee.method.family, node.loc)),) \
            if isinstance(resolved.callee, Builtin) else ()
        return _CallValue(callee, receiver + tuple(self.argument(node, index, arg)
                                                   for index, arg in enumerate(node.args)))

    def callee(self, node: e.Call, resolved: ResolvedCall) -> st.SlirCallee:
        """A free function by its symbol and its source name; an intrinsic with the display
        type of each argument (L7); a built-in method by its family and its name, with no
        types: the receiver is the first operand of the call (`receiver_convention`)."""
        callee = resolved.callee
        if isinstance(callee, Free):
            if resolved.unit is None:
                raise StageStop(STAGE, "library function", node.loc)
            name = self.decls.name_of(callee.decl).rpartition(".")[2]
            return st.Direct(function_symbol(name, resolved.unit), name)
        if isinstance(callee, Intrinsic):
            return st.Builtin(callee, tuple(self.display_type(node.shir_id, index, node.loc)
                                            for index in range(len(node.args))))
        if isinstance(callee, Builtin):
            return st.Builtin(callee.method, ())
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
                args.append((p.Const(k.text(part)), Convention.GUARANTEED))
                types.append(BuiltinType.STRING)
            else:
                args.append((self.operand(part), Convention.GUARANTEED))
                types.append(self.display_type(node.shir_id, index, part.loc))
        return _CallValue(st.Builtin(Intrinsic("interp"), tuple(types)), tuple(args))

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
    s.If: ctl.if_,
    s.Loop: ctl.loop,
    s.Break: ctl.break_,
    s.Continue: ctl.continue_,
}

_VALUES: dict[type, Callable[..., _Value]] = {
    e.Lit: _Lowering.leaf,
    e.LocalRef: _Lowering.leaf,
    e.ConstRef: _Lowering.leaf,
    e.Binary: _Lowering.binary,
    e.Unary: _Lowering.unary,
    e.Cast: _Lowering.cast,
    e.EnumInit: _Lowering.enum_init,
    e.Call: _Lowering.call,
    e.Interp: _Lowering.interp,
    e.Try: ctl.try_,
}


def receiver_convention(family: str, span: Span) -> Convention:
    """The convention of the receiver of a built-in method of `family`: a borrow
    (`Guaranteed`) for a family of `RECEIVER_READ_FAMILIES`. Any other family can change or
    consume its receiver, and it stops in `typecheck` or `borrow` until its group gives it
    a row, so its call here is an internal error."""
    if family not in RECEIVER_READ_FAMILIES:
        er.raise_internal_error(
            "CE0000", span=span,
            detail=f"a call of a method of the family '{family}' reached SLIR, and no "
                   f"receiver convention is written for that family")
    return Convention.GUARANTEED


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
