#!/usr/bin/env python3
"""The fork-server of the test runner (#1059): one compiler start-up for a whole run.

A fresh `sushic` process spends most of a small compile on its start-up: the `uv run`
wrapper, the interpreter, the imports and the grammar. The server does that ONCE. It
starts the way `./sushic` starts the compiler (`uv run --frozen --active python`, in the
checkout), imports the compiler, builds the grammar, and then forks one child for each
compile. The child is a copy of that clean image: it takes the arguments, the working
directory and the environment of its request, runs `python -m sushi_lang` through
`runpy` as the wrapper does, and exits with its code. No compile runs in the server, so
no state passes from one compile to the next.

The server is single-threaded when it forks. The runner's worker threads talk to it
over a Unix socket, one connection for each compile. The server waits for each child,
kills a child at its timeout, and sends back the exit status; a child that a signal
stopped answers the negative signal number, as `subprocess` does. The child writes its
stdout and stderr to the two files the request names.
"""
from __future__ import annotations

import json
import os
import selectors
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

# The compiler reads these before the server forks (the grammar-cache directory, when
# the grammar is built). A compile whose environment gives one of them another value
# than the server had cannot use the server.
IMPORT_TIME_ENV = ("SUSHI_GRAMMAR_CACHE_DIR", "XDG_CACHE_HOME", "HOME")

# The modules that `cli.main` and the pipeline import inside their functions, for a
# compile that reaches code generation. The server imports them before it forks, so that
# each child does not.
_WARM_MODULES = (
    "sushi_lang.compiler.cli",
    "sushi_lang.compiler.loader",
    "sushi_lang.compiler.options",
    "sushi_lang.compiler.pipeline",
    "sushi_lang.internals.errors",
    "sushi_lang.internals.parser",
    "sushi_lang.backend.codegen_llvm",
    "sushi_lang.backend.driver",
    "sushi_lang.backend.library_paths",
    "sushi_lang.backend.stdlib_builder",
)

_HEADER = struct.Struct("!I")


def _send(sock: socket.socket, message: dict) -> None:
    data = json.dumps(message).encode("utf-8")
    sock.sendall(_HEADER.pack(len(data)) + data)


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = []
    while count:
        chunk = sock.recv(count)
        if not chunk:
            raise ConnectionError("the fork-server closed the connection")
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def _recv(sock: socket.socket) -> dict:
    (length,) = _HEADER.unpack(_recv_exact(sock, _HEADER.size))
    return json.loads(_recv_exact(sock, length).decode("utf-8"))


def wrapper_env(env: Mapping[str, str], cwd: str, project_root: str) -> Dict[str, str]:
    """The environment `./sushic` gives the compiler, from the one its caller gives it.

    The wrapper is bash: it names the caller's directory in SUSHI_CWD, and its `cd` into
    the checkout sets PWD and OLDPWD. Bash also counts SHLVL up. Each is set only when
    the caller's environment has it, as bash exports only those.
    """
    out = dict(env)
    out["SUSHI_CWD"] = cwd
    if "PWD" in out:
        out["PWD"] = project_root
    if "OLDPWD" in out:
        out["OLDPWD"] = cwd
    if "SHLVL" in out:
        try:
            out["SHLVL"] = str(int(out["SHLVL"]) + 1)
        except ValueError:
            out["SHLVL"] = "1"
    return out


def can_fork_for(env: Mapping[str, str], server_env: Mapping[str, str]) -> bool:
    """Does a compile with `env` give the same answer in a child of the server?"""
    return all(env.get(name) == server_env.get(name) for name in IMPORT_TIME_ENV)


# --- the runner's half -------------------------------------------------------------


class ForkServer:
    """The runner's handle on one server. `run` is safe to call from many threads."""

    def __init__(self, project_root: Path, work_dir: Path):
        self.project_root = Path(project_root)
        self.work_dir = Path(work_dir)
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._control: Optional[int] = None
        self._socket_dir: Optional[str] = None
        self._socket_path = ""
        self._counter = 0
        self.env: Dict[str, str] = {}
        # The process id of the server itself, under `uv run`.
        self.server_pid = 0

    def _start(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        # A Unix socket path has a short limit (104 bytes on macOS), so the socket gets a
        # short directory of its own.
        self._socket_dir = tempfile.mkdtemp(prefix="sushi-fs-")
        self._socket_path = os.path.join(self._socket_dir, "s")
        self.env = dict(os.environ)
        base = self.work_dir / "server-env.json"
        base.write_text(json.dumps(self.env), encoding="utf-8")
        control_r, control_w = os.pipe()
        log = open(self.work_dir / "server.log", "wb")
        try:
            self._proc = subprocess.Popen(
                ["uv", "run", "--frozen", "--active", "python", str(Path(__file__).resolve()),
                 self._socket_path, str(base), str(control_r)],
                cwd=self.project_root, env=self.env, stdout=subprocess.PIPE, stderr=log,
                pass_fds=(control_r,))
        finally:
            os.close(control_r)
            log.close()
        self._control = control_w
        assert self._proc.stdout is not None
        ready = self._proc.stdout.readline().split()
        if ready[:1] != [b"ready"]:
            detail = (self.work_dir / "server.log").read_text(errors="replace")
            self.close()
            raise RuntimeError(f"the fork-server did not start: {detail.strip()}")
        self.server_pid = int(ready[1])

    def run(self, args: List[str], cwd: str, env: Mapping[str, str], timeout: float,
            on_start: Optional[Callable[[int], None]] = None
            ) -> Optional[subprocess.CompletedProcess]:
        """Compile as `subprocess.run([sushic, *args], cwd=cwd, env=env)` would.

        None when the server cannot give that answer (see `can_fork_for`): the caller
        then starts a fresh process. `on_start` receives the process id of the child.
        """
        with self._lock:
            if self._proc is None:
                self._start()
            self._counter += 1
            number = self._counter
        if not can_fork_for(env, self.env):
            return None
        out = self.work_dir / f"{number}.out"
        err = self.work_dir / f"{number}.err"
        request = {"argv": list(args), "cwd": str(cwd),
                   "env": wrapper_env(env, str(cwd), str(self.project_root)),
                   "stdout": str(out), "stderr": str(err), "timeout": timeout}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            # The server kills the child at its timeout; this is the guard on the server.
            sock.settimeout(timeout + 60)
            sock.connect(self._socket_path)
            _send(sock, request)
            started = _recv(sock)
            if on_start is not None:
                on_start(started["pid"])
            reply = _recv(sock)
        command = [str(self.project_root / "sushic"), *args]
        try:
            stdout = out.read_text()
            stderr = err.read_text()
        finally:
            out.unlink(missing_ok=True)
            err.unlink(missing_ok=True)
        if reply.get("timed_out"):
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(command, reply["returncode"], stdout, stderr)

    def close(self) -> None:
        with self._lock:
            if self._control is not None:
                os.close(self._control)
                self._control = None
            if self._proc is not None:
                try:
                    self._proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
                if self._proc.stdout is not None:
                    self._proc.stdout.close()
                self._proc = None
            if self._socket_dir is not None:
                try:
                    os.unlink(self._socket_path)
                except OSError:
                    pass
                os.rmdir(self._socket_dir)
                self._socket_dir = None


# --- the server's half -------------------------------------------------------------


def _warm(project_root: str) -> None:
    import importlib

    for name in _WARM_MODULES:
        importlib.import_module(name)
    from sushi_lang.internals.parser import build_parser
    build_parser()


def _exit_code(exc: SystemExit) -> int:
    """The exit status Python gives for an uncaught SystemExit."""
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code & 0xFF
    print(code, file=sys.stderr)
    return 1


def _run_cli() -> int:
    """`python -m sushi_lang`, then the interpreter's own shutdown steps."""
    import atexit
    import runpy

    try:
        runpy.run_module("sushi_lang", run_name="__main__", alter_sys=True)
        code = 0
    except SystemExit as exc:
        code = _exit_code(exc)
    except BaseException:
        sys.excepthook(*sys.exc_info())
        code = 1
    shutdown = getattr(threading, "_shutdown", None)
    if shutdown is not None:
        shutdown()
    atexit._run_exitfuncs()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            code = 120
    return code


def _child(request: dict, project_root: str, delta: Dict[str, str],
           removed: List[str], close_fds: List[int]) -> None:
    """The forked child: become the compile the request names, then exit. Never returns."""
    code = 1
    try:
        signal.set_wakeup_fd(-1)
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        for fd in close_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        for fd, path in ((1, request["stdout"]), (2, request["stderr"])):
            opened = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.dup2(opened, fd)
            os.close(opened)
        os.chdir(project_root)
        env = dict(request["env"])
        env.update(delta)
        for name in removed:
            env.pop(name, None)
        os.environ.clear()
        os.environ.update(env)
        sys.argv = [sys.argv[0], *request["argv"]]
        code = _run_cli()
    except BaseException:
        try:
            import traceback
            traceback.print_exc()
            sys.stderr.flush()
        except BaseException:
            pass
    finally:
        os._exit(code)


class _Child:
    def __init__(self, conn: socket.socket, timeout: float):
        self.conn = conn
        self.deadline = time.monotonic() + timeout
        self.timed_out = False


def serve(socket_path: str, base_env_path: str, control_fd: int) -> int:
    """The server loop. It ends when the runner closes the control pipe."""
    project_root = str(Path(__file__).resolve().parent.parent)
    # `python -m` puts the directory it starts in first on the path, and the wrapper
    # starts in the checkout. A script puts its own directory there instead.
    sys.path[0] = project_root
    base = json.loads(Path(base_env_path).read_text(encoding="utf-8"))
    # What `uv run` adds to the environment it was given: the child adds it too.
    delta = {k: v for k, v in os.environ.items() if base.get(k) != v}
    removed = [k for k in base if k not in os.environ]
    _warm(project_root)
    if threading.active_count() != 1:
        print("the fork-server has more than one thread after warm-up", file=sys.stderr)
        return 1

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(socket_path)
    listener.listen(128)
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_r, False)
    os.set_blocking(wake_w, False)
    signal.set_wakeup_fd(wake_w)
    signal.signal(signal.SIGCHLD, lambda signum, frame: None)
    selector = selectors.DefaultSelector()
    selector.register(listener, selectors.EVENT_READ, "accept")
    selector.register(wake_r, selectors.EVENT_READ, "wake")
    selector.register(control_fd, selectors.EVENT_READ, "control")
    children: Dict[int, _Child] = {}
    sys.stdout.write(f"ready {os.getpid()}\n")
    sys.stdout.flush()

    running = True
    while running or children:
        now = time.monotonic()
        wait = min((c.deadline for c in children.values()), default=now + 1.0) - now
        for key, _ in selector.select(max(0.0, min(wait, 1.0))):
            if key.data == "accept":
                conn, _ = listener.accept()
                conn.setblocking(True)
                request = _recv(conn)
                sys.stdout.flush()
                sys.stderr.flush()
                pid = os.fork()
                if pid == 0:
                    selector.close()
                    fds = [listener.fileno(), conn.fileno(), wake_r, wake_w, control_fd,
                           *(c.conn.fileno() for c in children.values())]
                    _child(request, project_root, delta, removed, fds)
                children[pid] = _Child(conn, float(request["timeout"]))
                try:
                    _send(conn, {"pid": pid})
                except OSError:
                    pass
            elif key.data == "wake":
                try:
                    while os.read(wake_r, 512):
                        pass
                except BlockingIOError:
                    pass
            elif key.data == "control":
                if not os.read(control_fd, 512):
                    selector.unregister(control_fd)
                    selector.unregister(listener)
                    running = False
                    for pid in children:
                        _kill(pid)
        while children:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
            child = children.pop(pid, None)
            if child is None:
                continue
            try:
                _send(child.conn, {"returncode": os.waitstatus_to_exitcode(status),
                                   "timed_out": child.timed_out})
            except OSError:
                pass
            child.conn.close()
        now = time.monotonic()
        for pid, child in children.items():
            if not child.timed_out and now >= child.deadline:
                child.timed_out = True
                _kill(pid)
    listener.close()
    return 0


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


if __name__ == "__main__":
    sys.exit(serve(sys.argv[1], sys.argv[2], int(sys.argv[3])))
