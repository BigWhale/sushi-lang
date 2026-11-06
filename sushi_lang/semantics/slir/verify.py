"""The SLIR verifier (docs/design/ir.md 8.13).

The SLIR stage gives each function that the lowering finished to `SlirVerifier.verify`,
before the function meets the next stage, on every compile. `verify_function` checks one
function, and a failed check is the internal error CE0158: one diagnostic, at the span of
the statement, that names the function and the rule.

The structure comes first, statement by statement: a span on each statement and terminator,
one terminator for each block, blocks that exist, a type for each local and for each
projection of a place, a `Move` out of no `Global`, a `Drop` of an owning type, a `bool`
flag for a `DropIfSet`, no `and` or `or` in a `Binary`, a `Cast` between two numeric types
or of one type, an `Int` constant in the range of its type, a `SwitchInt` on an integer or a
`bool` whose cases its type holds, each one time, a tag of its enum for each case of a switch
on the local that a `Discriminant` of its block writes, a `Discriminant` of an enum, a
variant (an rvalue or a constant) that its enum declares with each value of its payload, a
struct or a tuple (an rvalue or a constant) with each field of its type, each part of the
type of its payload value or of its field, an array (an rvalue or a constant) whose parts
fit its runs and its element type, a `Len` of an array or a string, a `Slice` of an array
with two `i32` bounds, a view (a local that a `Slice` writes) that nothing else writes,
moves or drops, a typed place for each `BeginBorrow`, no `Move` of an element, a `Drop` of an
element only of an owning type, no `Move` of the value of a repeat run, the
conventions of a `Direct` callee, a
receiver operand for a call of a built-in method, no copy of an owning value
other than a `string` into an `Owned` argument, and a registered `RExxxx` for a `Trap`, with
one operand of the type that each printf conversion of its text takes. The type of a place
also gives its root local, so a `Move` of a projection is a `Move` of its root.

Then `verify_regions.py` checks the view locals and the borrow regions, and the dataflow of
`verify_flow.py` checks the moves and the drops of each local on each path. A view local
owns nothing, so the rule of the drops does not read it. The checks of a call are in
`verify_calls.py`, the checks of an array value, a length and a slice in `verify_arrays.py`,
and the names of the rules in `verify_rules.py`.

The verifier refuses a shape that no lowering makes yet, and that it does not check, with
the internal error CE0000: a `Drop` of a place that is no local and no element, and a
`DropIfSet` of a place that is no local. A projection of a `Global` is typed from the type that the
`Global` carries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Callable, NoReturn, Optional, Sequence, get_args

from sushi_lang.internals import errors as er
from sushi_lang.internals.errors.registry import REGISTRY, Category
from sushi_lang.internals.errors.runtime import printf_conversions
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.integer_width import fits_integer_type, integer_range
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import SlirBlock, SlirFunction, address_locals
from sushi_lang.semantics.slir.ids import BlockId, LocalId
from sushi_lang.semantics.slir.printer import show
from sushi_lang.semantics.slir.verify_calls import (
    DeclaredLookup, DeclaredParams as DeclaredParams, check_call, declared_params,
)
from sushi_lang.semantics.slir.verify_arrays import (
    array_value, length_or_slice, part_drop,
)
from sushi_lang.semantics.slir.verify_flow import Flow, successors
from sushi_lang.semantics.slir.verify_regions import check_regions
from sushi_lang.semantics.slir.verify_rules import CONVENTIONS as CONVENTIONS
from sushi_lang.semantics.slir.verify_rules import (
    ARRAY_VALUE, BOOL_FLAG, CONSTANT_RANGE, ELEMENT_MOVE, ENUM_VALUE, GLOBAL_MOVE,
    INTEGER_SWITCH, KNOWN_BLOCKS, NUMERIC_CAST, ONE_TERMINATOR, OWNING_DROP, PEEK_RETURN,
    SHORT_CIRCUIT, SPANS, STRUCT_VALUE, TAG_SWITCH, TRAP, TYPED_LOCALS, TYPED_PLACES, VIEW,
)
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, PointerType, ReferenceType,
    StructType, Type, UnknownType, owns_resource,
)
from sushi_lang.semantics.type_predicates import BUILTIN_NUMERIC_TYPES

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.externals import ExternalTable
    from sushi_lang.semantics.passes.collect.functions import ExtensionTable, FunctionTable


_TERMINATORS = get_args(tm.SlirTerminator)

#: The type of the operand that each printf conversion of a runtime text takes (ir.md 8.8).
TRAP_OPERAND_TYPES: dict[str, Type] = {"%d": BuiltinType.I32, "%.*s": BuiltinType.STRING}


@dataclass(slots=True)
class SlirVerifier:
    """The pipeline entry of the verifier: the SLIR stage of a unit gives it each function
    that the lowering finished. The declared parameters come from the function table, the
    extension table and the external table, at each call, so a generic instance that a
    later body declares is found too."""

    def verify(self, function: SlirFunction, *, funcs: FunctionTable,
               extensions: ExtensionTable, externals: ExternalTable,
               drops: frozenset[str], filename: Optional[str]) -> None:
        verify_function(function, drops=drops,
                        declared=partial(declared_params, funcs, extensions,
                                         externals=externals),
                        filename=filename)


def verify_function(function: SlirFunction, *, drops: frozenset[str],
                    declared: DeclaredLookup, filename: Optional[str] = None) -> None:
    """CE0158 at the first rule of ir.md 8.13 that `function` breaks."""
    check = Check(function, drops, declared, filename)
    check.structure()
    check_regions(function, check.fail)
    Flow(check).run()


@dataclass(frozen=True, slots=True)
class _Payload:
    """What a `Downcast` place holds: the payload of one variant, read through a `Field`."""
    types: tuple[Type, ...]


@dataclass(frozen=True, slots=True)
class _Typed:
    """The root of a place, its type, and the operand of each `Index` in it."""
    root: p.Local | p.Global
    ty: Type | _Payload
    indexes: tuple[p.Operand, ...] = ()


@dataclass(slots=True)
class Check:
    """The structure of one function, and the facts that the dataflow reads."""
    function: SlirFunction
    drops: frozenset[str]
    declared: DeclaredLookup
    filename: Optional[str]
    blocks: dict[BlockId, SlirBlock] = field(default_factory=dict)
    conventions: dict[LocalId, Convention] = field(default_factory=dict)
    owning: set[LocalId] = field(default_factory=set)
    guards: dict[LocalId, LocalId] = field(default_factory=dict)
    views: set[LocalId] = field(default_factory=set)

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
            if owns_resource(local.ty, self.drops) and not local.view:
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
            self.views |= {stmt.place.id for stmt in block.statements
                           if isinstance(stmt, st.Assign) and isinstance(stmt.rvalue, r.Slice)
                           and isinstance(stmt.place, p.Local)}
        self.owning -= self.views | address_locals(function)
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
        for target in successors(term):
            if target not in self.blocks:
                self.fail(KNOWN_BLOCKS, f"{where} goes to bb{target.index}, which is no block "
                                        f"of the function", term.span)
        if isinstance(term, tm.SwitchInt):
            self.operand(term.operand, term.span)
            self.switch(term)
            self.tag_switch(term, block)
        if isinstance(term, tm.Trap):
            self.trap(term)

    def trap(self, term: tm.Trap) -> None:
        """One operand for each printf conversion of the registry text, of the type that
        the conversion takes. The trap reads each operand, so a `Move` is refused."""
        message = REGISTRY.get(term.code)
        if message is None or message.category is not Category.RUNTIME:
            self.fail(TRAP, f"{term.code} is no registered runtime code", term.span)
        conversions = printf_conversions(message.text)
        if len(term.args) != len(conversions):
            self.fail(TRAP, f"{term.code} prints {len(conversions)} values, and the trap gives "
                            f"{len(term.args)}", term.span)
        for conversion, arg in zip(conversions, term.args, strict=True):
            self.operand(arg, term.span)
            if isinstance(arg, p.Move):
                self.fail(TRAP, f"{show(arg)} moves; the trap of {term.code} reads each "
                                f"operand", term.span)
            wanted = TRAP_OPERAND_TYPES.get(conversion)
            if wanted is None:
                self.fail(TRAP, f"the text of {term.code} holds the conversion {conversion}, "
                                f"which no trap operand fills", term.span)
            given = self.operand_type(arg, term.span)
            if given != wanted:
                shown = "no type" if given is None else f"the type {display_type(given)}"
                self.fail(TRAP, f"{show(arg)} has {shown}, and the conversion {conversion} of "
                                f"{term.code} takes {display_type(wanted)}", term.span)

    def switch(self, term: tm.SwitchInt) -> None:
        """The operand is an integer or a `bool`, and its type holds each case value, which
        comes one time. A match on an i8 gives `0xff` as the case -1."""
        ty = self.operand_type(term.operand, term.span)
        bounds = (0, 1) if ty == BuiltinType.BOOL else integer_range(ty)
        if ty is None or bounds is None:
            shown = "no type" if ty is None else f"the type {display_type(ty)}"
            self.fail(INTEGER_SWITCH, f"{show(term.operand)} has {shown}, and a switch reads "
                                      f"an integer or a bool", term.span)
        seen: set[int] = set()
        for value, _ in term.targets:
            if not bounds[0] <= value <= bounds[1]:
                self.fail(INTEGER_SWITCH, f"the case {value} is out of the range of "
                                          f"{display_type(ty)}", term.span)
            if value in seen:
                self.fail(INTEGER_SWITCH, f"the case {value} comes two times", term.span)
            seen.add(value)

    def tag_switch(self, term: tm.SwitchInt, block: SlirBlock) -> None:
        """A switch on the local that a `Discriminant` of its own block writes: each case is
        the tag of a variant of that enum."""
        operand = term.operand
        if not (isinstance(operand, p.Copy) and isinstance(operand.place, p.Local)):
            return
        writes = [stmt.rvalue for stmt in block.statements
                  if isinstance(stmt, st.Assign) and stmt.place == operand.place]
        if not writes or not isinstance(writes[-1], r.Discriminant):
            return
        enum = self.place(writes[-1].place, term.span).ty
        if not isinstance(enum, EnumType):
            return
        for value, _ in term.targets:
            if not 0 <= value < len(enum.variants):
                self.fail(TAG_SWITCH, f"the case {value} is the tag of no variant of "
                                      f"{display_type(enum)}", term.span)

    def enum_variant(self, ty: Type, variant: str, parts: list[Optional[Type]],
                     span: Span) -> None:
        """`ty` is an enum that declares `variant` with a payload value for each part."""
        info = ty.get_variant(variant) if isinstance(ty, EnumType) else None
        if info is None:
            self.fail(ENUM_VALUE, f"{display_type(ty)} declares no variant '{variant}'", span)
        self.parts_fit(ENUM_VALUE, f"the variant {variant} of {display_type(ty)}",
                       info.associated_types, parts, span)

    def struct_value(self, ty: Type, parts: list[Optional[Type]], span: Span) -> None:
        """`ty` is a struct or a tuple with a field for each part."""
        if not isinstance(ty, StructType):
            self.fail(STRUCT_VALUE, f"a struct value of {display_type(ty)}, which is no "
                                    f"struct", span)
        self.parts_fit(STRUCT_VALUE, display_type(ty),
                       [field_ty for _, field_ty in ty.fields], parts, span)

    def parts_fit(self, rule: str, what: str, wanted: Sequence[Type],
                  parts: list[Optional[Type]], span: Span) -> None:
        """One part for each wanted type, of that type. A part whose type SLIR does not
        hold (a payload behind a `Downcast`, a `FnRef`) is not compared."""
        if len(parts) != len(wanted):
            self.fail(rule, f"{what} holds {len(wanted)} values, and it is given "
                            f"{len(parts)}", span)
        for index, (want, have) in enumerate(zip(wanted, parts, strict=True)):
            if have is not None and have != want:
                self.fail(rule, f"part {index} of {what} has the type {display_type(have)}, "
                                f"and the value holds {display_type(want)} there", span)

    def place(self, place: p.Place, span: Span) -> _Typed:
        """The root and the type of `place`; each projection must fit the type of its base."""
        if isinstance(place, p.Global):
            return _Typed(place, place.ty)
        arm = _PLACES.get(type(place))
        if arm is None:
            er.raise_internal_error("CE0000", span=span,
                                    detail=f"the SLIR verifier has no row for the place "
                                           f"{type(place).__name__}")
        return arm(self, place, span)

    def operand(self, operand: p.Operand, span: Span) -> None:
        if isinstance(operand, p.Const):
            self.constant(operand.constant, span)
            return
        typed = self.place(operand.place, span)
        if isinstance(operand, p.Move) and isinstance(typed.root, p.Global):
            self.fail(GLOBAL_MOVE, f"{show(operand)} takes the value of a global", span)
        if isinstance(operand, p.Move) and typed.indexes:
            self.fail(ELEMENT_MOVE, f"{show(operand)} moves an element out of its array", span)
        if isinstance(operand, p.Move) and typed.root == operand.place \
                and isinstance(typed.root, p.Local) and typed.root.id in self.views:
            self.fail(VIEW, f"{show(operand)} moves a view, which owns nothing", span)

    def constant(self, constant: k.Constant, span: Span) -> None:
        """An integer constant holds a value that its type can hold. A variant is a variant of
        its enum, and each part is a constant by the same rules."""
        if isinstance(constant, k.Int) and integer_range(constant.ty) is not None \
                and not fits_integer_type(constant.value, constant.ty):
            self.fail(CONSTANT_RANGE, f"{constant.value} is out of the range of "
                                      f"{display_type(constant.ty)}", span)
        if isinstance(constant, k.Aggregate):
            types = [_constant_type(part) for part in constant.parts]
            if constant.variant is not None:
                self.enum_variant(constant.ty, constant.variant, types, span)
            elif isinstance(constant.ty, ArrayType):
                self.parts_fit(ARRAY_VALUE, display_type(constant.ty),
                               [constant.ty.base_type] * constant.ty.size, types, span)
            else:
                self.struct_value(constant.ty, types, span)
            for part in constant.parts:
                self.constant(part, span)

    def rvalue(self, value: r.Rvalue, span: Span) -> None:
        if isinstance(value, r.Binary) and value.op in ("and", "or"):
            self.fail(SHORT_CIRCUIT, f"a binary carries '{value.op}'", span)
        for part in r.operands_of(value):
            if isinstance(part, (p.Copy, p.Move, p.Const)):
                self.operand(part, span)
            else:
                self.place(part, span)
        if isinstance(value, r.Cast):
            self.cast(value, span)
        if isinstance(value, r.Discriminant) \
                and not isinstance(self.place(value.place, span).ty, (EnumType, type(None))):
            self.fail(ENUM_VALUE, f"{show(value)} reads a value that is no enum", span)
        if isinstance(value, r.Aggregate) and isinstance(value.kind, r.AggVariant):
            self.enum_variant(value.kind.ty, value.kind.variant,
                              [self.operand_type(part, span) for part in value.parts], span)
        if isinstance(value, r.Aggregate) and isinstance(value.kind, (r.AggStruct, r.AggTuple)):
            self.struct_value(value.kind.ty,
                              [self.operand_type(part, span) for part in value.parts], span)
        if isinstance(value, r.Aggregate) and isinstance(value.kind, r.AggArray):
            array_value(self, value.kind, value.parts, span)
        if isinstance(value, (r.Len, r.Slice)):
            length_or_slice(self, value, span)

    def cast(self, value: r.Cast, span: Span) -> None:
        """A cast joins two numeric types, or keeps one type. Its operand has a known type
        in SLIR except behind a `Downcast`."""
        source = self.operand_type(value.operand, span)
        if source is None or source == value.target:
            return
        if source not in BUILTIN_NUMERIC_TYPES or value.target not in BUILTIN_NUMERIC_TYPES:
            self.fail(NUMERIC_CAST, f"{show(value)} casts {display_type(source)} to "
                                    f"{display_type(value.target)}", span)

    def operand_type(self, operand: p.Operand, span: Span) -> Optional[Type]:
        if isinstance(operand, p.Const):
            return _constant_type(operand.constant)
        ty = self.place(operand.place, span).ty
        return None if isinstance(ty, _Payload) else ty

    def assign(self, stmt: st.Assign) -> None:
        self.place(stmt.place, stmt.span)
        self.rvalue(stmt.rvalue, stmt.span)
        if isinstance(stmt.rvalue, r.Slice):
            ty = self.place(stmt.place, stmt.span).ty
            if not isinstance(ty, DynamicArrayType):
                self.fail(VIEW, f"the view {show(stmt.place)} is no T[]", stmt.span)
        else:
            self.not_a_view(stmt.place, stmt.span)
            self.no_view_copy(stmt)
            self.no_copy_into_return_slot(stmt)

    def no_view_copy(self, stmt: st.Assign) -> None:
        """A view goes into no owning local: that local would drop storage it does not own."""
        value = stmt.rvalue
        if isinstance(value, r.Use) and isinstance(value.operand, p.Copy) \
                and isinstance(value.operand.place, p.Local) \
                and value.operand.place.id in self.views \
                and isinstance(stmt.place, p.Local) and stmt.place.id in self.owning:
            self.fail(VIEW, f"{show(stmt.place)} owns a copy of the view "
                            f"{show(value.operand.place)}", stmt.span)

    def no_copy_into_return_slot(self, stmt: st.Assign) -> None:
        """A `Copy` of an owning value goes into `_0` only when `_0` is a view: the
        declaration returns `peek`. A `string` is the carve-out of `OWNED_COPY`: a string
        bound from a literal is adopted, and it owns no heap."""
        if stmt.place != p.Local(LocalId(0)) or self.function.locals[0].view:
            return
        value = stmt.rvalue
        parts = (value.operand,) if isinstance(value, r.Use) else (
            value.parts if isinstance(value, r.Aggregate) else ())
        for part in parts:
            if isinstance(part, p.Copy):
                ty = self.operand_type(part, stmt.span)
                if (ty is not None and ty != BuiltinType.STRING
                        and owns_resource(ty, self.drops)):
                    self.fail(PEEK_RETURN, f"_0 takes a copy of the owning value "
                                           f"{show(part.place)}, and the function does not "
                                           f"return peek", stmt.span)

    def not_a_view(self, place: p.Place, span: Span) -> None:
        """Only a `Slice` writes a view."""
        if isinstance(place, p.Local) and place.id in self.views:
            self.fail(VIEW, f"{show(place)} is a view, and only a slice writes it", span)

    def dropped(self, stmt: st.Drop | st.DropIfSet) -> LocalId:
        """The local that a drop targets: a drop of any other place is not checked yet."""
        if not isinstance(stmt.place, p.Local):
            self.not_checked(f"a drop of {show(stmt.place)}, which is no local", stmt.span)
        self.local_place(stmt.place, stmt.span)
        return stmt.place.id

    def drop(self, stmt: st.Drop | st.DropIfSet) -> None:
        if isinstance(stmt, st.Drop) and isinstance(stmt.place, (p.Field, p.Index)):
            part_drop(self, stmt)
            return
        if self.dropped(stmt) in self.views:
            self.fail(VIEW, f"{show(stmt.place)} is a view, which owns nothing to drop",
                      stmt.span)
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
        if isinstance(stmt, st.BeginBorrow):
            self.place(stmt.place, stmt.span)

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
        """The base of a projection and its type."""
        base = self.place(place.base, span)
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
        """An element of an array, or a byte of a string, at an `i32` index."""
        base, ty = self.base(place, span)
        self.operand(place.index, span)
        if self.operand_type(place.index, span) not in (BuiltinType.I32, None):
            self.fail(TYPED_PLACES, f"{show(place)} has an index that is no i32", span)
        if ty == BuiltinType.STRING:
            return _Typed(base.root, BuiltinType.U8, (*base.indexes, place.index))
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
    st.Assign: Check.assign,
    st.Call: check_call,
    st.Drop: Check.drop,
    st.DropIfSet: Check.drop_if_set,
    st.BeginBorrow: Check.borrow_region,
    st.EndBorrow: Check.borrow_region,
    st.StorageLive: Check.storage,
    st.StorageDead: Check.storage,
}

_PLACES: dict[type, Callable[..., _Typed]] = {
    p.Local: Check.local_place,
    p.Field: Check.field_place,
    p.Index: Check.index_place,
    p.Downcast: Check.downcast_place,
    p.Deref: Check.deref_place,
}


def _constant_type(constant: k.Constant) -> Optional[Type]:
    """The type of a constant; None for a `FnRef`, whose type SLIR does not hold."""
    if isinstance(constant, (k.Int, k.Float, k.Aggregate)):
        return constant.ty
    if isinstance(constant, k.Str):
        return BuiltinType.STRING
    if isinstance(constant, k.Bool):
        return BuiltinType.BOOL
    return BuiltinType.BLANK if isinstance(constant, k.Blank) else None
