"""Expression validation for type validation.

Port templates: `validate_range_expression` call `TypeValidator.validate_expression`,
and that API is deleted. No pass reaches them today. Group A7 (counted ranges) ports
them to SHIR inputs before a SHIR pass calls them.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, NamedTuple, Optional, Tuple

from sushi_lang.internals import errors as er
from sushi_lang.semantics import array_runs
from sushi_lang.semantics.typesys import (
    BuiltinType, ArrayType, DynamicArrayType, EnumType, ForeignPtrType, StructType)
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ast import ArrayLiteral, IndexAccess, RangeExpr
from sushi_lang.semantics.conversions import find_conversion
from sushi_lang.semantics.operators import EQUALITY_OPS
from sushi_lang.semantics.type_predicates import (
    is_error_type, is_instance_of, is_integer_type, is_numeric_type)
from sushi_lang.semantics.generics.results import result_ok_err
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir.exprs import IntConst, Lit, Unary
from sushi_lang.semantics.shir.typeck import NoConversion, TryFacts
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from .arrays import (
    ARRAY_INDEX, RANGE_BOUND, REPEAT_COUNT, STRING_INDEX, reject_non_i32)
from .compatibility import is_valid_cast
from .resolution import resolve_variable_type
from .utils import validate_constant_array_index
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.internals.report import Span
    from sushi_lang.semantics.conversions import Conversion
    from sushi_lang.semantics.shir import exprs as e
    from sushi_lang.semantics.shir.exprs import Binary, ShirExpr
    from sushi_lang.semantics.typesys import Type


def validate_array_literal(validator: 'TypeValidator', expr: ArrayLiteral) -> None:
    """Validate array literal - all elements must have same type."""
    if not expr.elements:
        return

    for element in expr.elements:
        if isinstance(element.value, RangeExpr):
            validate_range_expression(validator, element.value)
        else:
            validator.validate_expression(element.value)
        if element.count is not None:
            reject_non_i32(validator, element.count,
                           validator.validate_expression(element.count),
                           position=REPEAT_COUNT)

    # CE2017 for a repeat count that is not a count, CE2019 for a range that yields nothing,
    # and CE2020 for a range carrying a count. A `const` never arrives here with one, because
    # its evaluator reads the same runs first and `validate_constant` returns on the error --
    # so the two speakers cannot both fire for one literal. The result is discarded: this
    # call is here to SPEAK, and every caller that needs the runs reads them itself.
    array_runs.read_runs(
        expr.elements,
        array_runs.const_int_reader(validator.constant_evaluator()),
        validator.reporter)

    # Rows that disagree are measured against the row type their position stamped: a
    # short row is CE2011 at that row, and not a CE2013 against the first row.
    from .compatibility import reject_array_size_mismatch, short_rows
    short = short_rows(validator, expr)
    for row in short:
        reject_array_size_mismatch(validator, row.resolved_type, row, row.loc)
    if short:
        return

    # Check type consistency of all elements (CE2013). A range element compares as the i32
    # it puts in a slot, and not as the Iterator@(i32) it types as (Ruling 4, #478).
    from sushi_lang.semantics.passes.types.inference import infer_array_element_type

    first_element_type = infer_array_element_type(validator, expr.elements[0].value)
    if first_element_type is not None:
        for element in expr.elements[1:]:
            element_type = infer_array_element_type(validator, element.value)
            if element_type is not None and element_type != first_element_type:
                er.emit(validator.reporter, er.ERR.CE2013, element.value.loc,
                       expected=display_type(first_element_type), got=display_type(element_type))


def is_indexable(ty) -> bool:
    """An array, or a string: `s[i]` reads its byte at offset i in place (#1091)."""
    return isinstance(ty, (ArrayType, DynamicArrayType)) or ty == BuiltinType.STRING


def validate_index_access(validator: 'TypeValidator', expr: IndexAccess) -> None:
    """Validate array indexing - array must be array type, index must be int."""
    validator.validate_expression(expr.array)
    array_type = validator.infer_expression_type(expr.array)

    if expr.is_slice:
        validate_slice(validator, expr, array_type)
        return

    reject_non_i32(validator, expr.index, validator.validate_expression(expr.index),
                   position=STRING_INDEX if array_type == BuiltinType.STRING else ARRAY_INDEX)

    if array_type is not None and not is_indexable(array_type):
        er.emit(validator.reporter, er.ERR.CE2114, expr.array.loc,
                type=display_type(array_type))

    if isinstance(array_type, ArrayType):
        validate_constant_array_index(validator, expr.index, array_type.size)


def validate_slice(validator: 'TypeValidator', expr: IndexAccess,
                   array_type: Optional['Type']) -> None:
    """`data[a..b]`: a borrowed view of a part of a `T[]` or a `T[N]` (#1226, S1-S3, S6).

    The range is the index position of a range: its bounds are i32 and readable bounds
    must go up (CE2125), as in a `foreach`. A reversed range has no slice, and a string
    has no slice (S6), so the range is in no position of its own there (CE2122).
    """
    rng = expr.index
    if not isinstance(rng, RangeExpr):
        return
    if rng.reverse:
        _refuse_range_in_index(validator, rng, "a slice takes its range in order: write "
                               "`data[a..b]`, and walk the view with an index that "
                               "counts down")
        return
    if array_type == BuiltinType.STRING:
        _refuse_range_in_index(validator, rng, "a string has no slice: copy the part with "
                               "`s.s(a, b)`, or index one byte with `s[i]`")
        return
    validate_range_value(validator, rng)
    if array_type is not None and not isinstance(array_type, (ArrayType, DynamicArrayType)):
        er.emit(validator.reporter, er.ERR.CE2114, expr.array.loc,
                type=display_type(array_type))


def _refuse_range_in_index(validator: 'TypeValidator', expr: RangeExpr, help_text: str) -> None:
    """CE2122 for a range in an index that cannot be a slice. The bounds are still checked."""
    er.emit_with(validator.reporter, er.ERR.CE2122, expr.loc).help(help_text).emit()
    validator.refused_ranges.add(id(expr))
    validate_range_expression(validator, expr)


def check_cast(validator: 'TypeValidator', node: 'e.Cast',
               _expected: Optional['Type']) -> Optional['Type']:
    """The arm of `as` (docs/design/ir.md 4.8, 7.3): it answers the target type.

    A cast between two numeric types, and a cast to the type of its operand, call nothing:
    the conversion row is `NoConversion`. Any other pair is CE2014. In the body of a
    conversion, a cast of the pair of that conversion is CE2523. Three casts are not typed
    yet, and the walk of the body stops there: a cast to an opaque type parameter of a
    template check (C1), a cast to or from `ptr` (D5), and a cast between two error types
    that a declared conversion joins (D2).
    """
    target = validator.written_type(node.target)
    resolved = resolve_variable_type(validator, target, node.loc)
    source = _check_cast_operand(validator, node.operand, resolved)
    if source is None:
        return None
    from sushi_lang.semantics.generics.opaque import holds_opaque, note_opaque
    if holds_opaque(target) or any(isinstance(ty, ForeignPtrType) for ty in (source, resolved)):
        visit.stop_at(validator, node)
    conversion = find_conversion(validator.tables, source, resolved)
    if conversion is not None:
        if _reject_self_conversion(validator, node, conversion):
            return None
        visit.stop_at(validator, node)
    if not is_valid_cast(source, resolved):
        diag = er.emit_with(validator.reporter, er.ERR.CE2014, node.loc,
                            source=display_type(source), target=display_type(target))
        if is_error_type(source) and is_error_type(resolved):
            diag.help(conversion_help(source, resolved, at_try=False))
        note_opaque(diag, source, resolved).emit()
        return None
    validator.body_answers().record_conversion(node.shir_id, NoConversion())
    return resolved


def _check_cast_operand(validator: 'TypeValidator', operand: 'ShirExpr',
                        target: 'Type') -> Optional['Type']:
    """The type of the operand of a cast to `target`.

    An integer literal directly under a cast to an integer type, with or without its
    negation, has the target type, and its range is not checked: the cast keeps the bits
    that the type holds (`300 as u8` holds 44, `visit.answer`). The fold reads the operand
    at the target too (`fold._cast`). Any other operand gets no type from its position.
    """
    literal = operand.operand if isinstance(operand, Unary) and operand.op == "neg" else operand
    if not (is_integer_type(target) and isinstance(literal, Lit)
            and isinstance(literal.value, IntConst)):
        return visit.check_expr(validator, operand)
    if literal is not operand:
        visit.answer(validator, literal, target, wraps=True)
    return visit.answer(validator, operand, target, wraps=True)


def _reject_self_conversion(validator: 'TypeValidator', node: 'e.Cast',
                            conversion: 'Conversion') -> bool:
    """CE2523: a cast of a conversion's own pair inside that conversion's body. Answers True
    when it reported.

    `self` is always a value of the source, so the call never ends. A cast of another
    pair in the body is legal.
    """
    if conversion != validator.body_conversion:
        return False
    diag = er.emit_with(validator.reporter, er.ERR.CE2523, node.loc,
                        source=display_type(conversion.source),
                        target=display_type(conversion.target))
    if conversion.name_span is not None:
        diag.note_at("the conversion is declared here", conversion.name_span,
                     conversion.filename)
    diag.help("build the target value directly, for example with one of its "
              "variants").emit()
    return True


def validate_range_value(validator: 'TypeValidator', expr: 'RangeExpr') -> None:
    """A range in a position that expects `Iterator@(i32)`: an owned iterator (#1259).

    The bounds are i32, and readable bounds must go up, as in a `foreach`.
    """
    from sushi_lang.semantics.array_runs import const_int_reader
    from sushi_lang.semantics.ranges import read_range, reject_a_range_that_goes_down
    validate_range_expression(validator, expr)
    reject_a_range_that_goes_down(
        read_range(expr, const_int_reader(validator.constant_evaluator()),
                   validator.reporter),
        validator.reporter)


def refuse_range_value(validator: 'TypeValidator', expr: 'RangeExpr') -> None:
    """CE2122: a range in a position that expects no `Iterator@(i32)`.

    A `foreach` iterable and an array element validate their range directly, and a
    position that expects `Iterator@(i32)` stamps it a value, so a range that reaches the
    general dispatch unstamped is in any other position (#1165, #1259). The bounds are
    still checked.
    """
    er.emit_with(validator.reporter, er.ERR.CE2122, expr.loc) \
        .help("walk it with `foreach`, spell it into an array (`from([a..b])`), or give "
              "it to a position of the type `Iterator@(i32)`") \
        .emit()
    validator.refused_ranges.add(id(expr))
    validate_range_expression(validator, expr)


def validate_range_expression(validator: 'TypeValidator', expr: 'RangeExpr') -> None:
    """Validate a range expression: each bound is an i32 (#870).

    A bound that is not a number at all is the range's own refusal (CE2072). A number of
    any other type is refused by the one i32-position rule.
    """
    walked = [(bound, validator.validate_expression(bound)) for bound in (expr.start, expr.end)]
    for bound, bound_type in walked:
        if bound_type is not None and not is_numeric_type(bound_type):
            er.emit(validator.reporter, er.ERR.CE2072, bound.loc,
                   got=display_type(bound_type), expected="integer type")
            continue
        reject_non_i32(validator, bound, bound_type, position=RANGE_BOUND)


class _Arms(NamedTuple):
    """What a `??` reads off the `Result@(T, E)` it is applied to."""
    value_type: 'Type'       # the payload the success arm carries
    success_tag: int         # that arm's variant index
    error_type: 'Type'       # the failure arm's payload


def check_try(validator: 'TypeValidator', node: 'e.Try',
              _expected: Optional['Type']) -> Optional['Type']:
    """The arm of `??` (docs/design/ir.md 7.3, 7.4): it answers the value of the success arm.

    Four questions, in this order, and each one reports its own fault: what channel
    encloses the `??` (CE0131, CE2508), what the operand under it is (CE2507), whether
    the two failure arms name one type (CE2511), and the facts of the `??`, which are rows
    of the table. The channel comes first, so a `??` in a bare body gets CE0131 alone
    (docs/design/error-conversion.md section 4). Two error types that a declared
    conversion joins are not typed yet, and the walk of the body stops there.
    """
    inner_type = visit.check_expr(validator, node.inner)
    channel = _enclosing_channel(validator, node)
    if channel is None or inner_type is None:
        return None
    arms = _unwrapped_arms(validator, node, inner_type)
    if arms is None:
        return None
    agreed, conversion = _error_arms_agree(validator, node, arms, channel)
    if not agreed:
        return None
    if conversion is not None:
        visit.stop_at(validator, node)
    answers = validator.body_answers()
    answers.record_conversion(node.shir_id, NoConversion())
    answers.record_try(node.shir_id, TryFacts(arms.success_tag, arms.value_type,
                                              arms.error_type, channel))
    return arms.value_type


def _unwrapped_arms(validator: 'TypeValidator', node: 'e.Try',
                    inner_type: 'Type') -> Optional[_Arms]:
    """What the `Result@(T, E)` under a `??` carries, or None once CE2507 is reported.

    `??` takes a `Result@(T, E)` and nothing else (#1168). It reads the TYPE and not its
    variant names: a `Maybe@(T)` holds no error value, and a user enum shaped like a
    `Result` or a `Maybe` is not one.
    """
    if not (isinstance(inner_type, EnumType) and is_instance_of(inner_type, "Result")):
        diag = er.emit_with(validator.reporter, er.ERR.CE2507, node.loc,
                            got=display_type(inner_type))
        if is_instance_of(inner_type, "Maybe"):
            diag.help("a `Maybe` holds no error value, so write one with `or_err`: "
                      "`m.or_err(nom <error value>)??`")
        else:
            diag.help("answer a `Result@(T, E)` here (the callee writes `| E`), "
                      "or use the value without `??`")
        diag.emit()
        return None

    ok_type, err_type = result_ok_err(inner_type)
    success_tag = inner_type.get_variant_index("Ok")
    if success_tag is None:
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"'{display_type(inner_type)}' has no Ok variant")
    return _Arms(value_type=ok_type, success_tag=success_tag, error_type=err_type)


def _enclosing_channel(validator: 'TypeValidator', node: 'e.Try') -> Optional['Type']:
    """The interned `Result@(T, E)` a `??` propagates into, or None when there is none.

    A BARE body has no channel (docs/design/error-channel.md). The collect pass already
    refused every `??` in a written function or method body with CE0131, so a second
    diagnostic here would only mislead: the body holds that error, and the pass records
    it (`refused_before`). A lambda is the one body that pass cannot judge: its channel
    comes from its TYPE, which this pass infers, so its CE0131 is emitted here. A `??`
    outside every body keeps the CE2508 backstop.
    """
    channel = validator.channel_result
    if channel is not None:
        return channel
    if validator.body_name is None:
        er.emit(validator.reporter, er.ERR.CE2508, node.loc)
    elif validator.body_name == "lambda":
        er.emit_with(validator.reporter, er.ERR.CE0131, node.loc,
                     context="a lambda without '| E'") \
            .help("write '| E' in the function type the lambda takes, or handle the "
                  "value in the body (match, .realise(default))").emit()
    else:
        validator.refused_before = True
    return None


def _error_arms_agree(validator: 'TypeValidator', node: 'e.Try', arms: _Arms,
                      channel: 'Type') -> Tuple[bool, Optional['Conversion']]:
    """Whether the operand's failure arm reaches the channel's, and through which conversion.

    The same type propagates unchanged. Two error types propagate through the declared
    conversion of the pair, the one lookup of `semantics/conversions.py`, and nothing
    else (docs/design/error-conversion.md section 3.3): a chain is never followed.
    Otherwise it is CE2511, and the help names the declaration to write. In a generic
    function the pair is asked one time, on the template (#1070): an opaque `E` is equal
    to itself alone, so a `??` propagates the same `E` and nothing else (R8).
    """
    outer_ok_type, outer_err_type = result_ok_err(channel)
    inner_err_type = arms.error_type

    structs = validator.struct_table.by_name
    enums = validator.enum_table.by_name
    inner = resolve_unknown_type(inner_err_type, structs, enums)
    outer = resolve_unknown_type(outer_err_type, structs, enums)
    if inner == outer:
        return True, None

    conversion = find_conversion(validator.tables, inner, outer)
    if conversion is not None:
        return True, conversion

    from sushi_lang.semantics.generics.opaque import note_opaque
    diagnostic = er.emit_with(validator.reporter, er.ERR.CE2511, node.loc,
                              ok_type=display_type(outer_ok_type),
                              inner_err=display_type(inner_err_type),
                              outer_err=display_type(outer_err_type))
    note_opaque(diagnostic, inner, outer) \
        .help(conversion_help(inner, outer, at_try=True)).emit()
    return False, None


def conversion_help(source: 'Type', target: 'Type', *, at_try: bool) -> str:
    """The help that names the conversion to declare, for `??` (CE2511) and `as` (CE2014).

    It names only a declaration that the collect pass accepts. When no declaration of
    the pair is legal (a generic side, C6; a target with no home module, C12), it names
    the form at the site: `.map_err(f)??` for a `??`, and a function that takes the error
    `nom` for an `as`. It never offers `r?? as T`: `as` binds after `??`, so that
    spelling casts the VALUE.
    """
    from sushi_lang.semantics.passes.collect.enums import PREDEFINED_ENUM_HOMES
    from sushi_lang.semantics.generics.opaque import holds_opaque
    source_text, target_text = display_type(source), display_type(target)
    if holds_opaque(source) or holds_opaque(target):
        # R8 (#1070): no conversion can name a type parameter, so a `??` propagates the
        # same error type and no other.
        return (f"a type parameter has no conversion, so only the same error type "
                f"propagates; {_site_form(source_text, target_text, at_try)}, or write "
                f"the concrete error type in the signature")
    generic = next((side for side in (source, target) if getattr(side, "generic_args", None)),
                   None)
    if generic is not None:
        return (f"a conversion cannot take the generic error type "
                f"'{display_type(generic)}'; {_site_form(source_text, target_text, at_try)}")
    declaration = f"`extend {source_text} as {target_text}:`"
    name = getattr(target, "name", None)
    if name in PREDEFINED_ENUM_HOMES:
        home = PREDEFINED_ENUM_HOMES[name]
        if home is None:
            return (f"no unit may declare a conversion into '{target_text}', because it "
                    f"has no home module; {_site_form(source_text, target_text, at_try)}, "
                    f"or use an error type of your own as the target, and declare "
                    f"`extend {source_text} as <YourError>:` in the unit that declares it")
        return (f"only <{home}> may declare {declaration}; answer an error type of "
                f"your own, and convert both errors into it")
    return (f"declare {declaration} in the unit that declares '{target_text}', and "
            f"return the converted value")


def _site_form(source_text: str, target_text: str, at_try: bool) -> str:
    """The conversion at one site, with no declaration: `map_err` for `??`, a call for `as`."""
    if at_try:
        return (f"convert the error at this site with `.map_err(f)??`, where `f` takes "
                f"'{source_text}' `nom` and answers '{target_text}'")
    return f"call a function that takes the error `nom` and answers '{target_text}'"


def reject_mixed_numeric_operands(validator: 'TypeValidator', loc: 'Optional[Span]',
                                  left_type: 'Optional[Type]',
                                  right_type: 'Optional[Type]') -> bool:
    """CE2510 when two numeric operands are not of the same type. Answers True when it
    reported.

    One rule for every operator that takes its result from operands which must
    already agree: arithmetic, comparison, and the bitwise `& | ^`. Sushi converts
    no numeric type implicitly, so the operands say what the result is and `as` is
    the only way to change a width. A shift never asks both its operands: the
    count says how far to move, not what the result is.
    """
    if left_type is None or right_type is None:
        return False
    if not (is_numeric_type(left_type) and is_numeric_type(right_type)):
        return False
    if left_type == right_type:
        return False

    er.emit(validator.reporter, er.ERR.CE2510, loc,
            left_type=display_type(left_type), right_type=display_type(right_type))
    return True


def is_string_plus(op: str, left_type: 'Optional[Type]',
                   right_type: 'Optional[Type]') -> bool:
    """Is this the '+' that CE2509 owns -- the one with a string operand?

    Sushi has no concatenation operator, so CE2509 names the interpolation that
    replaces it. The arithmetic operand rule steps over exactly this pair, because
    one fault reads better as one diagnostic.
    """
    return op == "+" and BuiltinType.STRING in (left_type, right_type)


def reject_non_numeric_arithmetic(validator: 'TypeValidator', op: str,
                                  operands: 'list[Tuple[ShirExpr, Optional[Type]]]') -> bool:
    """CE2518 for the first arithmetic operand that is not a number. Answers True when it
    reported.

    One rule for the whole group: + - * / % and the unary minus, which is why the
    operands arrive as a list rather than as a pair. Arithmetic combines numbers and
    Sushi converts nothing on its own, so the permitted set is exactly the numeric
    types and there is no non-numeric escape to name -- where a comparison needs two
    allow-lists, this needs none.

    The first bad operand is enough: `a - b` on two strings is one fault. A wrapper
    is told how to take its value out, because a missing '??' is the common way here.
    """
    if len(operands) == 2 and is_string_plus(op, operands[0][1], operands[1][1]):
        return False

    for operand, operand_type in operands:
        if operand_type is None or is_numeric_type(operand_type):
            continue
        report = er.emit_with(validator.reporter, er.ERR.CE2518, operand.loc,
                              op=op, type_name=display_type(operand_type))
        wrapper = _wrapper_of(operand_type)
        if wrapper is not None:
            report = report.help(f"take the value with {take_the_value(wrapper[0])}")
        from sushi_lang.semantics.generics.opaque import explain_no_arithmetic
        explain_no_arithmetic(report, operand_type).emit()
        return True
    return False


def reject_zero_divisor(validator: 'TypeValidator', node: 'Binary',
                        left_type: 'Optional[Type]') -> bool:
    """CE0112 when the compiler can read the divisor of '/' or '%' and it is zero. Answers
    True when it reported.

    The same code the constant evaluator emits, because it is the same rule: one
    compile-time arithmetic, and a body cannot disagree with a constant. The fold reads
    the divisor at the type of the dividend (docs/design/ir.md 7.4). A divisor the
    compiler cannot read is ordinary code and is left alone, exactly as a computed shift
    count is (CE2512).

    A left operand that is not numeric belongs to CE2518, which has already spoken;
    reporting a zero divisor as well would make one expression two faults.
    """
    from sushi_lang.semantics.const_eval import is_numeric_constant

    if left_type is None or not is_numeric_type(left_type):
        return False

    divisor = validator.body_folder().scalar(node.rhs, left_type)
    if divisor is None or not is_numeric_constant(divisor) or divisor.value != 0:
        return False

    er.emit(validator.reporter, er.ERR.CE0112, node.rhs.loc)
    return True


def top_level_contract(validator: 'TypeValidator', ty: 'Optional[Type]',
                        contract: str, refused: 'Optional[list]' = None
                        ) -> 'tuple[bool, Optional[str]]':
    """May a value of `ty` meet the positions of `contract` at the top level?

    `Eq` and `Ord` are the operators, the array search methods (`contains`, `index_of`)
    and the method call (CE2100 cites CE2514 for a reason); `Display` is an
    interpolation hole and `print`/`println`. THE rule, in one place, so the positions
    cannot drift apart: a primitive keeps its closed set, and a struct or an enum asks
    the derived contract, with the compilation's override. A printed position also
    takes an array, a `List@(T)` and an `Own@(T)` (`printed_contract`). `refused`,
    when given, receives the types that the rule refuses, for the help of a refusal.
    """
    from sushi_lang.semantics.generics.contracts import (
        DISPLAY, operand_contract, override_of, printed_contract)
    overridden = override_of(validator.derived_methods, contract)
    if contract == DISPLAY:
        return printed_contract(ty, overridden=overridden, refused=refused)
    return operand_contract(ty, contract, overridden=overridden, refused=refused)


def has_equality(validator: 'TypeValidator', ty: 'Type',
                 refused: 'Optional[list]' = None) -> bool:
    """Can two values of `ty` meet `==`?"""
    from sushi_lang.semantics.generics.contracts import EQ
    return top_level_contract(validator, ty, EQ, refused)[0]


def _comparison_escape(ty: 'Type') -> Optional[str]:
    """The way to ask the question that a comparison of this type cannot answer."""
    if isinstance(ty, EnumType):
        return "use match to ask which variant the value holds"
    if ty == BuiltinType.BOOL:
        return "use != to ask whether two bools differ"
    if isinstance(ty, (ArrayType, DynamicArrayType)):
        return "compare the elements, or the lengths"
    if isinstance(ty, StructType):
        return "compare the fields one at a time"
    return None


def reject_uncomparable_operands(validator: 'TypeValidator', node: 'Binary',
                                 left_type: 'Optional[Type]',
                                 right_type: 'Optional[Type]') -> bool:
    """CE2513 for a mixed pair, CE2514 for a type that carries no such comparison. Answers
    True when it reported.

    One rule for all six comparison operators. Equality is defined for the numeric
    types, bool and string; an order is defined for the numeric types and string,
    where it reads the bytes. A bool is deliberately left out of the order, because
    `a < b` on two bools is almost always a typo for `!=`. A struct or an enum compares
    by its derived or implemented contract.
    """
    if left_type is None or right_type is None:
        return False
    if is_numeric_type(left_type) and is_numeric_type(right_type):
        return False

    if left_type != right_type:
        from sushi_lang.semantics.generics.opaque import note_opaque
        note_opaque(er.emit_with(validator.reporter, er.ERR.CE2513, node.loc,
                                 left_type=display_type(left_type),
                                 right_type=display_type(right_type), op=node.op),
                    left_type, right_type).emit()
        return True

    from sushi_lang.semantics.generics.contracts import EQ, ORD
    contract = EQ if node.op in EQUALITY_OPS else ORD
    refused: list = []
    permitted, reason = top_level_contract(validator, left_type, contract, refused)
    if permitted:
        return False

    builder = er.emit_with(validator.reporter, er.ERR.CE2514, node.loc,
                           op=node.op, type_name=display_type(left_type))
    if reason is not None:
        builder = builder.note(f"no derived {contract}: {reason}")
    escape = _comparison_escape(left_type)
    if escape is not None:
        builder = builder.help(escape)
    from sushi_lang.semantics.generics.opaque import explain_unpromised
    explain_unpromised(builder, contract, refused).emit()
    return True


def reject_non_integer_operands(validator: 'TypeValidator', op: str,
                                operands: 'list[Tuple[ShirExpr, Optional[Type]]]') -> bool:
    """CE2004 for the first operand of a bitwise operator that is not an integer. Answers
    True when it reported.

    An integer, not merely a number: a float has no bits to combine, and its bits
    are reached through `.to_bits()`. One rule for `& | ^`, the two shifts and `~`.
    """
    for operand, operand_type in operands:
        if operand_type is not None and not is_integer_type(operand_type):
            er.emit(validator.reporter, er.ERR.CE2004, operand.loc, op=op)
            return True
    return False


def reject_overflow(validator: 'TypeValidator', node: 'ShirExpr', ty: 'Type') -> bool:
    """CE2077 when the compiler can read the operation `node` at its type `ty` and the
    operation leaves that type. Answers True when it reported.

    The fold does the reading and the arithmetic (docs/design/ir.md 7.4), so the language
    has ONE compile-time arithmetic and a constant cannot disagree with the same
    expression in a body. An operand that the compiler cannot read -- a local, a call --
    is ordinary code and not a diagnostic.

    Each overflow-checked operation asks for itself, after its operands, so an overflow is
    reported at the operation that computed it and nowhere else: the innermost operation
    of `(200 + 100) / 2` reports, and the division around it has no type to ask with. The
    fold reads each node one time for each type, so a nest is folded one time. An
    overflow in a constant that the operation names is reported where the constant is
    declared, because the reader of a constant answers no value for it.
    """
    from sushi_lang.semantics.const_eval import emit_overflow

    folder = validator.body_folder()
    start = len(folder.overflows)
    folder.read(node, ty)
    found = [overflow for overflow in folder.overflows[start:] if overflow.node is node]
    for overflow in found:
        emit_overflow(validator.reporter, overflow.op, overflow.value, overflow.ty, node.loc)
    return bool(found)


def reject_impossible_shift_count(validator: 'TypeValidator', node: 'Binary',
                                  value_type: 'Optional[Type]',
                                  count_type: 'Optional[Type]') -> bool:
    """CE2512 when a shift count the compiler can read cannot fit the value. Answers True
    when it reported.

    The width of the shifted value is the limit: a count at or above it moves every
    bit out of the type, and a negative count is not a shift. LLVM answers such a
    shift with poison, so nothing reports the loss and the program prints whatever
    the optimizer left behind. The fold reads the count at its own type. A count that
    cannot be read at compile time is left alone -- a bit reader computes its count, and
    no check is emitted around it.
    """
    from sushi_lang.semantics.const_eval import is_integer_constant
    from sushi_lang.semantics.integer_width import integer_bit_width

    width = integer_bit_width(value_type)
    if width is None or count_type is None:
        return False

    value = validator.body_folder().scalar(node.rhs, count_type)
    if value is None or not is_integer_constant(value):
        return False
    count = int(value.value)
    if 0 <= count < width:
        return False

    er.emit(validator.reporter, er.ERR.CE2512, node.rhs.loc,
            count=count, value_type=display_type(value_type), max_count=width - 1)
    return True


# The predicate that turns each wrapper into the bool a condition wants.
_WRAPPER_PREDICATES = {"Result": "is_ok", "Maybe": "is_some"}


def take_the_value(wrapper: str) -> str:
    """The ways to take the value out of a wrapper, in the words of a help.

    `??` takes a Result only (docs/design/error-conversion.md section 4), so a Maybe is
    given an error value first.
    """
    if wrapper == "Maybe":
        return "'.realise(default)', match, or '.or_err(nom e)??'"
    return "'??', '.realise(default)' or match"


def _wrapper_of(ty: Optional['Type']) -> Optional[Tuple[str, str]]:
    """(wrapper name, predicate) when this type is a Result or a Maybe, else None.

    Both spellings answer: the interned enum a resolved signature carries, and the
    GenericTypeRef a declared one keeps.
    """
    for name, predicate in _WRAPPER_PREDICATES.items():
        if isinstance(ty, EnumType) and ty.generic_base == name:
            return name, predicate
        if isinstance(ty, GenericTypeRef) and ty.base_name == name:
            return name, predicate
    return None


def reject_non_bool_condition(validator: 'TypeValidator', node: 'ShirExpr',
                              ty: Optional['Type']) -> bool:
    """Refuse a value of the type `ty` that stands where a condition belongs and is not a bool.

    The ONE seam for every condition position: an `if`, a `while` (#522), an `assert`,
    and the operands of `and`, `or`, `xor` and `not` (#532). A wrapper is told which predicate
    answers for it (CE2516); everything else is CE2005, which offers the `== 0` escape
    only to an integer -- a string or a struct has no such spelling. Answers True when
    it reported. No type is an error that was reported already.
    """
    if ty is None or ty == BuiltinType.BOOL:
        return False

    wrapper = _wrapper_of(ty)
    if wrapper is not None:
        name, predicate = wrapper
        er.emit_with(validator.reporter, er.ERR.CE2516, node.loc,
                     ty=display_type(ty), wrapper=name) \
            .help(f"use '.{predicate}()' to test it, or take the value with "
                  f"{take_the_value(name)}").emit()
        return True

    report = er.emit_with(validator.reporter, er.ERR.CE2005, node.loc)
    if is_integer_type(ty):
        report = report.help("use '== 0' or '!= 0' for integer conditions")
    report.emit()
    return True
