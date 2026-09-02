"""A manifest record's `error_type` reaches the consumer's signature (#541)."""
from __future__ import annotations

from pathlib import Path

from sushi_lang.semantics.library_registry import LibraryRegistry
from sushi_lang.semantics.typesys import EnumType, EnumVariantInfo


def _manifest(record: dict) -> dict:
    return {"library_name": "chan", "public_functions": [record],
            "templates": {"version": 5, "private_functions": [
                {"name": "helper", "unit": "chan", "link_symbol": "chan$helper",
                 "params": [], "return_type": "i32", "error_type": "MyErr"}]}}


def _register(record: dict) -> LibraryRegistry:
    my_err = EnumType(name="MyErr", variants=(EnumVariantInfo(name="Bad", associated_types=()),))
    registry = LibraryRegistry()
    registry.register_library(lib_path=Path("chan.slib"), manifest=_manifest(record),
                              struct_table={}, enum_table={"MyErr": my_err})
    return registry


def test_a_spelled_channel_is_the_signature_error_type():
    registry = _register({"name": "risky", "params": [{"name": "x", "type": "i32", "mode": "borrow"}],
                          "return_type": "i32", "error_type": "MyErr"})
    sig = registry.get_all_functions()["risky"]
    assert getattr(sig.err_type, "name", None) == "MyErr"


def test_an_absent_channel_stays_the_default():
    registry = _register({"name": "plain", "params": [], "return_type": "i32"})
    assert registry.get_all_functions()["plain"].err_type is None


def test_a_closure_private_record_reads_the_channel_too():
    registry = _register({"name": "plain", "params": [], "return_type": "i32"})
    (_lib, sig), = registry.get_all_private_functions().values()
    assert getattr(sig.err_type, "name", None) == "MyErr"
