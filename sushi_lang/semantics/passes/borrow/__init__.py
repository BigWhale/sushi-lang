"""The borrow pass over SHIR (docs/design/ir.md 7.9). The pass object holds the state of one
body; the sibling modules hold the rules.

The lift pass gives the pass each body that typecheck and lift finished with no error,
with its `TypeckResults` (`check_body`). The DECLARATION half reads the declaration of the
body: the mode of each parameter, the receiver, a borrowed return. The BODY half walks the
SHIR body through one table of arms keyed on the SHIR class (`arms.py`), and writes each
answer as a row of the `OwnershipResults` of the body. The state of the walk is keyed by
`LocalId`, and a diagnostic names a local by the source name of its `ShirLocal`.

A branch starts each block from the state before it, and the paths that reach the code
after it JOIN (`flow.py`). A loop body is walked two rounds: a discovery round, which
reports nothing and writes no row, gives the facts that come round the back edge, and the
second round starts from the fixed point and reports.

A construct that the pass does not check yet stops the walk of its body with the stop code
CE0155 (`borrow, <class>`), and the pass records the stop. The body of a constant or `var`
initializer is walked as any other body: the constant evaluator makes its value, so a
constant that it names is read, not moved (`consume.source_provenance`). A body that the
walk finished with no error writes its conditionally moved locals, has its table
checked (`check_ownership`, CE0157), and goes to the stage after the pass, the SLIR
lowering (`slir/lower.py`).
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Callable, Dict, FrozenSet, Optional, Set

from sushi_lang.internals import errors as er
from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.error_reporter import PassErrorReporter
from sushi_lang.semantics.namespaces import NamespaceTable, body_namespaces
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.passes.collect.functions import GenericFuncDef
from sushi_lang.semantics.shir.body import TemplateCheck
from sushi_lang.semantics.shir.ownership_check import check_ownership
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType, ReferenceType, Type

# The table of arms first: the arm modules import it, and it imports them.
from . import arms  # noqa: F401
from .borrows import BY_ADDRESS
from .consume import binds_a_bare_literal_string
from .destroy_effects import compute_destroy_effects
from .flow import FlowFacts, LoopFrame, record_exit, terminates
from .methods import MUTATING_METHODS
from .state import BorrowState
from .statements import check_block
from .types import TypeQueries
from .writes import READONLY_RECEIVERS

if TYPE_CHECKING:
    from sushi_lang.semantics.shir import exprs as e
    from sushi_lang.semantics.shir.body import ShirBody, ShirLocal, ShirParam
    from sushi_lang.semantics.shir.ids import DeclId, LocalId
    from sushi_lang.semantics.shir.lowering import LoweredProgram
    from sushi_lang.semantics.shir.ownership import OwnershipResults
    from sushi_lang.semantics.shir.stop import Stops
    from sushi_lang.semantics.shir.typeck import TypeckResults
    from sushi_lang.semantics.tables import SymbolTables
    from .state import Owner

class BorrowChecker:
    """The borrow pass over the SHIR bodies of one unit, one body at a time."""

    def __init__(self, reporter: Reporter, *,
                 tables: SymbolTables,
                 shir: LoweredProgram,
                 stops: Stops,
                 next_stage: Callable[..., None],
                 destroy_effects: Dict[str, FrozenSet[int]],
                 namespaces: Optional[NamespaceTable] = None):
        self.reporter = reporter
        self.err = PassErrorReporter(reporter)
        # The tables that a type of the body is resolved in: the program's, or the overlay
        # of a template check (`check_body`).
        self.tables = tables
        self.types = TypeQueries(tables)
        self.shir = shir
        self.stops = stops
        self.next_stage = next_stage
        # fn name -> the poke param indices it destroys (#168), over EVERY unit, so a
        # cross-unit callee is not a blind spot (the `effects` pass).
        self.destroy_effects = destroy_effects
        # The names of the unit, and the names that the body resolves in (`_enter`): the
        # names of its unit, or of the library unit of a library copy (#1120).
        self.unit_names = namespaces if namespaces is not None else NamespaceTable()
        self.names = self.unit_names
        # The body that the pass walks, its tables and its locals.
        self.body: Optional[ShirBody] = None
        self.typeck: Optional[TypeckResults] = None
        self.ownership: Optional[OwnershipResults] = None
        self.locals: Dict[LocalId, ShirLocal] = {}
        # The text of the file of the body, for the source text of a node.
        self.source: Optional[str] = None
        # A `peek` return: the pass does not check its `return` yet.
        self.borrowed_return = False
        # The body of `main`, whose `string[] args` is a view of the process arguments.
        self.is_main = False
        self.borrow_state: Dict[Owner, BorrowState] = {}
        self.active_borrows: Set[Owner] = set()
        # One frame per open block; `check_block` pops it, which is what gives a
        # `let`-borrow a LEXICAL lifetime. `active_borrows` clears per statement.
        self._scope_binding_borrows: list[list[tuple[Owner, Owner]]] = []
        # The locals that each open block declared, the parameters in the first: an exit
        # of a block is an exit of each of them (`flow.record_exit`).
        self.block_locals: list[list[LocalId]] = []
        # One frame per loop that the walk is in: the paths of its `break` and `continue`.
        self.loop_frames: list[LoopFrame] = []
        # The count of the discovery rounds of loops that the walk is in.
        self.discovery_rounds = 0
        # Conditional-move tracking (#414): the locals moved on some path, and the locals
        # that an exit of their scope sees not moved on every path.
        self.moved_locals: Set[LocalId] = set()
        self.kept_at_an_exit: Set[LocalId] = set()

    def check_body(self, decl: object, body: ShirBody, *, tables: SymbolTables,
                   filename: Optional[str]) -> None:
        """Walk the SHIR body of `decl`, which typecheck and lift finished with no error.

        `tables` are the tables that the typecheck pass read the body in. A stop ends the
        walk: the pass records it with the file of the body. A walk with no stop and with
        no error checks the table of the body, and gives the body, with the tables that
        typecheck read it in, to the next stage, the SLIR lowering. A template check never
        goes to SLIR (docs/design/ir.md 4.3).
        """
        self._enter(decl, body, tables)
        offered = sum(self.reporter.errors_offered.values())
        has_receiver = bool(body.params) and self.name_of(body.params[0].local) == "self"
        # The check copy of a generic free function gives its parameters no span: the note
        # of CE2411 names the type parameter there.
        located = not (isinstance(body.origin, TemplateCheck)
                       and isinstance(decl, GenericFuncDef))
        try:
            for param in body.params:
                self._declare_param(param, has_receiver=has_receiver, located=located)
            self.block_locals.append([param.local for param in body.params])
            check_block(self, body.block)
            if not terminates(body.block):
                record_exit(self)
        except StageStop as stop:
            stop.filename = filename
            self.stops.borrow_stopped(stop)
            return
        finally:
            self._leave()
        if sum(self.reporter.errors_offered.values()) != offered:
            return
        ownership = self.shir.ownership_table_of(body)
        for local in sorted(self.moved_locals & self.kept_at_an_exit):
            ownership.record_conditional_move(local)
        check_ownership(body, self.shir.typeck_of(body), self.shir.lift_table_of(body),
                        ownership, self.shir.decls)
        if not isinstance(body.origin, TemplateCheck):
            self.next_stage(decl, body, tables=tables, filename=filename)

    def _enter(self, decl: object, body: ShirBody, tables: SymbolTables) -> None:
        self.tables, self.types = tables, TypeQueries(tables)
        self.names = (body_namespaces(decl, getattr(tables, "namespaces", None))
                      or self.unit_names)
        self.body = body
        self.typeck = self.shir.typeck_of(body)
        self.ownership = self.shir.ownership_table_of(body)
        self.locals = {local.id: local for local in body.locals}
        self.source = self.reporter.body_source()
        self.borrowed_return = getattr(decl, "borrowed_return", None) is not None
        self.is_main = self.shir.decls.name_of(body.fn_id) == "main"
        self.borrow_state = {}
        self.active_borrows = set()
        self._scope_binding_borrows = []
        self.block_locals = []
        self.loop_frames = []
        self.discovery_rounds = 0
        self.moved_locals = set()
        self.kept_at_an_exit = set()

    def _leave(self) -> None:
        self.body = self.typeck = self.ownership = None
        self.locals = {}
        self.source = None
        self.borrow_state = {}

    def answers(self) -> TypeckResults:
        """The `TypeckResults` of the body that the pass walks."""
        if self.typeck is None:
            er.raise_internal_error("CE0000", detail="the borrow pass walks no SHIR body")
        return self.typeck

    def table(self) -> OwnershipResults:
        """The `OwnershipResults` of the body that the pass walks."""
        if self.ownership is None:
            er.raise_internal_error("CE0000", detail="the borrow pass walks no SHIR body")
        return self.ownership

    def name_of(self, local: Owner) -> str:
        """The source name of a local or of a `var`, for a diagnostic."""
        if isinstance(local, str):
            return self.borrow_state[local].name
        shir_local = self.locals.get(local)
        if shir_local is None or shir_local.name is None:
            return f"_{local.index}"
        return shir_local.name

    def decl_name(self, decl: DeclId) -> str:
        """The written name of a declaration that a SHIR node names."""
        return self.shir.decls.name_of(decl)

    def constant_record(self, node: e.ConstRef) -> Any:
        """The record of the constant that `node` names in the scope of the body: a
        `ConstSig`, or the record of a stdlib constant. `typecheck` resolved the name, so a
        lookup that misses is an internal error."""
        record = self.names.constant_record(self.decl_name(node.const_id),
                                            self.tables.constants)
        if record is None:
            er.raise_internal_error("CE0000", span=node.loc,
                                    detail=f"the constant '{self.decl_name(node.const_id)}' "
                                           f"has no record in the borrow pass")
        return record

    def local_type(self, local: LocalId) -> Optional[Type]:
        return self.answers().local_type(local)

    def declare(self, state: BorrowState) -> None:
        """Give a local of the body its state, at its declaration."""
        self.borrow_state[state.local] = state

    def _declare_param(self, param: ShirParam, *, has_receiver: bool, located: bool) -> None:
        """A parameter: every mode but `nom` is a borrow, in every kind of callable.

        A `peek` or a `poke` parameter keeps the reference type, which wires the rules of a
        reference in by construction (#327). A `nom self` receiver is not a borrow at all:
        the method owns the value, so every read-only-receiver rule misses it (R25).
        """
        name = self.name_of(param.local)
        ty = self.local_type(param.local)
        mode = BY_ADDRESS.get(param.convention)
        var_type = ReferenceType(ty, mode) if mode is not None and ty is not None else ty
        is_borrow = param.convention is not Convention.OWNED
        receiver = name == "self" and has_receiver
        state = BorrowState(name=name, local=param.local, var_type=var_type,
                            declared_at_span=param.loc if located else None,
                            is_method_receiver=receiver and is_borrow,
                            is_method_param=has_receiver and not receiver,
                            is_borrow_param=is_borrow)
        # main's `string[] args` is a borrowed view of process argv (the runtime owns and
        # frees it): a by-value move of it is CE2410 (N2).
        state.is_argv_view = self.is_main and _is_argv_view(ty)
        self.declare(state)


def _is_argv_view(ty: Optional[Type]) -> bool:
    """True if `ty` is `string[]` -- the shape of main's borrowed argv parameter."""
    return isinstance(ty, DynamicArrayType) and ty.base_type == BuiltinType.STRING


__all__ = [
    'BorrowChecker',
    'BorrowState',
    'FlowFacts',
    'MUTATING_METHODS',
    'READONLY_RECEIVERS',
    'binds_a_bare_literal_string',
    'compute_destroy_effects',
]
