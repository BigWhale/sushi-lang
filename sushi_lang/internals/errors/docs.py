"""Documentation-block errors (CE7xxx) -- what a doc block claims about a declaration.

The whole point of putting a doc block in the grammar is that the compiler can check
it against the declaration standing next to it. A tag that contradicts the declaration
is wrong TODAY, whatever the project's documentation policy is, so every code here is
always on. What is merely absent is a matter of policy, and belongs behind
`--warn-missing-docs` (docs/design/documentation.md section 6).

CE7001 to CE7004 are tag errors, CE7005 and CE7006 are position errors, and CE7007 and
CE7008 are example errors, numbered in those three runs.
"""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


_add(ErrorMessage("CE7001", Severity.ERROR,
    "the '- Parameter {name}:' tag names no parameter of '{callable}'",
    Category.DOCS, "The tag names the thing the declaration DECLARES, so the compiler "
                   "can tell that no such parameter exists. A renamed parameter and a "
                   "copied tag both land here. Neither pydoc nor a source scraper can "
                   "find this, which is why the block belongs in the grammar."))

_add(ErrorMessage("CE7002", Severity.ERROR,
    "the parameter '{name}' is documented twice",
    Category.DOCS, "A `- Parameter` tag is keyed by the name it carries: many are "
                   "legal, one for each parameter. Two for one name is almost always a "
                   "tag that was copied and not renamed."))

_add(ErrorMessage("CE7003", Severity.ERROR,
    "the '- {tag}:' tag may appear only once",
    Category.DOCS, "`- Returns:` and `- Errors:` are singletons: a declaration has one "
                   "success value and one error arm. This is a different mistake from "
                   "CE7002 -- that one is a tag that was not renamed, this one is a tag "
                   "written twice -- and the two are fixed differently."))

_add(ErrorMessage("CE7004", Severity.ERROR,
    "'{word}' is not a documentation tag",
    Category.DOCS, "A list item shaped `- <Word>:` whose word is close to a tag keyword "
                   "is a typo, not prose. Every documentation system that treats it as "
                   "text makes a misspelled tag silently invisible, which is the failure "
                   "this feature exists to remove. A word further from every keyword, "
                   "such as `- Note:`, stays prose."))

_add(ErrorMessage("CE7005", Severity.ERROR,
    "a documentation block in a body must be the first item there",
    Category.DOCS, "A block that is first in a body documents the function that "
                   "encloses it. A block between two statements has no declaration it "
                   "could plausibly have meant, so there is nothing to guess at."))

_add(ErrorMessage("CE7006", Severity.ERROR,
    "'{name}' is documented twice: from above, and from inside its body",
    Category.DOCS, "The two positions document the same declaration, so a declaration "
                   "that uses both says which one is the documentation twice over. Keep "
                   "one of them."))

_add(ErrorMessage("CE7007", Severity.ERROR,
    "the '- Example:' tag introduces no fenced block",
    Category.DOCS, "The whole job of the tag is to introduce a fenced block of Sushi, "
                   "so a tag with nothing to introduce contradicts itself the way a "
                   "`- Parameter q:` that names no parameter does. This is always on "
                   "for that reason. An ABSENT example is a matter of policy and "
                   "belongs behind `--warn-missing-docs`."))

_add(ErrorMessage("CE7008", Severity.ERROR,
    "this documentation fence is never closed",
    Category.DOCS, "A doc block ends at its own `:##`, so a fence that runs past it is "
                   "truncated, and a truncated example is not one. Every documentation "
                   "system that reads a block as text loses the tail without a signal, "
                   "which is the failure this feature exists to remove. Close the fence "
                   "with a run of the SAME character that is at least as long."))
