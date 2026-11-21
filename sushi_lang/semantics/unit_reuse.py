"""The one answer to "is this unit reused": a bundled stdlib unit whose object is in the
per-user cache, or a source library unit whose object is in the project cache, skips its
body passes.

The pipeline makes one `UnitReuse` for an incremental build and gives it to the analyzer
before the analysis. The analyzer decides with it before the per-unit loop, skips each
reused unit, replays the record of the unit, and falls back to the body passes when the
unit gets a template copy or its key changes. The pipeline reads the same predicate to take
the object from the cache. `docs/internals/semantic-passes.md` holds the rules.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

from sushi_lang.internals.report import display_filename

if TYPE_CHECKING:
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.units import Unit

RECORD_FORMAT = 1

# The key of a unit: (unit, template copies of the program, the `Drop` set) -> digest.
KeyOf = Callable[["Unit", list, frozenset], str]

# The materialized file of each source library unit of this process -> its label.
_LIBRARY_FILES: dict[Path, str] = {}


def label_library_file(path: Path, unit_name: str) -> None:
    """The materialized file `path` of the source library unit `unit_name` prints as
    `lib/<name>/<unit>.sushi`, its path inside the library. A diagnostic prints a file of
    the program as `./<path>` or as a bare file name, so no file of the program has this
    label."""
    _LIBRARY_FILES[path.resolve()] = f"{unit_name}.sushi"


def object_label(filename: str) -> str:
    """The file name that the object of a unit prints in an `assert` or a trap, and that
    its key holds. A bundled stdlib module is `<sys/process>` and a source library unit is
    `lib/<name>/<unit>.sushi`, the same from each working directory; any other file is the name a
    diagnostic prints."""
    if not (filename.startswith("<") and filename.endswith(">")):
        from sushi_lang.semantics.stdlib_registry import SOURCE_STDLIB_MODULES
        try:
            resolved = Path(filename).resolve()
        except OSError:
            resolved = None
        if resolved in _LIBRARY_FILES:
            return _LIBRARY_FILES[resolved]
        for name, path in SOURCE_STDLIB_MODULES.items():
            if resolved is not None and Path(path).resolve() == resolved:
                return f"<{name}>"
    return display_filename(filename)


def held_extension_copies(unit: "Unit", copies: Optional[Iterable]) -> list:
    """The generic-target extension copies whose home is `unit`."""
    return [ext for ext in copies or () if getattr(ext, "home_unit", None) == unit.name]


def holds_template_copies(unit: "Unit") -> bool:
    """Whether the AST of the unit holds a generic function instance or a perk-impl copy."""
    if unit.ast is None:
        return False
    return (any(getattr(fn, "home_unit", None) == unit.name for fn in unit.ast.functions)
            or any(getattr(impl, "is_synthesized", False) for impl in unit.ast.perk_impls))


def _plain_type(ty: Any) -> bool:
    """A type that the `resolve` pass gives its final form: the typecheck pass interns
    nothing for it."""
    from sushi_lang.semantics.typesys import ArrayType, BuiltinType, UnknownType
    if isinstance(ty, ArrayType):
        return _plain_type(ty.base_type)
    return isinstance(ty, (BuiltinType, UnknownType))


def _meets_an_extension(unit: "Unit", tables: "SymbolTables") -> bool:
    """A perk implementation of the unit has a method of the name of an extension method
    on its target. The typecheck pass of the unit reports it (CE4007), so its body passes
    must run."""
    from sushi_lang.semantics.passes.types.perks import meets_an_extension
    return (any(meets_an_extension(impl, tables, False) for impl in unit.ast.perk_impls)
            or any(meets_an_extension(impl, tables, True)
                   for impl in unit.ast.generic_perk_impls or ()))


def _candidate(unit: "Unit", copies: list, tables: "SymbolTables") -> bool:
    """A unit that the skip can be safe for, before the per-unit loop: a bundled stdlib
    unit, or a source library unit (a warning in its code is dropped, and an error stops
    the build, so its cached object has no diagnostic to show)."""
    return ((unit.is_bundled_stdlib or unit.from_library) and unit.ast is not None
            and not holds_template_copies(unit) and not held_extension_copies(unit, copies)
            and all(_plain_type(const.ty) for const in unit.ast.constants)
            and not _meets_an_extension(unit, tables))


# The record of a body: the type arguments of an instance, as JSON.

def encode_type(ty: Any) -> Optional[Any]:
    """A type argument as JSON, or None for a type the record cannot hold."""
    from sushi_lang.semantics.typesys import (
        ArrayType, BuiltinType, DynamicArrayType, EnumType, StructType)
    if isinstance(ty, BuiltinType):
        return {"builtin": ty.name}
    if isinstance(ty, (StructType, EnumType)):
        return {"named": ty.name}
    if isinstance(ty, ArrayType):
        base = encode_type(ty.base_type)
        return None if base is None else {"array": base, "size": ty.size}
    if isinstance(ty, DynamicArrayType):
        base = encode_type(ty.base_type)
        return None if base is None else {"dynamic": base}
    return None


def decode_type(data: Any, tables: "SymbolTables") -> Optional[Any]:
    """The type of `encode_type`, read from the tables of this build, or None."""
    from sushi_lang.semantics.typesys import ArrayType, BuiltinType, DynamicArrayType
    if not isinstance(data, dict):
        return None
    if "builtin" in data:
        return BuiltinType.__members__.get(data["builtin"])
    if "named" in data:
        name = data["named"]
        return tables.structs.by_name.get(name) or tables.enums.by_name.get(name)
    if "array" in data:
        base = decode_type(data["array"], tables)
        return None if base is None else ArrayType(base, int(data["size"]))
    if "dynamic" in data:
        base = decode_type(data["dynamic"], tables)
        return None if base is None else DynamicArrayType(base)
    return None


@dataclass
class BodyRecord:
    """What the body passes of one unit asked of the program.

    The generic function instances its body calls, which a reused unit asks for again, and
    the first question that a template copy can answer (an extension template or a perk
    template). A body that asked such a question gives no record, so its unit is never
    reused: a copy of that kind has no replay.
    """
    instances: list = field(default_factory=list)
    refusal: Optional[str] = None

    def instance(self, key: tuple) -> None:
        """A call of the instance (declaring unit, name, type arguments)."""
        unit, name, type_args = key
        encoded = [encode_type(arg) for arg in type_args]
        if any(arg is None for arg in encoded):
            self.refuse(f"the instance {name} has a type argument that no record holds")
            return
        entry = [unit, name, encoded]
        if entry not in self.instances:
            self.instances.append(entry)

    def refuse(self, why: str) -> None:
        if self.refusal is None:
            self.refusal = why

    def perk_question(self, table: Any, ty: Any, perk: Optional[str],
                      method: Optional[str]) -> None:
        """A lookup of the perk-implementation table: a template that can answer it makes
        the object depend on a copy."""
        from sushi_lang.semantics.generics.opaque import holds_opaque
        from sushi_lang.semantics.generics.perk_copies import answering_templates, template_base
        if table.templates is None or holds_opaque(ty):
            return
        base, _args = template_base(ty)
        if base is not None and answering_templates(table.templates, base, perk=perk,
                                                    method=method):
            self.refuse(f"a perk template can answer a question about {ty}")


@dataclass
class _Decision:
    """The decision for one candidate unit."""
    base_key: str
    reused: bool
    library: bool
    replay: list = field(default_factory=list)
    record: Optional[BodyRecord] = None


class UnitReuse:
    """The reuse decisions of one build."""

    def __init__(self, stdlib_directory: Optional[Path], library_directory: Path,
                 key_of: KeyOf) -> None:
        # `~/.sushi/cache/compiler/stdlib/<global key>/`, or None when the per-user cache
        # is not the user's alone: no stdlib unit is reused then.
        self.stdlib_directory = stdlib_directory
        # `__sushi_cache__/reuse/<global key>/`: the source library units.
        self.library_directory = library_directory
        self.key_of = key_of
        self._decisions: dict[str, _Decision] = {}
        self.fell_back: list[str] = []

    def _directory(self, unit: "Unit") -> Optional[Path]:
        return self.library_directory if unit.from_library else self.stdlib_directory

    def object_path(self, unit: "Unit", key: str) -> Path:
        return self._directory(unit) / f"{unit.name}.{key[:12]}.o"

    def record_path(self, unit: "Unit", key: str) -> Path:
        return self._directory(unit) / f"{unit.name}.{key[:12]}.json"

    def decide(self, units: Iterable["Unit"], tables: "SymbolTables", copies: list,
               drops: frozenset) -> None:
        """Before the per-unit loop: each candidate unit is reused when its cache holds its
        object and its record, and else it records its body."""
        for unit in units:
            if self._directory(unit) is None or not _candidate(unit, copies, tables):
                continue
            key = self.key_of(unit, copies, drops)
            replay = self._read_record(unit, key)
            if replay is not None and self.object_path(unit, key).is_file():
                self._decisions[unit.name] = _Decision(key, True, unit.from_library, replay)
            else:
                self._decisions[unit.name] = _Decision(key, False, unit.from_library,
                                                       record=BodyRecord())

    def _read_record(self, unit: "Unit", key: str) -> Optional[list]:
        try:
            data = json.loads(self.record_path(unit, key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("format") != RECORD_FORMAT:
            return None
        instances = data.get("instances")
        return instances if isinstance(instances, list) else None

    def is_reused(self, unit: "Unit") -> bool:
        """THE predicate: the unit skips its body passes, and its object comes from the
        cache."""
        decision = self._decisions.get(unit.name)
        return decision is not None and decision.reused

    def recording(self, unit: "Unit") -> Optional[BodyRecord]:
        """The record that the body passes of the unit fill, or None."""
        decision = self._decisions.get(unit.name)
        return None if decision is None else decision.record

    def replay(self, unit: "Unit", tables: "SymbolTables") -> Optional[list]:
        """The instance keys of the record of a reused unit, in the types of this build, or
        None when a type of the record is not in the tables."""
        keys = []
        for entry in self._decisions[unit.name].replay:
            try:
                template_unit, name, encoded = entry
                type_args = tuple(decode_type(arg, tables) for arg in encoded)
            except (TypeError, ValueError):
                return None
            if any(arg is None for arg in type_args):
                return None
            keys.append((template_unit, name, type_args))
        return keys

    def fall_back(self, unit: "Unit") -> None:
        """The unit runs its body passes after all, and its object is built."""
        self._decisions.pop(unit.name, None)
        self.fell_back.append(unit.name)

    def drifted(self, units: Iterable["Unit"], copies: list, drops: frozenset) -> list:
        """Each reused unit whose key changed after the copy fixpoint: it holds a copy now,
        or the `Drop` set grew."""
        return [unit for unit in units if self.is_reused(unit)
                and self.key_of(unit, copies, drops) != self._decisions[unit.name].base_key]

    def stored_key(self, unit: "Unit", final_key: str) -> Optional[str]:
        """The key of the unit in the reuse cache, or None when its object depends on the
        program (it holds a copy) and goes with the objects of the program units."""
        decision = self._decisions.get(unit.name)
        if decision is None or decision.base_key != final_key:
            return None
        return decision.base_key

    def record_text(self, unit: "Unit") -> Optional[str]:
        """The record of a unit whose body passes ran, as the text of its file, or None
        when the body asked a question that no record holds."""
        decision = self._decisions.get(unit.name)
        record = None if decision is None else decision.record
        if record is None or record.refusal is not None:
            return None
        return json.dumps({"format": RECORD_FORMAT, "unit": unit.name,
                           "instances": record.instances}, indent=1) + "\n"

    def reused_count(self, library: bool) -> int:
        """The reused stdlib units, or with `library` the reused source library units."""
        return sum(1 for decision in self._decisions.values()
                   if decision.reused and decision.library == library)
