# semantics/passes/scope.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.error_reporter import PassErrorReporter
from sushi_lang.semantics.ast import (
    Program, FuncDef, ConstDef, ExtendDef, ExtendWithDef, Block, Stmt, Let, ExprStmt, Return, Print, PrintLn, While, Foreach, Expand, Match, MatchArm, Pattern, OwnPattern, Break,
    If, Expr, Name, IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess, UnaryOp, BinaryOp, Call, MethodCall, DotCall,
    DynamicArrayNew, DynamicArrayFrom, Rebind, Continue, CastExpr, MemberAccess, EnumConstructor, TryExpr, Borrow, RangeExpr, Spread, Lambda, Param
)
from sushi_lang.semantics.passes.collect import ConstantTable, StructTable, EnumTable, GenericEnumTable, GenericStructTable, ExternalTable


@dataclass
class VariableInfo:
    name: str
    declared_at: Optional[Span]
    used: bool = False


class ScopeAnalyzer:
    """
    Pass 1: Scope and variable usage analysis.

    Tracks:
    - Variable declarations (let statements)
    - Variable usage (in expressions, rebinds)
    - Emits warnings for unused variables
    """

    def __init__(self, reporter: Reporter, constants: Optional[ConstantTable] = None, structs: Optional[StructTable] = None, enums: Optional[EnumTable] = None, generic_enums: Optional[GenericEnumTable] = None, generic_structs: Optional['GenericStructTable'] = None, external_table: Optional['ExternalTable'] = None) -> None:
        self.reporter = reporter
        self.err = PassErrorReporter(reporter)
        self.constants = constants or ConstantTable()
        self.structs = structs or StructTable()
        self.enums = enums or EnumTable()
        self.generic_enums = generic_enums or GenericEnumTable()
        from sushi_lang.semantics.passes.collect import GenericStructTable, ExternalTable
        self.generic_structs = generic_structs or GenericStructTable()
        self.external_table = external_table or ExternalTable()
        # Stack of scopes, each scope maps variable name to VariableInfo
        self.scopes: List[Dict[str, VariableInfo]] = []
        # Loop-nesting depth for the current function. break/continue are only
        # legal when this is > 0 (CE1003); reset to 0 across nested functions.
        self._loop_depth: int = 0
        # Names of top-level functions. A bare reference to one (not shadowed by a
        # local) is a first-class function value, not an undeclared identifier.
        self.function_names: set[str] = set()
        # Active lambda capture collectors (one per enclosing lambda, innermost
        # last). Each is {'boundary': int, 'names': dict[str, Param]}: a variable
        # use resolving to a scope BELOW a collector's boundary is captured by that
        # lambda (and every enclosing lambda whose boundary is also above it).
        self._capture_collectors: List[dict] = []

    def run(self, program: Program) -> None:
        """Entry point for scope analysis."""
        # Collect top-level function names so a bare reference resolves to a function
        # value rather than CE1001. (The type pass decides whether the reference is
        # legal — e.g. CE2093 for generic functions.)
        self.function_names = {func.name for func in program.functions}

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

                # Variable is completely unused (a borrow counts as a use).
                self.err.emit(er.ERR.CW1001, var_info.declared_at, name=var_info.name)

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

    def _is_bound_local(self, name: str) -> bool:
        """True if `name` is currently a variable in any active scope."""
        return any(name in scope for scope in self.scopes)

    def _is_external_namespace(self, name: str) -> bool:
        """True if `name` is a registered FFI namespace and not shadowed by a local."""
        return self.external_table.is_namespace(name) and not self._is_bound_local(name)

    def _names_a_non_local(self, name: str) -> bool:
        """True if `name` resolves to something that is not a variable at all.

        A constant, a math constant, an enum type name, a top-level function referenced
        as a value, and the built-in I/O identifiers. None of them has a frame slot, so
        none is tracked as a variable -- and none is undeclared either. An FFI namespace
        is deliberately NOT here: it is only ever the receiver of a `DotCall`, which its
        own arm handles, so a bare mention of one stays the CE1001 it has always been.

        This is the ONE place that answers "what kind of name is this", because this pass
        is the one that owns names. `_borrow_variable` used to be a copy of `_use_variable`
        that had lost every case here, which is why `&peek SOME_CONST` reported CE1001
        about a constant declared two lines above.

        A local of the same name SHADOWS a constant, a function name or an FFI namespace,
        and then it is a plain variable read. Without that guard the local was never marked
        used (a bogus CW1001) and the backend read the CONSTANT, so a shadowing local of a
        different length was `CE0017: cannot convert '[3 x i32]' to '[4 x i32]'` (found
        while fixing #248).
        """
        if name in ('stdin', 'stdout', 'stderr', 'open'):
            return True
        if self._is_math_constant(name):
            return True
        if name in self.enums.by_name or name in self.generic_enums.by_name:
            return True
        if self._is_bound_local(name):
            return False
        return name in self.constants.by_name or name in self.function_names

    def _use_variable(self, name: str, usage_span: Optional[Span] = None, is_rebind: bool = False) -> None:
        """Mark a variable as used, searching through scope stack."""
        for i in range(len(self.scopes) - 1, -1, -1):
            if name in self.scopes[i]:
                self.scopes[i][name].used = True
                self._record_capture(name, i, usage_span)
                return

        # Variable not found in any scope - emit appropriate error
        if is_rebind:
            self.err.emit(er.ERR.CE1002, usage_span, name=name)
        else:
            self.err.emit(er.ERR.CE1001, usage_span, name=name)

    def _borrow_variable(self, name: str, usage_span: Optional[Span] = None) -> None:
        """A borrow needs a LOCAL. Mark it used, or say which way it is not one.

        A BORROW IS A USE. This used to set a separate `borrowed` flag and deliberately
        leave `used` false, so a variable only ever passed by `&peek`/`&poke` was reported
        as CW1003 ("only used through borrows ... may indicate unnecessary indirection").

        That advice became wrong when a `match`/`foreach` binding of an owning type stopped
        being consumable (CE2411): borrowing is now the REQUIRED form for a recursive
        traversal, not an avoidable indirection, and a pattern binding has no declaration
        to turn into a reference anyway. No mainstream language warns here -- Rust, Go and
        C# have no equivalent, and Clippy's `needless_borrow` warns the opposite way.
        A variable that is genuinely never touched is still CW1001.

        The failure half is what this function is FOR, and it was missing. A borrow takes
        the address of storage a frame owns, so a name that resolves to something else --
        a constant, a top-level function, an enum type, an FFI namespace -- cannot be
        borrowed even though it exists. That is CE2400, and this is the only pass that can
        tell it from a name that is declared nowhere (CE1001). The borrow checker used to
        ask the same question from `borrow_state`, which cannot distinguish the two, so it
        answered CE2400 for BOTH -- and the scope pass answered CE1001 for both, giving one
        token two diagnostics, one of them wrong (F15 of BORROW.md).
        """
        for i in range(len(self.scopes) - 1, -1, -1):
            if name in self.scopes[i]:
                self.scopes[i][name].used = True
                self._record_capture(name, i, usage_span)
                return

        if self._names_a_non_local(name) or self._is_external_namespace(name):
            self.err.emit(er.ERR.CE2400, usage_span, name=name)
        else:
            self.err.emit(er.ERR.CE1001, usage_span, name=name)

    def _record_capture(self, name: str, resolved_index: int, span: Optional[Span]) -> None:
        """Record `name` as a capture for every enclosing lambda it is free in.

        `resolved_index` is the absolute scope-stack index where `name` was found.
        A lambda captures it iff the variable lives BELOW that lambda's scope
        boundary (i.e. it is an enclosing local, not a lambda param or lambda-local).
        Deep captures propagate through every enclosing lambda so an outer closure
        also carries the value an inner closure needs.
        """
        for col in self._capture_collectors:
            if resolved_index < col['boundary'] and name not in col['names']:
                col['names'][name] = Param(name=name, ty=None, name_span=span, loc=span)

    def _check_lambda(self, lam: Lambda) -> None:
        """Scope-check a lambda body and record its captured free names.

        The lambda's params open a fresh scope; free names in the body that resolve
        to enclosing locals are recorded into `lam.captures` (types are None here and
        filled by the type pass, T1.3).
        """
        boundary = len(self.scopes)
        collector = {'boundary': boundary, 'names': {}}
        self._capture_collectors.append(collector)
        self._push_scope()
        for p in lam.params:
            self._declare_variable(p.name, p.name_span)
        if lam.is_block_body:
            self._check_block(lam.body)
        else:
            self._check_expression(lam.body)
        self._pop_scope()
        self._capture_collectors.pop()
        lam.captures = list(collector['names'].values())

    def _check_constant(self, const: ConstDef) -> None:
        """Check a constant definition - validate the value expression."""
        # Constants are global and don't create their own scope
        # We just need to validate the value expression for any variable references
        self._check_expression(const.value)

    def _check_function(self, func: FuncDef) -> None:
        """Check a function definition."""
        self._push_scope()

        # A (possibly nested) function starts a fresh loop context: a
        # break/continue in its body must not see a loop in an enclosing function.
        saved_loop_depth = self._loop_depth
        self._loop_depth = 0

        # Function parameters are implicitly declared and should be considered used
        # if they appear in the function signature (to avoid warnings for unused params)
        for param in func.params:
            # Synthesized pack fan-out params (args_0, args_1, ... produced when a
            # ...Ts pack is monomorphized) carry user-invisible names and cannot be
            # consumed until expand(...) lands (T7b). Declare them with no span so the
            # implicit-variable exemption suppresses a spurious CW1001 unused warning.
            span = None if getattr(param, 'is_pack', False) else param.name_span
            self._declare_variable(param.name, span)
            # Don't mark params as used automatically - let actual usage determine it

        self._check_block(func.body)
        self._loop_depth = saved_loop_depth
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
            # NOT a silent fall-through (#245). A statement with no handler got no scope
            # analysis at all, invisibly -- the same class CE0125 closed in the borrow
            # checker; `expand(...)` bodies were skipped this way. The CI gate is
            # tests/unit/test_scope_dispatch_is_total.py; this is the runtime backstop.
            er.raise_internal_error("CE0130", node=type(stmt).__name__)

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
        self._loop_depth += 1
        self._check_scoped_block(stmt.body)
        self._loop_depth -= 1

    def _check_foreach(self, stmt: Foreach) -> None:
        """Check a foreach statement."""
        # Check the iterable expression first (in outer scope)
        self._check_expression(stmt.iterable)

        # A `&poke` element binding (#300 phase 1) writes through a pointer into the
        # container's storage, so the container must be a LOCAL -- a constant is emitted
        # into `.rodata` and a store through it is undefined behaviour, not a diagnostic
        # (the CE2096 rationale). This pass owns the "what kind of name is this"
        # question (#330), so the rejection is CE2400, the borrow-of-a-non-local code.
        if stmt.item_borrow == "poke":
            root = stmt.iterable
            from sushi_lang.semantics.ast import DotCall as _DotCall, MethodCall as _MethodCall
            while isinstance(root, (_DotCall, _MethodCall)):
                root = root.receiver
            if isinstance(root, Name) and self._names_a_non_local(root.id):
                self.err.emit(er.ERR.CE2400, stmt.item_borrow_span or stmt.loc,
                              name=root.id)

        # The foreach body gets its own scope with the item variable
        self._push_scope()
        # Declare the loop variable in the inner scope
        self._declare_variable(stmt.item_name, stmt.item_name_span)
        self._loop_depth += 1
        self._check_block(stmt.body)
        self._loop_depth -= 1
        self._pop_scope()

    def _check_expand(self, stmt: Expand) -> None:
        """Check an expand statement (compile-time pack expansion).

        The compile-time analog of `_check_foreach`, found unhandled by the #245 totality
        gate: `expand` bodies got no scope analysis at all before it. The binding variable
        lives in its own scope, like a foreach item. `_loop_depth` is NOT bumped: the body
        is unrolled statements, not a runtime loop, so a bare `break` inside one is still
        CE1003.
        """
        self._check_expression(stmt.iterable)
        self._push_scope()
        self._declare_variable(stmt.var, stmt.var_span)
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
        """Check a break statement (only legal inside a loop)."""
        if self._loop_depth == 0:
            er.emit(self.reporter, er.ERR.CE1003, stmt.loc)

    def _check_continue(self, stmt: Continue) -> None:
        """Check a continue statement (only legal inside a loop)."""
        if self._loop_depth == 0:
            er.emit(self.reporter, er.ERR.CE1003, stmt.loc)

    def _check_funcdef(self, stmt: FuncDef) -> None:
        """Check a nested function definition."""
        self._check_function(stmt)

    def _check_scoped_block(self, block: Block) -> None:
        """Check a block with its own scope."""
        self._push_scope()
        self._check_block(block)
        self._pop_scope()

    def _check_expression(self, expr: Expr) -> None:
        """Check an expression for variable usage."""
        match expr:
            case Name():
                if self._names_a_non_local(expr.id):
                    # Not a variable: nothing to track, and nothing to report.
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
                # A callee that is a bound local is an indirect call through a function
                # value -> mark it used. A bare top-level function name is not a variable.
                if isinstance(expr.callee, Name) and self._is_bound_local(expr.callee.id):
                    self._use_variable(expr.callee.id, expr.callee.loc)
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
            case DotCall():
                # DotCall is the unified X.Y(args) node
                # Check if receiver is an enum/struct type name - if so, it's a constructor
                # Otherwise, it's a method call
                if isinstance(expr.receiver, Name):
                    receiver_name = expr.receiver.id
                    # FFI: foreign namespace call (e.g., libc.strlen) - locals shadow
                    # namespaces, so only treat as a namespace if not a bound local.
                    if self._is_external_namespace(receiver_name):
                        # Don't check the namespace name as a variable.
                        pass
                    # Check if it's an enum type (concrete or generic)
                    elif receiver_name in self.enums.by_name or receiver_name in self.generic_enums.by_name:
                        # Enum constructor (concrete or generic) - don't check receiver as variable
                        pass
                    # Check if it's a generic struct type (e.g., Own)
                    elif receiver_name in self.generic_structs.by_name:
                        # Struct constructor (e.g., Own.alloc) - don't check receiver as variable
                        pass
                    # f64.from_bits(...) / f32.from_bits(...): a primitive type name used
                    # as a static-method namespace, not a variable.
                    elif receiver_name in ("f64", "f32") and expr.method == "from_bits":
                        pass
                    else:
                        # Method call - check receiver as variable
                        self._check_expression(expr.receiver)
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
                # Borrow expression: &expr. What is borrowed is the ROOT of the place --
                # `&peek cfg.port` borrows out of `cfg` -- so the whole chain resolves
                # through the one borrow-specific arm. Walking to the base here is what
                # gives `&peek nope.x` the same single diagnostic as `&peek nope`; it used
                # to fall through to the ordinary member-access walk and be reported twice.
                base = expr.expr
                while isinstance(base, MemberAccess):
                    base = base.receiver
                if isinstance(base, Name):
                    self._borrow_variable(base.id, base.loc)
                else:
                    # Not a place at all (a call result, a literal). Pass 3 rejects it as
                    # CE2404; here it is just an ordinary expression to walk.
                    self._check_expression(expr.expr)
            case RangeExpr():
                # Range expression: start..end or start..=end
                # Check both start and end expressions for variable usage
                self._check_expression(expr.start)
                self._check_expression(expr.end)
            case Spread():
                # Bloom argument: arr... uses (and moves) its source array.
                self._check_expression(expr.value)
            case Lambda():
                self._check_lambda(expr)
            case BlankLit():
                # The blank literal `~` is a leaf: it owns nothing and names nothing.
                pass
            case _:
                # NOT a silent fall-through (#245). An expression node with no case got
                # no usage tracking, invisibly. The CI gate is
                # tests/unit/test_scope_dispatch_is_total.py; this is the backstop.
                er.raise_internal_error("CE0130", node=type(expr).__name__)
