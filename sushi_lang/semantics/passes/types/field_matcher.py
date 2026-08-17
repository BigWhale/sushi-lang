"""Field argument matcher for named struct constructors."""
from __future__ import annotations
from typing import List, Optional, Set, TYPE_CHECKING
from sushi_lang.semantics.ast import Expr
from sushi_lang.semantics.typesys import StructType
from sushi_lang.internals import errors as er

if TYPE_CHECKING:
    from sushi_lang.internals.report import Reporter, Span


def validate_and_reorder_named_args(
    struct_type: StructType,
    arg_exprs: List[Expr],
    field_names: List[str],
    reporter: 'Reporter',
    loc: 'Span'
) -> Optional[List[Expr]]:
    """Validate named arguments and reorder to match field declaration order."""
    if len(arg_exprs) != len(field_names):
        # Internal error - should never happen from AST builder
        er.raise_internal_error("CE0002", node="named struct constructor",
                                detail="arg_exprs and field_names length mismatch")

    expected_fields = list(struct_type.fields)  # List of (field_name, field_type) tuples
    expected_field_names = {name for name, _ in expected_fields}

    for field_name in field_names:
        if field_name not in expected_field_names:
            er.emit(reporter, er.ERR.CE2080, loc,
                   field=field_name, struct=struct_type.name)
            return None

    seen_fields: Set[str] = set()
    for field_name in field_names:
        if field_name in seen_fields:
            er.emit(reporter, er.ERR.CE2081, loc,
                   field=field_name)
            return None
        seen_fields.add(field_name)

    provided_fields = set(field_names)
    missing_fields = expected_field_names - provided_fields
    if missing_fields:
        missing_list = ", ".join(sorted(missing_fields))
        er.emit(reporter, er.ERR.CE2082, loc,
               fields=missing_list, struct=struct_type.name)
        return None

    name_to_expr = {name: expr for name, expr in zip(field_names, arg_exprs, strict=False)}

    reordered_args = []
    for field_name, _ in expected_fields:
        reordered_args.append(name_to_expr[field_name])

    return reordered_args


def detect_mixed_args(field_names: Optional[List[str]], args: List[Expr]) -> bool:
    """Detect if arguments mix positional and named styles."""
    if field_names is None:
        return False

    # If field_names exists, all should be named
    # Mixing is only possible if field_names length != args length
    # But AST builder should ensure this never happens
    return len(field_names) != len(args)
