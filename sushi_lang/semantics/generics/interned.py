"""The interned name of a generic instance, spelled in one place.

An instance of `List@(i32)` is filed in the tables as `List<i32>`: the base, then each
type argument's `str()`, separated by `", "`. The angle brackets are the INTERNAL spelling;
a user reads `display_type()`. Gate: `tests/unit/test_interned_name_is_one.py`.
"""

from typing import Iterable


def interned_name(base: str, args: Iterable[object]) -> str:
    """The table key of `base` applied to `args`: `List<i32>`, `Result<File, IoError>`."""
    return f"{base}<{', '.join(str(arg) for arg in args)}>"
