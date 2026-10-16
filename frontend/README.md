# Chainfolio web (PWA)

React 18 + TypeScript + Vite. Built 1:1 from the design `../Chainfolio.dc.html`
(colors, Manrope, animated coin background, phone layout, loading/empty/error states).

## Screens → API

| Route | Screen | Backend |
|---|---|---|
| `/` | Landing (demo ticker) | — |
| `/login` | Onboarding: Connect wallet (SIWE, chain 4663) — no e-mail path | `GET /auth/siwe/nonce`, `POST /auth/siwe` |
| `/app/portfolio` | Total, 24h change, holdings, pull-to-refresh | `GET /portfolio/{address}` |
| `/app/safety/:ca` | Verdict, risks, 4 metrics, Buy/Sell; polls on 202 | `GET /safety/{ca}` |
| `/app/copy` | Smart wallets feed (empty state until Phase 2 API) | `GET /copy/feed`, `POST /copy/subscribe` |
| `/app/profile` | Plan, alert toggles, sign out | `GET /auth/me`, `POST /billing/checkout` |

## Run

```bash
cp .env.example .env.local
npm install
npm run dev          # http://localhost:3000, /api -> http://localhost:8000
npm run build        # dist/
```

**Wallets:** "Connect wallet" uses an injected wallet when there is one, otherwise WalletConnect through Reown AppKit
(QR on desktop, deep link into the wallet app on phones; loaded on demand). Set `VITE_WC_PROJECT_ID`
(free at cloud.reown.com; it is a build-time variable, passed to the `web` image by compose). A session without a
wallet only sees the "Connect a wallet to continue" step.

Sign-in is wallet-only: there is no e-mail/magic-link path. Backend `.env`: `FRONTEND_URL=http://localhost:3000`
(Telegram bot link) and `SIWE_DOMAIN=localhost:3000`.

## Deploy

`backend/docker-compose.yml` includes the `web` service (nginx serving `dist/`, `/api/*` → `api:8000`),
listening on `127.0.0.1:8080`. Point Caddy/nginx with TLS at it, e.g.
`chainfolio.app { reverse_proxy 127.0.0.1:8080 }`, and set backend `FRONTEND_URL=https://chainfolio.app`,
`SIWE_DOMAIN=chainfolio.app`.

PWA: `public/manifest.webmanifest` + `public/sw.js` (app-shell cache, API never cached, Web Push handler).

## Design system and source of truth

`src/styles.css` is the source of truth for the look (tokens at the top: surface levels, hairline, radius scale
8/12/16/24/99, elevation, type scale `.t-display/.t-title/.t-body/.t-caption`, 180 ms ease-out motion, focus ring,
`prefers-reduced-motion`). `../Chainfolio.dc.html` is the original mock and is NOT kept in sync any more.
Every confirmation uses `components/Sheet.tsx` (the swap sheet included).

## In-app swap

`Buy`/`Sell` on the Safety screen opens `components/SwapSheet.tsx`: amount -> live quote from `POST /swap/quote`
-> the verdict in the same sheet (DANGER/UNKNOWN need an explicit tick) -> one-time approvals -> `eth_sendTransaction`
on the connected provider -> receipt. Keys never reach the browser; the server only quotes and builds calldata.
Off until the backend reports `GET /swap/config` -> `enabled: true`; `VITE_SWAP_URL` stays as the external fallback.
`npm test` runs the pure helpers (`src/lib/lib.test.ts`, node:test).
