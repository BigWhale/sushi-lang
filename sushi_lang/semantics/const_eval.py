"""Compile-time constant expression evaluator.

A helper and not a pass: the typecheck pass, the AST builder and the backend each call
it (`docs/design/compile-time-evaluation.md`). `evaluate` decides every `Expr` kind
through one table, `HANDLERS`, and the kinds that are not a constant are named in
`NOT_CONSTANT`; `tests/unit/test_const_eval_dispatch_is_total.py` keeps the two sets
and the `Expr` union in step.
"""
from __future__ import annotations
import math
import operator
from dataclasses import dataclass
from contextlib import contextmanager
from typing import (Callable, Iterator, List, Mapping, Optional, Tuple, TypeGuard, Union,
                    TYPE_CHECKING)


from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.ast import (
    BlankLit, Borrow, Call, DotCall, DynamicArrayFrom, DynamicArrayNew, EnumConstructor,
    Expr, IntLit, FloatLit, BoolLit, StringLit, ArrayLiteral, BinaryOp, UnaryOp, Name,
    CastExpr, IndexAccess, InterpolatedString, Lambda, MemberAccess, MethodCall, RangeExpr,
    Spread, TryExpr
)
from sushi_lang.semantics.integer_width import (
    fits_integer_type, integer_bit_width, wrap_to_integer_type)
from sushi_lang.semantics.typesys import Type, BuiltinType, StructType, EnumType
from sushi_lang.semantics.type_predicates import is_float_type, is_integer_type
from sushi_lang.semantics import array_runs
from sushi_lang.semantics.namespaces import NamespaceRef, NamespaceTable, UnitScope
from sushi_lang.semantics.passes.collect import ConstantTable
from sushi_lang.semantics.passes.collect.constants import ConstSig
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from sushi_lang.semantics.stdlib_registry import StdlibFunction

# Which unit's namespace table answers for a unit name. None for a unit the reader does
# not know, which means an unrestricted scope and no alias -- the scratch reader's view.
NamespacesOf = Callable[[Optional[str]], Optional[NamespaceTable]]

# The exact arithmetic of an overflow-checked operator. Unary minus is the fourth one
# and is applied where it is read, because it has no right operand.
_ARITHMETIC: Mapping[str, Callable[[object, object], object]] = {
    "+": operator.add, "-": operator.sub, "*": operator.mul,
}

# A width-defined operator: every bit of the result the width holds is kept, and the
# rest are lost. None of these can leave its type, so none of them reports.
_BITWISE: Mapping[str, Callable[[int, int], int]] = {
    "&": operator.and_, "|": operator.or_, "^": operator.xor,
}

_LOGICAL: Mapping[str, Callable[[bool, bool], bool]] = {
    "and": lambda left, right: left and right,
    "or": lambda left, right: left or right,
    "xor": operator.ne,
}

_COMPARISON: Mapping[str, Callable[[object, object], bool]] = {
    "==": operator.eq, "!=": operator.ne,
    "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}
_ORDERINGS = ("<", "<=", ">", ">=")

# The expression kinds that are never a constant. Each answers CE0108 through the one
# backstop in `evaluate`; naming them here is what lets the gate tell a decision from a
# fall-through. A `MethodCall` and an `EnumConstructor` never arrive from the parser in
# an initializer (both are spelled as a `DotCall` there) and a `Spread` is an argument,
# but the union holds them, so the table says what they would be.
NOT_CONSTANT: frozenset[type] = frozenset({
    MethodCall, EnumConstructor, DynamicArrayNew, DynamicArrayFrom, Borrow, TryExpr,
    RangeExpr, Spread, Lambda, BlankLit,
})


@dataclass
class ConstOverflow:
    """An operation whose result its type cannot hold.

    The node is kept so a caller that reads an expression with a silent reporter can
    tell its own overflow from one inside a constant it named: only the node that
    computed the value reports it (CE2077).
    """
    node: Expr
    op: str
    value: int
    semantic_type: Type
    span: Optional[Span]


def emit_overflow(reporter: Reporter, overflow: ConstOverflow) -> None:
    """Raise a recorded overflow as CE2077, whoever read the expression."""
    er.emit_with(reporter, er.ERR.CE2077, overflow.span, op=overflow.op,
                 value=overflow.value, type=display_type(overflow.semantic_type)) \
        .help("use a wider type, or compute in one and cast the result with 'as'").emit()


def emit_string_plus(reporter: Reporter, span: Optional[Span]) -> None:
    """CE2509 with its help, for a constant and for a body alike.

    Sushi has no concatenation operator anywhere, so a constant reports the language
    rule and not a constant-only one (#441), and it renders the rule as the body's
    validator does (#682).
    """
    er.emit_with(reporter, er.ERR.CE2509, span) \
        .help("use string interpolation: \"{a}{b}\"").emit()


@dataclass
class ScalarConstant:
    """One compile-time value with its type: a number, a bool or a string."""
    value: Union[int, float, bool, str]
    semantic_type: Type  # Sushi type (i32, f64, bool, string)


@dataclass
class AggregateConstant:
    """Compile-time elements with their type, and `semantic_type` says which kind.

    A `StructType` makes them the fields in declaration order, an `EnumType` makes them
    the payload of `variant` in declaration order (empty for a payload-free variant),
    and anything else makes them array elements. Every reader of an aggregate asks the
    type first, never the shape of the list.
    """
    elements: List['ConstantValue']
    semantic_type: Type  # a struct, an enum, a fixed array
    variant: Optional[str] = None  # the variant an EnumType value constructs


ConstantValue = Union[ScalarConstant, AggregateConstant]


def is_integer_constant(value: ConstantValue) -> TypeGuard[ScalarConstant]:
    """A scalar of an integer type."""
    return isinstance(value, ScalarConstant) and is_integer_type(value.semantic_type)


def is_numeric_constant(value: ConstantValue) -> TypeGuard[ScalarConstant]:
    """A scalar of an integer or a float type."""
    return isinstance(value, ScalarConstant) and (
        is_integer_type(value.semantic_type) or is_float_type(value.semantic_type))


def is_bool_constant(value: ConstantValue) -> TypeGuard[ScalarConstant]:
    return isinstance(value, ScalarConstant) and value.semantic_type == BuiltinType.BOOL


def is_string_constant(value: ConstantValue) -> TypeGuard[ScalarConstant]:
    return isinstance(value, ScalarConstant) and value.semantic_type == BuiltinType.STRING


def allocates_nothing(expr: Expr) -> bool:
    """Is `expr` an EMPTY container constructor -- `List.new()`, `from([])`, `new()`?

    Each one is the literal descriptor `{0, 0, null}` and touches no heap, which is what
    lets a unit variable start as one (docs/design/unit-storage.md). `HashMap.new()` is
    not here: it mallocs its buckets on the spot. ONE predicate, read by the typecheck
    pass and the backend alike, so the two cannot disagree about what qualifies.
    """
    if isinstance(expr, DynamicArrayNew):
        return True
    if isinstance(expr, DynamicArrayFrom):
        return not expr.elements.elements
    return (isinstance(expr, DotCall) and expr.method == "new" and not expr.args
            and isinstance(expr.receiver, Name) and expr.receiver.id == "List")


class ConstantEvaluator:
    """Compile-time constant expression evaluator."""

    def __init__(self, reporter: Reporter, const_table: ConstantTable,
                 unit_name: Optional[str] = None,
                 namespaces_of: Optional[NamespacesOf] = None,
                 struct_table: object = None, enum_table: object = None, *,
                 reported_cycles: Optional[
                     set[frozenset[Tuple[Optional[str], str]]]] = None):
        """Initialize the evaluator.

        `unit_name` is the unit whose constant expression is being evaluated. Two units
        may each declare a private `SCRATCH`, so a name is only an answer once the asking
        unit is known (`docs/design/unit-namespaces.md` section 9). `namespaces_of` is
        the rest of that answer, for EVERY unit and not only this one: what a unit may
        write bare (its scope, section 6) and behind a dot (its aliases, section 5). A
        foreign constant's initializer is read in the scope of the unit that WROTE it,
        so folding one switches unit (#561); a reader that passes None sees every name
        and no alias, which is the scratch reader's view.

        `reported_cycles` is the store CE0109 dedupes against. The table's own store is
        the default, because it is what every evaluator of one compilation shares; a
        SILENT twin takes a store of its own, so a cycle it swallows is still reported to
        the reader that speaks (#710).
        """
        self.reporter = reporter
        self.const_table = const_table
        self.namespaces_of = namespaces_of
        self.unit_name = unit_name
        self.namespaces = self._namespaces_for(unit_name)
        # The authorities for a struct's field ORDER and field TYPES, and for an enum's
        # variants and their payload types. A caller that reads only an integer -- an
        # array size, a run count -- passes neither, and a construction stays CE0108
        # there, which is what those positions want anyway.
        self.struct_table = struct_table
        self.enum_table = enum_table
        # For cycle detection. Keyed by DECLARATION, not by name: this unit's SIZE may be
        # built from another unit's SIZE, and that is two constants and not a cycle.
        self.evaluation_stack: List[Tuple[Optional[str], str]] = []
        self.reported_cycles = (const_table.reported_cycles if reported_cycles is None
                                else reported_cycles)
        # The FIRST operation that left its type, for a caller whose reporter is silent.
        self.overflow: Optional[ConstOverflow] = None

    @property
    def scope(self) -> UnitScope:
        """What the unit being read may write bare (section 6)."""
        return (self.namespaces.scope if self.namespaces is not None
                else UnitScope.unrestricted())

    def silent(self) -> "ConstantEvaluator":
        """A twin that reports nothing, for a reader that only wants the answer.

        Its cycle store is its own: what it swallows must still reach the reader that
        speaks, and this twin reads an expression the caller reads again (#710).
        """
        return ConstantEvaluator(Reporter(), self.const_table, self.unit_name,
                                 self.namespaces_of, self.struct_table, self.enum_table,
                                 reported_cycles=set())

    def _namespaces_for(self, unit_name: Optional[str]) -> Optional[NamespaceTable]:
        return None if self.namespaces_of is None else self.namespaces_of(unit_name)

    @contextmanager
    def _in_unit(self, unit_name: Optional[str]) -> Iterator[None]:
        """Read as `unit_name` would: its scope and its aliases, for one initializer."""
        saved = (self.unit_name, self.namespaces)
        self.unit_name = unit_name
        self.namespaces = self._namespaces_for(unit_name)
        try:
            yield
        finally:
            self.unit_name, self.namespaces = saved

    def evaluate(self, expr: Expr, expected_type: Type, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate an expression to a compile-time constant.

        A diagnostic is reported at the NODE that failed: `span` is only the fallback
        for a node that carries no location of its own. The caller of a `const` passes
        the declaration's span, and a handler that reported with it put the caret
        under the whole line (#682).

        The kind decides the handler, and a kind with none -- one of `NOT_CONSTANT`,
        or one the table has never met -- is CE0108, the one backstop.
        """
        span = expr.loc or span
        handler = self.HANDLERS.get(type(expr))
        if handler is None:
            er.emit(self.reporter, er.ERR.CE0108, span, expr_type=type(expr).__name__)
            return None
        return handler(self, expr, expected_type, span)

    def _evaluate_member_access(self, expr: MemberAccess, expected_type: Type,
                                span: Optional[Span]) -> Optional[ConstantValue]:
        """`Enum.Variant`, or a constant behind an alias: `sh.SIZE`, `m.PI`.

        The qualifier is read first, the way every written-name position reads it
        (`unit-namespaces.md` section 5): a leading alias on the receiver folds away
        and leaves the bare variant, and an alias on the whole name is a constant of the
        unit or module it names. A member that is not a constant -- a type, a function
        written without its parentheses -- is no value; the body's validator owns that
        diagnostic, so this answers None and says nothing.
        """
        self._fold_qualified_enum(expr)
        ref = self._qualified(expr)
        if ref is None:
            return self._evaluate_enum_construction(expr, expected_type, span)
        if ref.kind == "constant":
            return self._evaluate_constant_ref(ref, span)
        return None

    def _evaluate_dot_call(self, expr: DotCall, expected_type: Type,
                           span: Optional[Span]) -> Optional[ConstantValue]:
        """`Enum.Variant(...)`, or a struct constructor behind an alias: `sh.Point(0, 0)`.

        A struct behind the dot is read through a stand-in `Call`, the device the
        typecheck pass uses for the same node, so the construction is measured by the
        bare form's rules. Anything else the alias reaches with parentheses is a call,
        and a call is not a constant: CE0108, the code the bare `f()` answers.
        """
        self._fold_qualified_enum(expr)
        ref = self._qualified(expr)
        if ref is None:
            return self._evaluate_enum_construction(expr, expected_type, span)
        if ref.kind == "struct":
            stand_in = Call(callee=Name(id=ref.name, loc=expr.loc), args=expr.args,
                            field_names=expr.field_names, loc=expr.loc)
            return self._evaluate_struct_construction(stand_in, expected_type, span)
        er.emit(self.reporter, er.ERR.CE0108, span, expr_type=type(expr).__name__)
        return None

    def _qualified(self, node: Union[DotCall, MemberAccess]) -> Optional[NamespaceRef]:
        """What `alias.name` denotes when the receiver names a namespace, else None.

        The stamp answers first: the typecheck pass leaves one on every qualified node
        it resolved, and the back end's evaluator has no table and reads that alone.
        Without one, the unit's own namespace table answers and the node is stamped for
        every reader after this one. A constant initializer binds no local, so nothing
        can shadow the alias here.
        """
        ref = getattr(node, "namespace_ref", None)
        if ref is not None:
            return ref
        receiver = node.receiver
        if (self.namespaces is None or not isinstance(receiver, Name)
                or not self.namespaces.is_namespace(receiver.id)):
            return None
        name = node.member if isinstance(node, MemberAccess) else node.method
        binding = self.namespaces.lookup(receiver.id, name)
        if binding is None:
            return None
        node.namespace_ref = binding.ref()
        return node.namespace_ref

    def _fold_qualified_enum(self, node: Union[DotCall, MemberAccess]) -> None:
        """`sh.Shape.Circle(2)` becomes `Shape.Circle(2)`, in place.

        The fold the typecheck pass applies in a body (`calls/namespaced.py`): a type is
        one per program, so once the alias is gone the bare name is the whole address
        and every reader after this one -- the body's validator, the back end -- sees
        the bare form it already knows.
        """
        inner = node.receiver
        if not isinstance(inner, MemberAccess):
            return
        ref = self._qualified(inner)
        if ref is not None and ref.kind == "enum":
            node.receiver = Name(id=ref.name, loc=inner.loc)

    def _evaluate_constant_ref(self, ref: NamespaceRef,
                               span: Optional[Span]) -> Optional[ConstantValue]:
        """The constant an alias reaches: a registry module's, or a unit's own."""
        if ref.producer == "stdlib":
            from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
            module = get_stdlib_registry().get_module(ref.origin)
            record = module.constants.get(ref.name) if module is not None else None
            return None if record is None else self._stdlib_value(record)
        sig = self.const_table.lookup(ref.name, ref.origin)
        return None if sig is None else self._fold_constant(sig, span)

    def _stdlib_value(self, record: 'StdlibFunction') -> ScalarConstant:
        """A registry constant folds from the value its record carries (#560)."""
        return ScalarConstant(record.value, record.get_return_type())

    def _evaluate_struct_construction(self, expr: Call, expected_type: Type,
                                     span: Optional[Span]) -> Optional[ConstantValue]:
        """A struct built entirely from constants is itself a constant.

        Only a name the struct table knows reaches the aggregate path, so an ordinary
        function call is still CE0108 -- flat, and one level down inside a field
        argument, because each argument goes back through `evaluate`. The declared type
        is consulted for ONE thing, which instance a generic name means; it is never
        consulted for the fields: a named type is looked up and never rebuilt
        (`docs/design/type-identity.md`), and the table is the sole authority for the
        field order and the field types a literal argument is typed against.
        """
        struct_type = self._named_type(expr.callee, expected_type, self.struct_table,
                                       StructType)
        arguments = None if struct_type is None else self._arguments_in_field_order(
            expr, struct_type.fields)
        if arguments is None:
            er.emit(self.reporter, er.ERR.CE0108, span, expr_type=type(expr).__name__)
            return None

        field_values: List[ConstantValue] = []
        for (_field_name, field_type), argument in zip(struct_type.fields, arguments,
                                                       strict=True):
            value = self.evaluate(argument, field_type, span)
            if value is None:
                return None  # the recursion reported it
            field_values.append(value)
        return AggregateConstant(field_values, struct_type)

    def _evaluate_enum_construction(self, expr: Union[DotCall, MemberAccess],
                                    expected_type: Type,
                                    span: Optional[Span]) -> Optional[ConstantValue]:
        """A variant built entirely from constants is itself a constant (#551).

        Both spellings arrive: `Sign.Plus(...)` parses as a `DotCall` on the type NAME
        and the bare `Sign.Plus` as a `MemberAccess` on it. Only a name the enum table
        knows starts the variant path, so a method call or a field read on anything
        else stays CE0108. A GENERIC enum is found through the DECLARED type:
        `Maybe.None` names no table entry, and the interned `Maybe<i32>` the declaration
        resolved to is the one whose tag and payload types apply.

        A variant the enum does not declare, or a payload count that does not fit, is
        the typecheck pass's to name (CE2045, CE2050): this answers None in SILENCE, and
        `validate_constant` runs the body's validator when nothing was reported.
        """
        enum_type = self._named_type(expr.receiver, expected_type, self.enum_table,
                                     EnumType)
        if enum_type is None:
            er.emit(self.reporter, er.ERR.CE0108, span, expr_type=type(expr).__name__)
            return None

        if isinstance(expr, DotCall):
            variant_name, arguments = expr.method, list(expr.args)
        else:
            variant_name, arguments = expr.member, []
        variant = enum_type.get_variant(variant_name)
        if variant is None or len(arguments) != len(variant.associated_types):
            return None

        payload: List[ConstantValue] = []
        for argument, field_type in zip(arguments, variant.associated_types, strict=True):
            value = self.evaluate(argument, field_type, span)
            if value is None:
                return None  # the recursion reported it
            payload.append(value)
        return AggregateConstant(payload, enum_type, variant=variant_name)

    def _named_type(self, receiver: Expr, expected_type: Type, table: object, kind: type):
        """The `StructType` or `EnumType` a constructor's receiver names, or None.

        A plain type is its table entry. A generic one has no entry under its bare
        name -- the table holds the interned instances -- so the DECLARED type answers
        when its base is the name written: `const Maybe@(i32) X = Maybe.Some(7)` builds
        a `Maybe<i32>`. A qualified receiver (`geo.Vec(...)`, `geo.Sign.Plus`) never
        arrives here: the alias is folded away before this is asked (#561).
        """
        if table is None or not isinstance(receiver, Name):
            return None
        plain = table.by_name.get(receiver.id)
        if isinstance(plain, kind):
            return plain
        if isinstance(expected_type, kind) and expected_type.generic_base == receiver.id:
            return expected_type
        return None

    def _arguments_in_field_order(self, expr: Call, fields) -> Optional[List[Expr]]:
        """The call's arguments in DECLARATION order, or None if they do not fit.

        Named and positional construction are all-or-nothing and the parser keeps them
        apart, so this only has to put a complete named set back in order. A count or a
        name that does not fit is left to answer CE0108: the value is not a constant,
        which is true, and the construction itself is reported where every other
        constructor is.
        """
        if expr.field_names is None:
            return list(expr.args) if len(expr.args) == len(fields) else None

        if len(expr.field_names) != len(expr.args):
            return None
        by_name = dict(zip(expr.field_names, expr.args, strict=True))
        if set(by_name) != {name for name, _ty in fields} or len(by_name) != len(fields):
            return None
        return [by_name[name] for name, _ty in fields]

    def _evaluate_int_lit(self, expr: IntLit, expected_type: Type,
                          span: Optional[Span]) -> ScalarConstant:
        """Evaluate integer literal with type inference."""
        if is_integer_type(expected_type):
            return ScalarConstant(expr.value, expected_type)
        return ScalarConstant(expr.value, BuiltinType.I32)

    def _evaluate_float_lit(self, expr: FloatLit, expected_type: Type,
                            span: Optional[Span]) -> ScalarConstant:
        """Evaluate float literal with type inference."""
        if is_float_type(expected_type):
            return ScalarConstant(expr.value, expected_type)
        return ScalarConstant(expr.value, BuiltinType.F64)

    def _evaluate_bool_lit(self, expr: BoolLit, expected_type: Type,
                           span: Optional[Span]) -> ScalarConstant:
        return ScalarConstant(expr.value, BuiltinType.BOOL)

    def _evaluate_string_lit(self, expr: StringLit, expected_type: Type,
                             span: Optional[Span]) -> ScalarConstant:
        return ScalarConstant(expr.value, BuiltinType.STRING)

    def _format_hole(self, value: ConstantValue,
                     span: Optional[Span]) -> Optional[str]:
        """One hole, rendered as the run-time formatter prints it.

        The runtime prints an integer with printf %d/%u at its width, a float
        with %g, and a bool as true/false (#514). The evaluator must not
        drift, or a constant would print differently than the same
        expression written in a body. An integer needs no truncation here:
        every constant operation is range-checked (CE2070/CE2077), so the
        held value is the printed value.
        """
        if isinstance(value, ScalarConstant):
            t = value.semantic_type
            if t == BuiltinType.STRING:
                return str(value.value)
            if t == BuiltinType.BOOL:
                return "true" if value.value else "false"
            if t == BuiltinType.F32:
                import struct
                return "%g" % struct.unpack("f", struct.pack("f", value.value))[0]
            if t == BuiltinType.F64:
                return "%g" % value.value
            if is_integer_type(t):
                return str(value.value)
        er.emit(self.reporter, er.ERR.CE0108, span,
                expr_type=f"interpolation of {display_type(value.semantic_type)}")
        return None

    def _evaluate_interpolation(self, expr: InterpolatedString, expected_type: Type,
                                span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate an interpolated string constant (#447).

        A hole is a constant expression like any other; it carries no declared
        type, so a bare literal in one takes the same default it takes in a
        body.
        """
        rendered: List[str] = []
        for part in expr.parts:
            if isinstance(part, str):
                rendered.append(part)
                continue
            part_span = getattr(part, "loc", None) or span
            value = self.evaluate(part, BuiltinType.STRING, span)
            if value is None:
                return None
            text = self._format_hole(value, part_span)
            if text is None:
                return None
            rendered.append(text)
        return ScalarConstant("".join(rendered), BuiltinType.STRING)

    def _evaluate_binary_op(self, expr: BinaryOp, expected_type: Type, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate binary operation."""
        left_val = self.evaluate(expr.left, expected_type, span)
        right_val = self.evaluate(expr.right, expected_type, span)

        if left_val is None or right_val is None:
            return None

        if expr.op == '+' and BuiltinType.STRING in (left_val.semantic_type,
                                                     right_val.semantic_type):
            emit_string_plus(self.reporter, span)
            return None

        if expr.op in _ARITHMETIC:
            return self._eval_arithmetic(expr, left_val, right_val, span)
        elif expr.op == '/':
            return self._eval_division(expr, left_val, right_val, span)
        elif expr.op == '%':
            return self._eval_modulo(expr, left_val, right_val, span)

        elif expr.op in _BITWISE:
            return self._eval_bitwise(left_val, right_val, expr.op, span)
        elif expr.op == '<<':
            return self._eval_shift_left(left_val, right_val, span)
        elif expr.op == '>>':
            return self._eval_shift_right(left_val, right_val, span)

        elif expr.op in _LOGICAL:
            return self._eval_logical(left_val, right_val, expr.op, span)

        elif expr.op in _COMPARISON:
            return self._eval_comparison(left_val, right_val, expr.op, span)

        else:
            er.emit(self.reporter, er.ERR.CE0110, span, op=expr.op)
            return None

    def _evaluate_unary_op(self, expr: UnaryOp, expected_type: Type, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate unary operation."""
        operand = self.evaluate(expr.expr, expected_type, span)
        if operand is None:
            return None

        if expr.op == 'neg':
            if is_numeric_constant(operand):
                # A negated literal is ONE leaf, and the range of a leaf is CE2073's
                # question -- that is what makes -128 an i8 while 128 is not.
                if isinstance(expr.expr, (IntLit, FloatLit)):
                    return ScalarConstant(-operand.value, operand.semantic_type)
                return self._checked(expr, '-', -operand.value,
                                     operand.semantic_type, span)
            else:
                er.emit(self.reporter, er.ERR.CE0110, span, op='negation on non-numeric type')
                return None

        elif expr.op == '~':
            if is_integer_constant(operand):
                result = wrap_to_integer_type(~operand.value, operand.semantic_type)
                return ScalarConstant(result, operand.semantic_type)
            else:
                er.emit(self.reporter, er.ERR.CE0110, span, op='bitwise NOT on non-integer type')
                return None

        elif expr.op == 'not':
            if is_bool_constant(operand):
                return ScalarConstant(not operand.value, BuiltinType.BOOL)
            else:
                er.emit(self.reporter, er.ERR.CE0110, span, op='logical NOT on non-boolean type')
                return None

        else:
            er.emit(self.reporter, er.ERR.CE0110, span, op=expr.op)
            return None

    def _evaluate_array_literal(self, expr: ArrayLiteral, expected_type: Type, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate array literal with constant elements."""
        from sushi_lang.semantics.typesys import ArrayType

        element_type = None
        if isinstance(expected_type, ArrayType):
            element_type = expected_type.base_type

        # A run is evaluated ONCE and its result repeated. This is the speaker for CE2017
        # on a constant: `validate_constant` returns on the error, so the typecheck pass
        # never reaches the same literal twice.
        runs = array_runs.read_runs(
            expr.elements,
            array_runs.const_int_reader(self.silent()),
            self.reporter)
        if runs is None:
            return None

        # A constant's evaluator needs the VALUES and not only the count, so a bound or a
        # count it cannot read is CE2019 or CE2017 here (Ruling 3, #478).
        if array_runs.require_readable_length(runs, self.reporter) is None:
            return None

        element_values: List[ConstantValue] = []
        for run in runs:
            if run.plan is not None:
                # A readable range expands to literals, so the table lands in .rodata with
                # no arithmetic behind it.
                element_values.extend(
                    ScalarConstant(value, element_type) for value in run.plan.values())
                continue
            run_val = self.evaluate(run.value, element_type, span)
            if run_val is None:
                return None  # Non-constant element
            element_values.extend([run_val] * run.count)

        if not element_values:
            er.emit(self.reporter, er.ERR.CE0108, span, expr_type='empty array')
            return None

        return AggregateConstant(element_values, expected_type)

    def _evaluate_name(self, expr: Name, expected_type: Type,
                       span: Optional[Span]) -> Optional[ConstantValue]:
        """A bare name: a constant this unit may write, else a stdlib constant in scope."""
        const_name = expr.id

        const_sig = self.const_table.lookup(const_name, self.unit_name, self.scope)
        if const_sig is None:
            # Below every declared constant: a stdlib constant a flat `use <module>`
            # brought is a constant too, and it folds from the value its record
            # carries (#560).
            from sushi_lang.semantics.stdlib_registry import lookup_stdlib_constant
            stdlib_const = lookup_stdlib_constant(const_name, self.scope)
            if stdlib_const is not None:
                return self._stdlib_value(stdlib_const)
            # Silent. The scope pass walks every initializer and owns "what kind of name
            # is this": a name that reaches nothing is CE1001 and a TYPE name is CE2105,
            # both at this same token. The CE1002 that stood here was the second
            # diagnostic for one fault, and it read "assignment to" about a READ (#710).
            # A name the scope pass lets through -- a function -- is a value of the wrong
            # type, and the assignment rule below says so.
            return None
        return self._fold_constant(const_sig, span)

    def _fold_constant(self, sig: ConstSig, span: Optional[Span]) -> Optional[ConstantValue]:
        """The value of a declared constant, read as the unit that declared it.

        The initializer is folded in the DECLARING unit's scope and with its aliases: a
        name inside it means what it meant where it was written, so this unit's own
        `WIDTH` never stands in for one the other unit imported (#561).
        """
        if sig.is_var:
            # Storage has a run-time value, so neither a constant nor another variable
            # can fold it in -- and no initialization order exists to say otherwise.
            er.emit(self.reporter, er.ERR.CE0108, span,
                    expr_type=f"unit variable '{sig.name}'")
            return None

        key = (sig.unit_name, sig.name)
        cached = self.const_table.folded.get(key)
        if cached is not None:
            return cached

        if key in self.evaluation_stack:
            self._report_cycle(self.evaluation_stack[self.evaluation_stack.index(key):],
                               key, span)
            return None

        if sig.decl is None:
            er.emit(self.reporter, er.ERR.CE1002, span, name=sig.name)
            return None

        said = len(self.reporter.items)
        self.evaluation_stack.append(key)
        with self._in_unit(sig.unit_name):
            result = self.evaluate(sig.decl.value, sig.const_type, sig.loc)
        self.evaluation_stack.pop()

        # Only a CLEAN fold is kept. A fold that had something to say is the one thing a
        # cache would silence, and every failing path here answers None in any case, so
        # the two conditions cost nothing and the diagnostics stay exactly as they were.
        if result is not None and len(self.reporter.items) == said:
            self.const_table.folded[key] = result
        return result

    def _report_cycle(self, cycle: List[Tuple[Optional[str], str]],
                      key: Tuple[Optional[str], str], span: Optional[Span]) -> None:
        """CE0109 for one cycle, once (#710).

        The typecheck pass validates every constant in turn, so each member of a cycle is
        an entry point and each walk meets the same loop from its own side. The set of
        declarations IS the cycle, which is how `finite_types` tells one type cycle from
        the next.
        """
        members = frozenset(cycle)
        if members in self.reported_cycles:
            return
        self.reported_cycles.add(members)
        chain = " -> ".join(name for _unit, name in cycle + [key])
        er.emit(self.reporter, er.ERR.CE0109, span, chain=chain)

    def _evaluate_cast(self, expr: CastExpr, expected_type: Type,
                       span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate type cast."""
        value = self.evaluate(expr.expr, expr.target_type, span)
        if value is None:
            return None

        from_type = value.semantic_type
        to_type = expr.target_type

        # A cast asks for the bit pattern, so it truncates and never reports: it is the
        # escape from the overflow rule and cannot be subject to it.
        if is_integer_constant(value) and is_integer_type(to_type):
            return ScalarConstant(wrap_to_integer_type(value.value, to_type), to_type)

        elif is_integer_constant(value) and is_float_type(to_type):
            return ScalarConstant(float(value.value), to_type)

        elif isinstance(value, ScalarConstant) and is_float_type(from_type) \
                and is_integer_type(to_type):
            return ScalarConstant(wrap_to_integer_type(int(value.value), to_type), to_type)

        elif is_integer_constant(value) and to_type == BuiltinType.BOOL:
            return ScalarConstant(value.value != 0, BuiltinType.BOOL)

        elif is_bool_constant(value) and is_integer_type(to_type):
            return ScalarConstant(1 if value.value else 0, to_type)

        elif isinstance(value, ScalarConstant) and is_float_type(from_type) \
                and is_float_type(to_type):
            return ScalarConstant(value.value, to_type)

        else:
            er.emit(self.reporter, er.ERR.CE0111, span, from_type=display_type(from_type), to_type=display_type(to_type))
            return None

    def _evaluate_index(self, expr: IndexAccess, expected_type: Type,
                        span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate an index into an array constant.

        A constant cannot trap, so the bounds a body leaves to run time (RE2020) are
        compile-time diagnostics here, the same codes a constant index in a body gets.
        """
        base = self.evaluate(expr.array, None, span)
        if base is None:
            return None

        if not isinstance(base, AggregateConstant):
            er.emit(self.reporter, er.ERR.CE0110, span, op='index of a non-array constant')
            return None

        index = self.evaluate(expr.index, BuiltinType.I32, span)
        if index is None:
            return None

        if not is_integer_constant(index) or isinstance(index.value, bool) \
                or not isinstance(index.value, int):
            er.emit(self.reporter, er.ERR.CE0110, span, op='array index that is not an integer')
            return None

        if index.value < 0:
            er.emit(self.reporter, er.ERR.CE2056, expr.index.loc, index=index.value)
            return None

        if index.value >= len(base.elements):
            er.emit(self.reporter, er.ERR.CE2012, expr.index.loc,
                    index=index.value, size=len(base.elements))
            return None

        return base.elements[index.value]

    def _checked(self, node: Expr, op: str, value: Union[int, float], semantic_type: Type,
                 span: Optional[Span]) -> Optional[ConstantValue]:
        """The result of an overflow-checked operation, or CE2077 when it left its type.

        A float has no width to leave, and a value the operands already made a lie --
        a mixed pair, which CE2510 owns -- is not this diagnostic's to report.
        """
        if (is_integer_type(semantic_type)
                and isinstance(value, int) and not isinstance(value, bool)
                and not fits_integer_type(value, semantic_type)):
            record = ConstOverflow(node=node, op=op, value=value,
                                   semantic_type=semantic_type, span=span)
            if self.overflow is None:
                self.overflow = record
            emit_overflow(self.reporter, record)
            return None

        return ScalarConstant(value, semantic_type)

    def _eval_arithmetic(self, node: BinaryOp, left: ConstantValue, right: ConstantValue,
                         span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate arithmetic operation."""
        if not is_numeric_constant(left) or not is_numeric_constant(right):
            er.emit(self.reporter, er.ERR.CE0110, span, op='arithmetic on non-numeric type')
            return None

        result = _ARITHMETIC[node.op](left.value, right.value)
        return self._checked(node, node.op, result, left.semantic_type, span)  # type: ignore[arg-type]

    def _eval_division(self, node: BinaryOp, left: ConstantValue, right: ConstantValue,
                       span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate division with zero check."""
        if not is_numeric_constant(left) or not is_numeric_constant(right):
            er.emit(self.reporter, er.ERR.CE0110, span, op='division on non-numeric type')
            return None

        if right.value == 0:
            er.emit(self.reporter, er.ERR.CE0112, span)
            return None

        result: Union[int, float]
        if is_integer_constant(left):
            result = _truncated_quotient(left.value, right.value)  # type: ignore[arg-type]
        else:
            result = left.value / right.value  # type: ignore[operator]

        return self._checked(node, node.op, result, left.semantic_type, span)

    def _eval_modulo(self, node: BinaryOp, left: ConstantValue, right: ConstantValue,
                     span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate modulo with zero check."""
        if not is_numeric_constant(left) or not is_numeric_constant(right):
            er.emit(self.reporter, er.ERR.CE0110, span, op='modulo on non-numeric type')
            return None

        if right.value == 0:
            er.emit(self.reporter, er.ERR.CE0112, span)
            return None

        result: Union[int, float]
        if is_integer_constant(left):
            # The machine divides first, so the remainder overflows exactly where the
            # quotient does: the smallest signed value with -1. LLVM calls that srem
            # undefined and x86 traps on it, so the quotient is what is checked here
            # (compile-time-evaluation.md, Ruling 1).
            quotient = _truncated_quotient(left.value, right.value)  # type: ignore[arg-type]
            if not fits_integer_type(quotient, left.semantic_type):
                return self._checked(node, node.op, quotient, left.semantic_type, span)
            result = _truncated_remainder(left.value, right.value)  # type: ignore[arg-type]
        else:
            result = math.fmod(left.value, right.value)  # type: ignore[arg-type]

        return self._checked(node, node.op, result, left.semantic_type, span)

    def _eval_bitwise(self, left: ConstantValue, right: ConstantValue, op: str, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate bitwise operation."""
        if not is_integer_constant(left) or not is_integer_constant(right):
            er.emit(self.reporter, er.ERR.CE0110, span, op=f'bitwise {op} on non-integer type')
            return None

        result = _BITWISE[op](left.value, right.value)  # type: ignore[arg-type]
        return ScalarConstant(wrap_to_integer_type(result, left.semantic_type),
                              left.semantic_type)

    def _eval_shift_left(self, left: ConstantValue, right: ConstantValue, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate left shift."""
        shift = self._shift_count(left, right, span)
        if shift is None:
            return None
        value, count, width = shift

        result = 0 if count >= width else value << count
        return ScalarConstant(wrap_to_integer_type(result, left.semantic_type),
                              left.semantic_type)

    def _eval_shift_right(self, left: ConstantValue, right: ConstantValue, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate right shift (arithmetic for signed, logical for unsigned)."""
        shift = self._shift_count(left, right, span)
        if shift is None:
            return None
        value, count, width = shift

        # A held value is in its own range, so Python's shift IS the machine's: it fills
        # from the sign bit of a negative value, and a value of an unsigned type has none.
        return ScalarConstant(value >> min(count, width), left.semantic_type)

    def _shift_count(self, left: ConstantValue, right: ConstantValue,
                     span: Optional[Span]) -> Optional[Tuple[int, int, int]]:
        """The shifted value, the count and the width it moves bits in, or None with the
        reason reported.

        A count past the width is defined and not checked (Go's rule, and CE2512 covers
        the one a body writes), so the width is handed back to clamp with: a Python shift
        by a count of millions builds the number it names.
        """
        if not is_integer_constant(left) or not is_integer_constant(right):
            er.emit(self.reporter, er.ERR.CE0110, span, op='shift on non-integer type')
            return None

        if right.value < 0:  # type: ignore[operator]
            er.emit(self.reporter, er.ERR.CE0110, span, op='shift by negative amount')
            return None

        width = integer_bit_width(left.semantic_type)
        assert width is not None
        return int(left.value), int(right.value), width

    def _eval_logical(self, left: ConstantValue, right: ConstantValue, op: str, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate logical operation (and, or, xor)."""
        if not is_bool_constant(left) or not is_bool_constant(right):
            er.emit(self.reporter, er.ERR.CE0110, span, op=f'logical {op} on non-boolean type')
            return None

        return ScalarConstant(_LOGICAL[op](bool(left.value), bool(right.value)),
                              BuiltinType.BOOL)

    def _eval_comparison(self, left: ConstantValue, right: ConstantValue, op: str, span: Optional[Span]) -> Optional[ConstantValue]:
        """Evaluate comparison operation.

        A bool is equatable but not orderable, and a string is both, which is exactly
        what a body gets (#449). The typecheck pass owns that rule for a body; this
        evaluator has to agree with it, or a constant refuses what a local accepts.
        """
        compare = _COMPARISON[op]

        if op not in _ORDERINGS and self._is_equatable_pair(left, right):
            return ScalarConstant(compare(left.value, right.value), BuiltinType.BOOL)

        if op in _ORDERINGS and is_string_constant(left) and is_string_constant(right):
            # Compare the UTF-8 bytes, which is what emit_string_order does at run time.
            # Python's str orders by code point and UTF-8 keeps code points in numerical
            # order, so the two agree; encoding first makes them agree by construction.
            lhs = str(left.value).encode('utf-8')
            rhs = str(right.value).encode('utf-8')
            return ScalarConstant(compare(lhs, rhs), BuiltinType.BOOL)

        if not is_numeric_constant(left) or not is_numeric_constant(right):
            er.emit(self.reporter, er.ERR.CE0110, span, op=f'comparison {op} on non-comparable types')
            return None

        return ScalarConstant(compare(left.value, right.value), BuiltinType.BOOL)

    def _is_equatable_pair(self, left: ConstantValue,
                           right: ConstantValue) -> TypeGuard[ScalarConstant]:
        """Whether two non-numeric values of the same type compare for equality."""
        return (isinstance(left, ScalarConstant) and isinstance(right, ScalarConstant)
                and left.semantic_type == right.semantic_type
                and left.semantic_type in (BuiltinType.BOOL, BuiltinType.STRING))

    # The one dispatch. A kind absent here is CE0108 in `evaluate`; `NOT_CONSTANT` names
    # which kinds that is meant for, and the totality gate holds the two sets against the
    # `Expr` union.
    HANDLERS: Mapping[type, Callable[..., Optional[ConstantValue]]] = {
        IntLit: _evaluate_int_lit,
        FloatLit: _evaluate_float_lit,
        BoolLit: _evaluate_bool_lit,
        StringLit: _evaluate_string_lit,
        BinaryOp: _evaluate_binary_op,
        UnaryOp: _evaluate_unary_op,
        ArrayLiteral: _evaluate_array_literal,
        Name: _evaluate_name,
        CastExpr: _evaluate_cast,
        IndexAccess: _evaluate_index,
        InterpolatedString: _evaluate_interpolation,
        Call: _evaluate_struct_construction,
        MemberAccess: _evaluate_member_access,
        DotCall: _evaluate_dot_call,
    }


def _truncated_quotient(left: int, right: int) -> int:
    """Integer division that truncates toward zero, as the backend's sdiv does.

    Python floors instead, so '-7 / 2' read -4 in a constant and -3 in a body (#441).
    """
    magnitude = abs(left) // abs(right)
    return -magnitude if (left < 0) != (right < 0) else magnitude


def _truncated_remainder(left: int, right: int) -> int:
    """Integer remainder whose sign follows the dividend, as the backend's srem does."""
    magnitude = abs(left) % abs(right)
    return -magnitude if left < 0 else magnitude
