from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, AbstractSet, Dict, List, Optional

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.error_reporter import PassErrorReporter
from sushi_lang.semantics.ast import (
    Program, FuncDef, ConstDef, ExtendDef, ExtendWithDef, Block, Stmt, Let, ExprStmt, Return, Print, PrintLn, While, Foreach, Expand, Match, MatchArm, Pattern, OwnPattern, Break,
    If, Expr, Name, IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess, UnaryOp, BinaryOp, Call, MethodCall, DotCall,
    DynamicArrayNew, DynamicArrayFrom, Rebind, Continue, CastExpr, MemberAccess, EnumConstructor, TryExpr, Borrow, RangeExpr, Spread, Lambda, Param
)
from sushi_lang.semantics.passes.collect import ConstantTable, StructTable, EnumTable, GenericEnumTable, GenericStructTable, ExternalTable

if TYPE_CHECKING:
    from sushi_lang.semantics.namespaces import NamespaceTable


@dataclass
class VariableInfo:
    name: str
    declared_at: Optional[Span]
    used: bool = False


class ScopeAnalyzer:
    """The scope pass: scope and variable usage analysis."""

    def __init__(self, reporter: Reporter, constants: Optional[ConstantTable] = None, structs: Optional[StructTable] = None, enums: Optional[EnumTable] = None, generic_enums: Optional[GenericEnumTable] = None, generic_structs: Optional['GenericStructTable'] = None, external_table: Optional['ExternalTable'] = None, kept_constants: Optional[AbstractSet[str]] = None, namespaces: Optional['NamespaceTable'] = None) -> None:
        self.reporter = reporter
        self.err = PassErrorReporter(reporter)
        self.constants = constants or ConstantTable()
        self.structs = structs or StructTable()
        self.enums = enums or EnumTable()
        self.generic_enums = generic_enums or GenericEnumTable()
        from sushi_lang.semantics.passes.collect import GenericStructTable, ExternalTable
        self.generic_structs = generic_structs or GenericStructTable()
        self.external_table = external_table or ExternalTable()
        # What this unit may write behind a dot. One seam for an FFI namespace and a
        # `use ... as` alias alike (`docs/design/unit-namespaces.md` section 3): the
        # local-wins rule below used to be written here a second time.
        from sushi_lang.semantics.namespaces import externals_only
        self.namespaces = (namespaces if namespaces is not None
                           else externals_only(self.external_table))
        # A constant a binary library declares and keeps. It resolves to nothing here,
        # and "no such name" is the wrong word for a declaration the library has: the
        # type pass says whose it is (CE3005) once this pass lets the name through.
        self.kept_constants: AbstractSet[str] = kept_constants or frozenset()
        self.scopes: List[Dict[str, VariableInfo]] = []
        # Loop-nesting depth for the current function. break/continue are only
        # legal when this is > 0 (CE1003); reset to 0 across nested functions.
        self._loop_depth: int = 0
        self.function_names: set[str] = set()
        # One per enclosing lambda, innermost last. A use resolving to a scope BELOW a
        # collector's boundary is captured by that lambda and every enclosing one.
        self._capture_collectors: List[dict] = []
        # True while checking a `poke self` method body (#327): `self := v` is then
        # the store-through write, not the forbidden receiver rebind.
        self._self_is_poke: bool = False

    def run(self, program: Program) -> None:
        """Entry point for scope analysis."""
        # Collect top-level function names so a bare reference resolves to a function
        # value rather than CE1001. (The type pass decides whether the reference is
        # legal — e.g. CE2093 for generic functions.)
        self.function_names = {func.name for func in program.functions}

        for const in program.constants:
            self._check_constant(const)

        for func in program.functions:
            if hasattr(func, 'type_params') and func.type_params:
                continue
            self._check_function(func)

        self.reporter.origin = None

        for ext in program.extensions:
            self._check_extension_method(ext)

        for ext in program.generic_extensions:
            self._check_extension_method(ext)

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
                if var_info.declared_at is None:
                    continue

                self.err.emit(er.ERR.CW1001, var_info.declared_at, name=var_info.name)

    def _declare_variable(self, name: str, span: Optional[Span]) -> None:
        """Declare a variable in the current scope."""
        if not self.scopes:
            return

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

    def _is_namespace(self, name: str) -> bool:
        """True if `name` names a namespace here and is not shadowed by a local."""
        return self.namespaces.is_namespace(name) and not self._is_bound_local(name)

    def _names_a_non_local(self, name: str) -> bool:
        """True if `name` resolves to something that is not a variable at all."""
        if self._is_math_constant(name):
            return True
        # Local-wins: a bound local shadows an enum name, a constant and a function
        # name alike (#296) -- the same rule the EnumConstructor arm applies.
        if self._is_bound_local(name):
            return False
        if name in self.enums.by_name or name in self.generic_enums.by_name:
            return True
        if name in self.kept_constants:
            return True
        # Section 6: a constant of a unit this one did not import is not a name here,
        # so the same walk that would have called it a global says CE1001 instead. The
        # function half stays flat -- an out-of-scope CALL is the typecheck pass's
        # CE2008, which says which unit declares it.
        if self.constants.lookup(name, self.namespaces.scope.unit,
                                 self.namespaces.scope) is not None:
            return True
        return name in self.function_names

    def _use_variable(self, name: str, usage_span: Optional[Span] = None, is_rebind: bool = False) -> None:
        """Mark a variable as used, searching through scope stack."""
        for i in range(len(self.scopes) - 1, -1, -1):
            if name in self.scopes[i]:
                self.scopes[i][name].used = True
                self._record_capture(name, i, usage_span)
                return

        if is_rebind:
            self.err.emit(er.ERR.CE1002, usage_span, name=name)
            return
        diagnostic = er.emit_with(self.reporter, er.ERR.CE1001, usage_span, name=name)
        help_line = self._declared_elsewhere(name)
        if help_line is not None:
            diagnostic = diagnostic.help(help_line)
        diagnostic.emit()

    def _declared_elsewhere(self, name: str) -> Optional[str]:
        """Where a name that reaches nothing here IS declared, or None (section 6).

        Two producers can hold a name a unit cannot write: another unit's constant,
        which an import would bring, and another unit's `unsafe external` block, which
        nothing can bring -- a block binds its namespace where it is written.
        """
        from sushi_lang.semantics.namespaces import import_help
        scope = self.namespaces.scope
        owner = next(iter(scope.declaring_units(name, self.constants.by_unit)), None)
        if owner is not None:
            return import_help(owner)
        elsewhere = self._namespace_bound_elsewhere(name)
        if elsewhere is None:
            return None
        return (f"unit '{elsewhere}' binds the namespace '{name}'; an `unsafe external` "
                f"block binds its namespace in the unit that writes it")

    def _namespace_bound_elsewhere(self, name: str) -> Optional[str]:
        """The unit whose `unsafe external` block binds this namespace, if another does.

        The external table is program-wide and the BINDING is per unit (#503), so a
        name that reaches nothing here can still be a namespace next door -- and saying
        so is the difference between "undeclared" and "declared, elsewhere".
        """
        declared = getattr(self.external_table, "by_namespace", {}).get(name)
        if not declared:
            return None
        return next((sig.unit_name for sig in declared.values()
                     if sig.unit_name is not None), None)

    def _borrow_variable(self, name: str, usage_span: Optional[Span] = None) -> None:
        """A borrow needs a LOCAL. Mark it used, or say which way it is not one."""
        for i in range(len(self.scopes) - 1, -1, -1):
            if name in self.scopes[i]:
                self.scopes[i][name].used = True
                self._record_capture(name, i, usage_span)
                return

        if self._names_a_non_local(name) or self._is_namespace(name):
            self.err.emit(er.ERR.CE2400, usage_span, name=name)
        else:
            self.err.emit(er.ERR.CE1001, usage_span, name=name)

    def _record_capture(self, name: str, resolved_index: int, span: Optional[Span]) -> None:
        """Record `name` as a capture for every enclosing lambda it is free in."""
        for col in self._capture_collectors:
            if resolved_index < col['boundary'] and name not in col['names']:
                col['names'][name] = Param(name=name, ty=None, name_span=span, loc=span)

    def _check_lambda(self, lam: Lambda) -> None:
        """Scope-check a lambda body and record its captured free names."""
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
        self._check_expression(const.value)

    def _check_function(self, func: FuncDef) -> None:
        """Check a function definition."""
        # A transplanted library template's spans belong to the manifest slice, so a
        # diagnostic raised in this body is rendered against it and named for the
        # library (#471). None for every body the consumer wrote.
        self.reporter.origin = getattr(func, "library_origin", None)
        self._push_scope()

        # A plain function has no receiver; a stale flag from a previously checked
        # `poke self` method must not make `self := v` legal here (#327).
        self._self_is_poke = False

        # A (possibly nested) function starts a fresh loop context: a
        # break/continue in its body must not see a loop in an enclosing function.
        saved_loop_depth = self._loop_depth
        self._loop_depth = 0

        for param in func.params:
            # Synthesized pack fan-out params carry user-invisible names, so they are
            # declared with no span and the implicit-variable exemption suppresses CW1001.
            span = None if getattr(param, 'is_pack', False) else param.name_span
            self._declare_variable(param.name, span)

        self._check_block(func.body)
        self._loop_depth = saved_loop_depth
        self._pop_scope()

    def _check_extension_method(self, ext: ExtendDef) -> None:
        """Check an extension method definition."""
        self._push_scope()

        # Add implicit 'self' parameter first - this is the receiver of the method
        # It should not be declared explicitly by the user. A `poke self` receiver
        # (#327) is rebindable (`self := 0` writes the caller's primitive).
        self._self_is_poke = getattr(ext, "self_mode", None) == "poke"
        self._declare_variable("self", None)

        for param in ext.params:
            self._declare_variable(param.name, param.name_span)

        self._check_block(ext.body)
        self._pop_scope()

    def _check_perk_implementation(self, perk_impl: ExtendWithDef) -> None:
        """Check all methods in a perk implementation."""
        for method in perk_impl.methods:
            self._push_scope()

            # Add implicit 'self' parameter - represents the target type instance.
            # `poke self` (#327) makes it rebindable, as in extension methods.
            self._self_is_poke = getattr(method, "self_mode", None) == "poke"
            self._declare_variable("self", None)

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
        handler_name = f"_check_{type(stmt).__name__.lower()}"
        if hasattr(self, handler_name):
            handler = getattr(self, handler_name)
            handler(stmt)
        else:
            # NOT a silent fall-through (#245): a statement with no handler got NO scope
            # analysis, the class CE0125 closed in the borrow checker. The CI gate is
            # tests/unit/test_scope_dispatch_is_total.py; this is the backstop.
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
            # Not allowed for the read-only receiver, but a `poke self` method writes its
            # primitive receiver exactly this way (#327).
            if var_name == "self" and not self._self_is_poke:
                er.emit(self.reporter, er.ERR.CE1002, stmt.loc, name=var_name)
            else:
                self._use_variable(var_name, stmt.loc, is_rebind=True)
        elif isinstance(stmt.target, MemberAccess):
            self._check_expression(stmt.target)
        else:
            self._check_expression(stmt.target)

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
        self._check_expression(stmt.iterable)

        # A `poke` element binding writes through a pointer into the container's storage,
        # so the container must be a LOCAL: a constant lives in `.rodata`, where a store is
        # undefined behaviour rather than a diagnostic. This pass owns "what kind of name is
        # this" (#330), so the rejection is CE2400.
        if stmt.item_borrow == "poke":
            root = stmt.iterable
            from sushi_lang.semantics.ast import DotCall as _DotCall, MethodCall as _MethodCall
            while isinstance(root, (_DotCall, _MethodCall)):
                root = root.receiver
            if isinstance(root, Name) and self._names_a_non_local(root.id):
                self.err.emit(er.ERR.CE2400, stmt.item_borrow_span or stmt.loc,
                              name=root.id)

        self._push_scope()
        self._declare_variable(stmt.item_name, stmt.item_name_span)
        self._loop_depth += 1
        self._check_block(stmt.body)
        self._loop_depth -= 1
        self._pop_scope()

    def _check_expand(self, stmt: Expand) -> None:
        """Check an expand statement (compile-time pack expansion)."""
        self._check_expression(stmt.iterable)
        self._push_scope()
        self._declare_variable(stmt.var, stmt.var_span)
        self._check_block(stmt.body)
        self._pop_scope()

    def _check_match(self, stmt: Match) -> None:
        """Check a match statement."""
        self._check_expression(stmt.scrutinee)

        for arm in stmt.arms:
            self._check_match_arm(arm)

    def _check_match_arm(self, arm: MatchArm) -> None:
        """Check a match arm with pattern bindings (supports nested patterns)."""
        self._push_scope()

        pattern = arm.pattern
        if isinstance(pattern, Pattern):
            self._declare_pattern_bindings(pattern)

        if isinstance(arm.body, Block):
            self._check_block(arm.body)
        elif isinstance(arm.body, Expr):
            self._check_expression(arm.body)

        self._pop_scope()

    def _declare_pattern_bindings(self, pattern: Pattern) -> None:
        """Recursively declare variables from pattern bindings (including Own patterns)."""
        for binding_item in pattern.bindings:
            if isinstance(binding_item, str):
                if binding_item != "_":
                    self._declare_variable(binding_item, pattern.loc)
            elif isinstance(binding_item, Pattern):
                self._declare_pattern_bindings(binding_item)
            elif isinstance(binding_item, OwnPattern):
                inner = binding_item.inner_pattern
                if isinstance(inner, str):
                    if inner != "_":
                        self._declare_variable(inner, binding_item.loc or pattern.loc)
                elif isinstance(inner, Pattern):
                    self._declare_pattern_bindings(inner)
            else:
                # A RefBinding (#300 phase 3) declares its name like a plain binding;
                # it carries its own span.
                self._declare_variable(binding_item.name, binding_item.loc or pattern.loc)

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
                    pass
                else:
                    self._use_variable(expr.id, expr.loc)
            case IntLit() | FloatLit() | BoolLit() | StringLit():
                pass
            case InterpolatedString():
                for part in expr.parts:
                    if not isinstance(part, str):  # part is an Expr
                        self._check_expression(part)
            case ArrayLiteral():
                for element in expr.elements:
                    self._check_expression(element.value)
                    if element.count is not None:
                        self._check_expression(element.count)
            case IndexAccess():
                self._check_expression(expr.array)
                self._check_expression(expr.index)
            case UnaryOp():
                self._check_expression(expr.expr)
            case BinaryOp():
                self._check_expression(expr.left)
                self._check_expression(expr.right)
            case Call():
                if isinstance(expr.callee, Name) and self._is_bound_local(expr.callee.id):
                    self._use_variable(expr.callee.id, expr.callee.loc)
                for arg in expr.args:
                    self._check_expression(arg)
            case MethodCall():
                # An enum type name as receiver (`Result.Ok()`) is a constructor, not a
                # method call, so the name must not be treated as a variable.
                if isinstance(expr.receiver, Name) and (expr.receiver.id in self.enums.by_name or expr.receiver.id in self.generic_enums.by_name):
                    pass
                else:
                    self._check_expression(expr.receiver)

                for arg in expr.args:
                    self._check_expression(arg)
            case DotCall():
                # DotCall is the unified X.Y(args) node
                # Check if receiver is an enum/struct type name - if so, it's a constructor
                # Otherwise, it's a method call
                if isinstance(expr.receiver, Name):
                    receiver_name = expr.receiver.id
                    # Local-wins (#296): a bound local shadows an enum or generic-struct
                    # name, so the receiver is a variable use.
                    if self._is_bound_local(receiver_name):
                        self._check_expression(expr.receiver)
                    elif self._is_namespace(receiver_name):
                        pass
                    elif receiver_name in self.enums.by_name or receiver_name in self.generic_enums.by_name:
                        pass
                    elif receiver_name in self.generic_structs.by_name:
                        pass
                    elif receiver_name in ("f64", "f32") and expr.method == "from_bits":
                        pass
                    else:
                        self._check_expression(expr.receiver)
                else:
                    self._check_expression(expr.receiver)

                for arg in expr.args:
                    self._check_expression(arg)
            case DynamicArrayNew():
                pass
            case DynamicArrayFrom():
                self._check_expression(expr.elements)
            case CastExpr():
                self._check_expression(expr.expr)
            case MemberAccess():
                # `geo.MAX_DEPTH` reads a namespace, not a variable. A local named
                # `geo` wins, which is what `_is_namespace` asks.
                if not (isinstance(expr.receiver, Name)
                        and self._is_namespace(expr.receiver.id)):
                    self._check_expression(expr.receiver)
            case EnumConstructor():
                # The AST builder parses both `Result.Ok(42)` and `x.realise(0)` as an
                # EnumConstructor, so a receiver naming a VARIABLE is really a method call.

                enum_name = expr.enum_name
                is_variable = False
                for scope in reversed(self.scopes):
                    if enum_name in scope:
                        is_variable = True
                        break

                if is_variable:
                    # A method call on a variable. The AST cannot be rewritten mid-walk, so
                    # mark the receiver used and check the arguments.
                    self._use_variable(enum_name, expr.enum_name_span)
                else:
                    pass

                for arg in expr.args:
                    self._check_expression(arg)
            case TryExpr():
                self._check_expression(expr.expr)
            case Borrow():
                # What is borrowed is the ROOT of the place -- `peek cfg.port` borrows out
                # of `cfg` -- so the whole chain resolves through this one arm. That is what
                # gives `peek nope.x` a single diagnostic instead of two.
                base = expr.expr
                while isinstance(base, MemberAccess):
                    base = base.receiver
                if isinstance(base, Name):
                    self._borrow_variable(base.id, base.loc)
                else:
                    # Not a place at all (a call result, a literal). The borrow pass rejects it as
                    # CE2404; here it is just an ordinary expression to walk.
                    self._check_expression(expr.expr)
            case RangeExpr():
                self._check_expression(expr.start)
                self._check_expression(expr.end)
            case Spread():
                self._check_expression(expr.value)
            case Lambda():
                self._check_lambda(expr)
            case BlankLit():
                pass
            case _:
                # NOT a silent fall-through (#245). An expression node with no case got
                # no usage tracking, invisibly. The CI gate is
                # tests/unit/test_scope_dispatch_is_total.py; this is the backstop.
                er.raise_internal_error("CE0130", node=type(expr).__name__)
