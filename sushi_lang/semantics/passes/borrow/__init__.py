"""The borrow pass. The pass object holds the state; siblings hold the rules."""

from __future__ import annotations
from collections import ChainMap
from contextlib import contextmanager
from typing import TYPE_CHECKING, AbstractSet, Callable, Dict, FrozenSet, Iterator, List, Optional, Set

from sushi_lang.semantics.ast import (
    Block, Expand, ExtendDef, ExtendWithDef, FuncDef, Param, Program)
from sushi_lang.semantics.typesys import (
    BorrowMode, BuiltinType, DynamicArrayType, ReferenceType, StructType, Type,
)
from sushi_lang.semantics.passes.lift import ENV_PARAM_NAME
from sushi_lang.semantics.namespaces import body_namespaces
from sushi_lang.semantics.param_modes import CalleeModes, param_mode, receiver_mode
from sushi_lang.internals.report import Reporter, Span

from .consume import binds_a_bare_literal_string
from .destroy_effects import compute_destroy_effects
from .expressions import INERT_EXPRS, check_expr
from .flow import FlowFacts, LoopFrame
from .reads import captured_key, unit_variables
from .state import BorrowState
from .statements import check_block
from .types import TypeQueries
from .written_names import WrittenNameReporter
from .methods import MUTATING_METHODS
from .writes import READONLY_RECEIVERS

if TYPE_CHECKING:
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.template_scope import CheckCopy


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
    # A LIVE view of both tables: the typecheck pass runs after this is built, and it
    # interns the instance a generic struct constructor solves from its arguments (#1150).
    struct_names = ChainMap(*(
        table.by_name for table in (getattr(tables, "structs", None),
                                    getattr(tables, "generic_structs", None))
        if getattr(table, "by_name", None) is not None))
    stdlib_sigs = funcs.stdlib_by_name() if funcs is not None else {}
    # A generic fn is called by its bare name in a template body but interned under a
    # mangled one, and the mode does not vary per instantiation. Concrete table first.
    # Both tables answer per unit, or a call is measured against a declaration from
    # next door (#495).
    generics = getattr(tables, "generic_funcs", None)
    sigs = dict(generics.view_for(unit_name, scope) if generics is not None else {})
    sigs.update(funcs.view_for(unit_name, scope) if funcs is not None else {})
    return CalleeModes(
        func_sigs=sigs,
        struct_names=struct_names,
        stdlib_sigs=stdlib_sigs,
    )


class BorrowChecker:
    """Analyzes borrowing safety for a program."""

    def __init__(self, reporter: Reporter, *,
                 tables: SymbolTables,
                 destroy_effects: Optional[Dict[str, FrozenSet[int]]] = None,
                 enum_names: Optional[Set[str]] = None,
                 unit_name: Optional[str] = None,
                 scope: object = None):
        self.reporter = reporter
        # Used only to RESOLVE a named type before classifying it: `owns_resource`
        # answers False for an UnknownType, so without this an owning struct would alias.
        self.tables = tables
        self.types = TypeQueries(tables)
        self.err = WrittenNameReporter(reporter, lambda: self.borrow_state)
        # fn name -> the poke param indices it destroys (#168). Computed once over EVERY
        # unit by compute_destroy_effects(), so a cross-unit callee is not a blind spot.
        # Empty means "no call destroys anything", i.e. the old intra-procedural behaviour.
        self.destroy_effects: Dict[str, FrozenSet[int]] = destroy_effects or {}
        # Tells an enum constructor `Box.Full(a)` from a method call `xs.push(a)` -- both
        # are DotCall here, and only the former is an ownership sink (#134).
        self.enum_names: Set[str] = enum_names or set()
        self.borrow_state: Dict[str, BorrowState] = {}
        self.active_borrows: Set[str] = set()
        # The nodes that are the operand of a `??`: a read-through `or_err` stands only
        # there (CE2522).
        self.try_operands: Set[int] = set()
        # One frame per open block; `check_block` pops it, which is what gives a
        # `let`-borrow a LEXICAL lifetime. `active_borrows` clears per statement.
        self._scope_binding_borrows: list[list[tuple[str, str]]] = []
        # One frame per loop body being checked: the `break` and `continue` paths (#993).
        self._loop_frames: list[LoopFrame] = []
        # THE mode resolver. Which kind of callee a `Call` names, and what each of its
        # parameters declares. Built from the same tables the backend's copy reads, so
        # the two halves cannot reach different answers (docs/design/borrow-model.md S1).
        self.callee_modes = _build_callee_modes(tables, unit_name, scope)
        # Which unit is being checked, and what it may write bare. A bare name that is
        # no local is a CONSTANT, and reading its type needs the same per-unit ladder
        # every other reader walks (`docs/design/unit-namespaces.md` section 8).
        self.unit_name = unit_name
        self.scope = scope
        # Set only while a check copy is read (#1070, R6): the element type of each
        # `expand`, and the value packs that the template takes with `nom`.
        self.pack_element_of: Optional[Callable[[Expand], Optional[Type]]] = None
        self.owned_packs: FrozenSet[str] = frozenset()

    def refresh_callee_modes(self) -> None:
        """Read the function tables again: the typecheck pass can declare a late instance
        after this checker was built (#1155)."""
        self.callee_modes = _build_callee_modes(self.tables, self.unit_name, self.scope)

    def run(self, program: Program, skip: AbstractSet[int] = frozenset()) -> None:
        """Run borrow checking on the entire program.

        `skip` holds the `id` of each written template that the typecheck pass checked
        on a check copy: the borrow pass read that copy (`check_template_copy`), with
        each type parameter opaque.
        """
        self.refresh_callee_modes()
        for func in program.functions:
            if id(func) in skip:
                continue
            # Whose body this is: the file its diagnostics belong to (#471), and whether
            # it is one of many copies of one source (#648).
            self.reporter.enter_body(func)
            self._check_function(func)
        self.reporter.leave_body()

        for ext in program.extensions:
            self._check_extension(ext)

        # A TEMPLATE that no check copy covers, as written, with `self` abstract: a
        # template of a consumed library unit was checked at its own build. What each
        # copy decides per instance is in the stamps of its own borrow walk (R7).
        for ext in program.generic_extensions:
            if id(ext) not in skip:
                self._check_extension(ext)

        # Perk impl methods carry an implicit `self`, like an extension method. Omitting
        # them left a perk body unchecked entirely (#176).
        for perk_impl in program.perk_impls:
            self._check_perk_impl(perk_impl)
        self.reporter.leave_body()

    def check_template_copy(self, copy: 'CheckCopy') -> None:
        """The borrow rules on a check copy (#1070, R5): an opaque type parameter moves.

        The copy is read in the overlay tables, which hold its instances over an opaque
        parameter. A fault here refuses the template, so each copy of it is muted.
        """
        saved = (self.tables, self.types)
        self.tables, self.types = copy.tables, TypeQueries(copy.tables)
        self.pack_element_of, self.owned_packs = copy.element_of, copy.owned_packs
        try:
            node = copy.node
            if isinstance(node, ExtendWithDef):
                self._check_perk_impl(node)
            elif isinstance(node, ExtendDef):
                self._check_extension(node)
            else:
                self.reporter.enter_body(node)
                self._check_function(node)
            self.pack_element_of, self.owned_packs = None, frozenset()
            for fn in copy.lifted:
                self.reporter.enter_body(fn)
                self._check_function(fn)
        finally:
            self.tables, self.types = saved
            self.pack_element_of, self.owned_packs = None, frozenset()
            self.reporter.leave_body()

    def _check_perk_impl(self, perk_impl) -> None:
        """Borrow-check every method body of one perk implementation."""
        for method in perk_impl.methods:
            # Whether this body is one of many copies of one source (#800).
            self.reporter.enter_body(method)
            with self._body_scope(method):
                self._check_callable(method.params, method.body, fn_name=method.name,
                                     self_type=perk_impl.target_type,
                                     self_span=perk_impl.target_type_span,
                                     self_mode=getattr(method, "self_mode", None))

    @contextmanager
    def _body_scope(self, node) -> Iterator[None]:
        """Check one body in the scope its names resolve in (`body_namespaces`, #1120)."""
        table = body_namespaces(node, getattr(self.tables, "namespaces", None))
        if table is None:
            yield
            return
        saved = (self.scope, self.callee_modes)
        self.scope = table.scope
        self.callee_modes = _build_callee_modes(self.tables, self.unit_name, table.scope)
        try:
            yield
        finally:
            self.scope, self.callee_modes = saved

    def _check_function(self, func: FuncDef) -> None:
        """Check borrow safety for a single plain function."""
        with self._body_scope(func):
            self._check_callable(func.params, func.body, fn_name=func.name)

    def _check_extension(self, ext: ExtendDef) -> None:
        """Check borrow safety for an extension method."""
        # A copy of a refused template says nothing (#1070), as a perk method does.
        self.reporter.enter_body(ext)
        with self._body_scope(ext):
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
        self.err.enter_body()
        self.active_borrows = set()
        self._scope_binding_borrows = []
        self._loop_frames = []
        # Conditional-move tracking (#414): `branch_depth` counts the if/match/loop
        # bodies entered; a move at a depth greater than the owner's declaration depth
        # cannot dominate the scope exit, so the backend must guard that owner's frees
        # with a runtime drop flag. Stamped on the BODY block (shared by the synthetic
        # perk-method wrapper) for the backend to read.
        self.branch_depth = 0
        self.conditional_moves = set()

        # Unit variables first, so a parameter or a `let` of the same name shadows one by
        # overwriting its entry. No `declared_at_span`: the declaration may stand in
        # another unit's file, and a note here would point into the wrong one.
        for name, sig in unit_variables(self):
            self.borrow_state[name] = BorrowState(name=name, var_type=sig.const_type,
                                                  is_unit_var=True)

        if self_type is not None:
            # A `poke self` / `peek self` receiver keeps its full ReferenceType, which
            # wires the rules in by construction (#327). A `nom self` receiver is not a
            # borrow at all: the method owns the value, so it registers as an ordinary
            # owned local and every read-only-receiver rule must miss it (ruling R25).
            mode = receiver_mode(self_mode)
            receiver_type = self_type
            if mode.by_pointer:
                receiver_type = ReferenceType(self_type, mode.borrow_mode)
            self._declare(BorrowState(name="self", var_type=receiver_type,
                                      declared_at_span=self_span,
                                      is_method_receiver=not mode.consumes),
                          is_borrow=not mode.consumes)

        for param in params:
            state = BorrowState(name=param.name, var_type=param.ty,
                                declared_at_span=getattr(param, "loc", None),
                                is_method_param=self_type is not None)
            # main's `string[] args` is a borrowed view of process argv (the runtime owns
            # and frees it). Stamp it so a by-value move is a hard error (CE2410), not a
            # silent move that makes the callee free argv (N2).
            if fn_name == "main" and self._is_argv_view_param(param.ty):
                state.is_argv_view = True
            # Every mode but `nom` is a borrow, in every kind of callable. Being a METHOD
            # used to be the condition, because a method was the only callable whose
            # parameters did not transfer (docs/design/borrow-model.md S1).
            self._declare(state, is_borrow=not param_mode(param).consumes)

        if params and params[0].name == ENV_PARAM_NAME:
            self._declare_captures(params[0])

        check_block(self, body)
        body.conditional_move_names = frozenset(self.conditional_moves)

    def _declare(self, state: BorrowState, *, is_borrow: bool) -> None:
        """Register a parameter, recording whether its declared mode is a borrow."""
        # A borrow parameter does not own its value -- `string` included (#338).
        state.is_borrow_param = is_borrow
        self.borrow_state[state.name] = state

    def _declare_captures(self, env: Param) -> None:
        """Give each captured variable of a lifted lambda the state of a `poke` borrow.

        The environment is a `poke` parameter, and a borrow of one captured field is
        counted on that field alone (`borrow_owner`, #1129).
        """
        env_type = env.ty.referenced_type if isinstance(env.ty, ReferenceType) else None
        if not isinstance(env_type, StructType):
            return
        for field_name, field_type in env_type.fields:
            self._declare(BorrowState(name=captured_key(field_name),
                                      var_type=ReferenceType(field_type, BorrowMode.POKE),
                                      declared_at_span=getattr(env, "loc", None)),
                          is_borrow=True)

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
