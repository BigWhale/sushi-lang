"""Collection of FFI `unsafe external` declarations into an ExternalTable."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Tuple, TYPE_CHECKING

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType, Type
from sushi_lang.semantics.externs_manifest import RESERVED_EXTERNS
from sushi_lang.semantics.ffi_boundary import (
    intern_boundary_type, is_byte_buffer, is_c_abi_param, is_c_abi_type, is_c_abi_variable,
    nullable_payload,
)
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Program, ExternalBlock, ExternalDecl
    from sushi_lang.semantics.visibility import VisibilityTable


@dataclass
class ExternalSig:
    """A single collected foreign function signature."""
    name: str                      # Sushi-visible name
    link_name: str                 # C link symbol
    param_types: Tuple[Type, ...]  # Parameter types (C-ABI representable)
    ret_type: Optional[Type]       # Raw C return type
    namespace: str                 # Owning namespace
    is_variadic: bool = False      # Trailing untyped C varargs (`...`)
    name_span: Optional[Span] = None
    ret_span: Optional[Span] = None
    loc: Optional[Span] = None
    filename: Optional[str] = None  # The file it was declared in (#473)
    unit_name: Optional[str] = None  # The unit whose block binds the namespace (#503)


@dataclass
class ExternalVarSig:
    """A collected C global variable (#1090). Read-only from Sushi."""
    name: str                      # Sushi-visible name
    link_name: str                 # C link symbol
    ty: Optional[Type]             # A number, bool, ptr or the interned Maybe@(ptr)
    namespace: str
    name_span: Optional[Span] = None
    filename: Optional[str] = None
    unit_name: Optional[str] = None


@dataclass
class ExternalTable:
    """Namespace-keyed table of foreign function signatures and C global variables."""
    by_namespace: Dict[str, Dict[str, ExternalSig]] = field(default_factory=dict)
    variables: Dict[str, Dict[str, ExternalVarSig]] = field(default_factory=dict)

    def is_namespace(self, ns: str) -> bool:
        """True if `ns` is a registered external namespace."""
        return ns in self.by_namespace or ns in self.variables

    def lookup(self, ns: str, name: str) -> Optional[ExternalSig]:
        """Look up a foreign function by namespace and Sushi-visible name."""
        return self.by_namespace.get(ns, {}).get(name)

    def lookup_variable(self, ns: str, name: str) -> Optional[ExternalVarSig]:
        """Look up a C global variable by namespace and Sushi-visible name."""
        return self.variables.get(ns, {}).get(name)

    def add(self, sig: ExternalSig) -> None:
        self.by_namespace.setdefault(sig.namespace, {})[sig.name] = sig

    def add_variable(self, sig: ExternalVarSig) -> None:
        self.variables.setdefault(sig.namespace, {})[sig.name] = sig

    def records(self, ns: Optional[str] = None):
        """Every function and variable record, of one namespace or of all of them."""
        for table in (self.by_namespace, self.variables):
            for name_space, decls in table.items():
                if ns is None or name_space == ns:
                    yield from decls.values()


class ExternalCollector:
    """Collects `unsafe external` blocks into an ExternalTable."""

    def __init__(self, reporter: Reporter, externals: ExternalTable, enums: Any = None) -> None:
        self.r = reporter
        # A nullable pointer (`Maybe@(ptr)`) is interned here, so every reader of the
        # table meets the one Maybe the rest of the program names (#1085).
        self.enums = enums
        # The unit being collected. This pass shares one reporter across every
        # unit, so a record it stores has to remember its own file (#473).
        self.current_unit_file: Optional[str] = None
        self.current_unit_name: Optional[str] = None
        # Bound with the other five so one loop reaches all six. An external carries
        # no visibility (`NO_VISIBILITY`), so this collector files nothing and asks
        # nothing; the binding is there so a collector cannot be left out by omission.
        self.library_units: Set[str] = set()
        self.visibility: Optional[VisibilityTable] = None
        self.externals = externals

    def collect(self, root: 'Program') -> None:
        blocks = root.externals
        if not isinstance(blocks, list):
            return
        for block in blocks:
            self._collect_block(block)

    def _collect_block(self, block: 'ExternalBlock') -> None:
        # ABI must be "C" (CE5003). The type pass also reports this; collection
        # still registers the externals so call sites resolve.
        for decl in block.decls:
            self._collect_decl(block, decl)
        for var in block.variables:
            self._collect_var(block, var)

    def _collect_var(self, block: 'ExternalBlock', var) -> None:
        if (self.externals.lookup(block.namespace, var.name) is not None
                or self.externals.lookup_variable(block.namespace, var.name) is not None):
            er.emit(self.r, er.ERR.CE0101, var.name_span,
                    name=f"{block.namespace}.{var.name}")
            return
        self.externals.add_variable(ExternalVarSig(
            name=var.name, link_name=var.link_name,
            ty=intern_boundary_type(var.ty, self.enums), namespace=block.namespace,
            name_span=var.name_span, filename=self.current_unit_file,
            unit_name=self.current_unit_name))

    def _collect_decl(self, block: 'ExternalBlock', decl: 'ExternalDecl') -> None:
        sig = ExternalSig(
            name=decl.name,
            link_name=decl.link_name,
            param_types=tuple(intern_boundary_type(p.ty, self.enums) for p in decl.params),
            ret_type=intern_boundary_type(decl.ret, self.enums),
            namespace=block.namespace,
            is_variadic=decl.is_variadic,
            name_span=decl.name_span,
            ret_span=decl.ret_span,
            loc=decl.loc,
            filename=self.current_unit_file,
            unit_name=self.current_unit_name,
        )

        existing = self.externals.lookup(block.namespace, decl.name)
        if existing is not None:
            diag = er.emit_with(self.r, er.ERR.CE0101, decl.name_span,
                                name=f"{block.namespace}.{decl.name}")
            if existing.name_span is not None:
                diag.note_at("first defined here", existing.name_span, existing.filename)
            diag.emit()
            return

        self.externals.add(sig)


@dataclass(frozen=True)
class _Declared:
    """The first declaration of a link name: its C shape, and where it is written."""
    shape: tuple
    span: Optional[Span]
    filename: Optional[str]


def c_type(ty: Optional[Type]) -> str:
    """The C type a Sushi type crosses the boundary as. Two spellings of one C type
    give one answer: a `string` and a `Maybe@(string)` are both `char*`."""
    if ty is None or ty == BuiltinType.BLANK:
        return "void"
    ty = nullable_payload(ty) or ty
    if ty == BuiltinType.STRING:
        return "char*"
    if isinstance(ty, ForeignPtrType):
        return "void*"
    if is_byte_buffer(ty):
        return "uint8_t*"
    return display_type(ty)


def function_shape(params, ret: Optional[Type], variadic: bool) -> tuple:
    return ("fn", tuple(c_type(p) for p in params), c_type(ret), variadic)


class LinkNames:
    """Every declaration of a C symbol in the program, and the one shape it has (#1099).

    The built-ins that the compiler declares itself (`RESERVED_EXTERNS`) are the first
    declarations of their names, so a user declaration of `malloc` meets the same rule
    as a second user declaration of `abs`.
    """

    def __init__(self) -> None:
        self._first: Dict[str, _Declared] = {
            name: _Declared(function_shape(params, ret, variadic), None, None)
            for name, (params, ret, variadic) in RESERVED_EXTERNS.items()
        }

    def admit(self, reporter: Reporter, link_name: str, shape: tuple,
              span: Optional[Span], filename: Optional[str]) -> None:
        """CE5001: a second declaration of `link_name` with another C shape."""
        first = self._first.setdefault(link_name, _Declared(shape, span, filename))
        if first.shape == shape:
            return
        diag = er.emit_with(reporter, er.ERR.CE5001, span, symbol=link_name)
        if first.span is None:
            diag.note("the compiler declares this symbol itself, with another signature")
        else:
            diag.note_at("the other declaration of this symbol", first.span, first.filename)
        diag.emit()


def reject_disagreeing_link_names(reporter: Reporter, program: 'Program',
                                  externals: ExternalTable, link_names: LinkNames) -> None:
    """CE5001 for each declaration in one unit whose link name another declaration
    holds with another C shape. It runs after the link names are folded."""
    filename = reporter.filename
    for block in getattr(program, "externals", None) or ():
        for decl in block.decls:
            record = externals.lookup(block.namespace, decl.name)
            if (decl.link_name and _collected(record, decl, filename)
                    and _crosses(decl)):
                shape = function_shape([p.ty for p in decl.params], decl.ret, decl.is_variadic)
                link_names.admit(reporter, decl.link_name, shape,
                                 decl.name_span or decl.loc, filename)
        for var in block.variables:
            record = externals.lookup_variable(block.namespace, var.name)
            if (var.link_name and _collected(record, var, filename)
                    and is_c_abi_variable(var.ty)):
                link_names.admit(reporter, var.link_name, ("var", c_type(var.ty)),
                                 var.name_span or var.loc, filename)


def _crosses(decl: 'ExternalDecl') -> bool:
    """False for a signature that CE5003 refused: it has no C shape to compare."""
    return (all(is_c_abi_param(p.ty) for p in decl.params)
            and (decl.ret is None or is_c_abi_type(decl.ret)))


def _collected(record, decl, filename: Optional[str]) -> bool:
    """False for a declaration that CE0101 refused: the table holds another one."""
    return (record is not None and record.name_span == decl.name_span
            and record.filename == filename)
