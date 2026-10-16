# Chainfolio — rules for anyone (human or agent) changing this code

Live deployment: `/root/chainfolio` on the project box, public at https://212.113.101.238.nip.io.
Backend = FastAPI + Postgres + Redis + a Chromium sidecar; frontend = React/Vite PWA behind nginx.

## Hard rules — breaking one of these has already cost real damage

- **Non-custodial, always.** We never hold a private key and never sign on the server. User transactions are
  built and sent through the provider the user connected (Phantom, or WalletConnect via AppKit).
- **`app/services/intelligence/schema.py` keys are FROZEN.** The scan output schema is a contract.
- **Never read only `RPC_URL`.** The engine builds its endpoint list from
  `intelligence/config.py:rpc_urls()` = `RPC_URL` + `RPC_FALLBACK_URLS`, comma-joined; the pool then routes
  per request (the keyed endpoint cannot serve wide `eth_getLogs`). Reading only `RPC_URL` produced 1 644
  UNKNOWN verdicts.
- **Keep the per-container RPC split** in `backend/docker-compose.override.yml`: `api` = keyed Alchemy first
  (fast `eth_call`), `worker` = the official node (wide `eth_getLogs`).
- **`backend/.env` is not in git** and holds live secrets. Read it; never rewrite its secret lines.
- **Disk: a 10 GB box with ~900 MB free.** Run `docker builder prune -af` before every build and always
  build with `--no-deps` — a plain build pulls in the Chromium sidecar and dies with ENOSPC.
- **Never present a number you cannot stand behind.** UNKNOWN is not CLEAN; a balance a pool cannot pay out
  is not a balance; a rate we could not fetch is not a rate. Say what we could not check.

## Verify, do not assume

```bash
cd /root/chainfolio/backend
docker compose run --rm -T --no-deps -e SIWE_DOMAIN=localhost:3000 -v $PWD/tests:/app/tests:ro api \
  sh -c "pip install -q pytest pytest-asyncio pytest-randomly fakeredis aiosqlite; python -m pytest tests/ -q"
docker builder prune -af
docker compose up -d --build --no-deps migrate api worker web
curl -s https://212.113.101.238.nip.io/api/health
```

`SIWE_DOMAIN=localhost:3000` is required for the suite: the SIWE tests sign for that domain.

## Where things are

- `backend/app/services/intelligence/` — the token analytics engine (stdlib-only, `scan(address, chain) -> dict`).
- `backend/app/services/safety_service.py` — scan orchestration, cache, verdict; `app/jobs/` — the worker.
- `frontend/src/screens/` — Landing, Onboarding, Portfolio, Safety, Copy, Profile; `frontend/src/styles.css`
  is the visual system, implemented from the prototype in `design/prototype/` (see frontend/README.md).
- The owner reviews on a phone: check UI work in a 390x844 viewport.

## Open work

`TASKS/opus-2026-10-16.md` — the current defect list and the swap/style workstreams.
