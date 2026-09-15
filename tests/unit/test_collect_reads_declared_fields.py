"""The collect pass reads a DECLARED field as a field, not through `getattr` (#694).

Every AST node is a slotted dataclass, so each of its fields is declared and typed. A
`getattr(node, "field", default)` over such a field hides two things: the type checker
cannot see the read, and a renamed or missing field answers the default in silence --
which is how #596 shipped. The habit also hid a rebuild that enumerates the fields it
copies and drops the ones it forgets.

Three gates:
  - no `getattr` in the collect package names a declared field, except where the
    receiver is a UNION and the field lives on some arms alone,
  - a rebuilt parameter keeps every declared field but the one that is converted, and
    it stays that way when `Param` grows a field,
  - the type-parameter list a declaration carries reaches the table unchanged; the
    collectors build no type parameter of their own.
"""
from __future__ import annotations

import ast
import collections
import dataclasses
from pathlib import Path

from sushi_lang.semantics.typesys import BuiltinType, UnknownType

SUSHI_LANG = Path(__file__).resolve().parents[2] / "sushi_lang"
COLLECT = SUSHI_LANG / "semantics" / "passes" / "collect"
AST_MODULE = SUSHI_LANG / "semantics" / "ast.py"

# A `getattr` that is RIGHT: the receiver is a TYPE, and a name lives on some arms of
# that union and on none of the others. Keyed by (module, receiver, field) so that the
# entry survives an edit above it.
UNION_READS = {
    ("functions.py", "target_type", "name"),
    ("functions.py", "pack_param.ty", "name"),
    ("functions.py", "err_ty", "name"),
}


def _declared_field_names(path: Path) -> set[str]:
    """Every annotated class-body field name of every class in one module."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                names.add(stmt.target.id)
    return names


def _literal_getattr_reads(path: Path):
    """Every `getattr(x, "field", ...)` of one module, as (receiver, field)."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            yield ast.unparse(node.args[0]), node.args[1].value, node.lineno


def test_a_declared_field_is_read_as_a_field():
    declared = _declared_field_names(AST_MODULE)
    for module in sorted(COLLECT.glob("*.py")):
        declared |= _declared_field_names(module)

    offenders = []
    for module in sorted(COLLECT.glob("*.py")):
        for receiver, field, line in _literal_getattr_reads(module):
            if field not in declared:
                continue
            if (module.name, receiver, field) in UNION_READS:
                continue
            offenders.append(f"{module.name}:{line} getattr({receiver}, {field!r})")

    assert not offenders, (
        "a declared field is read through getattr:\n  " + "\n  ".join(offenders))


def test_a_rebuilt_parameter_keeps_every_field_but_its_type():
    from sushi_lang.semantics.passes.collect.functions import Param, convert_param_types

    original = Param(name="v", ty=UnknownType(name="T"), name_span=None,
                     type_span=None, index=3, is_variadic=True, is_pack=True,
                     is_nom=True)
    rebuilt, = convert_param_types([original], lambda _ty: BuiltinType.I32)

    assert rebuilt is not original
    assert rebuilt.ty is BuiltinType.I32
    carried = [f.name for f in dataclasses.fields(Param) if f.name != "ty"]
    assert carried, "Param declares no field but its type"
    for name in carried:
        assert getattr(rebuilt, name) == getattr(original, name), name


def test_the_collectors_build_no_type_parameter_of_their_own():
    """A declaration's type-parameter list reaches the table as it was parsed.

    The builder answers `BoundedTypeParam` for every type parameter, so the arms that
    rebuilt one from a string -- or that passed a `TypeParameter` where a name was
    wanted -- could never run.
    """
    built = collections.defaultdict(list)
    for module in (COLLECT / "structs.py", COLLECT / "enums.py",
                   COLLECT / "functions.py"):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in {"BoundedTypeParam", "TypeParameter"}):
                built[module.name].append(f"{node.func.id} at line {node.lineno}")

    # `deep_type_params` builds a TypeParameter per NAME of a signature conversion,
    # which is a substitution and not a declaration's own list.
    built["functions.py"] = [
        site for site in built["functions.py"] if not site.startswith("TypeParameter")]

    offenders = [f"{module}: {site}"
                 for module, sites in sorted(built.items()) for site in sites]
    assert not offenders, (
        "a collector rebuilds a declared type parameter:\n  " + "\n  ".join(offenders))
