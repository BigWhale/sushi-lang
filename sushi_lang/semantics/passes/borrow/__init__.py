"""Pass 3: borrow checking. The pass object holds the state; siblings hold the rules."""

from __future__ import annotations
from typing import Dict, FrozenSet, List, Optional, Set

from sushi_lang.semantics.ast import (
    ArrayLiteral,
    BinaryOp,
    BlankLit,
    Block,
    BoolLit,
    Borrow,
    CastExpr,
    Call,
    DotCall,
    DynamicArrayFrom,
    DynamicArrayNew,
    EnumConstructor,
    Expr,
    ExtendDef,
    FloatLit,
    FuncDef,
    IndexAccess,
    InterpolatedString,
    IntLit,
    Lambda,
    MemberAccess,
    MethodCall,
    Name,
    Param,
    Program,
    RangeExpr,
    Spread,
    StringLit,
    TryExpr,
    UnaryOp,
)
from sushi_lang.semantics.typesys import (
    BorrowMode, BuiltinType, DynamicArrayType, ReferenceType, Type,
)
from sushi_lang.semantics.ownership import ConsumingUse
from sushi_lang.semantics.param_modes import CalleeModes, param_mode
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.error_reporter import PassErrorReporter

from .borrows import check_borrow
from .calls import (
    apply_destroy_effects,
    consume_call_args,
    consume_indirect_args,
    is_enum_constructor,
    maybe_mark_container_insert,
    maybe_mark_own_alloc_move,
    settle_method_args,
)
from .consume import (
    binds_a_bare_literal_string, consume, consume_named, name_provenance,
)
from .destroy_effects import compute_destroy_effects
from .diagnostics import emit_use_after_move, emit_use_of_invalidated_borrow
from .flow import FlowFacts, reinitialize, terminates
from .state import BorrowState
from .statements import check_block
from .types import TypeQueries
from .writes import MUTATING_METHODS, READONLY_RECEIVERS, maybe_reject_mutation


# Nodes that own nothing and name nothing. Listed EXPLICITLY so `_check_expr`'s `else`
# can be a hard error (CE0125) instead of a silent skip.
INERT_EXPRS = (IntLit, FloatLit, BoolLit, BlankLit, StringLit, DynamicArrayNew)


def _build_callee_modes(tables) -> CalleeModes:
    """Build the mode resolver from Pass 0's tables."""
    if tables is None:
        return CalleeModes()
    funcs = getattr(tables, "funcs", None)
    struct_names = set(getattr(getattr(tables, "structs", None), "by_name", None) or ())
    struct_names |= set(
        getattr(getattr(tables, "generic_structs", None), "by_name", None) or ())
    stdlib_sigs = funcs.stdlib_by_name() if funcs is not None else {}
    # A generic fn is called by its bare name in a template body but interned under a
    # mangled one, and the mode does not vary per instantiation. Concrete table first.
    sigs = dict(getattr(getattr(tables, "generic_funcs", None), "by_name", None) or {})
    sigs.update(getattr(funcs, "by_name", None) or {})
    return CalleeModes(
        func_sigs=sigs,
        struct_names=struct_names,
        stdlib_sigs=stdlib_sigs,
    )


class BorrowChecker:
    """Analyzes borrowing safety for a program."""

    def __init__(self, reporter: Reporter,
                 destroy_effects: Optional[Dict[str, FrozenSet[int]]] = None,
                 enum_names: Optional[Set[str]] = None,
                 tables=None):
        self.reporter = reporter
        # Used only to RESOLVE a named type before classifying it: `owns_heap` answers
        # False for an UnknownType, so without this an owning struct would alias.
        self.tables = tables
        self.types = TypeQueries(tables)
        self.err = PassErrorReporter(reporter)
        # fn name -> the poke param indices it destroys (#168). Computed once over EVERY
        # unit by compute_destroy_effects(), so a cross-unit callee is not a blind spot.
        # Empty means "no call destroys anything", i.e. the old intra-procedural behaviour.
        self.destroy_effects: Dict[str, FrozenSet[int]] = destroy_effects or {}
        # Tells an enum constructor `Box.Full(a)` from a method call `xs.push(a)` -- both
        # are DotCall here, and only the former is an ownership sink (#134).
        self.enum_names: Set[str] = enum_names or set()
        self.borrow_state: Dict[str, BorrowState] = {}
        self.active_borrows: Set[str] = set()
        # One frame per open block; `check_block` pops it, which is what gives a
        # `let`-borrow a LEXICAL lifetime. `active_borrows` clears per statement.
        self._scope_binding_borrows: list[list[tuple[str, str]]] = []
        # THE mode resolver. Which kind of callee a `Call` names, and what each of its
        # parameters declares. Built from the same tables the backend's copy reads, so
        # the two halves cannot reach different answers (docs/design/borrow-model.md S1).
        self.callee_modes = _build_callee_modes(tables)

    def run(self, program: Program) -> None:
        """Run borrow checking on the entire program."""
        for func in program.functions:
            self._check_function(func)

        for ext in program.extensions:
            self._check_extension(ext)

        for ext in program.generic_extensions:
            self._check_extension(ext)

        # Perk impl methods carry an implicit `self`, like an extension method. Omitting
        # them left a perk body unchecked entirely (#176).
        for perk_impl in program.perk_impls:
            for method in perk_impl.methods:
                self._check_callable(method.params, method.body, fn_name=method.name,
                                     self_type=perk_impl.target_type,
                                     self_span=perk_impl.target_type_span,
                                     self_mode=getattr(method, "self_mode", None))

    def _check_function(self, func: FuncDef) -> None:
        """Check borrow safety for a single plain function."""
        self._check_callable(func.params, func.body, fn_name=func.name)

    def _check_extension(self, ext: ExtendDef) -> None:
        """Check borrow safety for an extension method."""
        self._check_callable(ext.params, ext.body, fn_name=ext.name,
                             self_type=ext.target_type,
                             self_span=ext.target_type_span,
                             self_mode=getattr(ext, "self_mode", None))

    def _check_callable(self, params: List[Param], body: Block, *,
                        fn_name: Optional[str] = None,
                        self_type: Optional[Type] = None,
                        self_span: Optional[Span] = None,
                        self_mode: Optional[str] = None) -> None:
        """Set up the state for one callable body and check it. THE entry point."""
        self.borrow_state = {}
        self.active_borrows = set()
        self._scope_binding_borrows = []
        is_method = self_type is not None

        if is_method:
            # A `poke self` / `peek self` receiver keeps its full ReferenceType, which
            # wires the rules in by construction (#327).
            receiver_type = self_type
            if self_mode is not None and self_type is not None:
                receiver_type = ReferenceType(
                    self_type,
                    BorrowMode.POKE if self_mode == "poke" else BorrowMode.PEEK)
            receiver = BorrowState(name="self", var_type=receiver_type,
                                   declared_at_span=self_span,
                                   is_method_receiver=True)
            self._mark_borrow_param(receiver)
            self.borrow_state["self"] = receiver

        for param in params:
            state = BorrowState(name=param.name, var_type=param.ty,
                                declared_at_span=getattr(param, "loc", None))
            # main's `string[] args` is a borrowed view of process argv (the runtime owns and
            # frees it). Stamp it so a by-value move is a hard error (CE2410), not a silent
            # move that makes the callee free argv (N2).
            if fn_name == "main" and self._is_argv_view_param(param.ty):
                state.is_argv_view = True
            # Every mode but `nom` is a borrow, in every kind of callable. `is_method`
            # used to be the condition, because a method was the only callable whose
            # parameters did not transfer (docs/design/borrow-model.md S1).
            if not param_mode(param).consumes:
                self._mark_borrow_param(state)
            self.borrow_state[param.name] = state

        check_block(self, body)

    @staticmethod
    def _mark_borrow_param(state: BorrowState) -> None:
        """A parameter whose mode is a borrow does not own its value -- `string` included."""
        state.is_borrow_param = True

    @staticmethod
    def _is_argv_view_param(ty: Optional[Type]) -> bool:
        """True if `ty` is `string[]` -- the shape of main's borrowed argv parameter."""
        return isinstance(ty, DynamicArrayType) and ty.base_type == BuiltinType.STRING

    @staticmethod
    def _reinitialize(state: BorrowState) -> None:
        """Delegate to flow module."""
        reinitialize(state)

    @staticmethod
    def _terminates(node) -> bool:
        """Delegate to flow module."""
        return terminates(node)

    def _snapshot_flow(self) -> FlowFacts:
        """Every path-sensitive fact (for branch and loop control-flow joins)."""
        return FlowFacts(
            moved=frozenset(n for n, s in self.borrow_state.items() if s.is_moved),
            destroyed=frozenset(n for n, s in self.borrow_state.items() if s.is_destroyed),
            owns_no_heap=frozenset(
                n for n, s in self.borrow_state.items() if s.owns_no_heap),
            invalidation=tuple(
                (n, s.invalidated_at, s.invalidated_by)
                for n, s in self.borrow_state.items() if s.invalidated_at is not None),
        )

    def _restore_flow(self, facts: FlowFacts) -> None:
        """Set every path-sensitive flag to exactly what `facts` says."""
        invalidation = {name: (span, by) for name, span, by in facts.invalidation}
        for name, state in self.borrow_state.items():
            state.is_moved = name in facts.moved
            state.is_destroyed = name in facts.destroyed
            state.owns_no_heap = name in facts.owns_no_heap
            span, by = invalidation.get(name, (None, ()))
            state.invalidated_at = span
            state.invalidated_by = by

    def _check_expr(self, expr: Expr) -> None:
        """Check borrow safety for an expression."""
        if isinstance(expr, Borrow):
            check_borrow(self, expr)

        elif isinstance(expr, Name):
            if expr.id in self.borrow_state:
                state = self.borrow_state[expr.id]
                if state.is_moved:
                    emit_use_after_move(self, expr.id, expr.loc, state)
                elif state.is_destroyed:
                    self.err.emit(er.ERR.CE2406, expr.loc, name=expr.id)
                elif state.invalidated_at is not None:
                    # A `let`-borrow binding read after its owner changed (#242).
                    emit_use_of_invalidated_borrow(self, expr.id, expr.loc, state)

        elif isinstance(expr, Call):
            self._check_expr(expr.callee)
            for arg in expr.args:
                self._check_expr(arg)

            # An argument is consumed if and only if the parameter it lands on DECLARES
            # a consume. The callee's kind decides where the declaration is read from
            # (docs/design/borrow-model.md S5); the mode decides what happens.
            consume_call_args(self, expr)

            # A callee that destroys its `poke` parameter destroys the CALLER's value
            # (#168). CE2406 still fires from the Name arm above -- no new emit site.
            apply_destroy_effects(self, expr)

        elif isinstance(expr, MethodCall):
            self._check_expr(expr.receiver)
            for arg in expr.args:
                self._check_expr(arg)
            maybe_reject_mutation(self, expr)
            settle_method_args(self, expr)
            maybe_mark_container_insert(self, expr)
            maybe_mark_own_alloc_move(self, expr)

        elif isinstance(expr, DotCall):
            # An FFI string argument NEVER consumes (docs/design/borrow-model.md S5), and
            # that holds STRUCTURALLY: an FFI call arrives as a DotCall, and this arm
            # consumes only for an enum constructor and a container insert.
            #
            # Do NOT add a blanket `consume(arg, CALL_ARG)` loop here -- it would make
            # every `libc.*(s)` call site a false CE2405.
            # `tests/ffi/test_ffi_string_arg_not_consumed.sushi` is the gate.
            self._check_expr(expr.receiver)
            for arg in expr.args:
                self._check_expr(arg)
            maybe_reject_mutation(self, expr)
            # An enum constructor is an ownership sink (#134): it stores the payload
            # shallowly and frees it, so a bare owning Name MOVES. `Box.Full(a)` arrives
            # here as a DotCall, not an EnumConstructor.
            if is_enum_constructor(self, expr):
                for arg in expr.args:
                    consume(self, arg, ConsumingUse.ENUM_PAYLOAD)
            elif getattr(expr, "callee_fn_type", None) is not None:
                # An indirect call through a fn-typed field is a real call, so its
                # arguments follow the declared modes. Keyed on Pass 2's `callee_fn_type`
                # stamp, so an FFI / extension / builtin method keeps the rule above.
                consume_indirect_args(self, expr)
            else:
                settle_method_args(self, expr)
                maybe_mark_container_insert(self, expr)
            maybe_mark_own_alloc_move(self, expr)

        elif isinstance(expr, BinaryOp):
            self._check_expr(expr.left)
            self._check_expr(expr.right)

        elif isinstance(expr, UnaryOp):
            self._check_expr(expr.expr)

        elif isinstance(expr, IndexAccess):
            self._check_expr(expr.array)
            self._check_expr(expr.index)

        elif isinstance(expr, MemberAccess):
            self._check_expr(expr.receiver)

        elif isinstance(expr, EnumConstructor):
            for arg in expr.args:
                self._check_expr(arg)
                consume(self, arg, ConsumingUse.ENUM_PAYLOAD)

        elif isinstance(expr, DynamicArrayFrom):
            for elem in expr.elements.elements:
                self._check_expr(elem)
                # from([...]) is an ownership sink (#134): a bare owning element variable
                # moves into the new dynamic array; a MemberAccess element keeps its copy.
                consume(self, elem, ConsumingUse.ARRAY_ELEMENT)

        elif isinstance(expr, ArrayLiteral):
            for elem in expr.elements:
                self._check_expr(elem)
                # An array-literal element is an ownership sink (#134): a bare owning
                # element variable moves into the array. A MemberAccess element keeps its
                # continuing-owner copy (only bare Names are marked by _mark_moved).
                consume(self, elem, ConsumingUse.ARRAY_ELEMENT)

        elif isinstance(expr, CastExpr):
            self._check_expr(expr.expr)

        elif isinstance(expr, TryExpr):
            self._check_expr(expr.expr)

        elif isinstance(expr, InterpolatedString):
            for part in expr.parts:
                if not isinstance(part, str):
                    self._check_expr(part)

        elif isinstance(expr, Lambda):
            # The heap environment takes ownership and outlives the creating scope, so
            # this is the CAPTURE consuming use. A capture holds no source `Expr`, so the
            # provenance goes on the `Param` itself and `emit_lambda` reads it there.
            for cap in (expr.captures or []):
                if not isinstance(cap.name, str):
                    continue
                provenance = name_provenance(self, cap.name)
                cap.ownership_provenance = provenance
                consume_named(self, cap.name, provenance, expr.loc)

        elif isinstance(expr, Spread):
            # Bloom: `arr...`. The source is USED here (so a moved source is reported)
            # and, in a call-argument position, MOVED -- see consume,
            # which the Call arm runs over every argument after checking them.
            self._check_expr(expr.value)

        elif isinstance(expr, RangeExpr):
            self._check_expr(expr.start)
            self._check_expr(expr.end)

        elif isinstance(expr, INERT_EXPRS):
            pass

        else:
            # NOT a silent fall-through: a node with no arm gets NO borrow checking, a
            # soundness hole rather than a crash (#174, #175, #176). The CI gate is
            # tests/unit/test_borrow_dispatch_is_total.py; this is the backstop.
            er.raise_internal_error("CE0125", node=type(expr).__name__)


__all__ = [
    'BorrowChecker',
    'BorrowState',
    'FlowFacts',
    'INERT_EXPRS',
    'MUTATING_METHODS',
    'READONLY_RECEIVERS',
    'binds_a_bare_literal_string',
    'compute_destroy_effects',
]
