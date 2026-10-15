from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import JSONResponse

from app.api.deps import safety_service
from app.core.ratelimit import rate_limit
from app.core.redis import get_redis
from app.jobs.scan_job import enqueue_scan
from app.schemas.safety import SafetyOut, ScanQueued
from app.services.rpc import ADDRESS_RE
from app.services.safety_service import SafetyService, ScanInProgress

router = APIRouter(prefix="/safety", tags=["safety"], dependencies=[Depends(rate_limit)])
CA = Path(pattern=ADDRESS_RE.pattern, description="Token contract address")


@router.get("/{ca}", response_model=SafetyOut, responses={202: {"model": ScanQueued}})
async def get_safety(ca: str = CA, svc: SafetyService = Depends(safety_service)):
    try:
        return await svc.get(ca)
    except ScanInProgress:
        return JSONResponse(ScanQueued(address=ca.lower()).model_dump(), status_code=202,
                            headers={"Retry-After": "5"})
    except TimeoutError as e:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "We couldn't run the safety check.") from e


@router.post("/{ca}/refresh", response_model=ScanQueued, status_code=202)
async def refresh_safety(ca: str = CA):
    """Queue a fresh scan for the background worker."""
    await enqueue_scan(get_redis(), ca.lower())
    return ScanQueued(address=ca.lower())
