"""The #502 hint fires on the hole shape and stays quiet elsewhere."""
from __future__ import annotations

from sushi_lang.internals.parser import _string_opens_a_hole_before


def test_an_open_hole_before_the_error_is_seen():
    line = '    println("n={pick("ab")??}")'
    col = line.index("ab") + 1
    assert _string_opens_a_hole_before(line, col)


def test_a_later_hole_is_seen_too():
    line = '    println("{fetch(k).realise("?")}")'
    col = line.index("?") + 1
    assert _string_opens_a_hole_before(line, col)


def test_a_plain_string_stays_quiet():
    line = '    println("n=" m)'
    col = line.index(" m") + 2
    assert not _string_opens_a_hole_before(line, col)


def test_a_closed_hole_stays_quiet():
    line = '    println("n={x}" m)'
    col = line.index(" m") + 2
    assert not _string_opens_a_hole_before(line, col)


def test_an_escaped_quote_does_not_end_the_literal():
    line = '    println("a\\"b" m)'
    col = line.index(" m") + 2
    assert not _string_opens_a_hole_before(line, col)
