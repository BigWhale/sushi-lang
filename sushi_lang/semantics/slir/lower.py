"""SHIR to SLIR: the SLIR stage and the lowering of one body (docs/design/ir.md 8.2, 8.9).

`SlirStage` is the stage of one unit, after `borrow`. The borrow pass gives it each body
that it finished with no error, with the tables that typecheck read the body in; a
template check never comes (ir.md 4.3). `lower_function` makes the `SlirFunction` of the
body from the body and its side tables. A construct that the lowering does not handle yet
stops the body with the stop code CE0155 (`slir, <construct>`): the stage records the stop
(`Stops.slir_stopped`) and the construct (`SlirStopped`, for the dump), and the analysis
goes on with the next body. A body that the lowering finished meets the next stage, which
is not built yet (`Stops.finished`).

The lowering handles what `fn main() i32: return 0` needs: the return slot `_0`, the entry
block, and a `return` of a literal, as `_0 = const <value>` and the `return` terminator.
The construct of a stop is the SHIR class of the node (`ShirParam` for a parameter), with
three more: `Initializer` for the body of a constant, `method` for the body of an
extension method, and `channel` for a body that answers a `Result`. The symbol, the unit
and the return type of the function are facts of its declaration, which the lowering reads
after the body (ir.md 6.4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.semantics.ast import FuncDef
from sushi_lang.semantics.channel import has_channel
from sushi_lang.semantics.externs_manifest import USER_MAIN_SYMBOL
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import Initializer, ShirBody, TemplateCheck
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import (
    STAGE, SlirBlock, SlirFunction, SlirLocal, SlirProgram, SlirStopped,
)
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.unit_symbols import declaring_unit, emitted_symbol

if TYPE_CHECKING:
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
            function = lower_function(decl, self.shir.tables_of(body), name=name,
                                      unit=self.unit, tables=tables)
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


def lower_function(decl: object, answers: BodyTables, *, name: str, unit: str,
                   tables: SymbolTables) -> SlirFunction:
    """The `SlirFunction` of one body that `borrow` finished, from the body and its tables.

    Raises `StageStop` at the first construct that the lowering does not handle yet.
    """
    body = answers.body
    if isinstance(body.origin, TemplateCheck):
        er.raise_internal_error("CE0000", detail=f"the template check '{name}' reached SLIR")
    if isinstance(body.origin, Initializer):
        raise StageStop(STAGE, "Initializer", body.block.loc)
    if body.params:
        raise StageStop(STAGE, "ShirParam", body.params[0].loc)
    entry = _Block(BlockId(0))
    for stmt in body.block.statements:
        _lower_statement(entry, stmt, answers.typeck)
    block = entry.closed()
    symbol = _symbol(decl, unit, body)
    slot = SlirLocal(RETURN_SLOT, _return_type(decl, tables, body), None, None)
    return SlirFunction(symbol, name, unit, (), (slot,), (block,), block.id)


def _lower_statement(block: _Block, stmt: s.ShirStmt, typeck: TypeckResults) -> None:
    if block.terminator is not None:
        er.raise_internal_error("CE0000", span=stmt.loc,
                                detail="a SHIR statement after the end of its block")
    if not isinstance(stmt, s.Return):
        dispatch.stop(STAGE, stmt)
    value = stmt.value
    if not isinstance(value, e.Lit):
        dispatch.stop(STAGE, value)
    rvalue = r.Use(p.Const(_constant(value, typeck)))
    block.statements.append(st.Assign(p.Local(RETURN_SLOT), rvalue, stmt.loc))
    block.terminator = tm.Return(stmt.loc)


def _constant(lit: e.Lit, typeck: TypeckResults) -> k.Constant:
    """The SLIR constant of a literal, at the type that typecheck gave it."""
    value = lit.value
    if isinstance(value, e.BoolConst):
        return k.Bool(value.value)
    if isinstance(value, e.BlankConst):
        return k.Blank()
    if isinstance(value, e.StrConst):
        return k.Str(value.value.encode("utf-8", "surrogatepass"))
    ty = typeck.type_of(lit.shir_id)
    if ty is None:
        er.raise_internal_error("CE0000", span=lit.loc, detail="a literal with no type reached SLIR")
    if isinstance(value, e.IntConst):
        return k.Int(value.value, ty)
    return k.Float(value.value, ty)


def _symbol(decl: object, unit: str, body: ShirBody) -> str:
    """The symbol of the function in its module: `user_main` for the body of `main`."""
    if not isinstance(decl, FuncDef):
        raise StageStop(STAGE, "method", body.block.loc)
    if decl.name == "main":
        return USER_MAIN_SYMBOL
    return emitted_symbol(decl.name, declaring_unit(decl, unit))


def _return_type(decl: object, tables: SymbolTables, body: ShirBody) -> Type:
    """The type of the return slot: the declared return type of a bare function."""
    if has_channel(decl):
        raise StageStop(STAGE, "channel", body.block.loc)
    ret = getattr(decl, "ret", None)
    if ret is None:
        er.raise_internal_error("CE0000", span=body.block.loc,
                                detail="a function with no return type reached SLIR")
    return resolve_unknown_type(ret, tables.structs.by_name, tables.enums.by_name)
