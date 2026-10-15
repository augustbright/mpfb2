"""Local socket server that lets other programs (for example the MPFB MCP server) run AgentService commands.

Protocol: newline-delimited JSON over TCP on 127.0.0.1. Each request is one line,

    {"id": 1, "command": "create_human", "args": {"name": "Hero"}}

and gets one response line,

    {"id": 1, "ok": true, "result": {...}}  or  {"id": 1, "ok": false, "error": "...", "traceback": "..."}

Besides the AgentService commands, "ping" answers with versions, "reload_agent" re-imports the AgentService and
GarmentService code from disk, and "shutdown" stops a blocking server.

In the Blender UI the server runs on background threads and hands every request to the main thread through
bpy.app.timers, since bpy may only be used from there. With "blender -b" there is no event loop for timers, so
serve_blocking() handles connections on the main thread instead.

Only fixed commands are exposed (no arbitrary code), and the server only listens on the loopback interface. It is
never started unless the user enables it (preferences or the Remote control panel) or a script calls start().
"""

import json, os, queue, socket, threading, traceback  # pylint: disable=C0410
from typing import Any

import bpy

from .logservice import LogService
from .agentservice import AgentService

_LOG = LogService.get_logger("services.remotecontrolservice")

DEFAULT_PORT = 9877
_POLL_INTERVAL = 0.05


class _Request():

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.response: dict[str, Any] | None = None
        self.done = threading.Event()


class RemoteControlService():
    """Start, stop and query the remote control server. See the module docstring."""

    _server: socket.socket | None = None
    _thread: threading.Thread | None = None
    _queue: "queue.Queue[_Request]" = queue.Queue()
    _port: int | None = None
    _stop = threading.Event()

    def __init__(self) -> None:
        raise RuntimeError("You should not instance RemoteControlService. Use its static methods instead.")

    # ----------------------------------------------------------------------------------------------------------------

    @staticmethod
    def handle(payload: dict[str, Any]) -> dict[str, Any]:
        """Run one request on the calling (main) thread and build the response."""
        rid = payload.get("id")
        command = payload.get("command")
        try:
            if command == "reload_agent":
                result = RemoteControlService.reload_agent()
            elif command == "ping":
                from .. import VERSION  # pylint: disable=C0415
                result: Any = {"mpfb_version": VERSION, "blender_version": bpy.app.version_string,
                               "background": bpy.app.background, "commands": AgentService.command_names()}
            else:
                result = AgentService.call(str(command), payload.get("args") or {})
                if not bpy.app.background:
                    try:
                        bpy.ops.ed.undo_push(message="MPFB remote: " + str(command))
                    except RuntimeError:
                        pass
            return {"id": rid, "ok": True, "result": result}
        except Exception as err:  # pylint: disable=W0718
            _LOG.error("Remote command failed", (command, err))
            return {"id": rid, "ok": False, "error": "%s: %s" % (type(err).__name__, err),
                    "traceback": traceback.format_exc()}

    @staticmethod
    def reload_agent() -> list[str]:
        """Re-import ClothesService, GarmentService and AgentService from disk, so command changes take effect without restarting
        Blender (developer convenience; the rest of MPFB is not reloaded)."""
        import importlib, sys  # pylint: disable=C0410,C0415
        global AgentService  # pylint: disable=W0603
        package = __name__.rsplit(".", 1)[0]
        for name in ("clothesservice", "garmentservice", "agentservice"):
            importlib.reload(sys.modules[package + "." + name])
        AgentService = sys.modules[package + ".agentservice"].AgentService
        services = sys.modules[package]
        services.GarmentService = sys.modules[package + ".garmentservice"].GarmentService
        services.ClothesService = sys.modules[package + ".clothesservice"].ClothesService
        services.AgentService = AgentService
        return AgentService.command_names()

    @staticmethod
    def _bind(port: int) -> socket.socket:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen(4)
        return server

    @staticmethod
    def _read_lines(conn: socket.socket):
        buf = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    yield line

    @staticmethod
    def _parse(line: bytes) -> dict[str, Any] | None:
        try:
            payload = json.loads(line.decode("utf-8"))
            return payload if isinstance(payload, dict) else None
        except ValueError:
            return None

    @staticmethod
    def _send(conn: socket.socket, response: dict[str, Any]) -> None:
        conn.sendall((json.dumps(response) + "\n").encode("utf-8"))

    # ----------------------------------------------------------------------------------------------------------------
    # Server inside the Blender UI

    @staticmethod
    def is_running() -> bool:
        return RemoteControlService._server is not None

    @staticmethod
    def get_port() -> int | None:
        return RemoteControlService._port

    @staticmethod
    def start(port: int = DEFAULT_PORT) -> None:
        """Start serving on 127.0.0.1:port. Requests run on the main thread via a timer."""
        if RemoteControlService.is_running():
            return
        RemoteControlService._stop.clear()
        server = RemoteControlService._bind(port)
        server.settimeout(0.5)
        RemoteControlService._server = server
        RemoteControlService._port = port
        RemoteControlService._thread = threading.Thread(target=RemoteControlService._accept_loop, daemon=True)
        RemoteControlService._thread.start()
        if not bpy.app.timers.is_registered(RemoteControlService._process_queue):
            bpy.app.timers.register(RemoteControlService._process_queue, first_interval=_POLL_INTERVAL, persistent=True)
        _LOG.info("Remote control listening on port", port)

    @staticmethod
    def stop() -> None:
        RemoteControlService._stop.set()
        server = RemoteControlService._server
        RemoteControlService._server = None
        RemoteControlService._port = None
        if server is not None:
            server.close()
        if bpy.app.timers.is_registered(RemoteControlService._process_queue):
            bpy.app.timers.unregister(RemoteControlService._process_queue)
        _LOG.info("Remote control stopped")

    @staticmethod
    def _accept_loop() -> None:
        while not RemoteControlService._stop.is_set():
            server = RemoteControlService._server
            if server is None:
                return
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=RemoteControlService._serve_connection, args=(conn,), daemon=True).start()

    @staticmethod
    def _serve_connection(conn: socket.socket) -> None:
        with conn:
            try:
                for line in RemoteControlService._read_lines(conn):
                    payload = RemoteControlService._parse(line)
                    if payload is None:
                        RemoteControlService._send(conn, {"id": None, "ok": False, "error": "Invalid JSON request"})
                        continue
                    request = _Request(payload)
                    RemoteControlService._queue.put(request)
                    request.done.wait()
                    RemoteControlService._send(conn, _response_or_error(request.response))
            except OSError:
                pass

    @staticmethod
    def _process_queue() -> float | None:
        while True:
            try:
                request = RemoteControlService._queue.get_nowait()
            except queue.Empty:
                break
            request.response = RemoteControlService.handle(request.payload)
            request.done.set()
        return _POLL_INTERVAL if RemoteControlService.is_running() else None

    # ----------------------------------------------------------------------------------------------------------------
    # Headless server

    @staticmethod
    def serve_blocking(port: int = DEFAULT_PORT, parent_pid: int | None = None) -> None:
        """Serve on the main thread until a "shutdown" request; for "blender -b" where timers never fire.
        With parent_pid, also stop once that process is gone, so a spawned Blender never outlives its client."""
        server = RemoteControlService._bind(port)
        server.settimeout(1.0)
        print("MPFB remote control: listening on 127.0.0.1:%d" % port, flush=True)
        try:
            while True:
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    if parent_pid and not _process_alive(parent_pid):
                        return
                    continue
                conn.settimeout(None)
                with conn:
                    try:
                        for line in RemoteControlService._read_lines(conn):
                            payload = RemoteControlService._parse(line)
                            if payload is None:
                                RemoteControlService._send(conn, {"id": None, "ok": False,
                                                                  "error": "Invalid JSON request"})
                                continue
                            if payload.get("command") == "shutdown":
                                RemoteControlService._send(conn, {"id": payload.get("id"), "ok": True,
                                                                  "result": "bye"})
                                return
                            RemoteControlService._send(conn, RemoteControlService.handle(payload))
                    except OSError:
                        pass
        finally:
            server.close()


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _response_or_error(response: dict[str, Any] | None) -> dict[str, Any]:
    return response if response is not None else {"id": None, "ok": False, "error": "No response"}
