# Chainfolio backend

FastAPI backend for the Chainfolio PWA (design: `../Chainfolio.dc.html`) on Robinhood Chain (EVM L2, chain 4663).
Token analytics are delegated to the existing **CRAWLSCAN** engine — it is imported, not reimplemented.

## Layout

```
app/
  api/        routers: auth, portfolio, safety (+ deps)
  core/       config (.env), db, redis, JWT, rate limit, structured logging
  models/     SQLAlchemy: users, wallets, tokens, holders_snapshots, safety_scores,
              positions, copy_targets, copy_events, alerts, subscriptions
  schemas/    pydantic request/response models (shapes follow the design screens)
  services/
    engine/   CRAWLSCAN adapter (crawlscan.py) + builtin DexScreener fallback
    safety_service.py     report -> CLEAN/RISKY/DANGER + plain-language risks + 4 metrics
    portfolio_service.py  RPC balances + prices + 24h change + PnL (avg entry)
    auth_service.py, siwe.py, mailer.py, embedded_wallet.py (Web3Auth/Privy stub)
  jobs/       worker (APScheduler): scan queue, stale-score refresh, position snapshots
alembic/      migrations
scripts/bootstrap_indexer.py   seed tokens from chain + queue scans
tests/        service-layer tests (sqlite + fakeredis + mocked RPC)
```

## CRAWLSCAN integration

`app/services/engine/crawlscan.py` adds `CRAWLSCAN_PATH` to `sys.path` and calls `CRAWLSCAN_ENTRY`
(`module:function`, `function(address) -> dict`). `map_report()` is the only place that knows CRAWLSCAN's
output keys — **verify it against the real report format** and adjust the key paths if needed.
If CRAWLSCAN can't be imported, the API falls back to `BuiltinEngine` (DexScreener market data only,
holder/contract fields unknown) and logs `crawlscan_unavailable_fallback_builtin`.

CRAWLSCAN is sync/stdlib, so scans run in a thread with a timeout, guarded by a Redis lock per token.

## Phase 1 API

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/magic-link` | `{email}` → 202, link e-mailed (logged if `SMTP_HOST` empty) |
| GET | `/auth/verify?token=` | single-use, → `{access_token, user}` |
| GET | `/auth/siwe/nonce` | EIP-4361 nonce |
| POST | `/auth/siwe` | `{message, signature}`; with Bearer → links wallet to current user |
| GET | `/auth/me` | Bearer |
| GET | `/portfolio/{address}` | holdings, total, 24h change, PnL vs avg entry; cached 30s |
| GET | `/safety/{ca}` | verdict + summary + risks + metrics; Redis 10m → DB (<60m) → scan; 202 if a scan is already running |
| POST | `/safety/{ca}/refresh` | queue rescan for the worker |

OpenAPI: `/docs`. Public endpoints are rate-limited per IP (`RATE_LIMIT_PER_MIN`).
Phase 2: `/token`, `/copy/*`, `/alerts`, `/billing/checkout` (tables already exist).

## Run locally

```bash
cp .env.example .env            # set JWT_SECRET, POSTGRES_PASSWORD, CRAWLSCAN_HOST_PATH
docker compose up -d --build    # postgres, redis, migrate, api, worker
docker compose exec api python -m scripts.bootstrap_indexer --discover-blocks 5000 --top 50
open http://localhost:8000/docs
```

Tests:
```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

## Deploy (single VPS)

1. Install Docker + compose plugin; clone repo; `cd backend && cp .env.example .env` and fill secrets.
2. Put CRAWLSCAN at `CRAWLSCAN_HOST_PATH` (default `/root/crawlscan`).
3. `docker compose up -d --build` — API listens on `127.0.0.1:8000` only.
4. Put Caddy/nginx in front for TLS, e.g. Caddyfile: `api.chainfolio.app { reverse_proxy 127.0.0.1:8000 }`.
5. Update: `git pull && docker compose up -d --build` (migrations run automatically via `migrate`).
6. Logs are JSON on stdout: `docker compose logs -f api worker`.
