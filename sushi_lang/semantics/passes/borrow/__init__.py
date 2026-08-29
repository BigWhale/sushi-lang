"""The borrow pass. The pass object holds the state; siblings hold the rules."""

from __future__ import annotations
from typing import Dict, FrozenSet, List, Optional, Set

from sushi_lang.semantics.ast import Block, ExtendDef, FuncDef, Param, Program
from sushi_lang.semantics.typesys import (
    BuiltinType, DynamicArrayType, ReferenceType, Type,
)
from sushi_lang.semantics.param_modes import CalleeModes, param_mode
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.error_reporter import PassErrorReporter

from .consume import binds_a_bare_literal_string
from .destroy_effects import compute_destroy_effects
from .expressions import INERT_EXPRS, check_expr
from .flow import FlowFacts
from .state import BorrowState, borrow_mode
from .statements import check_block
from .types import TypeQueries
from .writes import MUTATING_METHODS, READONLY_RECEIVERS


def _build_callee_modes(tables, unit_name: Optional[str] = None,
                        scope: object = None) -> CalleeModes:
    """Build the mode resolver from the collect pass's tables, as ONE unit reads them.

    `unit_name` is the unit whose bodies are about to be checked. A name it declares
    itself answers with its own signature, which is what stops a source library's own
    call being measured against the consumer's declaration of the same name (#487,
    `docs/design/unit-namespaces.md` section 13.1), and `scope` is what stops it reading
    a declaration from a unit it never imported (section 6).
    """
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
    sigs.update(funcs.view_for(unit_name, scope) if funcs is not None else {})
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
                 tables=None,
                 unit_name: Optional[str] = None,
                 scope: object = None):
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
        self.callee_modes = _build_callee_modes(tables, unit_name, scope)
        # Which unit is being checked, and what it may write bare. A bare name that is
        # no local is a CONSTANT, and reading its type needs the same per-unit ladder
        # every other reader walks (`docs/design/unit-namespaces.md` section 8).
        self.unit_name = unit_name
        self.scope = scope

    def run(self, program: Program) -> None:
        """Run borrow checking on the entire program."""
        for func in program.functions:
            # Whose file the diagnostics of this body belong to (#471).
            self.reporter.origin = getattr(func, "library_origin", None)
            self._check_function(func)
        self.reporter.origin = None

        for ext in program.extensions:
            self._check_extension(ext)

        # The TEMPLATE, with `self` still abstract. Its per-instantiation truth is checked
        # on the monomorphized copies instead (semantic_analyzer), because an owning field
        # is a consume in one instantiation and a plain copy in another -- one answer
        # cannot serve both (#391). This walk stays for what does NOT depend on the type
        # argument, and because an UNINSTANTIATED template has no copy to be checked on.
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
        # Conditional-move tracking (#414): `branch_depth` counts the if/match/loop
        # bodies entered; a move at a depth greater than the owner's declaration depth
        # cannot dominate the scope exit, so the backend must guard that owner's frees
        # with a runtime drop flag. Stamped on the BODY block (shared by the synthetic
        # perk-method wrapper) for the backend to read.
        self.branch_depth = 0
        self.conditional_moves = set()

        if self_type is not None:
            # A `poke self` / `peek self` receiver keeps its full ReferenceType, which
            # wires the rules in by construction (#327).
            receiver_type = self_type
            if self_mode is not None:
                receiver_type = ReferenceType(self_type, borrow_mode(self_mode))
            self._declare(BorrowState(name="self", var_type=receiver_type,
                                      declared_at_span=self_span,
                                      is_method_receiver=True),
                          is_borrow=True)

        for param in params:
            state = BorrowState(name=param.name, var_type=param.ty,
                                declared_at_span=getattr(param, "loc", None))
            # main's `string[] args` is a borrowed view of process argv (the runtime owns
            # and frees it). Stamp it so a by-value move is a hard error (CE2410), not a
            # silent move that makes the callee free argv (N2).
            if fn_name == "main" and self._is_argv_view_param(param.ty):
                state.is_argv_view = True
            # Every mode but `nom` is a borrow, in every kind of callable. Being a METHOD
            # used to be the condition, because a method was the only callable whose
            # parameters did not transfer (docs/design/borrow-model.md S1).
            self._declare(state, is_borrow=not param_mode(param).consumes)

        check_block(self, body)
        body.conditional_move_names = frozenset(self.conditional_moves)

    def _declare(self, state: BorrowState, *, is_borrow: bool) -> None:
        """Register a parameter, recording whether its declared mode is a borrow."""
        # A borrow parameter does not own its value -- `string` included (#338).
        state.is_borrow_param = is_borrow
        self.borrow_state[state.name] = state

    @staticmethod
    def _is_argv_view_param(ty: Optional[Type]) -> bool:
        """True if `ty` is `string[]` -- the shape of main's borrowed argv parameter."""
        return isinstance(ty, DynamicArrayType) and ty.base_type == BuiltinType.STRING


__all__ = [
    'BorrowChecker',
    'BorrowState',
    'FlowFacts',
    'INERT_EXPRS',
    'MUTATING_METHODS',
    'READONLY_RECEIVERS',
    'binds_a_bare_literal_string',
    'check_expr',
    'compute_destroy_effects',
]
