"""Pattern matching validation for type validation."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, List, Optional, Set, Tuple

from sushi_lang.semantics.type_predicates import generic_base_of
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.passes.types.visibility import name_is_contested
from sushi_lang.semantics.typesys import (
    BuiltinType, EnumType, ReferenceType, StructType, Type,
)
from sushi_lang.semantics.ast import (
    Match, MatchArm, Pattern, LiteralPattern, WildcardPattern, OwnPattern, Block, Expr,
    NomBinding, OrPattern, RangePattern, RefBinding, TupleLiteral, TuplePattern,
    alternatives_of, pattern_source,
)
from sushi_lang.semantics.constant_borrow import reject_borrow_of_constant
from sushi_lang.semantics.ownership import is_own_type
from sushi_lang.semantics.param_modes import ParamMode, borrow_mode, receiver_mode
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.generics.own import own_payload_type
from sushi_lang.semantics.generics.tuples import display_tuple, is_tuple_type, tuple_elements
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.integer_patterns import goes_down, literal_value, pattern_interval
from sushi_lang.semantics.integer_width import integer_range
from .exhaustiveness import (
    OWN_KEY, TUPLE_KEY, WILD, Ctor, IntegerColumn, IntRange, Or, Pat, Signature, Wild,
    analyze, covers, string_key,
)
from .utils import resolve_declared_type
from sushi_lang.semantics.type_predicates import BUILTIN_INTEGER_TYPES

# The scrutinee types an integer literal match accepts (#415).
_INTEGER_SCRUTINEES = BUILTIN_INTEGER_TYPES


def _literal_kind(value: int | str) -> str:
    """The kind of a literal pattern's value, for the `{kind}` slots: integer or string."""
    return "string" if isinstance(value, str) else "integer"


def _scrutinee_literal_kind(ty: Type) -> Optional[str]:
    """The literal kind a scrutinee type takes, or None when it takes no literal arm."""
    if ty == BuiltinType.STRING:
        return "string"
    if ty in _INTEGER_SCRUTINEES:
        return "integer"
    return None

if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.internals.report import Span

# Where the type of a pattern position comes from, for the relational note of CE2107: a
# label and the span it points at.
_Note = Tuple[str, Optional["Span"]]


def _resolve(validator: 'TypeValidator', ty: Type) -> Type:
    """The type a scrutinee or a pattern binding stands for.

    A variant's associated type can still be a NAME when the pattern rules read it, so
    every reader of one asks this first. It ran nine times by hand, each copy naming both
    tables (#742), and then it answered the pass's own question with a body of its own
    (#755): `resolve_declared_type` is that answer.
    """
    resolved = resolve_declared_type(validator, ty)
    return resolved if resolved is not None else ty


def _walk_arm_body(validator: 'TypeValidator', arm: MatchArm) -> None:
    """Validate one arm's body, in either shape a `->` takes."""
    if isinstance(arm.body, Block):
        validator._validate_block(arm.body)
    elif isinstance(arm.body, Expr):
        validator.validate_expression(arm.body)


class _Dropped:
    """An alternative that an older rule reported as a repeat (CE2041, CE2075).

    It gets no place in the row, so the dead-pattern error does not speak again.
    """


_DROPPED = _Dropped()


@dataclass(frozen=True)
class _Bound:
    """One name a pattern binds: its value type and its mode."""
    name: str
    ty: Type
    mode: str                 # "bare" | "peek" | "poke" | "nom"


_MODE_WORDS = {"bare": "a bare binding", "peek": "a `peek` binding",
               "poke": "a `poke` binding", "nom": "a `nom` binding"}


@dataclass
class _ArmRows:
    """What the arms of one match give the checker.

    `rows[i]` is arm i's pattern for the matrix, or None for an arm left out (a duplicate
    that CE2041 or CE2075 reported). `reported` holds the arms whose own rule spoke, so the
    dead-arm error does not speak again. `invalid` says that an arm has an error in its
    pattern: the checker then gives no answer, because the coverage is not known.
    """
    rows: List[Optional[Pat]] = field(default_factory=list)
    reported: Set[int] = field(default_factory=set)
    invalid: bool = False
    after_wildcard: Optional[int] = None


def validate_match_statement(validator: 'TypeValidator', stmt: Match) -> None:
    """Validate a match statement: the scrutinee, each arm's pattern, and the coverage."""
    scrutinee_type = validate_match_scrutinee(validator, stmt)
    if scrutinee_type is None:
        return

    if not isinstance(scrutinee_type, EnumType) and not is_tuple_type(scrutinee_type):
        # An integer (#415) or a string scrutinee dispatches on literal arms.
        validate_literal_match(validator, stmt, scrutinee_type)
        return

    # A contested enum name has no trustworthy declaration for this unit: it declared
    # the name itself and already heard why its own declaration lost (CE0004, CE2046,
    # CE3011). Checking its arms against the winner's variants would report a variant it
    # never wrote and an exhaustiveness it cannot satisfy (D2).
    if isinstance(scrutinee_type, EnumType) and name_is_contested(
            validator, "enum", scrutinee_type.name):
        return

    # Stash the resolved concrete type on the node so the backend does not have to
    # re-derive it from the scrutinee expression (which it cannot always do; a miss there
    # silently drops pattern bindings and surfaces as CE0055).
    stmt.resolved_scrutinee_type = scrutinee_type

    reject_poke_binding_into_a_constant(validator, stmt)

    arms = collect_and_validate_patterns(validator, stmt, scrutinee_type)

    check_match_exhaustiveness(validator, stmt, scrutinee_type, arms)


def reject_poke_binding_into_a_constant(validator: 'TypeValidator',
                                        stmt: Match) -> None:
    """A `poke` pattern binding needs a scrutinee with storage (#685).

    `Variant(poke x)` binds a POINTER into the scrutinee's payload, and a write through
    it reaches that storage. A constant is read-only there, so the write landed in
    `.rodata` and the program stopped -- the same question a `poke self` call asks, at
    the one position that never asked it. A `peek` and a bare binding READ the payload,
    and reading a constant is legal. Each element of a tuple-literal scrutinee is its own
    scrutinee (ruling 3 of the tuple design), so each element asks it for the bindings
    of its own position.
    """
    for scrutinee, items in _scrutinee_positions(stmt):
        root = walk_place(scrutinee, Step.MEMBER | Step.INDEX).name
        if root is None or root.id in validator.variable_types:
            continue
        binding = next((b for item in items for b in _poke_bindings(item)), None)
        if binding is None:
            continue
        reject_borrow_of_constant(validator.err, root.id,
                                  validator.const_sig(root.id),
                                  binding.loc or stmt.loc, mode=binding.mode)


def _scrutinee_positions(stmt: Match) -> Iterator[Tuple[Expr, List[object]]]:
    """Each scrutinee expression with the pattern items that read it."""
    if not isinstance(stmt.scrutinee, TupleLiteral):
        yield stmt.scrutinee, [arm.pattern for arm in stmt.arms]
        return
    for index, element in enumerate(stmt.scrutinee.elements):
        yield element, [alt.elements[index] for arm in stmt.arms
                        for alt in alternatives_of(arm.pattern)
                        if isinstance(alt, TuplePattern) and index < len(alt.elements)]


def _poke_bindings(item: object) -> Iterator[RefBinding]:
    """The `poke` bindings of a pattern item, at any depth, in source order."""
    if isinstance(item, RefBinding):
        if receiver_mode(item.mode) is ParamMode.POKE:
            yield item
    elif isinstance(item, Pattern):
        for binding in item.bindings:
            yield from _poke_bindings(binding)
    elif isinstance(item, TuplePattern):
        for element in item.elements:
            yield from _poke_bindings(element)
    elif isinstance(item, OrPattern):
        for alternative in item.alternatives:
            yield from _poke_bindings(alternative)
    elif isinstance(item, OwnPattern) and not isinstance(item.inner_pattern, str):
        yield from _poke_bindings(item.inner_pattern)


def validate_match_scrutinee(validator: 'TypeValidator', stmt: Match) -> Optional[Type]:
    """Validate the scrutinee is matchable: an enum, an integer (#415), a string, or a tuple."""
    validator.validate_expression(stmt.scrutinee)
    scrutinee_type = validator.infer_expression_type(stmt.scrutinee)

    if scrutinee_type is None:
        # A scrutinee with no type is refused by the check that found the fault. With no
        # error at all, the arms would go unchecked and the backend would guess (#1005).
        # The reporter drops a repeat in an instance body, and a refusal of a call site
        # is reported at its first site only, so the test is "any error offered".
        if not validator.reporter.has_offered_errors:
            raise_internal_error(
                "CE0015", message="match scrutinee has no type and no diagnostic says why")
        return None

    # An unresolved generic scrutinee (e.g. an indexed element of a Maybe<i32>[]
    # array, or a method returning Maybe<T>) infers to a GenericTypeRef/UnknownType.
    # Resolve it to its concrete monomorphized enum so pattern matching sees a real
    # EnumType instead of rejecting it (CE2048).
    scrutinee_type = _resolve(validator, scrutinee_type)
    if isinstance(scrutinee_type, ReferenceType) and is_tuple_type(scrutinee_type):
        scrutinee_type = _resolve(validator, scrutinee_type.referenced_type)

    if isinstance(scrutinee_type, EnumType) or _scrutinee_literal_kind(scrutinee_type):
        return scrutinee_type
    if isinstance(scrutinee_type, StructType) and is_tuple_type(scrutinee_type):
        return scrutinee_type

    # A type name this unit lost (#863): the value has the winner's type, and the
    # declaration's CE0004 / CE0006 is the one fault.
    if isinstance(scrutinee_type, StructType) and name_is_contested(
            validator, "struct", scrutinee_type.name):
        return None

    er.emit(validator.reporter, er.ERR.CE2048, stmt.scrutinee.loc, got=display_type(scrutinee_type))
    return None


def validate_literal_match(validator: 'TypeValidator', stmt: Match,
                           scrutinee_type: Type) -> None:
    """Validate a match on an integer (#415) or a string scrutinee: literal and range arms.

    Each literal arm must be of the scrutinee's kind (CE2076 otherwise). An integer
    literal takes the scrutinee's type under the same fit rule as any context-typed
    literal (a non-decimal literal is a bit pattern). Duplicates are duplicates by VALUE,
    so `0x2a` and `42` are one arm, `0xff` and `-1` are one arm on an i8, and `"a"` and
    `'a'` are one arm. A range that shares a value with an arm above is CE2075 too. The
    one checker answers the coverage: the integer arms cover the type, or a `_` arm does
    (CE2074); the values of a string cannot be listed, so a string match needs the `_`.
    """
    kind = _scrutinee_literal_kind(scrutinee_type)
    # The backend switches on the value of an integer match. A string match has no
    # switch: it reads `resolved_scrutinee_type`, as a tuple match does.
    if kind == "integer":
        stmt.integer_match_type = scrutinee_type
    else:
        stmt.resolved_scrutinee_type = scrutinee_type

    arms = _ArmRows()
    seen = _Seen()
    for idx, arm in enumerate(stmt.arms):
        pattern = arm.pattern

        if isinstance(pattern, WildcardPattern):
            _wildcard_row(validator, arms, idx, len(stmt.arms), pattern)
        else:
            pieces = [(alt, _literal_alternative(validator, alt, scrutinee_type, kind, seen))
                      for alt in alternatives_of(pattern)]
            _add_row(arms, idx, pattern, pieces)

        _walk_arm_body(validator, arm)

    check_match_exhaustiveness(validator, stmt, scrutinee_type, arms)


@dataclass
class _Seen:
    """The values that the arms and alternatives above already match, for CE2075.

    A string is one value. An integer literal and a range are an interval of the
    scrutinee type, each with the spelling of the pattern that matches it first.
    """
    strings: dict[str, str] = field(default_factory=dict)
    integers: List[Tuple[IntRange, str]] = field(default_factory=list)


def _literal_alternative(validator: 'TypeValidator', alt: object, scrutinee_type: Type,
                         kind: Optional[str], seen: _Seen) -> Optional[Pat | _Dropped]:
    """One top-level alternative of a literal arm: its place in the row, or None after an
    error. A value that an arm or an alternative above already matches is CE2075."""
    if isinstance(alt, WildcardPattern):
        return WILD
    if isinstance(alt, RangePattern) and (kind == "integer" or _has_string_bound(alt)):
        return _first_values(validator, alt, _check_range(validator, alt, scrutinee_type),
                             seen)
    if isinstance(alt, LiteralPattern) and _literal_kind(alt.value) == kind:
        row = _check_literal(validator, alt, scrutinee_type)
        if row is None or isinstance(alt.value, int):
            return _first_values(validator, alt, row, seen)
        if alt.value in seen.strings:
            _report_repeat(validator, alt, seen.strings[alt.value])
            return _DROPPED
        seen.strings[alt.value] = alt.display
        return row
    if isinstance(alt, TuplePattern):
        _check_tuple_pattern(validator, alt, scrutinee_type, ("", None))
    elif isinstance(alt, (Pattern, LiteralPattern, RangePattern)):
        _reject_arm_kind(validator, alt, scrutinee_type)
    return None


def _first_values(validator: 'TypeValidator', alt: LiteralPattern | RangePattern,
                  row: Optional[Pat], seen: _Seen) -> Optional[Pat | _Dropped]:
    """An integer alternative against the values above it (CE2075), then recorded.

    One value that an arm above matches is CE2075, and the alternative is dropped: it
    adds nothing. A range that shares a value with an arm above, and also matches a
    value of its own, is CE2075 at the first shared value, and it keeps its row. A range
    that the arms above match all of adds nothing either: it is a dead arm, which the one
    checker reports (CE2118), so the one fault gives one diagnostic.
    """
    if not isinstance(row, Ctor) or not isinstance(row.key, IntRange):
        return row
    key = row.key
    shared = [(max(key.low, other.low), first) for other, first in seen.integers
              if other.overlaps(key)]
    if shared and (isinstance(alt, LiteralPattern)
                   or not covers([other for other, _first in seen.integers], key)):
        value, first = min(shared, key=lambda pair: pair[0])
        er.emit(validator.reporter, er.ERR.CE2075, alt.loc,
                value=_spell_value(value, _radix_of(alt)), first=first)
        if isinstance(alt, LiteralPattern):
            return _DROPPED
    seen.integers.append((key, pattern_source(alt)))
    return row


def _radix_of(alt: object) -> int:
    """The base an integer pattern is written in: the base of its first bound."""
    if isinstance(alt, RangePattern):
        return alt.low.radix
    return alt.radix if isinstance(alt, LiteralPattern) else 10


def _spell_value(value: int, radix: int) -> str:
    """An integer for a diagnostic: decimal, and the written base beside it (`143 (0x8f)`)."""
    if value < 0 or radix == 10:
        return str(value)
    prefix = {16: "x", 2: "b", 8: "o"}.get(radix)
    if prefix is None:
        return str(value)
    return f"{value} ({format(value, '#' + prefix)})"


def _report_repeat(validator: 'TypeValidator', alt: Pattern | LiteralPattern,
                   first: str) -> None:
    """An alternative that repeats one above it: CE2075 for a value, CE2041 for a variant.

    A string value prints as the first arm spells it, quotes included.
    """
    if isinstance(alt, LiteralPattern):
        er.emit(validator.reporter, er.ERR.CE2075, alt.loc, value=first, first=first)
    else:
        er.emit(validator.reporter, er.ERR.CE2041, alt.loc, variant=alt.variant_name)


def _add_row(arms: _ArmRows, idx: int, pattern: object,
             pieces: List[Tuple[object, Optional[Pat | _Dropped]]]) -> Optional[Pat]:
    """Give arm `idx` its row from the places of its alternatives, and answer it.

    An error in one alternative makes the coverage unknown. When every alternative is a
    reported repeat, the arm gets no row.
    """
    if any(piece is None for _alt, piece in pieces):
        arms.invalid = True
        arms.rows.append(None)
        return None
    kept = [(alt, piece) for alt, piece in pieces if isinstance(piece, (Wild, Ctor, Or))]
    if not kept:
        arms.reported.add(idx)
        arms.rows.append(None)
        return None
    row = _alternatives_row(pattern, kept)
    arms.rows.append(row)
    return row


def _alternatives_row(pattern: object, kept: List[Tuple[object, Pat]]) -> Pat:
    """The row place of a pattern's alternatives: one place, or an `Or` of them.

    The label of each alternative is the alternative and its span, for CE2118. A bare
    name has no span of its own, so it takes the span of the whole list.
    """
    if len(kept) == 1:
        return kept[0][1]
    return Or(tuple(piece for _alt, piece in kept),
              tuple((alt, getattr(alt, "loc", None) or getattr(pattern, "loc", None))
                    for alt, _piece in kept))


def _reject_arm_kind(validator: 'TypeValidator',
                     pattern: Pattern | LiteralPattern | RangePattern,
                     scrutinee_type: Type) -> None:
    """An arm whose kind does not fit the scrutinee (CE2076)."""
    if isinstance(pattern, LiteralPattern):
        arm_kind = f"{_literal_kind(pattern.value)} literal"
    elif isinstance(pattern, RangePattern):
        arm_kind = "integer range"
    else:
        arm_kind = "enum-pattern"
    report = er.emit_with(validator.reporter, er.ERR.CE2076, pattern.loc,
                          arm_kind=arm_kind, scrutinee_type=display_type(scrutinee_type))
    other_quote = _other_quote_form(pattern) if isinstance(pattern, LiteralPattern) else None
    if other_quote is not None:
        report = report.help(other_quote)
    report.emit()


def _other_quote_form(pattern: LiteralPattern) -> Optional[str]:
    """The help for an arm written in the wrong quote form, the byte `a'/'` against the
    string `'/'`, when the other form holds the same one character from 0 to 127."""
    if pattern.is_byte and isinstance(pattern.value, int) and pattern.value <= 127:
        return f"write `{pattern.display[1:]}` for a string arm"
    if (isinstance(pattern.value, str) and len(pattern.value) == 1
            and ord(pattern.value) <= 127):
        return f"write `a'{pattern.display[1:-1]}'` for the byte"
    return None


def _wildcard_row(validator: 'TypeValidator', arms: _ArmRows, idx: int, count: int,
                  pattern: WildcardPattern) -> None:
    """A `_` arm: it must be the last arm (CE2041), and it matches every value."""
    if idx != count - 1:
        er.emit(validator.reporter, er.ERR.CE2041, pattern.loc, variant="_")
        if arms.after_wildcard is None:
            arms.after_wildcard = idx + 1
    arms.rows.append(WILD)


def reject_other_enum(validator: 'TypeValidator', written: str,
                      span: Optional['Span'], subject: EnumType,
                      subject_label: str, subject_span: Optional['Span']) -> None:
    """A pattern names one enum, and the value it reads is another one (CE2107).

    Relational, because the pattern alone cannot say which enum the position holds.
    The note carries the value: the scrutinee for an outer arm, and the variant that
    declares the payload for a nested pattern.
    """
    spelling = display_type(subject)
    diagnostic = er.emit_with(validator.reporter, er.ERR.CE2107, span,
                              got=written, expected=spelling)
    if subject_span is not None:
        diagnostic.note_at(f"{subject_label} '{spelling}'", subject_span)
    diagnostic.emit()


def collect_and_validate_patterns(validator: 'TypeValidator', stmt: Match,
                                  scrutinee_type: Type) -> _ArmRows:
    """Validate each arm of an enum or tuple match, and give the checker its rows."""
    arms = _ArmRows()
    signatures: Set[str] = set()
    top_note: _Note = ("the value matched here is", stmt.scrutinee.loc)

    for idx, arm in enumerate(stmt.arms):
        pattern = arm.pattern

        if isinstance(pattern, WildcardPattern):
            _wildcard_row(validator, arms, idx, len(stmt.arms), pattern)
            _walk_arm_body(validator, arm)
            continue

        pieces = [(alt, _enum_alternative(validator, alt, scrutinee_type, top_note,
                                          signatures))
                  for alt in alternatives_of(pattern)]
        if isinstance(pattern, OrPattern) and all(piece is not None for _alt, piece in pieces):
            # A binding that differs between the alternatives leaves the arm body with no
            # one type for the name, so the arm is an error like a pattern with a fault.
            if _reject_binding_mismatch(validator, pattern, scrutinee_type):
                pieces.append((pattern, None))
        if _add_row(arms, idx, pattern, pieces) is None:
            continue

        saved_vars = validator.variable_types.copy()
        _register_bindings(validator, pattern, scrutinee_type)

        _walk_arm_body(validator, arm)

        validator.variable_types = saved_vars

    return arms


def _enum_alternative(validator: 'TypeValidator', alt: object, scrutinee_type: Type,
                      note: _Note, signatures: Set[str]) -> Optional[Pat | _Dropped]:
    """One top-level alternative of an enum or a tuple arm: its place in the row, or None
    after an error. A variant that an arm or an alternative above already names is
    CE2041."""
    if isinstance(alt, WildcardPattern):
        return WILD
    if isinstance(alt, (LiteralPattern, RangePattern)) or (
            isinstance(alt, Pattern) and not isinstance(scrutinee_type, EnumType)):
        # A literal or a range arm needs an integer (#415) or a string scrutinee, and an
        # enum pattern arm an enum scrutinee.
        _reject_arm_kind(validator, alt, scrutinee_type)
        return None
    if isinstance(alt, Pattern):
        signature = get_pattern_signature(alt)
        if signature in signatures and isinstance(scrutinee_type, EnumType) \
                and _names_the_scrutinee(validator, alt, scrutinee_type):
            _report_repeat(validator, alt, alt.variant_name)
            return _DROPPED
        signatures.add(signature)
    return _check_item(validator, alt, scrutinee_type, note)


def _names_the_scrutinee(validator: 'TypeValidator', pattern: Pattern,
                         scrutinee_type: EnumType) -> bool:
    """Does an arm's enum pattern name a variant of the scrutinee's own enum?"""
    return (_names_enum(validator, pattern.enum_name, scrutinee_type)
            and scrutinee_type.get_variant(pattern.variant_name) is not None)


def _names_enum(validator: 'TypeValidator', written: str, enum_type: EnumType) -> bool:
    """Does a pattern's written enum name name `enum_type`?

    For a generic enum the pattern uses the base name (`Maybe`), and the type carries its
    type arguments (`Maybe<i32>`).
    """
    if written == enum_type.name:
        return True
    return (written in validator.generic_enum_table.by_name
            and generic_base_of(enum_type) == written)


def _check_item(validator: 'TypeValidator', item: object, ty: Type,
                note: _Note) -> Optional[Pat]:
    """Validate one pattern position against the type of the value it reads.

    The answer is the position for the checker's matrix, or None after an error.
    """
    ty = _resolve(validator, ty)
    if isinstance(item, (str, RefBinding, NomBinding)):
        return WILD
    if isinstance(item, LiteralPattern):
        return _check_literal(validator, item, ty)
    if isinstance(item, RangePattern):
        return _check_range(validator, item, ty)
    if isinstance(item, TuplePattern):
        return _check_tuple_pattern(validator, item, ty, note)
    if isinstance(item, Pattern):
        return _check_enum_pattern(validator, item, ty, note)
    if isinstance(item, OwnPattern):
        return _check_own_pattern(validator, item, ty, note)
    if isinstance(item, OrPattern):
        return _check_alternatives(validator, item, ty, note)
    raise_internal_error("CE0121", pattern=pattern_source(item))
    return None


def _check_alternatives(validator: 'TypeValidator', pattern: OrPattern, ty: Type,
                        note: _Note) -> Optional[Pat]:
    """`|` alternatives in a pattern position: each one is a pattern of the position.

    An alternative that repeats a value or a variant of an earlier one is the repeat error
    of its kind (CE2075, CE2041), and each alternative binds what the first one binds
    (CE2126). An integer literal or range that shares a value with an earlier one is
    CE2075 by the rule of the top of an arm (`_first_values`).
    """
    pieces: List[Tuple[object, Optional[Pat | _Dropped]]] = []
    firsts: dict[str, object] = {}
    seen = _Seen()
    for alt in pattern.alternatives:
        piece: Optional[Pat | _Dropped] = _check_item(validator, alt, ty, note)
        if isinstance(alt, (LiteralPattern, RangePattern)) and isinstance(piece, Ctor) \
                and isinstance(piece.key, IntRange):
            pieces.append((alt, _first_values(validator, alt, piece, seen)))
            continue
        signature = _item_signature(alt)
        if piece is not None and isinstance(alt, (Pattern, LiteralPattern)) \
                and signature in firsts:
            first = firsts[signature]
            _report_repeat(validator, alt, getattr(first, "display", ""))
            piece = _DROPPED
        firsts.setdefault(signature, alt)
        pieces.append((alt, piece))
    if any(piece is None for _alt, piece in pieces):
        return None
    if _reject_binding_mismatch(validator, pattern, ty):
        return None
    return _alternatives_row(pattern, [(alt, piece) for alt, piece in pieces
                                       if isinstance(piece, (Wild, Ctor, Or))])


def _check_literal(validator: 'TypeValidator', pattern: LiteralPattern,
                   ty: Type) -> Optional[Pat]:
    """A literal pattern: a value of the literal's kind, and an integer literal that fits.

    A string literal reads a `string` value. An integer literal reads an integer value
    and must fit its type; its key is its value as that type reads it.
    """
    if isinstance(pattern.value, str):
        if ty != BuiltinType.STRING:
            er.emit(validator.reporter, er.ERR.CE2119, pattern.loc,
                    kind="a string", got=display_type(ty))
            return None
        return Ctor(string_key(pattern.value))
    if not _reads_an_integer(validator, pattern, ty) or not _bound_fits(validator, pattern, ty):
        return None
    value = literal_value(pattern, ty)
    return Ctor(IntRange(value, value))


def _check_range(validator: 'TypeValidator', pattern: RangePattern,
                 ty: Type) -> Optional[Pat]:
    """A range pattern: two integer bounds that fit the type at its position, going up.

    A string bound is CE2072, once for the range. A range reads an integer value (CE2119), each bound fits
    the type (CE2073), and a range whose start is above its end is CE2125. `5..5` is
    legal and matches no value: the checker reports it as a dead arm (CE2118).
    """
    if _has_string_bound(pattern):
        bound = pattern.low if isinstance(pattern.low.value, str) else pattern.high
        er.emit(validator.reporter, er.ERR.CE2072, bound.loc,
                got=display_type(BuiltinType.STRING), expected="an integer literal")
        return None
    if not _reads_an_integer(validator, pattern, ty):
        return None
    fits = [_bound_fits(validator, bound, ty) for bound in (pattern.low, pattern.high)]
    if not all(fits):
        return None
    if goes_down(pattern, ty):
        operator = "..=" if pattern.inclusive else ".."
        er.emit_with(validator.reporter, er.ERR.CE2125, pattern.loc,
                     range=pattern_source(pattern)) \
            .help(f"a range pattern goes up: write "
                  f"'{pattern.high.display}{operator}{pattern.low.display}'") \
            .emit()
        return None
    low, high = pattern_interval(pattern, ty)
    return Ctor(IntRange(low, high))


def _has_string_bound(pattern: RangePattern) -> bool:
    """Does a range pattern have a string bound (CE2072)?"""
    return any(isinstance(bound.value, str) for bound in (pattern.low, pattern.high))


def _reads_an_integer(validator: 'TypeValidator', pattern: LiteralPattern | RangePattern,
                      ty: Type) -> bool:
    """An integer literal or range reads an integer value (CE2119)."""
    if isinstance(ty, BuiltinType) and ty in _INTEGER_SCRUTINEES:
        return True
    er.emit(validator.reporter, er.ERR.CE2119, pattern.loc,
            kind="an integer", got=display_type(ty))
    return False


def _bound_fits(validator: 'TypeValidator', pattern: LiteralPattern, ty: Type) -> bool:
    """An integer literal fits the type it reads (CE2073), by the context-typed rule."""
    from sushi_lang.semantics.passes.types.inference import int_literal_fits

    if isinstance(pattern.value, int) and isinstance(ty, BuiltinType) \
            and int_literal_fits(pattern.value, pattern.radix, ty):
        return True
    er.emit(validator.reporter, er.ERR.CE2073, pattern.loc,
            literal=pattern.display, type=display_type(ty))
    return False


def _check_tuple_pattern(validator: 'TypeValidator', pattern: TuplePattern, ty: Type,
                         note: _Note) -> Optional[Pat]:
    """A tuple pattern: a tuple value, one item for each element (CE2117, CE2120)."""
    if not is_tuple_type(ty):
        er.emit(validator.reporter, er.ERR.CE2117, pattern.loc, type=display_type(ty))
        return None
    elements = tuple_elements(ty)
    if len(pattern.elements) != len(elements):
        er.emit(validator.reporter, er.ERR.CE2120, pattern.loc,
                count=len(pattern.elements), type=display_type(ty), arity=len(elements))
        return None
    spelling = display_type(ty)
    args = []
    for index, (item, element_type) in enumerate(zip(pattern.elements, elements, strict=True)):
        sub = _check_item(validator, item, element_type,
                          (f"element {index} of '{spelling}' is", note[1]))
        if sub is None:
            return None
        args.append(sub)
    return Ctor(TUPLE_KEY, tuple(args))


def _check_enum_pattern(validator: 'TypeValidator', pattern: Pattern, ty: Type,
                        note: _Note) -> Optional[Pat]:
    """An enum pattern: the enum of the value, one of its variants, and its payload."""
    # `geo.Sign.Plus ->`: the qualifier is checked here and folds away, so every rule
    # below reads the bare enum name (unit-namespaces.md section 5.2).
    if pattern.namespace is not None:
        from .qualified import reject_qualified_name
        if reject_qualified_name(validator, pattern.namespace, pattern.enum_name,
                                 pattern.enum_name_span or pattern.loc, kind="type"):
            return None

    if not isinstance(ty, EnumType):
        er.emit(validator.reporter, er.ERR.CE2108, pattern.loc, got=display_type(ty))
        return None

    if not _names_enum(validator, pattern.enum_name, ty):
        reject_other_enum(validator, pattern.enum_name,
                          pattern.enum_name_span or pattern.loc, ty, note[0], note[1])
        return None

    variant = ty.get_variant(pattern.variant_name)
    if variant is None:
        er.emit(validator.reporter, er.ERR.CE2045, pattern.variant_name_span or pattern.loc,
                variant=pattern.variant_name, enum=display_type(ty))
        return None

    if len(variant.associated_types) != len(pattern.bindings):
        er.emit(validator.reporter, er.ERR.CE2044, pattern.loc,
                variant=pattern.variant_name,
                expected=len(variant.associated_types),
                got=len(pattern.bindings))
        return None

    payload_note: _Note = (f"variant '{pattern.variant_name}' of '{display_type(ty)}' carries",
                           pattern.variant_name_span or pattern.loc)
    args = []
    for binding, binding_type in zip(pattern.bindings, variant.associated_types, strict=True):
        sub = _check_item(validator, binding, binding_type, payload_note)
        if sub is None:
            return None
        args.append(sub)
    return Ctor(pattern.variant_name, tuple(args))


def _check_own_pattern(validator: 'TypeValidator', pattern: OwnPattern, ty: Type,
                       note: _Note) -> Optional[Pat]:
    """An `Own(...)` pattern: an `Own@(T)` value, and a pattern of its pointee."""
    if not is_own_type(ty):
        er.emit(validator.reporter, er.ERR.CE2109, pattern.loc, got=display_type(ty))
        return None
    if isinstance(pattern.inner_pattern, str):
        return WILD
    element_type = own_payload_type(ty)
    if element_type is None:
        er.emit(validator.reporter, er.ERR.CE2109, pattern.loc, got=display_type(ty))
        return None
    sub = _check_item(validator, pattern.inner_pattern, element_type, note)
    return None if sub is None else Ctor(OWN_KEY, (sub,))


def _register_bindings(validator: 'TypeValidator', item: object, ty: Type) -> None:
    """Register the names a pattern item binds in `variable_types`, with their types.

    A `peek` / `poke` binding (#300) IS a reference into the scrutinee's storage, so it
    gets the reference type: every consumer that asks "is this name a borrow?" answers
    truthfully, and inference auto-derefs the name. A `nom` binding (ruling R11) OWNS the
    payload and has the payload's own type, as a bare binding has.
    """
    for bound in _bindings_of(validator, item, ty):
        if bound.mode in ("peek", "poke"):
            validator.variable_types[bound.name] = ReferenceType(bound.ty,
                                                                 borrow_mode(bound.mode))
        else:
            validator.variable_types[bound.name] = bound.ty


def _bindings_of(validator: 'TypeValidator', item: object, ty: Type) -> List[_Bound]:
    """The names a pattern item binds, in source order, each with its type and mode.

    `|` alternatives bind what their first alternative binds: `_reject_binding_mismatch`
    holds the others to it.
    """
    ty = _resolve(validator, ty)
    if isinstance(item, NomBinding):
        return [_Bound(item.name, ty, "nom")]
    if isinstance(item, RefBinding):
        return [_Bound(item.name, ty, item.mode)]
    if isinstance(item, str):
        return [] if item == "_" else [_Bound(item, ty, "bare")]
    if isinstance(item, OrPattern):
        return _bindings_of(validator, item.alternatives[0], ty)
    if isinstance(item, Pattern):
        variant = ty.get_variant(item.variant_name) if isinstance(ty, EnumType) else None
        if variant is None:
            return []
        return [bound for binding, binding_type in zip(item.bindings, variant.associated_types,
                                                       strict=False)
                for bound in _bindings_of(validator, binding, binding_type)]
    if isinstance(item, TuplePattern):
        return [bound for element, element_type in zip(item.elements, tuple_elements(ty),
                                                       strict=False)
                for bound in _bindings_of(validator, element, element_type)]
    if isinstance(item, OwnPattern):
        pointee = own_payload_type(ty)
        if pointee is None:
            return []
        inner = item.inner_pattern
        if isinstance(inner, str) and inner != "_" and item.inner_borrow is not None:
            # `Own(poke x)` (#300 phase 1): the binding IS a reference to the pointee.
            return [_Bound(inner, _resolve(validator, pointee), item.inner_borrow)]
        return _bindings_of(validator, inner, pointee)
    return []


def _reject_binding_mismatch(validator: 'TypeValidator', pattern: OrPattern,
                             ty: Type) -> bool:
    """CE2126: an alternative that binds other names, types or modes than the first one.

    The arm body reads one binding, whatever alternative matched, so every alternative
    gives it the same names with the same types and the same modes. One error for each
    alternative that differs, with a note at the first alternative. True when reported.
    """
    head = pattern.alternatives[0]
    first = {bound.name: bound for bound in _bindings_of(validator, head, ty)}
    reported = False
    for alt in pattern.alternatives[1:]:
        here = {bound.name: bound for bound in _bindings_of(validator, alt, ty)}
        detail = _binding_difference(first, here)
        if detail is None:
            continue
        diagnostic = er.emit_with(validator.reporter, er.ERR.CE2126,
                                  getattr(alt, "loc", None) or pattern.loc,
                                  alternative=pattern_source(alt), detail=detail)
        head_span = getattr(head, "loc", None) or pattern.loc
        if head_span is not None:
            names = ", ".join(f"'{name}'" for name in first) or "no name"
            diagnostic.note_at(f"the first alternative '{pattern_source(head)}' binds {names}",
                               head_span)
        diagnostic.emit()
        reported = True
    return reported


def _binding_difference(first: dict[str, _Bound], here: dict[str, _Bound]) -> Optional[str]:
    """How the bindings of an alternative differ from the first one's, or None."""
    for name in first:
        if name not in here:
            return f"'{name}' is not bound here"
    for name in here:
        if name not in first:
            return f"'{name}' is not bound by the first alternative"
    for name, bound in here.items():
        other = first[name]
        if bound.ty != other.ty:
            return (f"'{name}' is '{display_type(bound.ty)}' here and "
                    f"'{display_type(other.ty)}' in the first alternative")
        if bound.mode != other.mode:
            return (f"'{name}' is {_MODE_WORDS[bound.mode]} here and "
                    f"{_MODE_WORDS[other.mode]} in the first alternative")
    return None


def check_match_exhaustiveness(validator: 'TypeValidator', stmt: Match, scrutinee_type: Type,
                               arms: _ArmRows) -> None:
    """Run the one checker over the arms: the missing patterns, and the dead arms."""
    if arms.invalid:
        return
    result = analyze(lambda ty: _signature(validator, ty), arms.rows, scrutinee_type)

    if result.missing:
        stmt.not_exhaustive = True
        kind = _scrutinee_literal_kind(scrutinee_type)
        if kind is not None:
            er.emit(validator.reporter, er.ERR.CE2074, stmt.loc, kind=kind,
                    missing=_first_missing(stmt, scrutinee_type, result.missing[0]))
        else:
            er.emit(validator.reporter, er.ERR.CE2040, stmt.loc,
                    variants=_render_missing(validator, result.missing, scrutinee_type,
                                             arms.rows))

    for index, covering in result.dead:
        if index in arms.reported or (arms.after_wildcard is not None
                                      and index >= arms.after_wildcard):
            continue
        dead = stmt.arms[index].pattern
        diagnostic = er.emit_with(validator.reporter, er.ERR.CE2118, dead.loc,
                                  pattern=pattern_source(dead))
        _note_covering_arms(diagnostic, stmt, covering)
        _help_for_an_empty_range(diagnostic, dead, scrutinee_type)
        diagnostic.emit()

    for dead_alternative in result.dead_alternatives:
        if dead_alternative.arm in arms.reported or (
                arms.after_wildcard is not None
                and dead_alternative.arm >= arms.after_wildcard):
            continue
        alt, span = dead_alternative.label
        diagnostic = er.emit_with(validator.reporter, er.ERR.CE2118, span,
                                  pattern=pattern_source(alt))
        _note_covering_arms(diagnostic, stmt, dead_alternative.arms)
        _help_for_an_empty_range(diagnostic, alt, scrutinee_type)
        for earlier, earlier_span in dead_alternative.alternatives:
            if earlier_span is not None:
                diagnostic.note_at(f"the alternative '{pattern_source(earlier)}' matches "
                                   f"these values first", earlier_span)
        diagnostic.emit()


def _first_missing(stmt: Match, ty: Type, witness: Pat) -> str:
    """The `{missing}` slot of CE2074: the first integer value that no arm matches, in the
    base of the first integer arm, or the other strings."""
    if ty == BuiltinType.STRING:
        return "every other string"
    if isinstance(witness, Ctor) and isinstance(witness.key, IntRange):
        value = witness.key.low
    else:
        bounds = integer_range(ty)
        value = bounds[0] if bounds is not None else 0
    radix = next((_radix_of(alt) for arm in stmt.arms for alt in alternatives_of(arm.pattern)
                  if isinstance(alt, (LiteralPattern, RangePattern))), 10)
    return f"the value {_spell_value(value, radix)}"


def _help_for_an_empty_range(diagnostic, pattern: object, ty: Type) -> None:
    """A range like `5..5` at the top of an arm matches no value: say so."""
    if not isinstance(pattern, RangePattern) or _has_string_bound(pattern) \
            or integer_range(ty) is None:
        return
    low, high = pattern_interval(pattern, ty)
    if low > high:
        diagnostic.help(f"'{pattern_source(pattern)}' matches no value: '{pattern.low.display}"
                        f"..={pattern.low.display}' matches the one value "
                        f"{pattern.low.display}")


def _note_covering_arms(diagnostic, stmt: Match, covering: List[int]) -> None:
    """A note at each arm above that matches the values of a dead pattern first."""
    for above in covering:
        cover = stmt.arms[above].pattern
        if cover.loc is not None:
            diagnostic.note_at(
                f"the arm '{pattern_source(cover)}' matches these values first",
                cover.loc)


def _signature(validator: 'TypeValidator', ty: Type) -> Signature:
    """The constructors of a column type for the checker, or None when they cannot be listed."""
    ty = _resolve(validator, ty)
    if isinstance(ty, EnumType):
        return [(variant.name, tuple(variant.associated_types)) for variant in ty.variants]
    if is_tuple_type(ty):
        return [(TUPLE_KEY, tuple_elements(ty))]
    if is_own_type(ty):
        payload = own_payload_type(ty)
        return None if payload is None else [(OWN_KEY, (payload,))]
    bounds = integer_range(ty)
    if bounds is not None:
        return IntegerColumn(*bounds)
    return None


def _render_missing(validator: 'TypeValidator', missing: List[Pat], ty: Type,
                    rows: List[Optional[Pat]]) -> str:
    """The `{variants}` slot of CE2040: the missing patterns, in source syntax.

    A plain enum match, where no arm tests inside a payload, lists the names of the
    missing variants as it always has.
    """
    if isinstance(ty, EnumType) and all(_is_shallow(row) for row in rows + missing):
        return ", ".join(sorted(str(w.key) for w in missing if isinstance(w, Ctor)))
    return ", ".join(_render_witness(validator, w, ty) for w in missing)


def _is_shallow(row: Optional[Pat]) -> bool:
    """Does a pattern test no more than an outer variant?"""
    if isinstance(row, Or):
        return all(_is_shallow(alt) for alt in row.alts)
    return not isinstance(row, Ctor) or all(isinstance(a, Wild) for a in row.args)


def _render_witness(validator: 'TypeValidator', pat: Pat, ty: Type) -> str:
    """One missing pattern in source syntax: `Maybe.Some(Color.Green)`, `(_, Color.Red)`,
    `(2..=2147483647, _)`.

    A witness is built from constructors and `_` alone; it holds no `Or`. An integer
    position is an interval that no arm matches, a value or a range.
    """
    if not isinstance(pat, Ctor):
        return "_"
    ty = _resolve(validator, ty)
    if pat.key == TUPLE_KEY:
        return display_tuple(_render_witness(validator, a, t)
                             for a, t in zip(pat.args, tuple_elements(ty), strict=False))
    if pat.key == OWN_KEY:
        payload = own_payload_type(ty)
        inner = _render_witness(validator, pat.args[0], payload) if payload else "_"
        return f"Own({inner})"
    if isinstance(ty, EnumType):
        variant = ty.get_variant(str(pat.key))
        head = f"{generic_base_of(ty) or ty.name}.{pat.key}"
        if variant is None or not variant.associated_types:
            return head
        inner = ", ".join(_render_witness(validator, a, t)
                          for a, t in zip(pat.args, variant.associated_types, strict=False))
        return f"{head}({inner})"
    if isinstance(pat.key, IntRange):
        if pat.key.low == pat.key.high:
            return str(pat.key.low)
        return f"{pat.key.low}..={pat.key.high}"
    return str(pat.key)


def get_pattern_signature(pattern: 'Pattern') -> str:
    """Generate a unique signature for a pattern including nested and Own patterns."""
    signature = pattern.variant_name
    if pattern.bindings:
        signature += "(" + ",".join(_item_signature(b) for b in pattern.bindings) + ")"
    return signature


def _item_signature(item: object) -> str:
    """The signature of one payload position; every binding reads as `_`.

    A RefBinding (#300 phase 3) and a NomBinding (ruling R11) bind like a plain name: the
    marker changes how the binding is materialized, not which values the arm matches, so
    `Poly(p)`, `Poly(poke p)` and `Poly(nom p)` are duplicates of each other.
    """
    if isinstance(item, Pattern):
        return get_pattern_signature(item)
    if isinstance(item, TuplePattern):
        return "(" + ",".join(_item_signature(e) for e in item.elements) + ")"
    if isinstance(item, LiteralPattern):
        # A string keeps its quotes, so `"1"` is not the integer 1 and `"_"` is not `_`.
        return repr(item.value) if isinstance(item.value, str) else str(item.value)
    if isinstance(item, RangePattern):
        operator = "..=" if item.inclusive else ".."
        return f"{_item_signature(item.low)}{operator}{_item_signature(item.high)}"
    if isinstance(item, OwnPattern):
        if isinstance(item.inner_pattern, str):
            return "Own"
        return f"Own({_item_signature(item.inner_pattern)})"
    if isinstance(item, OrPattern):
        return "|".join(_item_signature(alt) for alt in item.alternatives)
    return "_"
