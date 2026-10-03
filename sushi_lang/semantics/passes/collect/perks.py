"""Perk definition and implementation collection."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import ERR
from sushi_lang.semantics.visibility import (
    VisibilityTable, library_clash_origin, record_declaration,
    reject_library_clash, reject_private_perk_contract, taken_by_a_library)
from sushi_lang.semantics.ast import (
    Param, PerkDef, PerkMethodSignature, ExtendWithDef, FuncDef, Program)
from sushi_lang.semantics.passes.collect.unit_names import RefusedDeclarations
from sushi_lang.semantics.typesys import (
    Type, BuiltinType, StructType, EnumType, FunctionType, ReceiverType)
from sushi_lang.semantics.generics.extension_targets import RefusalRecord

from .utils import reject_reference_in, reject_try_in_body, reject_variadic_param


@dataclass
class PerkTable:
    """Registry of all defined perks."""
    by_name: Dict[str, PerkDef] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    # The unit each perk was defined in. A PerkDef is an AST node and carries no file,
    # and a duplicate is reported while ANOTHER unit is being collected (#473).
    files: Dict[str, Optional[str]] = field(default_factory=dict)
    # The `lib/<library>/<unit>` key of each perk a compiled library ships. Such a perk
    # has no visibility record, and the scope of a unit is asked for this key (#1124).
    library_units: Dict[str, str] = field(default_factory=dict)

    def register(self, perk: PerkDef) -> bool:
        """Register a perk. Returns False if duplicate."""
        if perk.name in self.by_name:
            return False
        self.by_name[perk.name] = perk
        self.order.append(perk.name)
        return True

    def get(self, name: str) -> Optional[PerkDef]:
        """Get a perk definition by name."""
        return self.by_name.get(name)


@dataclass
class GenericPerkImpl:
    """A perk implementation on a GENERIC target: `extend Box@(T) with Show`.

    A TEMPLATE, kept beside `GenericExtensionMethod` and read the same way. The
    implementation cannot be registered as it stands: the table is keyed by the
    instantiation the target names, and `Box@(T)` names none. One copy per
    instantiation of the base name is registered instead, after monomorphization.

    Every type in the methods' signatures is written in `TypeParameter`s, exactly as a
    generic extension method's is, so one substitution answers the whole signature.
    """
    base_type_name: str                    # "Box"
    type_params: Tuple[str, ...]           # ("T",) -- the names the target declares
    impl: ExtendWithDef                    # the template, signatures already converted
    unit_name: Optional[str] = None
    filename: Optional[str] = None


@dataclass
class GenericPerkImplTable(RefusalRecord):
    """The generic-target perk implementations, by the base name of their target.

    The refusal record holds the implementations whose target the collect pass refused,
    by the target and each method name, so a call of one adds no CE2008.
    """
    by_base: Dict[str, List[GenericPerkImpl]] = field(default_factory=dict)

    def add(self, template: GenericPerkImpl) -> None:
        self.by_base.setdefault(template.base_type_name, []).append(template)

    def templates(self, base_type_name: str) -> List[GenericPerkImpl]:
        return self.by_base.get(base_type_name, [])

    def __bool__(self) -> bool:
        return bool(self.by_base)


@dataclass
class PerkImplementationTable:
    """Tracks which types implement which perks."""
    implementations: Dict[Tuple[str, str], ExtendWithDef] = field(default_factory=dict)

    by_type: Dict[str, Set[str]] = field(default_factory=dict)

    by_perk: Dict[str, Set[str]] = field(default_factory=dict)

    # Which unit declared each implementation. Here and not on the collector, because
    # the question "may this implementation be replaced?" is asked of the table
    # (decision 11 of `docs/design/visibility.md`). A library manifest record has no
    # declaring unit, so the answer may be None.
    units: Dict[Tuple[str, str], Optional[str]] = field(default_factory=dict)

    def register(self, impl: ExtendWithDef, type_name: str,
                 *, unit_name: Optional[str] = None) -> bool:
        """Register an implementation. Returns False if duplicate."""
        key = (type_name, impl.perk_name)
        if key in self.implementations:
            return False  # Duplicate implementation

        self.implementations[key] = impl
        self.units[key] = unit_name

        if type_name not in self.by_type:
            self.by_type[type_name] = set()
        self.by_type[type_name].add(impl.perk_name)

        if impl.perk_name not in self.by_perk:
            self.by_perk[impl.perk_name] = set()
        self.by_perk[impl.perk_name].add(type_name)

        return True

    def replace(self, impl: ExtendWithDef, type_name: str,
                *, unit_name: Optional[str] = None) -> Optional[ExtendWithDef]:
        """Take a pair over, and hand back the implementation it displaced.

        The sanctioned override: a consumer's `extend X with P` wins over a library's
        (CLAUDE.md's method-resolution rule). The displaced body is real Sushi in a real
        unit, so the caller has to drop it from that unit's AST.
        """
        key = (type_name, impl.perk_name)
        previous = self.implementations.get(key)
        self.implementations[key] = impl
        self.units[key] = unit_name
        return previous

    def owner(self, type_name: str, perk_name: str) -> Optional[str]:
        """The unit that declared this implementation, if a unit declared it."""
        return self.units.get((type_name, perk_name))

    # Cuts the copy of an array template (`extend T[] with P`) for one array type, on
    # a miss (#699): an array has no instantiation set to cut it from ahead of time.
    # It takes the array type and the perk OR the method name that was asked, and cuts
    # only a template that gives it. The analyzer installs it for the length of the
    # analysis; with none, a miss is a miss. True when a copy was registered.
    on_array_miss: Optional[Callable[..., bool]] = field(
        default=None, repr=False, compare=False)

    def implements(self, type_name: str, perk_name: str) -> bool:
        """Check if a type implements a perk."""
        return (type_name, perk_name) in self.implementations

    def implements_type(self, ty: 'Type', perk_name: str) -> bool:
        """Does this TYPE implement the perk? An array template's copy is cut on a miss."""
        type_name = _get_type_name(ty)
        if type_name is None:
            return False
        if self.implements(type_name, perk_name):
            return True
        return (self._cut_on_miss(ty, perk=perk_name)
                and self.implements(type_name, perk_name))

    def _cut_on_miss(self, ty: 'Type', *, perk: Optional[str] = None,
                     method: Optional[str] = None) -> bool:
        from sushi_lang.semantics.typesys import DynamicArrayType
        return (self.on_array_miss is not None and isinstance(ty, DynamicArrayType)
                and self.on_array_miss(ty, perk=perk, method=method))

    def get(self, type_name: str, perk_name: str) -> Optional[ExtendWithDef]:
        """Get a specific perk implementation."""
        return self.implementations.get((type_name, perk_name))

    def get_method(self, target_type: 'Type', method_name: str) -> Optional['FuncDef']:
        """Get a specific perk method for a type."""
        type_name = _get_type_name(target_type)
        if type_name is None:
            return None
        found = self._method(type_name, method_name)
        if found is None and self._cut_on_miss(target_type, method=method_name):
            found = self._method(type_name, method_name)
        return found

    def _method(self, type_name: str, method_name: str) -> Optional['FuncDef']:
        for perk_name in self.by_type.get(type_name, set()):
            impl = self.implementations.get((type_name, perk_name))
            if impl:
                for method in impl.methods:
                    if method.name == method_name:
                        return method
        return None


def _get_type_name(ty: Optional[Type]) -> Optional[str]:
    """Extract a string name from a Type for use in perk implementation tables."""
    if ty is None:
        return None

    if isinstance(ty, BuiltinType):
        return str(ty)

    if isinstance(ty, StructType):
        return ty.name

    if isinstance(ty, EnumType):
        return ty.name

    # A generic target is keyed by the instantiation it names, so it must mangle the way the
    # type table does. This built the name itself and joined on ',' where the interned name
    # joins on ', ': `extend Pair@(i32, string) with P` registered under `Pair<i32,string>`
    # while the receiver resolved to `Pair<i32, string>`, so every call was CE2008 -- the
    # single-argument case worked, which is what hid it. One authority for the name (#393).
    from sushi_lang.semantics.generics.extension_targets import instantiation_key
    from sushi_lang.semantics.generics.types import GenericTypeRef
    if isinstance(ty, GenericTypeRef):
        return instantiation_key(ty.base_name, tuple(ty.type_args))

    # Every other written target -- a struct or enum name the collect pass has not
    # resolved yet, an array, a function type, `ptr`. `str` and not `display_type`: this
    # answer is a table KEY and has to be the INTERNAL identity name, the same `<...>`
    # the three arms above answer. `extend List@(i32)[] with P` is the case that shows
    # it -- the display form keys it `List@(i32)[]` while the receiver resolves to
    # `List<i32>[]`, which is #393 again.
    return str(ty)


def _is_built_in(ty: Optional[Type]) -> bool:
    """A type no unit declares: a primitive, an array, a built-in generic, a predefined enum."""
    from sushi_lang.semantics.generics.cloning import CONTAINER_BASES
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.predefined_types import PREDEFINED_ENUMS
    from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType, UnknownType
    if isinstance(ty, (BuiltinType, ArrayType, DynamicArrayType)):
        return True
    if isinstance(ty, GenericTypeRef):
        name = ty.base_name
    elif isinstance(ty, (UnknownType, StructType, EnumType)):
        name = ty.name
    else:
        return False
    return (name in CONTAINER_BASES or name in ("Maybe", "Result")
            or any(enum.name == name for enum in PREDEFINED_ENUMS))


@dataclass(frozen=True)
class _Written:
    """One implementation as written: the node, its file and its unit."""
    impl: ExtendWithDef
    filename: Optional[str]
    unit_name: Optional[str]


def _covering_base(ty: Optional[Type]) -> Optional[str]:
    """The base a template of this target covers: a generic name, or every array."""
    from sushi_lang.semantics.generics.extension_targets import ARRAY_BASE_KEY
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.typesys import DynamicArrayType
    if isinstance(ty, GenericTypeRef):
        return ty.base_name
    if isinstance(ty, DynamicArrayType):
        return ARRAY_BASE_KEY
    return None


class PerkCollector:
    """Collector for perk definitions and implementations."""

    def __init__(
        self,
        reporter: Reporter,
        perks: PerkTable,
        perk_impls: PerkImplementationTable,
        is_declared_type: Callable[[str], bool],
        generic_perk_impls: Optional[GenericPerkImplTable] = None,
    ) -> None:
        """Initialize perk collector."""
        self.r = reporter
        # Which bare names are declared types, for reading a generic target's arguments:
        # `Box@(T)` and `Box@(Point)` are spelled identically and mean opposite things.
        self.is_declared_type = is_declared_type
        # The unit being collected. This pass shares one reporter across every
        # unit, so a record it stores has to remember its own file (#473).
        self.current_unit_file: Optional[str] = None
        self.perks = perks
        self.perk_impls = perk_impls
        # The generic-target templates. One copy per instantiation is registered in
        # `perk_impls` after monomorphization; nothing here can be keyed by a target
        # that names a type parameter.
        self.generic_perk_impls = (generic_perk_impls if generic_perk_impls is not None
                                   else GenericPerkImplTable())
        # Which unit is being collected, which units came from a source library, and
        # which unit registered each impl. Together they let a consumer impl replace a
        # library one silently -- the rule a binary library already follows
        # (docs/design/libraries.md section 7).
        self.current_unit_name: Optional[str] = None
        self.refused = RefusedDeclarations()
        self.library_units: Set[str] = set()
        # Library impls a consumer replaced. Their bodies are real Sushi code in a real
        # unit, so unless they are dropped from that unit's AST the backend emits both
        # and the method symbol is defined twice. (A binary library has no such problem:
        # its body is weak_odr in the .slib object and the linker discards it.)
        self.shadowed_impls: List[ExtendWithDef] = []
        # Who declared what, for the perk-contract rule (CE4011).
        self.visibility: Optional[VisibilityTable] = None
        # The first template and the first concrete implementation of each perk on
        # each base, with the file and the unit that wrote it: the overlap rule.
        self._templates: Dict[Tuple[str, str], _Written] = {}
        self._concretes: Dict[Tuple[str, str], _Written] = {}

    def collect_definitions(self, root: Program) -> None:
        """Collect all perk definitions from program AST."""
        perks = root.perks
        if isinstance(perks, list):
            for perk in perks:
                if isinstance(perk, PerkDef) and self.refused.admits(perk):
                    self._collect_perk_def(perk)

    def collect_implementations(self, root: Program) -> None:
        """Collect all perk implementations from program AST.

        A GENERIC target is re-filed out of `perk_impls` and into
        `generic_perk_impls`, for the same reason a generic extension is: every later
        walk over `perk_impls` -- the typecheck pass, the backend's declaration and
        definition loops, the fingerprint -- assumes a concrete `self`.

        A REFUSED implementation leaves both lists. A method that declares its own type
        parameters can match no contract, so every later reader would tell the same
        fault again in its own words -- which is what the CE4004 and CE2001 beside the
        real answer were (#704).
        """
        perk_impls = root.perk_impls
        if isinstance(perk_impls, list):
            moved, refused = [], []
            for impl in perk_impls:
                if not isinstance(impl, ExtendWithDef):
                    continue
                if (self._reject_type_params_in_impl(impl)
                        or self._reject_function_target(impl, impl.target_type)):
                    refused.append(impl)
                elif self._collect_perk_impl(impl):
                    moved.append(impl)
            if moved or refused:
                dropped = {id(i) for i in (*moved, *refused)}
                root.perk_impls[:] = [i for i in perk_impls
                                      if id(i) not in dropped]
                root.generic_perk_impls.extend(moved)

    # `Drop`'s contract is fixed, and both halves of it are checked: the RECEIVER must be
    # `poke self`, because a destructor writes, and the return must be blank, because a
    # destructor has nowhere to put a Result (CE4012).
    DROP_PERK = "Drop"
    # `Hashable` is the contract of the derived `hash()`: every type the derive pass can
    # hash satisfies it with no implementation, and `extend T with Hashable` is the one
    # override of the derived hash (#696). The constraint check reads this name.
    HASHABLE_PERK = "Hashable"
    # The three contracts the operators, the interpolation hole and `println` read. The
    # compiler derives each for a struct and an enum, and an implementation is the
    # override. `Eq` and `Ord` compare with a second value of the implementing type, so
    # their contracts name it with the `ReceiverType` placeholder.
    EQ_PERK = "Eq"
    ORD_PERK = "Ord"
    DISPLAY_PERK = "Display"

    def _predefined_perks(self) -> List[PerkDef]:
        """The perks that ship with the compiler, public and importless."""
        return [
            PerkDef(
                loc=None,
                name=self.DROP_PERK,
                methods=[PerkMethodSignature(
                    name="drop", params=[], ret=BuiltinType.BLANK, self_mode="poke")],
                is_public=True,
            ),
            PerkDef(
                loc=None,
                name=self.HASHABLE_PERK,
                methods=[PerkMethodSignature(name="hash", params=[], ret=BuiltinType.U64)],
                is_public=True,
            ),
            PerkDef(
                loc=None,
                name=self.EQ_PERK,
                methods=[PerkMethodSignature(
                    name="eq", params=[Param(name="other", ty=ReceiverType())],
                    ret=BuiltinType.BOOL)],
                is_public=True,
            ),
            PerkDef(
                loc=None,
                name=self.ORD_PERK,
                methods=[PerkMethodSignature(
                    name="compare", params=[Param(name="other", ty=ReceiverType())],
                    ret=BuiltinType.I32)],
                is_public=True,
            ),
            PerkDef(
                loc=None,
                name=self.DISPLAY_PERK,
                methods=[PerkMethodSignature(name="to_str", params=[],
                                             ret=BuiltinType.STRING)],
                is_public=True,
            ),
        ]

    def register_predefined_perks(self) -> None:
        """Register the perks that ship with the compiler.

        `Drop` declares a resource (HANDLES.md ruling R2): a type that implements it owns
        something RAII must release, whatever its fields say. `Hashable` names the
        derived hash (#696): a type satisfies it when the derive pass can hash it, and
        an implementation is the override. `Eq`, `Ord` and `Display` follow the
        `Hashable` rule for equality, order and the string form. All of them stand
        beside the synthesized enums, so they need no import -- `owns_resource` and a
        constraint ask every program the question, so the answer has to exist in every
        program.
        """
        for perk in self._predefined_perks():
            if self.perks.get(perk.name) is not None:
                continue
            self.perks.register(perk)
            self.perks.files[perk.name] = None

    def _collect_perk_def(self, perk: PerkDef) -> None:
        """Collect perk definition and register in perk table."""
        name = perk.name
        if not isinstance(name, str):
            return
        record_declaration(self.visibility, "perk", perk,
                           unit_name=self.current_unit_name,
                           filename=self.current_unit_file)

        # The definition sweep collected this same declaration already, and the unit it
        # belongs to is being collected now. One node twice is not two perks, so the
        # duplicate rule below must not read it as one (#487).
        if self.perks.get(name) is perk:
            return

        name_span: Optional[Span] = perk.name_span or perk.loc

        # Perks cannot be generic (CE4010). The grammar parses `perk Name<T>:`
        # and the AST builder stores the params, but nothing consumes them - so
        # before this check a generic perk was silently accepted and inert.
        if perk.type_params:
            er.emit(self.r, ERR.CE4010, name_span, name=name)
            return

        # Variadic parameters are not allowed in perk methods (CE0115).
        # The pack half is unreachable today, but the guard must match its
        # documented contract and stay correct by construction (#246).
        for method in perk.methods or []:
            reject_variadic_param(self.r, method.params, name_span, "a perk method")

        # A perk method that promises to RETURN a borrow is the same unsound shape as a
        # plain function returning one (CE2417, #314): the implementation would hand out a
        # view of its own frame. Perk method PARAMETERS stay legal -- that is the one
        # supported reference position.
        for method in perk.methods or []:
            reject_reference_in(self.r, method.ret,
                                method.ret_span
                                or method.name_span or name_span,
                                ERR.CE2417)

        if self.perks.register(perk):
            self.perks.files[name] = self.current_unit_file
        else:
            if self._reject_library_clash(name, name_span):
                return
            prev = self.perks.get(name)
            prev_span = prev.name_span if prev else None
            diag = er.emit_with(self.r, ERR.CE4001, name_span, name=name)
            if prev_span is not None:
                diag.note_at("first defined here", prev_span, self.perks.files.get(name))
            diag.emit()
            return

    def _reject_library_clash(self, name: str, name_span: Optional[Span]) -> bool:
        """CE3011 when a library already took this name PRIVATELY (#705).

        The perk follows the type's rule, because the substance already matches one: the
        table is flat and one perk name is one perk per program. CE4001's note pointed
        into a file the visibility rules say the consumer cannot see, and the user's two
        options -- rename, or ask the library to export the perk -- are the ones CE3011
        leads to. A PUBLIC library perk keeps CE4001: the consumer can read both
        declarations, which is the case that code was written for.
        """
        clash = library_clash_origin(
            self.visibility, "perk", name,
            current_unit=self.current_unit_name, library_units=self.library_units)
        if clash is None or clash.is_public:
            return False
        reject_library_clash(self.r, clash, name_span, kind="perk", name=name,
                             filename=self.current_unit_file)
        return True

    def _reject_static_in_impl(self, impl: ExtendWithDef, perk_name: str) -> bool:
        """CE4014: a perk implementation may not declare a static method (#542, R1)."""
        refused = False
        for method in impl.methods or []:
            span = method.static_span
            if span is None:
                continue
            er.emit_with(self.r, ERR.CE4014, span,
                         perk=perk_name, method=method.name) \
                .help("declare it as a plain extension method on the type "
                      "('extend T static name(...)'); a perk contracts instance "
                      "methods only").emit()
            refused = True
        return refused

    def _reject_type_params_in_impl(self, impl: ExtendWithDef) -> bool:
        """CE4010: an implementation method declares no type parameters of its own.

        A perk cannot be generic, and a CONTRACT method has no `@(...)` slot in the
        grammar at all, so an implementation has no contract to match with one. The
        list was read by nothing: `@(U)` compiled and did nothing when the rest of the
        signature matched, and named an unknown type `U` when it did not (#704).

        The methods ride the shared `function_def`, which admits the list here for the
        reason it admits `public` and `static`: so this diagnostic can point at it.
        """
        perk_name = impl.perk_name if isinstance(impl.perk_name, str) else "?"
        refused = False
        for method in impl.methods or []:
            params = method.type_params or ()
            if not params:
                continue
            span = params[0].loc or method.name_span or method.loc
            er.emit_with(self.r, ERR.CE4010, span, name=perk_name) \
                .help("a perk contract declares no type parameters, so neither does "
                      "its implementation; a generic method is a plain extension "
                      "method ('extend T name@(U)(...)')").emit()
            refused = True
        return refused

    def _reject_bad_drop_target(self, impl: ExtendWithDef, target_type: Optional[Type],
                                type_name: str, span: Optional[Span]) -> bool:
        """The one target `Drop` refuses: a FOREIGN one. True when it was reported.

        CE4012 is ruling R2b: only the unit that declares a type may say what releasing
        it means. A type with no declaration record yet is THIS unit's own -- the record
        is written after the collectors run -- or is synthesized, and both are allowed.

        A GENERIC target reads its BASE name. `type_name` is the key the implementation
        registers under, and for a generic target that key carries the type arguments
        (`Crate<T>`, `Box<i32>`), which matches no declaration record -- so the rule
        would go silent on exactly the shape a generic `Drop` needs.
        """
        from sushi_lang.semantics.generics.types import GenericTypeRef
        if self.visibility is None:
            return False
        declared_name = (target_type.base_name
                         if isinstance(target_type, GenericTypeRef) else type_name)
        for kind in ("struct", "enum"):
            origin = self.visibility.origin(kind, declared_name)
            if origin is None or origin.unit_name is None:
                continue
            if origin.unit_name != self.current_unit_name:
                diag = er.emit_with(self.r, ERR.CE4012, span,
                                    type=declared_name, owner=origin.unit_name)
                if origin.name_span is not None:
                    diag.note_at(f"'{declared_name}' is declared here",
                                 origin.name_span, origin.filename)
                diag.emit()
                return True
        return False

    def _reject_second_home(self, impl: ExtendWithDef, type_name: str) -> bool:
        """CE4015: a method name that another perk already gives this type.

        A name has one home. Two perks that each provide `compare` on one type leave a
        call of it naming neither, and both bodies would take one symbol.
        """
        others = []
        for other_perk in sorted(self.perk_impls.by_type.get(type_name, set())):
            other = self.perk_impls.get(type_name, other_perk)
            if other is not None:
                others.append(_Written(other, None, None))
        return self._reject_shared_name(impl, others)

    def _reject_second_home_on_base(self, impl: ExtendWithDef, base: str, *,
                                    template: bool) -> bool:
        """CE4015 where a template takes part: the template and every type it covers.

        A perk's method names are its contract's, so one implementation of each other
        perk on the base is enough to read. A template meets the templates and the
        concrete implementations of its base; a concrete one meets the templates.
        """
        others = [_Written(t.impl, t.filename, t.unit_name)
                  for t in self.generic_perk_impls.templates(base)]
        if template:
            others += [w for (b, _perk), w in sorted(self._concretes.items())
                       if b == base]
        return self._reject_shared_name(impl, others)

    def _reject_shared_name(self, impl: ExtendWithDef, others: List[_Written]) -> bool:
        """One CE4015 for the first method name another perk's implementation gives."""
        for other in others:
            other_perk = other.impl.perk_name
            if other_perk == impl.perk_name:
                continue
            taken = {m.name: m for m in other.impl.methods}
            for method in impl.methods or []:
                previous = taken.get(method.name)
                if previous is None:
                    continue
                diag = er.emit_with(self.r, ERR.CE4015,
                                    method.name_span or method.loc,
                                    method=method.name, perk=impl.perk_name,
                                    other=other_perk)
                prev_span = previous.name_span or previous.loc
                if prev_span is not None:
                    diag.note_at(f"perk '{other_perk}' provides '{method.name}' here",
                                 prev_span, other.filename)
                diag.emit()
                return True
        return False

    def _refuse_methods(self, base_type_name: str, impl: ExtendWithDef) -> None:
        """Record every method of a refused implementation, so its calls stay silent."""
        for method in impl.methods or []:
            self.generic_perk_impls.refuse(base_type_name, method.name)

    def _reject_function_target(self, impl: ExtendWithDef,
                                target_type: Optional[Type]) -> bool:
        """CE2110: a function type or a tuple type is not a perk-implementation target.

        The extension path's rule (#771, #864, and ruling 5 of tuples). The caller drops the implementation from both
        lists, and its methods are recorded, so the one diagnostic is this.
        """
        from sushi_lang.semantics.generics.tuples import is_tuple_type
        if isinstance(target_type, FunctionType):
            kind = "function type"
        elif is_tuple_type(target_type):
            kind = "tuple type"
        else:
            return False
        from sushi_lang.semantics.generics.type_display import display_type
        target = display_type(target_type)
        er.emit(self.r, ERR.CE2110, impl.target_type_span or impl.perk_name_span,
                kind=kind, target=target)
        self._refuse_methods(target, impl)
        return True

    def _register_generic_template(self, impl: ExtendWithDef,
                                   target_type: Optional[Type]) -> bool:
        """Register a GENERIC-target implementation as a template. True when it is one.

        The signature of every method is rewritten in `TypeParameter`s over the names
        the target declares, exactly as `_collect_extension_def` does for a generic
        extension method, so one substitution answers the whole signature later.

        True also when the target was REFUSED. The implementation then leaves
        `perk_impls` and registers nowhere, and its methods are recorded by the base
        name, which is what keeps one fault to one diagnostic: a call of one adds no
        CE2008 (#860).
        """
        from sushi_lang.semantics.generics.extension_targets import (
            classify_extension_target, reject_mixed_target, reject_unwritable_target)
        from sushi_lang.semantics.generics.types import GenericTypeRef

        if not isinstance(target_type, GenericTypeRef):
            return False
        shape = classify_extension_target(target_type, self.is_declared_type)
        span = impl.target_type_span or impl.perk_name_span
        if (reject_mixed_target(self.r, target_type, shape, span, "perk-implementation")
                or reject_unwritable_target(self.r, shape, self.is_declared_type, span)):
            self._refuse_methods(target_type.base_name, impl)
            return True
        if not shape.param_names:
            return False
        if (self._reject_overlap(impl, target_type, target_type.base_name, template=True)
                or self._reject_second_home_on_base(impl, target_type.base_name,
                                                    template=True)):
            return True

        self._add_template(impl, target_type.base_name, shape.param_names)
        return True

    def _register_array_template(self, impl: ExtendWithDef,
                                 target_type: Optional[Type]) -> bool:
        """Register an `extend T[] with P` implementation as a template. True when it is one.

        The element position reads as an array extension's does: a bare undeclared name
        is the type parameter, a declared or built-in name is ONE array type and
        registers as it stands, and anything else is CE2101. True also when the target
        was refused.
        """
        from sushi_lang.semantics.generics.extension_targets import (
            ARRAY_BASE_KEY, classify_array_extension_target, reject_array_target)
        from sushi_lang.semantics.typesys import DynamicArrayType

        if not isinstance(target_type, DynamicArrayType):
            return False
        element = target_type.base_type
        shape = classify_array_extension_target(element, self.is_declared_type)
        if reject_array_target(self.r, shape, element,
                               impl.target_type_span or impl.perk_name_span):
            return True
        if not shape.param_names:
            return False
        if (self._reject_overlap(impl, target_type, ARRAY_BASE_KEY, template=True)
                or self._reject_second_home_on_base(impl, ARRAY_BASE_KEY, template=True)):
            return True
        self._add_template(impl, ARRAY_BASE_KEY, shape.param_names)
        return True

    def _add_template(self, impl: ExtendWithDef, base_type_name: str,
                      type_params: Tuple[str, ...]) -> None:
        """File a template, its method signatures written in `TypeParameter`s."""
        from sushi_lang.semantics.passes.collect.functions import deep_type_params

        for method in impl.methods or []:
            method.ret = deep_type_params(method.ret, type_params)
            method.err_type = deep_type_params(method.err_type, type_params)
            for param in method.params:
                param.ty = deep_type_params(param.ty, type_params)

        self.generic_perk_impls.add(GenericPerkImpl(
            base_type_name=base_type_name,
            type_params=type_params,
            impl=impl,
            unit_name=self.current_unit_name,
            filename=self.current_unit_file,
        ))

    def _reject_overlap(self, impl: ExtendWithDef, target_type: Optional[Type],
                        base: str, *, template: bool) -> bool:
        """CE4002 for a second implementation of one perk that covers the same type.

        A template covers every instantiation of its base, so a concrete implementation
        of the same perk on that base is a second implementation for one type, in either
        order, and two templates are the same fault. Sushi has no specialization: the
        most specific does not win (`docs/design/method-resolution.md`). A library's
        implementation stays the one a consumer may replace (`taken_by_a_library`).
        """
        key = (base, impl.perk_name)
        first = self._templates.get(key)
        if first is None and template:
            first = self._concretes.get(key)
        if first is None or taken_by_a_library(
                first.unit_name, current_unit=self.current_unit_name,
                library_units=self.library_units):
            record = self._templates if template else self._concretes
            record.setdefault(key, _Written(impl, self.current_unit_file,
                                            self.current_unit_name))
            return False
        self._emit_duplicate_impl(impl, target_type, first)
        return True

    def _emit_duplicate_impl(self, impl: ExtendWithDef, target_type: Optional[Type],
                             first: Optional[_Written]) -> None:
        """CE4002, with a note at the implementation that came first."""
        from sushi_lang.semantics.generics.type_display import display_type
        diag = er.emit_with(self.r, ERR.CE4002, impl.loc,
                            type=display_type(target_type), perk=impl.perk_name)
        if first is not None:
            span = first.impl.target_type_span or first.impl.loc
            if span is not None:
                diag.note_at("this implementation already covers that type",
                             span, first.filename)
        diag.help("Sushi has no specialization: implement the perk once, on the "
                  "template or on each concrete target").emit()

    def _collect_perk_impl(self, impl: ExtendWithDef) -> bool:
        """Collect one perk implementation. Answers True when it is a TEMPLATE.

        A template is re-filed by the caller: its target names a type parameter, so it
        registers nothing here and one copy per instantiation is registered later.
        """
        perk_name = impl.perk_name
        if not isinstance(perk_name, str):
            return False

        # A `??` has no error channel in a BARE perk-impl body (CE0131, #398), the
        # rule every bare body has.
        from sushi_lang.semantics.channel import has_channel
        for method in impl.methods or []:
            if not has_channel(method):
                reject_try_in_body(self.r, method.body, "a perk method")

        # A perk has no `Self` (HANDLES.md R7), so a contract cannot hold a
        # constructor. The grammar admits the marker here only so this diagnostic can
        # point at it (#542, ruling R1).
        if self._reject_static_in_impl(impl, perk_name):
            return False

        for method in impl.methods or []:
            reject_variadic_param(self.r, method.params,
                                  method.name_span or impl.loc, "a perk method")

        perk_name_span: Optional[Span] = impl.perk_name_span or impl.loc
        target_type: Optional[Type] = impl.target_type

        # `extend peek T with P` has the same problem as a reference extension target
        # (CE2420, #319): the implementation is registered against a type no receiver ever
        # resolves to, so it is unreachable.
        if reject_reference_in(self.r, target_type,
                               impl.target_type_span
                               or impl.loc, ERR.CE2420):
            return False

        type_name = _get_type_name(target_type)
        if type_name is None:
            return False

        if not self.perks.get(perk_name):
            # The one diagnostic of the fault. Its calls stay silent.
            er.emit(self.r, ERR.CE4003, perk_name_span, perk=perk_name)
            from sushi_lang.semantics.generics.types import GenericTypeRef
            self._refuse_methods(target_type.base_name
                                 if isinstance(target_type, GenericTypeRef)
                                 else type_name, impl)
            return False

        if perk_name == self.DROP_PERK and _is_built_in(target_type):
            # Refused whole: it leaves `perk_impls`, so no later pass reads its target.
            from sushi_lang.semantics.generics.type_display import display_type
            er.emit(self.r, ERR.CE4016, impl.target_type_span or perk_name_span,
                    type=display_type(target_type))
            self._refuse_methods(type_name, impl)
            return True

        if perk_name == self.DROP_PERK and self._reject_bad_drop_target(
                impl, target_type, type_name, perk_name_span):
            return False

        # Ruling 3: a private perk keeps its CONTRACT, so another unit may not implement
        # it. The method it provides stays callable, which is the rest of the ruling.
        if reject_private_perk_contract(
                self.r, self.visibility, perk_name, perk_name_span,
                action="implement", current_unit=self.current_unit_name,
                filename=self.current_unit_file):
            return False

        if (self._register_generic_template(impl, target_type)
                or self._register_array_template(impl, target_type)):
            return True

        base = _covering_base(target_type)
        if base is not None and (
                self._reject_overlap(impl, target_type, base, template=False)
                or self._reject_second_home_on_base(impl, base, template=False)):
            return False

        if self._reject_second_home(impl, type_name):
            return False

        if not self.perk_impls.register(impl, type_name,
                                        unit_name=self.current_unit_name):
            owner = self.perk_impls.owner(type_name, perk_name)
            if not taken_by_a_library(owner, current_unit=self.current_unit_name,
                                      library_units=self.library_units):
                first = self.perk_impls.get(type_name, perk_name)
                self._emit_duplicate_impl(
                    impl, target_type,
                    _Written(first, self.current_unit_file, owner)
                    if first is not None and owner == self.current_unit_name else None)
                return False
            previous = self.perk_impls.replace(impl, type_name,
                                               unit_name=self.current_unit_name)
            if previous is not None:
                self.shadowed_impls.append(previous)
        return False
