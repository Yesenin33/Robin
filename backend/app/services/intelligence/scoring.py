"""0-100 safety score, verdict and plain-language risks — derived ONLY from the normalized result."""
from dataclasses import dataclass

CLEAN, RISKY, DANGER = "CLEAN", "RISKY", "DANGER"


@dataclass
class Assessment:
    score: int
    verdict: str
    risks: list[str]
    critical: bool


def sell_impact_pct(top_holder_pct: float | None, fdv: float | None, liquidity_usd: float | None) -> float | None:
    """Price drop if the top operator market-sells into a constant-product (x*y=k) pool.

    pool quote side Q = L/2; seller dumps X = top% * FDV of value; price_after/price_before = (Q/(Q+X))^2
    """
    if top_holder_pct is None or not fdv or not liquidity_usd:
        return None
    q = liquidity_usd / 2
    x = top_holder_pct / 100 * fdv
    return round((1 - (q / (q + x)) ** 2) * 100, 1)


def assess(r: dict) -> Assessment:
    score = 100
    risks: list[str] = []
    critical = False
    renounced = r.get("owner_renounced")

    def hit(points: int, line: str, crit: bool = False) -> None:
        nonlocal score, critical
        score -= points
        risks.append(line)
        critical = critical or crit

    if r.get("honeypot"):
        hit(60, "You may not be able to sell this token.", True)
    for side, tax in (("Selling", r.get("sell_tax")), ("Buying", r.get("buy_tax"))):
        if tax is not None and tax >= 30:
            hit(40, f"{side} costs a {tax:.0f}% fee.", True)
        elif tax is not None and tax >= 10:
            hit(15, f"{side} costs a {tax:.0f}% fee.")
    if r.get("blacklist") and renounced is not True:
        hit(20, "The creator can block wallets from selling.", renounced is False)
    if r.get("can_mint") and renounced is not True:
        hit(20, "The creator can print more of it at any time.")
    if r.get("proxy") and renounced is not True:
        hit(10, "The creator can change how the token works.")
    if renounced is False:
        hit(5, "The creator still controls the contract.")

    top = r.get("top_holder_pct")
    if top is not None and top >= 50:
        hit(40, "One wallet owns most of it.", True)
    elif top is not None and top >= 20:
        hit(15, "A few wallets hold a big share.")
    elif top is not None and top >= 10:
        score -= 5

    impact = sell_impact_pct(top, r.get("fdv"), r.get("liquidity_usd"))
    if impact is not None and impact >= 70:
        hit(20, f"It could drop {impact:.0f}% if the top holder sells.")
    elif impact is not None and impact >= 30:
        hit(10, f"It could drop {impact:.0f}% if the top holder sells.")

    rh = r.get("real_hands_pct")
    if rh is not None and rh < 50:
        hit(20, "Most holders look like bots or linked wallets.")
    elif rh is not None and rh < 80:
        hit(8, "Some holders look like bots or linked wallets.")

    liq = r.get("liquidity_usd")
    if liq is None:
        hit(10, "We couldn't find a trading pool for it.")
    elif liq < 20_000:
        hit(25, "Hard to sell at any price — very little liquidity.")
    elif liq < 100_000:
        hit(10, "Large sells will move the price a lot.")

    hc = r.get("holders_count")
    if hc is not None and hc < 200:
        hit(10, "Very few people own it.")
    if r.get("has_site") is False and r.get("has_twitter") is False:
        hit(5, "No website or Twitter found.")

    score = max(0, min(100, score))
    verdict = DANGER if critical or score < 40 else RISKY if score < 70 else CLEAN
    return Assessment(score, verdict, risks, critical)
