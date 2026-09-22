"""One table decides which built-in method family a call belongs to (#751).

The method families were dispatched twice. Inference walked a registry of checkers;
validation walked an `if/elif` chain of twelve arms in a DIFFERENT order. Nothing held
the two in step but two comments, and the failure mode they describe is a miscompile:
the typecheck pass types the call as the built-in's return while the backend calls the
perk implementation that actually answers it.

This file states the repair. There is ONE family table. A family carries the CLAIM (who
answers this receiver and this name), the inferrer the typecheck pass reads and the
validator it runs, and both halves reach it through `MethodTypeRegistry.claim`. The
tests below hold three things:

* the table is total -- every family carries both halves, so a family added for one of
  them cannot go missing from the other;
* neither half carries a second dispatcher -- the family predicates are named in the
  registry and nowhere else;
* the claims are pairwise DISJOINT, which is what makes the two halves' historical
  orders equivalent and what makes any order safe.

The disjointness sweep is measured against a space that claims every family at least
once (`test_the_claim_space_reaches_every_family`): a sweep over a space nothing claims
answers the same zero a real one does.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, FunctionType, StructType,
)

TYPES_PASS = (Path(__file__).resolve().parents[2]
              / "sushi_lang" / "semantics" / "passes" / "types")
REGISTRY = TYPES_PASS / "method_registry.py"
VALIDATION = TYPES_PASS / "calls" / "methods.py"

#: The predicate each family asks about a method NAME. A claim is the registry's to make,
#: so no other module of the pass may name one of these.
_FAMILY_PREDICATES = (
    "is_builtin_array_method",
    "is_builtin_string_method",
    "is_builtin_result_method",
    "is_builtin_maybe_method",
    "is_builtin_own_method",
    "is_builtin_hashmap_method",
    "is_builtin_list_method",
    "is_builtin_function_method",
    "has_primitive_method",
)


class _StubPerkTable:
    """No program under test implements a perk, so nothing is overridden."""

    def get_method(self, target_type, method_name):
        return None


class _StubDerivedTable:
    """The `derive` pass writes `hash` and `clone`, and a HashMap gets no hash."""

    def get_method(self, target_type, method_name):
        if not isinstance(target_type, (StructType, EnumType)):
            return None
        if method_name == "hash" and target_type.name.startswith("HashMap<"):
            return None
        if method_name in ("hash", "clone"):
            return object()
        return None


class _StubValidator:
    perk_impl_table = _StubPerkTable()
    derived_methods = _StubDerivedTable()


def _struct(name: str) -> StructType:
    return StructType(name=name, fields=())


def _enum(name: str) -> EnumType:
    return EnumType(name=name, variants=())


#: One receiver per family, plus the plain struct and enum the derived family answers for.
_RECEIVERS = (
    DynamicArrayType(BuiltinType.I32),
    ArrayType(BuiltinType.I32, 3),
    BuiltinType.STRING,
    BuiltinType.I32,
    BuiltinType.F64,
    BuiltinType.BOOL,
    _enum("Result<i32, StdError>"),
    _enum("Maybe<i32>"),
    _struct("List<i32>"),
    _struct("HashMap<i32, string>"),
    _struct("Own<i32>"),
    _struct("Point"),
    _enum("Colour"),
    FunctionType(param_types=(BuiltinType.I32,), ok_type=BuiltinType.I32,
                 err_type=BuiltinType.I32),
)

#: Every name any family answers to, plus names no family does.
_NAMES = (
    "len", "get", "push", "pop", "first", "last", "index_of", "clear",
    "s", "ss", "split", "contains",
    "is_ok", "is_err", "realise", "expect", "err",
    "is_some", "is_none",
    "alloc", "destroy", "new", "with_capacity", "insert", "remove",
    "contains_key", "is_empty", "tombstone_count", "rehash", "free", "debug",
    "keys", "values", "entries", "iter", "capacity", "reserve", "shrink_to_fit",
    "hash", "clone", "to_str", "to_bits",
    "no_such_method", "bump",
)


def _claims(receiver, name):
    """Every family that claims this pair, by name."""
    return [family.name for family in METHOD_TYPE_REGISTRY.families
            if family.claims(receiver, name, _StubValidator())]


def test_the_registry_holds_one_family_table():
    families = METHOD_TYPE_REGISTRY.families
    assert families, "the registry carries no family at all"
    assert len({family.name for family in families}) == len(families), \
        "two families share one name"


def test_every_family_carries_both_halves():
    """The totality that replaces the two comments: no family is half-registered."""
    missing = [family.name for family in METHOD_TYPE_REGISTRY.families
               if family.infer is None or family.validate is None]
    assert not missing, (
        "a family carries only one half, so the two dispatchers can disagree again: "
        + ", ".join(missing))


def test_both_halves_reach_the_one_claim():
    """Inference and validation are two hooks on one claim, measured at the source."""
    tree = ast.parse(REGISTRY.read_text(encoding="utf-8"), filename=str(REGISTRY))
    registry = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.ClassDef) and node.name == "MethodTypeRegistry")
    bodies = {node.name: ast.unparse(node) for node in registry.body
              if isinstance(node, ast.FunctionDef)}
    for half in ("infer_method_type", "validate_method"):
        assert half in bodies, f"the registry has no {half}"
        assert "self.claim(" in bodies[half], (
            f"{half} decides for itself instead of reading the one claim")


def test_validation_names_no_family_predicate():
    """The elif chain is gone: a claim is the registry's, and it is written once."""
    text = VALIDATION.read_text(encoding="utf-8")
    offenders = [name for name in _FAMILY_PREDICATES if name in text]
    assert not offenders, (
        "the validation half asks a family predicate of its own: " + ", ".join(offenders)
        + "\nA family claim belongs in method_registry.py and nowhere else.")


def test_the_registry_names_every_family_predicate():
    """The always-fires control for the reader above."""
    text = REGISTRY.read_text(encoding="utf-8")
    missing = [name for name in _FAMILY_PREDICATES if name not in text]
    assert not missing, ("the family table does not ask: " + ", ".join(missing))


@pytest.mark.parametrize("receiver", _RECEIVERS, ids=lambda r: str(r))
def test_the_families_claim_disjointly(receiver):
    """At most one family answers any (receiver, name): the order cannot matter."""
    clashes = []
    for name in _NAMES:
        claimed = _claims(receiver, name)
        if len(claimed) > 1:
            clashes.append(f"{receiver}.{name}() -> {', '.join(claimed)}")
    assert not clashes, (
        "two families claim one call, so the dispatch order decides the answer:\n  "
        + "\n  ".join(clashes))


def test_the_claim_space_reaches_every_family():
    """The always-fires control: a space nothing claims proves nothing about disjointness."""
    seen = set()
    for receiver in _RECEIVERS:
        for name in _NAMES:
            seen.update(_claims(receiver, name))
    unreached = sorted({family.name for family in METHOD_TYPE_REGISTRY.families} - seen)
    assert not unreached, (
        "the sweep above never reached these families, so it asserted nothing about "
        "them: " + ", ".join(unreached))


def test_a_name_no_family_answers_is_left_to_the_ladder():
    """A miss is a miss in both halves: the perk and extension ladder gets the call."""
    for receiver in _RECEIVERS:
        assert _claims(receiver, "no_such_method") == [], (
            f"a family claimed {receiver}.no_such_method()")


#: Where the `derive` pass writes into the derived-method table.
_DERIVE_SITES = (
    Path(__file__).resolve().parents[2] / "sushi_lang" / "semantics" / "generics",
)


def test_the_derive_pass_writes_only_hash_and_clone():
    """The derived family is split in two rows, one per name; this is what allows it.

    The rows claim `hash` and `clone` and nothing else. While the claim read the derived
    TABLE alone, a third derived name would have been claimed by it; two rows would miss
    one. The registrations are two, and this says so.
    """
    registered = []
    for root in _DERIVE_SITES:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "register_method"):
                    continue
                for arg in ast.walk(node):
                    if (isinstance(arg, ast.keyword) and arg.arg == "name"
                            and isinstance(arg.value, ast.Constant)):
                        registered.append(arg.value.value)
    assert sorted(registered) == ["clone", "hash"], (
        "the derive pass registers a name the family table does not claim: "
        + ", ".join(sorted(set(registered))))
