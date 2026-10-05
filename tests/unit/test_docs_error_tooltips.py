"""A link to an error-catalog entry must show that code's message on hover.

The hook reads the message of each code from `docs/error-catalog.md` and writes it into
the `title` of every link that points at the entry. Material's `content.tooltips`
renders the title as the popup; a click still follows the link to the catalog.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOOK = PROJECT_ROOT / "docs" / "hooks" / "error_tooltips.py"
CATALOG = PROJECT_ROOT / "docs" / "error-catalog.md"
MKDOCS = PROJECT_ROOT / "mkdocs.yml"


def _load_module():
    """`docs/hooks` is not a package, so import the file by path."""
    spec = importlib.util.spec_from_file_location("error_tooltips", HOOK)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


et = _load_module()

SAMPLE = """\
### CE1001 {#ce1001}

**Error** · scope

**Message:** `use of undeclared identifier '{name}'`

### CE2507 {#ce2507}

**Message:** `` `??` takes a `Result@(T, E)`, got '{got}' ``
"""


def test_messages_are_read_from_the_catalog():
    messages = et.read_messages(SAMPLE)
    assert messages == {
        "ce1001": "use of undeclared identifier '{name}'",
        "ce2507": "`??` takes a `Result@(T, E)`, got '{got}'",
    }


def test_every_catalog_entry_has_a_message():
    text = CATALOG.read_text(encoding="utf-8")
    entries = text.count("\n### ")
    assert len(et.read_messages(text)) == entries


def test_a_link_to_an_entry_gets_the_message_as_its_title():
    messages = et.read_messages(SAMPLE)
    html = '<p>It is <a href="../error-catalog/#ce1001">CE1001</a>.</p>'
    out = et.add_titles(html, messages)
    assert out == (
        '<p>It is <a href="../error-catalog/#ce1001" '
        'title="CE1001: use of undeclared identifier &#x27;{name}&#x27;">CE1001</a>.</p>'
    )


def test_a_link_inside_the_catalog_gets_a_title_too():
    messages = et.read_messages(SAMPLE)
    out = et.add_titles('<a href="#ce2507"><code>CE2507</code></a>', messages)
    assert 'title="CE2507: `??` takes a `Result@(T, E)`, got &#x27;{got}&#x27;"' in out


def test_a_link_that_has_a_title_is_left_alone():
    messages = et.read_messages(SAMPLE)
    html = '<a class="headerlink" href="#ce1001" title="Permanent link">&para;</a>'
    assert et.add_titles(html, messages) == html


def test_an_unknown_code_is_left_alone():
    messages = et.read_messages(SAMPLE)
    html = '<a href="../error-catalog/#ce9999">CE9999</a>'
    assert et.add_titles(html, messages) == html


def test_the_site_enables_the_hook_and_the_tooltips():
    config = MKDOCS.read_text(encoding="utf-8")
    assert "docs/hooks/error_tooltips.py" in config
    assert "content.tooltips" in config
