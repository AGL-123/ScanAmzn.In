"""Requests through the curl program, which Termux always has.

curl speaks HTTP/2 and has its own TLS handshake, so Amazon sees a different
client than Python's ``requests``; `ps5 probe` measures which one gets through
on the phone's network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class CurlError(Exception):
    pass


@dataclass
class Response:
    url: str
    status_code: int
    text: str


class _Cookies:
    """Cookies in curl's cookie-jar file (the Netscape format)."""

    def __init__(self, jar: Path):
        self.jar = jar

    def set(self, name: str, value: str, domain: str = "", path: str = "/") -> None:
        subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        with open(self.jar, "a", encoding="ascii") as fh:
            fh.write(f"{domain}\t{subdomains}\t{path}\tTRUE\t0\t{name}\t{value}\n")


class CurlSession:
    """A requests.Session look-alike (get/close, headers, cookies)."""

    def __init__(self, binary: str = "curl", http2: bool = True, proxy: str | None = None):
        self.binary, self.http2, self.proxy = binary, http2, proxy
        self.headers: dict[str, str] = {}
        self._dir = Path(tempfile.mkdtemp(prefix="scanamzn-curl-"))
        jar = self._dir / "cookies.txt"
        jar.write_text("# Netscape HTTP Cookie File\n", encoding="ascii")
        self.cookies = _Cookies(jar)

    def get(self, url: str, timeout: float = 20.0, allow_redirects: bool = True) -> Response:
        body = self._dir / "body"
        cmd = [
            self.binary, "--silent", "--show-error", "--compressed", "--max-time", f"{timeout:.0f}",
            "--output", str(body), "--write-out", "%{http_code} %{url_effective}",
            "--cookie", str(self.cookies.jar), "--cookie-jar", str(self.cookies.jar),
            "--http2" if self.http2 else "--http1.1",
        ]
        if allow_redirects:
            cmd += ["--location", "--max-redirs", "10"]
        if self.proxy:
            cmd += ["--proxy", self.proxy]
        for name, value in self.headers.items():
            cmd += ["--header", f"{name}: {value}"]
        cmd.append(url)
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=timeout + 15, check=False)
        except subprocess.TimeoutExpired as exc:
            raise CurlError(f"curl did not finish within {timeout + 15:.0f} s") from exc
        except OSError as exc:
            raise CurlError(f"could not run curl ({exc})") from exc
        if done.returncode != 0:
            message = done.stderr.decode(errors="replace").strip().splitlines()
            raise CurlError(f"curl exit {done.returncode}: {message[-1] if message else 'no message'}")
        code, _, effective = done.stdout.decode(errors="replace").partition(" ")
        try:
            text = body.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            text = ""
        finally:
            try:
                os.unlink(body)
            except OSError:
                pass
        return Response(url=effective or url, status_code=int(code or 0), text=text)

    def close(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)
