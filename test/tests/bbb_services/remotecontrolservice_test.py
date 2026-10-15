import json, socket, threading, time
from .. import RemoteControlService


def test_remotecontrolservice_exists():
    """RemoteControlService"""
    assert RemoteControlService is not None, "RemoteControlService can be imported"


def test_handle_reports_errors():
    """RemoteControlService.handle() turns exceptions into error responses"""
    response = RemoteControlService.handle({"id": 7, "command": "no_such_command"})
    assert response["id"] == 7
    assert response["ok"] is False
    assert "Unknown command" in response["error"]


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_blocking_server_round_trip():
    """RemoteControlService.serve_blocking() answers ping and stops on shutdown"""
    port = _free_port()
    thread = threading.Thread(target=RemoteControlService.serve_blocking, args=(port,), daemon=True)
    thread.start()
    for _ in range(50):
        try:
            conn = socket.create_connection(("127.0.0.1", port), timeout=5)
            break
        except OSError:
            time.sleep(0.1)
    with conn:
        reader = conn.makefile("r")
        conn.sendall(b'{"id": 1, "command": "ping"}\n')
        ping = json.loads(reader.readline())
        assert ping["ok"] and "create_human" in ping["result"]["commands"]
        conn.sendall(b'not json\n')
        assert json.loads(reader.readline())["ok"] is False
        conn.sendall(b'{"id": 2, "command": "shutdown"}\n')
        assert json.loads(reader.readline())["result"] == "bye"
    thread.join(timeout=5)
    assert not thread.is_alive()
