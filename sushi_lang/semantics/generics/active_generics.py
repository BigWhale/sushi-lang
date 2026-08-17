"""Which optional generic types are available in this compilation."""

# Sushi stdlib unit path -> the generic type that importing it makes available.
GENERIC_UNIT_TYPES = {
    "collections/hashmap": "HashMap",
    # Future: "collections/set": "Set",
}

_active: set[str] = set()


def activate_generic_unit(unit_path: str) -> None:
    """Mark the generic type provided by a stdlib unit as available, if it has one."""
    generic_name = GENERIC_UNIT_TYPES.get(unit_path)
    if generic_name is not None:
        _active.add(generic_name)


def is_generic_active(name: str) -> bool:
    """Whether a generic type has been made available by a `use` statement."""
    return name in _active


def reset_active_generics() -> None:
    """Clear the active set. Called at the start of each compilation."""
    _active.clear()
