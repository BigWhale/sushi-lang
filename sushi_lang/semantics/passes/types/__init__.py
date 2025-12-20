# semantics/passes/types/__init__.py
"""
Pass 2: Type validation and inference.

Validates:
- Unknown type names (CE2001)
- Type compatibility in assignments (CE2002)
- Return type matching (CE2003)
- Boolean conditions in control flow (CE2005)
- Extension method calls and resolution

Depends on:
- Pass 0: Function signatures, extension methods, and known types
- Pass 1: Scope information (if needed)

Architecture:
The TypeValidator class coordinates type validation by delegating to specialized modules:
- utils: Shared utilities (type name validation, parameter validation, array destruction tracking)
- inference: Type inference helpers
- compatibility: Type compatibility checking
- expressions: Expression validation
- matching: Pattern matching validation
- calls: Function and method call validation
- statements: Statement validation
"""
from __future__ import annotations
import sys
from typing import Dict, List, Optional, Set

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.error_reporter import PassErrorReporter
from sushi_lang.semantics.ast import (
    Program, FuncDef, ConstDef, ExtendDef, ExtendWithDef, Block, Stmt, Let, Return, While, Foreach, Match,
    If, Expr, Param
)
from sushi_lang.semantics.typesys import Type, BuiltinType, UnknownType, ArrayType, DynamicArrayType, StructType, EnumType
from sushi_lang.semantics.passes.collect import ConstantTable, StructTable, EnumTable, FunctionTable, ExtensionTable, PerkTable, PerkImplementationTable
from sushi_lang.semantics.type_visitor import StatementValidator, ExpressionValidator, TypeInferenceVisitor
from sushi_lang.semantics.type_resolution import resolve_unknown_type

# Import validation functions from specialized modules
from .utils import validate_type_name, validate_and_register_parameters
from .compatibility import validate_assignment_compatibility, types_compatible
from .statements import (
    validate_let_statement,
    validate_return_statement,
    validate_rebind_statement,
    validate_if_statement,
    validate_while_statement,
    validate_foreach_statement
)
from .matching import validate_match_statement
from .expressions import (
    validate_array_literal,
    validate_index_access,
    validate_cast_expression,
    validate_try_expression,
    validate_bitwise_operation,
    validate_boolean_condition
)
from .calls import (
    validate_function_call,
    validate_struct_constructor,
    validate_enum_constructor,
    validate_open_function,
    validate_method_call
)
from .inference import (
    infer_array_literal_type,
    infer_index_access_type,
    infer_dynamic_array_from_type
)
from .perks import (
    validate_perk_implementation,
    check_no_conflicts_with_regular_methods
)


class TypeValidator:
    """
    Pass 2: Type validation and inference.

    This is the main coordinator class that delegates validation logic to specialized modules.
    """

    def __init__(self, reporter: Reporter, const_table: ConstantTable, struct_table: StructTable, enum_table: EnumTable, func_table: FunctionTable, extension_table: Optional[ExtensionTable] = None, generic_enum_table: Optional['GenericEnumTable'] = None, generic_struct_table: Optional['GenericStructTable'] = None, perk_table: Optional[PerkTable] = None, perk_impl_table: Optional[PerkImplementationTable] = None, generic_extension_table: Optional['GenericExtensionTable'] = None, generic_func_table: Optional['GenericFunctionTable'] = None, current_unit_name: Optional[str] = None, monomorphized_functions: Optional[Dict[str, tuple]] = None) -> None:
        self.reporter = reporter
        self.err = PassErrorReporter(reporter)
        self.const_table = const_table
        self.struct_table = struct_table
        self.enum_table = enum_table
        self.func_table = func_table
        self.extension_table = extension_table or ExtensionTable()
        # Store generic enum table for checking generic enum names (e.g., Result)
        from sushi_lang.semantics.passes.collect import GenericEnumTable, GenericStructTable, GenericExtensionTable, GenericFunctionTable
        self.generic_enum_table = generic_enum_table or GenericEnumTable()
        # Store generic struct table for checking generic struct names (e.g., Box, Pair)
        self.generic_struct_table = generic_struct_table or GenericStructTable()
        # Store generic extension table for generic extension methods (e.g., extend Box<T> unwrap() T)
        self.generic_extension_table = generic_extension_table or GenericExtensionTable()
        # Store generic function table for generic functions (e.g., fn identity<T>(T x) T)
        self.generic_func_table = generic_func_table or GenericFunctionTable()
        # Store perk tables for validation
        self.perk_table = perk_table or PerkTable()
        self.perk_impl_table = perk_impl_table or PerkImplementationTable()
        self.current_unit_name = current_unit_name  # Track which unit is being validated (for visibility checking)
        # Store monomorphized functions map (mangled_name -> (generic_name, type_args))
        self.monomorphized_functions = monomorphized_functions or {}
        self.known_types: Set[BuiltinType] = {
            BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
            BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
            BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING,
            BuiltinType.BLANK, BuiltinType.STDIN, BuiltinType.STDOUT, BuiltinType.STDERR,
            BuiltinType.FILE
        }  # Built-in types
        self.current_function: Optional[FuncDef] = None
        # Track variable types within current function
        self.variable_types: Dict[str, Type] = {}
        # Track destroyed arrays per scope
        self.destroyed_arrays: List[set[str]] = []

        # Initialize visitor pattern components
        self.statement_validator = StatementValidator(self)
        self.expression_validator = ExpressionValidator(self)
        self.type_inference_visitor = TypeInferenceVisitor(self)

    def run(self, program: Program) -> None:
        """Entry point for type validation."""
        # Build AST constant map for constant evaluator
        self.ast_constants = {const.name: const for const in program.constants}

        # Validate constants first (they're global and may be referenced in functions)
        for const in program.constants:
            self._validate_constant(const)

        # Validate regular functions
        for func in program.functions:
            # Skip generic functions in Phase 1 (no type validation yet - will be handled in Pass 2 after monomorphization)
            if hasattr(func, 'type_params') and func.type_params:
                continue
            self._validate_function(func)

        # Validate non-generic extension methods
        # Generic extension methods are validated after monomorphization
        for ext in program.extensions:
            self._validate_extension_method(ext)

        # Validate perk implementations
        for impl in program.perk_impls:
            self._validate_perk_implementation(impl)

    def validate_expression(self, expr: Expr) -> Optional[Type]:
        """Validate an expression and its subexpressions using the Visitor Pattern."""
        # Recursively validate subexpressions using visitor
        self.expression_validator.visit(expr)

        # After validation, return the inferred type
        return self.infer_expression_type(expr)

    def infer_expression_type(self, expr: Expr) -> Optional[Type]:
        """Infer the type of an expression using the Visitor Pattern."""
        return self.type_inference_visitor.visit(expr)

    def _validate_constant(self, const: ConstDef) -> None:
        """Validate a constant definition."""
        # Validate the constant's type annotation
        validate_type_name(self, const.ty, const.type_span)

        # Blank type cannot be used for constants
        if const.ty == BuiltinType.BLANK:
            self.err.emit(er.ERR.CE2032, const.type_span)
            return

        # Constants should not use dynamic arrays (they don't make sense for compile-time values)
        if isinstance(const.ty, DynamicArrayType):
            self.err.emit(er.ERR.CE2015, const.type_span, name=const.name)
            return

        # Evaluate constant expression at compile-time
        from sushi_lang.semantics.passes.const_eval import ConstantEvaluator
        evaluator = ConstantEvaluator(self.reporter, self.const_table, self.ast_constants)
        const_value = evaluator.evaluate(const.value, const.ty, const.loc)

        if const_value is None:
            # Error already emitted by evaluator
            return

        # Validate value type matches declared type
        validate_assignment_compatibility(self, const.ty, const.value, const.type_span, const.loc)

    def _validate_function(self, func: FuncDef) -> None:
        """Validate types within a function."""
        self.current_function = func
        self.variable_types = {}  # Reset for each function
        self.destroyed_arrays = [set()]  # Reset for each function with initial scope

        # Validate parameter types and add them to variable table
        validate_and_register_parameters(self, func.params)

        # Validate return type (blank type is allowed here)
        validate_type_name(self, func.ret, func.ret_span)

        # Validate error type if specified (must be an enum)
        if func.err_type is not None:
            # First validate the type name itself
            validate_type_name(self, func.err_type, func.ret_span)  # Use ret_span since we don't have err_span

            # Then check if it's an enum
            from sushi_lang.semantics.generics.types import GenericTypeRef
            resolved_err_type = func.err_type

            # Resolve UnknownType to actual type
            if isinstance(func.err_type, UnknownType):
                resolved_err_type = resolve_unknown_type(
                    func.err_type,
                    self.struct_table.by_name,
                    self.enum_table.by_name
                )

            # Check if resolved type is an enum
            if not isinstance(resolved_err_type, EnumType):
                # Error type must be an enum, not a struct or primitive
                self.err.emit(er.ERR.CE2084, func.ret_span,
                             type_name=str(func.err_type))

        # Validate function body
        self._validate_block(func.body)

        # Check if function returns a value on all code paths
        # Skip this check for functions returning blank (~)
        if func.ret != BuiltinType.BLANK:
            if not self._block_always_returns(func.body):
                self.err.emit(er.ERR.CE0107, func.name_span, name=func.name)

        self.current_function = None

    def _validate_extension_method(self, ext: ExtendDef) -> None:
        """Validate types within an extension method."""
        self.current_function = None  # Extension methods are not functions, but we can reuse some logic
        self.variable_types = {}  # Reset for each extension method
        self.destroyed_arrays = [set()]  # Reset for each extension method with initial scope

        # Validate target type
        validate_type_name(self, ext.target_type, ext.target_type_span)

        # Blank type cannot be used as target type for extension methods
        if ext.target_type == BuiltinType.BLANK:
            self.err.emit(er.ERR.CE2032, ext.target_type_span)

        # Add 'self' parameter with target type to variable table
        if isinstance(ext.target_type, (BuiltinType, ArrayType, DynamicArrayType, StructType)):
            self.variable_types["self"] = ext.target_type
        elif isinstance(ext.target_type, UnknownType):
            # Resolve UnknownType to StructType for struct-typed self
            resolved_type = resolve_unknown_type(ext.target_type, self.struct_table.by_name, self.enum_table.by_name)
            if resolved_type != ext.target_type:
                self.variable_types["self"] = resolved_type

        # Validate explicit parameter types and add them to variable table
        validate_and_register_parameters(self, ext.params)

        # Validate return type (blank type is allowed here)
        validate_type_name(self, ext.ret, ext.ret_span)

        # Validate extension method body
        self._validate_block(ext.body)

        # Check if extension method returns a value on all code paths
        # Skip this check for methods returning blank (~)
        if ext.ret != BuiltinType.BLANK:
            if not self._block_always_returns(ext.body):
                self.err.emit(er.ERR.CE0107, ext.name_span, name=ext.name)

    def _validate_perk_implementation(self, impl: ExtendWithDef) -> None:
        """Validate a perk implementation."""
        # Look up the perk definition
        perk_def = self.perk_table.by_name.get(impl.perk_name)
        if not perk_def:
            # Error should have been caught in collection phase, but double check
            self.err.emit(er.ERR.CE4003, impl.perk_name_span, perk=impl.perk_name)
            return

        # Validate that implementation satisfies perk requirements
        validate_perk_implementation(impl, perk_def, self.reporter)

        # Check for conflicts with regular extension methods
        # This extracts type name from impl.target_type for lookup
        from sushi_lang.semantics.passes.types.perks import _get_type_name_from_impl
        type_name = _get_type_name_from_impl(impl, self.struct_table, self.enum_table)
        if type_name:
            check_no_conflicts_with_regular_methods(type_name, impl, self.extension_table, self.reporter)

        # Validate each method in the implementation
        for method in impl.methods:
            # Treat perk implementation methods like extension methods
            self.current_function = None
            self.variable_types = {}
            self.destroyed_arrays = [set()]

            # Validate target type
            validate_type_name(self, impl.target_type, impl.target_type_span)

            # Add 'self' parameter with target type
            if isinstance(impl.target_type, (BuiltinType, ArrayType, DynamicArrayType, StructType)):
                self.variable_types["self"] = impl.target_type
            elif isinstance(impl.target_type, UnknownType):
                resolved_type = resolve_unknown_type(impl.target_type, self.struct_table.by_name, self.enum_table.by_name)
                if resolved_type != impl.target_type:
                    self.variable_types["self"] = resolved_type

            # Validate method parameters
            validate_and_register_parameters(self, method.params)

            # Validate return type
            validate_type_name(self, method.ret, method.ret_span)

            # Validate method body
            self._validate_block(method.body)

            # Check if method returns on all code paths
            if method.ret != BuiltinType.BLANK:
                if not self._block_always_returns(method.body):
                    self.err.emit(er.ERR.CE0107, method.name_span, name=method.name)

    def _block_always_returns(self, block: Block) -> bool:
        """Check if a block always returns on all code paths."""
        for stmt in block.statements:
            if self._statement_always_returns(stmt):
                return True
        return False

    def _statement_always_returns(self, stmt: Stmt) -> bool:
        """Check if a statement always returns on all code paths."""
        from sushi_lang.semantics.ast import Break, Continue, ExprStmt, Let, Rebind, Print, PrintLn, Foreach, While

        # Return statements always return
        if isinstance(stmt, Return):
            return True

        # If statements return if all branches return
        if isinstance(stmt, If):
            # Check if all arms return
            all_arms_return = all(self._block_always_returns(block) for _, block in stmt.arms)
            # If statement returns only if all arms return AND there's an else block that returns
            if stmt.else_block:
                return all_arms_return and self._block_always_returns(stmt.else_block)
            return False  # No else block means some paths don't return

        # Match statements return if all arms return
        if isinstance(stmt, Match):
            return all(
                self._block_always_returns(arm.body) if isinstance(arm.body, Block) else False
                for arm in stmt.arms
            )

        # Loops never guarantee a return (they might not execute or might break)
        if isinstance(stmt, (While, Foreach)):
            return False

        # Other statements don't return
        if isinstance(stmt, (Let, Rebind, ExprStmt, Print, PrintLn, Break, Continue)):
            return False

        # Unknown statement type - conservatively return False
        return False

    def _validate_block(self, block: Block) -> None:
        """Validate statements in a block."""
        for stmt in block.statements:
            self._validate_statement(stmt)

    def _validate_statement(self, stmt: Stmt) -> None:
        """Validate a statement using the Visitor Pattern."""
        self.statement_validator.visit(stmt)

    # Delegate validation methods to specialized modules
    # These methods are called by the visitor pattern components

    def _validate_let_statement(self, stmt: Let) -> None:
        """Delegate to statements module."""
        validate_let_statement(self, stmt)

    def _validate_return_statement(self, stmt: Return) -> None:
        """Delegate to statements module."""
        validate_return_statement(self, stmt)

    def _validate_rebind_statement(self, stmt) -> None:
        """Delegate to statements module."""
        validate_rebind_statement(self, stmt)

    def _validate_if_statement(self, stmt: If) -> None:
        """Delegate to statements module."""
        validate_if_statement(self, stmt)

    def _validate_while_statement(self, stmt: While) -> None:
        """Delegate to statements module."""
        validate_while_statement(self, stmt)

    def _validate_foreach_statement(self, stmt: Foreach) -> None:
        """Delegate to statements module."""
        validate_foreach_statement(self, stmt)

    def _validate_match_statement(self, stmt: Match) -> None:
        """Delegate to matching module."""
        validate_match_statement(self, stmt)

    def _validate_array_literal(self, expr) -> None:
        """Delegate to expressions module."""
        validate_array_literal(self, expr)

    def _validate_index_access(self, expr) -> None:
        """Delegate to expressions module."""
        validate_index_access(self, expr)

    def _validate_cast_expression(self, expr) -> None:
        """Delegate to expressions module."""
        validate_cast_expression(self, expr)

    def _validate_try_expression(self, expr) -> None:
        """Delegate to expressions module."""
        validate_try_expression(self, expr)

    def _validate_bitwise_operation(self, expr) -> None:
        """Delegate to expressions module."""
        validate_bitwise_operation(self, expr)

    def _validate_boolean_condition(self, expr, context: str) -> None:
        """Delegate to expressions module."""
        validate_boolean_condition(self, expr, context)

    def _validate_function_call(self, call) -> None:
        """Delegate to calls module."""
        validate_function_call(self, call)

    def _validate_struct_constructor(self, call) -> None:
        """Delegate to calls module."""
        validate_struct_constructor(self, call)

    def _validate_enum_constructor(self, constructor) -> None:
        """Delegate to calls module."""
        validate_enum_constructor(self, constructor)

    def _validate_open_function(self, call) -> None:
        """Delegate to calls module."""
        validate_open_function(self, call)

    def _validate_method_call(self, call) -> None:
        """Delegate to calls module."""
        validate_method_call(self, call)

    # Type inference delegation methods
    # These methods are called by the type inference visitor

    def _infer_array_literal_type(self, expr) -> Optional[Type]:
        """Delegate to inference module."""
        return infer_array_literal_type(self, expr)

    def _infer_index_access_type(self, expr) -> Optional[Type]:
        """Delegate to inference module."""
        return infer_index_access_type(self, expr)

    def _infer_dynamic_array_from_type(self, expr, expected_type=None) -> Optional[Type]:
        """Delegate to inference module."""
        return infer_dynamic_array_from_type(self, expr, expected_type)

    # Type compatibility delegation method
    # This is called by backend extension modules for type checking

    def _types_compatible(self, actual: Type, expected: Type) -> bool:
        """Delegate to compatibility module."""
        return types_compatible(self, actual, expected)


# Re-export TypeValidator for backwards compatibility
__all__ = ['TypeValidator']
