"""Expression validation for the typecheck pass."""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Expr
    from sushi_lang.semantics.passes.types import TypeValidator
    from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.passes.types.calls import (
    validate_enum_constructor, validate_function_call, validate_method_call)
from sushi_lang.semantics.passes.types.expressions import (
    validate_array_literal, validate_bitwise_operation, validate_cast_expression,
    validate_index_access, validate_try_expression)
from sushi_lang.semantics.visitors import RecursiveVisitor
from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType
from sushi_lang.semantics.passes.types.visibility import (
    reject_ambiguous_name, reject_private_kept, reject_private_name)
from sushi_lang.semantics.passes.types.utils import reject_named_args
from sushi_lang.semantics.ast import (
    Name, IntLit, FloatLit, BoolLit, StringLit, InterpolatedString, ArrayLiteral, IndexAccess,
    UnaryOp, BinaryOp, Call, MethodCall, DotCall, DynamicArrayNew, DynamicArrayFrom, CastExpr, EnumConstructor, TryExpr, RangeExpr, Lambda,
    MemberAccess
)
from sushi_lang.semantics.passes.types.visit.helpers import (
    infer_lambda_type, validate_fn_field_call_args)


class ExpressionValidator(RecursiveVisitor):
    """Visitor for validating expressions using the Visitor Pattern."""

    def __init__(self, type_validator: 'TypeValidator'):
        """Initialize with reference to the main type validator."""
        self.type_validator = type_validator

    def visit_unaryop(self, node: UnaryOp) -> None:
        """Validate unary operation."""
        # CE5010: a foreign ptr is an opaque handle - no negation, NOT, or truthiness
        operand_type = self.type_validator.infer_expression_type(node.expr)
        if isinstance(operand_type, ForeignPtrType):
            er.emit(self.type_validator.reporter, er.ERR.CE5010, node.loc, op=node.op)
            return

        # A negated integer literal is range-checked as one signed value, so
        # that i32 min (-2147483648) stays legal while the positive literal
        # 2147483648 alone would not be. Only when it has no context type: a stamped
        # literal was already checked against its target, and re-checking it against
        # the i32 default made `let i64 a = -4294967303` a CE2070 -- the same shape
        # `visit_intlit` guards against for the positive half.
        if node.op == "neg" and isinstance(node.expr, IntLit):
            context_typed = (getattr(node.expr, 'in_cast_context', False)
                             or node.expr.resolved_type is not None)
            if not context_typed and -int(node.expr.value) < -(2 ** 31):
                self._emit_literal_overflow(node.expr)
            node.expr.range_checked = True

        self.type_validator.validate_expression(node.expr)

        if node.op == "~":
            from sushi_lang.semantics.passes.types.expressions import validate_bitwise_unary
            validate_bitwise_unary(self.type_validator, node)

        # The operand of `not` is a condition, so it obeys the condition rule (#532).
        if node.op == "not":
            from sushi_lang.semantics.passes.types.expressions import (
                reject_non_bool_condition)
            reject_non_bool_condition(self.type_validator, node.expr, operand_type)

        # Unary minus is arithmetic, so it takes a number like the binary half, and it
        # is overflow-checked because the smallest signed value has no positive twin.
        # `~` is width-defined and never reports.
        if node.op == "neg":
            from sushi_lang.semantics.passes.types.expressions import (
                reject_non_numeric_arithmetic, reject_overflowing_operation)
            reject_non_numeric_arithmetic(self.type_validator, "-",
                                          [(node.expr, operand_type)])
            reject_overflowing_operation(self.type_validator, node, operand_type)

    def visit_binaryop(self, node: BinaryOp) -> None:
        """Validate binary operation."""
        from sushi_lang.semantics.passes.types.expressions import (
            ARITHMETIC_OPS, COMPARISON_OPS, DIVISION_OPS, is_string_plus,
            reject_mixed_numeric_operands, reject_non_bool_condition,
            reject_non_numeric_arithmetic, reject_overflowing_operation,
            reject_uncomparable_operands, reject_zero_divisor)

        # Operand-driven literal typing: when one operand is a bare (unstamped)
        # numeric literal and the other a concrete numeric type, stamp the literal
        # to that type so `a + 1` (a: u8) is u8 + u8, not the mixed u8 + i32 below.
        # BEFORE validation, so the range check reads the sibling's type and not the
        # i32 default (#826); again after it, for a sibling only validation can type.
        self._context_type_operand_from_sibling(node, self._infer_leaving_no_trace)
        self.type_validator.validate_expression(node.left)
        self.type_validator.validate_expression(node.right)
        self._context_type_operand_from_sibling(
            node, self.type_validator.infer_expression_type)

        left_type = self.type_validator.infer_expression_type(node.left)
        right_type = self.type_validator.infer_expression_type(node.right)

        # CE5010: a foreign ptr is an opaque handle - no comparison, arithmetic,
        # bitwise, or logical operations of any kind.
        if isinstance(left_type, ForeignPtrType) or isinstance(right_type, ForeignPtrType):
            er.emit(self.type_validator.reporter, er.ERR.CE5010, node.loc, op=node.op)
            return

        if is_string_plus(node.op, left_type, right_type):
            from sushi_lang.semantics.const_eval import emit_string_plus
            emit_string_plus(self.type_validator.reporter, node.loc)

        if node.op in COMPARISON_OPS or node.op in ARITHMETIC_OPS:
            reject_mixed_numeric_operands(self.type_validator, node, left_type, right_type)

        # Arithmetic takes a number on every side, and a divisor it can read must not
        # be zero -- the rule a constant has always obeyed (#709).
        if node.op in ARITHMETIC_OPS:
            reject_non_numeric_arithmetic(self.type_validator, node.op,
                                          [(node.left, left_type), (node.right, right_type)])

        if node.op in DIVISION_OPS:
            reject_zero_divisor(self.type_validator, node, left_type)

        if node.op in COMPARISON_OPS:
            reject_uncomparable_operands(self.type_validator, node, left_type, right_type)

        if node.op in ["&", "|", "^", "<<", ">>"]:
            validate_bitwise_operation(self.type_validator, node)

        # Both operands of a logical operator are conditions (#532).
        if node.op in ["and", "or", "xor"]:
            reject_non_bool_condition(self.type_validator, node.left, left_type)
            reject_non_bool_condition(self.type_validator, node.right, right_type)

        # The overflow-checked operators. A width-defined one cannot leave its type,
        # so it is not asked (Ruling 1 of docs/design/compile-time-evaluation.md).
        if node.op in ARITHMETIC_OPS:
            reject_overflowing_operation(self.type_validator, node, left_type)

    def _infer_leaving_no_trace(self, expr) -> Optional[Type]:
        """The type of a value that is not validated yet, with no stamp and no diagnostic.

        Validation reads the stamps inference writes, so every field under the value is
        put back, as `ReadOnlyInferrer` does for the early passes (#806). A diagnostic
        goes to a Reporter nobody reads: validation reports it later, for real.
        """
        from sushi_lang.internals.report import Reporter
        from sushi_lang.semantics.passes.types import fields_restored_under
        tv = self.type_validator
        reporter, tv.reporter = tv.reporter, Reporter()
        try:
            with fields_restored_under(expr):
                return tv.infer_expression_type(expr)
        finally:
            tv.reporter = reporter

    def _context_type_operand_from_sibling(
            self, node: BinaryOp, infer: Callable[[Expr], Optional[Type]]) -> None:
        """Stamp a bare numeric-literal operand with its concrete sibling's type.

        Bareness is read THROUGH the unary operators that keep their operand's type, so
        `flags & ~0x0F` is one width rather than the mixed u8/i32 pair of CE2510 (#448).
        What gets propagated is still the operand node, not the literal under it: the
        recursion owns the negated-literal rule.
        """
        from sushi_lang.semantics.passes.types.propagation import (
            is_bare_numeric_literal, propagate_types_to_value, unwrap_type_preserving_unary)
        left, right = node.left, node.right
        left_bare = is_bare_numeric_literal(unwrap_type_preserving_unary(left))
        right_bare = is_bare_numeric_literal(unwrap_type_preserving_unary(right))
        if left_bare == right_bare:
            return
        if left_bare:
            sibling_type = infer(right)
            if isinstance(sibling_type, BuiltinType):
                propagate_types_to_value(self.type_validator, left, sibling_type)
        else:
            sibling_type = infer(left)
            if isinstance(sibling_type, BuiltinType):
                propagate_types_to_value(self.type_validator, right, sibling_type)

    def visit_lambda(self, node: Lambda) -> None:
        """Type the lambda and reject illegal captures (CE2094).

        The BODY is not walked here. A lifted lambda is a function, and the `lift` pass
        hands its body to `_validate_function` like any other -- so walking it here as
        well checked it twice and reported every fault in it twice (#629). What is left
        is what no lifted function carries: the capture list, which lift consumes into
        the environment struct, and the function TYPE the enclosing expression needs.
        """
        tv = self.type_validator
        infer_lambda_type(tv, node)  # fills param + capture types (idempotent)

        # CE2094: capturing a peek/poke borrow is deferred to Tier 2. An owning capture
        # is a move into the environment, and the borrow pass checks the outer name.
        from sushi_lang.semantics.passes.types.utils import resolve_declared_type
        from sushi_lang.semantics.typesys import ReferenceType, owns_resource
        for cap in (node.captures or []):
            if isinstance(cap.ty, ReferenceType):
                er.emit(tv.reporter, er.ERR.CE2094, node.loc,
                        reason=f"cannot capture '{cap.name}': it is a borrow (peek/poke capture is deferred to Tier 2)")

        # CE2094: an owning parameter type on a function value has no deep-copy on the
        # indirect-call path yet, so reject it. A `string` is excluded deliberately -- its
        # `owned` bit is cleared at entry, so it frees nothing, and including it would
        # silently make `|string s| ...` illegal.
        drops = tv.drop_type_names
        for p in node.params:
            if p.ty != BuiltinType.STRING and owns_resource(
                    p.ty, drops, resolve=lambda ty: resolve_declared_type(tv, ty)):
                er.emit(tv.reporter, er.ERR.CE2094, node.loc,
                        reason=f"lambda parameter '{p.name}' has an owning type '{display_type(p.ty)}'; "
                               f"owning function-value parameters are deferred to Tier 2")

    def visit_call(self, node: Call) -> None:
        """Validate function call."""
        validate_function_call(self.type_validator, node)
        reject_named_args(self.type_validator, node)

    def visit_methodcall(self, node: MethodCall) -> None:
        """Validate method call."""
        validate_method_call(self.type_validator, node)
        reject_named_args(self.type_validator, node)

    def visit_dotcall(self, node: DotCall) -> None:
        """Validate dot-call expression - resolve to enum constructor or method call."""
        self._dispatch_dotcall(node)
        reject_named_args(self.type_validator, node)

    def _dispatch_dotcall(self, node: DotCall) -> None:
        """Send `X.Y(args)` to the rules of whatever X.Y turns out to name."""
        from sushi_lang.semantics.passes.types.calls.dotcall import (
            DotCallKind, copy_callee_stamps, resolve_dotcall)

        target = resolve_dotcall(self.type_validator, node, report=True)

        if target.kind is DotCallKind.FN_FIELD:
            self.type_validator.validate_expression(node.receiver)
            validate_fn_field_call_args(self.type_validator, node, target.fn_type)
            return

        if target.kind is DotCallKind.METHOD:
            validate_method_call(self.type_validator, target.method_call)
            copy_callee_stamps(node, target.method_call)

    def visit_arrayliteral(self, node: ArrayLiteral) -> None:
        """Validate array literal."""
        validate_array_literal(self.type_validator, node)

    def visit_indexaccess(self, node: IndexAccess) -> None:
        """Validate index access."""
        validate_index_access(self.type_validator, node)

    def visit_dynamicarraynew(self, node: DynamicArrayNew) -> None:
        """new() constructor - no subexpressions to validate."""
        pass

    def visit_dynamicarrayfrom(self, node: DynamicArrayFrom) -> None:
        """from(array_literal) - validate the array literal."""
        self.type_validator.validate_expression(node.elements)

    def visit_castexpr(self, node: CastExpr) -> None:
        """Cast expression - validate the source expression and check cast validity."""
        validate_cast_expression(self.type_validator, node)

    def visit_enumconstructor(self, node: EnumConstructor) -> None:
        """Validate enum constructor call (including Result.Ok() and Result.Err())."""
        validate_enum_constructor(self.type_validator, node)

    def visit_memberaccess(self, node: MemberAccess) -> None:
        """A field read -- or the bare spelling of a payload-free variant (#545)."""
        from sushi_lang.semantics.passes.types.calls.namespaced import fold_namespaced_enum
        fold_namespaced_enum(self.type_validator, node)
        if (isinstance(node.receiver, Name)
                and self._validate_variant_spelling(node, node.member, [])):
            return
        self.visit(node.receiver)
        from sushi_lang.semantics.passes.types.expressions import reject_unknown_field
        reject_unknown_field(self.type_validator, node)

    def _validate_variant_spelling(self, node, variant_name: str, args: list) -> bool:
        """`Enum.Variant(...)` and the bare `Enum.Variant` -- the ladder's one home."""
        from sushi_lang.semantics.passes.types.calls.dotcall import (
            validate_variant_spelling)
        return validate_variant_spelling(self.type_validator, node, variant_name, args)

    def visit_tryexpr(self, node: TryExpr) -> None:
        """Validate try expression (?? operator)."""
        validate_try_expression(self.type_validator, node)

    def visit_rangeexpr(self, node: RangeExpr) -> None:
        """Validate range expression."""
        from sushi_lang.semantics.passes.types.expressions import validate_range_expression
        validate_range_expression(self.type_validator, node)

    def visit_name(self, node: Name) -> None:
        """Name expressions are terminal."""
        tv = self.type_validator
        if node.id in tv.variable_types:
            return
        const_sig = tv.const_sig(node.id)
        if const_sig is not None:
            # The one place a bare constant is validated, so the one place the fence
            # sits (D3). A local of the same name shadows it and never reaches here.
            kind = "variable" if const_sig.is_var else "constant"
            if not reject_private_name(tv, kind, const_sig, node.loc):
                reject_ambiguous_name(tv, kind, node.id, node.loc)
            return
        # A constant a binary library declares and keeps registers in no table: the
        # manifest holds the name and the kind, and that is the whole origin (#487).
        if reject_private_kept(tv, node.id, node.loc, kinds={"constant", "variable"}):
            return
        if tv.generic_sig(node.id) is not None:
            # A generic-fn reference is allowed WITH an explicit expected fn type: solve
            # the type args and rewrite to the mangled name. A bare one stays CE2093.
            from sushi_lang.semantics.passes.types.calls.generics import resolve_generic_fn_reference
            resolved = resolve_generic_fn_reference(tv, node.id, getattr(node, "expected_type", None))
            if resolved is not None:
                node.id = resolved[0]  # mirror the call-site mangled-name rewrite
                return
            er.emit(tv.reporter, er.ERR.CE2093, node.loc,
                    name=node.id, reason="generic function references are deferred (v1)")

    def visit_intlit(self, node: IntLit) -> None:
        """Range-check a bare integer literal (CE2070)."""
        if getattr(node, 'in_cast_context', False) or getattr(node, 'range_checked', False):
            return
        if node.resolved_type is not None:
            return
        value = int(node.value)
        if node.radix == 10:
            in_range = 0 <= value <= 2 ** 31 - 1
        else:
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

