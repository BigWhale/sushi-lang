"""A template check cuts no copy and writes no program table (#1070).

The check copy of a template names instances over an opaque type parameter. Each writer
that cuts or queues a copy, or that writes a program signature, asks the check first:
`in_template_check`, or one of the two flags that `TemplateValidator` sets to False. A
writer that does not ask would put an opaque instance into the program, where the
backend has no layout for it (the CE0148 backstop catches that at run time; this scan
catches it at review time). A scan of the compiler's Python source.
"""
from __future__ import annotations

import inspect

import pytest

from sushi_lang.semantics.passes.types import TypeValidator
from sushi_lang.semantics.passes.types.calls import generics, methods, statics
from sushi_lang.semantics.passes.types import utils
from sushi_lang.semantics.passes.types.templates import TemplateValidator

# (function, the write it guards, the question it must ask before that write)
WRITERS = [
    (methods.instantiate_array_extension, "extension_table.add_method",
     "in_template_check"),
    (methods.instantiate_array_extension, "_queue_extension_instantiation",
     "in_template_check"),
    (methods.resolve_method_generic_extension, "_queue_extension_instantiation",
     "in_template_check"),
    (statics._interned_static_target, "_add_late_static_copy", "queues_late_copies"),
    (generics._request_late_instance, "request(", "requests_late_instances"),
    (utils.validate_and_register_parameters, "sig_param.ty =", "in_template_check"),
]


@pytest.mark.parametrize("function,write,question", WRITERS,
                         ids=[f"{f.__name__}:{w}" for f, w, _ in WRITERS])
def test_each_writer_asks_before_it_writes(function, write, question):
    source = inspect.getsource(function)
    assert write in source, f"{function.__name__} no longer holds '{write}': update the gate"
    assert question in source[:source.index(write)], (
        f"{function.__name__} writes '{write}' before it asks '{question}'")


def test_the_template_validator_answers_every_question():
    assert TemplateValidator.in_template_check is True
    assert TemplateValidator.queues_late_copies is False
    assert TemplateValidator.requests_late_instances is False
    assert TypeValidator.in_template_check is False
