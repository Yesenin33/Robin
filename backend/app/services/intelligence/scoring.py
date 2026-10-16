"""0-100 safety score, verdict and plain-language risks — derived ONLY from the normalized result.

The score starts at 100 and loses points for problems we DETECT, so "we couldn't look" would read as "safe".
To prevent that, a verdict needs a minimum evidence bar (see `evidence_gaps`). Below it the assessment is
UNKNOWN with score None — never CLEAN or RISKY. A detected danger (honeypot, a wallet owning most of it, a
very low score from real findings) still shows as DANGER: missing data must not hide a threat.
"""
from dataclasses import dataclass

CLEAN, RISKY, DANGER, UNKNOWN = "CLEAN", "RISKY", "DANGER", "UNKNOWN"


@dataclass
class Assessment:
    score: int | None  # None when the verdict is UNKNOWN
    verdict: str
    risks: list[str]
    critical: bool


def evidence_gaps(r: dict) -> list[str]:
    """What we still need before we may call a token CLEAN or RISKY. Empty list = the evidence bar is met.

    The bar: the market lookup worked and found a trading pool (liquidity known), holders are known AND broken
    down (top holder, real hands), and the contract itself could be read. `errors` carries the failed stages
    (market:, meta:, security:). Beyond the bar, an unobserved sell caps the verdict at RISKY (see assess)."""
    errors = r.get("errors") or []
    failed = lambda *stages: any(str(e).split(":", 1)[0] in stages for e in errors)  # noqa: E731
    gaps = []
    if failed("market"):
        gaps.append("market data")
    elif r.get("liquidity_usd") is None:
        gaps.append("trading pool info")  # market worked but found no pool
    if r.get("holders_count") is None:
        gaps.append("holder data")
    elif r.get("top_holder_pct") is None or r.get("real_hands_pct") is None:
        gaps.append("holder breakdown")  # we know how many holders, not who they are or whether they are linked
    if failed("meta", "security"):
        gaps.append("contract details")
    return gaps


def unknown_note(gaps: list[str]) -> str:
    names = gaps[0] if len(gaps) == 1 else ", ".join(gaps[:-1]) + " and " + gaps[-1]
    return f"We couldn't check this token: {names} {'was' if len(gaps) == 1 else 'were'} unavailable."


def sell_impact_pct(top_holder_pct: float | None, fdv: float | None, liquidity_usd: float | None) -> float | None:
    """Price drop if the top operator market-sells into a constant-product (x*y=k) pool.

    pool quote side Q = L/2; seller dumps X = top% * FDV of value; price_after/price_before = (Q/(Q+X))^2
    """
    if top_holder_pct is None or not fdv or liquidity_usd is None:
        return None  # a missing input is unknown, not "no impact"
    if liquidity_usd <= 0:
        return 100.0  # a known empty pool: any sell wipes out the price
    q = liquidity_usd / 2
    x = top_holder_pct / 100 * fdv
    return round((1 - (q / (q + x)) ** 2) * 100, 1)


def assess(r: dict) -> Assessment:
    score = 100
    risks: list[str] = []
    critical = False
    cap_at_risky = False
    renounced = r.get("owner_renounced")

    def hit(points: int, line: str, crit: bool = False) -> None:
        nonlocal score, critical
        score -= points
        risks.append(line)
        critical = critical or crit

    if r.get("honeypot"):
        hit(60, "You may not be able to sell this token.", True)
    elif r.get("honeypot") is None:
        # Nobody was seen selling (and too few buys to say it is stuck): we cannot confirm it can be sold.
        # Absent evidence of a honeypot is not evidence that it is not one, so it can never be CLEAN.
        cap_at_risky = True
        risks.append("We haven't seen anyone sell it recently, so we can't confirm you could sell it.")
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

    liq = r.get("liquidity_usd")  # None is an evidence gap (handled below), not a point deduction
    if liq is None:
        pass
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
    if cap_at_risky:
        score = min(score, 69)
    verdict = DANGER if critical or score < 40 else RISKY if score < 70 else CLEAN
    if gaps := evidence_gaps(r):
        note = unknown_note(gaps)
        if verdict == DANGER:  # a real finding stands even when other data is missing
            return Assessment(score, DANGER, [*risks, note], critical)
        return Assessment(None, UNKNOWN, [note, *risks], False)
    return Assessment(score, verdict, risks, critical)
