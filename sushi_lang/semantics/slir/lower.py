"""SHIR to SLIR: the SLIR stage and the lowering of one body (docs/design/ir.md 8.2, 8.9, 8.10).

`SlirStage` is the stage of one unit, after `borrow`. The borrow pass gives it each body
that it finished with no error, with the tables that typecheck read the body in; a
template check never comes (ir.md 4.3). `lower_function` makes the `SlirFunction` of the
body from the body and its side tables. A construct that the lowering does not handle yet
stops the body with the stop code CE0155 (`slir, <construct>`): the stage records the stop
(`Stops.slir_stopped`) and the construct (`SlirStopped`, for the dump), and the analysis
goes on with the next body. A body that the lowering finished meets the next stage, which
is not built yet (`Stops.finished`).

The lowering handles the straight line: the parameters, a `let`, a rebind, a call
statement and a `return`, over literals, locals, the operators other than `and` and `or`,
the construction of an enum variant, a call of a free function, `print`, `println` and an
interpolation. The operand of a value is the answer of `borrow` and the shape of the value
alone (ir.md 7.9): a `move` row gives `Move`; an `adopt` row gives `Copy` of a place, a
constant for a literal, and `Move` of the temporary of a new value. A value with no row is
read with `Copy`. A new value goes into the place that takes it (the local of a `let`,
`_0`), else into a temporary of its statement. Drop placement (`drops.py`) places every
drop and every `StorageDead`.

The construct of a stop is the SHIR class of the node, or one of these names: `Initializer`
for the body of a constant, `method` for the body of an extension method, `borrowed view`
for a `reject` row, `borrowed rebind` for a rebind of a borrowed parameter that owns,
`short circuit` for `and` and `or`, `library function` for a callee whose declaring unit
the typecheck row does not name, and `bloom` for a bloom argument. The symbol, the unit and
the return type of the function are facts of its declaration (ir.md 6.4). The unit is the
declaring unit; a function that has none (a lifted lambda, the instance of a template of a
binary library) goes into the module of the unit that holds its body.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import FuncDef
from sushi_lang.semantics.channel import channel_result, has_channel
from sushi_lang.semantics.drop_set import drop_type_names
from sushi_lang.semantics.externs_manifest import USER_MAIN_SYMBOL
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import Initializer, ShirBody, ShirLocal, TemplateCheck
from sushi_lang.semantics.shir.callee import Free, Intrinsic
from sushi_lang.semantics.shir.ids import LocalId as ShirLocalId
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.typeck import ResolvedCall, TypeckResults
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.drops import DropPlacement
from sushi_lang.semantics.slir.function import (
    STAGE, SlirBlock, SlirFunction, SlirLocal, SlirProgram, SlirStopped,
)
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.typesys import BuiltinType, Type, owns_resource
from sushi_lang.semantics.unit_symbols import declaring_unit, emitted_symbol

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

    def lower_body(self, decl: object, body: ShirBody, *, tables: SymbolTables,
                   filename: Optional[str]) -> None:
        """Lower the SHIR body of `decl` and record the outcome.

        A stop ends the lowering of the body: the stage records it with the file of the
        body. A body that the lowering finished meets the next stage.
        """
        name = self.shir.decls.name_of(body.fn_id)
        try:
            function = lower_function(decl, self.shir.tables_of(body), decls=self.shir.decls,
                                      name=name, unit=self.unit, tables=tables)
        except StageStop as stop:
            stop.filename = filename
            self.stops.slir_stopped(stop)
            self.slir.record(body.fn_id, SlirStopped(name, str(stop.params["construct"])))
            return
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


_Value: TypeAlias = r.Rvalue | _CallValue


def lower_function(decl: object, answers: BodyTables, *, decls: DeclNames, name: str,
                   unit: str, tables: SymbolTables) -> SlirFunction:
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
    lowering = _Lowering(answers, decls, drop_type_names(tables.perk_impls),
                         {local.id: local for local in body.locals})
    lowering.new_local(ret, None, None)
    params = lowering.parameters()
    lowering.lower_block(ret)
    block = lowering.block.closed()
    return SlirFunction(symbol, name, unit if home is None else home, params,
                        tuple(lowering.locals), (block,), block.id)


@dataclass(slots=True)
class _Lowering:
    """The lowering of one body: its SLIR locals, its block and its drop placement."""
    answers: BodyTables
    decls: DeclNames
    drops: frozenset[str]
    shir_locals: dict[ShirLocalId, ShirLocal]
    locals: list[SlirLocal] = field(default_factory=list)
    ids: dict[ShirLocalId, LocalId] = field(default_factory=dict)
    block: _Block = field(default_factory=lambda: _Block(BlockId(0)))
    placement: DropPlacement = field(default_factory=DropPlacement)

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
        return ident, ty

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
        return tuple(params)

    # -- statements ----------------------------------------------------------------------

    def emit(self, statements: list[st.SlirStatement]) -> None:
        self.block.statements.extend(statements)

    def lower_block(self, ret: Type) -> None:
        """The body block, then the end of a bare `~` body that reaches it."""
        block = self.answers.body.block
        self.placement.open_scope()
        for stmt in block.statements:
            self.statement(stmt)
        if self.block.terminator is None:
            if ret != BuiltinType.BLANK:
                er.raise_internal_error("CE0000", span=block.loc,
                                        detail="a body that answers a value reaches its end")
            self.finish(r.Use(p.Const(k.Blank())), block.loc)

    def statement(self, stmt: s.ShirStmt) -> None:
        """One statement, in a scope of its own that holds its temporaries (L12)."""
        if self.block.terminator is not None:
            er.raise_internal_error("CE0000", span=stmt.loc,
                                    detail="a SHIR statement after the end of its block")
        arm = _STATEMENTS.get(type(stmt))
        if arm is None:
            dispatch.stop(STAGE, stmt)
        self.placement.open_statement()
        arm(self, stmt)
        if self.block.terminator is None:
            self.emit(self.placement.close_scope(stmt.loc))
        else:
            self.placement.leave_scope()

    def assign(self, stmt: s.Assign) -> None:
        target = stmt.target
        if not isinstance(target, e.LocalRef):
            dispatch.stop(STAGE, target)
        if stmt.is_init:
            value = self.value(stmt.value)
            ident, ty = self.bind(target.local, stmt.loc)
            self.placement.declare(ident, self.owns(ty))
            self.emit([st.StorageLive(ident, stmt.loc)])
            self.write(p.Local(ident), value, stmt.loc)
        else:
            self.rebind(stmt, target)

    def rebind(self, stmt: s.Assign, target: e.LocalRef) -> None:
        """A rebind: the new value first, then the drop of the old one, then the store
        (rule 6). An old value that a move took drops nothing."""
        ident = self.local_of(target.local, target.loc)
        owning = self.owns(self.locals[ident.index].ty)
        if owning and self.placement.is_borrowed(ident):
            raise StageStop(STAGE, "borrowed rebind", stmt.loc)
        if not self.placement.holds_live_value(ident):
            value = self.value(stmt.value)
            self.emit(self.placement.drop_old_value(ident, stmt.loc))
            self.write(p.Local(ident), value, stmt.loc)
            return
        value = self.value(stmt.value)
        if not (isinstance(value, r.Use) and isinstance(value.operand, p.Const)):
            temp = self.temporary(value, self.type_of(stmt.value), stmt.value.loc)
            self.placement.move(temp, stmt.value.loc)
            value = r.Use(p.Move(p.Local(temp)))
        self.emit(self.placement.drop_old_value(ident, stmt.loc))
        self.write(p.Local(ident), value, stmt.loc)

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
        if isinstance(value, _CallValue):
            self.emit([st.Call(place, value.callee, value.args, span)])
        else:
            self.emit([st.Assign(place, value, span)])

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
            if row is Ownership.MOVE:
                self.placement.move(place.id, expr.loc)
                return p.Move(place)
            return p.Copy(place)
        if row is Ownership.MOVE:
            er.raise_internal_error("CE0000", span=expr.loc,
                                    detail=f"a move row on a {type(expr).__name__}, which is no place")
        temp = self.temporary(self.value(expr), self.type_of(expr), expr.loc)
        if row is Ownership.ADOPT:
            self.placement.move(temp, expr.loc)
            return p.Move(p.Local(temp))
        return p.Copy(p.Local(temp))

    def leaf(self, expr: e.Lit | e.LocalRef) -> _Value:
        return r.Use(self.operand(expr))

    def binary(self, node: e.Binary) -> _Value:
        if node.op in ("and", "or"):
            raise StageStop(STAGE, "short circuit", node.loc)
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
            return st.Direct(emitted_symbol(name, resolved.unit), name)
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
}

_VALUES: dict[type, Callable[..., _Value]] = {
    e.Lit: _Lowering.leaf,
    e.LocalRef: _Lowering.leaf,
    e.Binary: _Lowering.binary,
    e.Unary: _Lowering.unary,
    e.EnumInit: _Lowering.enum_init,
    e.Call: _Lowering.call,
    e.Interp: _Lowering.interp,
}


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
    """The symbol of the function in its module: `user_main` for the body of `main`."""
    if not isinstance(decl, FuncDef):
        raise StageStop(STAGE, "method", body.block.loc)
    if decl.name == "main":
        return USER_MAIN_SYMBOL
    return emitted_symbol(decl.name, home)


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
