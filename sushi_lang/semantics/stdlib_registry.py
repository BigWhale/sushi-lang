"""Standard Library Function Registry"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, AbstractSet, Any, Callable, Optional, Dict, Sequence, Tuple, List
import importlib
import re

from sushi_lang.internals.host import host_key

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Program
    from sushi_lang.semantics.visibility import StdlibReach
    from sushi_lang.sushi_stdlib.src.signatures import Signature


# Bundled Sushi-SOURCE stdlib modules: `use <path>` maps to a .sushi file that is
# parsed and merged as an ordinary compilation unit (no .bc). Its generic free
# functions are collected and monomorphized like any user generic -- nothing is
# emitted unless a program instantiates one. Distinct from KNOWN_MODULES (Python
# metadata for .bc/native modules) and the .bc virtual-unit table in stdlib_linker.
_SRC_SUSHI_ROOT = Path(__file__).resolve().parent.parent / "sushi_stdlib" / "src_sushi"

# A module whose file differs per platform and architecture (#1089). The compiler has
# no conditional compilation: it selects a FILE, one per host, and every file declares
# the same names. The key is `<os>_<arch>` as `host_key()` spells the host.
PLATFORM_SOURCE_MODULES: Dict[str, Dict[str, Path]] = {
    "sys/platform": {
        key: _SRC_SUSHI_ROOT / "_platform" / f"{key}.sushi"
        for key in ("darwin_arm64", "linux_x86_64")
    },
}


def platform_source(module_path: str) -> Optional[Path]:
    """The file of a per-platform module for this host, or None when it has none."""
    return PLATFORM_SOURCE_MODULES[module_path].get(host_key())


SOURCE_STDLIB_MODULES: Dict[str, Path] = {
    "collections/iter": _SRC_SUSHI_ROOT / "collections" / "iter.sushi",
    "collections/sort": _SRC_SUSHI_ROOT / "collections" / "sort.sushi",
    "collections/strings": _SRC_SUSHI_ROOT / "collections" / "strings.sushi",
    "compression/zlib": _SRC_SUSHI_ROOT / "compression" / "zlib.sushi",
    "encoding/base64": _SRC_SUSHI_ROOT / "encoding" / "base64.sushi",
    "encoding/binary": _SRC_SUSHI_ROOT / "encoding" / "binary.sushi",
    "encoding/hex": _SRC_SUSHI_ROOT / "encoding" / "hex.sushi",
    "encoding/msgpack": _SRC_SUSHI_ROOT / "encoding" / "msgpack.sushi",
    "io/buf": _SRC_SUSHI_ROOT / "io" / "buf.sushi",
    "io/contracts": _SRC_SUSHI_ROOT / "io" / "contracts.sushi",
    "io/error": _SRC_SUSHI_ROOT / "io" / "error.sushi",
    "io/files": _SRC_SUSHI_ROOT / "io" / "files.sushi",
    "io/fs": _SRC_SUSHI_ROOT / "io" / "fs.sushi",
    "io/path": _SRC_SUSHI_ROOT / "io" / "path.sushi",
    "math": _SRC_SUSHI_ROOT / "math.sushi",
    "net/dns": _SRC_SUSHI_ROOT / "net" / "dns.sushi",
    "net/error": _SRC_SUSHI_ROOT / "net" / "error.sushi",
    "net/handle": _SRC_SUSHI_ROOT / "net" / "handle.sushi",
    "net/ip": _SRC_SUSHI_ROOT / "net" / "ip.sushi",
    "net/tcp": _SRC_SUSHI_ROOT / "net" / "tcp.sushi",
    "net/udp": _SRC_SUSHI_ROOT / "net" / "udp.sushi",
    "net/url": _SRC_SUSHI_ROOT / "net" / "url.sushi",
    "random": _SRC_SUSHI_ROOT / "random.sushi",
    "sys/env": _SRC_SUSHI_ROOT / "sys" / "env.sushi",
    "sys/process": _SRC_SUSHI_ROOT / "sys" / "process.sushi",
    "text/ascii": _SRC_SUSHI_ROOT / "text" / "ascii.sushi",
    "time": _SRC_SUSHI_ROOT / "time.sushi",
    "toolchain/slib": _SRC_SUSHI_ROOT / "toolchain" / "slib.sushi",
}
# A per-platform module is an ordinary source module on a host that has its file. On
# any other host the name stays known, and the injector says the host has no file.
SOURCE_STDLIB_MODULES.update({
    module: platform_source(module) or _SRC_SUSHI_ROOT / "_platform" / f"{host_key()}.sushi"
    for module in PLATFORM_SOURCE_MODULES
})


# The source modules that declare methods on the built-in types. A method of a built-in
# type needs no import (`docs/design/extension-visibility.md` R1), so a program that
# names one of these methods loads its module with no `use`. The load brings no name
# (R2): a name of the module still needs the import.
BUILTIN_TYPE_METHOD_MODULES = frozenset({
    "collections/iter", "collections/sort", "collections/strings", "encoding/binary",
    "math", "text/ascii",
})


def stdlib_imports(program: 'Program') -> tuple[str, ...]:
    """The stdlib module of each `use <...>` of one unit, as written and in order. The
    parent directory of a path is not an import of it (#501)."""
    return tuple(use_stmt.path for use_stmt in program.uses if use_stmt.is_stdlib)


def named_methods(program: 'Program') -> frozenset:
    """Every method name that one unit calls, or declares as an extension.

    A declaration counts, so that an extension which takes the name of a stdlib method
    on a built-in type meets that method and its refusal (C2).
    """
    from sushi_lang.semantics.ast import DotCall, ExtendDef, MethodCall
    from sushi_lang.semantics.ast_walk import walk_nodes

    names: set = set()

    def visit(node) -> bool:
        if isinstance(node, (MethodCall, DotCall)):
            names.add(node.method)
        elif isinstance(node, ExtendDef):
            names.add(node.name)
        return True

    walk_nodes(program, visit)
    return frozenset(names)


def builtin_type_method_bitcode(names: AbstractSet[str]) -> frozenset:
    """The stdlib modules whose bitcode holds a method of a built-in type named in
    `names`: the generator half of `<collections/strings>`. A build links such a module
    with no `use`, for the reason of R1."""
    from sushi_lang.sushi_stdlib.src.collections.strings import METHOD_SPECS
    return (frozenset({"collections/strings"}) if not names.isdisjoint(METHOD_SPECS)
            else frozenset())


# One `public extend` line: the target as written, then the method name, after `static`.
_DECLARED_METHOD = re.compile(
    r"public extend\s+((?:[^\s(]*\([^)]*\)|[^\s(]+)(?:\[\w*\])*)"
    r"\s+(?:static\s+)?([A-Za-z_]\w*)\s*[@(]")
# A type that a module declares.
_DECLARED_TYPE = re.compile(r"^(?:public )?(?:struct|enum|error) ([A-Za-z_]\w*)", re.M)


def may_declare_methods(source: str, names: AbstractSet[str]) -> bool:
    """Can the stdlib module with this source text declare a method on a built-in type
    that is named in `names`? A quick answer that is never a wrong "no", so that a build
    parses a module only when the answer can be "yes". `builtin_type_method_names`
    gives the exact answer.

    A public extension starts its line with `public extend`. A line whose method name
    this does not find answers "yes". A line whose target is a type that the module
    declares, with no type argument and no array suffix, is not on a built-in type.
    """
    own_types = set(_DECLARED_TYPE.findall(source))
    for line in source.splitlines():
        if not line.startswith("public extend"):
            continue
        declared = _DECLARED_METHOD.match(line)
        if declared is None:
            return True
        target, method = declared.groups()
        if target not in own_types and method in names:
            return True
    return False


def builtin_type_method_names(module: 'Program') -> frozenset:
    """The names of the public methods that one parsed stdlib module declares on the
    built-in types (R1)."""
    from sushi_lang.semantics.visibility import is_builtin_type
    return frozenset(
        extension.name
        for extension in [*module.extensions, *module.generic_extensions]
        if extension.is_public and is_builtin_type(extension.target_type))


def keep_builtin_type_methods(module: 'Program') -> None:
    """Reduce a parsed stdlib module to its methods on the built-in types (R1).

    A build loads a module for its methods when no unit imports it. That load brings no
    name (R2). So the types and the perks of the module are removed, with the methods,
    the perk implementations and the conversions that are declared on them, and its
    functions and constants become private. Nothing of the module then takes a name
    from a unit of the program, or hides one.
    """
    from sushi_lang.semantics.type_walk import spelled_names
    removed = {decl.name for decl in [*module.structs, *module.enums, *module.perks]}

    def keeps(decl) -> bool:
        spelled = {*spelled_names(decl.target_type),
                   *(spelled_names(decl.ret) if getattr(decl, "is_conversion", False)
                     else ())}
        return removed.isdisjoint(spelled) and getattr(decl, "perk_name", None) not in removed

    module.structs, module.enums, module.perks = [], [], []
    module.extensions = [decl for decl in module.extensions if keeps(decl)]
    module.generic_extensions = [decl for decl in module.generic_extensions if keeps(decl)]
    module.perk_impls = [decl for decl in module.perk_impls if keeps(decl)]
    module.generic_perk_impls = [decl for decl in module.generic_perk_impls if keeps(decl)]
    for decl in [*module.functions, *module.constants]:
        decl.is_public = False


def is_source_stdlib_module(module_path: str) -> bool:
    """True if `use <module_path>` resolves to a bundled Sushi-source module."""
    return module_path in SOURCE_STDLIB_MODULES


def resolve_source_stdlib_path(module_path: str) -> Optional[Path]:
    """Return the bundled .sushi Path for a source stdlib module, or None."""
    return SOURCE_STDLIB_MODULES.get(module_path)


@dataclass
class StdlibFunction:
    """Metadata for a single stdlib function."""
    name: str
    module_path: str
    get_return_type: Optional[Callable] = None
    params: Optional[List] = None  # None=polymorphic, []=no args, [Type,...]=typed args


@dataclass
class StdlibModule:
    """Metadata for a stdlib module."""
    path: str
    functions: Dict[str, StdlibFunction] = field(default_factory=dict)


_param_specs_cache = None

def _get_param_specs():
    """Lazily build parameter specs for stdlib functions."""
    global _param_specs_cache
    if _param_specs_cache is not None:
        return _param_specs_cache

    specs = {}

    # A module with a signature table keeps its parameter types in it, beside its
    # generators, and every reader takes its row from there (#550).
    from sushi_lang.sushi_stdlib.src.signatures import param_specs

    for module_path, table in signature_tables().items():
        specs.update(param_specs(module_path.split('/')[-1], table))

    _param_specs_cache = specs
    return _param_specs_cache


def signature_tables() -> Dict[str, Dict[str, "Signature"]]:
    """Every registry module's ONE signature table, keyed by its `use` path (#798)."""
    from sushi_lang.sushi_stdlib.src.io.files_funcs import FILES_SIGNATURES
    from sushi_lang.sushi_stdlib.src.net.socket_funcs import SOCKET_SIGNATURES

    return {
        "io/files": FILES_SIGNATURES,
        "net/socket": SOCKET_SIGNATURES,
    }


def _row_resolver(sig: "Signature") -> Callable:
    return lambda param_types=(): sig.return_type()


def stdlib_signature(module_path: str, name: str) -> Optional["Signature"]:
    """The row of one registry stdlib function, or None for a module with no table."""
    return signature_tables().get(module_path, {}).get(name)


def stdlib_arity(module_path: str, name: str) -> Optional[int]:
    """The number of parameters of a registry stdlib function, or None for no function."""
    row = stdlib_signature(module_path, name)
    return None if row is None else row.arity


#: The modules whose generated functions carry the name of their layer.
_SYMBOL_PREFIXES = {"io/files": "sushi_io_files_", "net/socket": "sushi_net_"}


def stdlib_symbol(module_path: str, name: str) -> str:
    """The symbol of the generated function that a call of a registry stdlib function
    calls: the ONE derivation (docs/design/ir.md 8.6). It is `sushi_<name>`
    (`sushi_io_files_<name>` and `sushi_net_<name>` for the two layers)."""
    return f"{_SYMBOL_PREFIXES.get(module_path, 'sushi_')}{name}"


class StdlibRegistry:
    """Central registry for stdlib functions."""

    KNOWN_MODULES = {
        "io/files": "sushi_lang.sushi_stdlib.src.io.files_funcs",
        "net/socket": "sushi_lang.sushi_stdlib.src.net.socket_funcs",
        # collections/strings is NOT registry-driven and cannot be listed here: it
        # exposes METHODS, while this registry reads the free-function rows of
        # signature_tables() (#247, #908). Its methods resolve through
        # semantics/passes/types/method_registry.py instead.
    }

    def __init__(self):
        self._modules: Dict[str, StdlibModule] = {}
        self._function_lookup: Dict[Tuple[str, str], StdlibFunction] = {}

    def discover_modules(self) -> None:
        """Discover and register all known stdlib modules.

        A failure here is a compiler configuration error, never a no-op: a
        KNOWN_MODULES entry that imports nothing or registers nothing used to
        be skipped in silence (#247).
        """
        for module_path, python_path in self.KNOWN_MODULES.items():
            try:
                self._discover_module(module_path, python_path)
            except ImportError as e:
                raise RuntimeError(
                    f"stdlib registry: KNOWN_MODULES entry '{module_path}' "
                    f"names '{python_path}', which does not import: {e}"
                ) from e

    def _discover_module(self, module_path: str, python_path: str) -> None:
        """Discover and register a single stdlib module."""
        importlib.import_module(python_path)
        stdlib_module = StdlibModule(path=module_path)
        module_name = module_path.split('/')[-1]

        self._discover_functions(stdlib_module, module_name, python_path)

        self._modules[module_path] = stdlib_module

    def _discover_functions(self, module: StdlibModule, module_name: str,
                            python_path: str) -> None:
        """Register one function per row of the module's table (#908).

        Every resolver has one shape: it takes the argument types, and a row ignores them
        and answers its declared type.
        """
        rows = signature_tables().get(module.path)
        if rows is None:
            raise RuntimeError(
                f"stdlib registry: module '{module.path}' ({python_path}) has no "
                "signature table -- a KNOWN_MODULES entry must have one in "
                "signature_tables()"
            )

        resolvers: Dict[str, Callable] = {name: _row_resolver(sig) for name, sig in rows.items()}

        for name, get_ret_type in resolvers.items():
            func = StdlibFunction(
                name=name,
                module_path=module.path,
                get_return_type=get_ret_type,
                params=_get_param_specs().get((module_name, name)),
            )
            module.functions[name] = func
            self._function_lookup[(module.path, name)] = func

    def get_function(self, module_path: str, function_name: str) -> Optional[StdlibFunction]:
        """Get function metadata by module and name."""
        return self._function_lookup.get((module_path, function_name))

    def get_module(self, module_path: str) -> Optional[StdlibModule]:
        """Get module metadata by path."""
        return self._modules.get(module_path)



_global_registry: Optional[StdlibRegistry] = None


def get_stdlib_registry() -> StdlibRegistry:
    """Get the global stdlib registry instance."""
    global _global_registry
    if _global_registry is None:
        _global_registry = StdlibRegistry()
        _global_registry.discover_modules()
    return _global_registry


def flat_stdlib_reach(units: Sequence[Any]) -> "StdlibReach":
    """The bundled stdlib units that the flat scope of each unit of the build reaches.

    A flat `use <m>` reaches `m`, and what `m` re-exports with a `public use`, to the end
    of the chain.
    """
    from sushi_lang.semantics.visibility import StdlibReach

    by_name = {unit.name: unit for unit in units}
    stdlib = frozenset(unit.name for unit in units
                       if unit.provenance is not None and not unit.from_library)

    def reexports(module_path: str) -> list:
        unit = by_name.get(module_path)
        if unit is not None and unit.ast is not None:
            return [use.path for use in unit.ast.uses if use.is_stdlib and use.is_public]
        return []

    flat = {}
    for unit in units:
        todo = ([use.path for use in unit.ast.uses if use.is_stdlib and use.alias is None]
                if unit.ast is not None else [])
        seen: set = set()
        while todo:
            module_path = todo.pop()
            if module_path not in seen:
                seen.add(module_path)
                todo.extend(reexports(module_path))
        flat[unit.name] = frozenset(seen)
    return StdlibReach(stdlib_units=stdlib, flat=flat)
