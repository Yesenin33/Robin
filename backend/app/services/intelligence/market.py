"""Market data: DexScreener first, GeckoTerminal fallback."""
from app.services.intelligence.config import ChainConfig
from app.services.intelligence.http import HttpError, get_json

DEXSCREENER = "https://api.dexscreener.com/latest/dex/tokens/{ca}"
GT = "https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{ca}"


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def from_dexscreener(cfg: ChainConfig, ca: str, fetch=get_json) -> dict | None:
    pairs = [p for p in (fetch(DEXSCREENER.format(ca=ca)).get("pairs") or []) if p.get("chainId") == cfg.dexscreener_slug]
    if not pairs:
        return None
    p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
    info = p.get("info") or {}
    base = p.get("baseToken") or {}
    return {
        "symbol": base.get("symbol"), "name": base.get("name"),
        "price_usd": _f(p.get("priceUsd")),
        "price_change_24h": _f((p.get("priceChange") or {}).get("h24")),
        "liquidity_usd": _f((p.get("liquidity") or {}).get("usd")),
        "fdv": _f(p.get("fdv")),
        "pair_address": (p.get("pairAddress") or "").lower() or None,
        "has_site": bool(info.get("websites")),
        "has_twitter": any(s.get("type") == "twitter" for s in info.get("socials") or []),
        "source": "dexscreener",
    }


def from_geckoterminal(cfg: ChainConfig, ca: str, fetch=get_json) -> dict | None:
    base = GT.format(net=cfg.geckoterminal_network, ca=ca)
    pools = fetch(base + "/pools?page=1").get("data") or []
    if not pools:
        return None
    a = max(pools, key=lambda p: _f(p["attributes"].get("reserve_in_usd")) or 0)["attributes"]
    out = {
        "symbol": None, "name": None,
        "price_usd": _f(a.get("base_token_price_usd")),
        "price_change_24h": _f((a.get("price_change_percentage") or {}).get("h24")),
        "liquidity_usd": _f(a.get("reserve_in_usd")),
        "fdv": _f(a.get("fdv_usd")),
        "pair_address": (a.get("address") or "").lower() or None,
        "has_site": None, "has_twitter": None,
        "source": "geckoterminal",
    }
    try:
        info = (fetch(base + "/info").get("data") or {}).get("attributes") or {}
        out["has_site"] = bool(info.get("websites"))
        out["has_twitter"] = bool(info.get("twitter_handle"))
    except HttpError:
        pass
    return out


def fetch_market(cfg: ChainConfig, ca: str, fetch=get_json) -> dict | None:
    errors = []
    for src in (from_dexscreener, from_geckoterminal):
        try:
            if m := src(cfg, ca, fetch):
                return m
        except (HttpError, KeyError, TypeError) as e:
            errors.append(str(e))
    if errors:
        raise HttpError("; ".join(errors))
    return None
