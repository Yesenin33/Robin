# Chromium sidecar

Reads the Cloudflare-challenged Blockscout API with a real browser. Internal service of the compose stack
(`browser`), not published to the host.

```
POST /internal/fetch   {"url": "https://robinhoodchain.blockscout.com/api/v2/tokens/<ca>/holders"}
GET  /internal/fetch?url=...        # same, for curl:  docker compose exec api curl -s "http://browser:8765/internal/fetch?url=..."
GET  /health                        # fetches, challenges_solved, challenges_failed, last_error
```

* Returns the upstream body and status unchanged (404, 429 + `Retry-After`, ...) plus `X-Upstream-Status`.
  Its own failures: 400 bad URL, 401 bad token, 403 host not allowed, 503 `challenge_unsolved` / `browser_error`.
* **Only `ALLOWED_HOSTS` are fetched** (default in compose: the explorer host; empty = refuse everything), so it
  cannot be used as an open proxy / SSRF gadget. `FETCH_TOKEN` adds an optional bearer check.
* How: a persistent Chromium context (`/data/profile`, volume `browser-profile`) opens the explorer origin, waits for
  the JS challenge to clear, then runs `fetch(url)` inside that page. The clearance cookie persists across
  restarts; when Cloudflare revokes it the next request re-solves it, one solver at a time per origin.
* Image: Debian `chromium` + Playwright driver (no bundled browsers), ~600 MB.

Tests (real Chromium against a fake challenged explorer):

```bash
pip install -r requirements-dev.txt
CHROMIUM_PATH=/usr/bin/chromium PYTHONPATH=. pytest -q
```
