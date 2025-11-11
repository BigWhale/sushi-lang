"""
Math module for Sushi standard library.

Provides mathematical functions and constants for numeric computations.

Available Constants:
    PI: f64 = 3.141592653589793
        Mathematical constant π (pi), the ratio of a circle's circumference to its diameter.

    E: f64 = 2.718281828459045
        Mathematical constant e (Euler's number), the base of natural logarithms.

    TAU: f64 = 6.283185307179586
        Mathematical constant τ (tau), equal to 2π.

Available Functions:
    abs(T value) -> T
        Returns the absolute value of a number.
        Supported types: i8, i16, i32, i64, f32, f64

    min(T a, T b) -> T
        Returns the smaller of two values.
        Supported types: i8, i16, i32, i64, u8, u16, u32, u64, f32, f64

    max(T a, T b) -> T
        Returns the larger of two values.
        Supported types: i8, i16, i32, i64, u8, u16, u32, u64, f32, f64

    sqrt(f64 x) -> f64
        Returns the square root of a number.

    pow(f64 base, f64 exponent) -> f64
        Returns base raised to the power of exponent.

    floor(f64 x) -> f64
        Returns the largest integer less than or equal to x.

    ceil(f64 x) -> f64
        Returns the smallest integer greater than or equal to x.

    round(f64 x) -> f64
        Returns x rounded to the nearest integer.

    trunc(f64 x) -> f64
        Returns x with the fractional part removed.

Example Usage:
    use <math>

    fn main() i32:
        # Constants
        let f64 circle_circumference = 2.0 * PI * 5.0  # radius = 5
        let f64 e_squared = E * E

        # Basic operations
        let i32 absolute = abs(-42)         # 42
        let i32 minimum = min(10, 20)       # 10
        let f64 maximum = max(3.14, 2.71)   # 3.14

        # Advanced math
        let f64 root = sqrt(16.0)           # 4.0
        let f64 power = pow(2.0, 10.0)      # 1024.0
        let f64 rounded = floor(3.7)        # 3.0

        return Result.Ok(0)

Implementation Notes:
    - All functions return bare types (wrapping in Result<T> happens at semantic level)
    - Constants are f64 with 15 decimal places (full IEEE 754 double precision)
    - abs() uses conditional negation for signed types, pass-through for unsigned
    - min/max use simple comparison and selection
    - sqrt, pow, floor, ceil, round, trunc use LLVM intrinsics for efficiency
"""
from __future__ import annotations
import typing
from llvmlite import ir

if typing.TYPE_CHECKING:
    from semantics.typesys import Type
    #from semantics.symbols import Signature

from stdlib.src import type_converters


def is_builtin_math_function(name: str) -> bool:
    """Check if name is a built-in math module function."""
    return name in {
        'abs',
        'min',
        'max',
        'sqrt',
        'pow',
        'floor',
        'ceil',
        'round',
        'trunc',
    }


def is_builtin_math_constant(name: str) -> bool:
    """Check if name is a built-in math module constant."""
    return name in {'PI', 'E', 'TAU'}


def get_builtin_math_constant_value(name: str) -> tuple[str, float]:
    """Get the type and value for a built-in math constant.

    Returns:
        Tuple of (type_name, value)
    """
    constants = {
        'PI': ('f64', 3.141592653589793),
        'E': ('f64', 2.718281828459045),
        'TAU': ('f64', 6.283185307179586),
    }

    if name not in constants:
        raise ValueError(f"Unknown math constant: {name}")

    return constants[name]


def get_builtin_math_function_return_type(name: str, param_types: list[Type]) -> Type:
    """Get the return type for a built-in math function."""
    from semantics.typesys import BuiltinType

    if name in {'abs', 'min', 'max'}:
        # These return the same type as their input(s)
        if not param_types:
            raise TypeError(f"{name} requires at least one parameter")
        return param_types[0]

    elif name in {'sqrt', 'pow', 'floor', 'ceil', 'round', 'trunc'}:
        # These always return f64
        return BuiltinType('f64')

    raise ValueError(f"Unknown math function: {name}")


def validate_math_function_call(name: str, signature: Signature) -> None:
    """Validate a call to a built-in math function."""
    from semantics.typesys import BuiltinType

    numeric_types = {
        BuiltinType('i8'), BuiltinType('i16'), BuiltinType('i32'), BuiltinType('i64'),
        BuiltinType('u8'), BuiltinType('u16'), BuiltinType('u32'), BuiltinType('u64'),
        BuiltinType('f32'), BuiltinType('f64'),
    }

    signed_int_types = {
        BuiltinType('i8'), BuiltinType('i16'), BuiltinType('i32'), BuiltinType('i64'),
    }

    float_types = {
        BuiltinType('f32'), BuiltinType('f64'),
    }

    if name == 'abs':
        # abs(T value) -> T where T is signed int or float
        if len(signature.params) != 1:
            raise TypeError(f"abs expects 1 argument, got {len(signature.params)}")

        param_type = signature.params[0].type
        if param_type not in (signed_int_types | float_types):
            raise TypeError(f"abs expects signed integer or float type, got {param_type}")

    elif name in {'min', 'max'}:
        # min/max(T a, T b) -> T where T is any numeric type
        if len(signature.params) != 2:
            raise TypeError(f"{name} expects 2 arguments, got {len(signature.params)}")

        param1_type = signature.params[0].type
        param2_type = signature.params[1].type

        if param1_type not in numeric_types:
            raise TypeError(f"{name} expects numeric type for first parameter, got {param1_type}")

        if param2_type not in numeric_types:
            raise TypeError(f"{name} expects numeric type for second parameter, got {param2_type}")

        if param1_type != param2_type:
            raise TypeError(f"{name} expects both parameters to have the same type, got {param1_type} and {param2_type}")

    elif name == 'sqrt':
        # sqrt(f64 x) -> f64
        if len(signature.params) != 1:
            raise TypeError(f"sqrt expects 1 argument, got {len(signature.params)}")

        param_type = signature.params[0].type
        if param_type != BuiltinType('f64'):
            raise TypeError(f"sqrt expects f64, got {param_type}")

    elif name == 'pow':
        # pow(f64 base, f64 exponent) -> f64
        if len(signature.params) != 2:
            raise TypeError(f"pow expects 2 arguments, got {len(signature.params)}")

        param1_type = signature.params[0].type
        param2_type = signature.params[1].type

        if param1_type != BuiltinType('f64'):
            raise TypeError(f"pow expects f64 for base, got {param1_type}")
        if param2_type != BuiltinType('f64'):
            raise TypeError(f"pow expects f64 for exponent, got {param2_type}")

    elif name in {'floor', 'ceil', 'round', 'trunc'}:
        # floor/ceil/round/trunc(f64 x) -> f64
        if len(signature.params) != 1:
            raise TypeError(f"{name} expects 1 argument, got {len(signature.params)}")

        param_type = signature.params[0].type
        if param_type != BuiltinType('f64'):
            raise TypeError(f"{name} expects f64, got {param_type}")


def generate_module_ir() -> ir.Module:
    """Generate LLVM IR module for math functions."""
    from stdlib.src.math import operations
    from stdlib.src.ir_common import create_stdlib_module

    module = create_stdlib_module("math")

    # Generate all math functions for supported types
    operations.generate_abs_functions(module)
    operations.generate_min_max_functions(module)
    operations.generate_sqrt(module)
    operations.generate_pow(module)
    operations.generate_floor(module)
    operations.generate_ceil(module)
    operations.generate_round(module)
    operations.generate_trunc(module)

    return module
