"""An unknown type name in a template signature is one CE2001, where it is written (#859).

A generic declaration with no instance reported nothing, and one with an instance
reported a CE2028 at the use, or a CE2060, a CE4004, or the internal CE0045. The rule is
the one a declaration that is not generic reads: CE2001 at the name, once, whether or not
the declaration is instantiated.

`EXPECT_ERROR_CODE` matches a SUBSTRING, so no fixture can count. This module counts.
"""
from __future__ import annotations

import pytest

_BOX = "struct Box@(T):\n    T item\n\n"
_SHOW = "perk Show:\n    fn show(i32 n) string\n\n"
_MAIN = "fn main() i32:\n    println(\"Mostly Harmless\")\n    return Result.Ok(0)\n"


def _main(*lines: str) -> str:
    body = "".join(f"    {line}\n" for line in lines)
    return f"fn main() i32:\n{body}    return Result.Ok(0)\n"


_BOX_USE = _main("let Box@(i32) b = Box(1)", "println(b.item)")

# name -> (template, the main that makes an instance, the unknown names written)
_POSITIONS = {
    "struct field": ("struct Wrap@(T):\n    Nope@(T) b\n    Missing c\n    T d\n\n",
                     _main("let Wrap@(i32) w = Wrap(1, 2, 3)", "println(w.d)"),
                     ["Missing", "Nope"]),
    "enum payload": ("enum Slot@(T):\n    A(Gone)\n    B(Gone@(T))\n    C(T)\n\n",
                     _main("let Slot@(i32) s = Slot.C(1)", "match s:",
                           "    Slot.C(v) -> println(v)", "    _ -> println(0)"),
                     ["Gone", "Gone"]),
    "parameter": ("fn f@(T)(Nope v, T a) i32:\n    return Result.Ok(0)\n\n",
                  _main("println(f(1, 2).realise(0))"),
                  ["Nope"]),
    "return": ("fn f@(T)(T v) Missing:\n    return Result.Ok(v)\n\n",
               _main("f(1).realise(0)"),
               ["Missing"]),
    "error arm": ("fn g@(T)(T v) i32 | Absent:\n    return Result.Ok(0)\n\n",
                  _main("println(g(1).realise(0))"),
                  ["Absent"]),
    "extension": (_BOX + "extend Box@(T) take(Nope v) Missing:\n    return self.item\n\n",
                  _BOX_USE,
                  ["Missing", "Nope"]),
    "method type parameter": ("extend i32 pick@(U)(U a, Gone b) i32:\n    return self\n\n",
                              _main("println(5.pick(1, 2))"),
                              ["Gone"]),
    "perk implementation": (_SHOW + _BOX + "extend Box@(T) with Show:\n"
                            "    fn show(Nope n) string:\n        return \"box\"\n\n",
                            _BOX_USE,
                            ["Nope"]),
}


def _errors(reporter):
    return [item for item in reporter.items if item.code.startswith("CE")]


def _unknown_names(errors) -> list[str]:
    return sorted(e.message.split("'")[1] for e in errors)


@pytest.mark.parametrize("instance", [False, True], ids=["no instance", "instance"])
@pytest.mark.parametrize("position", sorted(_POSITIONS))
def test_each_unknown_name_reads_one_ce2001(analyze, position, instance):
    template, with_instance, names = _POSITIONS[position]
    errors = _errors(analyze(template + (with_instance if instance else _MAIN), name="m"))
    assert [e.code for e in errors] == ["CE2001"] * len(names), \
        [(e.code, e.message) for e in errors]
    assert _unknown_names(errors) == sorted(names)
    assert all(e.span is not None for e in errors)


def test_the_declarations_own_type_parameters_are_known(analyze):
    src = (_SHOW + _BOX
           + "struct Pair@(T, U):\n    T a\n    Box@(U) b\n    U[] rest\n\n"
           + "enum Opt@(T):\n    Some(Box@(T))\n    Nothing\n\n"
           + "fn keep@(T)(peek T v, fn(T) -> T f) Maybe@(T):\n    return Result.Ok(Maybe.None)\n\n"
           + "extend Box@(T) swap@(U)(U v) Pair@(U, T):\n    return Pair(v, Box(self.item), from([]))\n\n"
           + "extend Box@(T) with Show:\n    fn show(i32 n) string:\n        return \"box\"\n\n"
           + _MAIN)
    assert _errors(analyze(src, name="m")) == []


def test_the_missing_import_help_applies(analyze):
    src = "struct Index@(T):\n    HashMap@(T, i32) seen\n\n" + _MAIN
    errors = _errors(analyze(src, name="m"))
    assert [e.code for e in errors] == ["CE2001"]
    helps = [sub.message for sub in errors[0].sub if sub.kind == "help"]
    assert any("use <collections/hashmap>" in line for line in helps), helps
