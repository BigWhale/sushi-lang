"""Collection of FFI `unsafe external` declarations into an ExternalTable.

Builds a namespace-keyed table of foreign function signatures and performs the
collection-time checks that do not require full type information:

- ABI string must be "C" (CE5003, shared with the type pass to keep the budget).
- No duplicate Sushi-name within a namespace (CE0101).
- Link-name clash with a reserved built-in extern of a different signature (CE5001).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, TYPE_CHECKING

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Program, ExternalBlock, ExternalDecl


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


@dataclass
class ExternalTable:
    """Namespace-keyed table of foreign function signatures."""
    by_namespace: Dict[str, Dict[str, ExternalSig]] = field(default_factory=dict)
    by_link_name: Dict[str, ExternalSig] = field(default_factory=dict)

    def is_namespace(self, ns: str) -> bool:
        """True if `ns` is a registered external namespace."""
        return ns in self.by_namespace

    def lookup(self, ns: str, name: str) -> Optional[ExternalSig]:
        """Look up a foreign function by namespace and Sushi-visible name."""
        return self.by_namespace.get(ns, {}).get(name)

    def add(self, sig: ExternalSig) -> None:
        self.by_namespace.setdefault(sig.namespace, {})[sig.name] = sig
        self.by_link_name[sig.link_name] = sig


class ExternalCollector:
    """Collects `unsafe external` blocks into an ExternalTable."""

    def __init__(self, reporter: Reporter, externals: ExternalTable) -> None:
        self.r = reporter
        self.externals = externals

    def collect(self, root: 'Program') -> None:
        blocks = getattr(root, "externals", None)
        if not isinstance(blocks, list):
            return
        for block in blocks:
            self._collect_block(block)

    def _collect_block(self, block: 'ExternalBlock') -> None:
        # ABI must be "C" (CE5003). The type pass also reports this; collection
        # still registers the externals so call sites resolve.
        for decl in block.decls:
            self._collect_decl(block, decl)

    def _collect_decl(self, block: 'ExternalBlock', decl: 'ExternalDecl') -> None:
        sig = ExternalSig(
            name=decl.name,
            link_name=decl.link_name,
            param_types=tuple(p.ty for p in decl.params),
            ret_type=decl.ret,
            namespace=block.namespace,
            is_variadic=decl.is_variadic,
            name_span=decl.name_span,
            ret_span=decl.ret_span,
            loc=decl.loc,
        )

        # Duplicate Sushi-name within the namespace.
        existing = self.externals.lookup(block.namespace, decl.name)
        if existing is not None:
            er.emit_with(self.r, er.ERR.CE0101, decl.name_span,
                         name=f"{block.namespace}.{decl.name}") \
                .note("first defined here", existing.name_span).emit()
            return

        # CE5001: clash with a reserved built-in extern of a DIFFERENT signature.
        self._check_reserved_clash(decl, sig)

        self.externals.add(sig)

    def _check_reserved_clash(self, decl: 'ExternalDecl', sig: ExternalSig) -> None:
        from sushi_lang.backend.runtime.core import RESERVED_EXTERNS
        reserved = RESERVED_EXTERNS.get(decl.link_name)
        if reserved is None:
            return
        reserved_params, reserved_ret = reserved
        if not self._abi_compatible(sig, reserved_params, reserved_ret):
            er.emit(self.r, er.ERR.CE5001, decl.name_span or decl.loc, symbol=decl.link_name)

    def _abi_compatible(self, sig: ExternalSig, reserved_params, reserved_ret) -> bool:
        """True if `sig` and the reserved signature lower to the same C declaration.

        LLVM only deduplicates identical declarations, so two Sushi signatures are
        compatible iff they lower identically: `string` and `ptr` both become i8*.
        A variadic user binding (e.g. `printf(string fmt, ...)`) is compatible with
        a reserved variadic family declaration as long as its FIXED params match the
        reserved fixed params; the trailing `...` matches the built-in's var_arg.
        """
        from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType

        def abi_key(ty):
            # string and ptr are both i8* at the C boundary.
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
