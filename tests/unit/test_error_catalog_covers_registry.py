"""The error catalog must agree with the diagnostic registry.

`docs/error-catalog.md` has one entry for each code in `REGISTRY`. This gate compares
the two. Each code must have exactly one entry, and the catalog must have no entry for a
code that is not registered. The message, the severity and the category of each entry
must be equal to the registry. The entries must be in code order: the families in the
order CE, CW, RE, NE, and the numbers in increasing order in each family.

The gate does not compare the help or the description. A person writes these by hand.

The message reader is `read_messages` from the tooltip hook, so the page has one parser
of the message line. This test reads the registry and a docs file only. It does not
start the compiler.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

from sushi_lang.internals.errors import REGISTRY
from sushi_lang.internals.errors.registry import ErrorMessage, Severity

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOOK = PROJECT_ROOT / "docs" / "hooks" / "error_tooltips.py"
CATALOG = PROJECT_ROOT / "docs" / "error-catalog.md"

FIX = "add or update the entry in docs/error-catalog.md"
FAMILY_ORDER = ("CE", "CW", "RE", "NE")

_HEADING = re.compile(r"^### (\S+) \{#([a-z]{2}[0-9]{4})\}\s*$")
_SEVERITY = re.compile(r"^\*\*(Error|Warning)\*\* · (\S+)\s*$")
_SEVERITY_WORD = {Severity.ERROR: "Error", Severity.WARNING: "Warning"}


def _load_module():
    """`docs/hooks` is not a package, so import the file by path."""
    spec = importlib.util.spec_from_file_location("error_tooltips", HOOK)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


et = _load_module()


def _entries(catalog: str) -> list[tuple[str, str]]:
    """Return the (code, anchor) of each entry heading, in page order."""
    found = []
    for line in catalog.splitlines():
        heading = _HEADING.match(line)
        if heading:
            found.append((heading.group(1), heading.group(2)))
    return found


def _severities(catalog: str) -> dict[str, tuple[str, str]]:
    """Map each anchor to the (severity, category) line that follows its heading."""
    lines: dict[str, tuple[str, str]] = {}
    anchor = None
    for line in catalog.splitlines():
        heading = _HEADING.match(line)
        if heading:
            anchor = heading.group(2)
            continue
        severity = _SEVERITY.match(line)
        if severity and anchor:
            lines[anchor] = (severity.group(1), severity.group(2))
            anchor = None
    return lines


def _order_key(code: str) -> tuple[int, str]:
    family = code[:2]
    rank = FAMILY_ORDER.index(family) if family in FAMILY_ORDER else len(FAMILY_ORDER)
    return (rank, code[2:])


def catalog_faults(catalog: str, registry: dict[str, ErrorMessage]) -> list[str]:
    """Return one line for each disagreement between the catalog and the registry."""
    faults: list[str] = []
    entries = _entries(catalog)
    codes = [code for code, _ in entries]
    counts = Counter(codes)

    missing = sorted(set(registry) - set(codes))
    if missing:
        faults.append(f"the catalog has no entry for {', '.join(missing)}: {FIX}")
    extra = sorted(set(codes) - set(registry))
    if extra:
        faults.append(
            f"the catalog has an entry for {', '.join(extra)}, but the registry does not "
            f"have this code: remove the entry from docs/error-catalog.md"
        )
    for code in sorted(c for c, n in counts.items() if n > 1):
        faults.append(f"{code} has {counts[code]} entries, not one: {FIX}")
    for code, anchor in entries:
        if anchor != code.lower():
            faults.append(f"{code} has the anchor '#{anchor}', not '#{code.lower()}': {FIX}")

    messages = et.read_messages(catalog)
    severities = _severities(catalog)
    for code in sorted(set(registry) & set(codes)):
        expected = registry[code]
        anchor = code.lower()
        message = messages.get(anchor)
        if message is None:
            faults.append(f"{code} has no message line: {FIX}")
        elif message != expected.text:
            faults.append(
                f"{code} has the message {message!r}, but the registry has "
                f"{expected.text!r}: {FIX}"
            )
        want = (_SEVERITY_WORD[expected.severity], expected.category.value)
        got = severities.get(anchor)
        if got is None:
            faults.append(f"{code} has no severity and category line: {FIX}")
        elif got != want:
            faults.append(
                f"{code} has '{got[0]} · {got[1]}', but the registry has "
                f"'{want[0]} · {want[1]}': {FIX}"
            )

    for before, after in zip(codes, codes[1:], strict=False):
        if _order_key(after) <= _order_key(before):
            faults.append(
                f"{after} comes after {before}, which is not code order "
                f"({', '.join(FAMILY_ORDER)}, then by number): move the entry in "
                f"docs/error-catalog.md"
            )
    return faults


def test_the_catalog_covers_the_registry():
    faults = catalog_faults(CATALOG.read_text(encoding="utf-8"), REGISTRY)
    assert not faults, "\n".join(faults)


# The tests below change a copy of the real catalog. Each change must give a fault.
# This shows that the gate can fail.

def _catalog() -> str:
    return CATALOG.read_text(encoding="utf-8")


def _first_code() -> str:
    return _entries(_catalog())[0][0]


def _entry_block(catalog: str, code: str) -> str:
    start = catalog.index(f"### {code} {{#{code.lower()}}}")
    end = catalog.find("\n### ", start)
    return catalog[start:] if end == -1 else catalog[start:end + 1]


def test_a_removed_entry_is_found():
    code = _first_code()
    catalog = _catalog()
    mutated = catalog.replace(_entry_block(catalog, code), "", 1)
    faults = catalog_faults(mutated, REGISTRY)
    assert any(code in f and "no entry" in f for f in faults), faults


def test_a_changed_message_is_found():
    code = _first_code()
    text = REGISTRY[code].text
    catalog = _catalog()
    block = _entry_block(catalog, code)
    mutated = catalog.replace(block, block.replace(text, text + " (changed)", 1), 1)
    faults = catalog_faults(mutated, REGISTRY)
    assert any(code in f and "message" in f for f in faults), faults


def test_a_changed_severity_is_found():
    code = _first_code()
    catalog = _catalog()
    block = _entry_block(catalog, code)
    mutated = catalog.replace(block, block.replace("**Error** ·", "**Warning** ·", 1), 1)
    faults = catalog_faults(mutated, REGISTRY)
    assert any(code in f and "Warning" in f for f in faults), faults


def test_a_changed_category_is_found():
    code = _first_code()
    category = REGISTRY[code].category.value
    catalog = _catalog()
    block = _entry_block(catalog, code)
    mutated = catalog.replace(
        block, block.replace(f"· {category}", "· not-a-category", 1), 1
    )
    faults = catalog_faults(mutated, REGISTRY)
    assert any(code in f and "not-a-category" in f for f in faults), faults


def test_an_extra_entry_is_found():
    extra = "### CE9999 {#ce9999}\n\n**Error** · type\n\n**Message:** `not registered`\n"
    faults = catalog_faults(_catalog() + "\n" + extra, REGISTRY)
    assert any("CE9999" in f and "does not have this code" in f for f in faults), faults


def test_a_duplicate_entry_is_found():
    code = _first_code()
    catalog = _catalog()
    faults = catalog_faults(catalog + "\n" + _entry_block(catalog, code), REGISTRY)
    assert any(code in f and "entries, not one" in f for f in faults), faults


def test_an_entry_out_of_order_is_found():
    code = _first_code()
    catalog = _catalog()
    block = _entry_block(catalog, code)
    mutated = catalog.replace(block, "", 1) + "\n" + block
    faults = catalog_faults(mutated, REGISTRY)
    assert any(code in f and "code order" in f for f in faults), faults
