"""The primitive `to_str` generator reads one table (#883).

`generate_module_ir` in `backend/types/primitives/to_str.py` makes one `sushi_<T>_to_str`
function for each row of `_TYPE_CONVERSION_SPECS`, and no wrapper for each type is
written by hand. The string fat pointer comes from `get_string_type`, never from a
struct spelled in the file.
"""
import ast
from pathlib import Path

from sushi_lang.backend.types.primitives import to_str
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_type


def test_one_function_for_each_row():
    module = to_str.generate_module_ir()
    defined = [fn.name for fn in module.functions if not fn.is_declaration]
    expected = [f"sushi_{prim_type}_to_str" for prim_type in to_str._TYPE_CONVERSION_SPECS]
    assert [name for name in defined if name.endswith("_to_str")] == expected


def test_every_function_answers_the_string_type():
    module = to_str.generate_module_ir()
    for prim_type in to_str._TYPE_CONVERSION_SPECS:
        fn = module.get_global(f"sushi_{prim_type}_to_str")
        assert fn.function_type.return_type == get_string_type()


def test_no_wrapper_and_no_spelled_string_struct():
    tree = ast.parse(Path(to_str.__file__).read_text(encoding="utf-8"))
    wrappers = [node.name for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name.startswith("_generate_")]
    spelled = [node.lineno for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "LiteralStructType"]
    assert wrappers == []
    assert spelled == []
