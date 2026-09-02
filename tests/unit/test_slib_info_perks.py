"""`--lib-info` lists a library's perks: each contract with its method signatures, and every
implementation of it -- concrete and generic-target alike -- with the same signatures (#537).

Since the handles epic the io contracts are shipped perks, so a contract is a first-class
part of a library's public surface, and the one report a consumer reads has to show it.
The manifest already carried the records (#543); the perk record carried no methods, and
the generic-target template was printed nowhere. Both renderers change together: the
Python fallback and the Sushi tool must print the same body.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_slib_doc_carriage import build_library  # noqa: E402
from sushic_path import SUSHIC, SUSHIC_AVAILABLE

REPO = Path(__file__).resolve().parents[2]
TOOL_SRC = REPO / "toolchain" / "src" / "slib_info.sushi"

# One public perk with a plain method and one that takes `poke self` and declares a
# channel; one concrete implementation and one generic-target template of it.
PERK_LIB = """\
##: Things that can show themselves. :##
public perk Show:
    ##: Render the value. :##
    fn show() string
    fn checked_show(poke self, i32 width) string | ShowError

public enum ShowError:
    TooWide

public struct Box@(T):
    T item

public struct Gadget:
    i32 base

extend Box@(T) with Show:
    fn show() string:
        return "box"
    fn checked_show(poke self, i32 width) string | ShowError:
        if (width > 80):
            return Result.Err(ShowError.TooWide)
        return "box"

##: A gadget shows its base. :##
extend Gadget with Show:
    ##: The base, as text. :##
    fn show() string:
        return "gadget {self.base}"
    fn checked_show(poke self, i32 width) string | ShowError:
        return "gadget {width}"
"""


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if not SUSHIC_AVAILABLE:
        pytest.skip("no compiler driver in this checkout")
    tmp = tmp_path_factory.mktemp("slibperks")
    slib, metadata = build_library(tmp, "perklib", PERK_LIB)
    tool = tmp / "slib-info"
    r = _run([SUSHIC, str(TOOL_SRC), "-o", str(tool)], cwd=tmp)
    assert r.returncode == 0, r.stdout + r.stderr
    return slib, tool, metadata


def _reports(built, *flags):
    slib, tool, _metadata = built
    tool_run = _run([str(tool), *flags, str(slib)])
    assert tool_run.returncode == 0, tool_run.stdout + tool_run.stderr
    env = dict(os.environ)
    env.pop("SUSHI_TOOLCHAIN_BIN", None)
    env["SUSHI_TOOLCHAIN"] = "off"
    py_run = _run([SUSHIC, "--lib-info", str(slib), *flags], env=env)
    assert py_run.returncode == 0, py_run.stdout + py_run.stderr
    return py_run.stdout, tool_run.stdout


@pytest.fixture(scope="module")
def plain(built):
    return _reports(built)


@pytest.fixture(scope="module")
def documented(built):
    return _reports(built, "--docs")


# ---------------------------------------------------------------- the manifest

def test_a_perk_record_carries_its_method_signatures(built):
    _slib, _tool, metadata = built
    perk = next(p for p in metadata["templates"]["perks"] if p["name"] == "Show")
    by_name = {m["name"]: m for m in perk["methods"]}
    assert by_name["show"]["params"] == []
    assert by_name["show"]["return_type"] == "string"
    assert "error_type" not in by_name["show"]
    checked = by_name["checked_show"]
    assert [p["name"] for p in checked["params"]] == ["width"]
    assert checked["return_type"] == "string"
    assert checked["error_type"] == "ShowError"
    assert checked["self_mode"] == "poke"
    assert "self_mode" not in by_name["show"]


def test_a_perk_method_carries_its_own_block(built):
    _slib, _tool, metadata = built
    perk = next(p for p in metadata["templates"]["perks"] if p["name"] == "Show")
    show = next(m for m in perk["methods"] if m["name"] == "show")
    assert show["doc"]["summary"] == "Render the value."


def test_an_implementation_method_carries_the_same_signature(built):
    _slib, _tool, metadata = built
    impl = next(i for i in metadata["templates"]["perk_impls"] if i["type"] == "Gadget")
    checked = next(m for m in impl["methods"] if m["name"] == "checked_show")
    assert checked["return_type"] == "string"
    assert checked["error_type"] == "ShowError"
    assert checked["self_mode"] == "poke"
    assert checked["symbol"]  # the link symbol stays: a concrete copy is in the bitcode


def test_a_generic_target_template_lists_its_methods(built):
    _slib, _tool, metadata = built
    template = next(i for i in metadata["templates"]["generic_perk_impls"]
                    if i["type"] == "Box")
    assert [m["name"] for m in template["methods"]] == ["show", "checked_show"]
    assert template["methods"][1]["error_type"] == "ShowError"
    assert "symbol" not in template["methods"][0]  # nothing to link: it is source alone


# ------------------------------------------------------------------ the report

@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_perk_prints_each_method_signature(plain, which):
    assert ("Perks (1):\n"
            "  perk Show:\n"
            "    fn show() string\n"
            "    fn checked_show(poke self, i32 width) string | ShowError\n"
            "\n") in plain[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_every_implementation_prints_with_its_signatures(plain, which):
    """The concrete implementation and the generic-target template share one section."""
    assert ("Perk Implementations (2):\n"
            "  extend Gadget with Show:\n"
            "    fn show() string\n"
            "    fn checked_show(poke self, i32 width) string | ShowError\n"
            "  extend Box@(T) with Show:\n"
            "    fn show() string\n"
            "    fn checked_show(poke self, i32 width) string | ShowError\n"
            "\n") in plain[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_a_perk_method_block_prints_under_its_signature(documented, which):
    assert ("Perks (1):\n"
            "  perk Show:\n"
            "    Things that can show themselves.\n"
            "\n"
            "    fn show() string\n"
            "      Render the value.\n"
            "\n"
            "    fn checked_show(poke self, i32 width) string | ShowError\n"
            "\n") in documented[which]


@pytest.mark.parametrize("which", [0, 1], ids=["python", "tool"])
def test_an_implementation_block_prints_under_its_header(documented, which):
    assert ("  extend Gadget with Show:\n"
            "    A gadget shows its base.\n"
            "\n"
            "    fn show() string\n"
            "      The base, as text.\n"
            "\n"
            "    fn checked_show(poke self, i32 width) string | ShowError\n"
            "  extend Box@(T) with Show:\n") in documented[which]


def test_the_two_implementations_agree(plain, documented):
    assert plain[0].endswith(plain[1])
    assert documented[0].endswith(documented[1])
