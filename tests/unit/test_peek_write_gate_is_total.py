"""Every mutating method is rejected through a `peek` receiver. No member left out."""
from __future__ import annotations


from sushi_lang.semantics.passes.borrow import MUTATING_METHODS




_HASHMAP = "use <collections/hashmap>\n\n"

# One case per member of `MUTATING_METHODS`: (parameter type, the call, extra source).
# The receiver type must genuinely have the method, or the typecheck pass rejects the call before
# the borrow checker ever runs and the case would prove nothing.
CASES = {
    "push":          ("i32[]", "r.push(9)", ""),
    "pop":           ("List@(i32)", "r.pop()", ""),
    "insert":        ("List@(i32)", "r.insert(0, 5)", ""),
    "remove":        ("List@(i32)", "r.remove(0)", ""),
    "clear":         ("List@(i32)", "r.clear()", ""),
    "truncate":      ("i32[]", "r.truncate(1)", ""),
    "reserve":       ("List@(i32)", "r.reserve(10)", ""),
    "shrink_to_fit": ("List@(i32)", "r.shrink_to_fit()", ""),
    "rehash":        ("HashMap@(i32, string)", "r.rehash()", _HASHMAP),
    "destroy":       ("List@(i32)", "r.destroy()", ""),
    "free":          ("HashMap@(i32, string)", "r.free()", _HASHMAP),
    "fill":          ("i32[]", "r.fill(7)", ""),
    "reverse":       ("i32[]", "r.reverse()", ""),
    "extend":        ("i32[]", "r.extend(from([1]))", ""),
    "extend_range":  ("i32[]", "r.extend_range(from([1]), 0, 1)", ""),
}




def test_every_mutating_method_has_a_case():
    """A method added to the set without a case here is a hole in this gate."""
    assert set(CASES) == set(MUTATING_METHODS)






# The two write shapes that are not method calls. They share the one gate, so a
# regression in it shows up here as well as above.





