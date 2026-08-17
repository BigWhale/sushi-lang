"""Borrow checker for Sushi's reference system."""

from __future__ import annotations
from typing import Callable, Dict, FrozenSet, Iterable, Iterator, List, Set, Optional
from dataclasses import dataclass, field

from sushi_lang.semantics.ast import (
    ArrayLiteral,
    BinaryOp,
    BlankLit,
    Block,
    BoolLit,
    Borrow,
    Break,
    Call,
    CastExpr,
    Continue,
    DotCall,
    DynamicArrayFrom,
    DynamicArrayNew,
    EnumConstructor,
    Expr,
    ExprStmt,
    ExtendDef,
    FloatLit,
    Foreach,
    FuncDef,
    If,
    IndexAccess,
    InterpolatedString,
    IntLit,
    Lambda,
    Let,
    Match,
    MemberAccess,
    MethodCall,
    Name,
    Param,
    Pattern,
    Print,
    PrintLn,
    Program,
    RangeExpr,
    RefBinding,
    Rebind,
    Return,
    Spread,
    Stmt,
    StringLit,
    TryExpr,
    UnaryOp,
    While,
)
from sushi_lang.semantics.typesys import BorrowMode, ReferenceType, DynamicArrayType, Type
from sushi_lang.semantics.param_modes import (
    CalleeKind, CalleeModes, ParamMode, param_mode,
)
from sushi_lang.semantics.ownership import (
    ConsumingUse, Ownership, Provenance, TypeClass, classify, is_get_out_container,
    type_class_of,
)
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors.registry import ErrorMessage
from sushi_lang.semantics.error_reporter import PassErrorReporter


# Nodes that own nothing and name nothing. Listed EXPLICITLY so `_check_expr`'s `else`
# can be a hard error (CE0125) instead of a silent skip.
_INERT_EXPRS = (IntLit, FloatLit, BoolLit, BlankLit, StringLit, DynamicArrayNew)


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


@dataclass
class BorrowState:
    """Tracks the borrow state of a single variable."""
    name: str
    var_type: Optional[Type] = None  # Variable type (for move semantics)
    poke_borrow_count: int = 0  # Number of active poke borrows (max 1)
    peek_borrow_count: int = 0  # Number of active peek borrows (unlimited)
    is_moved: bool = False  # Ownership has been transferred
    is_destroyed: bool = False  # Variable has been explicitly destroyed (via .destroy())
    is_argv_view: bool = False  # main's `string[] args`: a borrowed view of process argv;
                                # moving it by value would free argv, so it is a hard error
    is_borrowed_binding: bool = False  # A `match` payload binding, a `foreach` item, or a
                                # `let` bound from a read through a live owner: a
                                # read-only borrow (ownership-conventions.md S8).
    is_let_borrow: bool = False  # ...and this one is the `let` spelling specifically
                                # (#242). Narrower than is_borrowed_binding for the
                                # DIAGNOSTIC: a match binding is a private deep copy so a
                                # write is only lost (CE2414), while a `let`-borrow shares
                                # the owner's data so a reallocating write double-frees
                                # (CE2426, #344).
                                #
                                # NOT `borrows_from is not None`: a binding out of a
                                # temporary records no owner name and is still a
                                # `let`-borrow.
    is_borrow_param: bool = False  # A parameter whose declared MODE is a borrow -- i.e.
                                # anything but `nom`, in any callable, `self` included:
                                # a write cannot reach the caller (CE2421 / CE2422) and
                                # consuming an owning one gives it a second owner (CE2411).
                                # See docs/design/borrow-model.md S1.
                                #
                                # Its own kind, not a flavour of is_borrowed_binding: a
                                # parameter is a SHALLOW copy aliasing the caller's heap,
                                # so the same write was a double free (#326).
    is_method_receiver: bool = False  # ...and this one is `self` specifically. The narrower
                                # flag is for the DIAGNOSTIC, not the rule: the two
                                # escapes differ (`poke self` vs `poke T`), so the two
                                # carry different codes and different help.
    owns_no_heap: bool = False  # Option B: this binding's CURRENT value owns no
                                # heap, so consuming it transfers nothing. Only a `string`
                                # bound from a literal sets it.
                                #
                                # On the BINDING and not the type on purpose:
                                # `BuiltinType.STRING` is an enum member with nowhere to put
                                # a flag. Do not "fix" it by inventing a string subtype.
                                #
                                # RE-DERIVED on every rebind, never inherited. Default False
                                # means "assume it owns heap".
    bound_at_span: Optional[Span] = None  # Where the binding was introduced. CE2411 is a
                                # RELATIONAL error -- the use is only wrong BECAUSE of what
                                # the binding borrows from -- so it renders both.
    declared_at_span: Optional[Span] = None  # Where this variable was introduced. CE2411
                                # for a read THROUGH an owner points here as its second
                                # location: the error exists only because this owner keeps
                                # the value, so the ladder's tier 3 needs it.
    borrows_from: Optional[str] = None  # The root owner this binding reads out of, for a
                                # `let`-borrow binding (#242). `let x = c.get(0)??` names
                                # `c`. None for a `match` / `foreach` binding, whose owner
                                # is the scrutinee expression rather than a named local.
    invalidated_at: Optional[Span] = None  # On a `let`-borrow BINDING: where its owner was
                                # changed or released. Set rather than reported, so CE2412
                                # fires only on a read AFTER it (non-lexical lifetimes).
    invalidated_by: tuple = ()  # (owner name, what the change was), for that diagnostic.
    binding_borrows: list = field(default_factory=list)  # On the OWNER: every live
                                # `let`-borrow binding reading out of it. Mutating the
                                # owner while this is non-empty is CE2412.
                                #
                                # NOT one of the counters above: those clear per statement,
                                # while a binding borrow lives to the end of its lexical
                                # scope, so `_check_block` releases it.
    first_borrow_span: Optional[Span] = None  # Location of the first active borrow
    moved_at_span: Optional[Span] = None  # Where ownership was transferred away.
                                          # Use-after-move is a RELATIONAL error: the
                                          # use is only wrong BECAUSE of the move, so
                                          # CE2405 points at both.

    @property
    def is_borrowed(self) -> bool:
        """Returns True if variable has any active borrows."""
        return self.poke_borrow_count > 0 or self.peek_borrow_count > 0


@dataclass(frozen=True)
class FlowFacts:
    """The per-variable facts that must survive a branch join or a loop back edge.

    Which join rule a field takes is the whole design. `moved`, `destroyed` and
    `invalidation` are monotone and join by UNION, so a loop converges in two passes.
    `owns_no_heap` GRANTS permission, so it joins by INTERSECTION -- believable after the
    join only if it held on every path; union would be unsound. Because intersection has no
    empty identity, paths go through `join()` over the surviving list, never a fold into a
    blank `FlowFacts()`.
    """
    moved: frozenset[str] = frozenset()
    destroyed: frozenset[str] = frozenset()
    owns_no_heap: frozenset[str] = frozenset()
    # A tuple, not a frozenset: `Span` is an unfrozen dataclass and so unhashable. The
    # span must travel with the flag, or CE2412 renders with no location.
    invalidation: tuple = ()

    def __or__(self, other: "FlowFacts") -> "FlowFacts":
        """Join two paths. Union for the monotone facts, intersection for permission."""
        seen = {name for name, _span, _by in self.invalidation}
        merged = self.invalidation + tuple(
            entry for entry in other.invalidation if entry[0] not in seen
        )
        return FlowFacts(
            moved=self.moved | other.moved,
            destroyed=self.destroyed | other.destroyed,
            owns_no_heap=self.owns_no_heap & other.owns_no_heap,
            invalidation=merged,
        )

    @staticmethod
    def join(paths: list["FlowFacts"]) -> "FlowFacts":
        """Join every surviving path of a branch."""
        if not paths:
            return FlowFacts()
        result = paths[0]
        for facts in paths[1:]:
            result = result | facts
        return result


# Methods that change or release what a container holds, so each is CE2412 for a live
# `let`-borrow out of the receiver. ONE set: a name missing from a copy would be a silent
# dangling borrow, not a wrong diagnostic.
_MUTATING_METHODS = frozenset({
    "push", "pop", "insert", "remove", "clear", "reserve", "shrink_to_fit",
    "rehash", "destroy", "free", "fill", "reverse",
})


@dataclass(frozen=True)
class _ReadOnlyReceiver:
    """One kind of receiver a write cannot reach through, as DATA."""
    code: ErrorMessage                                 # the registry entry (er.ERR.CExxxx)
    matches: Callable[["BorrowState"], bool]           # is the state this kind?
    note_span: Callable[["BorrowState"], Optional[Span]]  # where the kind was introduced
    note: str
    help: str


# The five kinds, most specific first. They are disjoint by construction -- the receiver is
# never a reference parameter, neither is ever a binding, and the two binding rows split on
# `is_let_borrow` -- so the order is documentation rather than precedence.
_READONLY_RECEIVERS: tuple[_ReadOnlyReceiver, ...] = (
    _ReadOnlyReceiver(
        # `and not ReferenceType`: a `poke self` receiver (#327) is WRITABLE and must
        # fall through this row; a `peek self` receiver falls to the CE2408 row below,
        # which names its actual mode.
        code=er.ERR.CE2421,
        matches=lambda state: (state.is_method_receiver
                               and not isinstance(state.var_type, ReferenceType)),
        note_span=lambda state: state.declared_at_span,
        note="'{name}' is the receiver of a method on this type, a read-only borrow",
        help="the write ({what}) would land on the method's private copy of the "
             "receiver; declare the receiver mutable -- `(poke self, ...)` -- and "
             "the write reaches the caller (#327), or return the new value and let "
             "the caller store it",
    ),
    _ReadOnlyReceiver(
        # A reference parameter carries its full `ReferenceType` as `var_type`, so the
        # question is answerable here and nowhere else: Pass 2 unwraps a reference at
        # every mention, so no inferred type downstream can tell a borrow from a value.
        code=er.ERR.CE2408,
        matches=lambda state: (isinstance(state.var_type, ReferenceType)
                               and state.var_type.is_peek()),
        note_span=lambda state: state.declared_at_span,
        note="'{name}' is declared here as a read-only borrow",
        help="the write ({what}) would change the caller's value through a read-only "
             "borrow; declare the parameter `poke` if the callee must write, or take "
             "an independent value with `{name}.clone()`",
    ),
    _ReadOnlyReceiver(
        # AFTER the `peek` row and excluding every reference parameter, so `peek` keeps
        # its own code and `poke` stays writable -- the escape this code names (#298).
        code=er.ERR.CE2422,
        matches=lambda state: (state.is_borrow_param
                               and not state.is_method_receiver
                               and not isinstance(state.var_type, ReferenceType)),
        note_span=lambda state: state.declared_at_span,
        note="'{name}' is declared here, as a by-value parameter of a method",
        help="the write ({what}) would land on the method's private copy of the "
             "argument; declare the parameter `poke` if the method must write through "
             "it, or take an independent value with `{name}.clone()`",
    ),
    _ReadOnlyReceiver(
        # `and not is_let_borrow`: a match binding is a private deep copy, so the write
        # is only lost. A `let`-borrow shares the owner's data and gets CE2426 below.
        code=er.ERR.CE2414,
        matches=lambda state: (state.is_borrowed_binding
                               and not state.is_let_borrow),
        note_span=lambda state: state.bound_at_span,
        note="'{name}' is bound here, as a read-only view",
        help="the write ({what}) would land on a private copy and be lost; take an "
             "independent value with `{name}.clone()`, mutate it, and store it back "
             "into the owner",
    ),
    _ReadOnlyReceiver(
        # The fifth kind (#344). CE2412 asks "may I mutate the OWNER while the binding
        # lives?"; this asks "may I write THROUGH the binding?" -- complementary, not
        # alternatives. The write reaches storage the owner keeps, so a reallocating
        # `.push()` frees the owner's buffer twice.
        #
        # Keyed on `is_let_borrow`, NOT `borrows_from is not None`: a binding out of a
        # temporary records no owner name.
        code=er.ERR.CE2426,
        matches=lambda state: state.is_let_borrow,
        note_span=lambda state: state.bound_at_span,
        note="'{name}' is bound here, borrowing storage its owner keeps",
        help="the write ({what}) reaches storage another value owns and still frees, so "
             "it is lost from the owner's view and a reallocating write frees the "
             "owner's buffer; write to the owner directly, or take an independent value "
             "with `{name}.clone()`, mutate it, and store it back",
    ),
)


def binds_a_bare_literal_string(declared_ty, init) -> bool:
    """Option B: is this binding a `string` whose value is a plain literal?"""
    from sushi_lang.semantics.ast import StringLit
    from sushi_lang.semantics.typesys import BuiltinType
    return declared_ty == BuiltinType.STRING and isinstance(init, StringLit)


def _split_type_args(args: str) -> list[str]:
    """Split an interned type-argument list on its TOP-LEVEL commas."""
    parts, depth, current = [], 0, ""
    for ch in args:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def _iter_stmts(block: Block) -> Iterator[Stmt]:
    """Every statement in a body, descending into nested blocks."""
    for stmt in block.statements:
        yield stmt
        if isinstance(stmt, If):
            for _cond, arm in stmt.arms:
                yield from _iter_stmts(arm)
            if stmt.else_block:
                yield from _iter_stmts(stmt.else_block)
        elif isinstance(stmt, (While, Foreach)):
            yield from _iter_stmts(stmt.body)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                if isinstance(arm.body, Block):
                    yield from _iter_stmts(arm.body)


def _leading_call(expr: Optional[Expr]) -> Optional[Call]:
    """The Call an expression evaluates to, unwrapping `??` -- None if it is not a call."""
    while isinstance(expr, TryExpr):
        expr = expr.expr
    return expr if isinstance(expr, Call) else None


def _statement_call(stmt: Stmt) -> Optional[Call]:
    """The call a statement makes, for the shapes a `poke` argument can appear in."""
    if isinstance(stmt, ExprStmt):
        return _leading_call(stmt.expr)
    if isinstance(stmt, (Let, Rebind)):
        return _leading_call(stmt.value)
    return None


def _poke_param_indices(func: FuncDef) -> Dict[str, int]:
    """`poke` parameters of `func`, by name -> positional index."""
    return {
        param.name: i
        for i, param in enumerate(func.params)
        if isinstance(param.ty, ReferenceType) and param.ty.is_poke()
    }


def compute_destroy_effects(programs: Iterable[Program]) -> Dict[str, FrozenSet[int]]:
    """`fn name -> the poke parameter indices it destroys`, transitively (#168).

    The one inter-procedural analysis in semantics, and deliberately an UNDER-approximation:
    it can miss a destroy but never invent one, so it cannot produce a false CE2406. Known
    misses: a generic callee (monomorphized fns are not in `program.functions`), an
    extension/perk method destroying its implicit `self` (not in `func.params`), and a call
    nested anywhere but a statement's leading expression.
    """
    funcs: Dict[str, FuncDef] = {}
    for program in programs:
        for func in program.functions:
            funcs.setdefault(func.name, func)

    effects: Dict[str, Set[int]] = {}

    for name, func in funcs.items():
        poke = _poke_param_indices(func)
        destroyed: Set[int] = set()
        for stmt in _iter_stmts(func.body):
            if not isinstance(stmt, ExprStmt):
                continue
            call = stmt.expr
            if isinstance(call, (MethodCall, DotCall)) and call.method == "destroy":
                if isinstance(call.receiver, Name) and call.receiver.id in poke:
                    destroyed.add(poke[call.receiver.id])
        effects[name] = destroyed

    changed = True
    while changed:
        changed = False
        for name, func in funcs.items():
            poke = _poke_param_indices(func)
            if not poke:
                continue
            for stmt in _iter_stmts(func.body):
                call = _statement_call(stmt)
                if call is None or not isinstance(call.callee, Name):
                    continue
                for index in effects.get(call.callee.id, ()):
                    if index >= len(call.args):
                        continue
                    arg = call.args[index]
                    if isinstance(arg, Borrow):
                        arg = arg.expr
                    if isinstance(arg, Name) and arg.id in poke:
                        own_index = poke[arg.id]
                        if own_index not in effects[name]:
                            effects[name].add(own_index)
                            changed = True

    return {name: frozenset(indices) for name, indices in effects.items() if indices}


class BorrowChecker:
    """Analyzes borrowing safety for a program."""

    # The read-only receiver kinds, as data. Exposed on the class so
    # `tests/unit/test_readonly_receiver_matrix.py` can assert that every kind in the
    # table has a row in the matrix -- a kind added here without one is a red test.
    _READONLY_RECEIVERS = _READONLY_RECEIVERS

    def __init__(self, reporter: Reporter,
                 destroy_effects: Optional[Dict[str, FrozenSet[int]]] = None,
                 enum_names: Optional[Set[str]] = None,
                 tables=None):
        self.reporter = reporter
        # Used only to RESOLVE a named type before classifying it: `owns_heap` answers
        # False for an UnknownType, so without this an owning struct would alias.
        self.tables = tables
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
        # One frame per open block; `_check_block` pops it, which is what gives a
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

        self._check_block(body)

    @staticmethod
    def _mark_borrow_param(state: BorrowState) -> None:
        """A parameter whose mode is a borrow does not own its value -- `string` included."""
        state.is_borrow_param = True

    @staticmethod
    def _is_argv_view_param(ty: Optional[Type]) -> bool:
        """True if `ty` is `string[]` -- the shape of main's borrowed argv parameter."""
        from sushi_lang.semantics.typesys import BuiltinType
        return isinstance(ty, DynamicArrayType) and ty.base_type == BuiltinType.STRING

    def _check_block(self, block: Block) -> None:
        """Check borrow safety for a block of statements."""
        self._scope_binding_borrows.append([])
        try:
            for stmt in block.statements:
                self._check_stmt(stmt)
        finally:
            for owner, binding in self._scope_binding_borrows.pop():
                state = self.borrow_state.get(owner)
                if state is not None:
                    state.binding_borrows = [
                        entry for entry in state.binding_borrows if entry[0] != binding
                    ]

    def _check_stmt(self, stmt: Stmt) -> None:
        """Check borrow safety for a single statement."""
        if isinstance(stmt, Let):
            from sushi_lang.semantics.typesys import ForeignPtrType
            if isinstance(stmt.ty, ForeignPtrType):
                # A foreign `ptr` is exempt from ALIASING analysis but NOT from the
                # ownership stamp: skipping the stamp is CE0129 on the first FFI program
                # that binds one.
                self.borrow_state[stmt.name] = BorrowState(
                    name=stmt.name, var_type=stmt.ty, declared_at_span=stmt.loc)
                self._bind(stmt)
                self._clear_borrows()
                return
            self.borrow_state[stmt.name] = BorrowState(
                name=stmt.name, var_type=stmt.ty, declared_at_span=stmt.loc,
                # Option B: a string bound straight from a literal owns no heap,
                # so consuming it transfers nothing and CE2405 must not fire on it.
                owns_no_heap=binds_a_bare_literal_string(stmt.ty, stmt.value))
            self._check_expr(stmt.value)
            self._reconcile_closure_bind(stmt)
            # A `let` BINDS; it does not take ownership (#242). It inherits the source's
            # provenance, so a read through a live owner makes it a BORROW.
            self._bind(stmt)
            self._clear_borrows()

        elif isinstance(stmt, Rebind):
            # Variable or field rebinding - check if source is borrowed
            # For simple rebind (x := value), target is a Name
            # For field rebind (obj.field := value), target is a MemberAccess
            if isinstance(stmt.target, Name):
                var_name = stmt.target.id
                if var_name in self.borrow_state:
                    state = self.borrow_state[var_name]

                    if isinstance(state.var_type, ReferenceType):
                        if state.var_type.is_peek():
                            self.err.emit(er.ERR.CE2408, stmt.loc, name=var_name)
                        # poke references allow rebind (mutable reference semantics)
                    #
                    # No "rebind while borrowed" check here, deliberately: this runs BEFORE
                    # the value walk, and moving it after would reject `x := f(peek x)`.
                    # CE2401 lives at the consuming use instead.

                    # Option B: RE-DERIVE, never inherit. A rebind can only CLEAR this
                    # flag, never set it on a value that owns heap.
                    state.owns_no_heap = binds_a_bare_literal_string(
                        state.var_type, stmt.value)

            elif isinstance(stmt.target, MemberAccess):
                # A field rebind mutates in place, so it is allowed unless the root owner
                # is a read-only receiver, where the store cannot reach what it writes.
                self._reject_readonly_write(self._root_owner(stmt.target), stmt.loc,
                                            "assign to a field")
                self._check_expr(stmt.target)

            self._check_expr(stmt.value)
            # Both rebind shapes take ownership, and replacing what the owner holds
            # invalidates every binding reading out of it (#242).
            self._check_owner_not_borrowed(
                self._root_owner(stmt.target), stmt.loc, "assign")
            if isinstance(stmt.target, Name):
                self._consume(stmt.value, ConsumingUse.REBIND)
                # A rebind RE-INITIALIZES, so a previous move no longer holds. The value
                # was checked above, so `s := "{s}-x"` still reports the moved `s`.
                target_state = self.borrow_state.get(stmt.target.id)
                if target_state is not None:
                    self._reinitialize(target_state)
            elif isinstance(stmt.target, MemberAccess):
                self._consume(stmt.value, ConsumingUse.FIELD_ASSIGN)
            self._clear_borrows()

        elif isinstance(stmt, Return):
            self._check_expr(stmt.value)
            # A return hands the value to the caller. `return Result.Ok(x)` consumes `x`
            # at ENUM_PAYLOAD and the constructor itself is FRESH, so this matters for the
            # shape that is not wrapped: an extension method's bare `return value`.
            self._consume(stmt.value, ConsumingUse.RETURN)
            self._clear_borrows()

        elif isinstance(stmt, Print) or isinstance(stmt, PrintLn):
            self._check_expr(stmt.value)
            self._clear_borrows()

        elif isinstance(stmt, ExprStmt):
            self._check_expr(stmt.expr)
            # `x.destroy()` releases what `x` holds, so every later use is CE2406.
            #
            # No "destroy while borrowed" check (the retired CE2402): `.destroy()` returns
            # `~`, so it is always its own statement and the counters are already clear.
            # CE2408, CE2412 and CE2406 cover its intent.
            if isinstance(stmt.expr, (MethodCall, DotCall)):
                if stmt.expr.method == "destroy":
                    if isinstance(stmt.expr.receiver, Name):
                        state = self.borrow_state.get(stmt.expr.receiver.id)
                        if state is not None:
                            state.is_destroyed = True
            self._clear_borrows()

        elif isinstance(stmt, If):
            # Every arm starts from the pre-if state and the results JOIN: moved after the
            # `if` iff moved on ANY path. Without the snapshot a move leaks into siblings.
            entry = self._snapshot_flow()
            # Only the paths that REACH the code after the `if` contribute to the join. An
            # arm that ends in `return` leaves the function, so its move cannot reach a
            # sibling arm or the statements below (#287).
            paths: list[FlowFacts] = []
            for cond_expr, arm_block in stmt.arms:
                self._restore_flow(entry)
                self._check_expr(cond_expr)
                self._clear_borrows()
                self._check_block(arm_block)
                if not self._terminates(arm_block):
                    paths.append(self._snapshot_flow())
            if stmt.else_block:
                self._restore_flow(entry)
                self._check_block(stmt.else_block)
                if not self._terminates(stmt.else_block):
                    paths.append(self._snapshot_flow())
            else:
                paths.append(entry)
            self._restore_flow(FlowFacts.join(paths))

        elif isinstance(stmt, While):
            self._check_expr(stmt.cond)
            self._clear_borrows()
            self._check_loop_body(stmt.body)

        elif isinstance(stmt, Foreach):
            self._check_expr(stmt.iterable)
            self._clear_borrows()
            # The loop variable BORROWS the element, matching the backend's
            # `register_cleanup=False`. It lives for the LOOP and no longer (#337), so it
            # goes through the displaced-entry bracket and an outer local it shadows gets
            # its state back.
            displaced: dict = {}
            frozen: list = []
            span = stmt.item_name_span or stmt.loc
            if stmt.item_borrow is not None:
                # A reference binding (#300): the state carries the full `ReferenceType`,
                # which wires every rule in by construction. NOT `is_borrowed_binding` --
                # that would put a `poke` binding in the CE2414 row and reject the write
                # the marker exists to allow.
                mode = BorrowMode.POKE if stmt.item_borrow == "poke" else BorrowMode.PEEK
                state = BorrowState(
                    name=stmt.item_name,
                    var_type=ReferenceType(stmt.item_type, mode),
                    bound_at_span=span, declared_at_span=stmt.item_borrow_span or span,
                )
                self._register_binding(stmt.item_name, state, displaced)
                self._freeze_ref_binding_owner(state, stmt.iterable, span, frozen,
                                               poke_span=stmt.item_borrow_span)
            else:
                self._register_binding(
                    stmt.item_name,
                    BorrowState(
                        name=stmt.item_name, var_type=stmt.item_type,
                        is_borrowed_binding=True,
                        bound_at_span=span,
                    ),
                    displaced,
                )
            try:
                self._check_loop_body(stmt.body)
            finally:
                self._restore_displaced(displaced)
                self._release_frozen(frozen)

        elif isinstance(stmt, Match):
            self._check_expr(stmt.scrutinee)
            self._clear_borrows()
            # Match arms are EXCLUSIVE paths, so they take the same snapshot / restore /
            # join as the `If` arm above.
            entry = self._snapshot_flow()
            paths: list[FlowFacts] = []
            for arm in stmt.arms:
                self._restore_flow(entry)
                # The scrutinee type gives each binding its var_type; Pass 2 stamps it
                # (CE0121 guards that).
                #
                # A binding lives for its ARM and no longer (#337), so registration goes
                # through the displaced-entry bracket. The restore runs BEFORE the path
                # snapshot, so the join sees the outer local's facts, never the
                # binding's.
                displaced: dict = {}
                frozen: list = []
                if isinstance(arm.pattern, Pattern):
                    self._register_pattern_bindings(
                        arm.pattern, stmt.resolved_scrutinee_type, displaced,
                        scrutinee=stmt.scrutinee, frozen=frozen)
                try:
                    if isinstance(arm.body, Block):
                        self._check_block(arm.body)
                    else:
                        self._check_expr(arm.body)
                        self._clear_borrows()
                finally:
                    self._restore_displaced(displaced)
                    self._release_frozen(frozen)
                if not self._terminates(arm.body):
                    paths.append(self._snapshot_flow())
            # A `match` is exhaustive (Pass 2 enforces it), so unlike an `if` with no else
            # there is no fall-through path to add: some arm always runs.
            self._restore_flow(FlowFacts.join(paths))

        elif isinstance(stmt, Break) or isinstance(stmt, Continue):
            pass  # No borrow checking needed

    @staticmethod
    def _reinitialize(state: "BorrowState") -> None:
        """A rebind RE-INITIALIZES the binding: every fact about the OLD value is stale."""
        state.is_moved = False
        state.moved_at_span = None
        state.is_destroyed = False
        state.invalidated_at = None
        state.invalidated_by = ()
        state.is_borrowed_binding = False
        state.is_let_borrow = False
        state.borrows_from = None

    @classmethod
    def _terminates(cls, node) -> bool:
        """Does every path through this statement (or block) leave the function?"""
        if isinstance(node, Return):
            return True
        if isinstance(node, Block):
            # Any terminating statement terminates the block. Later statements are
            # unreachable; they are still checked, which over-checks and never under-checks.
            return any(cls._terminates(stmt) for stmt in node.statements)
        if isinstance(node, If):
            if not node.else_block:
                return False
            return (all(cls._terminates(arm) for _cond, arm in node.arms)
                    and cls._terminates(node.else_block))
        if isinstance(node, Match):
            arms = getattr(node, "arms", ())
            return bool(arms) and all(cls._terminates(arm.body) for arm in arms)
        return False

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

    def _check_loop_body(self, body: Block) -> None:
        """Borrow-check a loop body to a fixed point so the back edge is honoured."""
        entry = self._snapshot_flow()
        prev_suppressed = self.err.suppressed
        self.err.suppressed = True
        self._check_block(body)
        self.err.suppressed = prev_suppressed
        fixed_point = entry | self._snapshot_flow()
        self._restore_flow(fixed_point)
        self._check_block(body)
        self._restore_flow(fixed_point)

    def _register_binding(self, name: str, state: BorrowState,
                          displaced: dict) -> None:
        """Install a pattern/foreach binding, saving whatever entry it shadows (#337)."""
        if name not in displaced:
            displaced[name] = self.borrow_state.get(name)
        self.borrow_state[name] = state

    def _restore_displaced(self, displaced: dict) -> None:
        """End the bindings a `_register_binding` bracket installed (#337)."""
        for name, previous in displaced.items():
            if previous is None:
                self.borrow_state.pop(name, None)
            else:
                self.borrow_state[name] = previous

    def _freeze_ref_binding_owner(self, state: BorrowState, source: Expr,
                                  span: Optional[Span], frozen: list,
                                  poke_span: Optional[Span] = None) -> None:
        """Give a reference binding (#300) the owner freeze a `let`-borrow gets (#242)."""
        owner = self._root_owner(source)
        if owner is None:
            return
        owner_state = self.borrow_state.get(owner)
        if owner_state is None:
            return
        if (isinstance(state.var_type, ReferenceType) and state.var_type.is_poke()
                and isinstance(owner_state.var_type, ReferenceType)
                and owner_state.var_type.is_peek()):
            diag = self.err.emit_with(er.ERR.CE2408, poke_span or span, name=owner)
            if owner_state.declared_at_span is not None:
                diag.note(f"'{owner}' is declared here as a read-only borrow",
                          owner_state.declared_at_span)
            diag.help("a `poke` element binding would write the caller's container "
                      "through a read-only borrow; declare the parameter "
                      "`poke` if the elements must be written, or drop the marker "
                      "and bind the element by value")
            diag.emit()
            return
        state.borrows_from = owner
        owner_state.binding_borrows.append((state.name, span))
        frozen.append((owner, state.name))

    def _release_frozen(self, frozen: list) -> None:
        """End the owner freezes `_freeze_ref_binding_owner` installed."""
        for owner, binding in frozen:
            owner_state = self.borrow_state.get(owner)
            if owner_state is not None:
                owner_state.binding_borrows = [
                    entry for entry in owner_state.binding_borrows if entry[0] != binding
                ]

    def _register_pattern_bindings(self, pattern: Pattern,
                                   scrutinee_type: Optional[Type] = None,
                                   displaced: Optional[dict] = None,
                                   scrutinee: Optional[Expr] = None,
                                   frozen: Optional[list] = None) -> None:
        """Register a match arm's payload bindings, WITH their types."""
        if displaced is None:
            displaced = {}
        variant_types = self._variant_payload_types(scrutinee_type, pattern.variant_name)
        span = pattern.variant_name_span or pattern.loc

        for index, binding in enumerate(pattern.bindings):
            payload_type = variant_types[index] if index < len(variant_types) else None
            if isinstance(binding, str):
                if binding != "_":  # Skip wildcard bindings
                    self._register_binding(binding, BorrowState(
                        name=binding, var_type=payload_type,
                        is_borrowed_binding=True, bound_at_span=span,
                    ), displaced)
            elif isinstance(binding, RefBinding):
                # `Variant(poke x)` (#300): a REFERENCE into the scrutinee's payload. The
                # scrutinee is frozen for the arm -- rebinding it would change the variant
                # tag under the pointer.
                mode = BorrowMode.POKE if binding.mode == "poke" else BorrowMode.PEEK
                ref_span = binding.loc or span
                state = BorrowState(
                    name=binding.name,
                    var_type=ReferenceType(payload_type, mode),
                    bound_at_span=ref_span, declared_at_span=ref_span,
                )
                self._register_binding(binding.name, state, displaced)
                if scrutinee is not None:
                    # The pointer aims INTO the scrutinee's storage, so the scrutinee must
                    # HAVE storage. A temporary has none: the write would go nowhere.
                    if not isinstance(scrutinee, Name):
                        self.err.emit(er.ERR.CE2404, ref_span,
                                      expr=self._expr_to_string(scrutinee))
                    elif frozen is not None:
                        self._freeze_ref_binding_owner(
                            state, scrutinee, ref_span, frozen, poke_span=ref_span)
            elif isinstance(binding, Pattern):
                self._register_pattern_bindings(binding, payload_type, displaced,
                                                scrutinee=scrutinee, frozen=frozen)
            else:
                inner = getattr(binding, "inner_pattern", None)
                inner_borrow = getattr(binding, "inner_borrow", None)
                if isinstance(inner, Pattern):
                    self._register_pattern_bindings(
                        inner, self._own_payload(payload_type), displaced,
                        scrutinee=scrutinee, frozen=frozen)
                elif isinstance(inner, str) and inner != "_":
                    if inner_borrow is not None:
                        # `Own(poke x)` (#300): a REFERENCE to the pointee, and the
                        # owner is frozen for the arm like a `let`-borrow's (#242).
                        mode = (BorrowMode.POKE if inner_borrow == "poke"
                                else BorrowMode.PEEK)
                        borrow_span = getattr(binding, "inner_borrow_span", None) or span
                        state = BorrowState(
                            name=inner,
                            var_type=ReferenceType(self._own_payload(payload_type), mode),
                            bound_at_span=borrow_span, declared_at_span=borrow_span,
                        )
                        self._register_binding(inner, state, displaced)
                        if scrutinee is not None and frozen is not None:
                            self._freeze_ref_binding_owner(
                                state, scrutinee, borrow_span, frozen,
                                poke_span=borrow_span)
                    else:
                        self._register_binding(inner, BorrowState(
                            name=inner, var_type=self._own_payload(payload_type),
                            is_borrowed_binding=True, bound_at_span=span,
                        ), displaced)

    def _variant_payload_types(self, enum_type: Optional[Type],
                               variant_name: str) -> tuple:
        """The associated types of `variant_name`, or () when the enum is not resolved."""
        from sushi_lang.semantics.typesys import EnumType as _EnumType
        resolved = self._resolve_named(enum_type)
        if not isinstance(resolved, _EnumType):
            return ()
        variant = resolved.get_variant(variant_name)
        return tuple(variant.associated_types) if variant is not None else ()

    def _own_payload(self, ty: Optional[Type]) -> Optional[Type]:
        """The `T` inside an `Own@(T)`, for an OwnPattern's inner binding."""
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.typesys import PointerType, StructType as _StructType
        ty = self._resolve_named(ty)
        if isinstance(ty, GenericTypeRef) and ty.base_name == "Own" and ty.type_args:
            return ty.type_args[0]
        if isinstance(ty, _StructType) and ty.name.startswith("Own<") and ty.fields:
            value_field = ty.fields[0][1]
            if isinstance(value_field, PointerType):
                return value_field.pointee_type
        return None

    def _resolve_named(self, ty: Optional[Type]):
        """Resolve an `UnknownType` against the struct/enum tables; identity otherwise."""
        from sushi_lang.semantics.typesys import UnknownType
        if not isinstance(ty, UnknownType) or self.tables is None:
            return ty
        structs = getattr(getattr(self.tables, "structs", None), "by_name", None) or {}
        enums = getattr(getattr(self.tables, "enums", None), "by_name", None) or {}
        return structs.get(ty.name) or enums.get(ty.name) or ty

    def _check_expr(self, expr: Expr) -> None:
        """Check borrow safety for an expression."""
        if isinstance(expr, Borrow):
            self._check_borrow(expr)

        elif isinstance(expr, Name):
            if expr.id in self.borrow_state:
                state = self.borrow_state[expr.id]
                if state.is_moved:
                    self._emit_use_after_move(expr.id, expr.loc, state)
                elif state.is_destroyed:
                    self.err.emit(er.ERR.CE2406, expr.loc, name=expr.id)
                elif state.invalidated_at is not None:
                    # A `let`-borrow binding read after its owner changed (#242).
                    self._emit_use_of_invalidated_borrow(expr.id, expr.loc, state)

        elif isinstance(expr, Call):
            self._check_expr(expr.callee)
            for arg in expr.args:
                self._check_expr(arg)

            # An argument is consumed if and only if the parameter it lands on DECLARES
            # a consume. The callee's kind decides where the declaration is read from
            # (docs/design/borrow-model.md S5); the mode decides what happens.
            self._consume_call_args(expr)

            # A callee that destroys its `poke` parameter destroys the CALLER's value
            # (#168). CE2406 still fires from the Name arm above -- no new emit site.
            self._apply_destroy_effects(expr)

        elif isinstance(expr, MethodCall):
            self._check_expr(expr.receiver)
            for arg in expr.args:
                self._check_expr(arg)
            self._maybe_reject_mutation(expr)
            self._settle_method_args(expr)
            self._maybe_mark_container_insert(expr)
            self._maybe_mark_own_alloc_move(expr)

        elif isinstance(expr, DotCall):
            # An FFI string argument NEVER consumes (docs/design/borrow-model.md S5), and
            # that holds STRUCTURALLY: an FFI call arrives as a DotCall, and this arm
            # consumes only for an enum constructor and a container insert.
            #
            # Do NOT add a blanket `_consume(arg, CALL_ARG)` loop here -- it would make
            # every `libc.*(s)` call site a false CE2405.
            # `tests/ffi/test_ffi_string_arg_not_consumed.sushi` is the gate.
            self._check_expr(expr.receiver)
            for arg in expr.args:
                self._check_expr(arg)
            self._maybe_reject_mutation(expr)
            # An enum constructor is an ownership sink (#134): it stores the payload
            # shallowly and frees it, so a bare owning Name MOVES. `Box.Full(a)` arrives
            # here as a DotCall, not an EnumConstructor.
            if self._is_enum_constructor(expr):
                for arg in expr.args:
                    self._consume(arg, ConsumingUse.ENUM_PAYLOAD)
            elif getattr(expr, "callee_fn_type", None) is not None:
                # An indirect call through a fn-typed field is a real call, so its
                # arguments follow the declared modes. Keyed on Pass 2's `callee_fn_type`
                # stamp, so an FFI / extension / builtin method keeps the rule above.
                from sushi_lang.semantics.param_modes import effective_modes
                modes = effective_modes(expr.callee_fn_type.modes, CalleeKind.INDIRECT)
                for i, arg in enumerate(expr.args):
                    if self.callee_modes.mode_at(modes, i, CalleeKind.INDIRECT).consumes:
                        self._consume(arg, ConsumingUse.CALL_ARG)
            else:
                self._settle_method_args(expr)
                self._maybe_mark_container_insert(expr)
            self._maybe_mark_own_alloc_move(expr)

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
                self._consume(arg, ConsumingUse.ENUM_PAYLOAD)

        elif isinstance(expr, DynamicArrayFrom):
            for elem in expr.elements.elements:
                self._check_expr(elem)
                # from([...]) is an ownership sink (#134): a bare owning element variable
                # moves into the new dynamic array; a MemberAccess element keeps its copy.
                self._consume(elem, ConsumingUse.ARRAY_ELEMENT)

        elif isinstance(expr, ArrayLiteral):
            for elem in expr.elements:
                self._check_expr(elem)
                # An array-literal element is an ownership sink (#134): a bare owning
                # element variable moves into the array. A MemberAccess element keeps its
                # continuing-owner copy (only bare Names are marked by _mark_moved).
                self._consume(elem, ConsumingUse.ARRAY_ELEMENT)

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
                provenance = self._name_provenance(cap.name)
                cap.ownership_provenance = provenance
                self._consume_named(cap.name, provenance, expr.loc)

        elif isinstance(expr, Spread):
            # Bloom: `arr...`. The source is USED here (so a moved source is reported)
            # and, in a call-argument position, MOVED -- see _consume,
            # which the Call arm runs over every argument after checking them.
            self._check_expr(expr.value)

        elif isinstance(expr, RangeExpr):
            self._check_expr(expr.start)
            self._check_expr(expr.end)

        elif isinstance(expr, _INERT_EXPRS):
            pass

        else:
            # NOT a silent fall-through: a node with no arm gets NO borrow checking, a
            # soundness hole rather than a crash (#174, #175, #176). The CI gate is
            # tests/unit/test_borrow_dispatch_is_total.py; this is the backstop.
            er.raise_internal_error("CE0125", node=type(expr).__name__)

    def _check_borrow(self, borrow: Borrow) -> None:
        """Check borrow expression: peek expr or poke expr"""
        is_poke = borrow.mutability == "poke"

        if isinstance(borrow.expr, Name):
            var_name = borrow.expr.id

            # An unfindable name was ALREADY reported by the scope pass, which owns names
            # (CE1001 or CE2400). Asking again here gave one token two diagnostics, and the
            # wrong one, because `borrow_state` cannot tell the two cases apart.
            if var_name not in self.borrow_state:
                return

            state = self.borrow_state[var_name]

            if state.is_moved:
                self._emit_use_after_move(var_name, borrow.loc, state)
                return

            # A `poke` of a read-only receiver hands the write to a callee, which upgrades
            # the borrow (CE2408 / CE2414 / CE2421). A `peek` stays legal.
            if is_poke and self._reject_readonly_write(
                    var_name, borrow.loc, "take a `poke` borrow"):
                return

            # A `poke` may mutate or free, so it conflicts with a live `let`-borrow like a
            # mutating method (#242). CE2412 not CE2407: the user wrote no `peek`.
            if is_poke:
                self._check_owner_not_borrowed(var_name, borrow.loc, "take `poke`")

            if is_poke:
                if state.poke_borrow_count > 0:
                    self.err.emit_with(er.ERR.CE2403, borrow.loc, name=var_name) \
                        .note("first borrowed here", state.first_borrow_span).emit()
                    return
                if state.peek_borrow_count > 0:
                    self.err.emit_with(er.ERR.CE2407, borrow.loc, name=var_name) \
                        .note("first borrowed here", state.first_borrow_span).emit()
                    return
                if isinstance(state.var_type, ReferenceType) and state.var_type.is_poke():
                    self.err.emit(er.ERR.CW2409, borrow.loc, name=var_name)
                state.poke_borrow_count = 1
                state.first_borrow_span = borrow.loc
            else:
                if state.poke_borrow_count > 0:
                    self.err.emit_with(er.ERR.CE2407, borrow.loc, name=var_name) \
                        .note("first borrowed here", state.first_borrow_span).emit()
                    return
                if state.peek_borrow_count == 0:
                    state.first_borrow_span = borrow.loc
                state.peek_borrow_count += 1

            self.active_borrows.add(var_name)

        elif isinstance(borrow.expr, MemberAccess):
            base = self._get_member_access_base(borrow.expr)

            if not isinstance(base, Name):
                expr_str = self._expr_to_string(borrow.expr)
                self.err.emit(er.ERR.CE2404, borrow.loc, expr=expr_str)
                return

            base_var = base.id
            if base_var not in self.borrow_state:
                return

            state = self.borrow_state[base_var]
            if state.is_moved:
                self._emit_use_after_move(base_var, borrow.loc, state)
                return

            # The same rule as the Name arm, through a field: `poke peek_ref.field`,
            # `poke binding.field` and `poke self.field` all hand a callee a write that
            # cannot reach the value the user means.
            if is_poke and self._reject_readonly_write(
                    base_var, borrow.loc, "take a `poke` borrow"):
                return

            if is_poke:
                self._check_owner_not_borrowed(base_var, borrow.loc, "take `poke`")

            if is_poke:
                if state.poke_borrow_count > 0:
                    self.err.emit_with(er.ERR.CE2403, borrow.loc, name=base_var) \
                        .note("first borrowed here", state.first_borrow_span).emit()
                    return
                if state.peek_borrow_count > 0:
                    self.err.emit_with(er.ERR.CE2407, borrow.loc, name=base_var) \
                        .note("first borrowed here", state.first_borrow_span).emit()
                    return
                state.poke_borrow_count = 1
                state.first_borrow_span = borrow.loc
            else:
                if state.poke_borrow_count > 0:
                    self.err.emit_with(er.ERR.CE2407, borrow.loc, name=base_var) \
                        .note("first borrowed here", state.first_borrow_span).emit()
                    return
                if state.peek_borrow_count == 0:
                    state.first_borrow_span = borrow.loc
                state.peek_borrow_count += 1

            self.active_borrows.add(base_var)

        else:
            # Other expressions (function calls, literals, etc.) cannot be borrowed
            expr_str = self._expr_to_string(borrow.expr)
            self.err.emit(er.ERR.CE2404, borrow.loc, expr=expr_str)

    def _get_member_access_base(self, expr: MemberAccess) -> Expr:
        """Get the base variable of a member access chain."""
        current = expr
        while isinstance(current, MemberAccess):
            current = current.receiver
        return current

    def _is_enum_constructor(self, expr: Expr) -> bool:
        """Is this `X.Y(args)` an enum constructor rather than a method call?"""
        receiver = getattr(expr, 'receiver', None)
        if not isinstance(receiver, Name):
            return False
        return receiver.id in self.enum_names and receiver.id not in self.borrow_state

    # These store the argument and free it, so each is a consuming use. Only the METHOD
    # NAME is matched loosely -- the receiver must be a container, so a user extension
    # called `push` is not swept up.
    _CONTAINER_INSERT_METHODS = frozenset({"push", "insert"})

    def _maybe_mark_container_insert(self, expr: Expr) -> None:
        """`l.push(x)` / `m.insert(k, v)` takes ownership -- the CONTAINER_INSERT use."""
        if getattr(expr, "method", None) not in self._CONTAINER_INSERT_METHODS:
            return
        # `_read_type` is the ONE walker for read-through-an-owner shapes. A narrower
        # twin here did not unwrap TryExpr, so `outer.get(0)??.push(5)` went unstamped
        # and the seam reported CE0129.
        if not self._is_container_type(self._read_type(getattr(expr, "receiver", None))):
            return
        for arg in expr.args:
            self._consume(arg, ConsumingUse.CONTAINER_INSERT)

    def _is_container_type(self, ty: Optional[Type]) -> bool:
        """Is `ty` a `List@(T)`, `HashMap@(K, V)` or a dynamic array `T[]`?"""
        from sushi_lang.semantics.generics.types import GenericTypeRef
        ty = self._resolve_named(ty)
        if isinstance(ty, ReferenceType):
            ty = ty.referenced_type
        if isinstance(ty, DynamicArrayType):
            return True
        if isinstance(ty, GenericTypeRef):
            return ty.base_name in ("List", "HashMap")
        name = getattr(ty, "name", None)
        return isinstance(name, str) and (name.startswith("List<") or name.startswith("HashMap<"))

    def _maybe_mark_own_alloc_move(self, expr: Expr) -> None:
        """`Own.alloc(x)` takes ownership of `x` -- the OWN_ALLOC consuming use."""
        if getattr(expr, 'method', None) != 'alloc':
            return
        receiver = getattr(expr, 'receiver', None)
        if not (isinstance(receiver, Name) and receiver.id == 'Own'):
            return
        for arg in expr.args:
            self._consume(arg, ConsumingUse.OWN_ALLOC)

    def _reconcile_closure_bind(self, stmt: Let) -> None:
        """Record whether a `fn(...)` binding owns a heap environment."""
        from dataclasses import replace
        from sushi_lang.semantics.typesys import FunctionType
        if not isinstance(stmt.ty, FunctionType):
            return
        dest = self.borrow_state.get(stmt.name)
        if dest is None:
            return
        value = stmt.value
        if isinstance(value, Lambda):
            dest.var_type = replace(stmt.ty, captures=tuple(value.captures or ()))
        elif isinstance(value, Name):
            src = self.borrow_state.get(value.id)
            if src is None:
                # Not a local: a reference to a top-level function, which captures
                # nothing. State the empty tuple -- leaving it None would read as
                # "unstated", and a plain fn reference would then move on every use.
                dest.var_type = replace(stmt.ty, captures=())
            elif isinstance(src.var_type, FunctionType):
                dest.var_type = src.var_type

    def _emit_use_after_move(self, name: str, use_span: Optional[Span],
                             state: BorrowState) -> None:
        """Report a use-after-move, pointing at the MOVE as well as the use."""
        diag = self.err.emit_with(er.ERR.CE2405, use_span, name=name)
        if state.moved_at_span is not None:
            diag.note(f"'{name}' was moved here", state.moved_at_span)
        diag.emit()

    def _call_modes(self, expr: Call) -> tuple[CalleeKind, tuple[ParamMode, ...], Optional[int]]:
        """The callee's kind, its parameter modes, and where a `...T` slot starts."""
        if not isinstance(expr.callee, Name):
            fn_type = getattr(expr, "callee_fn_type", None)
            modes = getattr(fn_type, "modes", ()) if fn_type is not None else ()
            from sushi_lang.semantics.param_modes import effective_modes
            return CalleeKind.INDIRECT, effective_modes(modes, CalleeKind.INDIRECT), None

        name = expr.callee.id
        state = self.borrow_state.get(name)
        local_type = state.var_type if state is not None else None
        kind, modes = self.callee_modes.for_name(name, local_type)
        variadic_at = (None if kind is CalleeKind.INDIRECT
                       else self.callee_modes.variadic_from(name))
        return kind, modes, variadic_at

    def _consume_call_args(self, expr: Call) -> None:
        """Consume the arguments the callee's declared modes say it takes ownership of."""
        kind, modes, variadic_at = self._call_modes(expr)
        collected_owner_is_callee = (
            isinstance(expr.callee, Name)
            and self.callee_modes.variadic_callee_owns(expr.callee.id))
        for i, arg in enumerate(expr.args):
            if variadic_at is not None and i >= variadic_at:
                # A bloomed `arr...` hands the WHOLE array over, so it transfers only
                # when something else takes it. A collected element always transfers --
                # into the synthesized array, whoever ends up owning that.
                if isinstance(arg, Spread) and not collected_owner_is_callee:
                    continue
                self._consume(arg, ConsumingUse.ARRAY_ELEMENT)
                continue
            mode = self.callee_modes.mode_at(modes, i, kind)
            self._check_nom_marker(expr, arg, i, mode, kind)
            if mode.consumes:
                self._consume(arg, ConsumingUse.CALL_ARG)
            elif not mode.by_pointer:
                self._register_implicit_borrow(arg)

    def _check_nom_marker(self, call, arg: Expr, index: int,
                          mode: ParamMode, kind: CalleeKind) -> None:
        """The `nom` marker must be written at the call site if and only if it is declared."""
        if kind in (CalleeKind.CONSTRUCTOR, CalleeKind.CONTAINER):
            return
        marked = bool(getattr(arg, "nom_marked", False))
        if marked == mode.consumes:
            return
        span = getattr(arg, "nom_span", None) or arg.loc
        name = self._param_name(call, index) or f"#{index + 1}"
        if mode.consumes:
            self.err.emit_with(er.ERR.CE2427, span, name=name) \
                .help("the callee takes ownership here; write `nom` at the call site "
                      "too, or `nom <arg>.clone()` to keep your own value").emit()
        else:
            self.err.emit_with(er.ERR.CE2427, span, name=name) \
                .help("the callee only borrows this argument, so it stays yours after "
                      "the call; drop the `nom`").emit()

    def _param_name(self, call, index: int) -> Optional[str]:
        """The declared name of parameter `index` of a call's callee, if it is known."""
        names = getattr(call, "callee_param_names", None)
        if names is not None:
            return names[index] if index < len(names) else None
        if not isinstance(getattr(call, "callee", None), Name):
            return None
        sig = self.callee_modes.signature_of(call.callee.id)
        params = getattr(sig, "params", None) or ()
        return params[index].name if index < len(params) else None

    def _settle_method_args(self, expr) -> None:
        """Apply the declared modes of an extension or perk method to its arguments."""
        modes = getattr(expr, "callee_param_modes", None)
        if modes is None:
            return
        for i, arg in enumerate(expr.args):
            mode = self.callee_modes.mode_at(modes, i, CalleeKind.METHOD)
            self._check_nom_marker(expr, arg, i, mode, CalleeKind.METHOD)
            if mode.consumes:
                self._consume(arg, ConsumingUse.CALL_ARG)
            elif not mode.by_pointer:
                self._register_implicit_borrow(arg)

    def _register_implicit_borrow(self, arg: Expr) -> None:
        """Count an unmarked argument as the shared borrow it now is."""
        if not isinstance(arg, Name):
            return
        state = self.borrow_state.get(arg.id)
        if state is None or state.is_moved:
            return
        if self._type_class_of_source(state, state.var_type) is not TypeClass.MOVE:
            return
        if state.poke_borrow_count > 0:
            self.err.emit_with(er.ERR.CE2407, arg.loc, name=arg.id) \
                .note("first borrowed here", state.first_borrow_span).emit()
            return
        if state.peek_borrow_count == 0:
            state.first_borrow_span = arg.loc
        state.peek_borrow_count += 1
        self.active_borrows.add(arg.id)

    def _apply_destroy_effects(self, call: Call) -> None:
        """Mark each argument the callee destroys through a `poke` parameter (#168)."""
        if not isinstance(call.callee, Name):
            return
        for index in self.destroy_effects.get(call.callee.id, ()):
            if index >= len(call.args):
                continue
            arg = call.args[index]
            if isinstance(arg, Borrow):
                arg = arg.expr           # `poke map` -> `map`
            if isinstance(arg, Name) and arg.id in self.borrow_state:
                self.borrow_state[arg.id].is_destroyed = True

    def _source_provenance(self, expr: Expr) -> Provenance:
        """Where the value at a consuming use came from -- the half only semantics knows."""
        if isinstance(expr, Name):
            return self._name_provenance(expr.id)

        if self._reads_through_owner(expr):
            return Provenance.BORROWED

        return Provenance.FRESH

    def _reads_through_owner(self, expr: Optional[Expr]) -> bool:
        """Does `expr` read a value out of storage something else still owns?"""
        while isinstance(expr, TryExpr):
            expr = expr.expr

        if self._is_bare_enum_constant(expr):
            # `Shape.Empty` parses as a MemberAccess but CONSTRUCTS a payload-free
            # variant and owns it, like the `Shape.Empty()` spelling. Classifying it
            # BORROWED skipped cleanup and a later owning rebind leaked (#289).
            return False

        if isinstance(expr, (MemberAccess, IndexAccess)):
            return True

        if getattr(expr, "method", None) == "get":
            receiver = getattr(expr, "receiver", None)
            if isinstance(receiver, Name):
                state = self.borrow_state.get(receiver.id)
                return state is not None and is_get_out_container(state.var_type)
            return self._reads_through_owner(receiver)

        return False

    def _is_bare_enum_constant(self, expr: Optional[Expr]) -> bool:
        """Is `expr` the parenthesis-free spelling of a payload-free variant (#289)?"""
        if not isinstance(expr, MemberAccess) or not isinstance(expr.receiver, Name):
            return False
        if expr.receiver.id in self.borrow_state:
            return False  # a local shadows the enum name
        enums = getattr(self.tables, "enums", None) if self.tables is not None else None
        enum_type = getattr(enums, "by_name", {}).get(expr.receiver.id) if enums else None
        get_variant = getattr(enum_type, "get_variant", None)
        return get_variant is not None and get_variant(expr.member) is not None

    def _name_provenance(self, name: str) -> Provenance:
        """The `Provenance` of a source that is a bare name."""
        state = self.borrow_state.get(name)
        if state is None:
            return Provenance.FRESH
        if state.owns_no_heap:
            # Nothing to borrow: this binding's value owns no heap, so every position may
            # have it. Only a `string` bound straight from a literal answers True (#338
            # removed the method-parameter exemption -- the view it let out dangled).
            #
            # OWNED and not BORROWED, and that is about the SEAM: the backend re-derives
            # the class from the TYPE alone, so it answers MOVE for any `string`, and
            # (BORROWED, MOVE) is REJECT -- a CE0129 for a sound shape.
            return Provenance.OWNED
        if (state.is_borrowed_binding
                or state.is_borrow_param
                or isinstance(state.var_type, ReferenceType)):
            return Provenance.BORROWED
        return Provenance.OWNED

    def _consume(self, expr: Expr, use: ConsumingUse) -> None:
        """Classify a consuming use, stamp the decision, and act on it."""
        # A bloom `arr...` MOVES its source into the callee. CE0120 restricts the source
        # to a bare array variable, so unwrapping here makes a use-after-bloom a CE2405
        # instead of a use-after-free (#174).
        if isinstance(expr, Spread):
            expr = expr.value

        provenance = self._source_provenance(expr)
        # Only PROVENANCE is stamped. The `use` is the backend's to name: semantics
        # cannot tell `S(x)` from `f(x)` -- both are a `Call` here.
        expr.ownership_provenance = provenance

        if isinstance(expr, Name):
            self._consume_named(expr.id, provenance, expr.loc)
            return

        # A read through a live owner has no owner to mark moved, but it CAN be rejected
        # (#242): the owner keeps the value and still frees it. Leaving this cell copying
        # made the backend answer REJECT with no diagnostic ahead of it -- a CE0129.
        if provenance is not Provenance.BORROWED:
            return
        if classify(provenance, self._type_class(self._read_type(expr))) is Ownership.REJECT:
            self._emit_consume_of_read(expr)

    def _consume_named(self, name: str, provenance: Provenance,
                       use_span: Optional[Span]) -> None:
        """Apply the ownership decision to a source that is a bare name."""
        state = self.borrow_state.get(name)
        if state is None:
            return

        if state.is_argv_view:
            # Moving main's borrowed argv view would double-free process argv (N2). A more
            # specific diagnostic than CE2411, so it wins.
            self.err.emit(er.ERR.CE2410, use_span, name=name)
            return

        decision = classify(provenance, self._type_class_of_source(state, state.var_type))
        if decision is Ownership.MOVE:
            # The same value cannot be borrowed and handed away in ONE statement (CE2401).
            # Here because the counters are live: the call arm registers every argument's
            # borrow before consuming any, so both orders of `both(peek s, s)` are one
            # rule.
            if state.is_borrowed:
                self.err.emit_with(er.ERR.CE2401, use_span, name=name) \
                    .note("borrowed here, in the same statement",
                          state.first_borrow_span) \
                    .help(f"the new owner frees this value while the borrow still points "
                          f"at it; borrow it twice, or clone what the owning position "
                          f"needs: `{name}.clone()`") \
                    .emit()
                return
            # Handing the owner away leaves every binding reading out of it pointing at
            # storage the new owner frees (#242).
            self._check_owner_not_borrowed(name, use_span, "move")
            state.is_moved = True
            state.moved_at_span = state.moved_at_span or use_span
        elif decision is Ownership.REJECT:
            self._emit_consume_of_borrow(name, use_span, state)

    def _bind(self, stmt: Let) -> None:
        """Give a `let` binding the provenance of its initializer (#242)."""
        expr = stmt.value
        provenance = self._source_provenance(expr)
        expr.ownership_provenance = provenance

        dest = self.borrow_state.get(stmt.name)
        if dest is None:
            return

        src_state = self.borrow_state.get(expr.id) if isinstance(expr, Name) else None

        if src_state is not None and src_state.is_argv_view:
            # Binding main's argv view by value would make the new binding free argv
            # (N2). The same hard error as any other move of it, and more specific than
            # anything the table says.
            self.err.emit(er.ERR.CE2410, expr.loc, name=expr.id)
            return

        # The SOURCE's recorded type where there is one: the DECLARED type has lost the
        # capture list, so it would move a plain fn reference and report a false CE2405.
        ty = src_state.var_type if src_state is not None else stmt.ty

        decision = classify(provenance, self._type_class_of_source(src_state, ty))
        if decision is Ownership.MOVE:
            src_state.is_moved = True
            src_state.moved_at_span = src_state.moved_at_span or expr.loc
        elif decision is Ownership.REJECT:
            self._record_borrowed_binding(stmt, dest)

    def _record_borrowed_binding(self, stmt: Let, dest: BorrowState) -> None:
        """Record that a `let` borrows storage its initializer's owner keeps (#242)."""
        dest.is_borrowed_binding = True
        dest.is_let_borrow = True
        dest.bound_at_span = stmt.loc

        owner = self._root_owner(stmt.value)
        if owner is None:
            return
        owner_state = self.borrow_state.get(owner)
        if owner_state is None:
            return
        dest.borrows_from = owner
        owner_state.binding_borrows.append((stmt.name, stmt.loc))
        self._scope_binding_borrows[-1].append((owner, stmt.name))

    def _read_type(self, expr: Optional[Expr]) -> Optional[Type]:
        """The type a read-through-an-owner expression produces."""
        from sushi_lang.semantics.typesys import StructType as _StructType

        while isinstance(expr, TryExpr):
            expr = expr.expr

        if isinstance(expr, Name):
            state = self.borrow_state.get(expr.id)
            return state.var_type if state is not None else None

        if isinstance(expr, MemberAccess):
            receiver = self._resolve_named(self._read_type(expr.receiver))
            if isinstance(receiver, ReferenceType):
                receiver = self._resolve_named(receiver.referenced_type)
            if isinstance(receiver, _StructType):
                return receiver.get_field_type(expr.member)
            return None

        if isinstance(expr, IndexAccess):
            return self._element_type(self._read_type(expr.array))

        if getattr(expr, "method", None) == "get":
            receiver_type = self._read_type(getattr(expr, "receiver", None))
            # A container `.get()` returns `Maybe@(T)`, and every use of it reaches here
            # through the `??` this method already unwrapped, so the interesting type is
            # the element. `Own@(T).get()` hands back the bare `T`.
            return self._element_type(receiver_type)

        return None

    def _element_type(self, ty: Optional[Type]) -> Optional[Type]:
        """What a `.get()` on a receiver of type `ty` reads out."""
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.typesys import ArrayType as _ArrayType, StructType as _StructType

        ty = self._resolve_named(ty)
        if isinstance(ty, ReferenceType):
            ty = self._resolve_named(ty.referenced_type)
        if isinstance(ty, (DynamicArrayType, _ArrayType)):
            return ty.base_type
        if isinstance(ty, GenericTypeRef):
            if ty.base_name in ("List", "Own") and ty.type_args:
                return ty.type_args[0]
            if ty.base_name == "HashMap" and len(ty.type_args) == 2:
                return ty.type_args[1]
        # An interned StructType's NAME carries its type arguments and IS its identity
        # (#240), so reading them back out of it is the supported route. The angle
        # brackets are the internal spelling on purpose.
        if isinstance(ty, _StructType):
            if ty.name.startswith("List<"):
                return self._type_from_name(ty.name[len("List<"):-1])
            if ty.name.startswith("HashMap<"):
                args = _split_type_args(ty.name[len("HashMap<"):-1])
                return self._type_from_name(args[1]) if len(args) == 2 else None
        return self._own_payload(ty)

    def _type_from_name(self, type_str: str) -> Optional[Type]:
        """Resolve one interned type-argument spelling back to a `Type`."""
        if self.tables is None:
            return None
        from types import SimpleNamespace
        from sushi_lang.semantics.generics.type_strings import resolve_type_from_string
        adapter = SimpleNamespace(
            struct_table=getattr(self.tables, "structs", SimpleNamespace(by_name={})),
            enum_table=getattr(self.tables, "enums", SimpleNamespace(by_name={})),
        )
        try:
            return resolve_type_from_string(type_str, adapter)
        except Exception:
            return None

    def _root_owner(self, expr: Optional[Expr]) -> Optional[str]:
        """The named local a read-through-an-owner expression ultimately reads out of."""
        while True:
            if isinstance(expr, TryExpr):
                expr = expr.expr
            elif isinstance(expr, MemberAccess):
                expr = expr.receiver
            elif isinstance(expr, IndexAccess):
                expr = expr.array
            elif isinstance(expr, (MethodCall, DotCall)):
                expr = expr.receiver
            elif isinstance(expr, Name):
                return expr.id
            else:
                return None

    def _maybe_reject_mutation(self, expr: Expr) -> None:
        """Reject `c.push(x)` while a `let`-borrow binding reads out of `c` (#242)."""
        # A call to a `poke self` method (#327) IS a write to the receiver root --
        # Pass 2 stamps the resolution on the node, so this pass never re-resolves.
        is_poke_self_call = getattr(expr, "callee_self_mode", None) == "poke"
        if getattr(expr, "method", None) not in _MUTATING_METHODS and not is_poke_self_call:
            return
        root = self._root_owner(getattr(expr, "receiver", None))
        what = f"call `.{expr.method}()`"
        if self._reject_readonly_write(root, expr.loc, what):
            return
        self._check_owner_not_borrowed(root, expr.loc, what)

    def _reject_readonly_write(self, name: Optional[str], span: Optional[Span],
                               what: str) -> bool:
        """THE gate: a write that cannot reach the value it appears to write is rejected."""
        if name is None:
            return False
        state = self.borrow_state.get(name)
        if state is None:
            return False
        for kind in self._READONLY_RECEIVERS:
            if not kind.matches(state):
                continue
            diag = self.err.emit_with(kind.code, span, name=name)
            note_span = kind.note_span(state)
            if note_span is not None:
                diag.note(kind.note.format(name=name), note_span)
            diag.help(kind.help.format(name=name, what=what))
            diag.emit()
            return True
        return False

    def _check_owner_not_borrowed(self, owner: Optional[str], span: Optional[Span],
                                  what: str) -> None:
        """Reject a change to `owner` while a `let`-borrow binding reads out of it (#242)."""
        if owner is None:
            return
        state = self.borrow_state.get(owner)
        if state is None or not state.binding_borrows:
            return
        # INVALIDATE, do not report: the change is an error only on a read AFTER it.
        # Reporting here would reject `let g = fns.get(0)??; g(10); fns.free()`.
        for name, bound_at in state.binding_borrows:
            binding = self.borrow_state.get(name)
            if binding is not None and binding.invalidated_at is None:
                binding.invalidated_at = span
                binding.invalidated_by = (owner, what)
                binding.bound_at_span = binding.bound_at_span or bound_at
        # Invalidate ONCE, but NOT during a suppressed (loop-discovery) pass: clearing in
        # pass 1 leaves pass 2 with no live borrows to invalidate.
        if not self.err.suppressed:
            state.binding_borrows = []

    def _emit_use_of_invalidated_borrow(self, name: str, use_span: Optional[Span],
                                        state: BorrowState) -> None:
        """Report CE2412 at the change, and name the later use that makes it wrong."""
        owner, what = state.invalidated_by
        diag = self.err.emit_with(er.ERR.CE2412, state.invalidated_at,
                                  owner=owner, name=name)
        if state.bound_at_span is not None:
            diag.note(f"'{name}' borrows from '{owner}' here", state.bound_at_span)
        diag.note(f"'{name}' is used here, after the change", use_span)
        diag.help(f"{what} after the last use of '{name}', "
                  f"or bind an independent value with `.clone()`")
        diag.emit()
        # Report once per binding. A suppressed (loop-discovery) pass reported nothing, so
        # it must consume nothing -- clearing there erases the real pass's invalidation.
        if not self.err.suppressed:
            state.invalidated_at = None

    def _emit_consume_of_read(self, expr: Expr) -> None:
        """Report CE2411 for a read through a live owner (`h.inner`, `c.get(0)??`)."""
        text = self._expr_to_string(expr)
        diag = self.err.emit_with(er.ERR.CE2411, expr.loc, name=text)
        owner = self._root_owner(expr)
        state = self.borrow_state.get(owner) if owner is not None else None
        if state is not None and state.declared_at_span is not None:
            diag.note(f"'{owner}' owns this value and still frees it",
                      state.declared_at_span)
        # ONE branch, on purpose: a get-out `.clone()` still hits CE0019, and that is a
        # real defect rather than a reason to word around it. The three RED
        # `test_own_get_*` files hold the branch honest until it is fixed.
        diag.help(f"clone it to take an independent value: `{text}.clone()`")
        diag.emit()

    def _emit_consume_of_borrow(self, name: str, use_span: Optional[Span],
                                state: BorrowState) -> None:
        """Report CE2411, pointing at the binding as well as the use."""
        diag = self.err.emit_with(er.ERR.CE2411, use_span, name=name)
        if state.bound_at_span is not None:
            diag.note(f"'{name}' borrows here, and the owner keeps the value",
                      state.bound_at_span)
        elif isinstance(state.var_type, ReferenceType) \
                and state.declared_at_span is not None:
            diag.note(f"'{name}' is declared here as a `&{state.var_type.mutability}` "
                      f"borrow of the caller's value",
                      state.declared_at_span)
        elif state.is_method_receiver:
            # Unguarded on purpose: this sentence still says something without a location,
            # so a receiver whose type span is missing degrades to a note rather than to
            # nothing. The parameter arm below needs its location to read at all.
            diag.note("'self' is the receiver of a method on this type, which borrows "
                      "the caller's value", state.declared_at_span)
        elif state.is_borrow_param and state.declared_at_span is not None:
            diag.note(f"'{name}' is declared here, and a method parameter borrows the "
                      f"caller's value", state.declared_at_span)
        diag.help(f"clone it to take an independent value: `{name}.clone()`")
        diag.emit()

    def _type_class(self, ty: Optional[Type]) -> TypeClass:
        """Classify a type as PLAIN or MOVE, resolving named types first."""
        return type_class_of(ty, self._resolve_named)

    def _type_class_of_source(self, state: Optional[BorrowState],
                              ty: Optional[Type]) -> TypeClass:
        """Classify the SOURCE of a consuming use, applying option B."""
        if state is not None and state.owns_no_heap:
            return TypeClass.PLAIN
        return self._type_class(ty)

    def _clear_borrows(self) -> None:
        """Clear all active borrows (called after expression evaluation)."""
        for var_name in self.active_borrows:
            if var_name in self.borrow_state:
                state = self.borrow_state[var_name]
                state.poke_borrow_count = 0
                state.peek_borrow_count = 0
        self.active_borrows.clear()

    def _expr_to_string(self, expr: Expr) -> str:
        """Convert an expression to a string for error messages."""
        if isinstance(expr, Name):
            return expr.id
        elif isinstance(expr, IntLit):
            return str(expr.value)
        elif isinstance(expr, BinaryOp):
            return f"({self._expr_to_string(expr.left)} {expr.op} {self._expr_to_string(expr.right)})"
        elif isinstance(expr, (MethodCall, DotCall)):
            # Both spellings reach here, arguments included, so the text matches what the
            # user wrote and the `help` is something they can paste.
            args = ", ".join(self._expr_to_string(a) for a in (getattr(expr, "args", None) or []))
            return f"{self._expr_to_string(expr.receiver)}.{expr.method}({args})"
        elif isinstance(expr, MemberAccess):
            return f"{self._expr_to_string(expr.receiver)}.{expr.member}"
        elif isinstance(expr, IndexAccess):
            return f"{self._expr_to_string(expr.array)}[{self._expr_to_string(expr.index)}]"
        elif isinstance(expr, TryExpr):
            return f"{self._expr_to_string(expr.expr)}??"
        else:
            return "<expression>"
