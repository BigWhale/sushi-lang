"""The interned name of a generic instance, spelled in one place.

An instance of `List@(i32)` is filed in the tables as `List<i32>`: the base, then each
type argument's `str()`, separated by `", "`. The angle brackets are the INTERNAL spelling;
a user reads `display_type()`. Gate: `tests/unit/test_interned_name_is_one.py`.
"""

from typing import Iterable


def interned_name(base: str, args: Iterable[object]) -> str:
    """The table key of `base` applied to `args`: `List<i32>`, `Result<File, IoError>`."""
    return f"{base}<{', '.join(str(arg) for arg in args)}>"


def interned_prefix(base: str) -> str:
    """The start that every interned name of `base` shares, for a reader that matches on it.

    A type answers `type_predicates.is_instance_of` instead. This is for the readers that
    still take a prefix argument.
    """
    return f"{base}<"
