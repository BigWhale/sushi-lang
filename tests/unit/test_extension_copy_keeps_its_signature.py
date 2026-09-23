"""A monomorphized extension method and perk method keep the template's signature fields.

The copy is the template with its types substituted and nothing else changed. The
extension path rebuilt the declaration field by field and the perk path rebuilt each
parameter field by field, so each dropped what it did not spell: the doc block, the
receiver-mode span, the parameter `loc` and the rest (#803). `substitute_signature` in
`generics/extensions.py` is the one substitution both paths call.
"""
from __future__ import annotations

from dataclasses import fields

from sushi_lang.semantics.ast import ExtendDef, FuncDef, Param

SRC = """perk Sz:
    fn sz(i32 k) i32

struct Bx@(T):
    T v

##: The value, k times. :##
extend Bx@(T) scaled(poke self, nom string tag, i32 k) i32:
    return k

##: One array element. :##
extend T[] head_of(i32 at) T:
    return self[at]

extend Bx@(T) with Sz:
    ##: The size. :##
    fn sz(poke self, i32 k) i32:
        return Result.Ok(k)

fn main() i32:
    let Bx@(i32) p = Bx(1)
    let i32[] a = from([4, 5])
    let i32 n = p.scaled(nom 'tag', 2)
    println("{n} {p.sz(3).realise(0)} {a.head_of(1)}")
    return Result.Ok(0)
"""

# A copy is a new instance: its types, its body and whether it is still generic differ.
_SUBSTITUTED = {"target_type", "params", "ret", "err_type", "body", "type_params",
                "method_type_args", "ownership_provenance"}


def _copies(analyze_program):
    analysis = analyze_program(SRC)
    program, analyzer = analysis.program, analysis.analyzer
    extensions = {e.name: e for e in analyzer.monomorphized_extensions}
    templates = {e.name: e for e in program.generic_extensions}
    impl_copy = next(i for i in program.perk_impls if i.is_synthesized)
    impl_template = program.generic_perk_impls[0]
    assert set(extensions) == {"scaled", "head_of"}, "the gate is vacuous without the copies"
    return extensions, templates, impl_copy.methods[0], impl_template.methods[0]


def _assert_param_kept(copy: Param, template: Param) -> None:
    for f in fields(Param):
        if f.name == "ty":
            continue
        assert getattr(copy, f.name) == getattr(template, f.name), f.name
    assert copy.loc is not None


def _assert_decl_kept(copy, template, cls) -> None:
    for f in fields(cls):
        if f.name in _SUBSTITUTED:
            continue
        assert getattr(copy, f.name) == getattr(template, f.name), f.name


def test_an_extension_copy_keeps_every_declaration_field(analyze_program):
    extensions, templates, _, _ = _copies(analyze_program)
    for name in ("scaled", "head_of"):
        _assert_decl_kept(extensions[name], templates[name], ExtendDef)
        assert extensions[name].doc is not None
    assert extensions["scaled"].self_mode_span is not None


def test_an_extension_copy_keeps_every_parameter_field(analyze_program):
    extensions, templates, _, _ = _copies(analyze_program)
    for name in ("scaled", "head_of"):
        pairs = zip(extensions[name].params, templates[name].params, strict=True)
        for copy, template in pairs:
            _assert_param_kept(copy, template)
    assert extensions["scaled"].params[0].is_nom


def test_a_perk_method_copy_keeps_every_declaration_field(analyze_program):
    _, _, method, template = _copies(analyze_program)
    _assert_decl_kept(method, template, FuncDef)
    assert method.doc is not None and method.self_mode_span is not None


def test_a_perk_method_copy_keeps_every_parameter_field(analyze_program):
    _, _, method, template = _copies(analyze_program)
    for copy, original in zip(method.params, template.params, strict=True):
        _assert_param_kept(copy, original)


def test_the_substitution_puts_the_argument_in_the_type(analyze_program):
    from sushi_lang.semantics.generics.extensions import substitute_signature
    from sushi_lang.semantics.typesys import BuiltinType

    extensions, _, _, _ = _copies(analyze_program)
    assert extensions["head_of"].ret == BuiltinType.I32
    assert callable(substitute_signature)
