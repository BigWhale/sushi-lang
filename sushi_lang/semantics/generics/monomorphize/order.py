"""The order in which the monomorphize step takes its instantiations (#899)."""
from __future__ import annotations

from typing import Callable, Iterable, List, Tuple, TypeVar

from sushi_lang.internals.report import Diagnostic
from sushi_lang.semantics.generics.extension_targets import instantiation_key

Item = TypeVar("Item")


def _file_ranks(sites: dict) -> dict:
    """Each file of `sites`, ranked by the first site that names it."""
    files: dict = {}
    for _span, filename in sites.values():
        files.setdefault(filename, len(files))
    return files


def in_site_order(items: Iterable[Item], sites: dict,
                  site_key: Callable[[Item], object],
                  tie: Callable[[Item], Tuple[str, ...]]) -> List[Item]:
    """`items` ordered by the first site that named each one, and never by hash.

    A file keeps the rank its first site gave it in `sites`, and in a file the order is
    the line, then the column. An item with no site follows every sited one. `tie` gives
    each item a text key, so two items at one site, or two items with no site, still
    have one order.
    """
    files = _file_ranks(sites)

    def key(item: Item) -> tuple:
        span, filename = sites.get(site_key(item), (None, None))
        if span is None:
            return (1, 0, 0, 0, tie(item))
        return (0, files[filename], span.line, span.col, tie(item))

    return sorted(items, key=key)


def types_in_site_order(instantiations: Iterable[Tuple[str, tuple]],
                        sites: dict) -> List[Tuple[str, tuple]]:
    """Type instantiations `(base, args)`, in the order the program names them."""
    def name(item: Tuple[str, tuple]) -> str:
        return instantiation_key(item[0], item[1])

    return in_site_order(instantiations, sites, name, lambda item: (name(item),))


def functions_in_site_order(instantiations: Iterable[Tuple[object, str, tuple]],
                            sites: dict) -> List[Tuple[object, str, tuple]]:
    """Function instantiations `(unit, name, args)`, in the order the program calls them."""
    def name(item: Tuple[object, str, tuple]) -> str:
        return instantiation_key(item[1], item[2])

    return in_site_order(instantiations, sites, lambda item: ("fn", name(item)),
                         lambda item: (name(item), str(item[0])))


def diagnostics_in_site_order(items: Iterable[Diagnostic], sites: dict) -> List[Diagnostic]:
    """The monomorphize stage's diagnostics, in the order of their sites (#927).

    The stage cuts the types before the functions, so it finds the faults in work order.
    The files rank as they do for the instantiations; a file that no site names follows
    them, in the order of its first diagnostic. In a file the order is the line, then
    the column, and a diagnostic with no span follows the others. The sort is stable.
    """
    items = list(items)
    files = _file_ranks(sites)
    for d in items:
        files.setdefault(d.filename, len(files))

    def key(d: Diagnostic) -> tuple:
        if d.span is None:
            return (files[d.filename], 1, 0, 0)
        return (files[d.filename], 0, d.span.line, d.span.col)

    return sorted(items, key=key)
