"""The print-argument string frames: heap temporaries that a print statement frees."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, List

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


class PrintFrames:
    """The frames of the heap temporaries that a print argument makes (#141).

    A print statement opens a frame, emits its arguments, prints, and frees the frame.
    Two stacks move together, one frame each per open statement. The DATA stack holds
    heap data pointers, freed unconditionally; only a real allocation registers, so a
    literal or a plain load registers nothing. The VALUE stack holds whole string fat
    values, freed through the string destructor, so a literal element (owned=0) is a
    no-op and a heap one is freed once. A `let` store happens outside every print frame,
    so its new owner frees the value instead (#145).
    """

    def __init__(self, codegen: 'LLVMCodegen') -> None:
        self.codegen = codegen
        self._data: List[List[ir.Value]] = []
        self._values: List[List[ir.Value]] = []

    def reset(self) -> None:
        """Drop every open frame, unfreed: a new module starts with none."""
        self._data = []
        self._values = []

    def push(self) -> None:
        """Open a print-argument frame."""
        self._data.append([])
        self._values.append([])

    @contextmanager
    def own_frame(self) -> Iterator[None]:
        """Give an interpolation its OWN frame, discarded unfreed when it completes.

        An interpolation builds concat and to-string buffers and OWNS them: it frees each
        intermediate as the next concat copies its bytes, and hands the RESULT to whatever
        position it lands in. While an enclosing `println(...)` frame was the innermost
        one, the #141 registry claimed those buffers too -- a second owner for an
        interpolation that was a nested call's argument, and exit 133 (#521).

        A frame of its own rather than no frame at all, because the registry is also what
        an EARLY EXIT walks (#295): a `??` that propagates out of a part leaves through
        `free_all_on_early_exit`, which frees what the parts had built so far. That path
        is safe because every part is emitted BEFORE the first concat, so on an early exit
        no intermediate has been freed yet. On the straight-line path the concat loop has
        freed them, so the frame is dropped WITHOUT freeing.
        """
        self.push()
        try:
            yield
        finally:
            self._data.pop()
            self._values.pop()

    def register_data(self, data_ptr: ir.Value) -> None:
        """Register a heap-allocated string buffer if a frame is open."""
        if self._data:
            self._data[-1].append(data_ptr)

    def register_value(self, fat_value: ir.Value) -> None:
        """Register a whole string fat VALUE for an owned-bit-guarded free after output."""
        if self._values:
            self._values[-1].append(fat_value)

    def pop_and_free(self) -> None:
        """Free every temporary registered in the innermost frame, and pop the frame."""
        if not self._data:
            return
        data = self._data.pop()
        values = self._values.pop() if self._values else []
        if self._can_emit():
            self._free(data, values)

    def free_all_on_early_exit(self) -> None:
        """Free the temporaries of every open frame on an EARLY-EXIT path (#295)."""
        if self._data and self._can_emit():
            self._free([p for frame in self._data for p in frame],
                       [v for frame in self._values for v in frame])

    def _can_emit(self) -> bool:
        builder = self.codegen.builder
        return builder is not None and builder.block is not None \
            and not builder.block.is_terminated

    def _free(self, data: List[ir.Value], values: List[ir.Value]) -> None:
        from sushi_lang.backend.memory.heap import emit_free
        from sushi_lang.backend.destructors import emit_string_destructor_from_value
        for data_ptr in data:
            emit_free(self.codegen.builder, self.codegen, data_ptr)
        for fat_value in values:
            emit_string_destructor_from_value(self.codegen, fat_value)
