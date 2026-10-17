import glob
import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

CHALLENGE_HTML = (
    "<html><head><title>Just a moment...</title></head><body><div id='challenge-running'>Checking your browser"
    "</div><script>setTimeout(function(){document.cookie='cf_clearance=ok; path=/; max-age=3600';"
    "location.reload()}, 700)</script></body></html>"
)


def find_chromium() -> str | None:
    candidates = [os.environ.get("CHROMIUM_PATH"), shutil.which("chromium"), shutil.which("google-chrome"),
                  *glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")]
    return next((c for c in candidates if c and os.path.exists(c)), None)


CHROMIUM = find_chromium()
needs_chromium = pytest.mark.skipif(CHROMIUM is None, reason="no Chromium available (set CHROMIUM_PATH)")


class FakeExplorer:
    """A Blockscout behind a Cloudflare-style JS challenge. Requests without the clearance cookie get
    403 + `cf-mitigated: challenge` + the challenge page; the page sets the cookie via JS and reloads."""

    def __init__(self):
        self.never_solve = False
        self.challenges = 0
        self.api_hits = 0
        self.cookies_seen: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401
                pass

            def _send(self, status, body, ctype="application/json", extra=None):
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                cookie = self.headers.get("Cookie", "")
                outer.cookies_seen.append(cookie)
                cleared = "cf_clearance=ok" in cookie and not outer.never_solve
                if not cleared:
                    outer.challenges += 1
                    return self._send(403, CHALLENGE_HTML, "text/html; charset=utf-8", {"cf-mitigated": "challenge"})
                if self.path == "/":
                    return self._send(200, "<html><title>Blockscout</title>ok</html>", "text/html")
                outer.api_hits += 1
                if self.path.startswith("/api/v2/rate-limited"):
                    return self._send(429, '{"message":"slow down"}', extra={"Retry-After": "7"})
                if self.path.startswith("/api/v2/missing"):
                    return self._send(404, '{"message":"Not found"}')
                if self.path.startswith("/api/v2/tokens/") and self.path.endswith("/holders"):
                    items = [{"address": {"hash": f"0x{i:040x}", "is_contract": False}, "value": str(10**18 * (50 - i))}
                             for i in range(50)]
                    return self._send(200, json.dumps({"items": items, "next_page_params": None}))
                return self._send(200, json.dumps({"path": self.path, "holders_count": "108"}))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.host = "127.0.0.1"
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def explorer():
    e = FakeExplorer()
    yield e
    e.close()
