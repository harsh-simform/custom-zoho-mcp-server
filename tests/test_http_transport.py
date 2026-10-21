"""The real-HTTP adapter against a throwaway local server (loopback only, never the internet), and the
rule that nothing else may import the HTTP library."""
import ast
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from zoho_axi.core.http_transport import RequestsTransport
from zoho_axi.core.transport import PayloadTooLarge, TransportError

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self):
            length = int(self.headers.get("Content-Length") or 0)
            seen.append((self.command, self.path, dict(self.headers), self.rfile.read(length)))
            if self.path.startswith("/missing"):
                status, body = 404, b'{"error":"gone"}'
            elif self.path.startswith("/big"):
                status, body = 200, b"x" * 200_000
            else:
                status, body = 200, b'{"ok": true}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _reply

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
    httpd.shutdown()
    httpd.server_close()


def test_get_reports_status_body_and_content_type_and_sends_headers_and_query(server):
    base, seen = server
    response = RequestsTransport().send("GET", f"{base}/things", {"Authorization": "Zoho-oauthtoken abc"}, {"range": "5"}, 5)

    assert (response.status, response.body, response.content_type) == (200, b'{"ok": true}', "application/json")
    method, path, headers, _ = seen[0]
    assert (method, path) == ("GET", "/things?range=5")
    assert headers["Authorization"] == "Zoho-oauthtoken abc"


def test_post_sends_the_data_as_a_form_body(server):
    base, seen = server
    RequestsTransport().send("POST", f"{base}/token", {}, None, 5, data={"grant_type": "refresh_token"})
    method, path, headers, body = seen[0]
    assert (method, path, body) == ("POST", "/token", b"grant_type=refresh_token")
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"


def test_http_error_statuses_are_returned_not_raised(server):
    base, _ = server
    assert RequestsTransport().send("GET", f"{base}/missing", {}, None, 5).status == 404


def test_a_body_over_max_bytes_stops_the_read_with_payload_too_large(server):
    base, _ = server
    with pytest.raises(PayloadTooLarge):
        RequestsTransport().send("GET", f"{base}/big", {}, None, 5, max_bytes=1000)
    assert len(RequestsTransport().send("GET", f"{base}/big", {}, None, 5, max_bytes=300_000).body) == 200_000


def test_connection_failures_become_transport_errors_that_do_not_echo_the_url(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    with pytest.raises(TransportError) as exc:
        RequestsTransport().send("GET", "http://127.0.0.1:1/secret-path?sig=SIGNED", {}, None, 2)
    assert isinstance(exc.value, OSError)
    assert "SIGNED" not in str(exc.value)


def _imports_requests(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "requests" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "requests":
            return True
    return False


def test_only_the_real_http_adapter_imports_the_http_library():
    offenders = [
        str(p.relative_to(REPO))
        for p in sorted((REPO / "zoho_axi").rglob("*.py"))
        if _imports_requests(p) and p.name != "http_transport.py"
    ]
    assert offenders == []
    assert _imports_requests(REPO / "zoho_axi" / "core" / "http_transport.py")  # the scan works
