"""Desktop wrapper helper tests (no GUI required)."""
from __future__ import annotations

import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from scripts import desktop_app


class _Handler(BaseHTTPRequestHandler):
    payload = {"app": "oversell", "version": "0.2.0"}

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/health"):
            body = json.dumps(self.payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):  # noqa: D401
        pass


class DesktopHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = int(cls.server.server_address[1])
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_find_free_port_returns_bindable_port(self) -> None:
        port = desktop_app.find_free_port()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))

    def test_find_free_port_skips_occupied_preferred(self) -> None:
        port = desktop_app.find_free_port(preferred=self.port)
        self.assertNotEqual(port, self.port)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))

    def test_wait_for_http_returns_true_when_server_is_up(self) -> None:
        url = f"http://127.0.0.1:{self.port}/api/health"
        self.assertTrue(desktop_app.wait_for_http(url, timeout=5.0))

    def test_wait_for_http_returns_false_when_unreachable(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
        probe.close()
        self.assertFalse(
            desktop_app.wait_for_http(f"http://127.0.0.1:{port}/", timeout=1.2, interval=0.2)
        )

    def test_is_compatible_backend_accepts_matching_health(self) -> None:
        url = f"http://127.0.0.1:{self.port}"
        self.assertTrue(desktop_app.is_compatible_backend(url))

    def test_is_compatible_backend_rejects_wrong_app(self) -> None:
        _Handler.payload = {"app": "other", "version": "0.2.0"}
        try:
            url = f"http://127.0.0.1:{self.port}"
            with self.assertRaises(RuntimeError):
                desktop_app.is_compatible_backend(url)
        finally:
            _Handler.payload = {"app": "oversell", "version": "0.2.0"}

    def test_is_compatible_backend_returns_false_when_unreachable(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
        probe.close()
        self.assertFalse(desktop_app.is_compatible_backend(f"http://127.0.0.1:{port}"))

    def test_build_backend_command_contains_port(self) -> None:
        command = desktop_app.build_backend_command(8123, python="python")
        self.assertIn("--port", command)
        self.assertIn("8123", command)
        self.assertIn("backend.app:app", command)


if __name__ == "__main__":
    unittest.main()
