"""Lowering: the AST of one body and its `Bindings` give a `ShirBody` (docs/design/ir.md 6.3, 7.9).

`lower_body` is the one entry point of body lowering. It builds the FRAME of a body: its
declaration, its origin, its parameters with their conventions and the locals of the
parameters. `_lower_block` fills the block, one statement at a time, through the table of
`lower_nodes.py`. A construct that the table does not lower yet is a hole, and
`LoweredProgram.holes` records each one. The locals that the binders of the block declare
come with the lowering of each binder, and `ShirBody.locals` holds every local in id order.
The block of a constant or a `var` is its initializer, as one `Return` (ir.md 6.3).

`lower_unit` calls `lower_body` for each body that the scope pass checked in one unit,
except a template as written: SHIR is built from the specialized bodies (ir.md 7), and
the check copy of a template (ir.md 4.3) is lowered on its own. The analyzer calls it for
each unit, after the scope pass, on every compile; `LoweredProgram` keeps the bodies for
`--dump-shir`, and no pass reads them yet.

Every id comes from a counter in the order of the walk, so a body gives the same ids on
every run (ir.md 11.3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Sequence, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import (
    ConstDef, ExtendDef, ExtendWithDef, FuncDef, Param, Program,
)
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import param_mode, receiver_mode
from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
from sushi_lang.semantics.passes.collect.functions import Param as SignatureParam
from sushi_lang.semantics.shir.bindings import Bindings
from sushi_lang.semantics.shir.body import (
    BodyOrigin, Initializer, Instance, ShirBody, ShirLocal, ShirParam,
)
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import IdAllocator, ShirId
from sushi_lang.semantics.shir.lookup import DeclLookup
from sushi_lang.semantics.shir.lower_binders import written_param_type
from sushi_lang.semantics.shir.lower_nodes import BodyLowerer, binder_local, stops_on_hole
from sushi_lang.semantics.shir.nodes import Hole, ShirBlock, TypeRef

if TYPE_CHECKING:
    from sushi_lang.semantics.const_eval import ConstantEvaluator
    from sushi_lang.semantics.units import Unit

#: A declaration that owns a body: a function or a perk method, an instance of a generic
#: function, an extension method, and a constant or a `var` (its initializer).
Declaration: TypeAlias = FuncDef | GenericFuncDef | ExtendDef | ConstDef

#: A parameter of a declaration: the AST's, or the signature's of a generic function.
AnyParam: TypeAlias = Param | SignatureParam


@dataclass(frozen=True, slots=True)
class LoweringContext:
    """What a body needs beside its `Bindings`.

    `decls` gives each declaration its id, and `holes` records each hole of the program.
    `evaluator` reads the value of an operand (ruling E3, ir.md 7.4), in the scope of the
    unit of the body. `lookup` reads what a type name or a namespace before a dot holds.
    `owner` is the perk implementation that holds a perk method, because the method has
    no field that names its receiver type. `stops` is true when a hole raises the stop
    code (`stops_on_hole`).
    """
    decls: DeclNames
    holes: list[Hole]
    evaluator: Optional[ConstantEvaluator] = None
    lookup: Optional[DeclLookup] = None
    owner: Optional[ExtendWithDef] = None
    stops: bool = False


def lower_body(bindings: Bindings, ctx: LoweringContext) -> ShirBody:
    """The `ShirBody` of the declaration that `bindings` belongs to."""
    decl = bindings.body
    if not isinstance(decl, (FuncDef, GenericFuncDef, ExtendDef, ConstDef)):
        er.raise_internal_error("CE0000", detail=f"no lowering for a body of {type(decl).__name__}")
    ids: IdAllocator[ShirId] = IdAllocator(ShirId)
    params, locals_ = _frame(decl, bindings, ctx)
    fn_id = ctx.decls.declare(_written_name(decl, ctx))
    lowerer = BodyLowerer(ids, ctx.decls, bindings, ctx.holes, _span(decl.loc), ctx.evaluator,
                          ctx.lookup, ctx.stops)
    block = _lower_block(decl, lowerer)
    every_local = sorted([*locals_, *lowerer.locals], key=lambda local: local.id)
    return ShirBody(fn_id, _origin(decl), params, every_local, block)


@dataclass(slots=True)
class LoweredProgram:
    """The bodies of each unit, in the compilation order, the names of their ids, and the
    holes of every body in the order of the walk."""
    decls: DeclNames = field(default_factory=DeclNames)
    units: list[tuple[Unit, list[ShirBody]]] = field(default_factory=list)
    holes: list[Hole] = field(default_factory=list)


def lower_unit(lowered: LoweredProgram, unit: Unit, tables: Sequence[Bindings],
               evaluator: Optional[ConstantEvaluator] = None,
               lookup: Optional[DeclLookup] = None, *, scope_failed: bool) -> None:
    """The body of each declaration of `unit` that the scope pass checked, in order.

    `evaluator` is a silent evaluator and `lookup` reads the declaration tables, each in
    the scope of `unit`. `scope_failed` is true when the scope pass reported an error in
    the unit: then a hole does not stop the compile (`stops_on_hole`). The perk owners
    and the written templates of the unit are found once, so the cost of a unit is linear
    in its declarations.
    """
    program = unit.ast
    if program is None:
        return
    owners = _perk_owners(program)
    templates = _written_template_members(program)
    stops = stops_on_hole(scope_failed)
    lowered.units.append((unit, [
        lower_body(table, LoweringContext(lowered.decls, lowered.holes, evaluator, lookup,
                                          owners.get(id(table.body)), stops))
        for table in tables if not _is_written_template(table.body, templates)]))


def _written_template_members(program: Program) -> set[int]:
    """The generic extensions and the methods of the generic perk implementations."""
    members = {id(ext) for ext in program.generic_extensions}
    members.update(id(method) for impl in program.generic_perk_impls for method in impl.methods)
    return members


def _is_written_template(decl: object, members: set[int]) -> bool:
    """A template as the source writes it. Its copies are the bodies that SHIR holds."""
    if id(decl) in members:
        return True
    if isinstance(decl, ExtendDef):
        return bool(decl.type_params) and not decl.method_type_args
    return bool(getattr(decl, "type_params", None)) and getattr(decl, "instance_of", None) is None


def _perk_owners(program: Program) -> dict[int, ExtendWithDef]:
    """The perk implementation of each perk method of the unit, keyed by the method."""
    return {id(method): impl for impl in program.perk_impls for method in impl.methods}


def _lower_block(decl: Declaration, lowerer: BodyLowerer) -> ShirBlock:
    """The block of the body, or the initializer of a constant or a `var`."""
    if isinstance(decl, ConstDef):
        return lowerer.initializer(decl)
    return lowerer.block(decl.body)


def _origin(decl: Declaration) -> BodyOrigin:
    return Initializer() if isinstance(decl, ConstDef) else Instance()


def _owner_type(decl: Declaration, ctx: LoweringContext) -> Optional[TypeRef]:
    """The type that a method extends, or None for a function and a constant."""
    if isinstance(decl, ExtendDef):
        return decl.target_type
    if isinstance(decl, FuncDef) and ctx.owner is not None:
        return ctx.owner.target_type
    return None


def _written_name(decl: Declaration, ctx: LoweringContext) -> str:
    """The name of the declaration as the source writes it; a method has its type before it."""
    owner = _owner_type(decl, ctx)
    return decl.name if owner is None else f"{display_type(owner)}.{decl.name}"


def _frame(decl: Declaration, bindings: Bindings,
           ctx: LoweringContext) -> tuple[list[ShirParam], list[ShirLocal]]:
    """The parameters, the receiver first, and one local for each of them.

    A static method has no receiver (#542), and a constant has no parameter.
    """
    if isinstance(decl, ConstDef):
        return [], []
    params: list[ShirParam] = []
    locals_: list[ShirLocal] = []
    receiver = _owner_type(decl, ctx)
    if isinstance(decl, (FuncDef, ExtendDef)) and receiver is not None and not (
            isinstance(decl, ExtendDef) and decl.is_static):
        local = binder_local(bindings, decl, "self")
        params.append(ShirParam(local, receiver, receiver_mode(decl.self_mode).convention,
                                _span(decl.self_mode_span, decl.name_span, decl.loc)))
        locals_.append(ShirLocal(local, "self", None, None))
    for param in decl.params:
        local = binder_local(bindings, param, param.name)
        ty = _param_type(param)
        params.append(ShirParam(local, ty, param_mode(param).convention,
                                _span(getattr(param, "loc", None), param.name_span, decl.loc)))
        locals_.append(ShirLocal(local, param.name, ty, param.name_span))
    return params, locals_


def _param_type(param: AnyParam) -> TypeRef:
    """The declared type with no `peek` or `poke`: the convention says how it crosses."""
    ty = written_param_type(param.ty)
    if ty is None:
        er.raise_internal_error("CE0000", detail=f"the parameter '{param.name}' has no type")
    return ty


def _span(*candidates: Optional[Span]) -> Span:
    """The first span that the source gives. A body that the compiler makes keeps the span
    of its cause, so the declaration has one at the latest."""
    for span in candidates:
        if span is not None:
            return span
    er.raise_internal_error("CE0000", detail="a SHIR node has no span")
