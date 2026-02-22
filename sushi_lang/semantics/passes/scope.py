# semantics/passes/scope.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.error_reporter import PassErrorReporter
from sushi_lang.semantics.ast import (
    Program, FuncDef, ConstDef, ExtendDef, ExtendWithDef, Block, Stmt, Let, ExprStmt, Return, Print, PrintLn, While, Foreach, Match, MatchArm, Pattern, OwnPattern, Break,
    If, Expr, Name, IntLit, FloatLit, BoolLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess, UnaryOp, BinaryOp, Call, MethodCall, DotCall,
    DynamicArrayNew, DynamicArrayFrom, Rebind, Continue, CastExpr, MemberAccess, EnumConstructor, TryExpr, Borrow, RangeExpr
)
from sushi_lang.semantics.passes.collect import ConstantTable, StructTable, EnumTable, GenericEnumTable


@dataclass
class VariableInfo:
    name: str
    declared_at: Optional[Span]
    used: bool = False
    borrowed: bool = False  # True if variable is only accessed through borrows


class ScopeAnalyzer:
    """
    Pass 1: Scope and variable usage analysis.

    Tracks:
    - Variable declarations (let statements)
    - Variable usage (in expressions, rebinds)
    - Emits warnings for unused variables
    """

    def __init__(self, reporter: Reporter, constants: Optional[ConstantTable] = None, structs: Optional[StructTable] = None, enums: Optional[EnumTable] = None, generic_enums: Optional[GenericEnumTable] = None, generic_structs: Optional['GenericStructTable'] = None) -> None:
        self.reporter = reporter
        self.err = PassErrorReporter(reporter)
        self.constants = constants or ConstantTable()
        self.structs = structs or StructTable()
        self.enums = enums or EnumTable()
        self.generic_enums = generic_enums or GenericEnumTable()
        from sushi_lang.semantics.passes.collect import GenericStructTable
        self.generic_structs = generic_structs or GenericStructTable()
        # Stack of scopes, each scope maps variable name to VariableInfo
        self.scopes: List[Dict[str, VariableInfo]] = []
        # Track destroyed dynamic arrays per scope
        self.destroyed_arrays: List[set[str]] = []

    def run(self, program: Program) -> None:
        """Entry point for scope analysis."""
        # Check constants (validate expressions in constant definitions)
        for const in program.constants:
            self._check_constant(const)

        # Check regular functions
        for func in program.functions:
            # Skip generic functions in Phase 1 (no scope analysis yet - will be handled in Pass 2 after monomorphization)
            if hasattr(func, 'type_params') and func.type_params:
                continue
            self._check_function(func)

        # Check non-generic extension methods
        for ext in program.extensions:
            self._check_extension_method(ext)

        # Check generic extension methods (scope analysis works the same regardless of generics)
        for ext in program.generic_extensions:
            self._check_extension_method(ext)

        # Check perk implementations (each method needs implicit self)
        for perk_impl in program.perk_impls:
            self._check_perk_implementation(perk_impl)

    def _push_scope(self) -> None:
        """Enter a new scope."""
        self.scopes.append({})
        self.destroyed_arrays.append(set())

    def _pop_scope(self) -> None:
        """Exit current scope and emit warnings for unused variables."""
        if not self.scopes:
            return

        current_scope = self.scopes.pop()
        for var_info in current_scope.values():
            if not var_info.used:
                # Skip warnings for implicit variables (e.g., 'self' in extension/perk methods)
                # These have declared_at=None
                if var_info.declared_at is None:
                    continue

                # Check if variable was borrowed but not directly used
                if var_info.borrowed:
                    # Variable is only used through borrows - emit clarified warning
                    self.err.emit(er.ERR.CW1003, var_info.declared_at, name=var_info.name)
                else:
                    # Variable is completely unused
                    self.err.emit(er.ERR.CW1001, var_info.declared_at, name=var_info.name)

        # Also pop destroyed arrays for this scope
        if self.destroyed_arrays:
            self.destroyed_arrays.pop()

    def _declare_variable(self, name: str, span: Optional[Span]) -> None:
        """Declare a variable in the current scope."""
        if not self.scopes:
            return

        # Check for shadowing - look in outer scopes (not including current scope)
        for outer_scope in self.scopes[:-1]:
            if name in outer_scope:
                outer_var = outer_scope[name]
                self.err.emit_with(er.ERR.CW1002, span, name=name) \
                    .note("first declared here", outer_var.declared_at).emit()
                break

        current_scope = self.scopes[-1]
        current_scope[name] = VariableInfo(name=name, declared_at=span)

    def _is_math_constant(self, name: str) -> bool:
        """Check if name is a built-in math module constant."""
        from sushi_lang.sushi_stdlib.src import math as math_module
        return math_module.is_builtin_math_constant(name)

    def _use_variable(self, name: str, usage_span: Optional[Span] = None, is_rebind: bool = False) -> None:
        """Mark a variable as used, searching through scope stack."""
        for scope in reversed(self.scopes):
            if name in scope:
                scope[name].used = True
                return

        # Variable not found in any scope - emit appropriate error
        if is_rebind:
            self.err.emit(er.ERR.CE1002, usage_span, name=name)
        else:
            self.err.emit(er.ERR.CE1001, usage_span, name=name)

    def _borrow_variable(self, name: str, usage_span: Optional[Span] = None) -> None:
        """Mark a variable as borrowed (used through a reference), searching through scope stack."""
        for scope in reversed(self.scopes):
            if name in scope:
                # Mark as borrowed but don't mark as directly used
                # This allows us to detect variables that are ONLY borrowed
                scope[name].borrowed = True
                return

        # Variable not found in any scope - emit error
        self.err.emit(er.ERR.CE1001, usage_span, name=name)

    def _mark_array_destroyed(self, name: str) -> None:
        """Mark a dynamic array as destroyed in the current scope."""
        if self.destroyed_arrays:
            self.destroyed_arrays[-1].add(name)

    def _is_array_destroyed(self, name: str) -> bool:
        """Check if a dynamic array has been destroyed in any current scope."""
        for destroyed_set in self.destroyed_arrays:
            if name in destroyed_set:
                return True
        return False

    def _check_constant(self, const: ConstDef) -> None:
        """Check a constant definition - validate the value expression."""
        # Constants are global and don't create their own scope
        # We just need to validate the value expression for any variable references
        self._check_expression(const.value)

    def _check_function(self, func: FuncDef) -> None:
        """Check a function definition."""
        self._push_scope()

        # Function parameters are implicitly declared and should be considered used
        # if they appear in the function signature (to avoid warnings for unused params)
        for param in func.params:
            self._declare_variable(param.name, param.name_span)
            # Don't mark params as used automatically - let actual usage determine it

        self._check_block(func.body)
        self._pop_scope()

    def _check_extension_method(self, ext: ExtendDef) -> None:
        """Check an extension method definition."""
        self._push_scope()

        # Add implicit 'self' parameter first - this is the receiver of the method
        # It should not be declared explicitly by the user
        self._declare_variable("self", None)

        # Add explicit parameters from the extension method signature
        for param in ext.params:
            self._declare_variable(param.name, param.name_span)

        self._check_block(ext.body)
        self._pop_scope()

    def _check_perk_implementation(self, perk_impl: ExtendWithDef) -> None:
        """Check all methods in a perk implementation.

        Each method in a perk implementation gets an implicit 'self' parameter,
        just like extension methods.
        """
        for method in perk_impl.methods:
            self._push_scope()

            # Add implicit 'self' parameter - represents the target type instance
            self._declare_variable("self", None)

            # Add explicit parameters from the method signature
            for param in method.params:
                self._declare_variable(param.name, param.name_span)

            self._check_block(method.body)
            self._pop_scope()

    def _check_block(self, block: Block) -> None:
        """Check a block of statements."""
        for stmt in block.statements:
            self._check_statement(stmt)

    def _check_statement(self, stmt: Stmt) -> None:
        """Check a statement."""
        # Dispatch to specific handler based on statement type
        handler_name = f"_check_{type(stmt).__name__.lower()}"
        if hasattr(self, handler_name):
            handler = getattr(self, handler_name)
            handler(stmt)
        else:
            self._check_unknown_statement(stmt)

    def _check_let(self, stmt: Let) -> None:
        """Check a let statement."""
        self._declare_variable(stmt.name, stmt.loc)
        self._check_expression(stmt.value)

    def _check_rebind(self, stmt: Rebind) -> None:
        """Check a rebind statement."""
        from sushi_lang.semantics.ast import Name, MemberAccess

        # Extract the variable name from the target
        # For simple rebind (x := value), target is a Name
        # For field rebind (obj.field := value), target is a MemberAccess
        if isinstance(stmt.target, Name):
            var_name = stmt.target.id
            # Check if trying to rebind 'self' - this is not allowed in extension methods
            if var_name == "self":
                er.emit(self.reporter, er.ERR.CE1002, stmt.loc, name=var_name)
            else:
                self._use_variable(var_name, stmt.loc, is_rebind=True)
        elif isinstance(stmt.target, MemberAccess):
            # For field rebinding, we need to check the receiver expression
            # The receiver must be a valid variable/expression
            self._check_expression(stmt.target)
        else:
            # Target must be Name or MemberAccess - validate it as an expression
            self._check_expression(stmt.target)

        # Check the value expression
        self._check_expression(stmt.value)

    def _check_return(self, stmt: Return) -> None:
        """Check a return statement."""
        self._check_expression(stmt.value)

    def _check_print(self, stmt: Print) -> None:
        """Check a print statement."""
        self._check_expression(stmt.value)

    def _check_println(self, stmt: PrintLn) -> None:
        """Check a print statement."""
        self._check_expression(stmt.value)

    def _check_exprstmt(self, stmt: ExprStmt) -> None:
        """Check an expression statement."""
        self._check_expression(stmt.expr)

    def _check_if(self, stmt: If) -> None:
        """Check an if statement."""
        # Check all condition/block pairs in arms
        for condition, block in stmt.arms:
            self._check_expression(condition)
            self._check_scoped_block(block)

        if stmt.else_block:
            self._check_scoped_block(stmt.else_block)

    def _check_while(self, stmt: While) -> None:
        """Check a while statement."""
        self._check_expression(stmt.cond)
        self._check_scoped_block(stmt.body)

    def _check_foreach(self, stmt: Foreach) -> None:
        """Check a foreach statement."""
        # Check the iterable expression first (in outer scope)
        self._check_expression(stmt.iterable)

        # The foreach body gets its own scope with the item variable
        self._push_scope()
        # Declare the loop variable in the inner scope
        self._declare_variable(stmt.item_name, stmt.item_name_span)
        self._check_block(stmt.body)
        self._pop_scope()

    def _check_match(self, stmt: Match) -> None:
        """Check a match statement."""
        # Check the scrutinee expression (in current scope)
        self._check_expression(stmt.scrutinee)

        # Check each match arm
        for arm in stmt.arms:
            self._check_match_arm(arm)

    def _check_match_arm(self, arm: MatchArm) -> None:
        """Check a match arm with pattern bindings (supports nested patterns)."""
        # Each match arm body gets its own scope with pattern bindings
        self._push_scope()

        # Declare pattern bindings as variables in the arm's scope (recursive for nested patterns)
        pattern = arm.pattern
        if isinstance(pattern, Pattern):
            self._declare_pattern_bindings(pattern)

        # Check the arm body (either expression or block)
        if isinstance(arm.body, Block):
            self._check_block(arm.body)
        elif isinstance(arm.body, Expr):
            self._check_expression(arm.body)

        self._pop_scope()

    def _declare_pattern_bindings(self, pattern: Pattern) -> None:
        """Recursively declare variables from pattern bindings (including Own patterns)."""
        for binding_item in pattern.bindings:
            if isinstance(binding_item, str):
                # Simple binding: variable name or wildcard
                if binding_item != "_":
                    # Declare each binding as a variable
                    # We use the pattern's location since we don't have individual spans for bindings
                    self._declare_variable(binding_item, pattern.loc)
            elif isinstance(binding_item, Pattern):
                # Nested pattern: recursively declare its bindings
                self._declare_pattern_bindings(binding_item)
            elif isinstance(binding_item, OwnPattern):
                # Own pattern: unwrap and declare the inner pattern
                inner = binding_item.inner_pattern
                if isinstance(inner, str):
                    # Simple variable binding
                    if inner != "_":
                        self._declare_variable(inner, binding_item.loc or pattern.loc)
                elif isinstance(inner, Pattern):
                    # Nested pattern inside Own(...)
                    self._declare_pattern_bindings(inner)

    def _check_break(self, stmt: Break) -> None:
        """Check a break statement."""
        pass  # No variables to check

    def _check_continue(self, stmt: Continue) -> None:
        """Check a continue statement."""
        pass  # No variables to check

    def _check_funcdef(self, stmt: FuncDef) -> None:
        """Check a nested function definition."""
        self._check_function(stmt)

    def _check_unknown_statement(self, stmt: Stmt) -> None:
        """Handle unknown statement types."""
        # Could log a warning or raise an error
        pass

    def _check_scoped_block(self, block: Block) -> None:
        """Check a block with its own scope."""
        self._push_scope()
        self._check_block(block)
        self._pop_scope()

    def _check_expression(self, expr: Expr) -> None:
        """Check an expression for variable usage."""
        match expr:
            case Name():
                # Check for special built-in identifiers (stdin, stdout, stderr)
                if expr.id in ['stdin', 'stdout', 'stderr']:
                    # Built-in I/O identifiers don't need to be tracked
                    pass
                # Check for built-in global functions (open)
                elif expr.id in ['open']:
                    # Built-in global functions don't need to be tracked as variables
                    pass
                # Check if it's a constant
                elif expr.id in self.constants.by_name:
                    # Constants don't need to be tracked as variables
                    pass
                # Check if it's a math module constant (PI, E, TAU)
                elif self._is_math_constant(expr.id):
                    # Math constants don't need to be tracked as variables
                    pass
                # Check if it's an enum type name (concrete or generic)
                elif expr.id in self.enums.by_name or expr.id in self.generic_enums.by_name:
                    # Enum type names don't need to be tracked as variables
                    pass
                else:
                    # It's a variable, track its usage
                    self._use_variable(expr.id, expr.loc)
            case IntLit() | FloatLit() | BoolLit() | StringLit():
                # Literals don't use variables
                pass
            case InterpolatedString():
                # Check expressions in interpolated string
                for part in expr.parts:
                    if not isinstance(part, str):  # part is an Expr
                        self._check_expression(part)
            case ArrayLiteral():
                # Check each element expression
                for element in expr.elements:
                    self._check_expression(element)
            case IndexAccess():
                # Check both array and index expressions
                self._check_expression(expr.array)
                self._check_expression(expr.index)
            case UnaryOp():
                self._check_expression(expr.expr)
            case BinaryOp():
                self._check_expression(expr.left)
                self._check_expression(expr.right)
            case Call():
                # Function calls don't mark function names as variable usage
                for arg in expr.args:
                    self._check_expression(arg)
            case MethodCall():
                # Check receiver and arguments
                # Special case: if receiver is an enum type name (e.g., Result.Ok()),
                # this is actually an enum constructor, not a method call
                # We need to handle this specially to avoid treating the enum name as a variable
                if isinstance(expr.receiver, Name) and (expr.receiver.id in self.enums.by_name or expr.receiver.id in self.generic_enums.by_name):
                    # This is an enum constructor (concrete or generic enum)
                    # Don't check receiver as variable, just check arguments
                    pass
                else:
                    # Normal method call - check receiver
                    self._check_expression(expr.receiver)

                for arg in expr.args:
                    self._check_expression(arg)

                # Track destroy() calls on dynamic arrays
                if expr.method == "destroy" and isinstance(expr.receiver, Name):
                    self._mark_array_destroyed(expr.receiver.id)
            case DotCall():
                # DotCall is the unified X.Y(args) node
                # Check if receiver is an enum/struct type name - if so, it's a constructor
                # Otherwise, it's a method call
                if isinstance(expr.receiver, Name):
                    receiver_name = expr.receiver.id
                    # Check if it's an enum type (concrete or generic)
                    if receiver_name in self.enums.by_name or receiver_name in self.generic_enums.by_name:
                        # Enum constructor (concrete or generic) - don't check receiver as variable
                        pass
                    # Check if it's a generic struct type (e.g., Own)
                    elif receiver_name in self.generic_structs.by_name:
                        # Struct constructor (e.g., Own.alloc) - don't check receiver as variable
                        pass
                    else:
                        # Method call - check receiver as variable
                        self._check_expression(expr.receiver)
                        # Track destroy() calls
                        if expr.method == "destroy":
                            self._mark_array_destroyed(receiver_name)
                else:
                    # Complex receiver expression - check it
                    self._check_expression(expr.receiver)

                # Always check arguments
                for arg in expr.args:
                    self._check_expression(arg)
            case DynamicArrayNew():
                # new() constructor doesn't use variables
                pass
            case DynamicArrayFrom():
                # from(array_literal) - check the array literal
                self._check_expression(expr.elements)
            case CastExpr():
                # Cast expression - check the source expression for variable usage
                self._check_expression(expr.expr)
            case MemberAccess():
                # Struct member access - check the base expression (receiver.field)
                self._check_expression(expr.receiver)
            case EnumConstructor():
                # Enum variant constructor (including Result.Ok(), Result.Err()) - check all arguments
                # BUT: check if this is actually a method call on a variable (not an enum type)
                # This happens when user writes: let Result<i32> x = Result.Ok(42); x.realise(0)
                # The AST builder parses both as EnumConstructor, but x.realise should be MethodCall

                # Check if the enum_name is actually a variable, not an enum type
                enum_name = expr.enum_name
                is_variable = False
                for scope in reversed(self.scopes):
                    if enum_name in scope:
                        is_variable = True
                        break

                if is_variable:
                    # This is actually a method call on a variable, not an enum constructor
                    # We need to convert this EnumConstructor to MethodCall
                    # But we can't modify the AST directly here without breaking iteration
                    # For now, just check the receiver variable as used and check arguments
                    self._use_variable(enum_name, expr.enum_name_span)
                else:
                    # Normal enum constructor - don't check enum name as variable
                    pass

                # Check all arguments regardless
                for arg in expr.args:
                    self._check_expression(arg)
            case TryExpr():
                # Try operator: expr??
                # Check the inner expression for variable usage
                self._check_expression(expr.expr)
            case Borrow():
                # Borrow expression: &expr
                # Special handling: if borrowing a simple variable (Name node),
                # mark it as borrowed rather than used
                if isinstance(expr.expr, Name):
                    self._borrow_variable(expr.expr.id, expr.expr.loc)
                else:
                    # For complex expressions (like &cfg.port), just check normally
                    self._check_expression(expr.expr)
            case RangeExpr():
                # Range expression: start..end or start..=end
                # Check both start and end expressions for variable usage
                self._check_expression(expr.start)
                self._check_expression(expr.end)
