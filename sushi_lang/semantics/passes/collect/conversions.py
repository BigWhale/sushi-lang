"""The collect half of a conversion: `extend <Source> as <Target>:`.

docs/design/error-conversion.md sections 3.4 to 3.6. The rules, in the order they are
asked, each with its own code: a generic or non-error source or target (C6, CE2520), an
identity pair (CE2521), a target that another unit declares (C5, CE2519), and a second
declaration of one pair (CE0101). A declaration that passes is filed in
`SymbolTables.conversions` and NOT in the extension table (ruling P6), which keys on the
receiver and the method name.
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.ast import ExtendDef
from sushi_lang.semantics.conversions import Conversion, conversion_symbol
from sushi_lang.semantics.error_types import kind_of
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.type_resolution import resolve_unknown_type
from sushi_lang.semantics.typesys import EnumType, Type, UnknownType

from .utils import reject_misplaced_expands, reject_try_in_body

if TYPE_CHECKING:
    from .functions import FunctionCollector


class Filed(Enum):
    """What the collect pass did with one conversion."""

    FILED = "filed"            # in the table
    REFUSED = "refused"        # a diagnostic stands; the declaration leaves the AST
    UNRESOLVED = "unresolved"  # a side names no type yet; a later step or CE2001 answers


def collect_conversion(collector: 'FunctionCollector', ext: ExtendDef, *,
                       refile: bool = False) -> Filed:
    """Judge one conversion and file it. The answer says whether it stays in the AST.

    `refile` is the second call, from the `libraries` step, for a conversion whose
    types only a binary library declares: the body was judged on the first call.
    """
    # The body is bare (C7), and the grammar has no place for a `| E`.
    if not refile:
        reject_try_in_body(collector.r, ext.body, "a conversion",
                           help_text="a conversion is bare, so handle the Result in "
                                     "the body (match, .realise(default))")
        if reject_misplaced_expands(collector.r, ext.body, frozenset(),
                                    ext.target_type_span or ext.name_span):
            collector.refused_pack_bodies.append("conversion")

    source = _side(collector, ext.target_type, "source", ext.target_type_span)
    target = _side(collector, ext.ret, "target", ext.ret_span)
    if source is Filed.REFUSED or target is Filed.REFUSED:
        return Filed.REFUSED
    if not isinstance(source, EnumType) or not isinstance(target, EnumType):
        return Filed.UNRESOLVED

    if source == target:
        er.emit_with(collector.r, er.ERR.CE2521, ext.target_type_span or ext.name_span,
                     type_name=display_type(source)) \
            .help("`??` already propagates an error of the same type unchanged; "
                  "delete the declaration").emit()
        return Filed.REFUSED

    if _reject_foreign_target(collector, target, ext.ret_span or ext.name_span):
        return Filed.REFUSED

    # The declaration keeps its WRITTEN types: a name behind an alias (`ie.FileError`)
    # is checked as written in the typecheck pass, and its symbol is the same either way.
    conversion = Conversion(
        source=source, target=target, symbol=conversion_symbol(source, target),
        unit_name=collector.current_unit_name, filename=collector.current_unit_file,
        name_span=ext.name_span)
    first = collector.conversions.file(conversion)
    if first is not None:
        collector._emit_duplicate_extension(
            f"conversion '{display_type(source)} as {display_type(target)}'",
            ext.name_span, first.unit_name, first.name_span, first.filename)
        return Filed.REFUSED
    ext.declared_conversion = conversion
    return Filed.FILED


def _side(collector: 'FunctionCollector', ty: Optional[Type], side: str,
          span: Optional[Span]) -> 'EnumType | Filed':
    """The error type one side names, REFUSED once CE2520 stands, or UNRESOLVED."""
    if isinstance(ty, GenericTypeRef):
        _reject_side(collector, side, display_type(ty),
                     f"an instance of the generic {_template_word(collector, ty.base_name)} "
                     f"'{ty.base_name}'", span)
        return Filed.REFUSED

    resolved = resolve_unknown_type(ty, collector.structs.by_name,
                                    collector.enums.by_name) if ty is not None else None
    if isinstance(resolved, UnknownType):
        if (resolved.name not in collector.generic_enums.by_name
                and resolved.name not in collector.generic_structs.by_name):
            return Filed.UNRESOLVED
        _reject_side(collector, side, resolved.name,
                     f"a generic {_template_word(collector, resolved.name)}", span)
        return Filed.REFUSED
    if resolved is None:
        return Filed.UNRESOLVED

    if isinstance(resolved, EnumType) and resolved.is_error and not resolved.generic_args:
        return resolved
    help_text = None
    if (isinstance(resolved, EnumType) and not resolved.generic_args
            and resolved.home_module is None
            and not is_instance_of(resolved, "Maybe", "Result")):
        help_text = f"declare '{resolved.name}' with 'error' in place of 'enum'"
    _reject_side(collector, side, display_type(resolved), kind_of(resolved), span,
                 help_text)
    return Filed.REFUSED


def _template_word(collector: 'FunctionCollector', name: str) -> str:
    """What a generic template is called: an error type, or a type."""
    template = collector.generic_enums.by_name.get(name)
    return "error type" if template is not None and template.is_error else "type"


def _reject_side(collector: 'FunctionCollector', side: str, type_name: str, kind: str,
                 span: Optional[Span], help_text: Optional[str] = None) -> None:
    diag = er.emit_with(collector.r, er.ERR.CE2520, span, side=side,
                        type_name=type_name, kind=kind)
    if help_text is not None:
        diag.help(help_text)
    diag.emit()


def _reject_foreign_target(collector: 'FunctionCollector', target: EnumType,
                           span: Optional[Span]) -> bool:
    """CE2519 when this unit does not declare the target type (C5, C12)."""
    from sushi_lang.semantics.passes.collect.enums import PREDEFINED_ENUM_HOMES

    name = target.name
    here = collector.current_unit_name
    if name in PREDEFINED_ENUM_HOMES:
        home = PREDEFINED_ENUM_HOMES[name]
        if home is not None and home == here:
            return False
        if home is None:
            reason = f"'{name}' has no home module, so no unit may declare one"
        else:
            reason = f"only its home module <{home}> may declare one"
        er.emit_with(collector.r, er.ERR.CE2519, span, target=name, reason=reason) \
            .help(f"declare an error type of your own, and convert '{name}' into it") \
            .emit()
        return True

    origin = collector.visibility.origin("enum", name) if collector.visibility else None
    if origin is not None and origin.unit_name == here:
        return False
    diag = er.emit_with(collector.r, er.ERR.CE2519, span, target=name,
                        reason=f"only the unit that declares '{name}' may declare one")
    declared_at = collector.enums.spans.get(name)
    if declared_at is not None:
        diag.note_at(f"'{name}' is declared here", declared_at,
                     collector.enums.files.get(name))
    diag.help(f"move the conversion into the unit that declares '{name}'").emit()
    return True
