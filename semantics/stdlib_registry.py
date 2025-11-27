"""
Standard Library Function Registry

This module provides a unified registry for stdlib functions, eliminating hardcoded
special cases throughout the compiler. The registry auto-discovers stdlib modules
and provides type information, validators, and code emitters.

Design Principles:
- DRY: Single source of truth for stdlib metadata
- SOLID: Clean separation of concerns, extensible design
- Platform Agnostic: Respects existing platform detection
- Reuses existing stdlib module interfaces

Architecture:
- StdlibFunction: Metadata for individual function
- StdlibModule: Metadata for entire module
- StdlibRegistry: Central registry with discovery and lookup
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, Dict, Tuple, List
import importlib

if TYPE_CHECKING:
    from semantics.typesys import Type
    from semantics.symbols import Signature

@dataclass
class StdlibFunction:
    """
    Metadata for a single stdlib function.

    Attributes:
        name: Function name (e.g., "sleep", "getenv", "abs")
        module_path: Module path (e.g., "time", "sys/env", "math")
        is_constant: True if this is a constant (e.g., PI, E, TAU)
        get_return_type: Callable to get return type (may depend on params)
        validator: Callable to validate function call
    """
    name: str
    module_path: str
    is_constant: bool = False
    get_return_type: Optional[Callable] = None
    validator: Optional[Callable] = None


@dataclass
class StdlibModule:
    """
    Metadata for a stdlib module.

    Attributes:
        path: Module path (e.g., "time", "sys/env", "math")
        python_module: Imported Python module from stdlib/src/
        functions: Dict of function name -> StdlibFunction
        constants: Dict of constant name -> StdlibFunction
    """
    path: str
    python_module: any  # The imported Python module
    functions: Dict[str, StdlibFunction] = field(default_factory=dict)
    constants: Dict[str, StdlibFunction] = field(default_factory=dict)


class StdlibRegistry:
    """
    Central registry for stdlib functions.

    Provides:
    - Auto-discovery of stdlib modules
    - Function metadata lookup
    - Type information and validation
    - Code emitter dispatch
    """

    # Known stdlib modules and their Python module paths
    KNOWN_MODULES = {
        "time": "stdlib.src.time",
        "math": "stdlib.src.math",
        "sys/env": "stdlib.src.sys.env",
        "sys/process": "stdlib.src.sys.process",
        "random": "stdlib.src.random",
        "io/files": "stdlib.src.io.files_funcs",
        # Future modules can be added here
        # "io/stdio": "stdlib.src.io.stdio",
        # "collections/strings": "stdlib.src.collections.strings",
    }

    def __init__(self):
        self._modules: Dict[str, StdlibModule] = {}
        self._function_lookup: Dict[Tuple[str, str], StdlibFunction] = {}

    def discover_modules(self) -> None:
        """
        Discover and register all known stdlib modules.

        This method imports stdlib Python modules and extracts function metadata
        using the standard interface:
        - is_builtin_*_function(name) -> bool
        - get_builtin_*_function_return_type(name, ...) -> Type
        - validate_*_function_call(name, signature) -> None
        """
        for module_path, python_path in self.KNOWN_MODULES.items():
            try:
                self._discover_module(module_path, python_path)
            except ImportError as e:
                # Module not available (e.g., platform-specific)
                # This is not an error - some modules may be platform-specific
                pass

    def _discover_module(self, module_path: str, python_path: str) -> None:
        """
        Discover and register a single stdlib module.

        Args:
            module_path: Sushi module path (e.g., "time", "sys/env")
            python_path: Python import path (e.g., "stdlib.src.time")
        """
        # Import the Python module
        py_module = importlib.import_module(python_path)

        # Create module metadata
        stdlib_module = StdlibModule(
            path=module_path,
            python_module=py_module
        )

        # Extract module name for interface functions (e.g., "time" from "time", "env" from "sys/env")
        module_name = module_path.split('/')[-1]

        # Get the checker function (is_builtin_*_function)
        checker_name = f"is_builtin_{module_name}_function"
        checker = getattr(py_module, checker_name, None)

        # Get the type resolver function (get_builtin_*_function_return_type)
        type_resolver_name = f"get_builtin_{module_name}_function_return_type"
        type_resolver = getattr(py_module, type_resolver_name, None)

        # Get the validator function (validate_*_function_call)
        validator_name = f"validate_{module_name}_function_call"
        validator = getattr(py_module, validator_name, None)

        if not checker or not type_resolver or not validator:
            # Module doesn't follow standard interface, skip it
            return

        # Discover all functions by trying common function names
        # This is a heuristic - we could also add a list_functions() interface
        self._discover_functions_heuristic(
            stdlib_module, module_name, checker, type_resolver, validator
        )

        # Check for constants (math module has PI, E, TAU)
        constant_checker_name = f"is_builtin_{module_name}_constant"
        constant_checker = getattr(py_module, constant_checker_name, None)
        if constant_checker:
            self._discover_constants(stdlib_module, module_name, constant_checker, py_module)

        # Register the module
        self._modules[module_path] = stdlib_module

    def _discover_functions_heuristic(
        self,
        module: StdlibModule,
        module_name: str,
        checker: Callable[[str], bool],
        type_resolver: Callable,
        validator: Callable
    ) -> None:
        """
        Discover functions using heuristic approach.

        We try common function names and use the checker to verify existence.
        This works because stdlib modules have is_builtin_*_function() checks.
        """
        # Common function names per module
        common_names = {
            "time": ["sleep", "msleep", "usleep", "nanosleep"],
            "env": ["getenv", "setenv"],
            "process": ["getcwd", "chdir", "exit", "getpid", "getuid"],
            "math": ["abs", "min", "max", "sqrt", "pow", "floor", "ceil", "round", "trunc"],
            "random": ["rand", "rand_range", "srand", "rand_f64"],
            "files": ["exists", "is_file", "is_dir", "file_size", "remove", "rename", "copy", "mkdir", "rmdir"],
        }

        candidates = common_names.get(module_name, [])

        for name in candidates:
            if checker(name):
                # Create closures that capture the specific values
                # Note: Different modules have different type_resolver signatures
                if module_name in ["time", "env", "process", "random", "files"]:
                    # time, env, process, random, files: get_builtin_*_function_return_type(name) -> Type
                    def make_type_resolver(fn_name):
                        return lambda: type_resolver(fn_name)
                    get_ret_type = make_type_resolver(name)
                else:
                    # math: get_builtin_*_function_return_type(name, params) -> Type
                    def make_type_resolver_with_params(fn_name):
                        return lambda params: type_resolver(fn_name, params)
                    get_ret_type = make_type_resolver_with_params(name)

                def make_validator(fn_name):
                    return lambda sig: validator(fn_name, sig)

                func = StdlibFunction(
                    name=name,
                    module_path=module.path,
                    is_constant=False,
                    get_return_type=get_ret_type,
                    validator=make_validator(name)
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
        """
        Discover constants (e.g., PI, E, TAU in math module).
        """
        common_constants = ["PI", "E", "TAU"]

        # Get constant value getter
        constant_getter_name = f"get_builtin_{module_name}_constant_value"
        constant_getter = getattr(py_module, constant_getter_name, None)

        for name in common_constants:
            if checker(name):
                # Constants are treated as zero-parameter functions returning f64
                from semantics.typesys import BuiltinType

                func = StdlibFunction(
                    name=name,
                    module_path=module.path,
                    is_constant=True,
                    get_return_type=lambda: BuiltinType.F64,
                    validator=None  # Constants don't need validation
                )
                module.constants[name] = func
                self._function_lookup[(module.path, name)] = func

    def register_module(self, module_path: str, imported_units: List[str]) -> None:
        """
        Register a module that was imported via 'use <module>'.

        Args:
            module_path: Module path (e.g., "time", "sys/env")
            imported_units: List of all imported unit paths
        """
        if module_path not in self._modules and module_path in self.KNOWN_MODULES:
            # Lazy registration - only register when actually used
            python_path = self.KNOWN_MODULES[module_path]
            self._discover_module(module_path, python_path)

    def get_function(self, module_path: str, function_name: str) -> Optional[StdlibFunction]:
        """
        Get function metadata by module and name.

        Args:
            module_path: Module path (e.g., "time", "sys/env")
            function_name: Function name (e.g., "sleep", "getenv")

        Returns:
            StdlibFunction metadata or None if not found
        """
        return self._function_lookup.get((module_path, function_name))

    def is_stdlib_function(self, module_path: str, function_name: str) -> bool:
        """
        Check if a function is a stdlib function.

        Args:
            module_path: Module path (e.g., "time", "sys/env")
            function_name: Function name (e.g., "sleep", "getenv")

        Returns:
            True if function exists in stdlib, False otherwise
        """
        return (module_path, function_name) in self._function_lookup

    def get_module(self, module_path: str) -> Optional[StdlibModule]:
        """
        Get module metadata by path.

        Args:
            module_path: Module path (e.g., "time", "sys/env")

        Returns:
            StdlibModule metadata or None if not found
        """
        return self._modules.get(module_path)

    def get_all_modules(self) -> List[str]:
        """
        Get list of all registered module paths.

        Returns:
            List of module paths
        """
        return list(self._modules.keys())


# Global registry instance (created on-demand)
_global_registry: Optional[StdlibRegistry] = None


def get_stdlib_registry() -> StdlibRegistry:
    """
    Get the global stdlib registry instance.

    The registry is created and initialized on first access.

    Returns:
        Global StdlibRegistry instance
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = StdlibRegistry()
        _global_registry.discover_modules()
    return _global_registry
