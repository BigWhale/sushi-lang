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
from typing import Dict, List, Optional, Set

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.error_reporter import PassErrorReporter
from sushi_lang.semantics.ast import (
    Program, FuncDef, ConstDef, ExtendDef, ExtendWithDef, Block, Stmt, Let, Return, While, Foreach, Match,
    If, Expr
)
from sushi_lang.semantics.typesys import Type, BuiltinType
from sushi_lang.semantics.passes.collect import ConstantTable, StructTable, EnumTable, FunctionTable, ExtensionTable, PerkTable, PerkImplementationTable
from sushi_lang.semantics.type_visitor import StatementValidator, ExpressionValidator, TypeInferenceVisitor

# Import validation functions from specialized modules
from .compatibility import types_compatible
from .constants import validate_constant
from .signatures import (
    validate_function,
    validate_extension_method,
    validate_perk_implementation_method,
)
from .control_flow import block_always_returns, statement_always_returns
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


class TypeValidator:
    """
    Pass 2: Type validation and inference.

    This is the main coordinator class that delegates validation logic to specialized modules.
    """

    def __init__(self, reporter: Reporter, const_table: ConstantTable, struct_table: StructTable, enum_table: EnumTable, func_table: FunctionTable, extension_table: Optional[ExtensionTable] = None, generic_enum_table: Optional['GenericEnumTable'] = None, generic_struct_table: Optional['GenericStructTable'] = None, perk_table: Optional[PerkTable] = None, perk_impl_table: Optional[PerkImplementationTable] = None, generic_extension_table: Optional['GenericExtensionTable'] = None, generic_func_table: Optional['GenericFunctionTable'] = None, current_unit_name: Optional[str] = None, monomorphized_functions: Optional[Dict[str, tuple]] = None, external_table: Optional['ExternalTable'] = None) -> None:
        self.reporter = reporter
        self.err = PassErrorReporter(reporter)
        self.const_table = const_table
        self.struct_table = struct_table
        self.enum_table = enum_table
        self.func_table = func_table
        from sushi_lang.semantics.passes.collect import ExternalTable
        self.external_table = external_table or ExternalTable()
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

    def _resolve_external_call(self, node) -> Optional['ExternalSig']:
        """Resolve a DotCall to a foreign function signature, if applicable.

        Returns the ExternalSig when `node` is `<ns>.<name>(args)` where <ns> is a
        registered external namespace AND not a bound local (shadowing guard).
        Annotates the node with `external_ref = (ns, name)` for the backend.
        Returns None otherwise (the call falls through to normal handling).
        """
        from sushi_lang.semantics.ast import Name
        receiver = node.receiver
        if not isinstance(receiver, Name):
            return None
        ns = receiver.id
        # Locals shadow namespaces.
        if ns in self.variable_types:
            return None
        if not self.external_table.is_namespace(ns):
            return None
        sig = self.external_table.lookup(ns, node.method)
        if sig is None:
            return None
        node.external_ref = (ns, node.method)
        return sig

    def _validate_external_call_args(self, node) -> None:
        """Validate argument count and types for a resolved foreign call."""
        from sushi_lang.internals import errors as er
        sig = self.external_table.lookup(node.external_ref[0], node.external_ref[1])
        if sig is None:
            return
        expected = sig.param_types
        fq_name = f"{node.external_ref[0]}.{node.external_ref[1]}"
        if len(node.args) != len(expected):
            er.emit(self.reporter, er.ERR.CE2009, node.loc,
                    name=fq_name, expected=len(expected), got=len(node.args))
            return
        for index, (arg, exp_ty) in enumerate(zip(node.args, expected)):
            got_ty = self.infer_expression_type(arg)
            if got_ty is None or exp_ty is None:
                continue
            if not types_compatible(self, got_ty, exp_ty):
                er.emit(self.reporter, er.ERR.CE2006, arg.loc,
                        index=index, expected=str(exp_ty), got=str(got_ty))

    def _validate_constant(self, const: ConstDef) -> None:
        """Delegate to constants module."""
        validate_constant(self, const)

    def _validate_function(self, func: FuncDef) -> None:
        """Delegate to signatures module."""
        validate_function(self, func)

    def _validate_extension_method(self, ext: ExtendDef) -> None:
        """Delegate to signatures module."""
        validate_extension_method(self, ext)

    def _validate_perk_implementation(self, impl: ExtendWithDef) -> None:
        """Delegate to signatures module."""
        validate_perk_implementation_method(self, impl)

    def _block_always_returns(self, block: Block) -> bool:
        """Delegate to control_flow module."""
        return block_always_returns(self, block)

    def _statement_always_returns(self, stmt: Stmt) -> bool:
        """Delegate to control_flow module."""
        return statement_always_returns(self, stmt)

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
