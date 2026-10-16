import time
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import alerts, auth, copy, health, portfolio, safety, swap, telegram
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging

settings = get_settings()
setup_logging(settings.log_level)

app = FastAPI(
    title="Chainfolio API",
    version="0.1.0",
    description="Portfolio, token safety and copy-trading for Robinhood Chain (4663).",
)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)
log = get_logger("http")


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Request id on every log line + response header; unhandled errors logged with traceback."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    structlog.contextvars.bind_contextvars(request_id=rid)
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled_error", method=request.method, path=request.url.path)
        response = JSONResponse({"detail": "Something went wrong on our side.", "request_id": rid}, status_code=500)
    finally:
        structlog.contextvars.clear_contextvars()
    ms = round((time.perf_counter() - t0) * 1000)
    if response.status_code >= 500 or ms > 2000:
        log.warning("slow_or_failed_request", method=request.method, path=request.url.path,
                    status=response.status_code, ms=ms, request_id=rid)
    response.headers["x-request-id"] = rid
    return response


app.include_router(auth.router)
app.include_router(portfolio.router)
app.include_router(safety.router)
app.include_router(swap.router)
app.include_router(copy.router)
app.include_router(alerts.router)
app.include_router(health.router)
app.include_router(telegram.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"ok": True, "chain_id": settings.chain_id}
