"""Collection of FFI `unsafe external` declarations into an ExternalTable."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Tuple, TYPE_CHECKING

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.externs_manifest import RESERVED_EXTERNS
from sushi_lang.semantics.ffi_boundary import intern_boundary_type

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

        # CE5001: clash with a reserved built-in extern of a DIFFERENT signature.
        self._check_reserved_clash(decl, sig)

        self.externals.add(sig)

    def _check_reserved_clash(self, decl: 'ExternalDecl', sig: ExternalSig) -> None:
        reject_reserved_clash(self.r, decl, sig)


def reject_reserved_clash(reporter: Reporter, decl: 'ExternalDecl', sig: ExternalSig) -> None:
    """CE5001: a link name a compiler built-in declares, with another signature.

    A link name written as a constant is empty at collection, and the `ffi-clash` step
    asks again once it has folded the name (#1089).
    """
    reserved = RESERVED_EXTERNS.get(sig.link_name)
    if reserved is None:
        return
    reserved_params, reserved_ret = reserved
    if not _abi_compatible(sig, reserved_params, reserved_ret):
        er.emit(reporter, er.ERR.CE5001, decl.name_span or decl.loc, symbol=sig.link_name)


def _abi_compatible(sig: ExternalSig, reserved_params, reserved_ret) -> bool:
    """True if `sig` and the reserved signature lower to the same C declaration."""
    from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType

    def abi_key(ty):
        if isinstance(ty, ForeignPtrType):
            return "i8*"
        if isinstance(ty, BuiltinType) and ty == BuiltinType.STRING:
            return "i8*"
        return ty

    if abi_key(sig.ret_type) != abi_key(reserved_ret):
        return False
    # `sig.param_types` holds only the fixed params (a trailing `...` is not a
    # param), so a variadic binding's fixed params must match the reserved fixed
    # params; the `...` then covers the built-in's var_arg.
    sig_params = tuple(abi_key(p) for p in sig.param_types)
    reserved_keys = tuple(abi_key(p) for p in reserved_params)
    return sig_params == reserved_keys
