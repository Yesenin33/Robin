"""Turns an intelligence scan into the plain-language verdict shown on the Safety screen."""
import asyncio
import dataclasses
import json
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import HoldersSnapshot, SafetyScore, Token
from app.schemas.safety import NO_DATA_HINT as NO_DATA
from app.schemas.safety import NO_VALUE, Metric, SafetyOut
from app.services.engine import ScanEngine, ScanReport
from app.services.intelligence import ScanUnavailable
from app.services.intelligence.http import set_priority
from app.services.intelligence.scoring import assess, sell_impact_pct
from app.services.rpc import norm
from app.services.verdict_guard import effective_verdict, missing_metrics

log = get_logger(__name__)

CLEAN, RISKY, DANGER, UNKNOWN = "CLEAN", "RISKY", "DANGER", "UNKNOWN"
OK, WARN, BAD, NA = "ok", "warn", "bad", "unknown"


class ScanInProgress(Exception):
    pass


def is_incomplete(report: ScanReport, holders_stale_at: datetime | None) -> bool:
    """A scan that produced a verdict but lacks what the Safety screen would show as dashes (holders, token
    identity) because a source failed. Worth a retry; a token that simply has no pool is not incomplete."""
    return holders_stale_at is not None or (
        bool(report.errors) and (report.holders_count is None or report.symbol is None or report.verdict == UNKNOWN))


# ---------- presentation (unit-tested) ----------
# Score, verdict and risk lines come from intelligence.scoring via the scan result;
# this module only turns them into the Safety screen: summary + exactly 4 metrics.

CLEAN_PARTIAL = "No red flags in what we could check. We couldn't work out how far the price could fall if a big holder sells."
SUMMARY = {
    CLEAN: "Looks healthy. Lots of real people hold it and no one holder can move the price much.",
    RISKY: "Proceed with care.",
    DANGER: "Very likely a scam. Avoid.",
    UNKNOWN: "We couldn't check this one. Some of the data we need wasn't available, so don't treat it as safe — try again in a minute.",
}


def _money(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def build_metrics(r: ScanReport, impact: float | None) -> list[Metric]:
    m: list[Metric] = []
    hc = r.holders_count
    m.append(Metric(key="holders", label="Holders", emoji="👥", value=f"{hc:,}" if hc is not None else NO_VALUE,
                    tone=NA if hc is None else BAD if hc < 200 else OK,
                    hint=NO_DATA if hc is None else "Very few owners" if hc < 200 else "People who own it"))
    rh = r.real_hands_pct
    rh_tone = NA if rh is None else OK if rh >= 80 else WARN if rh >= 50 else BAD
    rh_hint = (NO_DATA if rh is None else "Not bots or linked wallets" if rh_tone == OK
               else f"About {round((100 - rh) / 10)} in 10 look like bots" if rh_tone == WARN else "Most look fake")
    m.append(Metric(key="real_hands", label="Real hands", emoji="✋", value=f"{rh:.0f}%" if rh is not None else NO_VALUE,
                    tone=rh_tone, hint=rh_hint))
    it = NA if impact is None else OK if impact < 20 else WARN if impact < 70 else BAD
    imp = None if impact is None else max(0, round(impact))  # round first, then clamp: never "-0%"
    m.append(Metric(key="top_holder_sell", label="If top holder sells", emoji="📉",
                    value=NO_VALUE if imp is None else (f"-{imp}%" if imp > 0 else "<1%"), tone=it,
                    hint=(NO_DATA if imp is None else "Price could nearly vanish" if it == BAD
                          else f"Price could drop about {imp}%" if imp >= 1 else "Price barely moves")))
    liq = r.liquidity_usd
    lt = NA if liq is None else OK if liq >= 500_000 else WARN if liq >= 20_000 else BAD
    m.append(Metric(key="liquidity", label="Liquidity", emoji="💧", value=_money(liq) if liq is not None else NO_VALUE,
                    tone=lt, hint={OK: "Easy to sell without a big loss", WARN: "Large sells will move the price",
                                   BAD: "Hard to sell at any price", NA: NO_DATA}[lt]))
    return m


def evaluate(r: ScanReport) -> SafetyOut:
    impact = sell_impact_pct(r.top_holder_pct, r.fdv, r.liquidity_usd)
    risks = list(r.risks)
    score, verdict = r.safety_score, r.verdict
    summary = SUMMARY[verdict]
    if verdict == CLEAN and impact is None:
        # the usual CLEAN sentence promises that no single holder can move the price; without FDV we can't say so
        summary = CLEAN_PARTIAL
    if verdict == RISKY and risks:
        summary = f"Proceed with care. {risks[0]}"
    elif verdict == DANGER and risks:
        summary = f"Very likely a scam. {' '.join(risks[:2])} Avoid."
    return SafetyOut(
        address=r.address, symbol=r.symbol, name=r.name, score=score, verdict=verdict,
        swarm_verdict=None, summary=summary, risks=risks, metrics=build_metrics(r, impact),
        buy_recommended=verdict in (CLEAN, RISKY), price_usd=r.price_usd, scanned_at=datetime.now(timezone.utc),
    )


# ---------- orchestration ----------

class SafetyService:
    def __init__(self, db: AsyncSession, redis: Redis, engine: ScanEngine):
        self.db, self.redis, self.engine = db, redis, engine
        self.s = get_settings()
        self.last_report: ScanReport | None = None  # the most recent scan() of this instance
        self.last_incomplete = False

    @staticmethod
    def cache_key(ca: str) -> str:
        return f"safety:{ca}"

    async def get(self, ca: str, force: bool = False) -> SafetyOut:
        ca = norm(ca)
        # POST /safety/{ca}/refresh sets this marker: neither the Redis entry nor the "recent DB row"
        # shortcut may answer until the new scan has landed (the scan clears the marker).
        refresh_requested = bool(await self.redis.exists(f"safety:stale:{ca}"))
        if not force and not refresh_requested:
            if cached := await self.redis.get(self.cache_key(ca)):
                return SafetyOut.model_validate_json(cached)
            if fresh := await self._fresh_from_db(ca):
                await self._cache(fresh)
                return fresh
        try:
            return await self.scan(ca, priority=refresh_requested)
        except (ScanUnavailable, TimeoutError):
            if refresh_requested and (previous := await self._fresh_from_db(ca, max_age=False)):
                previous.stale = True  # a failed refresh must not turn a known result into an error
                return previous
            raise

    async def scan(self, ca: str, priority: bool = False) -> SafetyOut:
        lock = f"lock:scan:{ca}"
        if not await self.redis.set(lock, "1", nx=True, ex=self.s.scan_timeout_s + 5):
            raise ScanInProgress(ca)
        try:
            def run() -> ScanReport:
                set_priority(priority)  # user-requested scans take explorer slots ahead of bulk ones
                return self.engine.scan(ca)

            report = await asyncio.wait_for(asyncio.to_thread(run), self.s.scan_timeout_s)
            holders_stale_at = await self._fill_stale_holders(report)
            out = evaluate(report)
            out.holders_stale_at = holders_stale_at
            self.last_report = report
            self.last_incomplete = is_incomplete(report, holders_stale_at)
            await self._persist(report, out)
            # an incomplete result is shown briefly, then re-scanned: it must not sit in the cache for 10 minutes
            await self._cache(out, ttl=60 if self.last_incomplete else None)
            await self.redis.delete(f"safety:stale:{ca}")
            log.info("safety_scanned", ca=ca, engine=report.engine or self.engine.name, sources=report.sources,
                     errors=report.errors, score=out.score, verdict=out.verdict)
            return out
        finally:
            await self.redis.delete(lock)

    async def _fill_stale_holders(self, r: ScanReport) -> datetime | None:
        """Explorer throttled / down -> holders came back empty. Show the last good snapshot instead of dashes
        (and re-score with it). Returns when that snapshot was taken, or None if nothing was substituted."""
        if r.holders_count is not None or r.real_hands_pct is not None or r.top_holder_pct is not None:
            return None
        row = (await self.db.execute(
            select(HoldersSnapshot).join(Token, Token.id == HoldersSnapshot.token_id)
            .where(Token.address == r.address, HoldersSnapshot.holders_count.is_not(None))
            .order_by(HoldersSnapshot.ts.desc()).limit(1)
        )).scalar_one_or_none()
        if row is None:
            return None
        r.holders_count = row.holders_count
        r.real_hands_pct = float(row.real_hands_pct) if row.real_hands_pct is not None else None
        r.top_holder_pct = float(row.top_holder_pct) if row.top_holder_pct is not None else None
        r.top_holders, r.operators = row.top_holders or [], row.operators or []
        a = assess({k: v for k, v in dataclasses.asdict(r).items() if k != "raw"})
        r.safety_score, r.verdict, r.risks = a.score, a.verdict, a.risks
        ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=timezone.utc)
        r.errors.append(f"holders: showing last good snapshot from {ts:%Y-%m-%d %H:%M} UTC (stale)")
        return ts

    async def _cache(self, out: SafetyOut, ttl: int | None = None) -> None:
        await self.redis.set(self.cache_key(out.address), out.model_dump_json(), ex=ttl or self.s.safety_cache_ttl_s)

    async def _fresh_from_db(self, ca: str, max_age: bool = True) -> SafetyOut | None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.s.safety_stale_min)
        row = (await self.db.execute(
            select(SafetyScore, Token).join(Token, Token.id == SafetyScore.token_id)
            .where(Token.address == ca).order_by(SafetyScore.ts.desc()).limit(1)
        )).first()
        if not row:
            return None
        score, token = row
        ts = score.ts if score.ts.tzinfo else score.ts.replace(tzinfo=timezone.utc)
        if max_age and ts < cutoff:
            return None
        # an incomplete row (dashes) is only served while the token is in backoff; otherwise re-scan it
        if max_age and score.metrics.get("incomplete") and not await self.redis.exists(f"scan:cool:{ca}"):
            return None
        verdict, risks, summary, sc = score.verdict, list(score.risks or []), None, score.score
        if effective_verdict(verdict, score.metrics) != verdict:  # optimistic legacy row: metrics are missing
            gone = ", ".join(missing_metrics(score.metrics)).replace("_", " ")
            verdict, sc, summary = UNKNOWN, None, SUMMARY[UNKNOWN]
            risks = [f"We couldn't check this token: {gone} unavailable.", *risks]
            if max_age and not await self.redis.exists(f"scan:cool:{ca}"):
                return None  # re-scan it under the current rules instead of serving the old claim
        return SafetyOut(
            address=ca, symbol=token.symbol, name=token.name, score=sc if verdict == UNKNOWN else score.score,
            verdict=verdict, swarm_verdict=score.swarm_verdict,
            summary=summary or score.metrics.get("summary", SUMMARY[score.verdict]),
            risks=risks, metrics=[Metric(**m) for m in score.metrics.get("items", [])],
            buy_recommended=verdict in (CLEAN, RISKY),
            price_usd=float(token.price_usd) if token.price_usd is not None else None, scanned_at=ts,
            holders_stale_at=score.metrics.get("holders_stale_at"),
        )

    async def _persist(self, r: ScanReport, out: SafetyOut) -> None:
        token = await upsert_token(self.db, r)
        previous = (await self.db.execute(
            select(SafetyScore.verdict).where(SafetyScore.token_id == token.id).order_by(SafetyScore.ts.desc()).limit(1)
        )).scalar_one_or_none()
        if out.holders_stale_at is None and r.holders_count is not None:  # never store substituted/empty data
            self.db.add(HoldersSnapshot(
                token_id=token.id, holders_count=r.holders_count, real_hands_pct=r.real_hands_pct,
                top_holder_pct=r.top_holder_pct, top_holders=r.top_holders, operators=r.operators,
            ))
        self.db.add(SafetyScore(
            token_id=token.id, score=out.score, verdict=out.verdict, swarm_verdict=out.swarm_verdict,
            risks=out.risks,
            metrics={"summary": out.summary, "items": [m.model_dump() for m in out.metrics],
                     "holders_stale_at": out.holders_stale_at.isoformat() if out.holders_stale_at else None,
                     "incomplete": self.last_incomplete},
            raw=json.loads(json.dumps(r.raw, default=str)),
        ))
        await self.db.commit()
        try:  # a verdict that got worse alerts holders; never fail the scan over it
            from app.services.alert_service import on_verdict

            await on_verdict(self.db, self.redis, token, previous, out.verdict, out.risks)
        except Exception as e:  # noqa: BLE001
            log.warning("verdict_alert_failed", ca=r.address, error=str(e))


async def upsert_token(db: AsyncSession, r: ScanReport) -> Token:
    token = (await db.execute(select(Token).where(Token.address == r.address))).scalar_one_or_none()
    if token is None:
        token = Token(address=r.address)
        db.add(token)
    token.symbol = r.symbol or token.symbol
    token.name = r.name or token.name
    token.decimals = r.decimals or token.decimals
    token.pair_address = r.pair_address or token.pair_address
    token.price_usd = r.price_usd if r.price_usd is not None else token.price_usd
    token.price_change_24h = r.price_change_24h if r.price_change_24h is not None else token.price_change_24h
    token.liquidity_usd = r.liquidity_usd if r.liquidity_usd is not None else token.liquidity_usd
    await db.flush()
    return token
