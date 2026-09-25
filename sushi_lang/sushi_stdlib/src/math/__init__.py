"""Math module for Sushi standard library."""
from __future__ import annotations
import typing
from typing import Dict, Optional, Tuple

from llvmlite import ir

from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.sushi_stdlib.src.signatures import Signature, params_of

if typing.TYPE_CHECKING:
    from sushi_lang.semantics.typesys import Type


F64 = BuiltinType.F64
_UNARY = ('sqrt', 'floor', 'ceil', 'round', 'trunc',
          'sin', 'cos', 'tan', 'asin', 'acos', 'atan',
          'sinh', 'cosh', 'tanh', 'log', 'log2', 'log10', 'exp', 'exp2')
_BINARY = ('pow', 'atan2', 'hypot')

# The ONE spelling of what each f64 `<math>` function takes and answers (#827).
MATH_SIGNATURES: Dict[str, Signature] = {
    **{name: Signature(params_of(F64), bare=F64) for name in _UNARY},
    **{name: Signature(params_of(F64, F64), bare=F64) for name in _BINARY},
}

_SIGNED = (BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64)
_UNSIGNED = (BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64)
_FLOATS = (BuiltinType.F32, BuiltinType.F64)

# `abs`, `min` and `max` are a FAMILY: one row per argument type, every parameter and
# the answer of that one type, and the generated function is `sushi_<name>_<type>`.
MATH_FAMILIES: Dict[str, Tuple[int, Tuple[BuiltinType, ...]]] = {
    'abs': (1, _SIGNED + _FLOATS),
    'min': (2, _SIGNED + _UNSIGNED + _FLOATS),
    'max': (2, _SIGNED + _UNSIGNED + _FLOATS),
}


def family_row(name: str, ty) -> Optional[Signature]:
    """The row of family `name` at argument type `ty`, or None if the family has none."""
    family = MATH_FAMILIES.get(name)
    if family is None or ty not in family[1]:
        return None
    arity, _types = family
    return Signature(params_of(*([ty] * arity)), bare=ty)


def is_builtin_math_function(name: str) -> bool:
    """Check if name is a built-in math module function."""
    return name in MATH_SIGNATURES or name in MATH_FAMILIES


def is_builtin_math_constant(name: str) -> bool:
    """Check if name is a built-in math module constant."""
    return name in {'PI', 'E', 'TAU'}


def get_builtin_math_constant_value(name: str) -> tuple[str, float]:
    """Get the type and value for a built-in math constant."""
    constants = {
        'PI': ('f64', 3.141592653589793),
        'E': ('f64', 2.718281828459045),
        'TAU': ('f64', 6.283185307179586),
    }

    if name not in constants:
        raise ValueError(f"Unknown math constant: {name}")

    return constants[name]


def get_builtin_math_function_return_type(name: str, param_types: list[Type]) -> Type:
    """The declared return type, from the row (a family answers its argument type)."""
    if name in MATH_FAMILIES:
        if not param_types:
            raise TypeError(f"{name} requires at least one parameter")
        return param_types[0]
    sig = MATH_SIGNATURES.get(name)
    if sig is None:
        raise ValueError(f"Unknown math function: {name}")
    return sig.return_type()


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for math functions."""
    from sushi_lang.sushi_stdlib.src.math import operations
    from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module

    module = create_stdlib_module("math")

    operations.generate_abs_functions(module)
    operations.generate_min_max_functions(module)
    operations.generate_sqrt(module)
    operations.generate_pow(module)
    operations.generate_floor(module)
    operations.generate_ceil(module)
    operations.generate_round(module)
    operations.generate_trunc(module)

    operations.generate_sin(module)
    operations.generate_cos(module)
    operations.generate_tan(module)

    operations.generate_asin(module)
    operations.generate_acos(module)
    operations.generate_atan(module)
    operations.generate_atan2(module)

    operations.generate_sinh(module)
    operations.generate_cosh(module)
    operations.generate_tanh(module)

    operations.generate_log(module)
    operations.generate_log2(module)
    operations.generate_log10(module)

    operations.generate_exp(module)
    operations.generate_exp2(module)

    operations.generate_hypot(module)

    return module
