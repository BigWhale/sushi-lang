# semantics/passes/borrow.py
"""
Borrow checker for Sushi's reference system.

This pass validates borrowing rules at compile-time:
1. Variables and struct member access can be borrowed (not arbitrary expressions)
2. Only one active borrow per variable at a time (no aliasing)
3. Cannot move, rebind, or destroy a variable while it's borrowed
4. Cannot borrow a moved variable

Supported borrow patterns:
- Variables: &x
- Member access: &obj.field
- Nested member access: &obj.nested.field

The borrow checker runs after type validation and ensures memory safety
without runtime overhead.
"""

from __future__ import annotations
from typing import Dict, FrozenSet, Iterable, Iterator, Set, Optional
from dataclasses import dataclass

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
    Pattern,
    Print,
    PrintLn,
    Program,
    RangeExpr,
    Rebind,
    Return,
    Spread,
    Stmt,
    StringLit,
    TryExpr,
    UnaryOp,
    While,
)
from sushi_lang.semantics.typesys import ReferenceType, DynamicArrayType, Type, is_owning_type
from sushi_lang.semantics.ownership import (
    ConsumingUse, Ownership, Provenance, TypeClass, classify, is_own_type, type_class_of,
)
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.error_reporter import PassErrorReporter


# Expression nodes that own nothing and name nothing: a literal value, or the empty
# dynamic-array constructor. They have no sub-expressions and cannot reference a binding,
# so there is nothing for the borrow checker to do with them. Listing them EXPLICITLY is
# what lets _check_expr's `else` be a hard error instead of a silent skip.
_INERT_EXPRS = (IntLit, FloatLit, BoolLit, BlankLit, StringLit, DynamicArrayNew)


@dataclass
class BorrowState:
    """Tracks the borrow state of a single variable.

    Supports two borrow modes:
    - &poke: Exclusive (read-write) - only one at a time
    - &peek: Shared (read-only) - multiple allowed

    Rules:
    - Multiple &peek borrows allowed
    - Only one &poke borrow at a time
    - Cannot have &peek and &poke borrows simultaneously
    """
    name: str
    var_type: Optional[Type] = None  # Variable type (for move semantics)
    poke_borrow_count: int = 0  # Number of active &poke borrows (max 1)
    peek_borrow_count: int = 0  # Number of active &peek borrows (unlimited)
    is_moved: bool = False  # Ownership has been transferred
    is_owning_closure: bool = False  # A capturing closure that owns a heap env (capture
                                     # is erased from the fn(...) type, so ownership is
                                     # tracked by binding provenance, not var_type)
    is_destroyed: bool = False  # Variable has been explicitly destroyed (via .destroy())
    is_argv_view: bool = False  # main's `string[] args`: a borrowed view of process argv;
                                # moving it by value would free argv, so it is a hard error
    is_borrowed_binding: bool = False  # A `match` payload binding or a `foreach` item: a
                                # READ-ONLY borrow of storage the scrutinee or the container
                                # still owns (docs/design/ownership-conventions.md S8).
                                # Distinct from is_argv_view, which is one specific borrow
                                # with its own diagnostic, and from a ReferenceType param,
                                # which is spelled `&peek`/`&poke` in the source.
    bound_at_span: Optional[Span] = None  # Where the binding was introduced. CE2411 is a
                                # RELATIONAL error -- the use is only wrong BECAUSE of what
                                # the binding borrows from -- so it renders both.
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

    Both flags are MONOTONE (they only ever go false -> true within a path), so a join
    is a union and a loop reaches its fixed point in two passes.

    `destroyed` used to be absent from this snapshot, which was harmless only because a
    destroy could not be reached through a call: the sole way to set it was a literal
    `x.destroy()` in the same function. Once a call can destroy its `&poke` argument
    (#168), a destroy inside one `if` arm would leak into its sibling arms and past the
    `if` -- a false CE2406, exactly the bug that per-arm snapshotting was introduced in
    Tier 2 to kill for moves (test_move_in_branch_arms).
    """
    moved: frozenset[str] = frozenset()
    destroyed: frozenset[str] = frozenset()

    def __or__(self, other: "FlowFacts") -> "FlowFacts":
        return FlowFacts(self.moved | other.moved, self.destroyed | other.destroyed)


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
    """The call a statement makes, for the shapes a `&poke` argument can appear in."""
    if isinstance(stmt, ExprStmt):
        return _leading_call(stmt.expr)
    if isinstance(stmt, (Let, Rebind)):
        return _leading_call(stmt.value)
    return None


def _poke_param_indices(func: FuncDef) -> Dict[str, int]:
    """`&poke` parameters of `func`, by name -> positional index.

    Only `&poke` counts: destroying through a `&peek` is already rejected (it is a
    read-only borrow), so a `&peek` param cannot carry a destroy effect out.
    """
    return {
        param.name: i
        for i, param in enumerate(func.params)
        if isinstance(param.ty, ReferenceType) and param.ty.is_poke()
    }


def compute_destroy_effects(programs: Iterable[Program]) -> Dict[str, FrozenSet[int]]:
    """Which `&poke` parameters does each function destroy? (#168)

    The borrow checker is otherwise strictly intra-procedural: `borrow_state` is reset per
    function, so a callee that calls `.destroy()` on its `&poke` parameter had no effect on
    the caller's binding and use-after-destroy compiled clean. This is the first
    inter-procedural analysis in the semantics layer.

    Returns `fn name -> the set of parameter indices it destroys`, transitively: if `f`
    forwards its own `&poke` param to a `g` that destroys it, `f` destroys it too. The
    lattice is a finite set of indices that only grows, so the fixed point converges.

    Deliberately an UNDER-approximation -- it can miss a destroy, it can never invent one,
    so it cannot produce a false CE2406 on code that compiles today. Known misses:
      - a generic callee (monomorphized fns are not in `program.functions`)
      - an extension/perk method destroying its implicit `self` (not in `func.params`)
      - a call nested somewhere other than a statement's leading expression
    """
    funcs: Dict[str, FuncDef] = {}
    for program in programs:
        for func in program.functions:
            funcs.setdefault(func.name, func)

    effects: Dict[str, Set[int]] = {}

    # Round 1: a literal `p.destroy()` where `p` is one of this function's &poke params.
    # Mirrors the receiver shape the intra-procedural check already recognises.
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

    # Round 2..n: propagate through calls until nothing changes. `f` destroys its param i
    # if it hands that param to a `g` that destroys the slot it lands in.
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
    """
    Analyzes borrowing safety for a program.

    Simplified borrow checking strategy:
    - Function-scoped analysis (borrows don't escape function boundaries)
    - Borrows are considered active for the duration of the function call they're passed to
    - One active borrow per variable at a time

    Future enhancements could add:
    - Lifetime tracking for more precise borrow scopes
    - Support for returning references
    - Nested borrow analysis
    """

    def __init__(self, reporter: Reporter,
                 destroy_effects: Optional[Dict[str, FrozenSet[int]]] = None,
                 enum_names: Optional[Set[str]] = None,
                 tables=None):
        self.reporter = reporter
        # Struct/enum tables, used only to RESOLVE a named type before classifying it.
        # `type_moves_by_value` answers False for an UnknownType by design, so without a
        # resolver an owning struct named by its declaration would classify as owning
        # nothing -- and every consuming use of it would alias instead of moving.
        self.tables = tables
        self.err = PassErrorReporter(reporter)
        # fn name -> the &poke param indices it destroys (#168). Computed once over EVERY
        # unit by compute_destroy_effects(), so a cross-unit callee is not a blind spot.
        # Empty means "no call destroys anything", i.e. the old intra-procedural behaviour.
        self.destroy_effects: Dict[str, FrozenSet[int]] = destroy_effects or {}
        # Bare enum type names ("Box", "Result", "Maybe"), used to tell an enum constructor
        # `Box.Full(a)` from an ordinary method call `xs.push(a)` -- both are DotCall nodes
        # at this pass, and only the former is an ownership sink. Empty means "recognise
        # none", which is exactly the pre-#134 behaviour of not marking enum payloads.
        self.enum_names: Set[str] = enum_names or set()
        # Track borrow state per variable in current scope
        self.borrow_state: Dict[str, BorrowState] = {}
        # Track variables currently borrowed (for clearing after expressions)
        self.active_borrows: Set[str] = set()

    def run(self, program: Program) -> None:
        """Run borrow checking on the entire program."""
        # Check all functions
        for func in program.functions:
            self._check_function(func)

        # Check non-generic extension methods
        for ext in program.extensions:
            self._check_extension(ext)

        # Check generic extension methods (borrow checking works the same regardless of generics)
        for ext in program.generic_extensions:
            self._check_extension(ext)

        # Check perk implementations. Each method is a FuncDef with an implicit `self`
        # (not in params), exactly like an extension method. Omitting these meant a perk
        # body was never borrow-checked AT ALL -- no use-after-move, no use-after-destroy,
        # no borrow conflicts (#176). ScopeAnalyzer.run() walked them the whole time.
        for perk_impl in program.perk_impls:
            for method in perk_impl.methods:
                self._check_function(method)

    def _check_function(self, func: FuncDef) -> None:
        """Check borrow safety for a single function."""
        # Reset borrow state for new function scope
        self.borrow_state = {}
        self.active_borrows = set()

        # Initialize parameters as unborrowed, unmoved
        for param in func.params:
            state = BorrowState(name=param.name, var_type=param.ty)
            # main's `string[] args` is a borrowed view of process argv (the runtime owns and
            # frees it). Stamp it so a by-value move is a hard error (CE2410), not a silent
            # move that makes the callee free argv (N2).
            if func.name == "main" and self._is_argv_view_param(param.ty):
                state.is_argv_view = True
            self.borrow_state[param.name] = state

        # Check function body
        self._check_block(func.body)

    @staticmethod
    def _is_argv_view_param(ty: Optional[Type]) -> bool:
        """True if `ty` is `string[]` -- the shape of main's borrowed argv parameter."""
        from sushi_lang.semantics.typesys import BuiltinType
        return isinstance(ty, DynamicArrayType) and ty.base_type == BuiltinType.STRING

    def _check_extension(self, ext: ExtendDef) -> None:
        """Check borrow safety for an extension method."""
        # Reset borrow state for new function scope
        self.borrow_state = {}
        self.active_borrows = set()

        # Extension methods have implicit 'self' parameter (not in AST, added by codegen)
        # Initialize explicit parameters
        for param in ext.params:
            self.borrow_state[param.name] = BorrowState(name=param.name, var_type=param.ty)

        # Check method body
        self._check_block(ext.body)

    def _check_block(self, block: Block) -> None:
        """Check borrow safety for a block of statements."""
        for stmt in block.statements:
            self._check_stmt(stmt)

    def _check_stmt(self, stmt: Stmt) -> None:
        """Check borrow safety for a single statement."""
        if isinstance(stmt, Let):
            # Variable declaration - initialize as unborrowed, unmoved
            from sushi_lang.semantics.typesys import ForeignPtrType
            if isinstance(stmt.ty, ForeignPtrType):
                # Foreign `ptr` is exempt from borrow checking: aliasing through a
                # foreign pointer is not tracked. Record the binding but skip any
                # borrow analysis of the initializer's reference semantics.
                self.borrow_state[stmt.name] = BorrowState(name=stmt.name, var_type=stmt.ty)
                self._clear_borrows()
                return
            self.borrow_state[stmt.name] = BorrowState(name=stmt.name, var_type=stmt.ty)
            # Check the initialization expression
            self._check_expr(stmt.value)
            # Closure move-on-bind: `let g = f` transfers a capturing closure's owned env.
            self._reconcile_closure_bind(stmt)
            # A let-binding from a bare owning variable MOVES it (#134): `let b = a`
            # consumes `a`, mirroring the `:=` rebind and call-argument sinks. Copy types
            # (primitives, strings, string-only composites) are untouched; a MemberAccess
            # or method-call RHS is not a Name, so it keeps its continuing-owner copy.
            self._consume(stmt.value, ConsumingUse.LET)
            # Clear any borrows from the expression
            self._clear_borrows()

        elif isinstance(stmt, Rebind):
            # Variable or field rebinding - check if source is borrowed
            # For simple rebind (x := value), target is a Name
            # For field rebind (obj.field := value), target is a MemberAccess
            if isinstance(stmt.target, Name):
                # Simple variable rebinding
                var_name = stmt.target.id
                if var_name in self.borrow_state:
                    state = self.borrow_state[var_name]

                    # Reference parameters: only &poke allows modification
                    if isinstance(state.var_type, ReferenceType):
                        # Check if it's a &peek reference (read-only)
                        if state.var_type.is_peek():
                            self.err.emit(er.ERR.CE2408, stmt.loc, name=var_name)
                        # &poke references allow rebind (mutable reference semantics)
                    elif state.is_borrowed:
                        self.err.emit(er.ERR.CE2401, stmt.loc, name=var_name)

            elif isinstance(stmt.target, MemberAccess):
                # Field rebinding (obj.field := value)
                # We need to check if the receiver (obj) is borrowed
                # The field rebinding itself is always allowed since we're mutating in place
                self._check_expr(stmt.target)

            # Check the value expression
            self._check_expr(stmt.value)
            # Both rebind shapes take ownership of the value: `x := v` replaces what `x`
            # owned, `obj.field := v` replaces what the field owned. Only the first was
            # ever classified, which is why a field assignment was not a recognised
            # position at all.
            if isinstance(stmt.target, Name):
                self._consume(stmt.value, ConsumingUse.REBIND)
            elif isinstance(stmt.target, MemberAccess):
                self._consume(stmt.value, ConsumingUse.FIELD_ASSIGN)
            # Clear any borrows from the expression
            self._clear_borrows()

        elif isinstance(stmt, Return):
            self._check_expr(stmt.value)
            self._clear_borrows()

        elif isinstance(stmt, Print) or isinstance(stmt, PrintLn):
            self._check_expr(stmt.value)
            self._clear_borrows()

        elif isinstance(stmt, ExprStmt):
            self._check_expr(stmt.expr)
            # Method calls like .destroy() need special handling
            if isinstance(stmt.expr, (MethodCall, DotCall)):
                if stmt.expr.method == "destroy":
                    # Check if the variable being destroyed is borrowed
                    if isinstance(stmt.expr.receiver, Name):
                        var_name = stmt.expr.receiver.id
                        if var_name in self.borrow_state:
                            state = self.borrow_state[var_name]
                            if state.is_borrowed:
                                self.err.emit(er.ERR.CE2402, stmt.loc, name=var_name)
                            # Mark variable as destroyed
                            state.is_destroyed = True
            self._clear_borrows()

        elif isinstance(stmt, If):
            # Evaluate every arm from a common pre-if move-state and JOIN the results: a
            # variable is moved after the `if` iff it is moved on ANY path (Rust semantics).
            # Without the per-arm snapshot/restore, a move in one arm leaked into its sibling
            # arms and past the `if`, producing a SPURIOUS CE2405 (test_move_in_branch_arms).
            entry = self._snapshot_flow()
            after = FlowFacts()
            for cond_expr, arm_block in stmt.arms:
                self._restore_flow(entry)
                self._check_expr(cond_expr)
                self._clear_borrows()
                self._check_block(arm_block)
                after |= self._snapshot_flow()
            if stmt.else_block:
                self._restore_flow(entry)
                self._check_block(stmt.else_block)
                after |= self._snapshot_flow()
            else:
                # No else arm: the fall-through path (no arm taken) changes nothing beyond entry.
                after |= entry
            self._restore_flow(after)

        elif isinstance(stmt, While):
            self._check_expr(stmt.cond)
            self._clear_borrows()
            self._check_loop_body(stmt.body)

        elif isinstance(stmt, Foreach):
            self._check_expr(stmt.iterable)
            self._clear_borrows()
            # The loop variable BORROWS the container's element -- that is what the backend
            # creates (`register_cleanup=False` in statements/loops.py), and registering it
            # here as an owned local is what made a foreach binding used by value report a
            # CE2405 whose "moved here" note pointed at the same span as the use.
            self.borrow_state[stmt.item_name] = BorrowState(
                name=stmt.item_name, var_type=stmt.item_type,
                is_borrowed_binding=True,
                bound_at_span=stmt.item_name_span or stmt.loc,
            )
            self._check_loop_body(stmt.body)

        elif isinstance(stmt, Match):
            self._check_expr(stmt.scrutinee)
            self._clear_borrows()
            for arm in stmt.arms:
                # Add pattern bindings to scope (recursive for nested patterns). The
                # scrutinee type is what gives each binding a var_type; Pass 2 stamps it
                # (that is what CE0121 guards) and this pass used to ignore it.
                if isinstance(arm.pattern, Pattern):
                    self._register_pattern_bindings(arm.pattern, stmt.resolved_scrutinee_type)
                # Check arm body
                if isinstance(arm.body, Block):
                    self._check_block(arm.body)
                else:
                    self._check_expr(arm.body)
                    self._clear_borrows()

        elif isinstance(stmt, Break) or isinstance(stmt, Continue):
            pass  # No borrow checking needed

    def _snapshot_flow(self) -> FlowFacts:
        """The moved / destroyed facts (for branch and loop control-flow joins)."""
        return FlowFacts(
            moved=frozenset(n for n, s in self.borrow_state.items() if s.is_moved),
            destroyed=frozenset(n for n, s in self.borrow_state.items() if s.is_destroyed),
        )

    def _restore_flow(self, facts: FlowFacts) -> None:
        """Set each variable's moved/destroyed flags to exactly what `facts` says.

        Used to reset to a snapshot before checking an alternative path (an `if` arm) and to
        install a join / loop fixed-point state afterwards. Only these two flags are
        restored; borrow counts are cleared per statement by _clear_borrows.
        """
        for name, state in self.borrow_state.items():
            state.is_moved = name in facts.moved
            state.is_destroyed = name in facts.destroyed

    def _check_loop_body(self, body: Block) -> None:
        """Borrow-check a loop body to a fixed point so the back edge is honoured.

        A single forward pass misses a use-after-move across iterations: a value moved in
        the body is moved at the TOP of every iteration after the first, but a one-shot walk
        checks the use before the move marks it (test_err_move_in_loop). `is_moved` only ever
        goes false->true, so two passes reach the fixed point: a silent discovery pass finds
        everything the body moves, then a real pass re-checks from that post-move state and
        reports a use of an already-moved variable exactly once. Suppression is saved/restored
        so a nested loop's own discovery pass does not un-suppress this one.
        """
        entry = self._snapshot_flow()
        prev_suppressed = self.err.suppressed
        self.err.suppressed = True
        self._check_block(body)
        self.err.suppressed = prev_suppressed
        fixed_point = entry | self._snapshot_flow()
        self._restore_flow(fixed_point)
        self._check_block(body)
        # A variable moved (or destroyed) anywhere in the loop is so after it (conservative join).
        self._restore_flow(fixed_point)

    def _register_pattern_bindings(self, pattern: Pattern,
                                   scrutinee_type: Optional[Type] = None) -> None:
        """Register a match arm's payload bindings, WITH their types.

        A payload binding is a read-only BORROW of storage the scrutinee still owns
        (docs/design/ownership-conventions.md S8) -- which is what the backend creates
        (`register_cleanup=False` in statements/matching.py).

        The `var_type` half is what makes this bug class diagnosable at all. Before it,
        every binding was registered as a bare `BorrowState(name=binding)` with no type, so
        `type_moves_by_value(None)` was always False and a match binding could never be
        classified as owning anything -- the eight positions that got (BORROWED, MOVE)
        wrong could not have been caught here even in principle. The types come from the
        variant Pass 2 already resolved for the backend.
        """
        variant_types = self._variant_payload_types(scrutinee_type, pattern.variant_name)
        span = pattern.variant_name_span or pattern.loc

        for index, binding in enumerate(pattern.bindings):
            payload_type = variant_types[index] if index < len(variant_types) else None
            if isinstance(binding, str):
                if binding != "_":  # Skip wildcard bindings
                    self.borrow_state[binding] = BorrowState(
                        name=binding, var_type=payload_type,
                        is_borrowed_binding=True, bound_at_span=span,
                    )
            elif isinstance(binding, Pattern):
                # Nested pattern: its own bindings are typed by the payload enum it matches.
                self._register_pattern_bindings(binding, payload_type)
            else:
                # An OwnPattern auto-unwraps `Own@(T)`; its inner pattern binds the pointee.
                inner = getattr(binding, "pattern", None)
                if isinstance(inner, Pattern):
                    self._register_pattern_bindings(inner, self._own_payload(payload_type))
                elif isinstance(inner, str) and inner != "_":
                    self.borrow_state[inner] = BorrowState(
                        name=inner, var_type=self._own_payload(payload_type),
                        is_borrowed_binding=True, bound_at_span=span,
                    )

    def _variant_payload_types(self, enum_type: Optional[Type],
                               variant_name: str) -> tuple:
        """The associated types of `variant_name`, or () when the enum is not resolved.

        Empty is the safe answer: it leaves every binding untyped, which is exactly the
        pre-existing behaviour, so an unresolved scrutinee degrades to "cannot classify"
        rather than to a wrong classification.
        """
        from sushi_lang.semantics.typesys import EnumType as _EnumType
        resolved = self._resolve_named(enum_type)
        if not isinstance(resolved, _EnumType):
            return ()
        variant = resolved.get_variant(variant_name)
        return tuple(variant.associated_types) if variant is not None else ()

    @staticmethod
    def _own_payload(ty: Optional[Type]) -> Optional[Type]:
        """The `T` inside an `Own@(T)`, for an OwnPattern's inner binding."""
        from sushi_lang.semantics.generics.types import GenericTypeRef
        if isinstance(ty, GenericTypeRef) and ty.base_name == "Own" and ty.type_args:
            return ty.type_args[0]
        return None

    def _resolve_named(self, ty: Optional[Type]):
        """Resolve an `UnknownType` against the struct/enum tables; identity otherwise.

        The single resolver this pass hands to `semantics.ownership.type_class_of`, so
        the classification and the pattern-binding lookup cannot disagree about what a
        name means.
        """
        from sushi_lang.semantics.typesys import UnknownType
        if not isinstance(ty, UnknownType) or self.tables is None:
            return ty
        structs = getattr(getattr(self.tables, "structs", None), "by_name", None) or {}
        enums = getattr(getattr(self.tables, "enums", None), "by_name", None) or {}
        return structs.get(ty.name) or enums.get(ty.name) or ty

    def _check_expr(self, expr: Expr) -> None:
        """Check borrow safety for an expression."""
        if isinstance(expr, Borrow):
            # Borrow expression: &variable
            self._check_borrow(expr)

        elif isinstance(expr, Name):
            # Variable reference - check if it's moved or destroyed
            if expr.id in self.borrow_state:
                state = self.borrow_state[expr.id]
                if state.is_moved:
                    self._emit_use_after_move(expr.id, expr.loc, state)
                elif state.is_destroyed:
                    self.err.emit(er.ERR.CE2406, expr.loc, name=expr.id)

        elif isinstance(expr, Call):
            # Check the callee (a moved closure used as `f(x)` is a use-after-move) and
            # all arguments. A top-level fn name is not in borrow_state, so it is inert.
            self._check_expr(expr.callee)
            for arg in expr.args:
                self._check_expr(arg)

            # A by-value owning argument (dynamic array / List / Own) is MOVED into the
            # callee. Borrows are spelled explicitly at the call site (`&peek x` is a
            # Borrow node, not a Name), so a bare owning Name argument is by definition
            # by-value and therefore moved. This holds uniformly for ordinary function
            # calls, indirect closure calls, and struct constructors.
            for arg in expr.args:
                self._consume(arg, ConsumingUse.CALL_ARG)

            # A callee that destroys its `&poke` parameter destroys the CALLER's value
            # (#168). Without this the borrow checker only ever saw a literal
            # `x.destroy()` in the same function, so `wreck(&poke map)` left `map` looking
            # live and the next `map.insert(...)` was a use-after-destroy that compiled.
            # CE2406 still fires from the Name arm above -- no new emit site.
            self._apply_destroy_effects(expr)

        elif isinstance(expr, MethodCall):
            self._check_expr(expr.receiver)
            for arg in expr.args:
                self._check_expr(arg)
            self._maybe_mark_container_insert(expr)
            self._maybe_mark_own_alloc_move(expr)

        elif isinstance(expr, DotCall):
            # DotCall is the unified X.Y(args) node used before type checking
            # Check receiver and arguments (same as MethodCall)
            self._check_expr(expr.receiver)
            for arg in expr.args:
                self._check_expr(arg)
            # An enum constructor is an ownership sink (#134), exactly like a `from([...])`
            # element or an array-literal element: the enum stores the payload shallowly and
            # frees it, so a bare owning Name argument MOVES. `Box.Full(a)` reaches this pass
            # as a DotCall, not an EnumConstructor, which is why the sink was missed -- the
            # backend moved the payload while the checker stayed silent, so a later use of
            # `a` read through a stale descriptor and printed plausible WRONG data.
            if self._is_enum_constructor(expr):
                for arg in expr.args:
                    self._consume(arg, ConsumingUse.ENUM_PAYLOAD)
            else:
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
                # Same ownership sink as the DotCall spelling above, for whichever paths
                # hand the checker an already-resolved EnumConstructor node.
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
            # Move-capture: an owned captured value (dynamic array / List / Own /
            # capturing closure) is moved into the closure's environment, so a later
            # use of the outer binding is a use-after-move (CE2405). Copyable captures
            # (primitives, strings) stay usable.
            for cap in (expr.captures or []):
                if isinstance(cap.name, str) and cap.name in self.borrow_state:
                    state = self.borrow_state[cap.name]
                    # Closure capture stays on is_owning_type, NOT the move-by-value flip
                    # (#134 spec §3: capture is unchanged; the backend capture path is not
                    # flipped, so an owning-struct capture copy-captures as before).
                    if is_owning_type(cap.ty):
                        state.is_moved = True
                        state.moved_at_span = state.moved_at_span or expr.loc

        elif isinstance(expr, Spread):
            # Bloom: `arr...`. The source is USED here (so a moved source is reported)
            # and, in a call-argument position, MOVED -- see _consume,
            # which the Call arm runs over every argument after checking them.
            self._check_expr(expr.value)

        elif isinstance(expr, RangeExpr):
            self._check_expr(expr.start)
            self._check_expr(expr.end)

        elif isinstance(expr, _INERT_EXPRS):
            # A leaf that owns nothing and names nothing: there is nothing to check.
            pass

        else:
            # NOT a silent fall-through. An expression node with no arm gets no borrow
            # checking at all, which is a soundness hole, not a crash -- exactly how the
            # bloom use-after-free (#174), the unchecked range bound (#175) and the
            # unchecked perk body (#176) survived. The CI gate is
            # tests/unit/test_borrow_dispatch_is_total.py; this is the backstop.
            er.raise_internal_error("CE0125", node=type(expr).__name__)

    def _check_borrow(self, borrow: Borrow) -> None:
        """Check borrow expression: &peek expr or &poke expr

        Supports:
        - Variables: &peek x, &poke x
        - Member access: &peek obj.field, &poke obj.nested.field

        Borrow rules:
        - Multiple &peek borrows allowed (read-only)
        - Only one &poke borrow at a time (exclusive)
        - Cannot have &peek and &poke borrows simultaneously
        """
        is_poke = borrow.mutability == "poke"

        if isinstance(borrow.expr, Name):
            # Variable borrows
            var_name = borrow.expr.id

            # Check if variable exists in borrow state
            if var_name not in self.borrow_state:
                self.err.emit(er.ERR.CE2400, borrow.loc, name=var_name)
                return

            state = self.borrow_state[var_name]

            # Check if variable has been moved
            if state.is_moved:
                self._emit_use_after_move(var_name, borrow.loc, state)
                return

            # Check borrow compatibility based on mode
            if is_poke:
                # &poke: exclusive borrow - no other borrows allowed
                if state.poke_borrow_count > 0:
                    self.err.emit_with(er.ERR.CE2403, borrow.loc, name=var_name) \
                        .note("first borrowed here", state.first_borrow_span).emit()
                    return
                if state.peek_borrow_count > 0:
                    self.err.emit_with(er.ERR.CE2407, borrow.loc, name=var_name) \
                        .note("first borrowed here", state.first_borrow_span).emit()
                    return
                # Warn when creating &poke of a variable that is itself a &poke reference
                # This is a nested mutable borrow - potentially dangerous but allowed
                if isinstance(state.var_type, ReferenceType) and state.var_type.is_poke():
                    self.err.emit(er.ERR.CW2409, borrow.loc, name=var_name)
                state.poke_borrow_count = 1
                state.first_borrow_span = borrow.loc
            else:
                # &peek: shared borrow - only check for poke conflict
                if state.poke_borrow_count > 0:
                    self.err.emit_with(er.ERR.CE2407, borrow.loc, name=var_name) \
                        .note("first borrowed here", state.first_borrow_span).emit()
                    return
                if state.peek_borrow_count == 0:
                    state.first_borrow_span = borrow.loc
                state.peek_borrow_count += 1

            self.active_borrows.add(var_name)

        elif isinstance(borrow.expr, MemberAccess):
            # Member access borrows
            base = self._get_member_access_base(borrow.expr)

            if not isinstance(base, Name):
                expr_str = self._expr_to_string(borrow.expr)
                self.err.emit(er.ERR.CE2404, borrow.loc, expr=expr_str)
                return

            # Check if base variable exists and is not moved
            base_var = base.id
            if base_var not in self.borrow_state:
                self.err.emit(er.ERR.CE2400, borrow.loc, name=base_var)
                return

            state = self.borrow_state[base_var]
            if state.is_moved:
                self._emit_use_after_move(base_var, borrow.loc, state)
                return

            # Check borrow compatibility based on mode
            if is_poke:
                # &poke: exclusive borrow - no other borrows allowed
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
                # &peek: shared borrow - only check for poke conflict
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
        """Get the base variable of a member access chain.

        Examples:
        - obj.field -> obj
        - obj.nested.field -> obj
        - obj.a.b.c -> obj

        Args:
            expr: The member access expression.

        Returns:
            The base expression (typically a Name node).
        """
        current = expr
        while isinstance(current, MemberAccess):
            current = current.receiver
        return current

    def _is_enum_constructor(self, expr: Expr) -> bool:
        """Is this `X.Y(args)` an enum constructor rather than a method call?

        Both spellings arrive as a DotCall, so the receiver must be matched against the
        known enum type names. Generic enums are interned under their concrete names
        ("Result<i32, StdError>") while the receiver is written bare ("Result"), so
        `enum_names` carries base names only. A local shadowing the type name would be a
        Name in borrow_state; the receiver of a constructor never is.
        """
        receiver = getattr(expr, 'receiver', None)
        if not isinstance(receiver, Name):
            return False
        return receiver.id in self.enum_names and receiver.id not in self.borrow_state

    # `List.push`/`.insert`, `HashMap.insert` and `T[].push` store the argument and free
    # it themselves, so each is a consuming use. Only the METHOD NAME is matched loosely;
    # the receiver must be a container, so a user extension method that happens to be
    # called `push` is not swept up.
    _CONTAINER_INSERT_METHODS = frozenset({"push", "insert"})

    def _maybe_mark_container_insert(self, expr: Expr) -> None:
        """`l.push(x)` / `m.insert(k, v)` takes ownership -- the CONTAINER_INSERT use.

        The backend has always MOVED here (`move_owning_arg_into_container` marks the
        source), while this pass marked nothing: only `Own.alloc` and enum constructors
        were recognised among method calls. So `l.push(a)` followed by a read of `a`
        compiled clean and read through a pointer the List owns -- and after the List is
        destroyed the same read is a use-after-free returning whatever the allocator left.
        `docs/design/move-semantics.md:120` called this sink "already move-shaped"; it was
        move-shaped in codegen only.
        """
        if getattr(expr, "method", None) not in self._CONTAINER_INSERT_METHODS:
            return
        receiver = getattr(expr, "receiver", None)
        if not isinstance(receiver, Name):
            return
        state = self.borrow_state.get(receiver.id)
        if state is None or not self._is_container_type(state.var_type):
            return
        for arg in expr.args:
            self._consume(arg, ConsumingUse.CONTAINER_INSERT)

    def _is_container_type(self, ty: Optional[Type]) -> bool:
        """Is `ty` a `List@(T)`, `HashMap@(K, V)` or a dynamic array `T[]`?

        `Own@(T)` is deliberately absent: it has no insert method, and its `.get()` is a
        deref through a live owner rather than a copy-out (see `is_own_type`).
        """
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
        """Track capturing-closure ownership across `let` bindings.

        Capture is erased from the `fn(...)` type, so a closure's heap-env ownership is
        tracked by binding provenance: a capturing lambda literal owns its env, and a
        plain rebind `let g = f` MOVES that ownership (a later use of `f` is CE2405, the
        same move semantics as arrays/List/Own). Non-capturing fn values are copyable and
        untracked, so plain fn-ref code keeps working."""
        from sushi_lang.semantics.typesys import FunctionType
        if not isinstance(stmt.ty, FunctionType):
            return
        dest = self.borrow_state.get(stmt.name)
        if dest is None:
            return
        value = stmt.value
        if isinstance(value, Lambda) and value.captures:
            dest.is_owning_closure = True
        elif isinstance(value, Name):
            src = self.borrow_state.get(value.id)
            if src is not None and src.is_owning_closure:
                src.is_moved = True
                src.moved_at_span = src.moved_at_span or value.loc
                dest.is_owning_closure = True

    def _emit_use_after_move(self, name: str, use_span: Optional[Span],
                             state: BorrowState) -> None:
        """Report a use-after-move, pointing at the MOVE as well as the use.

        Where the value was used is the half the user already knows -- they are
        looking at it. Where it was moved is the half they need.
        """
        diag = self.err.emit_with(er.ERR.CE2405, use_span, name=name)
        if state.moved_at_span is not None:
            diag.note(f"'{name}' was moved here", state.moved_at_span)
        diag.emit()

    def _apply_destroy_effects(self, call: Call) -> None:
        """Mark each argument the callee destroys through a `&poke` parameter (#168)."""
        if not isinstance(call.callee, Name):
            return
        for index in self.destroy_effects.get(call.callee.id, ()):
            if index >= len(call.args):
                continue
            arg = call.args[index]
            if isinstance(arg, Borrow):
                arg = arg.expr           # `&poke map` -> `map`
            if isinstance(arg, Name) and arg.id in self.borrow_state:
                self.borrow_state[arg.id].is_destroyed = True

    def _source_provenance(self, expr: Expr) -> Provenance:
        """Where the value at a consuming use came from -- the half only semantics knows.

        The backend cannot compute this. It has cleanup registries and LLVM values, and
        has been asking `is_owned_local` ("is this registered for cleanup?") as a proxy
        for "is this a borrow of something still live?". Those coincide for a `let` local
        and a fresh temporary and diverge for exactly one thing: a binding. This pass has
        the AST, the types, the scopes and `borrow_state` -- it KNOWS a match binding is a
        binding.

        Shapes, and why each is what it is:
          Name in borrow_state    -- a binding or a `&peek`/`&poke` param is BORROWED;
                                     anything else declared here is an OWNED local
          Name not in borrow_state -- a top-level fn reference or a constant: FRESH
          MemberAccess / own.get() -- reads THROUGH a live owner that keeps the storage
          everything else          -- FRESH: a constructor, a call result, `.clone()`, a
                                     literal, and a container get-out (array / List /
                                     HashMap `.get()` deep-copy AT THE READ, so what
                                     arrives here is already nobody else's). `Own@(T).get()`
                                     is the one that does not copy at the read, which is
                                     why it is named above and not here -- that was #256.
        """
        if isinstance(expr, Name):
            state = self.borrow_state.get(expr.id)
            if state is None:
                return Provenance.FRESH
            if state.is_borrowed_binding or isinstance(state.var_type, ReferenceType):
                return Provenance.BORROWED
            return Provenance.OWNED

        if isinstance(expr, MemberAccess):
            return Provenance.THROUGH_OWNER

        if getattr(expr, "method", None) == "get":
            receiver = getattr(expr, "receiver", None)
            if isinstance(receiver, Name):
                state = self.borrow_state.get(receiver.id)
                if state is not None and is_own_type(state.var_type):
                    return Provenance.THROUGH_OWNER

        return Provenance.FRESH

    def _consume(self, expr: Expr, use: ConsumingUse) -> None:
        """Classify a consuming use, stamp the decision, and act on it.

        THE one place the borrow checker decides what happens to a value handed to a
        position that takes ownership. It:

          1. computes the source's `Provenance` (only this pass can),
          2. stamps it on the source AST node for the backend seam to read,
          3. asks the shared `classify()` table what that means for this type, and
          4. marks the source moved (MOVE) or reports CE2411 (REJECT).

        Steps 3 and 4 use the SOURCE's recorded type. That is sound because Pass 2 has
        already validated assignability at this position, so the source and target types
        agree on what they own. Where the source has no recorded type -- a constructor, a
        call -- the provenance is FRESH, which is ADOPT for every type class, so the
        missing type cannot change the answer.

        The backend calls the same `classify()` with the resolved target type and this
        stamped provenance. Two callers, two halves of the input, ONE implementation of
        the rule -- which is what stops them drifting the way eleven inline derivations did.
        """
        # A bloom `arr...` MOVES its source array into the callee -- the backend marks it
        # moved and the callee frees it. CE0120 already restricts the source to a bare
        # array variable, so the inner expression is always a Name. Unwrapping it here is
        # what makes a use-after-bloom a CE2405 instead of a use-after-free (#174).
        if isinstance(expr, Spread):
            expr = expr.value

        provenance = self._source_provenance(expr)
        # Only PROVENANCE is stamped. The `use` is the backend's to name: semantics cannot
        # tell `S(x)` from `f(x)` (both are a `Call` here) while the backend knows exactly
        # which position it is emitting, so stamping a guess would be a second, weaker
        # answer to a question that already has an authoritative one.
        expr.ownership_provenance = provenance

        if not isinstance(expr, Name):
            return  # only a named source has an owner to move or a binding to reject

        state = self.borrow_state.get(expr.id)
        if state is None:
            return

        if state.is_argv_view:
            # Moving main's borrowed argv view would double-free process argv (N2). A more
            # specific diagnostic than CE2411, so it wins.
            self.err.emit(er.ERR.CE2410, expr.loc, name=expr.id)
            return

        decision = classify(provenance, self._type_class(state.var_type))
        if decision is Ownership.MOVE:
            state.is_moved = True
            state.moved_at_span = state.moved_at_span or expr.loc
        elif decision is Ownership.REJECT:
            self._emit_consume_of_borrow(expr.id, expr.loc, state)

    def _emit_consume_of_borrow(self, name: str, use_span: Optional[Span],
                                state: BorrowState) -> None:
        """Report CE2411, pointing at the binding as well as the use.

        A relational error: consuming this value is only wrong BECAUSE the name is a
        borrow of storage something else still owns. Rendering it with one location would
        show the user a rule without the reason for it.
        """
        diag = self.err.emit_with(er.ERR.CE2411, use_span, name=name)
        if state.bound_at_span is not None:
            diag.note(f"'{name}' borrows here, and the owner keeps the value",
                      state.bound_at_span)
        diag.help(f"clone it to take an independent value: `{name}.clone()`")
        diag.emit()

    def _type_class(self, ty: Optional[Type]) -> TypeClass:
        """Classify a type as PLAIN / COPY / MOVE, resolving named types first."""
        return type_class_of(ty, self._resolve_named)

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
        elif isinstance(expr, MethodCall):
            return f"{self._expr_to_string(expr.receiver)}.{expr.method}(...)"
        elif isinstance(expr, MemberAccess):
            return f"{self._expr_to_string(expr.receiver)}.{expr.member}"
        else:
            return "<expression>"
