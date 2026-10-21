"""The SLIR verifier (docs/design/ir.md 8.13).

The SLIR stage gives each function that the lowering finished to `SlirVerifier.verify`,
before the function meets the next stage, on every compile. `verify_function` checks one
function, and a failed check is the internal error CE0158: one diagnostic, at the span of
the statement, that names the function and the rule.

The structure comes first, statement by statement: a span on each statement and terminator,
one terminator for each block, blocks that exist, a type for each local and for each
projection of a place, a `Move` out of no `Global`, a `Drop` of an owning type, a `bool`
flag for a `DropIfSet`, no `and` or `or` in a `Binary`, the conventions of a `Direct`
callee, no copy of an owning value other than a `string` into an `Owned` argument, and a
registered `RExxxx` for a `Trap`. The type of a place also gives its root local, so a
`Move` of a projection is a `Move` of its root.

Then a dataflow over the CFG gives each local, at each point, the set of states that the
paths to that point can give it: uninit, live, moved or dropped, with the value of its drop
flag for a local that a `DropIfSet` reads. A join takes the union. A read or a move of a
local that a path moved is refused, and so is a read or a move of a local that holds no
value on a path: before its first write, or after its `StorageLive` or its `StorageDead`.
The `return` needs a value in `_0` on each path. An owning local must be moved or dropped
exactly one time on each path from its write to the end of its storage, to a write over
it, and to the `return`: a `DropIfSet` drops on the paths where its flag is true, and the
paths where the flag is false must have moved the local. An `Owned` parameter is an owning
local; a borrowed parameter never drops and never moves, and an `Inout` one holds a value
again at the `return`. A `Trap` ends the program, so it needs no drop.

The verifier refuses a shape that no lowering makes yet, and that it does not check, with
the internal error CE0000: a `BeginBorrow` or an `EndBorrow`, a projection of a `Global` (a
`Global` has no type in SLIR), and a `Drop` or a `DropIfSet` of a place that is no local.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Callable, NoReturn, Optional, TypeAlias, get_args

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors.registry import REGISTRY, Category
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import Convention, declared_modes
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import SlirBlock, SlirFunction
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.slir.printer import show
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, PointerType, ReferenceType,
    StructType, Type, UnknownType, owns_resource,
)
from sushi_lang.semantics.unit_symbols import emitted_symbol

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.functions import FunctionTable

TYPED_LOCALS = "typed locals"
TYPED_PLACES = "typed places"
USE_AFTER_MOVE = "no use after a move"
IN_STORAGE = "no use of a local outside its storage"
RETURN_SLOT = "a written return slot"
GLOBAL_MOVE = "no move out of a global"
ONE_TERMINATOR = "one terminator per block"
KNOWN_BLOCKS = "known blocks"
OWNING_DROP = "drop of an owning type"
BOOL_FLAG = "bool drop flag"
DROP_ONCE = "one drop or move on every path"
CONVENTIONS = "declared conventions"
OWNED_COPY = "no copy of an owning value into an owned argument"
SHORT_CIRCUIT = "no short circuit in a binary"
TRAP = "registered trap"
SPANS = "spans"

_TERMINATORS = get_args(tm.SlirTerminator)
_PRINTF = re.compile(r"%[-+ #0-9.]*[a-zA-Z%]")

# The state of a local on one path, and the value of its drop flag there.
_UNINIT, _LIVE, _MOVED, _DROPPED = "uninit", "live", "moved", "dropped"
_NO_FLAG, _UNSET, _TRUE, _FALSE, _UNKNOWN = "none", "unset", "true", "false", "unknown"
_Config: TypeAlias = tuple[str, str]
_State: TypeAlias = tuple[frozenset[_Config], ...]


@dataclass(frozen=True, slots=True)
class DeclaredParams:
    """The convention of each declared parameter of a direct callee. The tail of a `var_arg`
    extern declares no parameter, so the rule reads the declared prefix alone."""
    conventions: tuple[Convention, ...]
    var_arg: bool = False


#: The declared parameters of a `Direct` callee, or None when the build declares none.
DeclaredLookup: TypeAlias = Callable[[st.Direct], Optional[DeclaredParams]]


@dataclass(slots=True)
class SlirVerifier:
    """The pipeline entry of the verifier: the SLIR stage of a unit gives it each function
    that the lowering finished. The declared parameters come from the function table, at
    each call, so a generic instance that a later body declares is found too."""

    def verify(self, function: SlirFunction, *, funcs: FunctionTable, drops: frozenset[str],
               filename: Optional[str]) -> None:
        verify_function(function, drops=drops, declared=partial(declared_params, funcs),
                        filename=filename)


def declared_params(funcs: FunctionTable, callee: st.Direct) -> Optional[DeclaredParams]:
    """The declared parameters of the function that `callee` names: the function of its
    name in the unit whose symbol is the symbol of the callee."""
    for unit, own in funcs.by_unit.items():
        sig = own.get(callee.name)
        if sig is not None and emitted_symbol(callee.name, unit) == callee.symbol:
            return DeclaredParams(tuple(mode.convention for mode in declared_modes(sig.params)))
    return None


def verify_function(function: SlirFunction, *, drops: frozenset[str],
                    declared: DeclaredLookup, filename: Optional[str] = None) -> None:
    """CE0158 at the first rule of ir.md 8.13 that `function` breaks."""
    check = _Check(function, drops, declared, filename)
    check.structure()
    _Flow(check).run()


@dataclass(frozen=True, slots=True)
class _Payload:
    """What a `Downcast` place holds: the payload of one variant, read through a `Field`."""
    types: tuple[Type, ...]


@dataclass(frozen=True, slots=True)
class _Typed:
    """The root of a place, its type (None for a `Global`, whose type SLIR does not hold),
    and the operand of each `Index` in it."""
    root: p.Local | p.Global
    ty: Optional[Type | _Payload]
    indexes: tuple[p.Operand, ...] = ()


@dataclass(slots=True)
class _Check:
    """The structure of one function, and the facts that the dataflow reads."""
    function: SlirFunction
    drops: frozenset[str]
    declared: DeclaredLookup
    filename: Optional[str]
    blocks: dict[BlockId, SlirBlock] = field(default_factory=dict)
    conventions: dict[LocalId, Convention] = field(default_factory=dict)
    owning: set[LocalId] = field(default_factory=set)
    guards: dict[LocalId, LocalId] = field(default_factory=dict)

    def fail(self, rule: str, detail: str, span: Optional[Span]) -> NoReturn:
        er.raise_internal_error(er.ERR.CE0158.code, span=span, filename=self.filename,
                                function=self.function.name, rule=rule, detail=detail)

    def not_checked(self, what: str, span: Optional[Span]) -> NoReturn:
        """A shape that no lowering makes yet, and that the verifier does not check."""
        er.raise_internal_error("CE0000", span=span, filename=self.filename,
                                detail=f"{what} is not checked yet by the SLIR verifier, "
                                       f"because no lowering makes one")

    def has_local(self, local: LocalId) -> bool:
        return 0 <= local.index < len(self.function.locals)

    def type_of(self, local: LocalId) -> Type:
        return self.function.locals[local.index].ty

    # -- structure -----------------------------------------------------------------------

    def structure(self) -> None:
        function = self.function
        for index, local in enumerate(function.locals):
            if local.id != LocalId(index):
                self.fail(TYPED_LOCALS, f"the local at position {index} has the id "
                                        f"_{local.id.index}", local.loc)
            if local.ty is None:
                self.fail(TYPED_LOCALS, f"_{index} has no type", local.loc)
            if isinstance(local.ty, UnknownType):
                self.fail(TYPED_LOCALS, f"_{index} has the unresolved type "
                                        f"{display_type(local.ty)}", local.loc)
            if owns_resource(local.ty, self.drops):
                self.owning.add(local.id)
        for param, convention in function.params:
            if not self.has_local(param):
                self.fail(TYPED_LOCALS, f"the parameter _{param.index} is no local of the "
                                        f"function", None)
            self.conventions[param] = convention
        for block in function.blocks:
            if block.id in self.blocks:
                self.fail(KNOWN_BLOCKS, f"two blocks have the id bb{block.id.index}", None)
            self.blocks[block.id] = block
        if function.entry not in self.blocks:
            self.fail(KNOWN_BLOCKS, f"the entry bb{function.entry.index} is no block of the "
                                    f"function", None)
        for block in function.blocks:
            self.block(block)

    def block(self, block: SlirBlock) -> None:
        where = f"bb{block.id.index}"
        for index, stmt in enumerate(block.statements):
            span = getattr(stmt, "span", None)
            if not isinstance(span, Span):
                self.fail(SPANS, f"statement {index} of {where} has no span", None)
            if isinstance(stmt, _TERMINATORS):
                self.fail(ONE_TERMINATOR, f"{where} holds a terminator among its statements",
                          span)
            arm = _STATEMENTS.get(type(stmt))
            if arm is None:
                er.raise_internal_error("CE0000", span=span,
                                        detail=f"the SLIR verifier has no row for the class "
                                               f"{type(stmt).__name__}")
            arm(self, stmt)
        term = block.terminator
        if not isinstance(term, _TERMINATORS):
            self.fail(ONE_TERMINATOR, f"{where} has no terminator", None)
        if not isinstance(term.span, Span):
            self.fail(SPANS, f"the terminator of {where} has no span", None)
        for target in _successors(term):
            if target not in self.blocks:
                self.fail(KNOWN_BLOCKS, f"{where} goes to bb{target.index}, which is no block "
                                        f"of the function", term.span)
        if isinstance(term, tm.SwitchInt):
            self.operand(term.operand, term.span)
        if isinstance(term, tm.Trap):
            self.trap(term)

    def trap(self, term: tm.Trap) -> None:
        message = REGISTRY.get(term.code)
        if message is None or message.category is not Category.RUNTIME:
            self.fail(TRAP, f"{term.code} is no registered runtime code", term.span)
        wanted = sum(1 for hit in _PRINTF.findall(message.text) if hit != "%%")
        if len(term.args) != wanted:
            self.fail(TRAP, f"{term.code} prints {wanted} values, and the trap gives "
                            f"{len(term.args)}", term.span)
        for arg in term.args:
            self.operand(arg, term.span)

    def place(self, place: p.Place, span: Span) -> _Typed:
        """The root and the type of `place`; each projection must fit the type of its base."""
        if isinstance(place, p.Global):
            return _Typed(place, None)
        arm = _PLACES.get(type(place))
        if arm is None:
            er.raise_internal_error("CE0000", span=span,
                                    detail=f"the SLIR verifier has no row for the place "
                                           f"{type(place).__name__}")
        return arm(self, place, span)

    def operand(self, operand: p.Operand, span: Span) -> None:
        if isinstance(operand, p.Const):
            return
        typed = self.place(operand.place, span)
        if isinstance(operand, p.Move) and isinstance(typed.root, p.Global):
            self.fail(GLOBAL_MOVE, f"{show(operand)} takes the value of a global", span)

    def rvalue(self, value: r.Rvalue, span: Span) -> None:
        if isinstance(value, r.Binary) and value.op in ("and", "or"):
            self.fail(SHORT_CIRCUIT, f"a binary carries '{value.op}'", span)
        for part in r.operands_of(value):
            if isinstance(part, (p.Copy, p.Move, p.Const)):
                self.operand(part, span)
            else:
                self.place(part, span)

    def assign(self, stmt: st.Assign) -> None:
        self.place(stmt.place, stmt.span)
        self.rvalue(stmt.rvalue, stmt.span)

    def call(self, stmt: st.Call) -> None:
        self.place(stmt.dest, stmt.span)
        callee = stmt.callee
        if isinstance(callee, st.Indirect):
            self.operand(callee.operand, stmt.span)
        for operand, convention in stmt.args:
            self.operand(operand, stmt.span)
            if convention.consumes and isinstance(operand, p.Copy):
                self.owned_copy(operand, stmt.span)
        if isinstance(callee, st.Direct):
            self.conventions_of(stmt, callee)

    def owned_copy(self, operand: p.Copy, span: Span) -> None:
        """An `Owned` argument takes a copy of a plain value alone. A `string` is the one
        owning type that may come as a copy: one bound from a literal owns no heap, and
        `borrow` adopts it (ir.md 7.9)."""
        ty = self.place(operand.place, span).ty
        if ty is None or isinstance(ty, _Payload) or ty == BuiltinType.STRING:
            return
        if owns_resource(ty, self.drops):
            self.fail(OWNED_COPY, f"{show(operand)} has the type {display_type(ty)}, which "
                                  f"owns", span)

    def conventions_of(self, stmt: st.Call, callee: st.Direct) -> None:
        declared = self.declared(callee)
        if declared is None:
            self.fail(CONVENTIONS, f"the callee @{callee.name} has no declaration", stmt.span)
        wanted, given = declared.conventions, tuple(c for _, c in stmt.args)
        if len(given) < len(wanted) or (len(given) > len(wanted) and not declared.var_arg):
            self.fail(CONVENTIONS, f"the call of @{callee.name} has {len(given)} arguments, "
                                   f"and @{callee.name} declares {len(wanted)}", stmt.span)
        for index, (have, want) in enumerate(zip(given[:len(wanted)], wanted, strict=True)):
            if have is not want:
                self.fail(CONVENTIONS, f"argument {index} of @{callee.name} is @{have.value}, "
                                       f"and its parameter is @{want.value}", stmt.span)

    def dropped(self, stmt: st.Drop | st.DropIfSet) -> LocalId:
        """The local that a drop targets: a drop of any other place is not checked yet."""
        if not isinstance(stmt.place, p.Local):
            self.not_checked(f"a drop of {show(stmt.place)}, which is no local", stmt.span)
        self.local_place(stmt.place, stmt.span)
        return stmt.place.id

    def drop(self, stmt: st.Drop | st.DropIfSet) -> None:
        ty = self.type_of(self.dropped(stmt))
        if not owns_resource(ty, self.drops):
            self.fail(OWNING_DROP, f"{show(stmt.place)} has the type {display_type(ty)}, "
                                   f"which owns nothing", stmt.span)

    def drop_if_set(self, stmt: st.DropIfSet) -> None:
        self.drop(stmt)
        flag = stmt.flag
        if not self.has_local(flag) or self.type_of(flag) != BuiltinType.BOOL:
            self.fail(BOOL_FLAG, f"the flag _{flag.index} is no bool local", stmt.span)
        kept = self.guards.setdefault(self.dropped(stmt), flag)
        if kept != flag:
            self.fail(BOOL_FLAG, f"{show(stmt.place)} has two flags, _{kept.index} and "
                                 f"_{flag.index}", stmt.span)

    def borrow_region(self, stmt: st.BeginBorrow | st.EndBorrow) -> None:
        self.not_checked(f"the borrow region {stmt.id.index}", stmt.span)

    def storage(self, stmt: st.StorageLive | st.StorageDead) -> None:
        if not self.has_local(stmt.local):
            self.fail(TYPED_PLACES, f"_{stmt.local.index} is no local of the function",
                      stmt.span)

    # -- the places ----------------------------------------------------------------------

    def local_place(self, place: p.Local, span: Span) -> _Typed:
        if not self.has_local(place.id):
            self.fail(TYPED_PLACES, f"_{place.id.index} is no local of the function", span)
        return _Typed(place, self.type_of(place.id))

    def base(self, place: p.Field | p.Index | p.Downcast | p.Deref,
             span: Span) -> tuple[_Typed, Type | _Payload]:
        """The base of a projection and its type. Only a `Global` has no type in SLIR, so a
        projection of one is not checked yet."""
        base = self.place(place.base, span)
        if base.ty is None:
            self.not_checked(f"the projection {show(place)} of a global", span)
        return base, base.ty

    def field_place(self, place: p.Field, span: Span) -> _Typed:
        base, ty = self.base(place, span)
        if isinstance(ty, _Payload):
            parts = ty.types
        elif isinstance(ty, StructType):
            parts = tuple(field_ty for _, field_ty in ty.fields)
        else:
            self.fail(TYPED_PLACES, f"{show(place)} reads a field of the type "
                                    f"{display_type(ty)}, which has no fields", span)
        if not 0 <= place.index < len(parts):
            self.fail(TYPED_PLACES, f"{show(place)} reads field {place.index}, and the value "
                                    f"has {len(parts)}", span)
        return _Typed(base.root, parts[place.index], base.indexes)

    def index_place(self, place: p.Index, span: Span) -> _Typed:
        base, ty = self.base(place, span)
        self.operand(place.index, span)
        if not isinstance(ty, (ArrayType, DynamicArrayType)):
            self.fail(TYPED_PLACES, f"{show(place)} indexes a value that is no array", span)
        return _Typed(base.root, ty.base_type, (*base.indexes, place.index))

    def downcast_place(self, place: p.Downcast, span: Span) -> _Typed:
        base, ty = self.base(place, span)
        if isinstance(ty, EnumType):
            for variant in ty.variants:
                if variant.name == place.variant:
                    return _Typed(base.root, _Payload(variant.associated_types),
                                  base.indexes)
        self.fail(TYPED_PLACES, f"{show(place)} names no variant of the type of its place",
                  span)

    def deref_place(self, place: p.Deref, span: Span) -> _Typed:
        base, ty = self.base(place, span)
        if isinstance(ty, ReferenceType):
            return _Typed(base.root, ty.referenced_type, base.indexes)
        if isinstance(ty, PointerType):
            return _Typed(base.root, ty.pointee_type, base.indexes)
        self.fail(TYPED_PLACES, f"{show(place)} reads through a value that is no pointer",
                  span)


_STATEMENTS: dict[type, Callable[..., None]] = {
    st.Assign: _Check.assign,
    st.Call: _Check.call,
    st.Drop: _Check.drop,
    st.DropIfSet: _Check.drop_if_set,
    st.BeginBorrow: _Check.borrow_region,
    st.EndBorrow: _Check.borrow_region,
    st.StorageLive: _Check.storage,
    st.StorageDead: _Check.storage,
}

_PLACES: dict[type, Callable[..., _Typed]] = {
    p.Local: _Check.local_place,
    p.Field: _Check.field_place,
    p.Index: _Check.index_place,
    p.Downcast: _Check.downcast_place,
    p.Deref: _Check.deref_place,
}


def _successors(term: tm.SlirTerminator) -> list[BlockId]:
    if isinstance(term, tm.Goto):
        return [term.target]
    if isinstance(term, tm.SwitchInt):
        targets = [target for _, target in term.targets]
        return targets if term.otherwise is None else [*targets, term.otherwise]
    return []


@dataclass(slots=True)
class _Flow:
    """The dataflow of the moves and the drops of one function (see the module docstring)."""
    check: _Check
    flagged: dict[LocalId, tuple[LocalId, ...]] = field(default_factory=dict)
    report: bool = False

    def run(self) -> None:
        check = self.check
        for local, flag in check.guards.items():
            self.flagged[flag] = (*self.flagged.get(flag, ()), local)
        start = tuple(frozenset({(_LIVE if local.id in check.conventions else _UNINIT,
                                  _UNSET if local.id in check.guards else _NO_FLAG)})
                      for local in check.function.locals)
        entry = check.function.entry
        states: dict[BlockId, _State] = {entry: start}
        work = [entry]
        while work:
            block = check.blocks[work.pop()]
            out = self.through(block, states[block.id])
            for target in _successors(block.terminator):
                old = states.get(target)
                new = out if old is None else tuple(a | b for a, b in zip(old, out, strict=True))
                if new != old:
                    states[target] = new
                    work.append(target)
        self.report = True
        for block in check.function.blocks:
            if block.id in states:
                self.through(block, states[block.id])

    def through(self, block: SlirBlock, state: _State) -> _State:
        configs = list(state)
        for stmt in block.statements:
            self.statement(configs, stmt)
        self.terminator(configs, block.terminator)
        return tuple(configs)

    def violate(self, rule: str, detail: str, span: Optional[Span]) -> None:
        if self.report:
            self.check.fail(rule, detail, span)

    # -- the transfer --------------------------------------------------------------------

    def statement(self, configs: list[frozenset[_Config]], stmt: st.SlirStatement) -> None:
        span = stmt.span
        if isinstance(stmt, st.Assign):
            for part in r.operands_of(stmt.rvalue):
                self.operand(configs, part, span)
            self.write(configs, stmt.place, span, stmt.rvalue)
        elif isinstance(stmt, st.Call):
            if isinstance(stmt.callee, st.Indirect):
                self.operand(configs, stmt.callee.operand, span)
            for operand, _ in stmt.args:
                self.operand(configs, operand, span)
            self.write(configs, stmt.dest, span, None)
        elif isinstance(stmt, st.Drop):
            self.drop(configs, self.check.dropped(stmt), span)
        elif isinstance(stmt, st.DropIfSet):
            self.drop_if_set(configs, self.check.dropped(stmt), stmt.flag, span)
        elif isinstance(stmt, (st.StorageLive, st.StorageDead)):
            self.storage(configs, stmt.local, span,
                         "starts" if isinstance(stmt, st.StorageLive) else "ends")
        else:
            er.raise_internal_error("CE0000", span=span,
                                    detail=f"the dataflow of the SLIR verifier has no row for "
                                           f"the class {type(stmt).__name__}")

    def terminator(self, configs: list[frozenset[_Config]], term: tm.SlirTerminator) -> None:
        if isinstance(term, tm.SwitchInt):
            self.operand(configs, term.operand, term.span)
        elif isinstance(term, tm.Trap):
            for arg in term.args:
                self.operand(configs, arg, term.span)
        elif isinstance(term, tm.Return):
            self.leave(configs, term.span)

    def operand(self, configs: list[frozenset[_Config]], part: p.Operand | p.Place,
                span: Span) -> None:
        if isinstance(part, p.Const):
            return
        if isinstance(part, p.Move):
            self.move(configs, part.place, span)
        elif isinstance(part, p.Copy):
            self.read(configs, part.place, span)
        else:
            self.read(configs, part, span)

    def root(self, configs: list[frozenset[_Config]], place: p.Place,
             span: Span) -> Optional[LocalId]:
        """The root local of `place`, after the read of each index in it."""
        typed = self.check.place(place, span)
        for index in typed.indexes:
            self.operand(configs, index, span)
        return typed.root.id if isinstance(typed.root, p.Local) else None

    def read(self, configs: list[frozenset[_Config]], place: p.Place, span: Span) -> None:
        local = self.root(configs, place, span)
        if local is not None:
            self.spent(configs, local, f"reads {show(place)}", span)

    def spent(self, configs: list[frozenset[_Config]], local: LocalId, what: str,
              span: Span) -> None:
        states = {state for state, _ in configs[local.index]}
        if _UNINIT in states:
            self.violate(IN_STORAGE, f"{what} where _{local.index} holds no value", span)
        if _MOVED in states:
            self.violate(USE_AFTER_MOVE, f"{what} after a move of _{local.index}", span)
        if _DROPPED in states:
            self.violate(USE_AFTER_MOVE, f"{what} after a drop of _{local.index}", span)

    def move(self, configs: list[frozenset[_Config]], place: p.Place, span: Span) -> None:
        local = self.root(configs, place, span)
        if local is None:
            return
        if self.borrowed(local) or self.check.conventions.get(local) is Convention.INOUT:
            if local in self.check.owning:
                self.violate(DROP_ONCE, f"moves the parameter _{local.index}, which the "
                                        f"function does not own", span)
        self.spent(configs, local, f"moves {show(place)}", span)
        configs[local.index] = frozenset((_MOVED, flag) for _, flag in configs[local.index])

    def write(self, configs: list[frozenset[_Config]], place: p.Place, span: Span,
              value: Optional[r.Rvalue]) -> None:
        if not isinstance(place, p.Local):
            self.read(configs, place, span)
            return
        local = place.id
        if local in self.check.owning and any(s == _LIVE for s, _ in configs[local.index]):
            self.violate(DROP_ONCE, f"writes _{local.index} while it holds a value", span)
        configs[local.index] = frozenset((_LIVE, flag) for _, flag in configs[local.index])
        written = _UNKNOWN
        if (isinstance(value, r.Use) and isinstance(value.operand, p.Const)
                and isinstance(value.operand.constant, k.Bool)):
            written = _TRUE if value.operand.constant.value else _FALSE
        self.set_flag(configs, local, written)

    def set_flag(self, configs: list[frozenset[_Config]], flag: LocalId, value: str) -> None:
        for guarded in self.flagged.get(flag, ()):
            configs[guarded.index] = frozenset((s, value) for s, _ in configs[guarded.index])

    def drop(self, configs: list[frozenset[_Config]], local: LocalId, span: Span) -> None:
        if self.borrowed(local):
            self.violate(DROP_ONCE, f"drops the parameter _{local.index}, which the function "
                                    f"borrows", span)
        states = {s for s, _ in configs[local.index]}
        if _MOVED in states:
            self.violate(USE_AFTER_MOVE, f"drops _{local.index} after a move", span)
        if _DROPPED in states:
            self.violate(DROP_ONCE, f"drops _{local.index} two times", span)
        if _UNINIT in states:
            self.violate(DROP_ONCE, f"drops _{local.index}, which holds no value", span)
        configs[local.index] = frozenset((_DROPPED, flag) for _, flag in configs[local.index])

    def drop_if_set(self, configs: list[frozenset[_Config]], local: LocalId, flag: LocalId,
                    span: Span) -> None:
        after = set()
        for state, value in configs[local.index]:
            if value not in (_TRUE, _FALSE):
                self.violate(DROP_ONCE, f"reads the flag _{flag.index} of _{local.index}, "
                                        f"which holds no known value", span)
            elif value == _TRUE and state != _LIVE:
                self.violate(DROP_ONCE, f"the flag _{flag.index} is true where _{local.index} "
                                        f"holds no value", span)
            elif value == _FALSE and state == _LIVE:
                self.violate(DROP_ONCE, f"the flag _{flag.index} is false where _{local.index} "
                                        f"holds a value", span)
            after.add((_DROPPED if state == _LIVE else state, value))
        configs[local.index] = frozenset(after)

    def storage(self, configs: list[frozenset[_Config]], local: LocalId, span: Span,
                verb: str) -> None:
        """`StorageLive` and `StorageDead`: each leaves the local with no value."""
        if local in self.check.owning and any(s == _LIVE for s, _ in configs[local.index]):
            self.violate(DROP_ONCE, f"{verb} the storage of _{local.index} while it holds a "
                                    f"value", span)
        configs[local.index] = frozenset((_UNINIT, flag) for _, flag in configs[local.index])
        self.set_flag(configs, local, _UNSET)

    def leave(self, configs: list[frozenset[_Config]], span: Span) -> None:
        """The `return`: no owning local holds a value, except `_0` and an `Inout` parameter,
        which must hold one. `_0` holds a value on every path."""
        for local in sorted(self.check.owning):
            if local.index == 0 or self.borrowed(local):
                continue
            states = {s for s, _ in configs[local.index]}
            if self.check.conventions.get(local) is Convention.INOUT:
                if states != {_LIVE}:
                    self.violate(DROP_ONCE, f"returns while the inout parameter "
                                            f"_{local.index} holds no value", span)
            elif _LIVE in states:
                self.violate(DROP_ONCE, f"returns while _{local.index} holds a value", span)
        if any(state != _LIVE for state, _ in configs[0]):
            self.violate(RETURN_SLOT, "returns before a write of _0", span)

    def borrowed(self, local: LocalId) -> bool:
        convention = self.check.conventions.get(local)
        return convention is not None and convention.read_only
