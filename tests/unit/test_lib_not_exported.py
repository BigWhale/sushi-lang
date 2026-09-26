"""What a library declares and does not export (#469).

The export closure ships the privates a public GENERIC's body needs. A private no
template names ships nowhere, so before this key the consumer's answer was CE2008 --
"undefined function" for a function the library defines and deliberately keeps. The
manifest now carries those names, and the CE3005 gate answers for them, so the two
library kinds agree about the wording as well as the legality.
"""
from __future__ import annotations

from pathlib import Path





# A public generic whose body names a private: the closure ships `helper`, so it is
# booked there and not in `not_exported`.

# The bundled module arrives as an ordinary unit at build time, and its nine private
# helpers are not this library's to declare.












# --- The manifest key --------------------------------------------------------------











# --- The diagnostic ----------------------------------------------------------------











# --- Forward compatibility ----------------------------------------------------------



# --- The registry, without a build --------------------------------------------------

def test_the_registry_reads_the_key():
    from sushi_lang.semantics.library_registry import LibraryRegistry

    registry = LibraryRegistry()
    registry.register_library(
        lib_path=Path("keptlib.slib"),
        manifest={
            "library_name": "keptlib",
            "public_functions": [],
            "not_exported": [
                {"name": "scale", "kind": "function"},
                {"name": "convert", "kind": "generic_function"},
            ],
        },
    )

    assert registry.get_all_not_exported() == {
        "scale": ("keptlib", "function"),
        "convert": ("keptlib", "generic_function"),
    }


def test_a_manifest_without_the_key_reads_as_empty():
    from sushi_lang.semantics.library_registry import LibraryRegistry

    registry = LibraryRegistry()
    registry.register_library(lib_path=Path("plain.slib"),
                              manifest={"library_name": "plain",
                                        "public_functions": []})

    assert registry.get_all_not_exported() == {}
