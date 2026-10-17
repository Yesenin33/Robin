"""Probe every configured RPC endpoint the way the app uses it.

    docker compose exec worker python -m scripts.rpc_probe

For each endpoint: eth_blockNumber, a 50-call JSON-RPC batch, and eth_getLogs over 10 / 1,000 / 9,999 blocks.
Prints status + latency, so you can see which node serves what (e.g. who 500s on getLogs, who caps ranges)
and decide RPC_URL / RPC_FALLBACK_URLS / RPC_LOGS_URLS. Never prints API keys.
"""
import json
import time

from app.api.health import _redact
from app.core.config import get_settings
from app.services.engine.loader import rpc_urls
from app.services.intelligence.config import logs_urls
from app.services.intelligence.rpc import TRANSFER_TOPIC, _http_post


def call(url: str, payload: list[dict]) -> str:
    t0 = time.perf_counter()
    try:
        status, body, retry_after = _http_post(url, payload, 20)
    except Exception as e:  # noqa: BLE001
        return f"ERR   {type(e).__name__}: {str(e)[:60]}"
    ms = round((time.perf_counter() - t0) * 1000)
    errs = [r["error"] for r in body if isinstance(r, dict) and "error" in r] if isinstance(body, list) else \
        ([body["error"]] if isinstance(body, dict) and "error" in body else [])
    note = f" Retry-After={retry_after}" if retry_after else ""
    if status != 200:
        return f"{status}   {ms} ms{note}"
    if errs:
        return f"200 but JSON-RPC error: {json.dumps(errs[0])[:90]}  {ms} ms"
    return f"200   {ms} ms"


def main() -> None:
    s = get_settings()
    urls = list(dict.fromkeys([*rpc_urls().split(","), *logs_urls()]))
    head = None
    for url in urls:
        print(f"\n== {_redact(url)}")
        t = call(url, [{"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}])
        print(f"  eth_blockNumber        {t}")
        try:
            head = head or int(_http_post(url, [{"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}], 20)[1][0]["result"], 16)
        except Exception:  # noqa: BLE001
            pass
        batch = [{"jsonrpc": "2.0", "id": i, "method": "eth_getBalance", "params": ["0x" + "11" * 20, "latest"]} for i in range(50)]
        print(f"  batch of 50            {call(url, batch)}")
        for span in (10, 1_000, s.log_chunk_blocks):
            if not head:
                break
            lo, hi = head - span, head
            flt = {"topics": [TRANSFER_TOPIC], "fromBlock": hex(lo), "toBlock": hex(hi)}
            print(f"  eth_getLogs {span:>6} blk  {call(url, [{'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs', 'params': [flt]}])}")


if __name__ == "__main__":
    main()
