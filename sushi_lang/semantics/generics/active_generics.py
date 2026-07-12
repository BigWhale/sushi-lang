"""Which optional generic types are available in this compilation.

Most generics (Result, Maybe, Own, List) are always defined. `HashMap<K, V>` is
not: it only exists if the program says `use <collections/hashmap>`. Pass 0 needs
to know that before it registers the generic struct, and both the single-file and
multi-file pipelines need to record it when they resolve a stdlib import.

That is the whole contract -- a set of active type names. It used to be a
`GenericTypeProvider` protocol plus a `GenericTypeRegistry` of them, but nothing
ever called a provider: emission is dispatched by name in
`backend/expressions/calls/generics.py`, and validation by name in
`semantics/passes/types/calls/methods.py`. Only the flag was live.
"""

# Sushi stdlib unit path -> the generic type that importing it makes available.
GENERIC_UNIT_TYPES = {
    "collections/hashmap": "HashMap",
    # Future: "collections/set": "Set",
}

_active: set[str] = set()


def activate_generic_unit(unit_path: str) -> None:
    """Mark the generic type provided by a stdlib unit as available, if it has one.

    Called when a `use <...>` statement is resolved. A unit path with no generic
    type (e.g. "io/stdio") is a no-op.

    Args:
        unit_path: Stdlib unit path like "collections/hashmap".
    """
    generic_name = GENERIC_UNIT_TYPES.get(unit_path)
    if generic_name is not None:
        _active.add(generic_name)


def is_generic_active(name: str) -> bool:
    """Whether a generic type has been made available by a `use` statement."""
    return name in _active


def reset_active_generics() -> None:
    """Clear the active set. Called at the start of each compilation."""
    _active.clear()
