import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockAPIHandler(BaseHTTPRequestHandler):
    def _handle_request(self) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        auth_header = self.headers.get("Authorization", "<missing>")
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")

        payload: Any
        try:
            payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            payload = {"_raw_body": raw_body}

        print(f"[{timestamp}] {self.command} {self.path}")
        print(f"Authorization: {auth_header}")
        print(json.dumps(payload, ensure_ascii=True, indent=4))
        print("-" * 60, flush=True)

        response = json.dumps({"status": "ok"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def do_POST(self) -> None:  # noqa: N802
        self._handle_request()

    def do_GET(self) -> None:  # noqa: N802
        self._handle_request()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle_request()

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle_request()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle_request()

    def log_message(self, format: str, *args: Any) -> None:
        # Silence default HTTP server access logs.
        return


def main() -> None:
    host = "0.0.0.0"
    port = 8080
    print(f"Listening on http://{host}:{port} ...")
    server = ThreadingHTTPServer((host, port), MockAPIHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
