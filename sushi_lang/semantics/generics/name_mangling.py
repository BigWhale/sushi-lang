"""Name mangling utilities for generic functions.

This module provides canonical name mangling for monomorphized generic functions,
ensuring consistency between the monomorphizer and call validator.
"""

from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from sushi_lang.semantics.types import Type


# Reserved pack-marker token. A separate "__pack{N}" segment is appended for
# pack instantiations. The no-pack path never emits a second "__" segment (it
# joins args with single "_"), so this marker can never collide with a regular
# generic symbol -- guaranteeing invariant (D) below.
_PACK_MARKER = "pack"


def mangle_function_name(
    base_name: str,
    type_args: Tuple['Type', ...],
    *,
    pack_arity: Optional[int] = None,
) -> str:
    """Generate mangled name for monomorphized generic function.

    Format (no pack): base_name + "__" + sanitized_type_args
    Format (pack):    <no-pack form> + "__pack{N}"   (N == pack_arity)

    Both the monomorphizer and the call validator MUST pass the same
    ``pack_arity`` for a given instantiation so the symbols agree (Phase 1 will
    wire the call-validator side; today only the monomorphizer passes it).

    The ``pack_arity`` keyword is the number of concrete types absorbed by a
    trailing type-pack (``len(TypePack.types)``). It carries four invariants:

      (A) ``pack_arity is None`` -> output is byte-for-byte identical to the
          historical no-pack implementation for ALL inputs (hard regression
          gate for every pre-existing regular generic).
      (B) distinct per arity: arities 0, 1, 3 of the same base yield three
          different symbols; an arity-0 pack does NOT collapse to ``base_name``.
      (C) deterministic & stable: a pure function of the inputs.
      (D) collision-free vs regular generics: a "__pack{N}" segment is never
          produced by the no-pack path (which only joins args with single "_"),
          so a pack symbol never equals a non-pack symbol of the same base.

    Examples:
        identity<i32>           -> identity__i32
        swap<i32, string>       -> swap__i32_string
        process<List<i32>>      -> process__List_i32
        f(i32,str,bool), arity=2 -> f__i32_str_bool__pack2
        f(i32), arity=0         -> f__i32__pack0
        f(), arity=0            -> f__pack0   (NOT "f")

    Args:
        base_name: Original function name
        type_args: Concrete (flat) type arguments; for a pack instantiation this
            is the leading types followed by the pack element types.
        pack_arity: ``None`` if no pack is involved; otherwise the (>= 0) number
            of trailing args absorbed by the pack.

    Returns:
        Mangled function name (guaranteed unique)
    """
    # --- No-pack path: byte-for-byte unchanged historical behavior ---
    if pack_arity is None:
        if not type_args:
            return base_name
        return f"{base_name}__{_join_sanitized(type_args)}"

    # --- Pack path: append a reserved arity-encoding marker segment ---
    if type_args:
        prefix = f"{base_name}__{_join_sanitized(type_args)}"
    else:
        prefix = base_name
    return f"{prefix}__{_PACK_MARKER}{pack_arity}"


def _join_sanitized(type_args: Tuple['Type', ...]) -> str:
    """Sanitize each type arg's string form and join with single underscores."""
    arg_strs = []
    for arg in type_args:
        # Get string representation
        type_str = str(arg)

        # Sanitize for use in identifier
        sanitized = (type_str
                     .replace('<', '_')
                     .replace('>', '')
                     .replace(',', '_')
                     .replace(' ', '')
                     .replace('[', '_arr')
                     .replace(']', '')
                     .replace('&', '_ref')
                     .replace('*', '_ptr'))

        arg_strs.append(sanitized)

    return '_'.join(arg_strs)
