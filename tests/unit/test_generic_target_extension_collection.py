"""The instantiate pass reads every extension declaration, whichever list it is filed under (#389).

The AST builder splits extensions two ways: a plain target goes to `program.extensions`, and
a target spelled `@(...)` goes to `program.generic_extensions` -- CONCRETE arguments
included, so `extend List@(i32)` is in the second list beside the `extend Box@(T)`
templates. The instantiate pass walked the first list only, so a generic type named in such a
declaration was never collected and the declaration reported a false CE2001, for a type the
program declares.

The gate reads the ENUM AND STRUCT TABLES after analysis rather than the diagnostic: a
collected instantiation is what puts the type there, and asking the table says which half
failed when it fails.
"""
from __future__ import annotations




# (id, declaration, the interned name the declaration must make exist)






# A TEMPLATE target reads its signature per instantiation of the target. This case used to
# assert the OPPOSITE -- the restriction that kept a template's signature unread, because
# every instantiation shared one body AST and the per-instantiation stamps collided on it.
# Each instantiation owns its body now (#391), so the restriction is gone and the positive
# case takes its place.






# -- the array-target classifier (ruling 3 of the UFCS epic) ----------------------------
#
# `extend T[]` and `extend Crate[]` are spelled the same way -- a bare name in the
# element position -- and the collect pass tells them apart with the same declared-name
# question the `@(...)` classifier answers. A template files under the synthetic
# `$array` base key; a declared name stays a concrete extension.

def test_array_element_naming_nothing_binds_a_type_parameter():
    from sushi_lang.semantics.generics.extension_targets import (
        ARRAY_BASE_KEY, classify_array_extension_target)
    from sushi_lang.semantics.typesys import UnknownType

    shape = classify_array_extension_target(UnknownType(name="T"), lambda name: False)
    assert shape is not None
    assert shape.base_name == ARRAY_BASE_KEY
    assert shape.param_names == ("T",)
    assert not shape.is_concrete


def test_array_element_naming_a_declared_type_is_concrete():
    from sushi_lang.semantics.generics.extension_targets import (
        classify_array_extension_target)
    from sushi_lang.semantics.typesys import BuiltinType, UnknownType

    declared = classify_array_extension_target(UnknownType(name="Crate"),
                                               lambda name: name == "Crate")
    assert declared is not None and declared.is_concrete

    builtin = classify_array_extension_target(BuiltinType.I32, lambda name: False)
    assert builtin is not None and builtin.is_concrete


def test_array_element_of_any_other_shape_is_invalid():
    from sushi_lang.semantics.generics.extension_targets import (
        classify_array_extension_target)
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.typesys import DynamicArrayType, UnknownType

    generic = GenericTypeRef(base_name="Maybe", type_args=(UnknownType(name="T"),))
    assert classify_array_extension_target(generic, lambda name: True) is None

    nested = DynamicArrayType(base_type=UnknownType(name="T"))
    assert classify_array_extension_target(nested, lambda name: False) is None


