"""A local web site that answers like Amazon behind AWS WAF: a JavaScript
challenge (HTTP 202 with window.gokuProps) until the browser has run it and
holds a valid aws-waf-token cookie, then the product page."""

from __future__ import annotations

import collections
import http.server
import threading
import time

PRODUCT = (
    '<html><head><link rel=stylesheet href="/s.css"><script src="/app.js"></script></head><body>'
    '<span id="productTitle">PlayStation Gift Card {path}</span><img src="/img.jpg">'
    '<div id="availability"><span class="a-color-price">Currently unavailable.</span></div></body></html>'
)
CHALLENGE = (
    "<html><head><script>window.gokuProps = {{key: 1}};</script>"
    "<script>setTimeout(function () {{ document.cookie = 'aws-waf-token=' + Date.now() + '; path=/';"
    " location.reload(); }}, {delay});</script></head><body>Please wait</body></html>"
)
CAPTCHA = '<html><script>window.gokuProps = {}</script><script src="/captcha.js"></script></html>'
# A challenge whose script comes from a path the strict filter refuses.
SCRIPT_CHALLENGE = '<html><head><script>window.gokuProps = {key: 1};</script><script src="/waf.js"></script></head></html>'
WAF_JS = "document.cookie = 'aws-waf-token=' + Date.now() + '; path=/'; location.reload();"


class FakeAmazon:
    def __init__(self, challenge_delay_ms: int = 300):
        self.hits: collections.Counter[str] = collections.Counter()
        self.valid_after = 0.0  # tokens (ms timestamps) from before this are expired
        self.captcha = False  # answer every page with a CAPTCHA
        self.script_challenge = False  # the challenge needs /waf.js
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                site.hits[self.path] += 1
                if self.path == "/waf.js":
                    return self._send(200, WAF_JS, "application/javascript")
                if not self.path.startswith("/dp/"):
                    return self._send(200, "x")
                if site.captcha:
                    return self._send(405, CAPTCHA)
                if not site._token_ok(self.headers.get("Cookie") or ""):
                    if site.script_challenge:
                        return self._send(202, SCRIPT_CHALLENGE)
                    return self._send(202, CHALLENGE.format(delay=challenge_delay_ms))
                return self._send(200, PRODUCT.format(path=self.path))

            def _send(self, code: int, body: str, kind: str = "text/html; charset=utf-8"):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _token_ok(self, cookie: str) -> bool:
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "aws-waf-token":
                try:
                    return float(value) / 1000 >= self.valid_after
                except ValueError:
                    return False
        return False

    def expire_tokens(self) -> None:
        self.valid_after = time.time() + 0.001

    def page_hits(self) -> int:
        return sum(n for path, n in self.hits.items() if path.startswith("/dp/"))

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
