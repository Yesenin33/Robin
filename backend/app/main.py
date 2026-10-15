from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, portfolio, safety
from app.core.config import get_settings
from app.core.logging import setup_logging

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
app.include_router(auth.router)
app.include_router(portfolio.router)
app.include_router(safety.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"ok": True, "chain_id": settings.chain_id}
