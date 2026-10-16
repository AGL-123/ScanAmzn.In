"""Reading Amazon pages through a real Chromium browser on the phone.

Amazon (AWS WAF) answers some requests with a JavaScript challenge: a short
page whose script checks that it runs in a real browser, then reloads the
page. The bot's own HTTP requests can't run it, so they wait and retry; a
browser runs it the way it does for every visitor and gets the page.

This module starts Chromium without a window (headless) and talks to it over
the DevTools protocol on a private pipe - no network port that other apps on
the phone could use. Nothing is disguised: the browser keeps its own identity
(it says it is headless Chrome), and a CAPTCHA (a picture puzzle) is never
solved - the page is handed back as a bot check and the bot waits.

To spare data and battery, the browser loads only Amazon's HTML and the
challenge's own script (from awswaf.com): no images, styles, fonts, ads or
Amazon's other scripts. Once a page of the site is open, further pages are
read with fetch() from it, so nothing runs for them at all.

Termux: ``pkg install x11-repo && pkg install chromium`` (``ps5 browser``).
"""

from __future__ import annotations

import json
import logging
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# Program names of Chromium builds. Termux's chromium package (x11-repo) has
# both: the headless shell needs about half the memory and processes of the
# full browser (Android 12+ kills apps whose child processes exceed 32 across
# all apps), and it is what reports of headless browsers getting past Amazon's
# challenge used; the full Chromium is the second choice.
SHELL_NAMES = ("headless_shell", "chrome-headless-shell")
FULL_NAMES = ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable", "chrome")
BROWSER_NAMES = SHELL_NAMES + FULL_NAMES
# How long a page may take, including Amazon's challenge (it usually takes 1-5 s).
CHALLENGE_SECONDS = 30.0
START_SECONDS = 60.0

# Runs in the child process just before Chromium: Chromium reads DevTools
# commands from fd 3 and writes replies to fd 4 (--remote-debugging-pipe).
# Done here rather than with subprocess's preexec_fn, which isn't safe with
# threads; the fds go out of the way first in case one of them is 3 or 4.
_LAUNCHER = (
    "import fcntl, os, sys\n"
    "src = [int(a) for a in sys.argv[1:3]]\n"
    "high = [fcntl.fcntl(fd, fcntl.F_DUPFD, 10) for fd in src]\n"
    "for fd in src: os.close(fd)\n"
    "os.dup2(high[0], 3)\n"
    "os.dup2(high[1], 4)\n"
    "for fd in high: os.close(fd)\n"
    "os.execv(sys.argv[3], sys.argv[3:])\n"
)

# Where the open page stands: still loading, our old page (before a
# navigation started), Amazon's challenge, or a CAPTCHA the bot won't solve.
_STATE_JS = """JSON.stringify({
  ready: document.readyState,
  old: !!window.__scanamznOld,
  challenge: !!window.gokuProps,
  captcha: !!(window.AwsWafCaptcha || document.querySelector(
    'script[src*="captcha.js"], form[action*="validateCaptcha"], #captcha-container')),
})"""
_DOCUMENT_JS = """JSON.stringify({
  status: (performance.getEntriesByType('navigation')[0] || {}).responseStatus || 0,
  url: location.href,
  text: document.documentElement.outerHTML,
})"""
_FETCH_JS = """(async () => {
  const stop = new AbortController();
  const timer = setTimeout(() => stop.abort(), %d);
  try {
    const r = await fetch(%s, {credentials: 'include', redirect: 'follow', signal: stop.signal,
      headers: {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'}});
    return JSON.stringify({status: r.status, url: r.url, text: await r.text()});
  } finally { clearTimeout(timer); }
})()"""


class BrowserError(Exception):
    pass


def find_browser(path: str = "", names: tuple[str, ...] = BROWSER_NAMES) -> str | None:
    """The Chromium program to use: the configured one, else the first of
    ``names`` on PATH."""
    if path:
        found = shutil.which(path) or (path if os.access(path, os.X_OK) else None)
        return found
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def on_android() -> bool:
    return sys.platform == "android" or "ANDROID_ROOT" in os.environ or "com.termux" in os.environ.get("PREFIX", "")


@dataclass
class Response:
    url: str
    status_code: int
    text: str


class _Cookies:
    """The cookie interface the fetcher uses on every session type."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def set(self, name: str, value: str, domain: str = "", path: str = "/") -> None:
        self.items.append({"name": name, "value": value, "domain": domain, "path": path, "secure": True})


class _Pipe:
    """The DevTools protocol over --remote-debugging-pipe: JSON messages, each
    ended by a NUL byte."""

    def __init__(self, proc: subprocess.Popen, out_fd: int, in_fd: int):
        self.proc, self.out_fd, self.in_fd = proc, out_fd, in_fd
        self.handlers: dict[str, Callable[[dict, str | None], None]] = {}
        self.closed = False
        self._buf = bytearray()
        self._scanned = 0
        self._next_id = 0
        self._waiting: set[int] = set()
        self._results: dict[int, dict] = {}

    def send(self, method: str, params: dict | None = None, session: str | None = None) -> int:
        if self.closed:
            raise BrowserError("the browser has quit")
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        data = json.dumps(message).encode() + b"\0"
        try:
            while data:
                data = data[os.write(self.out_fd, data):]
        except OSError as exc:
            self.closed = True
            raise BrowserError(f"the browser has quit ({exc.strerror or exc})") from exc
        return self._next_id

    def call(self, method: str, params: dict | None = None, session: str | None = None,
             timeout: float = 30.0) -> dict:
        msg_id = self.send(method, params, session)
        self._waiting.add(msg_id)
        try:
            deadline = time.monotonic() + timeout
            while msg_id not in self._results:
                left = deadline - time.monotonic()
                if left <= 0:
                    self.closed = True  # hung: start a new one rather than wait on it again
                    raise BrowserError(f"the browser did not answer {method} within {timeout:.0f} s")
                self._read(left)
            reply = self._results.pop(msg_id)
        finally:
            self._waiting.discard(msg_id)
        if "error" in reply:
            error = reply["error"]
            raise BrowserError(f"{method}: {error.get('message', error) if isinstance(error, dict) else error}")
        return reply.get("result", {})

    def _read(self, timeout: float) -> None:
        ready, _, _ = select.select([self.in_fd], [], [], timeout)
        if not ready:
            return
        try:
            chunk = os.read(self.in_fd, 1 << 20)
        except OSError as exc:
            chunk = b""
            log.debug("reading from the browser failed: %s", exc)
        if not chunk:
            self.closed = True
            raise BrowserError("the browser has quit")
        self._buf += chunk
        while True:
            end = self._buf.find(b"\0", self._scanned)
            if end < 0:
                self._scanned = len(self._buf)
                return
            raw = bytes(self._buf[:end])
            del self._buf[:end + 1]
            self._scanned = 0
            try:
                self._dispatch(json.loads(raw))
            except ValueError:
                log.debug("unreadable message from the browser: %r", raw[:200])

    def _dispatch(self, message: dict) -> None:
        if "id" in message:
            if message["id"] in self._waiting:
                self._results[message["id"]] = message
            return  # replies to fire-and-forget commands
        handler = self.handlers.get(message.get("method", ""))
        if handler is not None:
            handler(message.get("params") or {}, message.get("sessionId"))


class BrowserSession:
    """A requests.Session look-alike (get/close, headers, cookies) that loads
    pages through Chromium. The browser starts on the first get()."""

    def __init__(self, binary: str, domain: str, user_data_dir: str | Path, proxy: str | None = None,
                 log_path: str | Path | None = None, wait: Callable[[float], Any] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.binary, self.domain = binary, domain
        self.site = (urlsplit("//" + domain).hostname or domain).removeprefix("www.")
        self.user_data_dir = Path(user_data_dir)
        self.proxy = proxy
        self.log_path = Path(log_path) if log_path else None
        self.headers: dict[str, str] = {}  # the browser sends its own
        self.cookies = _Cookies()
        self.wait, self.clock = wait, clock
        self._pipe: _Pipe | None = None
        self._session: str | None = None
        self._on_site = False  # a page of the site is open, so fetch() can read the others
        self.blocked_requests = 0

    # -- process ------------------------------------------------------------------
    def _args(self) -> list[str]:
        shell = "headless" in Path(self.binary).name  # the headless shell is always headless
        args = [
            "--remote-debugging-pipe", f"--user-data-dir={self.user_data_dir}",
            "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--disable-dev-shm-usage",
            "--disable-extensions", "--disable-sync", "--disable-default-apps", "--mute-audio",
            "--disable-background-networking", "--disable-component-update", "--disable-domain-reliability",
            "--disable-client-side-phishing-detection", "--no-pings", "--disk-cache-size=20000000",
            "--blink-settings=imagesEnabled=false", "--renderer-process-limit=1",
            "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication,"
            "SpareRendererForSitePerProcess",
            "--lang=en-IN", "--window-size=1366,768",
        ]
        if not shell:
            args.insert(0, "--headless=new")
        # Chromium's sandbox needs kernel features apps don't get on Android
        # (Termux's build turns it off anyway), and it refuses to run as root
        # with it. Without the sandbox, the zygote process isn't needed either.
        if on_android() or (hasattr(os, "geteuid") and os.geteuid() == 0):
            args[:0] = ["--no-sandbox", "--no-zygote"]
        if self.proxy:
            args.append(f"--proxy-server={self.proxy}")
        return args + ["about:blank"]

    def _start(self) -> None:
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        _stop_stale_browser(self.user_data_dir)
        to_chrome_r, to_chrome_w = os.pipe()
        from_chrome_r, from_chrome_w = os.pipe()
        out = subprocess.DEVNULL
        if self.log_path is not None:
            try:
                out = open(self.log_path, "wb")  # noqa: SIM115 - handed to the child
            except OSError:
                out = subprocess.DEVNULL
        env = dict(os.environ, LANG=os.environ.get("LANG") or "en_IN.UTF-8")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c", _LAUNCHER, str(to_chrome_r), str(from_chrome_w), self.binary, *self._args()],
                stdin=subprocess.DEVNULL, stdout=out, stderr=out, pass_fds=(to_chrome_r, from_chrome_w), env=env,
            )
        except OSError as exc:
            for fd in (to_chrome_r, to_chrome_w, from_chrome_r, from_chrome_w):
                os.close(fd)
            raise BrowserError(f"could not start the browser ({exc})") from exc
        finally:
            if out is not subprocess.DEVNULL:
                out.close()
        os.close(to_chrome_r)
        os.close(from_chrome_w)
        pipe = self._pipe = _Pipe(proc, to_chrome_w, from_chrome_r)
        pipe.handlers["Fetch.requestPaused"] = self._on_request
        try:
            target = pipe.call("Target.createTarget", {"url": "about:blank"}, timeout=START_SECONDS)
            self._session = pipe.call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})[
                "sessionId"]
            pipe.call("Page.enable", session=self._session)
            # Every request passes _on_request first.
            pipe.call("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
                      session=self._session)
            if self.cookies.items:
                pipe.call("Storage.setCookies", {"cookies": self.cookies.items})
        except BrowserError as exc:
            self.close()
            raise BrowserError(f"the browser did not start: {exc}{self._log_tail()}") from exc
        log.info("browser started (%s, pid %d)", Path(self.binary).name, proc.pid)

    def _log_tail(self) -> str:
        if self.log_path is None:
            return ""
        try:
            lines = [ln for ln in self.log_path.read_text(errors="replace").splitlines() if ln.strip()]
        except OSError:
            return ""
        return f" - its last words: {lines[-1][:200]}" if lines else ""

    def close(self) -> None:
        pipe, self._pipe = self._pipe, None
        self._session, self._on_site = None, False
        if pipe is None:
            return
        if not pipe.closed:
            try:
                pipe.send("Browser.close")
            except BrowserError:
                pass
        try:
            pipe.proc.wait(5)
        except subprocess.TimeoutExpired:
            pipe.proc.kill()
            try:
                pipe.proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
        for fd in (pipe.out_fd, pipe.in_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    # -- requests -----------------------------------------------------------------
    def _on_request(self, params: dict, session: str | None) -> None:
        """Let through the site's pages and the challenge's script; refuse
        images, styles, fonts, ads and other scripts (data and battery)."""
        url = (params.get("request") or {}).get("url", "")
        kind = params.get("resourceType", "")
        host = (urlsplit(url).hostname or "").lower()
        ours = host == self.site or host.endswith("." + self.site)
        allow = host.endswith(".awswaf.com") or (ours and kind in ("Document", "XHR", "Fetch"))
        if allow:
            method, extra = "Fetch.continueRequest", {}
        else:
            method, extra = "Fetch.failRequest", {"errorReason": "BlockedByClient"}
            self.blocked_requests += 1
        try:
            self._pipe.send(method, {"requestId": params.get("requestId"), **extra}, session)
        except (BrowserError, AttributeError):
            pass

    def _evaluate(self, expression: str, timeout: float, await_promise: bool = False) -> Any:
        result = self._pipe.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
            session=self._session, timeout=timeout,
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = (details.get("exception") or {}).get("description") or details.get("text") or "script error"
            raise BrowserError(text.splitlines()[0])
        return (result.get("result") or {}).get("value")

    def get(self, url: str, timeout: float = 20.0, allow_redirects: bool = True) -> Response:
        if self._pipe is None or self._pipe.closed:
            self.close()
            self._start()
        try:
            if self._on_site:
                page = self._fetch(url, timeout)
                if page.status_code not in (202, 405) or _is_captcha(page.text):
                    return page
                log.info("browser: Amazon sent its browser check; loading the page so the browser runs it")
            return self._navigate(url, timeout)
        except BrowserError:
            if self._pipe is not None and self._pipe.closed:
                self.close()
            raise

    def _fetch(self, url: str, timeout: float) -> Response:
        value = self._evaluate(_FETCH_JS % (int(timeout * 1000), json.dumps(url)), timeout + 10, await_promise=True)
        data = json.loads(value)
        return Response(url=data.get("url") or url, status_code=int(data["status"]), text=data["text"])

    def _navigate(self, url: str, timeout: float) -> Response:
        """Open the page in the tab, as a visitor would. If Amazon answers with
        its challenge, the browser runs it and reloads by itself; wait for the
        real page (or a CAPTCHA, which is left alone)."""
        try:
            self._evaluate("window.__scanamznOld = 1", 10)
        except BrowserError:
            pass  # e.g. a page that's still loading; the readyState check covers it
        nav = self._pipe.call("Page.navigate", {"url": url}, session=self._session, timeout=timeout + 10)
        if nav.get("errorText"):
            raise BrowserError(f"could not load the page ({nav['errorText']})")
        deadline = self.clock() + timeout + CHALLENGE_SECONDS
        challenged = passed = captcha = False
        while True:
            try:
                state = json.loads(self._evaluate(_STATE_JS, 10))
            except BrowserError as exc:  # the page is being replaced right now
                if self._pipe is None or self._pipe.closed:
                    raise
                log.debug("browser: page not ready (%s)", exc)
                state = None
            if state and not state["old"] and state["ready"] == "complete":
                if state["captcha"]:
                    log.warning("browser: Amazon shows a CAPTCHA; the bot does not solve those")
                    captcha = True
                    break
                if not state["challenge"]:
                    self._on_site = passed = True
                    if challenged:
                        log.info("browser: passed Amazon's browser check")
                    break
                challenged = True
            if self.clock() >= deadline:
                log.warning("browser: Amazon's browser check did not finish within %.0f s", timeout + CHALLENGE_SECONDS)
                break
            if self.wait(0.4):  # the bot is stopping
                raise BrowserError("stopped")
        data = json.loads(self._evaluate(_DOCUMENT_JS, 20))
        status = int(data.get("status") or 0)
        # Judge by the page itself (the status can be the challenge's 202 even
        # after the browser got past it, or 200 on Amazon's own CAPTCHA page).
        if passed and status in (0, 202, 405):
            status = 200
        elif not passed:
            status = 405 if captcha else 202
        return Response(url=data.get("url") or url, status_code=status, text=data.get("text") or "")


def _is_captcha(text: str) -> bool:
    lowered = text.lower()
    return "captcha.js" in lowered or "validatecaptcha" in lowered


def _stop_stale_browser(user_data_dir: Path) -> None:
    """A browser left over from a bot process that was killed holds the
    profile, and a new one would just hand over to it and quit."""
    lock = user_data_dir / "SingletonLock"
    try:
        target = os.readlink(lock)
    except OSError:
        return
    try:
        pid = int(target.rsplit("-", 1)[-1])
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (ValueError, OSError):
        return  # gone; Chromium cleans up its own stale lock
    if f"--user-data-dir={user_data_dir}".encode() not in cmdline:
        return  # the pid was reused by something else
    log.info("stopping a browser left over from an earlier run (pid %d)", pid)
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            time.sleep(0.1)
            os.kill(pid, 0)
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
