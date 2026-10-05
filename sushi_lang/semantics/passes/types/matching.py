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
    NomBinding, RefBinding, TupleLiteral, TuplePattern, pattern_source,
)
from sushi_lang.semantics.constant_borrow import reject_borrow_of_constant
from sushi_lang.semantics.ownership import is_own_type
from sushi_lang.semantics.param_modes import ParamMode, borrow_mode, receiver_mode
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.generics.own import own_payload_type
from sushi_lang.semantics.generics.tuples import display_tuple, is_tuple_type, tuple_elements
from sushi_lang.semantics.generics.type_display import display_type
from .exhaustiveness import (
    OWN_KEY, TUPLE_KEY, WILD, Ctor, Pat, Signature, Wild, analyze, string_key,
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
        yield element, [arm.pattern.elements[index] for arm in stmt.arms
                        if isinstance(arm.pattern, TuplePattern)
                        and index < len(arm.pattern.elements)]


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
    elif isinstance(item, OwnPattern) and not isinstance(item.inner_pattern, str):
        yield from _poke_bindings(item.inner_pattern)


def validate_match_scrutinee(validator: 'TypeValidator', stmt: Match) -> Optional[Type]:
    """Validate the scrutinee is matchable: an enum, an integer (#415), a string, or a tuple."""
    validator.validate_expression(stmt.scrutinee)
    scrutinee_type = validator.infer_expression_type(stmt.scrutinee)

    if scrutinee_type is None:
        # A scrutinee with no type is refused by the check that found the fault. With no
        # error at all, the arms would go unchecked and the backend would guess (#1005).
        # The reporter drops a repeat in an instance body, so the test is "any error".
        if not validator.reporter.has_errors:
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
    """Validate a match on an integer (#415) or a string scrutinee: literal arms + a `_`.

    Each literal arm must be of the scrutinee's kind (CE2076 otherwise). An integer
    literal takes the scrutinee's type under the same fit rule as any context-typed
    literal (a non-decimal literal is a bit pattern). Duplicates are duplicates by VALUE,
    so `0x2a` and `42` are one arm, and `"a"` and `'a'` are one arm. The wildcard is
    required because the values cannot be listed, which the one checker answers with
    CE2074.
    """
    kind = _scrutinee_literal_kind(scrutinee_type)
    # The backend switches on the value of an integer match. A string match has no
    # switch: it reads `resolved_scrutinee_type`, as a tuple match does.
    if kind == "integer":
        stmt.integer_match_type = scrutinee_type
    else:
        stmt.resolved_scrutinee_type = scrutinee_type

    arms = _ArmRows()
    seen: dict[int | str, str] = {}
    for idx, arm in enumerate(stmt.arms):
        pattern = arm.pattern

        if isinstance(pattern, WildcardPattern):
            _wildcard_row(validator, arms, idx, len(stmt.arms), pattern)
        elif isinstance(pattern, LiteralPattern) and _literal_kind(pattern.value) != kind:
            _reject_arm_kind(validator, pattern, scrutinee_type, arms)
        elif isinstance(pattern, LiteralPattern):
            row = _check_literal(validator, pattern, scrutinee_type)
            if row is None:
                arms.invalid = True
            elif pattern.value in seen:
                # A string value prints as the first arm spells it, quotes included.
                first = seen[pattern.value]
                value = first if isinstance(pattern.value, str) else pattern.value
                er.emit(validator.reporter, er.ERR.CE2075, pattern.loc,
                        value=value, first=first)
                arms.reported.add(idx)
                row = None
            else:
                seen[pattern.value] = pattern.display
            arms.rows.append(row)
        elif isinstance(pattern, TuplePattern):
            _check_tuple_pattern(validator, pattern, scrutinee_type, ("", None))
            arms.invalid = True
            arms.rows.append(None)
        else:
            _reject_arm_kind(validator, pattern, scrutinee_type, arms)

        _walk_arm_body(validator, arm)

    check_match_exhaustiveness(validator, stmt, scrutinee_type, arms)


def _reject_arm_kind(validator: 'TypeValidator', pattern: Pattern | LiteralPattern,
                     scrutinee_type: Type, arms: _ArmRows) -> None:
    """An arm whose kind does not fit the scrutinee (CE2076): the arm gets no row."""
    if isinstance(pattern, LiteralPattern):
        arm_kind = f"{_literal_kind(pattern.value)} literal"
    else:
        arm_kind = "enum-pattern"
    er.emit(validator.reporter, er.ERR.CE2076, pattern.loc,
            arm_kind=arm_kind, scrutinee_type=display_type(scrutinee_type))
    arms.invalid = True
    arms.rows.append(None)


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

        if isinstance(pattern, LiteralPattern) or (
                isinstance(pattern, Pattern) and not isinstance(scrutinee_type, EnumType)):
            # A literal arm needs an integer (#415) or a string scrutinee, and an enum
            # pattern arm an enum scrutinee.
            _reject_arm_kind(validator, pattern, scrutinee_type, arms)
            continue

        if isinstance(pattern, Pattern):
            signature = get_pattern_signature(pattern)
            if signature in signatures and isinstance(scrutinee_type, EnumType) \
                    and _names_the_scrutinee(validator, pattern, scrutinee_type):
                er.emit(validator.reporter, er.ERR.CE2041, pattern.loc,
                        variant=pattern.variant_name)
                arms.reported.add(idx)
                arms.rows.append(None)
                continue
            signatures.add(signature)

        row = _check_item(validator, pattern, scrutinee_type, top_note)
        arms.rows.append(row)
        if row is None:
            arms.invalid = True
            continue

        saved_vars = validator.variable_types.copy()
        _register_bindings(validator, pattern, scrutinee_type)

        _walk_arm_body(validator, arm)

        validator.variable_types = saved_vars

    return arms


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
    if isinstance(item, TuplePattern):
        return _check_tuple_pattern(validator, item, ty, note)
    if isinstance(item, Pattern):
        return _check_enum_pattern(validator, item, ty, note)
    if isinstance(item, OwnPattern):
        return _check_own_pattern(validator, item, ty, note)
    raise_internal_error("CE0121", pattern=pattern_source(item))
    return None


def _check_literal(validator: 'TypeValidator', pattern: LiteralPattern,
                   ty: Type) -> Optional[Pat]:
    """A literal pattern: a value of the literal's kind, and an integer literal that fits.

    A string literal reads a `string` value. An integer literal reads an integer value
    and must fit its type.
    """
    from sushi_lang.semantics.passes.types.inference import int_literal_fits

    if isinstance(pattern.value, str):
        if ty != BuiltinType.STRING:
            er.emit(validator.reporter, er.ERR.CE2119, pattern.loc,
                    kind="a string", got=display_type(ty))
            return None
        return Ctor(string_key(pattern.value))
    if not isinstance(ty, BuiltinType) or ty not in _INTEGER_SCRUTINEES:
        er.emit(validator.reporter, er.ERR.CE2119, pattern.loc,
                kind="an integer", got=display_type(ty))
        return None
    if not int_literal_fits(pattern.value, pattern.radix, ty):
        er.emit(validator.reporter, er.ERR.CE2073, pattern.loc,
                literal=pattern.display, type=display_type(ty))
        return None
    return Ctor(pattern.value)


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
    """Register the names a pattern item binds in `variable_types`, with their types."""
    ty = _resolve(validator, ty)
    if isinstance(item, NomBinding):
        # `Variant(nom x)` (ruling R11): the arm OWNS the payload, so the binding has the
        # payload's own type -- no reference wrapper, and every consumer that asks "may
        # this be given away?" answers yes.
        validator.variable_types[item.name] = ty
    elif isinstance(item, str):
        if item != "_":
            validator.variable_types[item] = ty
    elif isinstance(item, RefBinding):
        # `Variant(poke x)` (#300 phase 3): the binding IS a reference into the
        # scrutinee's storage, so register the reference type -- every consumer that asks
        # "is this name a borrow?" answers truthfully, and inference auto-derefs the name.
        validator.variable_types[item.name] = ReferenceType(ty, borrow_mode(item.mode))
    elif isinstance(item, Pattern):
        variant = ty.get_variant(item.variant_name) if isinstance(ty, EnumType) else None
        if variant is not None:
            for binding, binding_type in zip(item.bindings, variant.associated_types,
                                             strict=False):
                _register_bindings(validator, binding, binding_type)
    elif isinstance(item, TuplePattern):
        for element, element_type in zip(item.elements, tuple_elements(ty), strict=False):
            _register_bindings(validator, element, element_type)
    elif isinstance(item, OwnPattern):
        pointee = own_payload_type(ty)
        if pointee is None:
            return
        inner = item.inner_pattern
        if isinstance(inner, str) and inner != "_" and item.inner_borrow is not None:
            # `Own(poke x)` (#300 phase 1): the binding IS a reference to the pointee.
            validator.variable_types[inner] = ReferenceType(
                _resolve(validator, pointee), borrow_mode(item.inner_borrow))
        else:
            _register_bindings(validator, inner, pointee)


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
            er.emit(validator.reporter, er.ERR.CE2074, stmt.loc, kind=kind)
        else:
            er.emit(validator.reporter, er.ERR.CE2040, stmt.loc,
                    variants=_render_missing(validator, result.missing, scrutinee_type,
                                             arms.rows))

    for index, covers in result.dead:
        if index in arms.reported or (arms.after_wildcard is not None
                                      and index >= arms.after_wildcard):
            continue
        dead = stmt.arms[index].pattern
        diagnostic = er.emit_with(validator.reporter, er.ERR.CE2118, dead.loc,
                                  pattern=pattern_source(dead))
        for above in covers:
            cover = stmt.arms[above].pattern
            if cover.loc is not None:
                diagnostic.note_at(
                    f"the arm '{pattern_source(cover)}' matches these values first",
                    cover.loc)
        diagnostic.emit()


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
    return not isinstance(row, Ctor) or all(isinstance(a, Wild) for a in row.args)


def _render_witness(validator: 'TypeValidator', pat: Pat, ty: Type) -> str:
    """One missing pattern in source syntax: `Maybe.Some(Color.Green)`, `(_, Color.Red)`."""
    if isinstance(pat, Wild):
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
    if isinstance(item, OwnPattern):
        if isinstance(item.inner_pattern, str):
            return "Own"
        return f"Own({_item_signature(item.inner_pattern)})"
    return "_"
