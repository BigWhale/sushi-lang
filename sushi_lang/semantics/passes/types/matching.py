"""Pattern matching in the typecheck pass (docs/design/ir.md 7.8).

`matchable_type` is the rule of the scrutinee (CE2048). A match on an integer (#415) or a
string checks each arm with `check_literal_arm`: each alternative is a literal or a range of
the kind of the scrutinee (CE2076), and a repeated value is CE2075. A match on an enum or a
tuple checks each arm with `check_pattern_arm`: each alternative is a pattern at the type of
the scrutinee, a variant that an arm above names again is CE2041, the alternatives of `|`
bind the same names (CE2126), and each binder gets the type of its position, with no `peek`
or `poke`. `reject_poke_binding_into_a_constant` refuses a `poke` binding into a constant
(CE2400).

The pattern arms of the table of the pass (`visit.PATTERN_ARMS`) take the type of their
position and a note that says where that type comes from (for the relational CE2107). Each
one answers the place of the pattern in the matrix of the one checker (`exhaustiveness.py`),
or None after an error. A literal and the bounds of a range are values of that type
(`semantics/integer_patterns.py`). `check_match_exhaustiveness` runs the one checker over the
rows of the arms (CE2040, CE2074, CE2118).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, List, Optional, Set, Tuple

from sushi_lang.semantics.type_predicates import generic_base_of
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.passes.collect.constants import ConstSig
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.inference import int_literal_fits
from sushi_lang.semantics.passes.types.qualified import reject_qualified_name
from sushi_lang.semantics.passes.types.visibility import name_is_contested
from sushi_lang.semantics.passes.types.visit.expressions import constant_record
from sushi_lang.semantics.typesys import (
    BorrowMode, BuiltinType, EnumType, ReferenceType, StructType, Type,
)
from sushi_lang.semantics.constant_borrow import reject_borrow_of_constant
from sushi_lang.semantics.ownership import is_own_type
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.generics.own import own_payload_type
from sushi_lang.semantics.generics.tuples import display_tuple, is_tuple_type, tuple_elements
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.integer_patterns import goes_down, literal_value, pattern_interval
from sushi_lang.semantics.integer_width import integer_range
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir.exprs import Constant, IntConst, StrConst
from sushi_lang.semantics.shir.ids import LocalId
from sushi_lang.semantics.shir.spelling import spell_pattern, spell_range
from sushi_lang.semantics.shir.stmts import Ref
from .exhaustiveness import (
    OWN_KEY, TUPLE_KEY, WILD, Ctor, IntegerColumn, IntRange, Or, Pat, Signature, Wild,
    analyze, covers, string_key,
)
from .utils import resolve_declared_type
from sushi_lang.semantics.type_predicates import BUILTIN_INTEGER_TYPES

# The scrutinee types an integer literal match accepts (#415).
_INTEGER_SCRUTINEES = BUILTIN_INTEGER_TYPES


if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.internals.report import Span
    from sushi_lang.semantics.shir import stmts as s

#: Where the type of a pattern position comes from, for the relational note of CE2107: a
#: label and the span it points at.
PositionNote = Tuple[str, Optional["Span"]]


def _resolve(validator: 'TypeValidator', ty: Type) -> Type:
    """The type a scrutinee or a pattern binding stands for.

    A variant's associated type can still be a NAME when the pattern rules read it, so
    every reader of one asks this first. It ran nine times by hand, each copy naming both
    tables (#742), and then it answered the pass's own question with a body of its own
    (#755): `resolve_declared_type` is that answer.
    """
    resolved = resolve_declared_type(validator, ty)
    return resolved if resolved is not None else ty


class _Dropped:
    """An alternative that an older rule reported as a repeat (CE2041, CE2075).

    It gets no place in the row, so the dead-pattern error does not speak again.
    """


_DROPPED = _Dropped()


@dataclass(frozen=True)
class _Bound:
    """One name a pattern binds: its local, the type of its position and its mode."""
    local: LocalId
    name: str
    ty: Type
    mode: str                 # "bare" | "peek" | "poke" | "nom"


_MODE_WORDS = {"bare": "a bare binding", "peek": "a `peek` binding",
               "poke": "a `poke` binding", "nom": "a `nom` binding"}


@dataclass
class _Seen:
    """The values that the arms and alternatives above already match, for CE2075.

    A string is one value. An integer literal and a range are an interval of the
    scrutinee type, each with the spelling of the pattern that matches it first.
    """
    strings: dict[str, str] = field(default_factory=dict)
    integers: List[Tuple[IntRange, str]] = field(default_factory=list)


@dataclass
class ArmRows:
    """What the arms of one match give the checker.

    `rows[i]` is arm i's pattern for the matrix, or None for an arm left out (a duplicate
    that CE2041 or CE2075 reported). `reported` holds the arms whose own rule spoke, so the
    dead-arm error does not speak again. `invalid` says that an arm has an error in its
    pattern: the checker then gives no answer, because the coverage is not known. `values`
    holds the values that the literal arms above match, for CE2075, and `variants` the
    variant patterns that the arms above name, for CE2041.
    """
    rows: List[Optional[Pat]] = field(default_factory=list)
    reported: Set[int] = field(default_factory=set)
    invalid: bool = False
    after_wildcard: Optional[int] = None
    values: _Seen = field(default_factory=_Seen)
    variants: Set[str] = field(default_factory=set)


# -- the scrutinee, and the arms of a match on an integer or a string --------------

def literal_kind(ty: Type) -> Optional[str]:
    """The literal kind that a scrutinee type takes, or None when it takes no literal arm."""
    if ty == BuiltinType.STRING:
        return "string"
    if ty in _INTEGER_SCRUTINEES:
        return "integer"
    return None


def _constant_kind(const: Constant) -> str:
    """The kind of the value of a literal pattern, for the `{kind}` slots."""
    return "string" if isinstance(const, StrConst) else "integer"


def _alternatives(pattern: p.ShirPattern) -> List[p.ShirPattern | p.PatBinding]:
    """The alternatives of the pattern of an arm: its own list for `|`, else itself."""
    if isinstance(pattern, p.Or):
        return list(pattern.alternatives)
    return [pattern]


def matchable_type(validator: 'TypeValidator', match: 's.Match',
                   ty: Optional[Type]) -> Optional[Type]:
    """The type of the scrutinee `ty` of a match: an enum, an integer (#415), a string or a
    tuple. None after an error: the scrutinee has one, its type has no match (CE2048), or
    its type name is one that the unit lost."""
    if ty is None:
        return None
    ty = _resolve(validator, ty)
    if isinstance(ty, ReferenceType) and is_tuple_type(ty):
        ty = _resolve(validator, ty.referenced_type)
    if isinstance(ty, EnumType):
        # An enum name this unit lost (D2): the CE0004, CE2046 or CE3011 of its declaration
        # is the one fault, and its arms are not read against the winner's variants.
        return None if name_is_contested(validator, "enum", ty.name) else ty
    if literal_kind(ty) is not None:
        return ty
    if isinstance(ty, StructType) and is_tuple_type(ty):
        return ty
    # A type name this unit lost (#863): the value has the type of the winner, and the
    # CE0004 or CE0006 of the declaration is the one fault.
    if isinstance(ty, StructType) and name_is_contested(validator, "struct", ty.name):
        return None
    er.emit(validator.reporter, er.ERR.CE2048, match.scrutinee.loc, got=display_type(ty))
    return None


def check_literal_arm(validator: 'TypeValidator', rows: 'ArmRows', match: 's.Match',
                      index: int, ty: Type) -> None:
    """The pattern of arm `index` of a match on an integer (#415) or a string, and its row.

    Each alternative is an arm of the kind of the scrutinee (CE2076). An integer literal
    takes the type of the scrutinee under the fit rule of a context-typed literal (CE2073),
    so a non-decimal literal is a bit pattern. Duplicates are duplicates by VALUE: `0x2a` and
    `42` are one value, `0xff` and `-1` are one value on an i8, and `"a"` and `'a'` are one
    value (CE2075). A `_` arm is the last arm (CE2041).
    """
    pattern = match.arms[index].pattern
    if isinstance(pattern, p.Wildcard):
        _wildcard_row(validator, rows, index, len(match.arms), pattern)
        return
    kind = literal_kind(ty)
    _add_row(rows, index, pattern,
             [(alt, _literal_alternative(validator, alt, ty, kind, rows.values))
              for alt in _alternatives(pattern)])


def _literal_alternative(validator: 'TypeValidator', alt: p.ShirPattern | p.PatBinding,
                         ty: Type, kind: Optional[str],
                         seen: _Seen) -> Optional[Pat | _Dropped]:
    """One top-level alternative of a literal arm: its place in the row, or None after an
    error. A value that an arm or an alternative above already matches is CE2075."""
    if isinstance(alt, p.Wildcard):
        return visit.check_pattern(validator, alt, ty)
    if isinstance(alt, p.IntRange) and (kind == "integer" or _has_string_bound(alt)):
        return _first_values(validator, alt, visit.check_pattern(validator, alt, ty), seen)
    if isinstance(alt, p.Literal) and _constant_kind(alt.value) == kind:
        row = visit.check_pattern(validator, alt, ty)
        if row is None or not isinstance(alt.value, StrConst):
            return _first_values(validator, alt, row, seen)
        first = seen.strings.get(alt.value.value)
        if first is not None:
            er.emit(validator.reporter, er.ERR.CE2075, alt.loc, value=first, first=first)
            return _DROPPED
        seen.strings[alt.value.value] = alt.written
        return row
    if isinstance(alt, p.Tuple):
        er.emit(validator.reporter, er.ERR.CE2117, alt.loc, type=display_type(ty))
    elif isinstance(alt, (p.Variant, p.Literal, p.IntRange)):
        _reject_arm_kind(validator, alt, ty)
    else:
        raise_internal_error("CE0000", span=getattr(alt, "loc", None),
                             detail=f"no arm of a literal match is a {type(alt).__name__}")
    return None


def _reject_arm_kind(validator: 'TypeValidator', pattern: p.Literal | p.IntRange | p.Variant,
                     scrutinee_type: Type) -> None:
    """An arm whose kind does not fit the scrutinee (CE2076)."""
    if isinstance(pattern, p.Literal):
        arm_kind = f"{_constant_kind(pattern.value)} literal"
    elif isinstance(pattern, p.IntRange):
        arm_kind = "integer range"
    else:
        arm_kind = "enum-pattern"
    report = er.emit_with(validator.reporter, er.ERR.CE2076, pattern.loc,
                          arm_kind=arm_kind, scrutinee_type=display_type(scrutinee_type))
    other_quote = _other_quote_form(pattern) if isinstance(pattern, p.Literal) else None
    if other_quote is not None:
        report = report.help(other_quote)
    report.emit()


def _other_quote_form(pattern: p.Literal) -> Optional[str]:
    """The help for an arm written in the wrong quote form, the byte `a'/'` against the
    string `'/'`, when the other form holds the same one character from 0 to 127. The help
    gives the source text of the arm in the other quotes."""
    const = pattern.value
    if isinstance(const, IntConst) and const.byte_spelling is not None and const.value <= 127:
        return f"write `{pattern.written[1:]}` for a string arm"
    if isinstance(const, StrConst) and len(const.value) == 1 and ord(const.value) <= 127:
        return f"write `a'{pattern.written[1:-1]}'` for the byte"
    return None


def check_wildcard_pattern(_validator: 'TypeValidator', _node: p.Wildcard,
                           _ty: Optional[Type], _note: Optional[PositionNote] = None) -> Pat:
    """`_` matches every value of its position."""
    return WILD


def check_literal_pattern(validator: 'TypeValidator', node: p.Literal, ty: Type,
                          _note: Optional[PositionNote] = None) -> Optional[Pat]:
    """A literal pattern: a value of the kind of the literal, and an integer literal that
    fits.

    A string literal reads a `string` value (CE2119). An integer literal reads an integer
    value (CE2119) and fits its type (CE2073). Its key is its value as that type reads it.
    """
    if isinstance(node.value, StrConst):
        if ty != BuiltinType.STRING:
            er.emit(validator.reporter, er.ERR.CE2119, node.loc,
                    kind="a string", got=display_type(ty))
            return None
        return Ctor(string_key(node.value.value))
    if not _reads_an_integer(validator, node.loc, ty) \
            or not _bound_fits(validator, node.value, node.written, node.loc, ty):
        return None
    value = literal_value(node.value, ty)
    return Ctor(IntRange(value, value))


def check_range_pattern(validator: 'TypeValidator', node: p.IntRange, ty: Type,
                        _note: Optional[PositionNote] = None) -> Optional[Pat]:
    """A range pattern: two integer bounds that fit the type at its position, going up.

    A string bound is CE2072, once for the range. A range reads an integer value (CE2119),
    each bound fits the type (CE2073), and a range whose start is above its end is CE2125.
    `5..5` is legal and matches no value: the checker reports it as a dead arm (CE2118).
    """
    if _has_string_bound(node):
        loc = node.low.loc if isinstance(node.low.value, StrConst) else node.high.loc
        er.emit(validator.reporter, er.ERR.CE2072, loc,
                got=display_type(BuiltinType.STRING), expected="an integer literal")
        return None
    if not _reads_an_integer(validator, node.loc, ty):
        return None
    fits = [_bound_fits(validator, bound.value, bound.written, bound.loc, ty)
            for bound in (node.low, node.high)]
    if not all(fits):
        return None
    if goes_down(node, ty):
        operator = "..=" if node.inclusive else ".."
        er.emit_with(validator.reporter, er.ERR.CE2125, node.loc,
                     range=spell_range(node)) \
            .help(f"a range pattern goes up: write "
                  f"'{node.high.written}{operator}{node.low.written}'") \
            .emit()
        return None
    low, high = pattern_interval(node, ty)
    return Ctor(IntRange(low, high))


def _has_string_bound(pattern: p.IntRange) -> bool:
    """Does a range pattern have a string bound (CE2072)?"""
    return any(isinstance(bound.value, StrConst) for bound in (pattern.low, pattern.high))


def _reads_an_integer(validator: 'TypeValidator', loc: 'Span', ty: Type) -> bool:
    """An integer literal or range reads an integer value (CE2119)."""
    if isinstance(ty, BuiltinType) and ty in _INTEGER_SCRUTINEES:
        return True
    er.emit(validator.reporter, er.ERR.CE2119, loc, kind="an integer", got=display_type(ty))
    return False


def _bound_fits(validator: 'TypeValidator', const: Constant, written: str, loc: 'Span',
                ty: Type) -> bool:
    """An integer literal fits the type it reads (CE2073), by the context-typed rule. The
    diagnostic quotes the source text of the literal."""
    if not isinstance(const, IntConst):
        raise_internal_error("CE0000", span=loc,
                             detail="an integer pattern holds no integer")
        return False
    if isinstance(ty, BuiltinType) and int_literal_fits(const.value, const.radix, ty):
        return True
    er.emit(validator.reporter, er.ERR.CE2073, loc, literal=written,
            type=display_type(ty))
    return False


# -- the patterns of a match on an enum or a tuple ------------------------------------

def check_pattern_arm(validator: 'TypeValidator', rows: ArmRows, match: 's.Match',
                      index: int, ty: Type) -> bool:
    """The pattern of arm `index` of a match on an enum or a tuple, its row, and the type of
    each binder. True when the arm walks its body: an arm whose pattern has an error does
    not.

    Each alternative is a pattern of the scrutinee type. A variant that an arm or an
    alternative above names again is CE2041. The alternatives of `|` bind the same names
    with the same types and modes (CE2126), so the body reads one binding list.
    """
    pattern = match.arms[index].pattern
    if isinstance(pattern, p.Wildcard):
        _wildcard_row(validator, rows, index, len(match.arms), pattern)
        return True
    note: PositionNote = ("the value matched here is", match.scrutinee.loc)
    pieces = [(alt, _pattern_alternative(validator, alt, ty, note, rows.variants))
              for alt in _alternatives(pattern)]
    if (isinstance(pattern, p.Or) and all(piece is not None for _alt, piece in pieces)
            and _reject_binding_mismatch(validator, pattern, ty)):
        pieces.append((pattern, None))
    if _add_row(rows, index, pattern, pieces) is None:
        return False
    _record_binders(validator, pattern, ty)
    return True


def _pattern_alternative(validator: 'TypeValidator', alt: p.ShirPattern | p.PatBinding,
                         ty: Type, note: PositionNote,
                         variants: Set[str]) -> Optional[Pat | _Dropped]:
    """One top-level alternative of an enum or a tuple arm: its place in the row, or None
    after an error. A literal or a range arm needs an integer or a string scrutinee, and an
    enum pattern arm an enum scrutinee (CE2076)."""
    if isinstance(alt, p.Wildcard):
        return WILD
    if isinstance(alt, (p.Literal, p.IntRange)) or (
            isinstance(alt, p.Variant) and not isinstance(ty, EnumType)):
        _reject_arm_kind(validator, alt, ty)
        return None
    if isinstance(alt, p.Variant):
        signature = _item_signature(alt)
        if signature in variants and isinstance(ty, EnumType) \
                and _names_the_scrutinee(validator, alt, ty):
            er.emit(validator.reporter, er.ERR.CE2041, alt.loc, variant=alt.variant)
            return _DROPPED
        variants.add(signature)
    return _check_item(validator, alt, ty, note)


def _names_the_scrutinee(validator: 'TypeValidator', pattern: p.Variant,
                         scrutinee_type: EnumType) -> bool:
    """Does an arm's enum pattern name a variant of the scrutinee's own enum?"""
    return (_names_enum(validator, _enum_name(validator, pattern), scrutinee_type)
            and scrutinee_type.get_variant(pattern.variant) is not None)


def _enum_name(validator: 'TypeValidator', pattern: p.Variant) -> str:
    """The written enum name of a pattern, without its alias."""
    return validator.decl_name(pattern.decl).rpartition(".")[2]


def _names_enum(validator: 'TypeValidator', written: str, enum_type: EnumType) -> bool:
    """Does a pattern's written enum name name `enum_type`?

    For a generic enum the pattern uses the base name (`Maybe`), and the type carries its
    type arguments (`Maybe<i32>`).
    """
    if written == enum_type.name:
        return True
    return (written in validator.generic_enum_table.by_name
            and generic_base_of(enum_type) == written)


def _check_item(validator: 'TypeValidator', item: p.ShirPattern | p.PatBinding, ty: Type,
                note: Optional[PositionNote]) -> Optional[Pat]:
    """One pattern position at the type of the value it reads: its place in the matrix of
    the one checker, or None after an error. A binding matches every value."""
    ty = _resolve(validator, ty)
    if isinstance(item, p.PatBinding):
        return WILD
    return visit.check_pattern(validator, item, ty, note)


def check_variant_pattern(validator: 'TypeValidator', node: p.Variant, ty: Type,
                          note: Optional[PositionNote] = None) -> Optional[Pat]:
    """An enum pattern: the enum of the value (CE2108, CE2107), one of its variants
    (CE2045), and one item for each value of its payload (CE2044). Behind an alias, the
    namespace holds the enum (CE2001, CE3005)."""
    alias, dot, name = validator.decl_name(node.decl).rpartition(".")
    if dot and reject_qualified_name(validator, alias, name, node.enum_loc, kind="type"):
        return None
    if not isinstance(ty, EnumType):
        er.emit(validator.reporter, er.ERR.CE2108, node.loc, got=display_type(ty))
        return None
    if not _names_enum(validator, name, ty):
        label, span = note if note is not None else ("", None)
        reject_other_enum(validator, name, node.enum_loc, ty, label, span)
        return None
    variant = ty.get_variant(node.variant)
    if variant is None:
        er.emit(validator.reporter, er.ERR.CE2045, node.variant_loc,
                variant=node.variant, enum=display_type(ty))
        return None
    if len(variant.associated_types) != len(node.items):
        er.emit(validator.reporter, er.ERR.CE2044, node.loc, variant=node.variant,
                expected=len(variant.associated_types), got=len(node.items))
        return None
    payload: PositionNote = (f"variant '{node.variant}' of '{display_type(ty)}' carries",
                             node.variant_loc)
    args = []
    for item, item_type in zip(node.items, variant.associated_types, strict=True):
        sub = _check_item(validator, item, item_type, payload)
        if sub is None:
            return None
        args.append(sub)
    return Ctor(node.variant, tuple(args))


def check_tuple_pattern(validator: 'TypeValidator', node: p.Tuple, ty: Type,
                        note: Optional[PositionNote] = None) -> Optional[Pat]:
    """A tuple pattern: a tuple value (CE2117), one item for each element (CE2120)."""
    if not is_tuple_type(ty):
        er.emit(validator.reporter, er.ERR.CE2117, node.loc, type=display_type(ty))
        return None
    elements = tuple_elements(ty)
    if len(node.elements) != len(elements):
        er.emit(validator.reporter, er.ERR.CE2120, node.loc,
                count=len(node.elements), type=display_type(ty), arity=len(elements))
        return None
    spelling = display_type(ty)
    span = note[1] if note is not None else None
    args = []
    for index, (item, element_type) in enumerate(zip(node.elements, elements, strict=True)):
        sub = _check_item(validator, item, element_type,
                          (f"element {index} of '{spelling}' is", span))
        if sub is None:
            return None
        args.append(sub)
    return Ctor(TUPLE_KEY, tuple(args))


def check_own_pattern(validator: 'TypeValidator', node: p.Own, ty: Type,
                      note: Optional[PositionNote] = None) -> Optional[Pat]:
    """An `Own(...)` pattern: an `Own@(T)` value (CE2109), and a pattern of its pointee."""
    pointee = own_payload_type(ty) if is_own_type(ty) else None
    if pointee is None:
        er.emit(validator.reporter, er.ERR.CE2109, node.loc, got=display_type(ty))
        return None
    if isinstance(node.inner, (p.PatBinding, p.Wildcard)):
        return WILD
    sub = _check_item(validator, node.inner, pointee, note)
    return None if sub is None else Ctor(OWN_KEY, (sub,))


def check_or_pattern(validator: 'TypeValidator', node: p.Or, ty: Type,
                     note: Optional[PositionNote] = None) -> Optional[Pat]:
    """`|` alternatives in a pattern position: each one is a pattern of the position.

    An alternative that repeats a value or a variant of an earlier one is the repeat error
    of its kind (CE2075, CE2041), and each alternative binds what the first one binds
    (CE2126). An integer literal or range that shares a value with an earlier one is
    CE2075 by the rule of the top of an arm (`_first_values`).
    """
    pieces: List[Tuple[object, Optional[Pat | _Dropped]]] = []
    firsts: dict[str, p.ShirPattern | p.PatBinding] = {}
    seen = _Seen()
    for alt in node.alternatives:
        piece: Optional[Pat | _Dropped] = _check_item(validator, alt, ty, note)
        if isinstance(alt, (p.Literal, p.IntRange)) and isinstance(piece, Ctor) \
                and isinstance(piece.key, IntRange):
            pieces.append((alt, _first_values(validator, alt, piece, seen)))
            continue
        signature = _item_signature(alt)
        if piece is not None and isinstance(alt, (p.Variant, p.Literal)) \
                and signature in firsts:
            _report_repeat(validator, alt, firsts[signature])
            piece = _DROPPED
        firsts.setdefault(signature, alt)
        pieces.append((alt, piece))
    if any(piece is None for _alt, piece in pieces):
        return None
    if _reject_binding_mismatch(validator, node, ty):
        return None
    return _alternatives_row(node, [(alt, piece) for alt, piece in pieces
                                    if isinstance(piece, (Wild, Ctor, Or))])


def _report_repeat(validator: 'TypeValidator', alt: p.Variant | p.Literal,
                   first: p.ShirPattern | p.PatBinding) -> None:
    """An alternative that repeats one above it: CE2075 for a value, CE2041 for a variant.

    A string value prints as the first alternative spells it, quotes included.
    """
    if isinstance(alt, p.Literal):
        written = first.written if isinstance(first, p.Literal) else ""
        er.emit(validator.reporter, er.ERR.CE2075, alt.loc, value=written, first=written)
    else:
        er.emit(validator.reporter, er.ERR.CE2041, alt.loc, variant=alt.variant)


def _item_signature(item: p.ShirPattern | p.PatBinding) -> str:
    """The signature of one pattern position; every binding reads as `_`.

    A `poke`, `peek` or `nom` binding binds like a bare name: the mode changes how the
    binding is made, not which values the arm matches, so `Poly(p)`, `Poly(poke p)` and
    `Poly(nom p)` are duplicates of each other.
    """
    if isinstance(item, p.Variant):
        if not item.items:
            return item.variant
        return f"{item.variant}({','.join(_item_signature(i) for i in item.items)})"
    if isinstance(item, p.Tuple):
        return "(" + ",".join(_item_signature(e) for e in item.elements) + ")"
    if isinstance(item, p.Literal):
        return _constant_signature(item.value)
    if isinstance(item, p.IntRange):
        operator = "..=" if item.inclusive else ".."
        return (f"{_constant_signature(item.low.value)}{operator}"
                f"{_constant_signature(item.high.value)}")
    if isinstance(item, p.Own):
        if isinstance(item.inner, (p.PatBinding, p.Wildcard)):
            return "Own"
        return f"Own({_item_signature(item.inner)})"
    if isinstance(item, p.Or):
        return "|".join(_item_signature(alt) for alt in item.alternatives)
    return "_"


def _constant_signature(const: Constant) -> str:
    """A string keeps its quotes, so `"1"` is not the integer 1 and `"_"` is not `_`."""
    return repr(const.value) if isinstance(const, StrConst) else str(const.value)


def _bindings_of(validator: 'TypeValidator', item: p.ShirPattern | p.PatBinding,
                 ty: Type) -> List[_Bound]:
    """The names a pattern item binds, in source order, each with the type of its position
    and its mode.

    `|` alternatives bind what their first alternative binds: `_reject_binding_mismatch`
    holds the others to it.
    """
    ty = _resolve(validator, ty)
    if isinstance(item, p.PatBinding):
        local = validator.body_locals[item.local]
        return [_Bound(item.local, local.name or "_", ty, _mode_word(item.by))]
    if isinstance(item, p.Or):
        return _bindings_of(validator, item.alternatives[0], ty)
    if isinstance(item, p.Variant):
        variant = ty.get_variant(item.variant) if isinstance(ty, EnumType) else None
        if variant is None:
            return []
        return [bound for sub, sub_type in zip(item.items, variant.associated_types,
                                               strict=False)
                for bound in _bindings_of(validator, sub, sub_type)]
    if isinstance(item, p.Tuple):
        return [bound for sub, sub_type in zip(item.elements, tuple_elements(ty), strict=False)
                for bound in _bindings_of(validator, sub, sub_type)]
    if isinstance(item, p.Own):
        pointee = own_payload_type(ty)
        return [] if pointee is None else _bindings_of(validator, item.inner, pointee)
    return []


def _mode_word(mode: p.BindMode) -> str:
    """The written mode of a binding: `bare`, `peek`, `poke` or `nom`."""
    if isinstance(mode, Ref):
        return mode.mode.value
    return "nom" if isinstance(mode, p.Nom) else "bare"


def _record_binders(validator: 'TypeValidator', pattern: p.ShirPattern, ty: Type) -> None:
    """The type row of the local of each binder: the type of its position, with no `peek`
    or `poke` (ir.md 7.8). A local that the pattern names twice gets one row."""
    answers = validator.body_answers()
    recorded: Set[LocalId] = set()
    for bound in _bindings_of(validator, pattern, ty):
        if bound.local not in recorded:
            recorded.add(bound.local)
            answers.record_local(bound.local, bound.ty)


def _reject_binding_mismatch(validator: 'TypeValidator', pattern: p.Or, ty: Type) -> bool:
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
                                  alternative=_spell(validator, alt), detail=detail)
        names = ", ".join(f"'{name}'" for name in first) or "no name"
        diagnostic.note_at(f"the first alternative '{_spell(validator, head)}' binds {names}",
                           getattr(head, "loc", None) or pattern.loc)
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


def reject_poke_binding_into_a_constant(validator: 'TypeValidator', match: 's.Match') -> None:
    """A `poke` pattern binding needs a scrutinee with storage (#685).

    `Variant(poke x)` binds a POINTER into the scrutinee's payload, and a write through
    it reaches that storage. A constant is read-only there (CE2400). A `peek` and a bare
    binding READ the payload, and reading a constant is legal. The root of the scrutinee
    place decides: a field or an element of a constant is in the constant.
    """
    root = walk_place(match.scrutinee, Step.MEMBER | Step.INDEX).node
    if not isinstance(root, e.ConstRef):
        return
    binding = next((b for arm in match.arms for b in _poke_bindings(arm.pattern)), None)
    if binding is None:
        return
    written = validator.decl_name(root.const_id)
    record = constant_record(validator, written)
    reject_borrow_of_constant(validator.err, written,
                              record if isinstance(record, ConstSig) else None,
                              validator.body_locals[binding.local].loc, mode="poke")


def _poke_bindings(item: p.ShirPattern | p.PatBinding) -> Iterator[p.PatBinding]:
    """The `poke` bindings of a pattern item, at any depth, in source order. The binding
    of `Own(poke h)` points into the heap, not into the scrutinee."""
    if isinstance(item, p.PatBinding):
        if isinstance(item.by, Ref) and item.by.mode is BorrowMode.POKE:
            yield item
    elif isinstance(item, p.Variant):
        for sub in item.items:
            yield from _poke_bindings(sub)
    elif isinstance(item, p.Tuple):
        for sub in item.elements:
            yield from _poke_bindings(sub)
    elif isinstance(item, p.Or):
        for alternative in item.alternatives:
            yield from _poke_bindings(alternative)
    elif isinstance(item, p.Own) and not isinstance(item.inner, p.PatBinding):
        yield from _poke_bindings(item.inner)


def _spell(validator: 'TypeValidator', pattern: p.ShirPattern | p.PatBinding) -> str:
    """A pattern as it is written, for a diagnostic."""
    return spell_pattern(pattern, lambda decl: validator.decl_name(decl),
                         lambda local: validator.body_locals[local].name)


def _first_values(validator: 'TypeValidator', alt: p.Literal | p.IntRange,
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
    if shared and (isinstance(alt, p.Literal)
                   or not covers([other for other, _first in seen.integers], key)):
        value, first = min(shared, key=lambda pair: pair[0])
        er.emit(validator.reporter, er.ERR.CE2075, alt.loc,
                value=_spell_value(value, _radix_of(alt)), first=first)
        if isinstance(alt, p.Literal):
            return _DROPPED
    seen.integers.append((key, _spell(validator, alt)))
    return row


def _radix_of(alt: p.Literal | p.IntRange) -> int:
    """The base an integer pattern is written in: the base of its first bound."""
    const = alt.low.value if isinstance(alt, p.IntRange) else alt.value
    return const.radix if isinstance(const, IntConst) else 10


def _spell_value(value: int, radix: int) -> str:
    """An integer for a diagnostic: decimal, and the written base beside it (`143 (0x8f)`)."""
    if value < 0 or radix == 10:
        return str(value)
    prefix = {16: "x", 2: "b", 8: "o"}.get(radix)
    if prefix is None:
        return str(value)
    return f"{value} ({format(value, '#' + prefix)})"


def _add_row(arms: ArmRows, idx: int, pattern: object,
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


def _wildcard_row(validator: 'TypeValidator', arms: ArmRows, idx: int, count: int,
                  pattern: p.Wildcard) -> None:
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


def check_match_exhaustiveness(validator: 'TypeValidator', match: 's.Match',
                               scrutinee_type: Type, arms: ArmRows) -> None:
    """Run the one checker over the rows of the arms: the missing values (CE2040, CE2074),
    and the dead arms and alternatives (CE2118). A match whose arms miss a value goes into
    `validator.uncovered_matches`, for the dead-statement rule."""
    if arms.invalid:
        return
    result = analyze(lambda ty: _signature(validator, ty), arms.rows, scrutinee_type)

    if result.missing:
        validator.uncovered_matches.add(match.shir_id)
        kind = literal_kind(scrutinee_type)
        if kind is not None:
            er.emit(validator.reporter, er.ERR.CE2074, match.loc, kind=kind,
                    missing=_first_missing(match, scrutinee_type, result.missing[0]))
        else:
            er.emit(validator.reporter, er.ERR.CE2040, match.loc,
                    variants=_render_missing(validator, result.missing, scrutinee_type,
                                             arms.rows))

    for index, covering in result.dead:
        if index in arms.reported or (arms.after_wildcard is not None
                                      and index >= arms.after_wildcard):
            continue
        dead = match.arms[index].pattern
        diagnostic = er.emit_with(validator.reporter, er.ERR.CE2118, dead.loc,
                                  pattern=_spell(validator, dead))
        _note_covering_arms(validator, diagnostic, match, covering)
        _help_for_an_empty_range(diagnostic, dead, scrutinee_type)
        diagnostic.emit()

    for dead_alternative in result.dead_alternatives:
        if dead_alternative.arm in arms.reported or (
                arms.after_wildcard is not None
                and dead_alternative.arm >= arms.after_wildcard):
            continue
        alt, span = dead_alternative.label
        diagnostic = er.emit_with(validator.reporter, er.ERR.CE2118, span,
                                  pattern=_spell(validator, alt))
        _note_covering_arms(validator, diagnostic, match, dead_alternative.arms)
        _help_for_an_empty_range(diagnostic, alt, scrutinee_type)
        for earlier, earlier_span in dead_alternative.alternatives:
            if earlier_span is not None:
                diagnostic.note_at(f"the alternative '{_spell(validator, earlier)}' matches "
                                   f"these values first", earlier_span)
        diagnostic.emit()


def _first_missing(match: 's.Match', ty: Type, witness: Pat) -> str:
    """The `{missing}` slot of CE2074: the first integer value that no arm matches, in the
    base of the first integer arm, or the other strings."""
    if ty == BuiltinType.STRING:
        return "every other string"
    if isinstance(witness, Ctor) and isinstance(witness.key, IntRange):
        value = witness.key.low
    else:
        bounds = integer_range(ty)
        value = bounds[0] if bounds is not None else 0
    radix = next((_radix_of(alt) for arm in match.arms for alt in _alternatives(arm.pattern)
                  if isinstance(alt, (p.Literal, p.IntRange))), 10)
    return f"the value {_spell_value(value, radix)}"


def _help_for_an_empty_range(diagnostic, pattern: object, ty: Type) -> None:
    """A range like `5..5` at the top of an arm matches no value: say so."""
    if not isinstance(pattern, p.IntRange) or _has_string_bound(pattern) \
            or integer_range(ty) is None:
        return
    low, high = pattern_interval(pattern, ty)
    if low > high:
        written = pattern.low.written
        diagnostic.help(f"'{spell_range(pattern)}' matches no value: '{written}..={written}' "
                        f"matches the one value {written}")


def _note_covering_arms(validator: 'TypeValidator', diagnostic, match: 's.Match',
                        covering: List[int]) -> None:
    """A note at each arm above that matches the values of a dead pattern first."""
    for above in covering:
        cover = match.arms[above].pattern
        diagnostic.note_at(f"the arm '{_spell(validator, cover)}' matches these values first",
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
