"""Fetch explorer API URLs through a real Chromium that has passed Cloudflare's JS challenge.

Blockscout answers plain HTTP clients with 403 + `cf-mitigated: challenge`. A real browser solves the challenge
once (the clearance cookie lives in a persistent profile) and `fetch()` calls made from inside that page are then
ordinary same-origin requests. This module is the whole mechanism; app.py only wraps it in HTTP.

Safety: it fetches ONLY hosts in `allowed_hosts` (empty = nothing) - without that it would be an open proxy.
"""
import asyncio
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Error as PlaywrightError, Page, async_playwright

log = logging.getLogger("browser.fetcher")

CHALLENGE_TITLE = re.compile(r"just a moment|attention required|checking your browser|verify you are human", re.I)
CHALLENGE_DOM = (
    "() => !!document.querySelector('#challenge-form, #challenge-running, #cf-challenge-running, "
    ".cf-browser-verification, iframe[src*=\"challenges.cloudflare.com\"]')"
)
FETCH_JS = """async (url) => {
  const r = await fetch(url, {credentials: 'include', headers: {'accept': 'application/json'}});
  const text = await r.text();
  return {status: r.status, text, ct: r.headers.get('content-type') || '', retry: r.headers.get('retry-after'),
          mitigated: r.headers.get('cf-mitigated') || ''};
}"""
LAUNCH_ARGS = [
    "--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
    "--disable-blink-features=AutomationControlled", "--lang=en-US", "--no-first-run", "--no-default-browser-check",
]
RECYCLE_AFTER = 500  # fetches per page before it is replaced (cookies survive in the context)


class FetchError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


@dataclass
class Result:
    status: int
    text: str
    content_type: str
    retry_after: str | None


def chromium_user_agent(chromium_path: str) -> str | None:
    """A normal Chrome UA with the real version: headless Chromium advertises 'HeadlessChrome', which Cloudflare
    treats as a bot. Returns None if the version can't be read (the browser's own UA is then used)."""
    try:
        out = subprocess.run([chromium_path, "--version"], capture_output=True, text=True, timeout=15).stdout
        version = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
        if version:
            return ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    f"Chrome/{version.group(1)} Safari/537.36")
    except (OSError, subprocess.SubprocessError):
        pass
    return None


class Fetcher:
    def __init__(self, *, profile_dir: str, chromium_path: str, allowed_hosts: set[str], concurrency: int = 2,
                 challenge_timeout: float = 30.0, nav_timeout: float = 30.0):
        self.profile_dir, self.chromium_path = profile_dir, chromium_path
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.challenge_timeout, self.nav_timeout = challenge_timeout, nav_timeout
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._pw = None
        self._ctx: BrowserContext | None = None
        self._pages: dict[str, Page] = {}
        self._uses: dict[str, int] = {}
        self._solved_at: dict[str, float] = {}
        self._origin_locks: dict[str, asyncio.Lock] = {}
        self._ctx_lock = asyncio.Lock()
        self.stats = {"fetches": 0, "challenges_solved": 0, "challenges_failed": 0, "last_error": None}

    # ---------- lifecycle ----------
    async def start(self) -> None:
        await self._ensure_context()

    async def stop(self) -> None:
        try:
            if self._ctx:
                await self._ctx.close()
        finally:
            if self._pw:
                await self._pw.stop()
            self._ctx, self._pw, self._pages = None, None, {}

    async def _ensure_context(self) -> BrowserContext:
        async with self._ctx_lock:
            if self._ctx is not None:
                try:
                    _ = self._ctx.pages  # raises once the browser process has died
                    return self._ctx
                except PlaywrightError:
                    self._ctx, self._pages = None, {}
            if self._pw is None:
                self._pw = await async_playwright().start()
            ua = chromium_user_agent(self.chromium_path)
            self._ctx = await self._pw.chromium.launch_persistent_context(
                self.profile_dir, executable_path=self.chromium_path,
                headless=False,  # the real flag is --headless=new in LAUNCH_ARGS (playwright's own is the old mode)
                args=LAUNCH_ARGS, ignore_default_args=["--enable-automation"], user_agent=ua,
                viewport={"width": 1280, "height": 800}, locale="en-US",
            )
            self._ctx.set_default_navigation_timeout(self.nav_timeout * 1000)
            log.info("chromium started (profile=%s, ua=%s)", self.profile_dir, ua)
            return self._ctx

    # ---------- challenge handling ----------
    async def _challenged(self, page: Page) -> bool:
        try:
            if CHALLENGE_TITLE.search(await page.title()):
                return True
            return bool(await page.evaluate(CHALLENGE_DOM))
        except PlaywrightError:
            return True  # mid-navigation: the challenge page is reloading itself

    async def _wait_clear(self, page: Page) -> bool:
        deadline = time.monotonic() + self.challenge_timeout
        while time.monotonic() < deadline:
            if not await self._challenged(page):
                return True
            await asyncio.sleep(0.4)
        return False

    def _lock(self, origin: str) -> asyncio.Lock:
        return self._origin_locks.setdefault(origin, asyncio.Lock())

    async def _solve_unlocked(self, page: Page, origin: str) -> bool:
        try:
            await page.goto(origin + "/", wait_until="domcontentloaded")
        except PlaywrightError as e:
            log.warning("navigation to %s failed: %s", origin, str(e)[:120])
        ok = await self._wait_clear(page)
        if ok:
            self._solved_at[origin] = time.monotonic()
            self.stats["challenges_solved"] += 1
        else:
            self.stats["challenges_failed"] += 1
        return ok

    async def _solve(self, page: Page, origin: str) -> bool:
        """(Re)load the origin and wait for the challenge to clear. One solver per origin; everyone else waits."""
        started = time.monotonic()
        async with self._lock(origin):
            if self._solved_at.get(origin, 0.0) >= started:
                return True  # another request solved it while we waited for the lock
            return await self._solve_unlocked(page, origin)

    async def _page_for(self, origin: str) -> Page:
        """The warm page for this origin. Creating it and passing the challenge happens under the origin lock,
        so concurrent requests wait for a ready page instead of fetching while it is still navigating."""
        ctx = await self._ensure_context()
        async with self._lock(origin):
            page = self._pages.get(origin)
            if page is not None and (page.is_closed() or self._uses.get(origin, 0) >= RECYCLE_AFTER):
                if not page.is_closed():
                    await page.close()
                page = None
            if page is None:
                page = await ctx.new_page()
                self._pages[origin], self._uses[origin] = page, 0
                await self._solve_unlocked(page, origin)
            return page

    @staticmethod
    def _is_challenge(res: dict) -> bool:
        if res["status"] in (403, 429, 503) and res["mitigated"].lower() == "challenge":
            return True
        return res["status"] in (403, 503) and bool(CHALLENGE_TITLE.search(res["text"][:3000]))

    # ---------- public ----------
    def check(self, url: str) -> tuple[str, str]:
        """Validate the URL; returns (origin, host). Raises FetchError(400/403)."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise FetchError(400, "only absolute http(s) URLs")
        if parsed.hostname.lower() not in self.allowed_hosts:
            raise FetchError(403, f"host not allowed: {parsed.hostname}")
        return f"{parsed.scheme}://{parsed.netloc}", parsed.hostname.lower()

    async def fetch(self, url: str) -> Result:
        origin, _ = self.check(url)
        async with self._sem:
            self.stats["fetches"] += 1
            last = "no attempt"
            for _ in range(3):
                try:
                    page = await self._page_for(origin)
                    self._uses[origin] = self._uses.get(origin, 0) + 1
                    res = await page.evaluate(FETCH_JS, url)
                except PlaywrightError as e:
                    # a reload destroyed the page context (transient) or the page/browser died (drop the page)
                    last = str(e).splitlines()[0][:160]
                    self.stats["last_error"] = last
                    page = self._pages.get(origin)
                    if page is None or page.is_closed() or re.search(r"closed|crash|disconnected", last, re.I):
                        self._pages.pop(origin, None)
                        if page is not None and not page.is_closed():
                            await page.close()
                    else:
                        await asyncio.sleep(0.4)
                    continue
                if self._is_challenge(res):
                    last = "challenge"
                    if not await self._solve(await self._page_for(origin), origin):
                        break
                    continue
                return Result(res["status"], res["text"], res["ct"], res["retry"])
            self.stats["last_error"] = last
            raise FetchError(503, "challenge_unsolved" if last == "challenge" else f"browser_error: {last}")
