"""The typecheck pass: type validation and inference."""
from __future__ import annotations
from typing import Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.passes.collect.externals import ExternalSig

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.error_reporter import PassErrorReporter
from sushi_lang.semantics.ast import (
    Program, FuncDef, ConstDef, ExtendDef, ExtendWithDef, Block, Stmt, Let, Return, While, Foreach, Match,
    If, Expr
)
from sushi_lang.semantics.typesys import Type, BuiltinType
from sushi_lang.semantics.passes.types.visitor import StatementValidator, ExpressionValidator, TypeInferenceVisitor

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
from sushi_lang.semantics.generics.type_display import display_type


class TypeValidator:
    """The typecheck pass: type validation and inference."""

    def __init__(self, reporter: Reporter, tables: 'SymbolTables', current_unit_name: Optional[str] = None, monomorphized_functions: Optional[Dict[str, tuple]] = None) -> None:
        self.reporter = reporter
        self.err = PassErrorReporter(reporter)
        self.const_table = tables.constants
        self.struct_table = tables.structs
        self.enum_table = tables.enums
        self.func_table = tables.funcs
        self.external_table = tables.externals
        self.extension_table = tables.extensions
        self.generic_enum_table = tables.generic_enums
        self.generic_struct_table = tables.generic_structs
        self.generic_extension_table = tables.generic_extensions
        self.generic_func_table = tables.generic_funcs
        self.perk_table = tables.perks
        self.perk_impl_table = tables.perk_impls
        self.library_not_exported = tables.library_not_exported
        self.current_unit_name = current_unit_name  # Track which unit is being validated (for visibility checking)
        self.monomorphized_functions = monomorphized_functions or {}
        self.known_types: Set[BuiltinType] = {
            BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
            BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
            BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING,
            BuiltinType.BLANK, BuiltinType.STDIN, BuiltinType.STDOUT, BuiltinType.STDERR,
            BuiltinType.FILE
        }  # Built-in types
        self.current_function: Optional[FuncDef] = None
        # A library body transplanted into this program: a monomorphized instance of a
        # `.slib` template, or a lambda lifted out of one. It calls what it called at
        # home, the export closure included (#468).
        self.in_library_body = False
        self.variable_types: Dict[str, Type] = {}
        self.destroyed_arrays: List[set[str]] = []
        # `run` fills these from the program. A validator built to infer one type --
        # the monomorphizer and the instantiator both build one -- never runs, and it
        # reads no constant, so an empty map is the truth and not a fallback.
        self.ast_constants: Dict[str, ConstDef] = {}

        self.statement_validator = StatementValidator(self)
        self.expression_validator = ExpressionValidator(self)
        self.type_inference_visitor = TypeInferenceVisitor(self)

    def run(self, program: Program) -> None:
        """Entry point for type validation."""
        self.ast_constants = {const.name: const for const in program.constants}

        for const in program.constants:
            self._validate_constant(const)

        for func in program.functions:
            if hasattr(func, 'type_params') and func.type_params:
                continue
            self._validate_function(func)

        for ext in program.extensions:
            self._validate_extension_method(ext)

        for impl in program.perk_impls:
            self._validate_perk_implementation(impl)

    def validate_expression(self, expr: Expr) -> Optional[Type]:
        """Validate an expression and its subexpressions using the Visitor Pattern."""
        self.expression_validator.visit(expr)

        return self.infer_expression_type(expr)

    def infer_expression_type(self, expr: Expr) -> Optional[Type]:
        """Infer the type of an expression using the Visitor Pattern."""
        return self.type_inference_visitor.visit(expr)

    def _resolve_external_call(self, node) -> Optional['ExternalSig']:
        """Resolve a DotCall to a foreign function signature, if applicable."""
        from sushi_lang.semantics.ast import Name
        receiver = node.receiver
        if not isinstance(receiver, Name):
            return None
        ns = receiver.id
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
        from sushi_lang.semantics.passes.types.externals import _is_c_abi_type
        sig = self.external_table.lookup(node.external_ref[0], node.external_ref[1])
        if sig is None:
            return
        expected = sig.param_types
        is_variadic = getattr(sig, "is_variadic", False)
        fq_name = f"{node.external_ref[0]}.{node.external_ref[1]}"
        if is_variadic:
            if len(node.args) < len(expected):
                er.emit(self.reporter, er.ERR.CE2009, node.loc,
                        name=fq_name, expected=len(expected), got=len(node.args))
                return
        elif len(node.args) != len(expected):
            er.emit(self.reporter, er.ERR.CE2009, node.loc,
                    name=fq_name, expected=len(expected), got=len(node.args))
            return
        for index, (arg, exp_ty) in enumerate(zip(node.args, expected, strict=False)):
            got_ty = self.infer_expression_type(arg)
            if got_ty is None or exp_ty is None:
                continue
            if not types_compatible(self, got_ty, exp_ty):
                er.emit(self.reporter, er.ERR.CE2006, arg.loc,
                        index=index, expected=display_type(exp_ty), got=display_type(got_ty))
        # Trailing variadic args: each must be C-ABI representable (CE5005).
        # Record the inferred types so the backend can apply C promotion.
        if is_variadic:
            variadic_types = []
            for arg in node.args[len(expected):]:
                got_ty = self.infer_expression_type(arg)
                variadic_types.append(got_ty)
                if got_ty is not None and not _is_c_abi_type(got_ty):
                    er.emit(self.reporter, er.ERR.CE5005, arg.loc,
                            type=display_type(got_ty), name=fq_name)
            node.variadic_arg_types = variadic_types

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

    def _infer_array_literal_type(self, expr) -> Optional[Type]:
        """Delegate to inference module."""
        return infer_array_literal_type(self, expr)

    def _infer_index_access_type(self, expr) -> Optional[Type]:
        """Delegate to inference module."""
        return infer_index_access_type(self, expr)

    def _infer_dynamic_array_from_type(self, expr, expected_type=None) -> Optional[Type]:
        """Delegate to inference module."""
        return infer_dynamic_array_from_type(self, expr, expected_type)

    def _types_compatible(self, actual: Type, expected: Type) -> bool:
        """Delegate to compatibility module."""
        return types_compatible(self, actual, expected)


__all__ = ['TypeValidator']
