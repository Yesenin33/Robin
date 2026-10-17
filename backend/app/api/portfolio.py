from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.api.deps import portfolio_service
from app.core.ratelimit import rate_limit
from app.schemas.portfolio import PortfolioOut
from app.services.portfolio_service import PortfolioService
from app.services.rpc import ADDRESS_RE

router = APIRouter(prefix="/portfolio", tags=["portfolio"], dependencies=[Depends(rate_limit)])


@router.get("/{address}", response_model=PortfolioOut)
async def get_portfolio(
    address: str = Path(pattern=ADDRESS_RE.pattern), svc: PortfolioService = Depends(portfolio_service)
):
    try:
        return await svc.get(address)
    except Exception as e:  # RPC / network
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "We couldn't reach your balance.") from e
