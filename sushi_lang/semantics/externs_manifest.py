"""Reserved built-in extern symbols, and the ones the compiler generates."""
from __future__ import annotations

from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType, Type
from sushi_lang.sushi_stdlib.src.libc_declarations import LIBC_SIGNATURES

#: The Sushi type that a C type of the reserved signatures is written as. `size_t` is
#: `i64`, as the compiler lowers it.
_SUSHI_TYPE_OF_C: dict[str, Type] = {
    "void": BuiltinType.BLANK,
    "int": BuiltinType.I32,
    "size_t": BuiltinType.I64,
    "void*": ForeignPtrType(),
}

#: The libc symbols that are the compiler's own (#1099): a user declaration of one of
#: them must have the same C types (CE5001). Every other libc symbol that the compiler
#: declares is independent of a user declaration of the same name.
RESERVED_NAMES = ("malloc", "free", "exit")

# C link-name -> (param types, return type, var_arg), read from the one libc table.
RESERVED_EXTERNS: dict[str, tuple] = {
    name: (tuple(_SUSHI_TYPE_OF_C[p] for p in LIBC_SIGNATURES[name].params),
           _SUSHI_TYPE_OF_C[LIBC_SIGNATURES[name].ret],
           LIBC_SIGNATURES[name].var_arg)
    for name in RESERVED_NAMES
}


# The generated symbols that live in NO bitcode file: the backend emits them inline
# into the module it compiles, so the stdlib symbol manifest cannot report them.
# Unlike RESERVED_EXTERNS above, these may never be declared at all -- their real
# signature is the compiler's business and an `unsafe external` naming one is CE5013
# (#472). `backend/codegen_llvm.py` reads the runtime half to give it linkonce_odr.
INLINE_RUNTIME_SYMBOLS: frozenset[str] = frozenset({
    "llvm_strlen",
    "llvm_strcmp",
    "utf8_char_count",
})

# The body of the program's `main`: the C `main` is a wrapper that calls it
# (`backend/functions/main_wrapper.py`). It stays internal, so it is not a runtime
# symbol, but the build defines it (#1098).
USER_MAIN_SYMBOL = "user_main"

GENERATED_INLINE_SYMBOLS: frozenset[str] = INLINE_RUNTIME_SYMBOLS | {USER_MAIN_SYMBOL}
