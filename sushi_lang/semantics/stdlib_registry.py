"""Standard Library Function Registry"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Dict, Tuple, List
import importlib

if TYPE_CHECKING:
    pass


# Bundled Sushi-SOURCE stdlib modules: `use <path>` maps to a .sushi file that is
# parsed and merged as an ordinary compilation unit (no .bc). Its generic free
# functions are collected and monomorphized like any user generic -- nothing is
# emitted unless a program instantiates one. Distinct from KNOWN_MODULES (Python
# metadata for .bc/native modules) and the .bc virtual-unit table in stdlib_linker.
_SRC_SUSHI_ROOT = Path(__file__).resolve().parent.parent / "sushi_stdlib" / "src_sushi"

SOURCE_STDLIB_MODULES: Dict[str, Path] = {
    "collections/iter": _SRC_SUSHI_ROOT / "collections" / "iter.sushi",
    "compression/zlib": _SRC_SUSHI_ROOT / "compression" / "zlib.sushi",
    "encoding/msgpack": _SRC_SUSHI_ROOT / "encoding" / "msgpack.sushi",
    "io/buf": _SRC_SUSHI_ROOT / "io" / "buf.sushi",
    "io/contracts": _SRC_SUSHI_ROOT / "io" / "contracts.sushi",
    "io/fs": _SRC_SUSHI_ROOT / "io" / "fs.sushi",
    "io/path": _SRC_SUSHI_ROOT / "io" / "path.sushi",
    "net/dns": _SRC_SUSHI_ROOT / "net" / "dns.sushi",
    "net/error": _SRC_SUSHI_ROOT / "net" / "error.sushi",
    "net/ip": _SRC_SUSHI_ROOT / "net" / "ip.sushi",
    "net/tcp": _SRC_SUSHI_ROOT / "net" / "tcp.sushi",
    "net/udp": _SRC_SUSHI_ROOT / "net" / "udp.sushi",
    "net/url": _SRC_SUSHI_ROOT / "net" / "url.sushi",
    "toolchain/slib": _SRC_SUSHI_ROOT / "toolchain" / "slib.sushi",
}


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
    is_constant: bool = False
    get_return_type: Optional[Callable] = None
    validator: Optional[Callable] = None
    params: Optional[List] = None  # None=polymorphic, []=no args, [Type,...]=typed args
    is_variadic: bool = False  # True if the last param is a native '...T' collecting variadic
    # A constant's folded value, read once at discovery. The front end folds it and the
    # back end emits it from here, so no reader has to know which module declared it.
    value: Optional[object] = None


@dataclass
class StdlibModule:
    """Metadata for a stdlib module."""
    path: str
    python_module: any  # The imported Python module
    functions: Dict[str, StdlibFunction] = field(default_factory=dict)
    constants: Dict[str, StdlibFunction] = field(default_factory=dict)


# Stdlib functions whose last parameter is a native '...T' collecting variadic. Their
# param spec's last entry is the collected DynamicArrayType(T); trailing call arguments
# are collected (or a single `arr...` bloomed) into it, exactly like a user variadic.
_VARIADIC_STDLIB = {("process", "run")}

_param_specs_cache = None

def _get_param_specs():
    """Lazily build parameter specs for stdlib functions."""
    global _param_specs_cache
    if _param_specs_cache is not None:
        return _param_specs_cache

    from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType
    I32, I64, U64, F64, STRING = (
        BuiltinType.I32, BuiltinType.I64, BuiltinType.U64, BuiltinType.F64, BuiltinType.STRING
    )
    STRING_ARRAY = DynamicArrayType(BuiltinType.STRING)

    specs = {}

    for fn in ("sleep", "msleep", "usleep"):
        specs[("time", fn)] = [I64]
    specs[("time", "nanosleep")] = [I64, I64]
    specs[("time", "now")] = []
    specs[("time", "monotonic_ns")] = []

    specs[("env", "getenv")] = [STRING]
    specs[("env", "setenv")] = [STRING, STRING]

    for fn in ("getcwd", "getpid", "getuid"):
        specs[("process", fn)] = []
    specs[("process", "chdir")] = [STRING]
    specs[("process", "exit")] = [I32]
    specs[("process", "run")] = [STRING, STRING_ARRAY]

    for fn in ("abs", "min", "max"):
        specs[("math", fn)] = None
    for fn in ("sqrt", "floor", "ceil", "round", "trunc",
               "sin", "cos", "tan", "asin", "acos", "atan",
               "sinh", "cosh", "tanh", "log", "log2", "log10", "exp", "exp2"):
        specs[("math", fn)] = [F64]
    for fn in ("pow", "atan2", "hypot"):
        specs[("math", fn)] = [F64, F64]

    for fn in ("rand", "rand_f64"):
        specs[("random", fn)] = []
    specs[("random", "rand_range")] = [I32, I32]
    specs[("random", "srand")] = [U64]

    # `<io/files>` and `<net/socket>` keep their parameter types in ONE table each,
    # beside their generators, and every reader takes its row from there (#550).
    from sushi_lang.sushi_stdlib.src.io.files_funcs import FILES_SIGNATURES
    from sushi_lang.sushi_stdlib.src.net.socket_funcs import SOCKET_SIGNATURES
    from sushi_lang.sushi_stdlib.src.signatures import param_specs

    specs.update(param_specs("files", FILES_SIGNATURES))
    specs.update(param_specs("socket", SOCKET_SIGNATURES))

    _param_specs_cache = specs
    return _param_specs_cache


class StdlibRegistry:
    """Central registry for stdlib functions."""

    KNOWN_MODULES = {
        "time": "sushi_lang.sushi_stdlib.src.time",
        "math": "sushi_lang.sushi_stdlib.src.math",
        "sys/env": "sushi_lang.sushi_stdlib.src.sys.env",
        "sys/process": "sushi_lang.sushi_stdlib.src.sys.process",
        "random": "sushi_lang.sushi_stdlib.src.random",
        "io/files": "sushi_lang.sushi_stdlib.src.io.files_funcs",
        "net/socket": "sushi_lang.sushi_stdlib.src.net.socket_funcs",
        # io/stdio and collections/strings are NOT registry-driven and cannot
        # be listed here: they expose a METHOD interface
        # (is_builtin_stdio_method / is_builtin_string_method), while this
        # registry reads the free-FUNCTION interface (#247). Their methods
        # resolve through semantics/passes/types/method_registry.py instead.
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
        py_module = importlib.import_module(python_path)

        stdlib_module = StdlibModule(
            path=module_path,
            python_module=py_module
        )

        module_name = module_path.split('/')[-1]

        checker_name = f"is_builtin_{module_name}_function"
        checker = getattr(py_module, checker_name, None)

        type_resolver_name = f"get_builtin_{module_name}_function_return_type"
        type_resolver = getattr(py_module, type_resolver_name, None)

        validator_name = f"validate_{module_name}_function_call"
        validator = getattr(py_module, validator_name, None)

        missing = [name for name, symbol in ((checker_name, checker),
                                              (type_resolver_name, type_resolver),
                                              (validator_name, validator))
                   if not symbol]
        if missing:
            raise RuntimeError(
                f"stdlib registry: module '{module_path}' ({python_path}) is "
                f"missing {', '.join(missing)} -- a KNOWN_MODULES entry must "
                "expose the free-function interface"
            )

        self._discover_functions_heuristic(
            stdlib_module, module_name, checker, type_resolver, validator
        )

        constant_checker_name = f"is_builtin_{module_name}_constant"
        constant_checker = getattr(py_module, constant_checker_name, None)
        if constant_checker:
            self._discover_constants(stdlib_module, module_name, constant_checker, py_module)

        self._modules[module_path] = stdlib_module

    def _discover_functions_heuristic(
        self,
        module: StdlibModule,
        module_name: str,
        checker: Callable[[str], bool],
        type_resolver: Callable,
        validator: Callable
    ) -> None:
        """Discover functions using heuristic approach."""
        from sushi_lang.sushi_stdlib.src.io.files_funcs import FILE_UTILITY_FUNCTIONS
        from sushi_lang.sushi_stdlib.src.net.socket_funcs import SOCKET_FUNCTIONS

        common_names = {
            "time": ["sleep", "msleep", "usleep", "nanosleep", "now", "monotonic_ns"],
            "env": ["getenv", "setenv"],
            "process": ["getcwd", "chdir", "exit", "getpid", "getuid", "run"],
            "math": [
                "abs", "min", "max", "sqrt", "pow", "floor", "ceil", "round", "trunc",
                "sin", "cos", "tan",
                "asin", "acos", "atan", "atan2",
                "sinh", "cosh", "tanh",
                "log", "log2", "log10",
                "exp", "exp2",
                "hypot",
            ],
            "random": ["rand", "rand_range", "srand", "rand_f64"],
            # `files` and `socket` READ their lists rather than repeating them. The copies
            # had to be kept in step by hand, and a name in one and not the other is
            # invisible until a program calls it and gets CE2008 for a function the
            # compiler can emit.
            "files": FILE_UTILITY_FUNCTIONS,
            "socket": SOCKET_FUNCTIONS,
        }

        candidates = common_names.get(module_name, [])

        for name in candidates:
            if checker(name):
                # Different modules have different type_resolver signatures.
                if module_name in ["time", "env", "process", "random", "files", "socket"]:
                    def make_type_resolver(fn_name):
                        return lambda: type_resolver(fn_name)
                    get_ret_type = make_type_resolver(name)
                else:
                    def make_type_resolver_with_params(fn_name):
                        return lambda params: type_resolver(fn_name, params)
                    get_ret_type = make_type_resolver_with_params(name)

                def make_validator(fn_name):
                    return lambda sig: validator(fn_name, sig)

                param_spec = _get_param_specs().get((module_name, name))

                func = StdlibFunction(
                    name=name,
                    module_path=module.path,
                    is_constant=False,
                    get_return_type=get_ret_type,
                    validator=make_validator(name),
                    params=param_spec,
                    is_variadic=(module_name, name) in _VARIADIC_STDLIB
                )
                module.functions[name] = func
                self._function_lookup[(module.path, name)] = func

    def _discover_constants(
        self,
        module: StdlibModule,
        module_name: str,
        checker: Callable[[str], bool],
        py_module: any
    ) -> None:
        """Discover a module's constants: the names its checker accepts, with their values.

        The value getter is the second half of the protocol, and it is required: a
        constant the registry cannot fold is a name every reader would have to special-
        case, which is the bug #560 was (`tests/unit/test_stdlib_constants_take_the_ladder.py`).
        """
        from sushi_lang.semantics.typesys import BuiltinType

        common_constants = ["PI", "E", "TAU"]

        constant_getter_name = f"get_builtin_{module_name}_constant_value"
        getter = getattr(py_module, constant_getter_name, None)
        if getter is None:
            raise RuntimeError(
                f"stdlib registry: module '{module.path}' declares constants and is "
                f"missing {constant_getter_name}"
            )

        for name in common_constants:
            if checker(name):
                type_name, value = getter(name)
                const_type = BuiltinType(type_name)

                func = StdlibFunction(
                    name=name,
                    module_path=module.path,
                    is_constant=True,
                    get_return_type=lambda ty=const_type: ty,
                    validator=None,  # a constant takes no arguments to check
                    value=value,
                )
                module.constants[name] = func
                self._function_lookup[(module.path, name)] = func

    def register_module(self, module_path: str, imported_units: List[str]) -> None:
        """Register a module that was imported via 'use <module>'."""
        if module_path not in self._modules and module_path in self.KNOWN_MODULES:
            python_path = self.KNOWN_MODULES[module_path]
            self._discover_module(module_path, python_path)

    def get_function(self, module_path: str, function_name: str) -> Optional[StdlibFunction]:
        """Get function metadata by module and name."""
        return self._function_lookup.get((module_path, function_name))

    def is_stdlib_function(self, module_path: str, function_name: str) -> bool:
        """Check if a function is a stdlib function."""
        return (module_path, function_name) in self._function_lookup

    def get_module(self, module_path: str) -> Optional[StdlibModule]:
        """Get module metadata by path."""
        return self._modules.get(module_path)

    def get_all_modules(self) -> List[str]:
        """Get list of all registered module paths."""
        return list(self._modules.keys())


_global_registry: Optional[StdlibRegistry] = None


def get_stdlib_registry() -> StdlibRegistry:
    """Get the global stdlib registry instance."""
    global _global_registry
    if _global_registry is None:
        _global_registry = StdlibRegistry()
        _global_registry.discover_modules()
    return _global_registry


def lookup_stdlib_constant(name: str, scope: object) -> Optional[StdlibFunction]:
    """The registry constant a BARE name reaches in `scope`, or None.

    Section 8's ladder, its last rung for a value: a stdlib constant is a name a flat
    `use <module>` brings, so it answers only after a local, this unit's own declaration
    and every imported unit's, and only in a unit whose scope holds the module. A reader
    with no scope -- a scratch validator, a table built by hand -- sees every module,
    which is what `UnitScope.unrestricted()` means. Every reader of a bare name -- the
    scope pass, the inference visitor, the constant evaluator, the back end -- asks THIS
    and nothing module-specific, so a shadowed builtin cannot recur (#560).
    """
    registry = get_stdlib_registry()
    everything = scope is None or getattr(scope, "everything", True)
    module_paths = (registry.get_all_modules() if everything
                    else getattr(scope, "modules", ()))
    for module_path in module_paths:
        module = registry.get_module(module_path)
        record = module.constants.get(name) if module is not None else None
        if record is not None:
            return record
    return None
