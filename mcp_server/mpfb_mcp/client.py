"""Client for MPFB's remote control socket (RemoteControlService), optionally spawning a headless Blender."""

import atexit
import itertools
import json
import os
import shlex
import socket
import subprocess
import time
from typing import Any

HOST = os.environ.get("MPFB_HOST", "127.0.0.1")
PORT = int(os.environ.get("MPFB_PORT", "9877"))
TIMEOUT = float(os.environ.get("MPFB_TIMEOUT", "600"))
# Path to a Blender executable: when set, a headless Blender serving MPFB is started if none is listening.
BLENDER = os.environ.get("MPFB_BLENDER", "")
BLENDER_ARGS = shlex.split(os.environ.get("MPFB_BLENDER_ARGS", ""))

_SERVE_SCRIPT = """
import bpy, importlib
mods = [m for m in bpy.context.preferences.addons.keys() if m.split('.')[-1] == 'mpfb']
if not mods:
    raise SystemExit('MPFB is not enabled in this Blender')
importlib.import_module(mods[0] + '.services').RemoteControlService.serve_blocking(%d, parent_pid=%d)
"""


class MpfbError(RuntimeError):
    pass


class MpfbClient:

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self._sock: socket.socket | None = None
        self._buf = b""
        self._process: subprocess.Popen | None = None
        atexit.register(self.close)

    def _connect(self) -> socket.socket:
        if self._sock is not None:
            return self._sock
        try:
            self._sock = socket.create_connection((HOST, PORT), timeout=5)
        except OSError:
            if not BLENDER:
                raise MpfbError(
                    "MPFB is not listening on %s:%d. Open Blender, then MPFB > System > Remote control (MCP) > "
                    "Start remote control (or enable it in the MPFB preferences), or set MPFB_BLENDER to let the "
                    "MCP server start a headless Blender." % (HOST, PORT)) from None
            self._sock = self._spawn()
        self._sock.settimeout(TIMEOUT)
        return self._sock

    def _spawn(self) -> socket.socket:
        cmd = [BLENDER, "-b", *BLENDER_ARGS, "--python-expr", _SERVE_SCRIPT % (PORT, os.getpid())]
        self._process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 120
        while time.time() < deadline:
            if self._process.poll() is not None:
                raise MpfbError("Headless Blender exited with code %s" % self._process.returncode)
            try:
                return socket.create_connection((HOST, PORT), timeout=5)
            except OSError:
                time.sleep(0.5)
        raise MpfbError("Headless Blender did not start serving within 120 s")

    def call(self, command: str, args: dict[str, Any] | None = None) -> Any:
        payload = {"id": next(self._ids), "command": command,
                   "args": {k: v for k, v in (args or {}).items() if v is not None}}
        for attempt in (0, 1):
            sock = self._connect()
            try:
                sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                response = self._read_line(sock)
                break
            except (OSError, ConnectionError):
                # Blender was restarted or the server was toggled: reconnect once.
                self._drop()
                if attempt:
                    raise
        if not response.get("ok"):
            raise MpfbError(response.get("error", "MPFB command failed"))
        return response.get("result")

    def _read_line(self, sock: socket.socket) -> dict[str, Any]:
        while b"\n" not in self._buf:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("MPFB closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))

    def _drop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buf = b""

    def close(self) -> None:
        if self._process is not None and self._process.poll() is None:
            try:
                self.call("shutdown")
            except Exception:  # pylint: disable=W0718
                pass
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._drop()
