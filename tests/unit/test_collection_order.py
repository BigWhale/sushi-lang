"""A unit is collected after the units it depends on.

Ruling of `docs/design/unit-namespaces.md` section 13.2. `topological_sort` counts
in-degree as "how many units depend on me" and seeds its queue from the units nobody
imports, so it used to yield DEPENDENTS first and `main` was collected first. A per-unit
scope cannot be built that way: a provider reads a unit's collected symbols, so the unit
it names has to be collected already.

The order is asserted as a relation and never as a list. A list would pin the tie-break
between two units that do not depend on each other, and nothing rules on that.
"""
from __future__ import annotations

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.units import UnitManager


DEEP = """\
public fn deep_value() i32:
    return Result.Ok(7)
"""

MID = """\
use "deep"

public fn mid_value() i32:
    return Result.Ok(deep_value()?? + 1)
"""

TOP = """\
use "mid"

fn main() i32:
    println("{mid_value().realise(0)}")
    return Result.Ok(0)
"""


def _order(tmp_path, sources: dict[str, str]) -> list[str]:
    """Every unit's name, in the order the collect loop would walk them."""
    for name, text in sources.items():
        path = tmp_path / f"{name}.sushi"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    manager = UnitManager(root_path=tmp_path, reporter=Reporter(source="", filename="top"))
    for name, text in sources.items():
        program, _tree = parse_to_ast(text)
        assert manager.load_unit(name, program) is not None, name

    order = manager.get_compilation_order()
    assert order is not None
    return [unit.name for unit in order]


def test_a_dependency_is_collected_before_its_dependent(tmp_path):
    names = _order(tmp_path, {"deep": DEEP, "mid": MID, "top": TOP})

    assert set(names) == {"deep", "mid", "top"}, names
    assert names.index("deep") < names.index("mid"), names
    assert names.index("mid") < names.index("top"), names


def test_a_diamond_puts_both_middles_after_the_shared_dependency(tmp_path):
    """Two units importing one, and one importing both. Ties stay unruled."""
    names = _order(tmp_path, {
        "deep": DEEP,
        "left": 'use "deep"\n\npublic fn left_value() i32:\n'
                '    return Result.Ok(deep_value()?? + 1)\n',
        "right": 'use "deep"\n\npublic fn right_value() i32:\n'
                 '    return Result.Ok(deep_value()?? + 2)\n',
        "top": 'use "left"\nuse "right"\n\nfn main() i32:\n'
               '    println("{left_value().realise(0) + right_value().realise(0)}")\n'
               '    return Result.Ok(0)\n',
    })

    assert names.index("deep") < names.index("left"), names
    assert names.index("deep") < names.index("right"), names
    assert names.index("left") < names.index("top"), names
    assert names.index("right") < names.index("top"), names
