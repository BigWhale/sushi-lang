"""Version parsing, ordering and constraint matching.

The compiler needs this on the library-load path (a `.slib` states which compilers it
accepts, `requires_compiler`), and the Nori resolver needs the same comparisons for
dependency constraints. One module serves both -- see `TODO.md` 6a.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.semver import InvalidVersion, Version, VersionReq


# --- Version ---------------------------------------------------------------

@pytest.mark.parametrize("text,parts", [
    ("0.0.0", (0, 0, 0)),
    ("0.11.1", (0, 11, 1)),
    ("1.0.0", (1, 0, 0)),
    ("10.20.30", (10, 20, 30)),
])
def test_a_version_parses_into_its_three_parts(text, parts):
    v = Version.parse(text)
    assert (v.major, v.minor, v.patch) == parts


@pytest.mark.parametrize("text", [
    "", "1", "1.2", "1.2.3.4", "1.2.x", "v1.2.3", "unknown", "-1.0.0", "1.2.3-rc1",
    "01.2.3", " 1.2.3", "1.2.3 ",
])
def test_a_malformed_version_is_rejected(text):
    with pytest.raises(InvalidVersion):
        Version.parse(text)


def test_versions_order_by_major_then_minor_then_patch():
    shuffled = ["1.0.0", "0.11.0", "2.0.0", "0.9.9", "0.11.1", "0.10.0"]
    ordered = ["0.9.9", "0.10.0", "0.11.0", "0.11.1", "1.0.0", "2.0.0"]
    assert [str(v) for v in sorted(Version.parse(t) for t in shuffled)] == ordered


def test_a_version_round_trips_through_its_string_form():
    assert str(Version.parse("0.11.1")) == "0.11.1"


def test_equal_versions_compare_equal_and_hash_alike():
    a, b = Version.parse("1.2.3"), Version.parse("1.2.3")
    assert a == b
    assert hash(a) == hash(b)


# --- VersionReq: exact is the bare default ---------------------------------
#
# TODO.md 6a: bare `1.2.3` means `=1.2.3`. Caret, tilde, ranges and wildcards are
# opt-in explicit forms, so a build is reproducible without a lockfile.

@pytest.mark.parametrize("req,version,expected", [
    ("1.2.3", "1.2.3", True),
    ("1.2.3", "1.2.4", False),
    ("1.2.3", "1.3.0", False),
    ("=1.2.3", "1.2.3", True),
    ("=1.2.3", "1.2.2", False),
])
def test_a_bare_requirement_is_exact(req, version, expected):
    assert VersionReq.parse(req).matches(Version.parse(version)) is expected


# --- VersionReq: tilde is the compiler-compatibility form ------------------
#
# `~0.11` accepts every 0.11.z and rejects 0.12.0. Pre-1.0 semver makes the minor
# the breaking unit, which is why this is the default `requires_compiler`.

@pytest.mark.parametrize("req,version,expected", [
    ("~0.11", "0.11.0", True),
    ("~0.11", "0.11.1", True),
    ("~0.11", "0.11.99", True),
    ("~0.11", "0.12.0", False),
    ("~0.11", "0.10.9", False),
    ("~0.11", "1.11.0", False),
    ("~1.2.3", "1.2.3", True),
    ("~1.2.3", "1.2.9", True),
    ("~1.2.3", "1.2.2", False),
    ("~1.2.3", "1.3.0", False),
    ("~1", "1.0.0", True),
    ("~1", "1.9.9", True),
    ("~1", "2.0.0", False),
])
def test_tilde_pins_the_minor(req, version, expected):
    assert VersionReq.parse(req).matches(Version.parse(version)) is expected


# --- VersionReq: caret ------------------------------------------------------

@pytest.mark.parametrize("req,version,expected", [
    ("^1.2.3", "1.2.3", True),
    ("^1.2.3", "1.9.9", True),
    ("^1.2.3", "1.2.2", False),
    ("^1.2.3", "2.0.0", False),
    # Below 1.0.0 the leftmost non-zero part is the breaking unit.
    ("^0.2.3", "0.2.9", True),
    ("^0.2.3", "0.3.0", False),
    ("^0.0.3", "0.0.3", True),
    ("^0.0.3", "0.0.4", False),
])
def test_caret_pins_the_leftmost_non_zero_part(req, version, expected):
    assert VersionReq.parse(req).matches(Version.parse(version)) is expected


# --- VersionReq: comparator ranges -----------------------------------------

@pytest.mark.parametrize("req,version,expected", [
    (">=1.0.0, <2.0.0", "1.5.0", True),
    (">=1.0.0, <2.0.0", "2.0.0", False),
    (">=1.0.0, <2.0.0", "0.9.9", False),
    (">=1.0, <2.0", "1.5.0", True),
    (">1.0.0", "1.0.1", True),
    (">1.0.0", "1.0.0", False),
    ("<=1.0.0", "1.0.0", True),
    ("<=1.0.0", "1.0.1", False),
])
def test_a_comparator_range_matches_every_clause(req, version, expected):
    assert VersionReq.parse(req).matches(Version.parse(version)) is expected


# --- VersionReq: wildcards --------------------------------------------------

@pytest.mark.parametrize("req,version,expected", [
    ("1.*", "1.0.0", True),
    ("1.*", "1.9.9", True),
    ("1.*", "2.0.0", False),
    ("1.2.*", "1.2.9", True),
    ("1.2.*", "1.3.0", False),
    ("*", "0.0.1", True),
    ("*", "99.99.99", True),
])
def test_a_wildcard_leaves_its_part_free(req, version, expected):
    assert VersionReq.parse(req).matches(Version.parse(version)) is expected


# --- VersionReq: malformed --------------------------------------------------

@pytest.mark.parametrize("req", [
    "", "  ", "~", "^", ">=", "not-a-version", ">=1.0.0, ", "1.2.3, ", "1..2", "><1.0.0",
])
def test_a_malformed_requirement_is_rejected(req):
    with pytest.raises(InvalidVersion):
        VersionReq.parse(req)


def test_a_requirement_round_trips_through_its_string_form():
    for text in ("1.2.3", "~0.11", "^1.2.3", ">=1.0.0, <2.0.0", "1.*"):
        assert str(VersionReq.parse(text)) == text


# --- The default the compiler writes ---------------------------------------

@pytest.mark.parametrize("compiler,expected", [
    ("0.11.1", "~0.11"),
    ("0.12.0", "~0.12"),
    ("1.4.7", "~1.4"),
])
def test_the_default_requirement_pins_the_building_compilers_minor(compiler, expected):
    from sushi_lang.internals.semver import default_compiler_req

    assert default_compiler_req(Version.parse(compiler)) == expected
