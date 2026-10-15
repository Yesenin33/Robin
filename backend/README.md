# Chainfolio backend

FastAPI backend for the Chainfolio PWA (design: `../Chainfolio.dc.html`) on Robinhood Chain (EVM L2, chain 4663).
Token analytics live in this repo (`app/services/intelligence`, stdlib-only). No external engine, path or mount.

## Layout

```
app/
  api/        routers: auth, portfolio, safety (+ deps)
  core/       config (.env), db, redis, JWT, rate limit, structured logging
  models/     SQLAlchemy: users, wallets, tokens, holders_snapshots, safety_scores,
              positions, copy_targets, copy_events, alerts, subscriptions
  schemas/    pydantic request/response models (shapes follow the design screens)
  services/
    intelligence/  scan(address, chain) -> normalized dict (schema.py): contract checks, holders,
                   operators (linked wallets/snipers), taxes/honeypot, market, scoring
    engine/   IntelligenceEngine (default) + BuiltinEngine (market-only, offline dev); map_report()
    safety_service.py     report -> CLEAN/RISKY/DANGER + plain-language risks + 4 metrics
    portfolio_service.py  RPC balances + prices + 24h change + PnL (avg entry)
    auth_service.py, siwe.py, mailer.py, embedded_wallet.py (Web3Auth/Privy stub)
  jobs/       worker (APScheduler): scan queue, stale-score refresh, position snapshots
alembic/      migrations
scripts/bootstrap_indexer.py   seed tokens from chain + queue scans
tests/        service-layer tests (sqlite + fakeredis + mocked RPC)
```

## Token intelligence

`intelligence.scan(address, chain="robinhood") -> dict` — keys defined once in `intelligence/schema.py`
(`SCHEMA_KEYS`); `engine/base.py:map_report()` copies them 1:1 into `ScanReport`.

| Stage | How |
|---|---|
| contract | ERC-20 meta via `eth_call`; owner() → renounced; bytecode PUSH4 selectors → mint / blacklist; EIP-1967 / EIP-1167 → proxy |
| market | DexScreener (`DEXSCREENER_CHAIN`), GeckoTerminal fallback: price, 24h, liquidity, FDV, pool, site/twitter |
| holders | Blockscout `EXPLORER_API_URL` if set, else Transfer-log replay over `HOLDER_SCAN_BLOCKS` + live `balanceOf` |
| operators | union-find: wallet→wallet transfers, one-tx fan-outs, same-block snipers → real hands %, top operator % |
| trading | buy/sell tax from fee legs in swap txs; honeypot = many buys, zero sells |
| scoring | `scoring.assess()` → 0-100, CLEAN/RISKY/DANGER, plain-language risks (incl. x·y=k top-holder sell impact) |

A failing source is recorded in `errors`; if *no* source answers, `ScanUnavailable` → `/safety` returns 502
(never a verdict built on zero data). Each scan logs `safety_scanned` with `engine`, `sources`, `errors`.
`SCAN_ENGINE=builtin` switches to the market-only dev engine and logs `engine_builtin_dev_only`.

Scans run in a thread with a timeout, guarded by a Redis lock per token (202 while running).

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
cp .env.example .env            # set JWT_SECRET, POSTGRES_PASSWORD, RPC_URL
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
2. Set `RPC_URL` (and optionally `EXPLORER_API_URL`) for Robinhood Chain.
3. `docker compose up -d --build` — API listens on `127.0.0.1:8000` only.
4. Put Caddy/nginx in front for TLS, e.g. Caddyfile: `api.chainfolio.app { reverse_proxy 127.0.0.1:8000 }`.
5. Update: `git pull && docker compose up -d --build` (migrations run automatically via `migrate`).
6. Logs are JSON on stdout: `docker compose logs -f api worker`.
