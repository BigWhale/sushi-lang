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
    weak_odr dedup. The `__{margs}` suffix appears only when method-level type
    arguments exist, so every pre-existing extension symbol is unchanged -- and two
    different solved U's on one receiver are two symbols, never one colliding body.
    """
    base = f"{sanitize_extension_receiver(receiver_display)}_{method}"
    if method_type_args:
        return f"{base}__{_join_sanitized(tuple(method_type_args))}"
    return base


def sanitize_extension_receiver(receiver_display: str) -> str:
    """The receiver component of an extension-method symbol.

    ONE helper for the declaration, the call site and the manifest, so the three can
    never disagree. An array receiver folds to `arr__<element>`, because `[]` is not a
    symbol character; everything else keeps the historical sanitization of the interned
    `<...>` form.
    """
    if receiver_display.endswith("[]"):
        return "arr__" + sanitize_extension_receiver(receiver_display[:-2])
    return receiver_display.replace("<", "__").replace(">", "").replace(", ", "_")


def _join_sanitized(type_args: Tuple['Type', ...]) -> str:
    """Sanitize each type arg's string form and join with single underscores."""
    arg_strs = []
    for arg in type_args:
        type_str = str(arg)

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
