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
from sushi_lang.semantics.typesys import ReferenceType, DynamicArrayType, Type
from sushi_lang.semantics.ownership import (
    ConsumingUse, Ownership, Provenance, TypeClass, classify, is_get_out_container,
    type_class_of,
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
    is_destroyed: bool = False  # Variable has been explicitly destroyed (via .destroy())
    is_argv_view: bool = False  # main's `string[] args`: a borrowed view of process argv;
                                # moving it by value would free argv, so it is a hard error
    is_borrowed_binding: bool = False  # A `match` payload binding, a `foreach` item, or a
                                # `let` bound from any read through a live owner: a
                                # READ-ONLY borrow of storage something else still owns
                                # (docs/design/ownership-conventions.md S8).
                                # Distinct from is_argv_view, which is one specific borrow
                                # with its own diagnostic, and from a ReferenceType param,
                                # which is spelled `&peek`/`&poke` in the source.
    owns_no_heap: bool = False  # Option B (MM.md S0.4): this binding's CURRENT value owns no
                                # heap, so a consuming use of it transfers nothing. Today only
                                # a `string` bound directly from a literal sets it.
                                #
                                # THIS LIVES ON THE BINDING AND NOT ON THE TYPE ON PURPOSE. A
                                # closure's answer lives in `FunctionType.captures` because
                                # FunctionType is a dataclass; `BuiltinType.STRING` is an enum
                                # member with nowhere to put a flag. The asymmetry is
                                # structural -- do not "fix" it by inventing a string subtype.
                                #
                                # Must be RE-DERIVED on every rebind, never inherited: after
                                # `a := "Hi {name}"` the binding owns a buffer. A conditional
                                # rebind is unknowable, so it falls back to False.
                                #
                                # Default False = "assume it owns heap", the same safe fallback
                                # an unstated `captures` takes (MM.md finding A1).
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
                                # changed or released. Set instead of reporting, so CE2412
                                # fires only if the binding is read afterwards -- Rust's
                                # non-lexical lifetimes, and the same deferred shape
                                # `is_moved` already uses for CE2405.
    invalidated_by: tuple = ()  # (owner name, what the change was), for that diagnostic.
    binding_borrows: list = field(default_factory=list)  # On the OWNER: every live
                                # `let`-borrow binding reading out of it, as
                                # (binding name, `let` span). Mutating the owner while this
                                # list is non-empty is CE2412 -- Rust's E0502.
                                #
                                # Deliberately NOT one of the counters above: `_clear_borrows`
                                # zeroes those at the end of every statement, because an
                                # explicit `&peek x` lives only for the expression that made
                                # it. A binding borrow lives to the end of its lexical scope,
                                # so `_check_block` releases it instead.
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


# Methods that change or release what a container holds. A live `let`-borrow binding out of
# the receiver cannot survive any of them, so each is CE2412 -- Rust's E0502.
#
# Drawn from `BUILTIN_LIST_METHODS`, the HashMap method set and the array method set, and
# kept as ONE set: three copies would drift, and a name missing from any copy is a silent
# dangling borrow rather than a wrong diagnostic.
_MUTATING_METHODS = frozenset({
    "push", "pop", "insert", "remove", "clear", "reserve", "shrink_to_fit",
    "rehash", "destroy", "free", "fill", "reverse",
})


def binds_a_bare_literal_string(declared_ty, init) -> bool:
    """Option B (MM.md S0.4): is this binding a `string` whose value is a plain literal?

    A `StringLit` points into `.rodata` and carries `owned = 0`, so it owns nothing and a
    consuming use of it transfers nothing. An `InterpolatedString` is a DIFFERENT AST node
    that builds a heap buffer at runtime and owns it -- classifying one of those as owning
    nothing would skip a real free. The two being distinct node types is what makes this
    question exact rather than a heuristic.

    Deliberately an ALLOW-LIST (`isinstance(init, StringLit)`) and not a deny-list
    ("anything that is not an InterpolatedString"): every other initializer shape -- a call, a
    `??`, a name, a field read, a container get-out -- must also answer False. A deny-list
    would answer True for all of them.

    The single spelling of this rule. The `let` path and the rebind path both call it, because
    a rebind must RE-DERIVE the answer rather than inherit it.
    """
    from sushi_lang.semantics.ast import StringLit
    from sushi_lang.semantics.typesys import BuiltinType
    return declared_ty == BuiltinType.STRING and isinstance(init, StringLit)


def _split_type_args(args: str) -> list[str]:
    """Split an interned type-argument list on its TOP-LEVEL commas.

    `HashMap<i32, List<i32>>` carries `i32, List<i32>`, and a plain `split(",")` would cut
    the nested argument in half. Depth counting is all that is needed, because the interned
    spelling is always balanced.
    """
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
        # One frame per open block, holding the (owner, binding) pairs that block
        # registered. `_check_block` pops its frame and releases them, which is what gives
        # a `let`-borrow binding a LEXICAL lifetime. `active_borrows` cannot serve: it is
        # emptied after every statement, and a binding outlives the statement that made it.
        self._scope_binding_borrows: list[list[tuple[str, str]]] = []

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
        self._scope_binding_borrows = []

        # Initialize parameters as unborrowed, unmoved
        for param in func.params:
            state = BorrowState(name=param.name, var_type=param.ty,
                                declared_at_span=getattr(param, "loc", None))
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
        """Check borrow safety for a block of statements.

        A block is also the LIFETIME of every `let`-borrow binding declared in it (#242).
        The frame pushed here collects them, and the release below ends them, so mutating
        the owner after the block is legal and mutating it inside is CE2412. This is the
        only lexical-scope notion in the pass, and it is deliberately the coarse one:
        Rust's non-lexical lifetimes would end the borrow at its last use instead.
        """
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
            # Variable declaration - initialize as unborrowed, unmoved
            from sushi_lang.semantics.typesys import ForeignPtrType
            if isinstance(stmt.ty, ForeignPtrType):
                # Foreign `ptr` is exempt from borrow checking: aliasing through a
                # foreign pointer is not tracked. Record the binding but skip any
                # borrow analysis of the initializer's reference semantics.
                #
                # The ownership stamp is NOT skipped. The exemption is about aliasing
                # analysis, not about classification: a `ptr` is an unmanaged handle, so
                # it classifies as PLAIN and the decision is ADOPT. Skipping the stamp
                # left the backend's `let` position with no decision to read at all --
                # CE0129 on the first FFI program that binds one.
                self.borrow_state[stmt.name] = BorrowState(
                    name=stmt.name, var_type=stmt.ty, declared_at_span=stmt.loc)
                self._bind(stmt)
                self._clear_borrows()
                return
            self.borrow_state[stmt.name] = BorrowState(
                name=stmt.name, var_type=stmt.ty, declared_at_span=stmt.loc,
                # Option B (MM.md S0.4): a string bound straight from a literal owns no heap,
                # so consuming it transfers nothing and CE2405 must not fire on it.
                owns_no_heap=binds_a_bare_literal_string(stmt.ty, stmt.value))
            # Check the initialization expression
            self._check_expr(stmt.value)
            # Closure move-on-bind: `let g = f` transfers a capturing closure's owned env.
            self._reconcile_closure_bind(stmt)
            # A `let` BINDS; it does not take ownership (#242). The binding inherits the
            # source's provenance, so a bare owning variable still MOVES, and a read
            # through a live owner now makes the binding a BORROW instead of making the
            # compiler insert a deep copy.
            self._bind(stmt)
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

                    # Option B (MM.md S0.4): RE-DERIVE, never inherit. `let string a = "hi"`
                    # owns nothing, but after `a := "Hi {name}"` it owns a buffer. Every
                    # non-literal initializer answers False here, which also covers the
                    # conditional rebind the decision calls unknowable -- the fallback is
                    # "assume owning", so a rebind can only ever CLEAR this flag, never set it
                    # on a value that owns heap.
                    state.owns_no_heap = binds_a_bare_literal_string(
                        state.var_type, stmt.value)

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
            # Replacing what the owner holds invalidates every binding reading out of
            # it, whether the target is the owner itself or one of its fields (#242).
            self._check_owner_not_borrowed(
                self._root_owner(stmt.target), stmt.loc, "assign")
            if isinstance(stmt.target, Name):
                self._consume(stmt.value, ConsumingUse.REBIND)
            elif isinstance(stmt.target, MemberAccess):
                self._consume(stmt.value, ConsumingUse.FIELD_ASSIGN)
            # Clear any borrows from the expression
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
                inner = getattr(binding, "inner_pattern", None)
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

    def _own_payload(self, ty: Optional[Type]) -> Optional[Type]:
        """The `T` inside an `Own@(T)`, for an OwnPattern's inner binding.

        Both spellings must be handled. Before monomorphization the payload is a
        `GenericTypeRef("Own", [T])`; after it, an interned `StructType` named `Own<T>`
        whose single field is `T*`. Recognising only the first left the binding untyped,
        which classifies as PLAIN -- so an `Own(tail)` binding of an owning enum was
        adopted rather than copied, and the pointee was freed twice.
        """
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
                elif state.invalidated_at is not None:
                    # A `let`-borrow binding read after its owner changed (#242).
                    self._emit_use_of_invalidated_borrow(expr.id, expr.loc, state)

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
            self._maybe_reject_mutation(expr)
            self._maybe_mark_container_insert(expr)
            self._maybe_mark_own_alloc_move(expr)

        elif isinstance(expr, DotCall):
            # DotCall is the unified X.Y(args) node used before type checking
            # Check receiver and arguments (same as MethodCall)
            #
            # DECISION 1 (MM.md S2.F): an FFI string argument NEVER consumes. `libc.strlen(s)`
            # must not move `s` -- the callee is C, it cannot own a Sushi string, and the
            # marshalled buffer is already freed at scope exit. Rust passes `as_ptr(&self)`,
            # Zig takes `[*:0]const u8`, cgo leaves the pointer caller-owned.
            #
            # That holds here STRUCTURALLY rather than by a condition: an FFI call arrives as a
            # DotCall (`external_ref` is stamped by Pass 2), and this arm consumes only for an
            # enum constructor and a container insert -- `ConsumingUse.CALL_ARG` is reached
            # from the `Call` arm alone. Verified under the all-strings-move prototype: the
            # FFI corpus compiles clean.
            #
            # **Do not add a blanket `_consume(arg, CALL_ARG)` loop to this arm.** It would
            # make every FFI argument a move and every `libc.*(s)` call site a false CE2405.
            # `tests/ffi/test_ffi_string_arg_not_consumed.sushi` is the gate, and it becomes
            # load-bearing the moment `string` starts moving.
            self._check_expr(expr.receiver)
            for arg in expr.args:
                self._check_expr(arg)
            self._maybe_reject_mutation(expr)
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
            # A captured slot is the CAPTURE consuming use: the heap environment takes
            # ownership of the value, and it outlives the scope that created it. So the
            # decision is the shared table's, exactly like the other ten positions.
            #
            # A capture holds no source `Expr` -- `Lambda.captures` is a list of `Param`
            # -- so the provenance goes on the `Param` itself, and `emit_lambda` reads it
            # from there. `Param` is an unslotted dataclass, and this pass runs after
            # lambda lifting, so the backend sees the objects stamped here.
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

            # A `&poke` may mutate or free, so it conflicts with a live `let`-borrow
            # binding exactly as a mutating method does (#242). Reported as CE2412 rather
            # than CE2407, because the user wrote no `&peek` and CE2407's text would name
            # a borrow they cannot see.
            if is_poke:
                self._check_owner_not_borrowed(var_name, borrow.loc, "take `&poke`")

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

            if is_poke:
                self._check_owner_not_borrowed(base_var, borrow.loc, "take `&poke`")

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

        The backend has always MOVED here, while this pass marked nothing: only
        `Own.alloc` and enum constructors
        were recognised among method calls. So `l.push(a)` followed by a read of `a`
        compiled clean and read through a pointer the List owns -- and after the List is
        destroyed the same read is a use-after-free returning whatever the allocator left.
        `docs/design/move-semantics.md:120` called this sink "already move-shaped"; it was
        move-shaped in codegen only.
        """
        if getattr(expr, "method", None) not in self._CONTAINER_INSERT_METHODS:
            return
        if not self._is_container_type(self._expr_type(getattr(expr, "receiver", None))):
            return
        for arg in expr.args:
            self._consume(arg, ConsumingUse.CONTAINER_INSERT)

    def _expr_type(self, expr) -> Optional[Type]:
        """Best-effort type of a receiver expression: a local, or a field reached from one.

        `c.numbers.push(10)` is as much a container insert as `numbers.push(10)`, and the
        backend treats them identically. Resolving only the bare-`Name` shape here left
        the field receiver unstamped, which the seam then reported as CE0129 -- the loud
        failure doing exactly its job. Anything more elaborate than a chain of field reads
        off a local answers None, which classifies as PLAIN and stamps nothing extra.
        """
        if isinstance(expr, Name):
            state = self.borrow_state.get(expr.id)
            return state.var_type if state is not None else None
        if isinstance(expr, MemberAccess):
            from sushi_lang.semantics.typesys import StructType as _StructType
            owner = self._resolve_named(self._expr_type(expr.receiver))
            if isinstance(owner, ReferenceType):
                owner = self._resolve_named(owner.referenced_type)
            if isinstance(owner, _StructType):
                return owner.get_field_type(expr.member)
        return None

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
        """Record whether a `fn(...)` binding owns a heap environment.

        `FunctionType.__eq__` excludes `captures` from type identity, so the DECLARED type
        of a `fn(...)` local says nothing about ownership. The initializer says everything:
        a capturing lambda literal owns an environment, and a plain fn reference owns
        nothing. This writes that answer into the binding's recorded type, so the one
        shared classifier reads a precise input here and needs no override of its own.

        The backend cannot do the same. At each position it holds the declared TARGET type
        -- a `List@(fn(i32) -> i32)` element, a struct field, a parameter -- which has
        already lost the captures. So it classifies every function value as owning and
        lets the null `drop_ptr` / `clone_ptr` guard make that answer exact at runtime.
        One rule, two precisions. Being conservative there costs nothing; being
        conservative HERE would report CE2405 for a plain fn reference used twice.

        Runs before the `LET` consume, so marking the source moved stays that call's job.
        """
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
                # `let g = f` hands `f`'s environment, if it has one, to `g`.
                dest.var_type = src.var_type

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
          Name in borrow_state     -- a binding, a `let` bound from a borrow, or a
                                      `&peek`/`&poke` param is BORROWED; anything else
                                      declared here is an OWNED local
          Name not in borrow_state -- a top-level fn reference or a constant: FRESH
          a read through an owner  -- BORROWED. `s.field`, `arr[i]`, and a container
                                      get-out. See `_reads_through_owner`.
          everything else          -- FRESH: a constructor, a call result, `.clone()`, a
                                      literal, and a `List.pop()`, which REMOVES the
                                      element so the container stops owning it.
        """
        if isinstance(expr, Name):
            return self._name_provenance(expr.id)

        if self._reads_through_owner(expr):
            return Provenance.BORROWED

        return Provenance.FRESH

    def _reads_through_owner(self, expr: Optional[Expr]) -> bool:
        """Does `expr` read a value out of storage something else still owns?

        A field read, an index and a container get-out all hand back a value the owner
        keeps and still frees. Until #242 the backend deep-copied at each of these, so the
        result was nobody else's and the honest answer was FRESH. Phase 7 deleted those
        copies, so the result is a view and this predicate had to move with them.

        The get-out arm keys on the RECEIVER'S TYPE, never on the bare method name: a user
        extension method called `get` is an ordinary call that returns a fresh value, and
        classifying it as a container read would report a false CE2411. Where the receiver
        is itself a read through an owner the answer recurses -- a get-out of a borrow is
        a borrow.
        """
        while isinstance(expr, TryExpr):
            # `c.get(i)??` -- the get-out sits under the propagation operator.
            expr = expr.expr

        if isinstance(expr, (MemberAccess, IndexAccess)):
            return True

        if getattr(expr, "method", None) == "get":
            receiver = getattr(expr, "receiver", None)
            if isinstance(receiver, Name):
                state = self.borrow_state.get(receiver.id)
                return state is not None and is_get_out_container(state.var_type)
            return self._reads_through_owner(receiver)

        return False

    def _name_provenance(self, name: str) -> Provenance:
        """The `Provenance` of a source that is a bare name.

        Split out of `_source_provenance` because a lambda capture names a variable
        without holding an `Expr` for it -- `Lambda.captures` is a list of `Param`. Both
        paths must give the same answer, so both call this.
        """
        state = self.borrow_state.get(name)
        if state is None:
            # Not declared in this function: a top-level fn reference or a constant.
            return Provenance.FRESH
        if state.is_borrowed_binding or isinstance(state.var_type, ReferenceType):
            return Provenance.BORROWED
        return Provenance.OWNED

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

        if isinstance(expr, Name):
            self._consume_named(expr.id, provenance, expr.loc)
            return

        # A read through a live owner -- `h.inner`, `rows[i]`, `c.get(0)??` -- has no owner
        # to mark moved, but it CAN be rejected (#242): the owner keeps the value and still
        # frees it, so a position that takes ownership cannot have it. Before let-borrow
        # bindings this cell COPIED, so there was nothing to report and only a Name mattered.
        # Leaving it that way made the backend classify REJECT with no diagnostic ahead of
        # it, which surfaced as CE0129 -- an internal error for a plain user mistake.
        if provenance is not Provenance.BORROWED:
            return
        if classify(provenance, self._type_class(self._read_type(expr))) is Ownership.REJECT:
            self._emit_consume_of_read(expr)

    def _consume_named(self, name: str, provenance: Provenance,
                       use_span: Optional[Span]) -> None:
        """Apply the ownership decision to a source that is a bare name.

        The decision core of `_consume`, split out because a lambda capture reaches it
        without an `Expr`. It marks the source moved, reports CE2411, or does nothing.
        Only a name has an owner to move or a binding to reject.
        """
        state = self.borrow_state.get(name)
        if state is None:
            return

        if state.is_argv_view:
            # Moving main's borrowed argv view would double-free process argv (N2). A more
            # specific diagnostic than CE2411, so it wins.
            self.err.emit(er.ERR.CE2410, use_span, name=name)
            return

        decision = classify(provenance, self._type_class(state.var_type))
        if decision is Ownership.MOVE:
            # Handing the owner away leaves every binding reading out of it pointing at
            # storage the new owner frees (#242).
            self._check_owner_not_borrowed(name, use_span, "move")
            state.is_moved = True
            state.moved_at_span = state.moved_at_span or use_span
        elif decision is Ownership.REJECT:
            self._emit_consume_of_borrow(name, use_span, state)

    def _bind(self, stmt: Let) -> None:
        """Give a `let` binding the provenance of its initializer (#242).

        A `let` BINDS. It does not take ownership. The binding inherits the source's
        provenance, and that one rule produces every shape with no per-shape exception:

            OWNED source, MOVE type  -> move the value;          the binding OWNS
            OWNED source, PLAIN/COPY -> copy the bytes;          the binding OWNS
            BORROWED source          -> borrow the same storage; the binding BORROWS
            FRESH source             -> adopt the value;         the binding OWNS

        So this is `_consume_named` with ONE answer mapped differently: where a position
        that takes ownership reports CE2411, a `let` records a borrowed binding. The rule
        still has exactly one implementation -- both ask `classify()` -- because what
        differs is what the CALLER does with REJECT, not what the table says.
        """
        expr = stmt.value
        provenance = self._source_provenance(expr)
        # Stamped for the backend seam, exactly as `_consume` stamps a consuming use.
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

        # The SOURCE's recorded type where there is one, because it is the more precise
        # input: `_reconcile_closure_bind` writes the initializer's real capture list onto
        # a `fn(...)` local, and the DECLARED type has already lost it -- classifying by
        # the declared type would move a plain fn reference and report a false CE2405.
        ty = src_state.var_type if src_state is not None else stmt.ty

        decision = classify(provenance, self._type_class(ty))
        if decision is Ownership.MOVE:
            # OWNED is the only provenance that reaches MOVE, and only a named local is
            # ever OWNED, so `src_state` is always present here.
            src_state.is_moved = True
            src_state.moved_at_span = src_state.moved_at_span or expr.loc
        elif decision is Ownership.REJECT:
            self._record_borrowed_binding(stmt, dest)

    def _record_borrowed_binding(self, stmt: Let, dest: BorrowState) -> None:
        """Record that a `let` borrows storage its initializer's owner keeps (#242).

        Two halves, and both are needed. The binding is marked BORROWED, so consuming it
        later is CE2411 exactly like a `match` binding. And the OWNER is told, so mutating
        or freeing it while the binding is live is CE2412 -- without that half the binding
        can dangle, which is the whole reason a borrow needs a lifetime.
        """
        dest.is_borrowed_binding = True
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
        """The type a read-through-an-owner expression produces.

        Needed because a BORROWED source is now often NOT a bare name: `h.inner`,
        `rows[i]` and `c.get(0)??` all carry a provenance but no `BorrowState`, so there is
        no recorded type to classify. Without this, a MOVE-typed field read reached the
        backend, classified REJECT there, and became CE0129 -- an internal error where the
        user should have seen CE2411.

        Deliberately partial. It walks only the shapes `_reads_through_owner` recognises,
        and answers None for anything else. None classifies as PLAIN, i.e. "consume it
        freely", which is the answer this pass gave every non-name source before #242 --
        so a gap here can only fail to report, never report falsely.
        """
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
        """What a `.get()` on a receiver of type `ty` reads out.

        An array and a `List@(T)` yield `T`, an `Own@(T)` yields its pointee, and a
        `HashMap@(K, V)` yields `V`. A container `.get()` actually returns `Maybe@(T)`
        rather than `T`, and that difference does not matter here: this answer feeds
        `type_class_of` only, and a `Maybe@(T)` owns heap exactly when its payload does.
        """
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
        # After monomorphization the container is an interned StructType whose NAME
        # carries the type arguments -- `List<i32>`, `HashMap<i32, List<i32>>`. That name
        # IS the identity (#240), so reading the argument back out of it is the supported
        # route, not a workaround. Angle brackets are the internal spelling on purpose.
        if isinstance(ty, _StructType):
            if ty.name.startswith("List<"):
                return self._type_from_name(ty.name[len("List<"):-1])
            if ty.name.startswith("HashMap<"):
                args = _split_type_args(ty.name[len("HashMap<"):-1])
                return self._type_from_name(args[1]) if len(args) == 2 else None
        return self._own_payload(ty)

    def _type_from_name(self, type_str: str) -> Optional[Type]:
        """Resolve one interned type-argument spelling back to a `Type`.

        Shares `type_strings.resolve_type_from_string` with the rest of the compiler, so a
        name this pass reads means the same thing it means everywhere else. The adapter is
        needed because that helper wants `struct_table`/`enum_table` and this pass holds
        `structs`/`enums`.
        """
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
        """The named local a read-through-an-owner expression ultimately reads out of.

        `c.get(0)??` names `c`; `s.inner.data` names `s`; `rows[i]` names `rows`. Returns
        None where the root is not a bare name -- a call result owns itself, so there is
        nothing to keep alive.
        """
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
        """Reject `c.push(x)` while a `let`-borrow binding reads out of `c` (#242).

        A mutating method is not a `Borrow` node, so it reaches none of the existing
        borrow-conflict checks -- those fire only where the user wrote `&peek` / `&poke`.
        This is the arm that makes `let v = c.get(0)??` followed by `c.free()` an error
        instead of a use-after-free.
        """
        if getattr(expr, "method", None) not in _MUTATING_METHODS:
            return
        self._check_owner_not_borrowed(
            self._root_owner(getattr(expr, "receiver", None)), expr.loc,
            f"call `.{expr.method}()`")

    def _check_owner_not_borrowed(self, owner: Optional[str], span: Optional[Span],
                                  what: str) -> None:
        """Reject a change to `owner` while a `let`-borrow binding reads out of it (#242).

        The half that gives a borrowed binding a LIFETIME. Without it the binding is a
        pointer into storage the owner may free, shrink or hand away at any time, and the
        model would trade an implicit deep copy for a dangling read.

        `what` names the action, so the diagnostic says which of the four happened: a
        mutating method, a rebind, a move, or a `&poke` borrow.
        """
        if owner is None:
            return
        state = self.borrow_state.get(owner)
        if state is None or not state.binding_borrows:
            return
        # INVALIDATE, do not report. The change is an error only if the binding is read
        # AFTER it -- Rust's non-lexical lifetimes, and the same shape as `is_moved`, which
        # this pass already reports at the USE rather than at the move. Reporting here
        # instead would reject `let g = fns.get(0)??; g(10); fns.free()`, which is safe:
        # the borrow is dead by the time the owner is freed.
        for name, bound_at in state.binding_borrows:
            binding = self.borrow_state.get(name)
            if binding is not None and binding.invalidated_at is None:
                binding.invalidated_at = span
                binding.invalidated_by = (owner, what)
                binding.bound_at_span = binding.bound_at_span or bound_at
        state.binding_borrows = []

    def _emit_use_of_invalidated_borrow(self, name: str, use_span: Optional[Span],
                                        state: BorrowState) -> None:
        """Report CE2412 at the change, and name the later use that makes it wrong.

        Three locations, because the error needs all three to be explicable: WHAT changed,
        WHERE the borrow came from, and WHICH later read is left dangling. Rust's E0502
        renders the same three.
        """
        owner, what = state.invalidated_by
        diag = self.err.emit_with(er.ERR.CE2412, state.invalidated_at,
                                  owner=owner, name=name)
        if state.bound_at_span is not None:
            diag.note(f"'{name}' borrows from '{owner}' here", state.bound_at_span)
        diag.note(f"'{name}' is used here, after the change", use_span)
        diag.help(f"{what} after the last use of '{name}', "
                  f"or bind an independent value with `.clone()`")
        diag.emit()
        # Report once per binding: the first dangling read is the whole story, and a
        # second one adds a location without adding information.
        state.invalidated_at = None

    def _emit_consume_of_read(self, expr: Expr) -> None:
        """Report CE2411 for a read through a live owner (`h.inner`, `c.get(0)??`).

        Relational like the binding form, but the second location is the OWNER's
        declaration rather than a `let`. Where the root is not a named local there is no
        second location to give, and the message carries the expression instead.
        """
        text = self._expr_to_string(expr)
        diag = self.err.emit_with(er.ERR.CE2411, expr.loc, name=text)
        owner = self._root_owner(expr)
        state = self.borrow_state.get(owner) if owner is not None else None
        if state is not None and state.declared_at_span is not None:
            diag.note(f"'{owner}' owns this value and still frees it",
                      state.declared_at_span)
        # ONE branch, on purpose. A field read and an index take the clone directly and
        # compile; a get-out takes it and hits CE0019, because a chained method call on a
        # call receiver does not resolve its semantic type. That is a real defect, not a
        # reason to word around it -- see MM.md B5. This help states the rule, and the
        # three RED `test_own_get_*` files hold the branch honest until the defect is
        # fixed. A shape-dependent help would have hidden it.
        diag.help(f"clone it to take an independent value: `{text}.clone()`")
        diag.emit()

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
        elif isinstance(expr, (MethodCall, DotCall)):
            # Both spellings reach here. A `DotCall` used to fall to `<expression>`, which
            # made CE2411 for an `own.get()` name a value the user cannot find in the source.
            # Arguments are rendered too, so the text matches what the user wrote and the
            # `help` below is something they can paste.
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
