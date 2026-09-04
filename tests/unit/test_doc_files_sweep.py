"""The FILE collector, out of `tests/docs_sweep.py` (#547).

Nothing compiled a `.sushi` FILE under `docs/` before this collector: the `docs` pass
reads fenced blocks out of Markdown, and the `examples` pass compiles `- Example:`
fences FROM a file without compiling the file itself. Three files under `docs/` had
been broken for two phases of the handles epic before anybody noticed.

The rules this module gates:

- a file with no `fn main(` is a LIBRARY, and is built as one (`--lib`), so "no main"
  is a category and never drift
- a library is built BEFORE the programs of its own directory, and the programs see it
  on `SUSHI_LIB_PATH`, so the tutorial's library story is checked end to end
- exit 1 is a PASS, because a warning is not a failure -- the same rule the other two
  collectors carry
- the directive is the escape hatch, in the sweep's own vocabulary, and it is read
  ONLY from the leading comment block
- every page's `--8<--` snippet include names a file that is on disk

The sweep is a by-hand tool, so this module is where its rules are gated.
"""
from __future__ import annotations

from pathlib import Path

from tests.docs_sweep import (
    INCLUDE_MARK,
    PROJECT_ROOT,
    doc_file_at,
    doc_files,
    missing_includes,
    parse_file_attrs,
    run_doc_files,
    snippet_includes,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# -- the directive --------------------------------------------------------------

def test_a_file_with_no_directive_is_compiled():
    assert parse_file_attrs("fn main() i32:\n    return Result.Ok(0)\n").mode == "run"


def test_skip_carries_its_reason():
    attrs = parse_file_attrs("# docs-sweep: skip (needs a listening socket)\n\nfn main() i32:\n")
    assert attrs.mode == "skip"
    assert attrs.reason == "needs a listening socket"


def test_error_carries_every_code():
    attrs = parse_file_attrs("# docs-sweep: error CE3007 CE1234\n\nfn main() i32:\n")
    assert attrs.mode == "error"
    assert attrs.codes == ("CE3007", "CE1234")


def test_a_directive_below_the_leading_comment_block_is_not_read():
    """The doc-block attachment rule, applied here: a declaration breaks the block."""
    text = "# a heading\n\nfn main() i32:\n    # docs-sweep: skip (too late)\n    return Result.Ok(0)\n"
    assert parse_file_attrs(text).mode == "run"


def test_a_directive_survives_a_blank_line_inside_the_leading_block():
    text = "# a heading\n#\n# docs-sweep: skip (parked)\n\nfn main() i32:\n"
    assert parse_file_attrs(text).mode == "skip"


def test_an_unknown_attribute_is_its_own_outcome():
    """A typo must be visible; a silent skip is the failure mode this removes."""
    assert parse_file_attrs("# docs-sweep: no_runn\n").mode == "unknown"


# -- library or program ---------------------------------------------------------

def test_a_file_with_no_main_is_a_library(tmp_path):
    doc = doc_file_at(_write(tmp_path / "lib.sushi", "public fn add(i32 a) i32:\n    return Result.Ok(a)\n"))
    assert doc.is_library


def test_a_file_with_main_is_a_program(tmp_path):
    doc = doc_file_at(_write(tmp_path / "p.sushi", "fn main() i32:\n    return Result.Ok(0)\n"))
    assert not doc.is_library


def test_main_named_in_a_comment_does_not_make_a_program(tmp_path):
    text = "# Compile it, then call fn main() from the shell.\npublic fn add(i32 a) i32:\n    return Result.Ok(a)\n"
    assert doc_file_at(_write(tmp_path / "c.sushi", text)).is_library


# -- what is collected ----------------------------------------------------------

def test_every_collected_file_is_under_docs():
    collected = doc_files()
    assert collected, "the corpus is not empty"
    for doc in collected:
        assert doc.path.is_relative_to(PROJECT_ROOT / "docs")


def test_the_corpus_holds_the_tutorial_and_the_examples():
    files = {doc.file for doc in doc_files()}
    assert "docs/examples/mathlib.sushi" in files
    assert "docs/tutorial/examples/08-structs-and-enums/struct-basics.sushi" in files


# -- the outcomes, end to end ---------------------------------------------------

def _outcome(docs, tmp_path):
    return [(doc.file, outcome, detail)
            for doc, outcome, detail in run_doc_files(docs, tmp_path / "work")]


def test_a_program_that_compiles_passes(tmp_path):
    doc = doc_file_at(_write(tmp_path / "ok.sushi",
                             'fn main() i32:\n    println("Mostly Harmless")\n    return Result.Ok(0)\n'))
    assert _outcome([doc], tmp_path)[0][1] == "PASS"


def test_a_warning_only_program_passes(tmp_path):
    """Exit 1 is a warning, and counting warnings is the trap the sweep already pins."""
    doc = doc_file_at(_write(tmp_path / "warn.sushi",
                             'fn main() i32:\n    let i32 unused = 3\n    println("Mostly Harmless")\n    return Result.Ok(0)\n'))
    assert _outcome([doc], tmp_path)[0][1] == "PASS"


def test_a_broken_program_fails(tmp_path):
    doc = doc_file_at(_write(tmp_path / "bad.sushi",
                             "fn main() i32:\n    let i32 x = nope()\n    return Result.Ok(0)\n"))
    file, outcome, detail = _outcome([doc], tmp_path)[0]
    assert outcome == "FAIL"
    assert "CE2008" in detail


def test_a_library_is_built_as_a_library(tmp_path):
    """No `main` is a CATEGORY, not drift: the file is built with `--lib`."""
    doc = doc_file_at(_write(tmp_path / "mathy.sushi",
                             "public fn add(i32 a, i32 b) i32:\n    return Result.Ok(a + b)\n"))
    assert _outcome([doc], tmp_path)[0][1] == "PASS"


def test_a_marked_error_file_is_expected(tmp_path):
    doc = doc_file_at(_write(tmp_path / "err.sushi",
                             "# docs-sweep: error CE2008\n\nfn main() i32:\n    let i32 x = nope()\n    return Result.Ok(0)\n"))
    assert _outcome([doc], tmp_path)[0][1] == "EXPECTED-ERROR"


def test_a_marked_skip_is_not_compiled(tmp_path):
    doc = doc_file_at(_write(tmp_path / "skipped.sushi",
                             "# docs-sweep: skip (needs a live socket)\n\nfn main() i32:\n    let i32 x = nope()\n"))
    file, outcome, detail = _outcome([doc], tmp_path)[0]
    assert outcome == "SKIP"
    assert detail == "needs a live socket"


def test_a_consumer_sees_the_library_of_its_own_directory(tmp_path):
    """The ordering rule: every library of a directory is built before its programs."""
    where = tmp_path / "story"
    lib = doc_file_at(_write(where / "guidey.sushi",
                             "public fn answer() i32:\n    return Result.Ok(42)\n"))
    consumer = doc_file_at(_write(where / "use-it.sushi",
                                  'use <lib/guidey>\n\nfn main() i32:\n'
                                  '    println("{answer().realise(0)}")\n    return Result.Ok(0)\n'))
    outcomes = {file: outcome for file, outcome, _detail in _outcome([consumer, lib], tmp_path)}
    assert outcomes == {lib.file: "PASS", consumer.file: "PASS"}


def test_a_program_leaves_no_cache_in_the_source_tree(tmp_path):
    """The collector writes nothing beside the file it compiles."""
    where = tmp_path / "clean"
    doc = doc_file_at(_write(where / "prog.sushi",
                             'fn main() i32:\n    println("Mostly Harmless")\n    return Result.Ok(0)\n'))
    _outcome([doc], tmp_path)
    assert sorted(p.name for p in where.iterdir()) == ["prog.sushi"]


# -- the snippet includes -------------------------------------------------------

def test_every_snippet_include_resolves():
    """A page that includes a file that is not on disk ships a broken snippet."""
    broken = missing_includes()
    assert broken == [], "\n".join(f"{page}:{n} -> {t}" for page, n, t in broken)


def test_the_include_pattern_reads_a_tutorial_line():
    line = '--8<-- "docs/tutorial/examples/08-structs-and-enums/struct-basics.sushi"'
    match = INCLUDE_MARK.search(line)
    assert match is not None
    assert match.group("target").endswith("struct-basics.sushi")


def test_every_include_is_counted_not_only_the_broken_ones():
    """A check that is silent when it passes is invisible."""
    assert len(snippet_includes()) >= len(missing_includes())
    assert all(exists for _page, _n, _t, exists in snippet_includes())
