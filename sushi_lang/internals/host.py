"""The host key: the one name of the machine that the compiler runs on and targets."""
from __future__ import annotations

import platform


def host_key() -> str:
    """The host as `<os>_<arch>`: `darwin_arm64`, `linux_x86_64`, ...

    The compiler targets the host, and the host is the process it runs in. The key selects
    the `<sys/platform>` file and is the platform tag of a `.slib`; `aarch64` is spelled
    `arm64`, so one machine has one key.
    """
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else machine
    return f"{platform.system().lower()}_{arch}"
