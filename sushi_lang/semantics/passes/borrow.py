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
from typing import Dict, Set, Optional
from dataclasses import dataclass, field

from sushi_lang.semantics.ast import *
from sushi_lang.semantics.typesys import ReferenceType, DynamicArrayType, Type
from sushi_lang.internals.report import Reporter
from sushi_lang.internals import errors as er
from sushi_lang.semantics.error_reporter import PassErrorReporter


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

    @property
    def is_borrowed(self) -> bool:
        """Returns True if variable has any active borrows."""
        return self.poke_borrow_count > 0 or self.peek_borrow_count > 0


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

    def __init__(self, reporter: Reporter, struct_names = None):
        self.reporter = reporter
        self.err = PassErrorReporter(reporter)
        self.struct_names = struct_names  # Dict of struct names for identifying struct constructors
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

    def _check_function(self, func: FuncDef) -> None:
        """Check borrow safety for a single function."""
        # Reset borrow state for new function scope
        self.borrow_state = {}
        self.active_borrows = set()

        # Initialize parameters as unborrowed, unmoved
        for param in func.params:
            self.borrow_state[param.name] = BorrowState(name=param.name, var_type=param.ty)

        # Check function body
        self._check_block(func.body)

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
            self.borrow_state[stmt.name] = BorrowState(name=stmt.name, var_type=stmt.ty)
            # Check the initialization expression
            self._check_expr(stmt.value)
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
            # Check condition and all arms
            for cond_expr, arm_block in stmt.arms:
                self._check_expr(cond_expr)
                self._clear_borrows()
                self._check_block(arm_block)
            if stmt.else_block:
                self._check_block(stmt.else_block)

        elif isinstance(stmt, While):
            self._check_expr(stmt.cond)
            self._clear_borrows()
            self._check_block(stmt.body)

        elif isinstance(stmt, Foreach):
            self._check_expr(stmt.iterable)
            self._clear_borrows()
            # Declare loop variable
            self.borrow_state[stmt.item_name] = BorrowState(name=stmt.item_name, var_type=stmt.item_type)
            self._check_block(stmt.body)

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
                    self.err.emit(er.ERR.CE2405, expr.loc, name=expr.id)
                elif state.is_destroyed:
                    self.err.emit(er.ERR.CE2406, expr.loc, name=expr.id)

        elif isinstance(expr, Call):
            # Check all arguments
            for arg in expr.args:
                self._check_expr(arg)

            # Check if this is a struct constructor - if so, mark dynamic array args as moved
            if isinstance(expr.callee, Name) and self.struct_names is not None:
                callee_name = expr.callee.id
                if callee_name in self.struct_names:
                    # This is a struct constructor - mark dynamic array arguments as moved
                    for arg in expr.args:
                        self._mark_moved_if_applicable(arg)

        elif isinstance(expr, MethodCall):
            self._check_expr(expr.receiver)
            for arg in expr.args:
                self._check_expr(arg)

        elif isinstance(expr, DotCall):
            # DotCall is the unified X.Y(args) node used before type checking
            # Check receiver and arguments (same as MethodCall)
            self._check_expr(expr.receiver)
            for arg in expr.args:
                self._check_expr(arg)

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

        elif isinstance(expr, StructConstructor):
            # Check arguments and mark moved for dynamic arrays
            for arg in expr.args:
                self._check_expr(arg)
                # Mark dynamic arrays as moved when passed to struct constructors
                self._mark_moved_if_applicable(arg)

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

        # Literals and other leaf expressions don't need checking

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
                self.err.emit(er.ERR.CE2405, borrow.loc, name=var_name)
                return

            # Check borrow compatibility based on mode
            if is_poke:
                # &poke: exclusive borrow - no other borrows allowed
                if state.poke_borrow_count > 0:
                    self.err.emit(er.ERR.CE2403, borrow.loc, name=var_name)
                    return
                if state.peek_borrow_count > 0:
                    self.err.emit(er.ERR.CE2407, borrow.loc, name=var_name)
                    return
                # Warn when creating &poke of a variable that is itself a &poke reference
                # This is a nested mutable borrow - potentially dangerous but allowed
                if isinstance(state.var_type, ReferenceType) and state.var_type.is_poke():
                    self.err.emit(er.ERR.CW2409, borrow.loc, name=var_name)
                state.poke_borrow_count = 1
            else:
                # &peek: shared borrow - only check for poke conflict
                if state.poke_borrow_count > 0:
                    self.err.emit(er.ERR.CE2407, borrow.loc, name=var_name)
                    return
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
                self.err.emit(er.ERR.CE2405, borrow.loc, name=base_var)
                return

            # Check borrow compatibility based on mode
            if is_poke:
                # &poke: exclusive borrow - no other borrows allowed
                if state.poke_borrow_count > 0:
                    self.err.emit(er.ERR.CE2403, borrow.loc, name=base_var)
                    return
                if state.peek_borrow_count > 0:
                    self.err.emit(er.ERR.CE2407, borrow.loc, name=base_var)
                    return
                state.poke_borrow_count = 1
            else:
                # &peek: shared borrow - only check for poke conflict
                if state.poke_borrow_count > 0:
                    self.err.emit(er.ERR.CE2407, borrow.loc, name=base_var)
                    return
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

    def _mark_moved_if_applicable(self, expr: Expr) -> None:
        """Mark a variable as moved if the expression is a simple variable reference to a dynamic array.

        Move semantics only apply to dynamic arrays in Sushi. Primitive types and other types are copied.
        """
        # In Sushi, rebinding from a variable moves ownership ONLY for dynamic arrays
        # Example: arr1 := arr2  (arr2 is moved to arr1 if arr2 is a dynamic array)
        # Example: x := y  (y is copied to x if y is a primitive like i32)
        if isinstance(expr, Name):
            if expr.id in self.borrow_state:
                state = self.borrow_state[expr.id]
                # Only mark as moved if the variable is a dynamic array
                if state.var_type and isinstance(state.var_type, DynamicArrayType):
                    state.is_moved = True

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
