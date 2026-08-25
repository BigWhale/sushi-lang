"""Doc blocks: the parser, the attachment rule, and nothing else in the compiler.

This module is the ONLY place that understands doc syntax. The twelve declaration
builders call `attach_docs` once per block they own; none of them grows doc handling
of its own. `docs/design/documentation.md` sections 2, 3 and 5 are the authority.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Sequence, Set, Tuple

from lark import Token

from sushi_lang.internals.report import Span, span_of
from sushi_lang.semantics.ast import DocBlock, DocExample, DocTag

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

# A fenced block opens with three or more backticks or tildes and closes with a run of
# the SAME character that is at least as long. That is CommonMark's rule, and it is what
# lets one fence hold another. The info string may not hold either fence character, so a
# bare closer never reads as an opener.
_FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})[ \t]*([^`~]*)$")

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


@dataclass
class _Fence:
    """One fenced region of a block, as entry indices (documentation.md S10, R13)."""
    open_at: int                 # the opening fence line
    body_end: int                # one past the last code line
    close_at: Optional[int]      # the closing fence line, or None when there is none
    info: str                    # the info string, as written
    indent: int                  # the opening fence's own indent, stripped off the code


def _fences(entries: Sequence[Tuple[int, int, str]]) -> List[_Fence]:
    """Every fenced region, in source order.

    Scanned rather than matched line by line, because a closing fence is shaped like an
    opening one with no info string: once a region opens, the scan continues from its
    closer and cannot read that closer as a second opener.
    """
    found: List[_Fence] = []
    index = 0
    while index < len(entries):
        match = _FENCE.match(entries[index][2])
        if match is None:
            index += 1
            continue
        indent, marker, info = match.group(1), match.group(2), match.group(3)
        close_at: Optional[int] = None
        for cursor in range(index + 1, len(entries)):
            if _closes(marker, entries[cursor][2]):
                close_at = cursor
                break
        body_end = close_at if close_at is not None else len(entries)
        found.append(_Fence(index, body_end, close_at, info.strip(), len(indent)))
        index = body_end + 1
    return found


def _closes(marker: str, text: str) -> bool:
    """Whether `text` is a closing fence for a region opened with `marker`."""
    run = text.strip()
    return len(run) >= len(marker) and set(run) == {marker[0]}


def _fenced_lines(fences: Sequence[_Fence]) -> Set[int]:
    """Every entry index a fence covers, its own two delimiter lines included.

    This is what makes `- Returns:` inside example code prose rather than a tag, and
    what stops a blank line inside a fence from ending the tag that introduced it.
    """
    covered: Set[int] = set()
    for fence in fences:
        last = fence.close_at if fence.close_at is not None else fence.body_end - 1
        covered.update(range(fence.open_at, last + 1))
    return covered


def _strip_indent(text: str, width: int) -> str:
    """`text` with up to `width` leading spaces or tabs removed."""
    cut = 0
    while cut < width and cut < len(text) and text[cut] in " \t":
        cut += 1
    return text[cut:]


def _candidates(entries: Sequence[Tuple[int, int, str]], fenced: Set[int]) -> List[int]:
    """The entry index of every tag candidate outside a fence."""
    return [index for index, (_line, _col, text) in enumerate(entries)
            if index not in fenced and _CANDIDATE.match(text)]


def _read_tags(entries: Sequence[Tuple[int, int, str]], candidates: Sequence[int],
               fenced: Set[int]) -> List[DocTag]:
    """Every list item that is a keyword or a near miss of one. The rest is prose."""
    tags: List[DocTag] = []
    for index in candidates:
        line, col, text = entries[index]
        match = _CANDIDATE.match(text)
        assert match is not None                       # `_candidates` matched it already
        word, name, description = match.group(1), match.group(2), match.group(3)
        if word not in TAG_KEYWORDS and suggest_tag(word) is None:
            continue

        # A Markdown list item runs on until a blank line, the next item, or the fence
        # it introduces. A fence ends the caption and starts the example.
        parts = [description.strip()]
        for following in range(index + 1, len(entries)):
            text_of = entries[following][2]
            if (not text_of.strip() or following in fenced
                    or _CANDIDATE.match(text_of)):
                break
            parts.append(text_of.strip())

        kind = _KIND_OF.get(word, "unknown")
        tags.append(DocTag(
            kind=kind,
            name=name if kind == "parameter" else None,
            text="\n".join(part for part in parts if part),
            loc=Span(line, col, line, col + len(text)),
            word=word,
        ))
    return tags


def _read_examples(entries: Sequence[Tuple[int, int, str]], fences: Sequence[_Fence],
                   candidates: Sequence[int]) -> List[DocExample]:
    """One entry per `- Example:` tag, in source order.

    The window of a tag runs to the next tag candidate, so the fence an example gets is
    the first one the author wrote under that tag and nothing later. A tag whose window
    holds no fence is the CE7007 defect; a fence with no closer is the CE7008 one.
    """
    examples: List[DocExample] = []
    for position, index in enumerate(candidates):
        match = _CANDIDATE.match(entries[index][2])
        if match is None or match.group(1) != "Example":
            continue
        stop = candidates[position + 1] if position + 1 < len(candidates) else len(entries)
        fence = next((f for f in fences if index < f.open_at < stop), None)

        if fence is None:
            line, col, text = entries[index]
            examples.append(DocExample(
                code="", attrs="", defect="no-fence",
                loc=Span(line, col, line, col + len(text))))
            continue

        line, col, text = entries[fence.open_at]
        examples.append(DocExample(
            code="\n".join(_strip_indent(entries[i][2], fence.indent)
                           for i in range(fence.open_at + 1, fence.body_end)),
            attrs=fence.info,
            loc=Span(line, col, line, col + len(text)),
            defect=None if fence.close_at is not None else "unterminated"))
    return examples


def _read_body(entries: Sequence[Tuple[int, int, str]], after_summary: int,
               candidates: Sequence[int]) -> str:
    """The prose between the summary and the first tag candidate.

    Parsed rather than derived. "The block with the tag lines taken out" is a different
    answer: a Markdown list item stops at a blank line, so a fenced example's closing
    lines fall outside its own tag and a line-removal rule copies them into the body as
    prose. Everything from the first candidate onward belongs to the tags.

    A fence that no tag introduces is prose, and it stays here as written. Only the
    CANDIDATE search reads the partition, which is why a fence cannot end the body.
    """
    first_tag = next((index for index in candidates if index >= after_summary),
                     len(entries))
    lines = [text for _line, _col, text in entries[after_summary:first_tag]]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def parse_doc_block(token: Token) -> DocBlock:
    """A DOC_BLOCK token, read into the block it carries.

    The block is PARTITIONED first: every fenced region is found before a single line is
    read as a tag (documentation.md S10, R13). Without that the parse has three defects,
    each of them silent -- a tag-shaped line inside example code becomes a tag, a blank
    line inside a fence ends the tag that introduced it, and the body rule needs a
    special case for a fence it cannot see.
    """
    entries = _dedent(token)
    fences = _fences(entries)
    fenced = _fenced_lines(fences)
    candidates = _candidates(entries, fenced)

    summary: List[str] = []
    for index, (_line, _col, text) in enumerate(entries):
        if not text.strip() or index in candidates:
            break
        summary.append(text)

    return DocBlock(
        summary="\n".join(summary).strip(),
        text="\n".join(text for _line, _col, text in entries),
        body=_read_body(entries, len(summary), candidates),
        tags=_read_tags(entries, candidates, fenced),
        examples=_read_examples(entries, fences, candidates),
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
