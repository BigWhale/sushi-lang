"""The borrow pass reads a container's element type from the interned instance (#782).

A monomorphized `List@(T)` / `HashMap@(K, V)` carries its type arguments in
`generic_args`, as a `GenericTypeRef` carries them in `type_args`. The pass reads them
there and parses no interned name, so it needs no bracket splitter, no table adapter
and no `except` that hides a fault.
"""
import ast
import inspect

from sushi_lang.semantics.passes.borrow import types as borrow_types
from sushi_lang.semantics.passes.borrow.types import TypeQueries
from sushi_lang.semantics.tables import SymbolTables
from sushi_lang.semantics.typesys import BuiltinType, StructType

# Not in any table: an answer that needs a lookup by name cannot find it.
POINT = StructType(name="Point", fields=(("x", BuiltinType.I32),))
LIST_OF_POINT = StructType(name="List<Point>", fields=(), generic_base="List",
                           generic_args=(POINT,))
MAP_TO_POINT = StructType(name="HashMap<i32, Point>", fields=(), generic_base="HashMap",
                          generic_args=(BuiltinType.I32, POINT))


def test_list_element_is_its_type_argument():
    assert TypeQueries(SymbolTables()).element_type(LIST_OF_POINT) is POINT


def test_hashmap_element_is_its_value_argument():
    assert TypeQueries(SymbolTables()).element_type(MAP_TO_POINT) is POINT


def test_containers_are_known_by_their_base():
    queries = TypeQueries(SymbolTables())
    assert queries.is_container(LIST_OF_POINT)
    assert queries.is_container(MAP_TO_POINT)
    assert not queries.is_container(POINT)


def test_no_second_reader_in_the_borrow_pass():
    tree = ast.parse(inspect.getsource(borrow_types))
    names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert not names & {"_split_type_args", "type_from_name"}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not imported & {"SimpleNamespace", "resolve_type_from_string"}
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    assert not handlers
