"""
Visitor-based type validation and inference for the Sushi language compiler.

This module implements the Visitor Pattern to replace large match/isinstance
chains in type validation and inference, providing a cleaner, more maintainable
approach to AST type analysis.
"""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from sushi_lang.internals import errors as er

if TYPE_CHECKING:
    from . import TypeValidator
from sushi_lang.semantics.visitors import NodeVisitor, RecursiveVisitor
from sushi_lang.semantics.typesys import Type, BuiltinType, ArrayType, DynamicArrayType, StructType, ForeignPtrType
from sushi_lang.semantics.type_predicates import is_string_convertible
from sushi_lang.semantics.ast import (
    # Statements
    Let, Rebind, ExprStmt, Return, Print, PrintLn, If, While, Foreach, Match, Break, Continue,
    # Expressions
    Name, IntLit, FloatLit, BoolLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess,
    UnaryOp, BinaryOp, Call, MethodCall, DotCall, DynamicArrayNew, DynamicArrayFrom, CastExpr, EnumConstructor, TryExpr, RangeExpr, Borrow, Spread, Lambda,
    BlankLit, MemberAccess
)


# Stdlib modules whose registry entries declare a return type outright, i.e. their
# get_return_type() takes no arguments. `math` is deliberately absent: its return type
# depends on the argument types, so its resolver takes params and visit_call handles it
# separately. Keyed by `use <path>`, so a function only resolves if its module is imported.
_REGISTRY_TYPED_STDLIB_MODULES = ("time", "sys/env", "sys/process", "random", "io/files")


def function_value_type_of(type_validator, name: str) -> Optional[Type]:
    """Build the FunctionType for a bare reference to a plain top-level function.

    Returns None when `name` is not a referenceable plain function value:
    - not a known top-level function, or
    - a variadic / parameter-pack function (their call ABI differs; deferred).

    The error type defaults to UnknownType("StdError") to mirror fn declarations; it is
    resolved alongside the other members by the normal type-resolution pass.
    """
    from sushi_lang.semantics.typesys import FunctionType, UnknownType
    sig = type_validator.func_table.by_name.get(name)
    if sig is None:
        return None
    for p in sig.params:
        if getattr(p, "is_variadic", False) or getattr(p, "is_pack", False):
            return None
    param_types = tuple(p.ty for p in sig.params)
    if any(pt is None for pt in param_types):
        return None
    ok_type = sig.ret_type if sig.ret_type is not None else BuiltinType.BLANK
    err_type = sig.err_type if sig.err_type is not None else UnknownType("StdError")
    return FunctionType(param_types=param_types, ok_type=ok_type, err_type=err_type)


def infer_lambda_type(type_validator, lam: Lambda, *, stamp: bool = True):
    """Compute (and, by default, cache on the node) the FunctionType of a lambda literal.

    Idempotent and diagnostic-free (the validator emits CE2094 separately). Resolves
    each param's type — declared for a typed param `|i32 x|`, or, for a bare param
    `|x|`, from an expected FunctionType propagated onto the node (`lam.expected_type`)
    — and fills the types of the captured free names from the enclosing scope. The
    result's `captures` descriptor marks the value as owning iff it captures anything.

    `stamp` is the annotate seam (issue #214). Pass 2 calls it with `stamp=True`: the
    result and the resolved param/capture types are persisted on the node for the lift
    pass and backend. Pass 1.5 calls it with `stamp=False` to type a lambda *argument*
    for instantiation collection WITHOUT mutating the node — Pass 1.5 runs before
    expected-type propagation, so stamping there would freeze an under-resolved
    `resolved_type` that Pass 2 (and lambda-lift) would then read back. The scope table
    is snapshotted and restored either way, so the read-only call has no side effect at
    all.
    """
    from sushi_lang.semantics.typesys import FunctionType, UnknownType
    if stamp and getattr(lam, "resolved_type", None) is not None:
        return lam.resolved_type

    expected = getattr(lam, "expected_type", None)
    saved = dict(type_validator.variable_types)

    param_types = []
    for idx, p in enumerate(lam.params):
        pty = p.ty
        if pty is None and isinstance(expected, FunctionType) and idx < len(expected.param_types):
            pty = expected.param_types[idx]
            if stamp:
                p.ty = pty  # persist the inferred type for the lift pass / backend
        param_types.append(pty)
        if pty is not None:
            type_validator.variable_types[p.name] = pty

    # Fill captured names' types from the ENCLOSING scope (pre-param bindings).
    if stamp:
        for cap in (lam.captures or []):
            if cap.ty is None:
                cap.ty = saved.get(cap.name)

    if lam.ret is not None:
        ok_type = lam.ret
    elif not lam.is_block_body:
        ok_type = type_validator.infer_expression_type(lam.body)
    elif isinstance(expected, FunctionType):
        ok_type = expected.ok_type
    else:
        ok_type = None

    err_type = lam.err_type
    if err_type is None:
        err_type = expected.err_type if isinstance(expected, FunctionType) else UnknownType("StdError")

    # Restore the enclosing variable table (params are lambda-local).
    type_validator.variable_types.clear()
    type_validator.variable_types.update(saved)

    ft = FunctionType(
        param_types=tuple(param_types),
        ok_type=ok_type,
        err_type=err_type,
        captures=tuple(lam.captures or ()),
    )
    if stamp:
        lam.resolved_type = ft
    return ft


def resolve_fn_field_call(type_validator, node) -> Optional["Type"]:
    """Field-vs-method rule for `obj.handler(args)` (a DotCall).

    Returns the field's FunctionType when `node.receiver` has a struct type carrying a
    fn-typed field named `node.method` AND no method of that name exists for that struct
    (a same-named extension/perk/auto-derived method always WINS over the field). In that
    case `obj.handler(args)` is an indirect call through the field value,
    `(obj.handler)(args)`. Returns None otherwise (normal method-call dispatch).
    """
    from sushi_lang.semantics.typesys import StructType, FunctionType, ReferenceType
    recv_ty = type_validator.infer_expression_type(node.receiver)
    if isinstance(recv_ty, ReferenceType):
        recv_ty = recv_ty.referenced_type
    if not isinstance(recv_ty, StructType):
        return None
    field_ty = recv_ty.get_field_type(node.method)
    if not isinstance(field_ty, FunctionType):
        return None
    # A method of the same name wins over the field: auto-derived .hash(), then
    # concrete and generic extension/perk methods.
    if node.method == "hash":
        return None
    if type_validator.extension_table.get_method(recv_ty, node.method) is not None:
        return None
    if '<' in recv_ty.name:
        base_name = recv_ty.name.split('<')[0]
        if type_validator.generic_extension_table.get_method(base_name, node.method) is not None:
            return None
    return field_ty


def validate_fn_field_call_args(type_validator, node, fn_ty) -> None:
    """Validate `obj.handler(args)` arg count and types against the field's FunctionType."""
    expected = fn_ty.param_types
    if len(node.args) != len(expected):
        er.emit(type_validator.reporter, er.ERR.CE2092, node.loc,
                expected=str(fn_ty),
                actual=f"a call with {len(node.args)} argument(s)")
        return
    from sushi_lang.semantics.passes.types.compatibility import types_compatible
    for arg, param_ty in zip(node.args, expected, strict=False):
        type_validator.validate_expression(arg)
        arg_ty = type_validator.infer_expression_type(arg)
        if arg_ty is None:
            continue
        if not types_compatible(type_validator, arg_ty, param_ty):
            er.emit(type_validator.reporter, er.ERR.CE2092, getattr(arg, 'loc', node.loc),
                    expected=str(param_ty), actual=str(arg_ty))


class StatementValidator(RecursiveVisitor):
    """
    Visitor for validating statements using the Visitor Pattern.

    Replaces the large match statement in TypeValidator._validate_statement()
    with clean, focused methods for each statement type.
    """

    def __init__(self, type_validator: 'TypeValidator'):
        """Initialize with reference to the main type validator."""
        self.type_validator = type_validator

    # === Statement validation methods ===

    def visit_if(self, node: If) -> None:
        """Validate if statement conditions and branches."""
        # Validate all condition-block arms
        for cond, block in node.arms:
            # Validate condition is boolean (CE2005)
            self.type_validator._validate_boolean_condition(cond, "if")
            # Validate block
            self.type_validator._validate_block(block)

        # Validate else branch if present
        if node.else_block:
            self.type_validator._validate_block(node.else_block)

    def visit_while(self, node: While) -> None:
        """Validate while statement condition and body."""
        # Validate condition is boolean (CE2005)
        self.type_validator._validate_boolean_condition(node.cond, "while")
        # Validate body
        self.type_validator._validate_block(node.body)

    def visit_foreach(self, node: Foreach) -> None:
        """Validate foreach statement iterator type and body."""
        self.type_validator._validate_foreach_statement(node)

    def visit_match(self, node: Match) -> None:
        """Validate match statement with exhaustiveness checking."""
        self.type_validator._validate_match_statement(node)

    def visit_let(self, node: Let) -> None:
        """Validate let statement."""
        self.type_validator._validate_let_statement(node)

    def visit_return(self, node: Return) -> None:
        """Validate return statement."""
        self.type_validator._validate_return_statement(node)

    def visit_exprstmt(self, node: ExprStmt) -> None:
        """Validate expression statement and warn if Result<T> is unused."""
        # First validate the expression
        self.type_validator.validate_expression(node.expr)

        # Check if the expression evaluates to Result<T, E>
        expr_type = self.type_validator.infer_expression_type(node.expr)
        if expr_type is not None:
            from sushi_lang.semantics.typesys import EnumType, BuiltinType

            if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
                # Extract T from Result<T, E>
                ok_variant = expr_type.get_variant("Ok")
                if ok_variant and ok_variant.associated_types:
                    t_type = ok_variant.associated_types[0]

                    # Skip warning if T is blank type (~)
                    # Blank functions have no meaningful return value to handle
                    if t_type == BuiltinType.BLANK:
                        return

                # Emit warning for unused Result<T, E> (where T is not blank)
                er.emit(self.type_validator.reporter, er.ERR.CW2001, node.expr.loc)

    def visit_print(self, node: Print) -> None:
        """Validate print statement."""
        self.type_validator.validate_expression(node.value)

        # Check if trying to print Result<T> directly (CE2037)
        expr_type = self.type_validator.infer_expression_type(node.value)
        if expr_type is not None:
            from sushi_lang.semantics.typesys import EnumType
            if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
                er.emit(self.type_validator.reporter, er.ERR.CE2037, node.value.loc)

    def visit_println(self, node: PrintLn) -> None:
        """Validate println statement."""
        self.type_validator.validate_expression(node.value)

        # Check if trying to print Result<T> directly (CE2037)
        expr_type = self.type_validator.infer_expression_type(node.value)
        if expr_type is not None:
            from sushi_lang.semantics.typesys import EnumType
            if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
                er.emit(self.type_validator.reporter, er.ERR.CE2037, node.value.loc)

    def visit_rebind(self, node: Rebind) -> None:
        """Validate rebind statement."""
        self.type_validator._validate_rebind_statement(node)

    def visit_break(self, node: Break) -> None:
        """Break statements don't need type validation."""
        pass

    def visit_continue(self, node: Continue) -> None:
        """Continue statements don't need type validation."""
        pass


class ExpressionValidator(RecursiveVisitor):
    """
    Visitor for validating expressions using the Visitor Pattern.

    Replaces the large match statement in TypeValidator._validate_expression()
    with clean, focused methods for each expression type.
    """

    def __init__(self, type_validator: 'TypeValidator'):
        """Initialize with reference to the main type validator."""
        self.type_validator = type_validator

    # === Expression validation methods ===

    def visit_unaryop(self, node: UnaryOp) -> None:
        """Validate unary operation."""
        # CE5010: a foreign ptr is an opaque handle - no negation, NOT, or truthiness
        operand_type = self.type_validator.infer_expression_type(node.expr)
        if isinstance(operand_type, ForeignPtrType):
            er.emit(self.type_validator.reporter, er.ERR.CE5010, node.loc, op=node.op)
            return

        # A negated integer literal is range-checked as one signed value, so
        # that i32 min (-2147483648) stays legal while the positive literal
        # 2147483648 alone would not be.
        if node.op == "neg" and isinstance(node.expr, IntLit):
            if not getattr(node.expr, 'in_cast_context', False):
                if -int(node.expr.value) < -(2 ** 31):
                    self._emit_literal_overflow(node.expr)
            node.expr.range_checked = True

        self.type_validator.validate_expression(node.expr)

        # Additional validation for bitwise NOT operator
        if node.op == "~":
            from sushi_lang.semantics.passes.types.expressions import validate_bitwise_unary
            validate_bitwise_unary(self.type_validator, node)

    def visit_binaryop(self, node: BinaryOp) -> None:
        """Validate binary operation."""
        self.type_validator.validate_expression(node.left)
        self.type_validator.validate_expression(node.right)

        # Operand-driven literal typing: when one operand is a bare (unstamped)
        # numeric literal and the other a concrete numeric type, stamp the literal
        # to that type so `a + 1` (a: u8) is u8 + u8, not the mixed u8 + i32 below.
        self._context_type_operand_from_sibling(node)

        left_type = self.type_validator.infer_expression_type(node.left)
        right_type = self.type_validator.infer_expression_type(node.right)

        # CE5010: a foreign ptr is an opaque handle - no comparison, arithmetic,
        # bitwise, or logical operations of any kind.
        if isinstance(left_type, ForeignPtrType) or isinstance(right_type, ForeignPtrType):
            er.emit(self.type_validator.reporter, er.ERR.CE5010, node.loc, op=node.op)
            return

        # Check for string concatenation with + operator (CE2509)
        if node.op == "+":
            # Emit error if either operand is a string
            if left_type == BuiltinType.STRING or right_type == BuiltinType.STRING:
                er.emit_with(self.type_validator.reporter, er.ERR.CE2509, node.loc) \
                    .help("use string interpolation: \"{a}{b}\"").emit()

        # Check for mixed numeric types in comparison and arithmetic operations
        if node.op in ["==", "!=", "<", "<=", ">", ">=", "+", "-", "*", "/", "%"]:
            if left_type is not None and right_type is not None:
                # Check if both are numeric types but different
                left_is_numeric = isinstance(left_type, BuiltinType) and left_type in [
                    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
                    BuiltinType.F32, BuiltinType.F64
                ]
                right_is_numeric = isinstance(right_type, BuiltinType) and right_type in [
                    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
                    BuiltinType.F32, BuiltinType.F64
                ]

                if left_is_numeric and right_is_numeric and left_type != right_type:
                    # Mixed numeric types - require explicit cast
                    er.emit(self.type_validator.reporter, er.ERR.CE2510, node.loc,
                           left_type=str(left_type), right_type=str(right_type))

        # Additional validation for bitwise operators
        if node.op in ["&", "|", "^", "<<", ">>"]:
            self.type_validator._validate_bitwise_operation(node)

    def _context_type_operand_from_sibling(self, node: BinaryOp) -> None:
        """Stamp a bare numeric-literal operand with its concrete sibling's type.

        Applies only when exactly one operand is an unstamped IntLit/FloatLit and
        the other resolves to a concrete BuiltinType. Both-bare (`1 + 2`) keeps the
        i32/f64 default; both-concrete-and-different stays a CE2510 mixed-type error.
        """
        left, right = node.left, node.right
        left_bare = isinstance(left, (IntLit, FloatLit)) and left.resolved_type is None
        right_bare = isinstance(right, (IntLit, FloatLit)) and right.resolved_type is None
        if left_bare == right_bare:
            return
        from sushi_lang.semantics.passes.types.propagation import propagate_types_to_value
        if left_bare:
            sibling_type = self.type_validator.infer_expression_type(right)
            if isinstance(sibling_type, BuiltinType):
                propagate_types_to_value(self.type_validator, left, sibling_type)
        else:
            sibling_type = self.type_validator.infer_expression_type(left)
            if isinstance(sibling_type, BuiltinType):
                propagate_types_to_value(self.type_validator, right, sibling_type)

    def visit_lambda(self, node: Lambda) -> None:
        """Validate a lambda body and reject illegal captures (CE2094)."""
        tv = self.type_validator
        ft = infer_lambda_type(tv, node)  # fills param + capture types (idempotent)

        # CE2094: capturing a &peek/&poke borrow is deferred to Tier 2. A captured
        # name whose enclosing type is a reference is a borrow capture.
        from sushi_lang.semantics.typesys import ReferenceType, DynamicArrayType, is_owning_type
        for cap in (node.captures or []):
            if isinstance(cap.ty, ReferenceType):
                er.emit(tv.reporter, er.ERR.CE2094, node.loc,
                        reason=f"cannot capture '{cap.name}': it is a borrow (&peek/&poke capture is deferred to Tier 2)")
            elif isinstance(cap.ty, DynamicArrayType):
                # Move-capture (T1.5): a dynamic array is moved into the heap environment,
                # which owns it and frees it in the env destructor. The outer binding is
                # consumed (borrow-checked use-after-move, CE2405). No diagnostic.
                pass
            elif is_owning_type(cap.ty):
                # List<T> / Own<T> move-capture into the env (T1.5 item 1): moved in,
                # owned + freed by the env destructor, outer binding consumed (CE2405).
                # Reading them back inside the body dispatches through the env-field
                # member-access receiver path (backend calls/utils.py).
                pass

        # CE2094 (T1.7 cut): an owning parameter type on a function value has no
        # deep-copy on the indirect-call path yet (a latent double-free), so reject it.
        for p in node.params:
            if is_owning_type(p.ty):
                er.emit(tv.reporter, er.ERR.CE2094, node.loc,
                        reason=f"lambda parameter '{p.name}' has an owning type '{p.ty}'; "
                               f"owning function-value parameters are deferred to Tier 2")

        # Validate the body with the lambda's params in scope (captures already are).
        saved_vars = dict(tv.variable_types)
        for p in node.params:
            if p.ty is not None:
                tv.variable_types[p.name] = p.ty
        if node.is_block_body:
            # Return statements inside must check against the LAMBDA's ok/err, not
            # the enclosing function's, so swap current_function to a synthetic sig.
            from sushi_lang.semantics.ast import FuncDef
            synthetic = FuncDef(name="<lambda>", params=list(node.params), ret=ft.ok_type,
                                body=node.body, err_type=ft.err_type, loc=node.loc)
            saved_fn = tv.current_function
            tv.current_function = synthetic
            tv._validate_block(node.body)
            tv.current_function = saved_fn
        else:
            tv.validate_expression(node.body)
        tv.variable_types.clear()
        tv.variable_types.update(saved_vars)

    def visit_call(self, node: Call) -> None:
        """Validate function call."""
        self.type_validator._validate_function_call(node)

    def visit_methodcall(self, node: MethodCall) -> None:
        """Validate method call."""
        self.type_validator._validate_method_call(node)

    def visit_dotcall(self, node: DotCall) -> None:
        """Validate dot-call expression - resolve to enum constructor or method call."""
        # FFI: foreign namespace call (e.g., libc.strlen) - NEW FIRST branch.
        # Locals shadow namespaces, so skip if the name is a bound local.
        if self.type_validator._resolve_external_call(node):
            for arg in node.args:
                self.type_validator.validate_expression(arg)
            self.type_validator._validate_external_call_args(node)
            return

        # f64.from_bits(bits) / f32.from_bits(bits): static bit-reinterpret constructor.
        # The receiver is a primitive float type NAME, not a value, so handle it before
        # validating the receiver as an expression.
        if (isinstance(node.receiver, Name) and node.receiver.id in ("f64", "f32")
                and node.method == "from_bits"):
            self._validate_from_bits(node)
            return

        # The receiver is deliberately NOT validated here. Every path out of this method either
        # does not have a receiver to validate, or delegates to something that validates it --
        # and validating it here as well walked the receiver TWICE, so any diagnostic inside it
        # was reported twice (#201). Errors, not just warnings.
        #
        #   enum constructor  -- the receiver is a type NAME, not a value; nothing to validate
        #   fn-typed field    -- validated explicitly in that branch, below
        #   method call       -- validate_method_call validates it (calls/methods.py), as it must
        #                        anyway for a real MethodCall node reaching visit_methodcall

        # Check if receiver is an enum type name
        if isinstance(node.receiver, Name):
            receiver_name = node.receiver.id
            # Check if it's an enum type (concrete or generic)
            if (receiver_name in self.type_validator.enum_table.by_name or
                receiver_name in self.type_validator.generic_enum_table.by_name):
                # This is an enum constructor - validate as such
                # Convert to EnumConstructor for validation
                from sushi_lang.semantics.ast import EnumConstructor
                temp_constructor = EnumConstructor(
                    enum_name=receiver_name,
                    variant_name=node.method,
                    args=node.args,
                    enum_name_span=node.receiver.loc,
                    loc=node.loc
                )

                # CRITICAL: Copy resolved_enum_type FROM the DotCall TO the temp BEFORE validation
                # This is set by _validate_return_statement or _validate_let_statement
                if hasattr(node, 'resolved_enum_type') and node.resolved_enum_type is not None:
                    temp_constructor.resolved_enum_type = node.resolved_enum_type

                self.type_validator._validate_enum_constructor(temp_constructor)

                # CRITICAL: Copy resolved_enum_type back to the DotCall node for codegen
                # (in case validation set or updated it)
                if hasattr(temp_constructor, 'resolved_enum_type'):
                    node.resolved_enum_type = temp_constructor.resolved_enum_type
                return

        # obj.handler(): indirect call through a fn-typed struct field (a same-named
        # method wins over the field -- see resolve_fn_field_call). The backend reads
        # node.callee_fn_type to emit the fat-pointer indirect call.
        fn_field_ty = resolve_fn_field_call(self.type_validator, node)
        if fn_field_ty is not None:
            # This branch does not reach validate_method_call, so it owns the receiver walk.
            self.type_validator.validate_expression(node.receiver)
            node.callee_fn_type = fn_field_ty
            validate_fn_field_call_args(self.type_validator, node, fn_field_ty)
            return

        # Otherwise, it's a method call - validate as such
        # Convert to MethodCall for validation
        from sushi_lang.semantics.ast import MethodCall
        temp_method_call = MethodCall(
            receiver=node.receiver,
            method=node.method,
            args=node.args,
            loc=node.loc
        )
        self.type_validator._validate_method_call(temp_method_call)

        # CRITICAL: Copy inferred_return_type back to the DotCall node for codegen
        # This is set by perk/extension method validation
        if hasattr(temp_method_call, 'inferred_return_type') and temp_method_call.inferred_return_type is not None:
            node.inferred_return_type = temp_method_call.inferred_return_type

        # CRITICAL: Copy resolved_enum_type back to the DotCall node for codegen
        # This is set by Result<T>/Maybe<T> method validation
        if hasattr(temp_method_call, 'resolved_enum_type') and temp_method_call.resolved_enum_type is not None:
            node.resolved_enum_type = temp_method_call.resolved_enum_type

    def _validate_from_bits(self, node: DotCall) -> None:
        """Validate f64.from_bits(u64) / f32.from_bits(u32) static reinterpret calls."""
        tv = self.type_validator
        is_f64 = node.receiver.id == "f64"
        float_ty = BuiltinType.F64 if is_f64 else BuiltinType.F32
        expected_arg = BuiltinType.U64 if is_f64 else BuiltinType.U32
        node.inferred_return_type = float_ty

        if len(node.args) != 1:
            er.emit(tv.reporter, er.ERR.CE2009, node.loc,
                    name=f"{node.receiver.id}.from_bits", expected=1, got=len(node.args))
            return

        arg = node.args[0]
        tv.validate_expression(arg)
        arg_type = tv.infer_expression_type(arg)
        if arg_type is not None and arg_type != expected_arg:
            er.emit(tv.reporter, er.ERR.CE2006, getattr(arg, 'loc', node.loc),
                    index=0, expected=str(expected_arg), got=str(arg_type))

    def visit_arrayliteral(self, node: ArrayLiteral) -> None:
        """Validate array literal."""
        self.type_validator._validate_array_literal(node)

    def visit_indexaccess(self, node: IndexAccess) -> None:
        """Validate index access."""
        self.type_validator._validate_index_access(node)

    def visit_dynamicarraynew(self, node: DynamicArrayNew) -> None:
        """new() constructor - no subexpressions to validate."""
        pass

    def visit_dynamicarrayfrom(self, node: DynamicArrayFrom) -> None:
        """from(array_literal) - validate the array literal."""
        self.type_validator.validate_expression(node.elements)

    def visit_castexpr(self, node: CastExpr) -> None:
        """Cast expression - validate the source expression and check cast validity."""
        self.type_validator._validate_cast_expression(node)

    def visit_enumconstructor(self, node: EnumConstructor) -> None:
        """Validate enum constructor call (including Result.Ok() and Result.Err())."""
        self.type_validator._validate_enum_constructor(node)

    def visit_tryexpr(self, node: TryExpr) -> None:
        """Validate try expression (?? operator)."""
        self.type_validator._validate_try_expression(node)

    def visit_rangeexpr(self, node: RangeExpr) -> None:
        """Validate range expression."""
        from sushi_lang.semantics.passes.types.expressions import validate_range_expression
        validate_range_expression(self.type_validator, node)

    # Terminal expressions don't need recursive validation
    def visit_name(self, node: Name) -> None:
        """Name expressions are terminal.

        The one check here is the first-class-function v1 boundary: referencing a
        *generic* function as a value is not supported yet (CE2093). A local of the
        same name shadows the function and is a plain variable read.
        """
        tv = self.type_validator
        if node.id in tv.variable_types or node.id in tv.const_table.by_name:
            return
        if node.id in tv.generic_func_table.by_name:
            # T2.3: a generic-fn reference is allowed when an explicit expected fn type
            # is present (e.g. `let fn(i32) -> i32 g = identity`). Solve the type args,
            # rewrite the node to the mangled concrete name, and accept. A bare reference
            # with no expected fn type stays CE2093 (the minimal-slice boundary).
            from sushi_lang.semantics.passes.types.calls.generics import resolve_generic_fn_reference
            resolved = resolve_generic_fn_reference(tv, node.id, getattr(node, "expected_type", None))
            if resolved is not None:
                node.id = resolved[0]  # mirror the call-site mangled-name rewrite
                return
            er.emit(tv.reporter, er.ERR.CE2093, node.loc,
                    name=node.id, reason="generic function references are deferred (v1)")

    def visit_intlit(self, node: IntLit) -> None:
        """Range-check a bare integer literal (CE2070).

        Literals default to i32, so outside a direct integer `as` cast a
        decimal literal must fit the signed i32 range and a radix literal
        (hex/binary/octal) must fit the 32-bit pattern. Inside a direct cast
        the literal materializes at the target width instead (Rust `as`
        semantics) and is exempt. Negated literals are pre-checked (and
        marked) by visit_unaryop as a single signed value.
        """
        if getattr(node, 'in_cast_context', False) or getattr(node, 'range_checked', False):
            return
        # A context-typed literal was already range-checked against its stamped type
        # at propagation time; skip the default-i32 overflow check.
        if node.resolved_type is not None:
            return
        value = int(node.value)
        if node.radix == 10:
            in_range = 0 <= value <= 2 ** 31 - 1
        else:
            # Bit-pattern semantics: 0xFFFFFFFF is a legal 32-bit pattern
            in_range = 0 <= value <= 2 ** 32 - 1
        if not in_range:
            self._emit_literal_overflow(node)

    def _emit_literal_overflow(self, node: IntLit) -> None:
        """Emit CE2070 for an integer literal that cannot fit its default i32 type."""
        radix_names = {2: "binary", 8: "octal", 10: "decimal", 16: "hexadecimal"}
        er.emit(self.type_validator.reporter, er.ERR.CE2070, node.loc,
                radix=radix_names.get(node.radix, "integer"),
                literal=str(node.value), type="i32")

    def visit_floatlit(self, node: FloatLit) -> None:
        """Float literals are terminal."""
        pass

    def visit_boollit(self, node: BoolLit) -> None:
        """Boolean literals are terminal."""
        pass

    def visit_stringlit(self, node: StringLit) -> None:
        """String literals are terminal."""
        pass

    def visit_interpolatedstring(self, node: InterpolatedString) -> None:
        """Visit expressions in interpolated string."""
        for part in node.parts:
            if not isinstance(part, str):  # part is an Expr
                self.visit(part)


class TypeInferenceVisitor(NodeVisitor[Optional[Type]]):
    """
    Visitor for type inference using the Visitor Pattern.

    Replaces the large match statement in TypeValidator._infer_expression_type()
    with clean, focused methods for each expression type.
    """

    def __init__(self, type_validator: 'TypeValidator'):
        """Initialize with reference to the main type validator."""
        self.type_validator = type_validator

    # === Utility methods ===

    def _resolve_generic_to_semantic_type(self, generic_type: 'Type') -> 'Type':
        """Resolve a GenericTypeRef to its concrete semantic type where applicable.

        This centralizes the conversion logic for special generic types that have
        semantic representations beyond simple monomorphization.

        Args:
            generic_type: The type to potentially resolve (may be GenericTypeRef or other)

        Returns:
            Resolved semantic type, or the original type if no resolution is needed

        Examples:
            GenericTypeRef("Result", [i32, MyError]) → EnumType("Result<i32, MyError>")
            GenericTypeRef("Maybe", [i32]) → GenericTypeRef("Maybe", [i32])  # no change
        """
        from sushi_lang.semantics.generics.types import GenericTypeRef

        # Only process GenericTypeRef types
        if not isinstance(generic_type, GenericTypeRef):
            return generic_type

        # Result<T, E> interns to its EnumType, like every other generic.
        if generic_type.base_name == "Result" and len(generic_type.type_args) == 2:
            interned = self._intern_result(generic_type.type_args[0], generic_type.type_args[1])
            if interned is not None:
                return interned

        # For other generic types (Maybe, Own, etc.), return as-is
        # They will be handled by monomorphization
        return generic_type

    # === Type inference methods ===

    def visit_intlit(self, node: IntLit) -> Optional[Type]:
        """Infer integer literal type (context-typed if stamped, else default i32)."""
        return node.resolved_type or BuiltinType.I32

    def visit_floatlit(self, node: FloatLit) -> Optional[Type]:
        """Infer float literal type (context-typed if stamped, else default f64)."""
        return node.resolved_type or BuiltinType.F64

    def visit_boollit(self, node: BoolLit) -> Optional[Type]:
        """Infer boolean literal type."""
        return BuiltinType.BOOL

    def visit_blanklit(self, node: 'BlankLit') -> Optional[Type]:
        """Infer blank literal type."""
        return BuiltinType.BLANK

    def visit_spread(self, node: Spread) -> Optional[Type]:
        """A bloomed argument `arr...` infers as the whole array type of its source.

        Position/target validity (must be a variadic slot, sole last trailing arg)
        is enforced by the call validators, not here.
        """
        return self.type_validator.infer_expression_type(node.value)

    def visit_stringlit(self, node: StringLit) -> Optional[Type]:
        """Infer string literal type."""
        return BuiltinType.STRING

    def visit_interpolatedstring(self, node: InterpolatedString) -> Optional[Type]:
        """Infer interpolated string type and validate expression types."""
        # Validate that all expression parts can be converted to strings
        for part in node.parts:
            if not isinstance(part, str):
                # This is an expression - validate it can be converted to string
                expr_type = self.type_validator.infer_expression_type(part)
                if expr_type and not is_string_convertible(expr_type):
                    # Emit error for unsupported type in interpolation
                    er.emit(
                        self.type_validator.reporter,
                        er.ERR.CE2035,
                        part.loc,
                        type=str(expr_type)
                    )
        return BuiltinType.STRING

    def visit_arrayliteral(self, node: ArrayLiteral) -> Optional[Type]:
        """Infer array literal type."""
        return self.type_validator._infer_array_literal_type(node)

    def visit_indexaccess(self, node: IndexAccess) -> Optional[Type]:
        """Infer index access type."""
        return self.type_validator._infer_index_access_type(node)

    def visit_memberaccess(self, node: MemberAccess) -> Optional[Type]:
        """Infer member access type (struct field access).

        For fields with generic types like Result<T, E>, this resolves them to their
        semantic type representations for compatibility with pattern matching
        and other type operations.
        """
        # Get the type of the receiver (the struct)
        receiver_type = self.type_validator.infer_expression_type(node.receiver)

        if receiver_type is None:
            return None

        # If it's a struct type, look up the field type
        if isinstance(receiver_type, StructType):
            # Fields are stored as tuples of (field_name, field_type)
            for field_name, field_type in receiver_type.fields:
                if field_name == node.member:
                    # Resolve generic types to semantic types where applicable
                    # E.g., GenericTypeRef("Result", [T, E]) → EnumType("Result<T, E>")
                    # This ensures pattern matching and other operations work correctly
                    resolved_type = self._resolve_generic_to_semantic_type(field_type)
                    return resolved_type

        return None

    def visit_name(self, node: Name) -> Optional[Type]:
        """Infer name expression type."""
        # Check for special built-in identifiers first (stdin, stdout, stderr)
        if node.id == "stdin":
            return BuiltinType.STDIN
        elif node.id == "stdout":
            return BuiltinType.STDOUT
        elif node.id == "stderr":
            return BuiltinType.STDERR

        # Check for math module constants (PI, E, TAU)
        if node.id in {'PI', 'E', 'TAU'}:
            from sushi_lang.sushi_stdlib.src import math as math_module
            if math_module.is_builtin_math_constant(node.id):
                return BuiltinType.F64

        # Look up variable type from variable table
        var_type = self.type_validator.variable_types.get(node.id)
        if var_type is not None:
            # Auto-dereference reference types - using a reference variable
            # yields the referenced value, not the reference itself
            from sushi_lang.semantics.typesys import ReferenceType
            if isinstance(var_type, ReferenceType):
                return var_type.referenced_type
            return var_type

        # If not found in variables, check constants
        if node.id in self.type_validator.const_table.by_name:
            const_sig = self.type_validator.const_table.by_name[node.id]
            return const_sig.const_type

        # A bare reference to a plain top-level function is a first-class function value.
        fn_value_type = function_value_type_of(self.type_validator, node.id)
        if fn_value_type is not None:
            return fn_value_type

        # A generic-fn reference with an explicit expected fn type (T2.3): solve the type
        # args and return the concrete FunctionType (the node is rewritten to the mangled
        # name during validation).
        if node.id in self.type_validator.generic_func_table.by_name:
            from sushi_lang.semantics.passes.types.calls.generics import resolve_generic_fn_reference
            resolved = resolve_generic_fn_reference(
                self.type_validator, node.id, getattr(node, "expected_type", None))
            if resolved is not None:
                return resolved[1]

        return None

    def visit_lambda(self, node: Lambda) -> Optional[Type]:
        """Infer a lambda literal's type (its FunctionType)."""
        return infer_lambda_type(self.type_validator, node)

    def visit_unaryop(self, node: UnaryOp) -> Optional[Type]:
        """Infer unary operation type."""
        # Logical NOT returns bool
        if node.op == "not":
            return BuiltinType.BOOL
        # Bitwise NOT preserves the integer operand type
        if node.op == "~":
            return self.type_validator.infer_expression_type(node.expr)
        # Negation preserves numeric type
        if node.op == "neg":
            return self.type_validator.infer_expression_type(node.expr)
        # Default: preserve type
        return self.type_validator.infer_expression_type(node.expr)

    def visit_binaryop(self, node: BinaryOp) -> Optional[Type]:
        """Infer binary operation type directly (no delegation)."""
        # Comparison operators return bool
        if node.op in ["==", "!=", "<", "<=", ">", ">="]:
            return BuiltinType.BOOL

        # Logical operators return bool
        if node.op in ["and", "or", "xor"]:
            return BuiltinType.BOOL

        # Arithmetic operators - return type depends on operands
        if node.op in ["+", "-", "*", "/", "%"]:
            left_type = self.type_validator.infer_expression_type(node.left)
            right_type = self.type_validator.infer_expression_type(node.right)

            # If either operand is a string, this is an error (handled by validation)
            # Return None to avoid cascading type mismatch errors
            if left_type == BuiltinType.STRING or right_type == BuiltinType.STRING:
                return None

            # Strict same-type rule: the result is the common operand type.
            # Mixed numeric operands are a CE2510 error (emitted by the
            # ExpressionValidator); return None there to avoid cascading
            # mismatch errors on the enclosing expression.
            numeric = (BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
                       BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
                       BuiltinType.F32, BuiltinType.F64)
            if left_type == right_type and left_type in numeric:
                return left_type
            # One side unknown (e.g. an unresolved call): trust the known side
            if left_type is None and right_type in numeric:
                return right_type
            if right_type is None and left_type in numeric:
                return left_type
            return None

        # Bitwise operators return the type of the left operand
        if node.op in ["&", "|", "^", "<<", ">>"]:
            return self.type_validator.infer_expression_type(node.left)

        return None

    def _intern_result(self, ok_type: Type, err_type: Type) -> Optional[Type]:
        """The interned ``Result<ok, err>`` EnumType -- what a call's return type IS at runtime.

        A call used to infer as a `ResultType`, which is not an `EnumType`, so it compared unequal
        to the `Result<...>` enum a declared field/annotation resolves to -- that is #184's
        "expects Result<string, StdError>, got Result<string, StdError>". Interning both sides
        through here makes them the same object.
        """
        from sushi_lang.semantics.generics.results import ensure_result_type_in_table
        return ensure_result_type_in_table(
            self.type_validator.enum_table, ok_type, err_type,
            struct_table=self.type_validator.struct_table.by_name,
        )

    def _materialize_stdlib_return_type(self, ret_type: Optional[Type]) -> Optional[Type]:
        """Resolve a registry-declared stdlib return type into a concrete type.

        The registry declares return types as type-REFS, because it has no access to the enum
        table: a Result comes back as GenericTypeRef("Result", (i64, UnknownType("FileError")))
        and getenv's Maybe as GenericTypeRef("Maybe", (string,)). Materialize both into real
        enums here, so `??` and `match` see a concrete EnumType rather than a forward reference.
        Non-generic returns (i32, bool, ~) pass through.
        """
        from sushi_lang.semantics.generics.types import GenericTypeRef
        from sushi_lang.semantics.generics.maybe import ensure_maybe_type_in_table
        from sushi_lang.semantics.generics.results import ensure_result_type_in_table
        from sushi_lang.semantics.type_resolution import resolve_unknown_type

        structs = self.type_validator.struct_table.by_name
        enums = self.type_validator.enum_table.by_name

        if (isinstance(ret_type, GenericTypeRef) and ret_type.base_name == "Result"
                and len(ret_type.type_args) == 2):
            return ensure_result_type_in_table(
                self.type_validator.enum_table,
                ret_type.type_args[0],
                ret_type.type_args[1],
                struct_table=structs,
            ) or ret_type

        if (isinstance(ret_type, GenericTypeRef) and ret_type.base_name == "Maybe"
                and len(ret_type.type_args) == 1):
            value_type = resolve_unknown_type(ret_type.type_args[0], structs, enums)
            return ensure_maybe_type_in_table(
                self.type_validator.enum_table, value_type, struct_table=structs) or ret_type

        return ret_type

    def visit_call(self, node: Call) -> Optional[Type]:
        """Infer function call type."""
        from sushi_lang.semantics.typesys import FunctionType
        # Call-through an arbitrary expression that evaluates to a function value:
        # `env.f(x)` (a captured closure in a lifted lambda body), `obj.handler()`,
        # `arr[0]()`, `(e)()`. Calling through it yields Result<ok, err>, exactly like a
        # direct call (so `f(x)??` unwraps to ok_type).
        if not isinstance(node.callee, Name):
            callee_ty = self.type_validator.infer_expression_type(node.callee)
            if isinstance(callee_ty, FunctionType):
                return self._intern_result(callee_ty.ok_type, callee_ty.err_type)
            return None

        # Look up function return type
        function_name = node.callee.id

        # Indirect call through a first-class function value: yields Result<ok, err>,
        # exactly like a direct call (so `f(x)??` unwraps to ok_type).
        callee_var_ty = self.type_validator.variable_types.get(function_name)
        if isinstance(callee_var_ty, FunctionType):
            return self._intern_result(callee_var_ty.ok_type, callee_var_ty.err_type)

        # Check if this is a struct constructor
        if function_name in self.type_validator.struct_table.by_name:
            return self.type_validator.struct_table.by_name[function_name]

        # Check for built-in global functions
        if function_name == "open":
            # open() returns FileResult enum
            return self.type_validator.enum_table.by_name.get("FileResult")

        # Stdlib functions whose return type the registry declares outright. The registry
        # is the single source of truth the backend consults too, so reading it here keeps
        # the two from drifting. Hardcoded copies used to live here and had already gone
        # stale: they looked up a one-arg "Result<i32>" enum that is never registered (the
        # canonical name is two-arg, Result<T, E>), so every time/sys-env call silently
        # returned None and fell through to backend re-inference.
        #
        # math is excluded on purpose: its return type depends on the argument types, so
        # its get_return_type() takes params and it keeps its own branch below.
        for module_path in _REGISTRY_TYPED_STDLIB_MODULES:
            stdlib_func = self.type_validator.func_table.lookup_stdlib_function(
                module_path, function_name
            )
            if stdlib_func is not None and not stdlib_func.is_constant:
                return self._materialize_stdlib_return_type(stdlib_func.get_return_type())

        # Check for math module functions
        if function_name in {'abs', 'min', 'max', 'sqrt', 'pow', 'floor', 'ceil', 'round', 'trunc'}:
            from sushi_lang.sushi_stdlib.src import math as math_module
            if math_module.is_builtin_math_function(function_name):
                # Get the parameter types to determine return type
                param_types = []
                for arg in node.args:
                    arg_type = self.type_validator.infer_expression_type(arg)
                    if arg_type is not None:
                        param_types.append(arg_type)

                # abs, min, max return the same type as their input(s)
                if function_name in {'abs', 'min', 'max'} and param_types:
                    return param_types[0]

                # sqrt, pow, floor, ceil, round, trunc always return f64
                if function_name in {'sqrt', 'pow', 'floor', 'ceil', 'round', 'trunc'}:
                    return BuiltinType.F64

            return None

        # Otherwise, check if it's a function call
        if function_name in self.type_validator.func_table.by_name:
            func_sig = self.type_validator.func_table.by_name[function_name]
            # Functions can declare explicit Result<T, E> or just T (implicit Result<T, StdError>)
            if func_sig.ret_type is not None:
                from sushi_lang.semantics.generics.types import GenericTypeRef
                from sushi_lang.semantics.type_resolution import resolve_unknown_type

                # Every shape of Result a signature can declare interns to the same EnumType,
                # which is what the call's value actually is at runtime.
                from sushi_lang.semantics.generics.results import is_result_enum

                # Already the interned enum (the signature was resolved in place): return it.
                # Wrapping it again would produce Result<Result<T, E>, StdError>.
                if is_result_enum(func_sig.ret_type):
                    return func_sig.ret_type

                # Explicit `fn foo() Result<T, E>` -- not wrapped again.
                if isinstance(func_sig.ret_type, GenericTypeRef) and func_sig.ret_type.base_name == "Result":
                    if len(func_sig.ret_type.type_args) == 2:
                        return self._intern_result(func_sig.ret_type.type_args[0],
                                                   func_sig.ret_type.type_args[1])
                    return resolve_unknown_type(
                        func_sig.ret_type,
                        self.type_validator.struct_table.by_name,
                        self.type_validator.enum_table.by_name
                    )
                elif func_sig.err_type is not None:
                    # Implicit Result with a custom error: fn foo() i32 | MyError
                    return self._intern_result(func_sig.ret_type, func_sig.err_type)
                else:
                    # Implicit Result, default error: fn foo() i32 -> Result<i32, StdError>
                    err_type = self.type_validator.enum_table.by_name.get("StdError")
                    if err_type is not None:
                        return self._intern_result(func_sig.ret_type, err_type)
                # Fallback to declared return type
                return func_sig.ret_type
        return None

    def visit_methodcall(self, node: MethodCall) -> Optional[Type]:
        """Infer method call type and annotate node with inferred return type."""
        # Check if this is actually an enum constructor (like Result.Ok())
        # In the new parsing, these are MethodCall nodes, not EnumConstructor nodes
        if isinstance(node.receiver, Name):
            enum_name = node.receiver.id
            # Check if the receiver is an enum type
            if enum_name in self.type_validator.enum_table.by_name:
                # This is an enum constructor, return the enum type
                inferred_type = self.type_validator.enum_table.by_name[enum_name]
                node.inferred_return_type = inferred_type
                return inferred_type
            elif enum_name in self.type_validator.generic_enum_table.by_name:
                # This is a generic enum constructor (like Result.Ok())
                # We can't infer the complete type without more context
                # For now, return None and let the type be inferred from context
                return None

        # Look up method return type using the registry pattern
        receiver_type = self.type_validator.infer_expression_type(node.receiver)
        from sushi_lang.semantics.typesys import StructType, EnumType, ReferenceType


        # Unwrap ReferenceType to get the underlying type
        # Methods on &T are the same as methods on T
        actual_type = receiver_type
        if isinstance(receiver_type, ReferenceType):
            actual_type = receiver_type.referenced_type

        # Handle GenericTypeRef by resolving to actual StructType
        from sushi_lang.semantics.generics.types import GenericTypeRef
        if isinstance(actual_type, GenericTypeRef):
            type_args_str = ", ".join(str(arg) for arg in actual_type.type_args)
            type_name = f"{actual_type.base_name}<{type_args_str}>"
            if type_name in self.type_validator.struct_table.by_name:
                actual_type = self.type_validator.struct_table.by_name[type_name]
            elif type_name in self.type_validator.enum_table.by_name:
                actual_type = self.type_validator.enum_table.by_name[type_name]

        if actual_type is not None and isinstance(actual_type, (BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType)):
            # Try the method type registry first (handles all built-in types)
            from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
            inferred_type = METHOD_TYPE_REGISTRY.infer_method_type(
                actual_type, node.method, self.type_validator
            )

            # Fall back to perk methods first (higher priority than extensions)
            if inferred_type is None:
                perk_method = self.type_validator.perk_impl_table.get_method(actual_type, node.method)
                if perk_method is not None and perk_method.ret is not None:
                    # Perk methods return bare types (like extension methods)
                    inferred_type = perk_method.ret

            # Fall back to extension table if registry didn't find it
            if inferred_type is None:
                method = self.type_validator.extension_table.get_method(actual_type, node.method)
                if method is not None:
                    inferred_type = method.ret_type

            # Annotate the node with the inferred type
            if inferred_type is not None:
                node.inferred_return_type = inferred_type
                return inferred_type

        return None

    def visit_dotcall(self, node: DotCall) -> Optional[Type]:
        """Infer dot-call type and annotate node with inferred return type."""
        # FFI: foreign namespace call - the raw C return type stands verbatim.
        sig = self.type_validator._resolve_external_call(node)
        if sig is not None:
            node.inferred_return_type = sig.ret_type
            return sig.ret_type

        # f64.from_bits(bits) / f32.from_bits(bits): static bit-reinterpret -> f64/f32.
        if (isinstance(node.receiver, Name) and node.receiver.id in ("f64", "f32")
                and node.method == "from_bits"):
            ty = BuiltinType.F64 if node.receiver.id == "f64" else BuiltinType.F32
            node.inferred_return_type = ty
            return ty

        # Check if receiver is an enum type name
        if isinstance(node.receiver, Name):
            receiver_name = node.receiver.id
            # Check if it's an enum type (concrete or generic)
            if receiver_name in self.type_validator.enum_table.by_name:
                # This is an enum constructor - return the enum type
                inferred_type = self.type_validator.enum_table.by_name[receiver_name]
                node.inferred_return_type = inferred_type
                return inferred_type
            elif receiver_name in self.type_validator.generic_enum_table.by_name:
                # This is a generic enum constructor (like Result.Ok())
                # We can't infer the complete type without more context
                # For now, return None and let the type be inferred from context
                return None

        # obj.handler(): indirect call through a fn-typed struct field yields
        # Result<ok, err>, exactly like a direct call (so `obj.handler(x)??` unwraps).
        fn_field_ty = resolve_fn_field_call(self.type_validator, node)
        if fn_field_ty is not None:
            node.callee_fn_type = fn_field_ty
            node.inferred_return_type = self._intern_result(fn_field_ty.ok_type,
                                                            fn_field_ty.err_type)
            return node.inferred_return_type

        # Otherwise, it's a method call - infer return type from method
        # Convert to MethodCall temporarily for type inference
        from sushi_lang.semantics.ast import MethodCall
        temp_method_call = MethodCall(
            receiver=node.receiver,
            method=node.method,
            args=node.args,
            loc=node.loc
        )
        inferred_type = self.visit_methodcall(temp_method_call)
        # Copy the inferred type to the DotCall node
        if inferred_type is not None:
            node.inferred_return_type = inferred_type
        return inferred_type

    def visit_dynamicarraynew(self, node: DynamicArrayNew) -> Optional[Type]:
        """new() constructor requires context for type inference."""
        # This should be handled by the caller that has access to LHS type
        return None

    def visit_dynamicarrayfrom(self, node: DynamicArrayFrom) -> Optional[Type]:
        """from(array_literal) can infer type from array literal elements."""
        return self.type_validator._infer_dynamic_array_from_type(node)

    def visit_castexpr(self, node: CastExpr) -> Optional[Type]:
        """Cast expression - return the target type."""
        return node.target_type

    def visit_enumconstructor(self, node: EnumConstructor) -> Optional[Type]:
        """EnumConstructor - return the enum type (including Result.Ok/Result.Err)."""
        # Check if the node has a resolved enum type (for generic enums like Result<T>)
        # This is set by the type checker during validation
        if hasattr(node, 'resolved_enum_type') and node.resolved_enum_type is not None:
            return node.resolved_enum_type

        # Otherwise, look up the concrete enum type
        if node.enum_name in self.type_validator.enum_table.by_name:
            return self.type_validator.enum_table.by_name[node.enum_name]

        return None

    def visit_tryexpr(self, node: TryExpr) -> Optional[Type]:
        """Try expression (?? operator) - unwrap result-like enum to Ok type.

        Supports any enum with Ok(T) and Err(...) variants, including:
        - Result<T> (generic)
        - FileResult (concrete enum with Ok(file) variant)
        """
        # Infer the type of the inner expression
        inner_type = self.type_validator.infer_expression_type(node.expr)

        if inner_type is None:
            return None

        # A first-class function value call yields a Result enum (not a concrete Result
        # EnumType); `??` unwraps it to its ok_type -- e.g. a captured closure called in
        # a lambda body, `f(x)??`.
        from sushi_lang.semantics.typesys import EnumType
        # Result-like (Ok(T)) or Maybe (Some(T)) enum: `??` unwraps the payload variant.
        if isinstance(inner_type, EnumType):
            for variant_name in ("Ok", "Some"):
                variant = inner_type.get_variant(variant_name)
                if variant and variant.associated_types:
                    return variant.associated_types[0]

        # Not a result-like enum - will be caught by validation
        return None

    def visit_rangeexpr(self, node: RangeExpr) -> Optional[Type]:
        """Infer type of range expression - always Iterator<i32>."""
        from sushi_lang.semantics.passes.types.inference import infer_range_expression_type
        return infer_range_expression_type(self.type_validator, node)

    def visit_borrow(self, node: Borrow) -> Optional[Type]:
        """Infer type of borrow expression (&peek expr or &poke expr).

        Returns ReferenceType with the correct mutability based on the
        borrow mode (peek or poke) specified in the node.
        """
        from sushi_lang.semantics.typesys import ReferenceType, BorrowMode

        # Get the type of the borrowed expression
        inner_type = self.type_validator.infer_expression_type(node.expr)
        if inner_type is None:
            return None

        # Create ReferenceType with the correct mutability
        mutability = BorrowMode.PEEK if node.mutability == "peek" else BorrowMode.POKE
        return ReferenceType(referenced_type=inner_type, mutability=mutability)

    def generic_visit(self, node) -> Optional[Type]:
        """Default behavior for unknown nodes."""
        return None
