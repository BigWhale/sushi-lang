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

from sushi_lang.semantics.ast import *
from sushi_lang.semantics.typesys import ReferenceType, DynamicArrayType, Type, is_owning_type
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
                 destroy_effects: Optional[Dict[str, FrozenSet[int]]] = None):
        self.reporter = reporter
        self.err = PassErrorReporter(reporter)
        # fn name -> the &poke param indices it destroys (#168). Computed once over EVERY
        # unit by compute_destroy_effects(), so a cross-unit callee is not a blind spot.
        # Empty means "no call destroys anything", i.e. the old intra-procedural behaviour.
        self.destroy_effects: Dict[str, FrozenSet[int]] = destroy_effects or {}
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
            # Mark source as moved if rebinding from another variable (only for simple variable rebind)
            if isinstance(stmt.target, Name):
                self._mark_moved_if_applicable(stmt.value)
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
            # Declare loop variable
            self.borrow_state[stmt.item_name] = BorrowState(name=stmt.item_name, var_type=stmt.item_type)
            self._check_loop_body(stmt.body)

        elif isinstance(stmt, Match):
            self._check_expr(stmt.scrutinee)
            self._clear_borrows()
            for arm in stmt.arms:
                # Add pattern bindings to scope (recursive for nested patterns)
                if isinstance(arm.pattern, Pattern):
                    self._register_pattern_bindings(arm.pattern)
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

    def _register_pattern_bindings(self, pattern: Pattern) -> None:
        """Recursively register pattern bindings in borrow state."""
        for binding in pattern.bindings:
            if isinstance(binding, str):
                if binding != "_":  # Skip wildcard bindings
                    self.borrow_state[binding] = BorrowState(name=binding)
            elif isinstance(binding, Pattern):
                # Nested pattern - recursively register bindings
                self._register_pattern_bindings(binding)

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
                self._mark_moved_if_applicable(arg)

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
            self._maybe_mark_own_alloc_move(expr)

        elif isinstance(expr, DotCall):
            # DotCall is the unified X.Y(args) node used before type checking
            # Check receiver and arguments (same as MethodCall)
            self._check_expr(expr.receiver)
            for arg in expr.args:
                self._check_expr(arg)
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

        elif isinstance(expr, DynamicArrayFrom):
            for elem in expr.elements.elements:
                self._check_expr(elem)

        elif isinstance(expr, ArrayLiteral):
            for elem in expr.elements:
                self._check_expr(elem)

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
                    if self._type_is_owning(cap.ty):
                        state.is_moved = True
                        state.moved_at_span = state.moved_at_span or expr.loc

        elif isinstance(expr, Spread):
            # Bloom: `arr...`. The source is USED here (so a moved source is reported)
            # and, in a call-argument position, MOVED -- see _mark_moved_if_applicable,
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

    def _maybe_mark_own_alloc_move(self, expr: Expr) -> None:
        """Own.alloc(x) takes ownership of an owning x; mark x moved so a later use of x
        is a use-after-move (CE2405), matching move semantics for arrays/lists/structs."""
        if getattr(expr, 'method', None) != 'alloc':
            return
        receiver = getattr(expr, 'receiver', None)
        if not (isinstance(receiver, Name) and receiver.id == 'Own'):
            return
        for arg in expr.args:
            if isinstance(arg, Name) and arg.id in self.borrow_state:
                state = self.borrow_state[arg.id]
                if self._type_is_owning(state.var_type):
                    state.is_moved = True
                    state.moved_at_span = state.moved_at_span or arg.loc

    def _type_is_owning(self, vt: Optional[Type]) -> bool:
        """True if a value of this type carries heap ownership (so Own.alloc moves it).

        Delegates to the shared `is_owning_type` predicate (typesys) so the borrow
        checker and the backend RAII paths agree on what owns memory — including a
        capturing closure value.
        """
        return is_owning_type(vt)

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

    def _mark_moved_if_applicable(self, expr: Expr) -> None:
        """Mark a variable as moved if the expression is a bare reference to an owning value.

        Move semantics apply to every owning type in Sushi -- dynamic arrays, `List<T>`,
        `Own<T>`, and capturing closures (the shared `is_owning_type` predicate). Primitives,
        strings, and copyable structs are copied, not moved.
        """
        # Rebinding from / passing a bare owning variable transfers ownership:
        # Example: arr1 := arr2  (arr2 is moved if arr2 is owning)
        # Example: x := y        (y is copied if y is a primitive like i32)

        # A bloom `arr...` MOVES its source array into the callee -- the backend marks it
        # moved and the callee frees it. CE0120 already restricts the source to a bare
        # array variable, so the inner expression is always a Name. Unwrapping it here is
        # what makes a use-after-bloom a CE2405 instead of a use-after-free (#174).
        if isinstance(expr, Spread):
            expr = expr.value

        if isinstance(expr, Name):
            if expr.id in self.borrow_state:
                state = self.borrow_state[expr.id]
                if state.is_argv_view:
                    # Moving main's borrowed argv view would double-free process argv (N2).
                    self.err.emit(er.ERR.CE2410, expr.loc, name=expr.id)
                elif self._type_is_owning(state.var_type):
                    state.is_moved = True
                    state.moved_at_span = state.moved_at_span or expr.loc

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
