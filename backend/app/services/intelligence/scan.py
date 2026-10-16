"""Public entry point: scan(address, chain) -> normalized dict (see schema.py).

Each stage is independent: a failing source is recorded in `errors` and the rest still runs.
"""
from collections.abc import Callable

from app.services.intelligence import contract, holders, market, operators, trading
from app.services.intelligence.config import get_chain
from app.services.intelligence.detect import is_address
from app.services.intelligence.http import get_json
from app.services.intelligence.rpc import Rpc, Transport
from app.services.intelligence.schema import empty_result
from app.services.intelligence.scoring import assess


class ScanUnavailable(RuntimeError):
    """No data source (RPC, market, explorer) answered."""


def scan(address: str, chain: str = "robinhood", *, transport: Transport | None = None,
         fetch: Callable = get_json) -> dict:
    if not is_address(address):
        raise ValueError(f"invalid address: {address!r}")
    ca = address.lower()
    cfg = get_chain(chain)
    rpc = Rpc(cfg.rpc_url, transport)
    r = empty_result(ca, chain)
    errors, sources = r["errors"], r["sources"]

    def stage(name: str, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — partial results beat no results
            errors.append(f"{name}: {e}")
            return None

    # 1. contract
    meta = stage("meta", lambda: contract.read_meta(rpc, ca))
    if meta:
        sources.append("rpc")
        r.update(symbol=meta["symbol"], name=meta["name"], decimals=meta["decimals"])
        sec = stage("security", lambda: contract.security(rpc, ca, meta["owner"]))
        if sec:
            r.update({k: sec[k] for k in ("proxy", "can_mint", "blacklist", "owner_renounced")})

    # 2. market
    m = stage("market", lambda: market.fetch_market(cfg, ca, fetch))
    if m:
        sources.append(m["source"])
        r["symbol"] = r["symbol"] or m["symbol"]
        r["name"] = r["name"] or m["name"]
        for k in ("price_usd", "price_change_24h", "liquidity_usd", "fdv", "pair_address", "has_site", "has_twitter"):
            r[k] = m[k]

    # 3. transfers -> holders, operators, taxes
    supply = meta["total_supply_raw"] if meta else None
    if supply:
        r["total_supply"] = supply / 10 ** r["decimals"]

    def load_transfers():
        problems = []
        if cfg.explorer_api:  # the explorer indexes tokens whose Transfer logs eth_getLogs does not return
            try:
                got = holders.explorer_transfers(cfg, ca)
                if got:
                    sources.append("explorer-transfers")
                    return got, False
                problems.append("explorer returned no transfers")
            except Exception as e:  # noqa: BLE001 — fall back to the node
                problems.append(f"explorer: {e}")
        head = rpc.block_number()
        start = max(0, head - cfg.holder_scan_blocks)
        got = holders.parse_transfers(rpc.transfer_logs(ca, start, head, cfg.log_chunk))
        if not got and problems:
            raise RuntimeError("; ".join([*problems, "no transfer logs from the node"]))
        return got, start == 0

    tr = stage("transfers", load_transfers)
    transfers, from_genesis = tr if tr else ([], False)

    hd = None
    if cfg.explorer_api:
        hd = stage("holders_explorer", lambda: holders.from_explorer(cfg, ca))
    if hd is None and transfers:
        hd = stage("holders_rpc", lambda: holders.from_transfers(rpc, ca, transfers, from_genesis))
    if hd:
        sources.append(hd.source)
        r["holders_count"], r["holders_count_exact"] = hd.count, hd.exact
        if not hd.contracts:  # mark contracts among the biggest holders
            top = sorted(hd.balances, key=lambda a: -hd.balances[a])[:50]
            codes = stage("holder_codes", lambda: rpc.codes(top)) or {}
            hd.contracts = {a for a, c in codes.items() if c not in ("0x", "0x0")}
        if supply:
            ops = operators.analyze(hd.balances, supply, transfers, ca, r["pair_address"], hd.contracts)
            # "real hands" = share NOT held by linked wallets. Linking needs transfers: without any (the read failed,
            # or none in the window) nothing was linked only because nothing was looked at - that is unknown.
            r.update(operators=ops["operators"], top_holder_pct=ops["top_holder_pct"],
                     real_hands_pct=ops["real_hands_pct"] if transfers else None, top_holders=ops["top_holders"],
                     whale_labels=ops["whale_labels"])

    if not transfers and hd and (hd.count or 0) > 0 and not any(e.startswith("transfers:") for e in errors):
        # holders exist but no transfer was read: that is a source gap, not an empty history
        errors.append(f"transfers: the explorer lists {hd.count} holders but no transfers could be read")

    t = trading.analyze(transfers, r["pair_address"], ca)
    r["buy_tax"], r["sell_tax"], r["honeypot"] = t["buy_tax"], t["sell_tax"], t["honeypot"]

    if not sources:  # nothing reachable: a verdict on zero data would be a lie
        raise ScanUnavailable("; ".join(errors))

    # 4. assessment
    a = assess(r)
    r["safety_score"], r["verdict"], r["risks"] = a.score, a.verdict, a.risks
    return r
