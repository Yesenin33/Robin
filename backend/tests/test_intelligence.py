"""intelligence.scan() end-to-end on a fully mocked chain (JSON-RPC transport + market fetch)."""
import pytest

from app.services import intelligence
from app.services.intelligence import SCHEMA_KEYS, contract, trading
from app.services.intelligence.detect import detect_chain
from app.services.intelligence.holders import Transfer
from app.services.intelligence.http import HttpError
from app.services.intelligence.rpc import TRANSFER_TOPIC, ZERO, pad_addr


@pytest.fixture(autouse=True)
def _offline_sources():
    """These tests mock the chain and market; keep the real explorer defaults out of the way."""
    intelligence.configure(explorer_api_url="", explorer_proxy_prefix="")


E18 = 10**18
T = "0x" + "70" * 20  # token
P = "0x" + "50" * 20  # pool
D = "0x" + "d0" * 20  # deployer / owner
S1, S2 = "0x" + "51" * 20, "0x" + "52" * 20  # snipers
W = ["0x" + f"a{i}" * 20 for i in range(1, 4)]  # deployer's split wallets
B = ["0x" + f"{0xb0 + i:02x}" * 20 for i in range(12)]  # organic buyers


def abi_string(s: str) -> str:
    b = s.encode()
    return "0x" + (32).to_bytes(32, "big").hex() + len(b).to_bytes(32, "big").hex() + b.ljust(32, b"\0").hex()


def bytecode(*selectors: str) -> str:
    # PUSH32 whose payload *contains* a PUSH4(mint) pattern must be skipped by the parser
    decoy = "7f" + ("6340c10f19" + "00" * 27)
    return "0x6080" + decoy + "".join(f"63{s}14" for s in selectors) + "00"


class Chain:
    """Minimal in-memory EVM view answering the JSON-RPC calls scan() makes."""

    def __init__(self, transfers, code, owner=D, supply=1000 * E18, impl_slot="0x" + "0" * 64):
        self.transfers, self.code, self.owner, self.supply, self.impl_slot = transfers, code, owner, supply, impl_slot
        self.bal: dict[str, int] = {}
        for _, _, frm, to, v in transfers:
            if frm != ZERO:
                self.bal[frm] = self.bal.get(frm, 0) - v
            self.bal[to] = self.bal.get(to, 0) + v

    def eth_call(self, to, data):
        sel = data[2:10]
        if to != T:
            return "0x"
        return {
            "95d89b41": abi_string("MOON"), "06fdde03": abi_string("Mooncat"),
            "313ce567": "0x" + (18).to_bytes(32, "big").hex(), "18160ddd": "0x" + self.supply.to_bytes(32, "big").hex(),
            "8da5cb5b": "0x" + pad_addr(self.owner),
            "70a08231": "0x" + max(self.bal.get("0x" + data[-40:], 0), 0).to_bytes(32, "big").hex(),
        }[sel]

    def handle(self, m, p):
        if m == "eth_blockNumber":
            return hex(200)
        if m == "eth_getCode":
            return self.code if p[0] == T else ("0x6001" if p[0] == P else "0x")
        if m == "eth_getStorageAt":
            return self.impl_slot if p[1] == contract.EIP1967_IMPL else "0x" + "0" * 64
        if m == "eth_call":
            return self.eth_call(p[0]["to"], p[0]["data"])
        if m == "eth_getLogs":
            lo, hi = int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16)
            return [{"topics": [TRANSFER_TOPIC, "0x" + pad_addr(f), "0x" + pad_addr(t)], "data": hex(v),
                     "blockNumber": hex(b), "transactionHash": tx} for b, tx, f, t, v in self.transfers if lo <= b <= hi]
        raise AssertionError(m)

    def __call__(self, payload):
        return [{"jsonrpc": "2.0", "id": c["id"], "result": self.handle(c["method"], c["params"])} for c in payload]


def dexscreener(liq=6_000, fdv=100_000, site=False):
    def fetch(url):
        assert "dexscreener" in url
        return {"pairs": [{"chainId": "robinhood", "pairAddress": P, "priceUsd": "0.0001", "fdv": fdv,
                           "liquidity": {"usd": liq}, "priceChange": {"h24": -31},
                           "baseToken": {"symbol": "MOON", "name": "Mooncat"},
                           "info": {"websites": [{"url": "x"}] if site else [], "socials": []}}]}
    return fetch


SCAM = [
    (100, "0xm", ZERO, D, 1000 * E18),
    (101, "0xl", D, P, 400 * E18),  # add liquidity
    (102, "0xs1", P, S1, 50 * E18),  # snipers, same block
    (102, "0xs2", P, S2, 50 * E18),
    (103, "0xf", D, W[0], 100 * E18), (103, "0xf", D, W[1], 100 * E18), (103, "0xf", D, W[2], 100 * E18),
] + [(110 + i, f"0xb{i}", P, b, 5 * E18) for i, b in enumerate(B)]


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(intelligence.config, "_overrides", {"HOLDER_SCAN_BLOCKS": "1000", "EXPLORER_API_URL": ""})


def test_scan_scam_token_end_to_end():
    from app.services.intelligence.contract import BLACKLIST, MINT  # noqa: F401
    chain = Chain(SCAM, bytecode("a9059cbb", "40c10f19", "f9f92be4"))
    r = intelligence.scan(T, transport=chain, fetch=dexscreener())

    assert tuple(r) == SCHEMA_KEYS
    assert (r["symbol"], r["name"], r["decimals"], r["total_supply"]) == ("MOON", "Mooncat", 18, 1000)
    assert r["can_mint"] is True and r["blacklist"] is True and r["proxy"] is False and r["owner_renounced"] is False
    assert r["holders_count"] == 19 and r["holders_count_exact"] is True  # window starts at genesis
    assert r["pair_address"] == P and r["liquidity_usd"] == 6000
    # deployer + 3 split wallets = one operator holding 60%
    top = r["operators"][0]
    assert top["label"] == "cluster" and set(top["wallets"]) == {D, *W} and top["pct"] == pytest.approx(60)
    assert r["top_holder_pct"] == pytest.approx(60)
    assert any(o["label"] == "sniper" and set(o["wallets"]) == {S1, S2} for o in r["operators"])
    # circulating 760 (pool excluded), linked 600 + snipers 100 -> 60/760
    assert r["real_hands_pct"] == pytest.approx(7.89, abs=0.01)
    assert r["honeypot"] is True and r["sell_tax"] is None and r["buy_tax"] == 0
    assert r["top_holders"][0]["address"] == D and r["top_holders"][1]["label"] == "pool"
    assert r["whale_labels"][0].endswith("4 linked wallets · 60.0%")
    # assessment
    assert r["verdict"] == "DANGER" and r["safety_score"] < 40
    assert "You may not be able to sell this token." in r["risks"]
    assert "One wallet owns most of it." in r["risks"]
    assert r["sources"] == ["rpc", "dexscreener", "rpc-logs"] and r["errors"] == []


def test_scan_healthy_token_is_clean():
    buyers = [(110 + i, f"0xb{i}", P, b, 5 * E18) for i, b in enumerate(B)]
    sells = [(130 + i, f"0xs{i}", b, P, 2 * E18) for i, b in enumerate(B[:6])]
    transfers = [(100, "0xm", ZERO, P, 1000 * E18)] + buyers + sells  # all supply to the pool, burned LP
    chain = Chain(transfers, bytecode("a9059cbb"), owner=ZERO)
    r = intelligence.scan(T, transport=chain, fetch=dexscreener(liq=2_000_000, fdv=1_000_000, site=True))
    assert r["owner_renounced"] is True and r["can_mint"] is False and r["honeypot"] is False
    assert r["sell_tax"] == 0 and r["real_hands_pct"] == 100
    # few holders in this toy chain is the only remaining risk
    assert r["risks"] == ["Very few people own it."] and r["verdict"] == "CLEAN"


def test_scan_survives_market_outage():
    def down(url):
        raise HttpError("dexscreener down")
    r = intelligence.scan(T, transport=Chain(SCAM, bytecode()), fetch=down)
    assert tuple(r) == SCHEMA_KEYS
    assert r["symbol"] == "MOON" and r["liquidity_usd"] is None
    assert any(e.startswith("market:") for e in r["errors"])
    # holders were read from the logs, but the market lookup failed: below the evidence bar
    assert r["verdict"] in ("UNKNOWN", "DANGER")
    assert r["verdict"] != "CLEAN" and any("market data" in x for x in r["risks"])


def test_scan_rejects_bad_address():
    with pytest.raises(ValueError):
        intelligence.scan("0x123")


def test_proxy_detection_eip1967_and_1167():
    impl = "0x" + "99" * 20
    chain = Chain(SCAM, bytecode("a9059cbb"), impl_slot="0x" + pad_addr(impl))
    assert intelligence.scan(T, transport=chain, fetch=dexscreener())["proxy"] is True
    minimal = "0x363d3d373d3d3d363d73" + "99" * 20 + "5af43d82803e903d91602b57fd5bf3"
    sec = contract.security(intelligence.rpc.Rpc("x", Chain(SCAM, minimal)), T, None)
    assert sec["proxy"] is True and sec["implementation"] == impl and sec["owner_renounced"] is None


def test_push4_parser_skips_push_data():
    assert contract.push4_selectors(bytecode("a9059cbb")) == {"a9059cbb"}


def test_trading_tax_from_fee_legs():
    seller, fee_wallet = "0x" + "c1" * 20, T
    transfers = [
        Transfer(10, "0x1", seller, P, 90), Transfer(10, "0x1", seller, fee_wallet, 10),  # 10% sell tax
        Transfer(9, "0x2", P, "0x" + "c2" * 20, 95), Transfer(9, "0x2", P, fee_wallet, 5),  # 5% buy tax
    ]
    t = trading.analyze(transfers, P, T)
    assert (t["sell_tax"], t["buy_tax"], t["honeypot"]) == (10.0, 5.0, False)


def test_detect_chain():
    assert detect_chain(T, rpc_factory=lambda url: intelligence.rpc.Rpc(url, Chain(SCAM, bytecode()))) == "robinhood"
    assert detect_chain("0x" + "01" * 20, rpc_factory=lambda url: intelligence.rpc.Rpc(url, Chain(SCAM, bytecode()))) is None


def test_selector_constants_are_real_keccak():
    from eth_utils import keccak

    sigs = {
        "40c10f19": "mint(address,uint256)", "a0712d68": "mint(uint256)", "449a52f8": "mintTo(address,uint256)",
        "f9f92be4": "blacklist(address)", "44337ea1": "addToBlacklist(address)", "153b0d1e": "setBlacklist(address,bool)",
        "455a4396": "blacklistAddress(address,bool)", "d34628cc": "addBots(address[])",
        "9c0db5f3": "setBots(address[],bool)", "342aa8b5": "setBot(address,bool)",
        "95d89b41": "symbol()", "06fdde03": "name()", "313ce567": "decimals()", "18160ddd": "totalSupply()",
        "8da5cb5b": "owner()", "70a08231": "balanceOf(address)",
    }
    for sel, sig in sigs.items():
        assert keccak(text=sig)[:4].hex() == sel, sig
    assert keccak(text="Transfer(address,address,uint256)").hex() == TRANSFER_TOPIC[2:]
    assert contract.MINT | contract.BLACKLIST <= set(sigs)


def test_scan_raises_when_nothing_is_reachable():
    def dead_rpc(payload):
        raise HttpError("connection refused")

    def dead_market(url):
        raise HttpError("blocked")

    with pytest.raises(intelligence.ScanUnavailable, match="meta"):
        intelligence.scan(T, transport=dead_rpc, fetch=dead_market)


# ---------- evidence bar: "unknown" must never read as "safe" ----------

from app.services.intelligence.scoring import UNKNOWN, assess, evidence_gaps  # noqa: E402


def _dead(url):
    raise HttpError("blocked")


def test_fully_failed_scan_is_unknown_not_clean():
    """Production repro: explorer + market failing -> used to be CLEAN, score 90."""
    chain = Chain([], bytecode("a9059cbb"), owner=ZERO)  # RPC works, no transfers, so no holders from logs either
    intelligence.configure(explorer_api_url="https://explorer.test", explorer_proxy_prefix="")
    import app.services.intelligence.holders as holders_mod

    original = holders_mod.get_json
    holders_mod.get_json = _dead
    try:
        r = intelligence.scan(T, transport=chain, fetch=_dead)
    finally:
        holders_mod.get_json = original
    assert r["holders_count"] is None and r["real_hands_pct"] is None and r["liquidity_usd"] is None
    assert r["errors"], "the failures are recorded"
    assert r["verdict"] == UNKNOWN == "UNKNOWN"
    assert r["safety_score"] is None
    assert r["verdict"] != "CLEAN"
    assert r["risks"][0].startswith("We couldn't check this token:")
    assert "market data" in r["risks"][0] and "holder data" in r["risks"][0]


def test_detected_danger_survives_missing_data():
    """Missing market data must not hide a real finding: the deployer's cluster owns 60%."""
    chain = Chain(SCAM, bytecode("a9059cbb", "40c10f19"))
    r = intelligence.scan(T, transport=chain, fetch=_dead)
    assert r["liquidity_usd"] is None and r["verdict"] == "DANGER"
    assert r["safety_score"] is not None and "One wallet owns most of it." in r["risks"]
    assert any("market data" in x for x in r["risks"])  # ...and we still say what we couldn't check


FULL = dict(holders_count=5000, liquidity_usd=900_000, real_hands_pct=95, top_holder_pct=1, fdv=2_000_000,
            honeypot=False, can_mint=False, owner_renounced=True, errors=[])


@pytest.mark.parametrize("override,gap", [
    ({"holders_count": None}, "holder data"),
    ({"liquidity_usd": None}, "trading pool info"),
    ({"errors": ["market: GET https://api.dexscreener.com/...: HTTP Error 403"], "liquidity_usd": None}, "market data"),
    ({"errors": ["meta: POST https://rpc: boom"]}, "contract details"),
    ({"errors": ["security: not a contract"]}, "contract details"),
])
def test_each_missing_piece_blocks_clean(override, gap):
    a = assess({**FULL, **override})
    assert a.verdict == UNKNOWN and a.score is None
    assert gap in a.risks[0] and a.risks[0].startswith("We couldn't check this token:")
    assert evidence_gaps({**FULL, **override})


def test_complete_evidence_is_still_clean_and_failures_elsewhere_do_not_count():
    a = assess(FULL)
    assert (a.verdict, a.score, a.risks) == ("CLEAN", 100, [])
    # an unrelated stage failing (e.g. transfers) is not an evidence gap by itself
    assert assess({**FULL, "errors": ["transfers: all RPC endpoints failed"]}).verdict == "CLEAN"


def test_gap_text_names_everything_missing():
    a = assess({**FULL, "holders_count": None, "liquidity_usd": None, "errors": ["market: boom"]})
    assert a.risks[0] == "We couldn't check this token: market data and holder data were unavailable."
    three = assess({**FULL, "holders_count": None, "errors": ["market: x", "meta: y"], "liquidity_usd": None})
    assert three.risks[0] == "We couldn't check this token: market data, holder data and contract details were unavailable."


def test_non_critical_findings_with_missing_data_are_unknown_but_listed():
    a = assess({**FULL, "can_mint": True, "owner_renounced": False, "holders_count": None})
    assert a.verdict == UNKNOWN and a.score is None
    assert "The creator can print more of it at any time." in a.risks  # what we did find is still shown
