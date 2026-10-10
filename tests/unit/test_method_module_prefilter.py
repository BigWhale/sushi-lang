"""The quick test that lets a build skip the parse of a stdlib method module.

A build loads a stdlib module for its methods on the built-in types when a unit names one
of them (`docs/design/extension-visibility.md` R1). `may_declare_methods` reads the source
TEXT of a module and tells the build when a parse cannot find such a method. A wrong "no"
leaves a method out of the build, so every case here that can declare the name must
answer "yes". The test reads text only. It does not start the compiler.
"""
from __future__ import annotations

from sushi_lang.semantics.stdlib_registry import may_declare_methods


def test_the_method_name_after_each_target_form_is_found():
    lines = {
        "public extend string lines() string[]:": "lines",
        "public extend (T: Ord)[] sort(poke self) ~:": "sort",
        "public extend T[] map@(U)(fn(T) -> U f) List@(U):": "map",
        "public extend List@(T: Clone) zip@(U: Clone)(List@(U) other) List@((T, U)):": "zip",
        "public extend u8[] push_u16_be(u16 v) ~:": "push_u16_be",
        "public extend f64 static parse(string s) f64:": "parse",
    }
    for line, name in lines.items():
        assert may_declare_methods(line + "\n", {name}), line
        assert not may_declare_methods(line + "\n", {"other"}), line


def test_a_line_whose_name_is_not_found_answers_yes():
    assert may_declare_methods("public extend Box@(List@(T)) size() i32:\n", {"other"})
    assert may_declare_methods("public extend Pair@(\n    T, U) swap() Pair@(U, T):\n",
                               {"other"})


def test_a_method_of_the_modules_own_type_is_not_on_a_built_in_type():
    source = "public struct Builder:\n    i32 n\n\npublic extend Builder push(i32 v) ~:\n"
    assert not may_declare_methods(source, {"push"})
    assert may_declare_methods(source + "public extend Builder[] push(i32 v) ~:\n",
                               {"push"})


def test_a_private_extension_is_not_counted():
    assert not may_declare_methods("extend string lines() string[]:\n", {"lines"})


_PRIMITIVES = {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "f32", "f64",
               "bool", "string"}
_BUILTIN_GENERICS = ("List@", "Own@", "Maybe@", "Result@")


def _on_a_built_in_type(target: str) -> bool:
    return (target in _PRIMITIVES or target.endswith("]")
            or target.startswith(_BUILTIN_GENERICS))


def test_every_module_with_a_method_on_a_built_in_type_is_in_the_table():
    """A module that declares a public method on a built-in type and is not in
    `BUILTIN_TYPE_METHOD_MODULES` is never loaded for the method, so a call with no
    import does not find it."""
    from sushi_lang.semantics.stdlib_registry import (
        _DECLARED_METHOD, BUILTIN_TYPE_METHOD_MODULES, SOURCE_STDLIB_MODULES)
    missing = []
    for module, path in sorted(SOURCE_STDLIB_MODULES.items()):
        if module in BUILTIN_TYPE_METHOD_MODULES or not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            declared = _DECLARED_METHOD.match(line)
            if declared is not None and _on_a_built_in_type(declared.group(1)):
                missing.append(f"{module}: {line}")
    assert missing == []
