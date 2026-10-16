from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import JSONResponse

from app.api.deps import safety_service
from app.core.ratelimit import rate_limit
from app.core.redis import get_redis
from app.core.config import get_settings
from app.jobs.scan_job import cool_key, enqueue_scan, record_hit, request_refresh, stale_marker
from app.schemas.safety import SafetyOut, ScanQueued
from app.core.logging import get_logger
from app.services.intelligence import ScanUnavailable
from app.services.rpc import ADDRESS_RE
from app.services.safety_service import SafetyService, ScanInProgress, ScanNeeded

log = get_logger(__name__)
router = APIRouter(prefix="/safety", tags=["safety"], dependencies=[Depends(rate_limit)])
CA = Path(pattern=ADDRESS_RE.pattern, description="Token contract address")


def _queued(ca: str, state: str) -> JSONResponse:
    body = ScanQueued(address=ca, state=state)  # type: ignore[arg-type]
    return JSONResponse(body.model_dump(), status_code=202, headers={"Retry-After": str(body.retry_after_s)})


@router.get("/{ca}", response_model=SafetyOut, responses={202: {"model": ScanQueued}})
async def get_safety(ca: str = CA, svc: SafetyService = Depends(safety_service)):
    redis, ca = get_redis(), ca.lower()
    await record_hit(redis, ca)
    try:
        return await svc.get(ca, inline=False)  # a request never holds its socket for a scan
    except ScanInProgress:
        return _queued(ca, "running")
    except ScanNeeded:
        if await redis.exists(cool_key(ca)):  # the queued scan failed and is backing off: say so, don't spin
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "We couldn't run the safety check.") from None
        # a user-pasted token goes to the PRIORITY queue (run_scan only takes priority entries with this marker)
        await redis.set(stale_marker(ca), "1", nx=True, ex=get_settings().refresh_marker_ttl_s)
        await enqueue_scan(redis, ca, priority=True)
        return _queued(ca, "queued")
    except TimeoutError as e:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "We couldn't run the safety check.") from e
    except ScanUnavailable as e:
        log.warning("safety_scan_unavailable", ca=ca, error=str(e))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "We couldn't run the safety check.") from e


@router.post("/{ca}/refresh", response_model=ScanQueued, status_code=202)
async def refresh_safety(ca: str = CA):
    """Fresh scan, ahead of the bulk queue. Drops the cached result and the recent-DB-row shortcut, so the
    next GET returns the new scan (202 while it runs) instead of the previous row."""
    await request_refresh(get_redis(), ca.lower())
    return ScanQueued(address=ca.lower())
