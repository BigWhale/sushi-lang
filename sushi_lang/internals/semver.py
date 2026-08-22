"""Semantic versions and the constraints that accept them.

Two callers share this module. The compiler enforces a `.slib`'s `requires_compiler`
when it loads the library, and the Nori resolver matches dependency constraints
(`TODO.md` 6a). It lives in `internals` because that is the layer both can import, and
it has no dependencies outside the standard library so the toolchain build stays
hermetic.

A bare requirement is EXACT (`1.2.3` means `=1.2.3`). Caret, tilde, comparator ranges
and wildcards are opt-in explicit forms, so a build is reproducible without a lockfile.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

__all__ = ["InvalidVersion", "Version", "VersionReq", "default_compiler_req"]

# No leading zeros, exactly three parts, nothing around them. A pre-release suffix is
# not accepted: Sushi versions are major.minor.patch (packager/manifest.py agrees).
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

# A constraint operand may be partial (`1.0` in `>=1.0`) or wildcarded (`1.*`).
_PART_RE = re.compile(r"^(0|[1-9]\d*|\*)$")

_OPERATORS = (">=", "<=", ">", "<", "=")


class InvalidVersion(ValueError):
    """A version or requirement string could not be understood."""


@dataclass(frozen=True, order=True)
class Version:
    """A `major.minor.patch` version. Ordering is by part, most significant first."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> "Version":
        if not isinstance(text, str):
            raise InvalidVersion(f"version must be a string, got {type(text).__name__}")
        m = _VERSION_RE.match(text)
        if m is None:
            raise InvalidVersion(
                f"'{text}' is not a version (expected major.minor.patch, e.g. 1.2.3)")
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def default_compiler_req(compiler: Version) -> str:
    """The `requires_compiler` a build stamps by default: the building compiler's minor.

    Pre-1.0 semver makes the minor the breaking unit, which is how Sushi's 0.x releases
    already behave, so `0.11.1` stamps `~0.11` and rejects `0.12.0`.
    """
    return f"~{compiler.major}.{compiler.minor}"


def _parse_parts(text: str) -> Tuple[List[Optional[int]], bool]:
    """Split a possibly partial, possibly wildcarded operand into up to three parts.

    Returns the parts (None for a wildcard or an absent part) and whether a wildcard
    was written. `1.*` gives `([1, None, None], True)`; `1.0` gives `([1, 0, None], False)`.
    """
    if not text:
        raise InvalidVersion("empty version requirement")
    raw = text.split(".")
    if len(raw) > 3:
        raise InvalidVersion(f"'{text}' has more than three parts")

    parts: List[Optional[int]] = []
    wildcard = False
    for part in raw:
        if _PART_RE.match(part) is None:
            raise InvalidVersion(f"'{text}' is not a version requirement")
        if part == "*":
            wildcard = True
            parts.append(None)
        elif wildcard:
            # `1.*.3` names a part after a wildcard, which cannot mean anything.
            raise InvalidVersion(f"'{text}' names a part after a wildcard")
        else:
            parts.append(int(part))
    while len(parts) < 3:
        parts.append(None)
    return parts, wildcard


def _filled(parts: List[Optional[int]]) -> Version:
    """The lowest version the operand can mean: absent parts read as zero."""
    return Version(parts[0] or 0, parts[1] or 0, parts[2] or 0)


def _bump(parts: List[Optional[int]], index: int) -> Version:
    """The first version ABOVE the range, obtained by incrementing one part."""
    values = [p or 0 for p in parts]
    values[index] += 1
    for i in range(index + 1, 3):
        values[i] = 0
    return Version(values[0], values[1], values[2])


def _caret_bound(parts: List[Optional[int]]) -> Version:
    """Caret pins the leftmost non-zero part: ^1.2.3 <2.0.0, ^0.2.3 <0.3.0, ^0.0.3 <0.0.4."""
    values = [p or 0 for p in parts]
    for index in range(3):
        if values[index] != 0:
            return _bump(parts, index)
    # ^0.0.0 constrains nothing below 0.0.1.
    return Version(0, 0, 1)


def _tilde_bound(parts: List[Optional[int]]) -> Version:
    """Tilde pins the minor when one is written, otherwise the major: ~1.2.3 <1.3.0, ~1 <2.0.0."""
    return _bump(parts, 1) if parts[1] is not None else _bump(parts, 0)


class _Clause:
    """One comparison every candidate version must satisfy."""

    def __init__(self, low: Optional[Version], low_inclusive: bool,
                 high: Optional[Version], high_inclusive: bool) -> None:
        self.low = low
        self.low_inclusive = low_inclusive
        self.high = high
        self.high_inclusive = high_inclusive

    def matches(self, version: Version) -> bool:
        if self.low is not None:
            if version < self.low or (version == self.low and not self.low_inclusive):
                return False
        if self.high is not None:
            if version > self.high or (version == self.high and not self.high_inclusive):
                return False
        return True

    @staticmethod
    def exactly(version: Version) -> "_Clause":
        return _Clause(version, True, version, True)

    @staticmethod
    def between(low: Version, high: Version) -> "_Clause":
        """Half-open `[low, high)` -- the shape every caret/tilde/wildcard expands to."""
        return _Clause(low, True, high, False)


def _parse_clause(text: str) -> _Clause:
    if not text:
        raise InvalidVersion("empty clause in version requirement")

    if text.startswith("~"):
        parts, _ = _parse_parts(text[1:])
        return _Clause.between(_filled(parts), _tilde_bound(parts))

    if text.startswith("^"):
        parts, _ = _parse_parts(text[1:])
        return _Clause.between(_filled(parts), _caret_bound(parts))

    for op in _OPERATORS:
        if text.startswith(op):
            parts, wildcard = _parse_parts(text[len(op):].strip())
            if wildcard:
                raise InvalidVersion(f"'{text}' combines a comparator with a wildcard")
            bound = _filled(parts)
            if op == ">=":
                return _Clause(bound, True, None, False)
            if op == ">":
                return _Clause(bound, False, None, False)
            if op == "<=":
                return _Clause(None, False, bound, True)
            if op == "<":
                return _Clause(None, False, bound, False)
            return _Clause.exactly(bound)

    parts, wildcard = _parse_parts(text)
    if wildcard:
        # `*` constrains nothing; `1.*` and `1.2.*` free the parts to their right.
        free = parts.index(None)
        if free == 0:
            return _Clause(None, False, None, False)
        return _Clause.between(_filled(parts), _bump(parts, free - 1))

    if any(p is None for p in parts):
        raise InvalidVersion(
            f"'{text}' is partial; write all three parts, or a range such as '>={text}'")
    return _Clause.exactly(_filled(parts))


class VersionReq:
    """A requirement: every comma-separated clause must match."""

    def __init__(self, clauses: List[_Clause], text: str) -> None:
        self._clauses = clauses
        self._text = text

    @classmethod
    def parse(cls, text: str) -> "VersionReq":
        if not isinstance(text, str):
            raise InvalidVersion(
                f"version requirement must be a string, got {type(text).__name__}")
        stripped = text.strip()
        if not stripped:
            raise InvalidVersion("empty version requirement")
        clauses = [_parse_clause(part.strip()) for part in stripped.split(",")]
        return cls(clauses, text)

    def matches(self, version: Version) -> bool:
        return all(clause.matches(version) for clause in self._clauses)

    def __str__(self) -> str:
        return self._text
