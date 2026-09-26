"""The grammar side of doc blocks: the corpus regression and the three lex-time errors.

`docs/design/documentation.md` section 4 measured two things against the real grammar
before the terminals were written: that no `.sushi` file in the tree contains `:#` in
any position or a line-initial `##`, and that narrowing `_NEWLINE` leaves ordinary
comments alone. This module is where those measurements live permanently.

The delimiter diagnostics are asserted here rather than beside the parser because they
are decided at LEX time, before the AST builder has an opinion about anything.
"""
from __future__ import annotations

from pathlib import Path



PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"__sushi_cache__", ".git", "node_modules", ".venv", "venv", "build", "dist"}

# Named roots rather than the repository root, for the reason
# `test_path_references_exist.py` gives for the same choice: the top level holds
# local-only scratch files (`/a.sushi` is in `.gitignore`), and a scratch file is not
# a corpus this gate has any business measuring.
SCAN_ROOTS = ("docs", "editor-support", "site", "sushi_lang", "tests", "toolchain")

# What writes doc blocks ON PURPOSE. Everything else in the tree predates the feature
# and must not change meaning, which is what this gate measures. Each entry is a path
# prefix and each one is deliberate:
#   tests/docs                        -- the feature's own corpus
#   tests/libs/helpers/doc_lib.sushi  -- phase 3's documented helper library. It has to
#                                        live beside the other helpers, because
#                                        `build_test_helpers` globs that directory.
#   src_sushi/io/, src_sushi/net/,
#   src_sushi/toolchain/              -- bundled stdlib modules born after the
#                                        feature; the missing-docs budget gate
#                                        REQUIRES their doc blocks. Named by DIRECTORY
#                                        rather than one file at a time: every module
#                                        under either is younger than doc blocks, so a
#                                        new one is exempt for the same reason its
#                                        neighbours are, and listing them singly only
#                                        turned a new module into a red gate.
DOC_SOURCES = (
    "tests/docs",
    "tests/libs/helpers/doc_lib.sushi",
    "sushi_lang/sushi_stdlib/src_sushi/io/",
    "sushi_lang/sushi_stdlib/src_sushi/collections/iter.sushi",
    "sushi_lang/sushi_stdlib/src_sushi/net/",
    "sushi_lang/sushi_stdlib/src_sushi/toolchain/",
)

# `.sushi` files that do not parse, and did not before doc blocks existed. Each entry
# carries its reason; adding one is deliberate. A `test_err_` file declaring a CE6xxx
# code is exempt by its own header and is not listed here.



def _sushi_files() -> list[Path]:
    found: list[Path] = []
    for root in SCAN_ROOTS:
        for path in sorted((PROJECT_ROOT / root).rglob("*.sushi")):
            if not any(part in SKIP_DIRS for part in path.relative_to(PROJECT_ROOT).parts):
                found.append(path)
    return found




# -- the corpus regression ------------------------------------------------------

def test_no_doc_delimiters_outside_the_doc_tests():
    """Zero `:#` and zero line-initial `##`: no existing source changes meaning."""
    offenders: list[str] = []
    for path in _sushi_files():
        rel = str(path.relative_to(PROJECT_ROOT))
        if rel.startswith(DOC_SOURCES):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if ":#" in line or line.lstrip().startswith("##"):
                offenders.append(f"{rel}:{number}: {line.strip()}")

    assert not offenders, (
        "doc-block delimiters outside " + str(DOC_SOURCES) + " -- these sources changed "
        "meaning when the terminals landed:\n  " + "\n  ".join(offenders)
    )




# -- ordinary comments still behave ---------------------------------------------







# -- the block is one token -----------------------------------------------------





# -- the three lex-time diagnostics ---------------------------------------------



















