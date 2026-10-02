"""Name mangling utilities for generic functions."""

from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from sushi_lang.semantics.types import Type


# Reserved pack-marker token. A ".pack{N}" segment is appended for pack
# instantiations. The "." separator is what makes invariant (D) STRUCTURAL
# rather than probabilistic, and `tests/unit/test_pack_mangling.py` is where
# invariant (D) is stated and gated.
_PACK_MARKER = "pack"

# The pack marker's separator. A "." is chosen because it lies OUTSIDE the alphabet of
# every other symbol component -- identifiers and sanitized type args are [A-Za-z0-9_]
# only -- so the marker cannot occur in a no-pack symbol. LLVM accepts it (`llvm.*`).
_PACK_SEP = "."


def mangle_function_name(
    base_name: str,
    type_args: Tuple['Type', ...],
    *,
    pack_arity: Optional[int] = None,
) -> str:
    """Generate mangled name for monomorphized generic function."""
    if pack_arity is not None and pack_arity < 0:
        raise ValueError(f"pack_arity must be >= 0, got {pack_arity}")

    if pack_arity is None:
        if not type_args:
            return base_name
        return f"{base_name}__{_join_sanitized(type_args)}"

    if type_args:
        prefix = f"{base_name}__{_join_sanitized(type_args)}"
    else:
        prefix = base_name
    return f"{prefix}{_PACK_SEP}{_PACK_MARKER}{pack_arity}"


def extension_symbol(receiver_display: str, method: str,
                     method_type_args: Tuple['Type', ...] = ()) -> str:
    """The ONE symbol of an extension-method instance.

    Three consumers agree through this helper: the declaration, the call site, and the
    dedup of the copies. The `__{margs}` suffix appears only when method-level type
    arguments exist, so every pre-existing extension symbol is unchanged -- and two
    different solved U's on one receiver are two symbols, never one colliding body.
    """
    base = f"{sanitize_extension_receiver(receiver_display)}_{method}"
    if method_type_args:
        return f"{base}__{_join_sanitized(tuple(method_type_args))}"
    return base


def extension_receiver_name(target_type: Optional['Type']) -> str:
    """The receiver text an extension declaration's symbol is built from.

    ONE rule for the backend that declares the method and the manifest that names the
    symbol a binary library defines: a builtin spells its keyword, anything else its
    interned name.
    """
    from sushi_lang.semantics.typesys import BuiltinType

    if isinstance(target_type, BuiltinType):
        return target_type.value
    return str(target_type) if target_type else "unknown"


def sanitize_extension_receiver(receiver_display: str) -> str:
    """The receiver component of an extension-method symbol.

    ONE helper for the declaration, the call site and the manifest, so the three can
    never disagree. An array receiver folds to `arr__<element>`, because `[]` is not a
    symbol character; everything else keeps the historical sanitization of the interned
    `<...>` form.
    """
    if receiver_display.endswith("[]"):
        return "arr__" + sanitize_extension_receiver(receiver_display[:-2])
    return (_unit_free(receiver_display)
            .replace("<", "__").replace(">", "").replace(", ", "_"))


def _unit_free(type_str: str) -> str:
    """A type string with no `$` in it, so no part of a symbol reads as a unit prefix.

    The one `$` a type string holds starts a tuple's base name, `$Tuple`. It becomes a
    digit, and no written type name starts with a digit, so the sanitized tuple
    collides with no user type: a `struct _Tuple@(A, B)` keeps `_Tuple`
    (docs/design/tuples.md).
    """
    return type_str.replace("$", "0")


def _join_sanitized(type_args: Tuple['Type', ...]) -> str:
    """Sanitize each type arg's string form and join with single underscores."""
    arg_strs = []
    for arg in type_args:
        type_str = _unit_free(str(arg))

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
