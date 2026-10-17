"""Chromium sidecar. Internal only (docker network, no published port).

    POST /internal/fetch   {"url": "https://robinhoodchain.blockscout.com/api/v2/..."}  -> upstream body as-is
    GET  /internal/fetch?url=...                                                          (same, for curl)
    GET  /health                                                                          browser + challenge state

The response carries the UPSTREAM status (404, 429 + Retry-After, ...) and `X-Upstream-Status`; sidecar-side
problems use 400 (bad URL), 401, 403 (host not allowed), 503 (challenge_unsolved / browser_error).

Env: ALLOWED_HOSTS (comma list, REQUIRED), FETCH_TOKEN (optional bearer), CHROMIUM_PATH, PROFILE_DIR,
     CONCURRENCY (2), CHALLENGE_TIMEOUT (30), NAV_TIMEOUT (30).
"""
import hmac
import logging
import os
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from fetcher import FetchError, Fetcher

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("browser")


def find_chromium() -> str:
    for candidate in (os.environ.get("CHROMIUM_PATH"), shutil.which("chromium"), shutil.which("chromium-browser"),
                      shutil.which("google-chrome")):
        if candidate and os.path.exists(candidate):
            return candidate
    raise RuntimeError("no Chromium found: set CHROMIUM_PATH")


def build_fetcher() -> Fetcher:
    hosts = {h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}
    if not hosts:
        log.warning("ALLOWED_HOSTS is empty: every fetch will be refused")
    return Fetcher(
        profile_dir=os.environ.get("PROFILE_DIR", "/data/profile"), chromium_path=find_chromium(),
        allowed_hosts=hosts, concurrency=int(os.environ.get("CONCURRENCY", "2")),
        challenge_timeout=float(os.environ.get("CHALLENGE_TIMEOUT", "30")), nav_timeout=float(os.environ.get("NAV_TIMEOUT", "30")),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if getattr(app.state, "fetcher", None) is None:
        app.state.fetcher = build_fetcher()
    await app.state.fetcher.start()
    yield
    await app.state.fetcher.stop()


app = FastAPI(title="Chainfolio browser sidecar", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class FetchIn(BaseModel):
    url: str


def authorize(authorization: str | None) -> None:
    token = os.environ.get("FETCH_TOKEN", "")
    if token and not hmac.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(401, "unauthorized")


async def do_fetch(request: Request, url: str) -> Response:
    fetcher: Fetcher = request.app.state.fetcher
    try:
        res = await fetcher.fetch(url)
    except FetchError as e:
        return JSONResponse({"error": e.detail}, status_code=e.status)
    headers = {"X-Upstream-Status": str(res.status)}
    if res.retry_after:
        headers["Retry-After"] = res.retry_after
    status = res.status if 200 <= res.status <= 599 else 502
    return Response(res.text, status_code=status, media_type=res.content_type or "application/json", headers=headers)


@app.post("/internal/fetch")
async def fetch_post(body: FetchIn, request: Request, authorization: str | None = Header(default=None)):
    authorize(authorization)
    return await do_fetch(request, body.url)


@app.get("/internal/fetch")
async def fetch_get(url: str, request: Request, authorization: str | None = Header(default=None)):
    authorize(authorization)
    return await do_fetch(request, url)


@app.get("/health")
async def health(request: Request):
    f: Fetcher = request.app.state.fetcher
    return {"ok": True, "allowed_hosts": sorted(f.allowed_hosts), **f.stats}
