"""Doc blocks: the parser, the attachment rule, and nothing else in the compiler.

This module is the ONLY place that understands doc syntax. The twelve declaration
builders call `attach_docs` once per block they own; none of them grows doc handling
of its own. `docs/design/documentation.md` sections 2, 3 and 5 are the authority.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from lark import Token

from sushi_lang.internals.report import Span, span_of
from sushi_lang.semantics.ast import DocBlock, DocTag

if TYPE_CHECKING:
    from sushi_lang.semantics.ast import Block
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder

OPEN = "##:"
CLOSE = ":##"

TAG_KEYWORDS = ("Parameter", "Returns", "Errors", "Example")

# Reserved by documentation.md section 3 and not parsed yet. They stay prose BY NAME
# rather than by edit distance, so a keyword added later cannot quietly turn one of
# them into a typo report.
RESERVED_KEYWORDS = ("Deprecated", "Traps")

# `- <Word>[ <name>]:` -- the shape that makes a Markdown list item a tag candidate.
# The item is line-initial: an indented bullet sits inside an example and is prose.
_CANDIDATE = re.compile(r"^-[ \t]+([A-Za-z]\w*)(?:[ \t]+([A-Za-z_]\w*))?[ \t]*:[ \t]?(.*)$")

# A transposition plus a dropped letter is what a mistyped keyword looks like. Three
# edits reaches `Err`, which is a word an author may well have meant.
NEAR_MISS_DISTANCE = 2

_KIND_OF = {keyword: keyword.lower() for keyword in TAG_KEYWORDS}


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance, case-folded by the caller."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1,
                               current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def suggest_tag(word: str) -> Optional[str]:
    """The tag `word` was probably meant to be, or None when it is prose.

    An exact keyword suggests itself, so a call site that has already failed the
    exact match reads the answer as "this is a near miss of that one".
    """
    if word in RESERVED_KEYWORDS:
        return None

    best: Optional[str] = None
    best_distance = NEAR_MISS_DISTANCE + 1
    for keyword in TAG_KEYWORDS:
        distance = _edit_distance(word.casefold(), keyword.casefold())
        if distance < best_distance:
            best, best_distance = keyword, distance
    return best


def _common_indent(lines: Sequence[str]) -> str:
    """The longest leading whitespace every non-blank line shares."""
    prefix: Optional[str] = None
    for line in lines:
        if not line.strip():
            continue
        indent = line[:len(line) - len(line.lstrip())]
        if prefix is None:
            prefix = indent
            continue
        limit = min(len(prefix), len(indent))
        while limit and prefix[:limit] != indent[:limit]:
            limit -= 1
        prefix = prefix[:limit]
    return prefix or ""


def _dedent(token: Token) -> List[Tuple[int, int, str]]:
    """The block's text as (line, column, text) triples, dedented and edge-trimmed.

    The first line carries no indent of its own -- it is whatever followed `##:` on
    the opening line -- so it is held out of the common-prefix calculation and the
    rest is dedented around it.
    """
    raw = str(token.value)
    body = raw[len(OPEN):]
    if body.endswith(CLOSE):
        body = body[:-len(CLOSE)]

    lines = body.split("\n")
    start_line = getattr(token, "line", 1) or 1
    start_col = getattr(token, "column", 1) or 1
    prefix = _common_indent(lines[1:])

    # Trailing whitespace goes on every line: the closer left some behind on the
    # line it stood on, and a run of spaces at the end of a line means nothing.
    head = lines[0].lstrip()
    entries: List[Tuple[int, int, str]] = [
        (start_line, start_col + len(OPEN) + (len(lines[0]) - len(head)), head.rstrip())
    ]
    for offset, line in enumerate(lines[1:], start=1):
        entries.append((start_line + offset, 1 + len(prefix), line[len(prefix):].rstrip()))

    while entries and not entries[0][2].strip():
        entries.pop(0)
    while entries and not entries[-1][2].strip():
        entries.pop()
    return entries


def _read_tags(entries: Sequence[Tuple[int, int, str]]) -> List[DocTag]:
    """Every list item that is a keyword or a near miss of one. The rest is prose."""
    tags: List[DocTag] = []
    for index, (line, col, text) in enumerate(entries):
        match = _CANDIDATE.match(text)
        if match is None:
            continue
        word, name, description = match.group(1), match.group(2), match.group(3)
        if word not in TAG_KEYWORDS and suggest_tag(word) is None:
            continue

        # A Markdown list item runs on until a blank line or the next item.
        parts = [description.strip()]
        for _line, _col, following in entries[index + 1:]:
            if not following.strip() or _CANDIDATE.match(following):
                break
            parts.append(following.strip())

        kind = _KIND_OF.get(word, "unknown")
        tags.append(DocTag(
            kind=kind,
            name=name if kind == "parameter" else None,
            text="\n".join(part for part in parts if part),
            loc=Span(line, col, line, col + len(text)),
            word=word,
        ))
    return tags


def parse_doc_block(token: Token) -> DocBlock:
    """A DOC_BLOCK token, read into the block it carries."""
    entries = _dedent(token)
    tags = _read_tags(entries)

    summary: List[str] = []
    for _line, _col, text in entries:
        if not text.strip() or _CANDIDATE.match(text):
            break
        summary.append(text)

    return DocBlock(
        summary="\n".join(summary).strip(),
        text="\n".join(text for _line, _col, text in entries),
        tags=tags,
        loc=span_of(token),
    )


def _takes_docs(node) -> bool:
    """Whether this node is a declaration that carries a doc block."""
    return "doc" in getattr(type(node), "__dataclass_fields__", {})


def attach_docs(children: Sequence, built: Sequence, ast_builder: 'ASTBuilder',
                allow_unit_doc: bool = False) -> None:
    """Bind each doc block among `children` to the node that starts on the next line.

    A blank line breaks the attachment, and so does an ordinary `#` comment: both are
    absorbed into `_NEWLINE`, so the builder compares line numbers and cannot tell
    them apart. A block that binds to nothing survives in `orphan_docs`, because the
    builder takes no Reporter and dropping it here is exactly the silent loss this
    feature exists to remove.
    """
    by_line = {node.loc.line: node for node in built
               if getattr(node, "loc", None) is not None and _takes_docs(node)}

    for child in children:
        if not (isinstance(child, Token) and child.type == "DOC_BLOCK"):
            continue

        doc = parse_doc_block(child)
        target = by_line.get(doc.loc.end_line + 1)
        if target is not None:
            target.doc = doc
        elif allow_unit_doc and child is children[0]:
            ast_builder.unit_doc = doc
        else:
            doc.orphan_reason = "detached"
            ast_builder.orphan_docs.append(doc)


def peel_body_docs(children: Sequence, ast_builder: 'ASTBuilder') -> Optional[DocBlock]:
    """Take the doc blocks out of a body, before the statement dispatcher sees one.

    A doc block reaching `parse_stmt` as a statement class would need an arm in every
    exhaustive statement dispatcher in the compiler. The first item is the body's own
    block; anything later documents nothing a body can name, and is a different code.
    """
    body_doc: Optional[DocBlock] = None
    for index, child in enumerate(children):
        if not (isinstance(child, Token) and child.type == "DOC_BLOCK"):
            continue
        doc = parse_doc_block(child)
        if index == 0:
            body_doc = doc
        else:
            doc.orphan_reason = "in-body"
            ast_builder.orphan_docs.append(doc)
    return body_doc


def lift_body_doc(body: 'Block', ast_builder: 'ASTBuilder') -> Optional[DocBlock]:
    """Hand a body-first block to the declaration that encloses the body.

    The pair is dropped from the pending map by the same call, so what stands there
    when `build()` finishes is exactly the set of blocks in a body that takes no
    docs -- a lambda body, an `if` arm -- and those become orphans.
    """
    ast_builder.pending_body_docs.pop(id(body), None)
    return body.doc
