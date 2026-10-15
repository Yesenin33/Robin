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

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/magic-link`, GET `/auth/verify` | e-mail sign-in |
| POST | `/auth/link-token` | one-time token (10 min): another browser attaches its wallet to THIS account (`link_token` on `/auth/siwe`) |
| POST | `/safety/{ca}/refresh` | priority scan; drops the cached result and the recent-DB-row shortcut until it lands |
| GET/POST | `/auth/siwe/nonce`, `/auth/siwe` | wallet sign-in (EIP-4361) |
| GET | `/portfolio/{address}` | holdings, 24h change, PnL |
| GET | `/safety/{ca}` | verdict + summary + risks + 4 metrics; 202 while scanning |
| GET | `/copy/feed?only_followed=` | followed wallets' swaps, plain-English reason + risk tag from the latest safety check |
| GET | `/copy/suggested` | wallets ranked by realised PnL on recently created pools (on-chain, never hand-picked) |
| POST/DELETE | `/copy/subscribe` | follow/unfollow; first follow queues a history backfill |
| GET | `/alerts`, POST `/alerts/{id}/read`, `/alerts/read-all` | in-app alerts |
| GET/POST/DELETE | `/alerts/push/key`, `/alerts/push/subscribe` | Web Push (VAPID) |
| POST | `/alerts/telegram/link-webapp` | Mini App: validated `initData` links (or signs in) with zero typing |
| POST | `/alerts/telegram/link` | deep link `t.me/<bot>?start=<token>` + 6-digit code (single use, 10 min) |
| POST/DELETE | `/alerts/telegram/verify`, `/alerts/telegram` | code the bot sent on `/start`; unlink |
| POST | `/telegram/webhook/{secret}` | bot updates (`/start /portfolio /safe /copy /alerts /stop /help`) |
| GET | `/health/sources?fresh=1` | status + latency of postgres, redis, every RPC, explorer, DexScreener, GeckoTerminal |

## Worker jobs

| Job | Every | What |
|---|---|---|
| `discover_pools` | 5 min | V4 PoolManager `Initialize` logs from a Redis cursor -> new `tokens` (+ scan queued) |
| `collect` (copy) | 2 min | followed wallets' swaps in any token since the cursor; ETH leg from receipts; follower alerts |
| `drain_priority` / `drain_queue` | 1 s / 5 s | user refreshes (own queue, never behind bulk) / bulk scans, `SCAN_CONCURRENCY` at a time, queue capped at `SCAN_QUEUE_MAX` |
| `drain_backfill` | 20 s | first-follow history: explorer token-transfers, RPC log scan if the explorer is down |
| `rank_wallets` | 60 min | realised **USD** PnL x hit rate over the newest pools -> `smart_wallets` (quote legs valued via `QUOTE_TOKENS`) |
| `warmup` | 2 min | re-scan the most requested tokens before their cached verdict expires |
| `deliver` | 30 s | Telegram + Web Push, `ALERTS_PER_HOUR` per user; stores `tg_message_id` |
| `price_moves` | 10 min | holders alerted on a >= `PRICE_MOVE_PCT` move |
| scan queue / stale refresh / position snapshots | 5 s / 30 min / 5 min | as before |

## Verify on the server

```bash
cd /root/chainfolio/backend
python -m scripts.gen_vapid                       # paste into .env with TELEGRAM_BOT_TOKEN/USERNAME/WEBHOOK_SECRET
docker builder prune -af && docker compose up -d --build migrate api worker web
docker compose exec api python -m scripts.telegram_setup && docker compose exec api python -m scripts.telegram_setup --info
docker compose logs worker --tail=60 | grep -E "pools_discovered|wallets_ranked|copy_collected|copy_backfilled|alerts_delivered"
curl -s localhost:8000/health/sources?fresh=1 | python3 -m json.tool
TOKEN=...   # from a SIWE login in the app (localStorage cf_token)
curl -s localhost:8000/copy/suggested -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
curl -s -XPOST localhost:8000/copy/subscribe -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"address":"<wallet from /copy/suggested>","follow":true}'
sleep 30; curl -s "localhost:8000/copy/feed?only_followed=true" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
curl -s localhost:8000/alerts -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
docker compose exec postgres psql -U chainfolio -c "select id, title, payload->>'tg_message_id' from alerts order by id desc limit 5"
docker compose exec api python -m scripts.loadtest http://localhost:8000/health -c 50 -n 3000
```

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
