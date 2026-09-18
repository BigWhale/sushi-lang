"""The types the compiler synthesizes: the nine predefined enums and the five generics.

DATA, and one factory per kind. No unit declares any of these, so there is no AST to
collect them from -- the collect pass registers what this module hands it, in the order
it hands it over. A factory rather than a module-level constant: a table lives for one
compilation, and a shared instance would carry one compilation's state into the next.

The variant ORDER of `NetError` and of `IoError` is the ABI. The index is the runtime tag
that the errno tables store into a Result payload, so a variant is only ever APPENDED.
`sushi_lang/backend/runtime/constants.py` reads the same order from the other side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from sushi_lang.semantics.typesys import (
    BuiltinType,
    EnumType,
    EnumVariantInfo,
    PointerType,
    Type,
)
from sushi_lang.semantics.generics.types import (
    GenericEnumType,
    GenericStructType,
    TypeParameter,
)


@dataclass(frozen=True)
class PredefinedEnum:
    """One synthesized enum: its name, its HOME module, and its variants in ABI order.

    `payloads` names the variants that carry associated data; every variant left out of
    it is a unit variant.
    """
    name: str
    home_module: Optional[str]
    variants: tuple[str, ...]
    payloads: Mapping[str, tuple[Type, ...]] = field(default_factory=dict)


# The HOME of every predefined enum (#574, Ruling 3). `docs/design/unit-namespaces.md`
# section 4 says a name no import brings can sit in no namespace, so each is given a
# module: the import GATES the bare name, as `<collections/hashmap>` gates `HashMap`
# (#506), and the alias holds it (`fs.FileMode`). `StdError` is the implicit Result arm
# and stays global.
#
# `SeekFrom` is homed at `<io/contracts>` and not at `<io/fs>`: `Seek.seek(SeekFrom)` is
# declared there, and `<io/fs>` imports `<io/contracts>` to implement the contracts, so
# the other way round would be a cycle. `NetError` gets `<net/error>`, a module created
# for it. `IoError` is `<io/error>`'s because every contract method answers it.
PREDEFINED_ENUMS: tuple[PredefinedEnum, ...] = (
    PredefinedEnum("FileMode", "io/fs", (
        "Read",         # Text read mode ("r")
        "Write",        # Text write mode ("w")
        "Append",       # Text append mode ("a")
        "ReadB",        # Binary read mode ("rb")
        "WriteB",       # Binary write mode ("wb")
        "AppendB",      # Binary append mode ("ab")
    )),
    PredefinedEnum("SeekFrom", "io/contracts", (
        "Start",        # SEEK_SET (0)
        "Current",      # SEEK_CUR (1)
        "End",          # SEEK_END (2)
    )),
    PredefinedEnum("FileError", "io/error", (
        "NotFound",          # ENOENT - File does not exist
        "PermissionDenied",  # EACCES, EPERM - Insufficient permissions
        "AlreadyExists",     # EEXIST - File already exists
        "IsDirectory",       # EISDIR - Path refers to a directory
        "DiskFull",          # ENOSPC - No space left on device
        "TooManyOpen",       # EMFILE, ENFILE - Too many open files
        "InvalidPath",       # ENAMETOOLONG - Invalid path or filename
        "IOError",           # EIO - Generic I/O error
        "Other",             # Any other error
    )),
    # NetError - the socket errors. ResolveFailed is the one variant no errno reaches:
    # getaddrinfo answers with an EAI_* code, whose sign even flips between platforms.
    PredefinedEnum("NetError", "net/error", (
        "ConnectionRefused",    # ECONNREFUSED
        "ConnectionReset",      # ECONNRESET, ECONNABORTED
        "TimedOut",             # ETIMEDOUT, EAGAIN on a blocking socket
        "Closed",               # EPIPE, ENOTCONN, EBADF
        "AddressInUse",         # EADDRINUSE
        "AddressNotAvailable",  # EADDRNOTAVAIL
        "NetworkUnreachable",   # ENETUNREACH, ENETDOWN, ENETRESET
        "HostUnreachable",      # EHOSTUNREACH
        "ResolveFailed",        # a getaddrinfo answer that is not EAI_SYSTEM
        "PermissionDenied",     # EACCES, EPERM
        "TooManyOpen",          # EMFILE, ENFILE
        "InvalidAddress",       # EAFNOSUPPORT, EINVAL, a text that is no address
        "Interrupted",          # EINTR
        "MessageTooLarge",      # EMSGSIZE
        "Other",                # Any other error
    )),
    PredefinedEnum("StdError", None, (
        "Error",        # Generic error
    )),
    # IoError - the ONE channel every io contract method answers (HANDLES.md, rulings R4
    # and R20). A perk contract carries one signature and there is no Self type, so
    # `Reader.read` cannot answer FileError on a File and NetError on a TcpStream. The
    # detailed enums stay on the concrete constructors, whose variants are the ones this
    # vocabulary drops -- every FileError and NetError variant with no twin here belongs
    # to an open, a connect or a bind, and never to a read or a write.
    #
    # `Os` carries the raw errno, which is the thread-safe way to keep the detail -- a
    # global last_errno() is not, and that is why the payload arm is here from the start
    # rather than added later, when it would change every match a user has written.
    PredefinedEnum("IoError", "io/error", (
        "NotFound",          # ENOENT
        "PermissionDenied",  # EACCES, EPERM
        "AlreadyExists",     # EEXIST
        "IsDirectory",       # EISDIR
        "ConnectionReset",   # ECONNRESET, ECONNABORTED
        "TimedOut",          # ETIMEDOUT
        "Closed",            # EPIPE, ENOTCONN, EBADF
        "Interrupted",       # EINTR
        "WouldBlock",        # EAGAIN, EWOULDBLOCK
        "DiskFull",          # ENOSPC
        "TooManyOpen",       # EMFILE, ENFILE
        "InvalidInput",      # EINVAL, ENAMETOOLONG
        "Os",                # the raw errno
        "Other",             # anything else
    ), payloads={"Os": (BuiltinType.I32,)}),
    PredefinedEnum("ProcessError", "sys/process", (
        "SpawnFailed",     # Failed to spawn process
        "ExitFailure",     # Process exited with error
        "SignalReceived",  # Process received signal
    )),
    PredefinedEnum("EnvError", "sys/env", (
        "NotFound",          # Environment variable not found
        "InvalidValue",      # Invalid value
        "PermissionDenied",  # Insufficient permissions
    )),
    PredefinedEnum("MathError", "math", (
        "DivisionByZero",  # Division by zero
        "Overflow",        # Arithmetic overflow
        "Underflow",       # Arithmetic underflow
        "InvalidInput",    # Invalid input to math function
    )),
)


def predefined_enums() -> tuple[EnumType, ...]:
    """The nine synthesized enums, each stamped with its home module."""
    return tuple(
        EnumType(
            name=predefined.name,
            home_module=predefined.home_module,
            variants=tuple(
                EnumVariantInfo(name=variant,
                                associated_types=predefined.payloads.get(variant, ()))
                for variant in predefined.variants
            ),
        )
        for predefined in PREDEFINED_ENUMS
    )


def builtin_generic_enums() -> tuple[GenericEnumType, ...]:
    """`Result@(T, E)` and `Maybe@(T)`."""
    return (
        GenericEnumType(
            name="Result",
            type_params=(TypeParameter(name="T"), TypeParameter(name="E")),
            variants=(
                EnumVariantInfo(name="Ok",
                                associated_types=(TypeParameter(name="T"),)),
                EnumVariantInfo(name="Err",
                                associated_types=(TypeParameter(name="E"),)),
            ),
        ),
        GenericEnumType(
            name="Maybe",
            type_params=(TypeParameter(name="T"),),
            variants=(
                EnumVariantInfo(name="Some",
                                associated_types=(TypeParameter(name="T"),)),
                EnumVariantInfo(name="None", associated_types=()),
            ),
        ),
    )


def builtin_generic_structs() -> tuple[GenericStructType, ...]:
    """`Own@(T)`, `HashMap@(K, V)` and `List@(T)`.

    HashMap is registered unconditionally, like the other two: the table holds the type
    and the per-unit SCOPE decides who may write the name (`unit-namespaces.md` section
    4.3.1). The gate that used to stand here was a process-global set, kept because
    there was no scope to ask.
    """
    from sushi_lang.semantics.generics.hashmap import hashmap_generic_struct

    return (
        # Own<T>: unique ownership of a heap T. The field is really a PointerType.
        GenericStructType(
            name="Own",
            type_params=(TypeParameter(name="T"),),
            fields=(("value", PointerType(pointee_type=TypeParameter(name="T"))),),
        ),
        hashmap_generic_struct(),
        # List<T>: `{i32 len, i32 capacity, T* data}`, 2x growth, lazily allocated.
        # See docs/stdlib/collections/list.md.
        GenericStructType(
            name="List",
            type_params=(TypeParameter(name="T"),),
            fields=(
                ("len", BuiltinType.I32),
                ("capacity", BuiltinType.I32),
                ("data", PointerType(BuiltinType.I32)),  # Placeholder for T*
            ),
        ),
    )
