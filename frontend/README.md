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

The look comes from the design prototype in `../design/prototype/*.dc.html` (open them in a browser; they link to each
other). `src/styles.css` is its implementation and the source of truth for the product: warm paper `#F7F5EF`, ink
`#110E08`, one lime accent `#CCFF00` used as a highlight (active tab pill, toggle knob, sidebar icon) and never as text
on paper, white hairline cards, pill buttons (ink = primary, outlined = secondary), Newsreader (serif) for display and
Albert Sans for UI. Verdict colours: clean `#E4F6DD`/`#0A8F0E`, risky `#FFEFCC`/`#C77700`, danger `#FCE6E2`/`#C8281E`,
unknown `#ECE9E1`. There is no dark theme.

Layout: < 1024 px one column with a bottom tab bar (lime pill on the active tab); >= 1024 px a dark 248 px sidebar and
a 1080 px content grid. Every confirmation uses `components/Sheet.tsx` (bottom sheet on phones, centred modal on
desktop; rendered in a portal so it sits above the tab bar).

Deliberate differences from the prototype: the portfolio has no decorative sparkline (we have no price history, so a
line would be a number we cannot stand behind); the landing ticker is labelled "Demo"; progress shows the five real scan
stages the server reports, not three invented ones.

Brand: `public/mark.svg` is the mark; `python3 scripts/make_icons.py` regenerates favicon.ico/svg, apple-touch-icon,
192/512 and maskable icons and the 1200x630 `og.png`. `VITE_PUBLIC_URL` (see `.env.production`) makes `og:image` absolute.

## In-app swap

`Buy`/`Sell` on the Safety screen opens `components/SwapSheet.tsx`: amount -> live quote from `POST /swap/quote`
-> the verdict in the same sheet (DANGER/UNKNOWN need an explicit tick) -> one-time approvals -> `eth_sendTransaction`
on the connected provider -> receipt. Keys never reach the browser; the server only quotes and builds calldata.
Off until the backend reports `GET /swap/config` -> `enabled: true`; `VITE_SWAP_URL` stays as the external fallback.
`npm test` runs the pure helpers (`src/lib/lib.test.ts`, node:test).
