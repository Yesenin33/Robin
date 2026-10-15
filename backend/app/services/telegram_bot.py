"""@chainfolio_bot: linking + commands. Replies are plain English and never contain a raw chat id."""
import asyncio
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import User, Wallet
from app.schemas.safety import SafetyOut
from app.services import copy_service
from app.services.notify import DeliveryError, Sender

log = get_logger(__name__)

LINK_TTL = 600
ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")
CODE = re.compile(r"^\d{6}$")
VERDICT_ICON = {"CLEAN": "✅", "RISKY": "⚠️", "DANGER": "⛔"}

HELP = ("Here's what I can do:\n"
        "/portfolio — your balance and top holdings\n"
        "/safe 0x… — is a token safe? Paste its contract address\n"
        "/copy — latest moves by smart wallets\n"
        "/alerts — turn Telegram alerts on or off\n"
        "/stop — disconnect this chat\n"
        "/help — this list")
NOT_LINKED = "Connect your Chainfolio account first: tap “Open Chainfolio” below, or send /start for a code."


@dataclass
class BotContext:
    db: AsyncSession
    redis: Redis
    sender: Sender
    safety: Callable[[str], Awaitable[SafetyOut]] | None = None  # ca -> verdict
    portfolio: Callable[[str], Awaitable[object]] | None = None  # address -> PortfolioOut
    sent: list[dict] = field(default_factory=list)


def webapp_button() -> dict:
    return {"inline_keyboard": [[{"text": "Open Chainfolio", "web_app": {"url": get_settings().frontend_url}}]]}


def alerts_buttons(on: bool) -> dict:
    return {"inline_keyboard": [[{"text": "Turn alerts off" if on else "Turn alerts on",
                                  "callback_data": "alerts:off" if on else "alerts:on"}]]}


async def new_code(redis: Redis, prefix: str, value: str) -> str:
    for _ in range(20):
        code = f"{secrets.randbelow(10**6):06d}"
        if await redis.set(f"{prefix}:{code}", value, nx=True, ex=LINK_TTL):
            return code
    raise RuntimeError("could not allocate a code")


async def issue_link(redis: Redis, user_id: int) -> dict:
    """App -> bot: deep-link token + 6-digit code, both single use, 10 minutes."""
    s = get_settings()
    tok = secrets.token_urlsafe(24)
    await redis.set(f"tg:tok:{tok}", str(user_id), ex=LINK_TTL)
    code = await new_code(redis, "tg:ucode", str(user_id))
    return {"url": f"https://t.me/{s.telegram_bot_username}?start={tok}", "code": code, "expires_in": LINK_TTL}


async def link_chat(db: AsyncSession, user: User, chat_id: str) -> None:
    """Bind a chat to one user (a chat can belong to only one account)."""
    await db.execute(update(User).where(User.telegram_chat_id == chat_id, User.id != user.id).values(telegram_chat_id=None))
    user.telegram_chat_id = chat_id
    user.telegram_alerts = True
    await db.commit()


async def user_for_chat(db: AsyncSession, chat_id: str) -> User | None:
    return (await db.execute(select(User).where(User.telegram_chat_id == chat_id))).scalars().first()


# ---------- formatting ----------

def _usd(v: float) -> str:
    return f"${v:,.2f}"


def format_safety(s: SafetyOut) -> str:
    name = f"{s.name} ({s.symbol})" if s.name and s.symbol and s.name != s.symbol else (s.symbol or s.name or "This token")
    lines = [f"{name} — {VERDICT_ICON[s.verdict]} {s.verdict}", s.summary, ""]
    lines += [f"{m.emoji} {m.label}: {m.value} — {m.hint}" for m in s.metrics]
    if not s.buy_recommended:
        lines += ["", "Buying this one is not recommended."]
    return "\n".join(lines)


def format_portfolio(p) -> str:
    if not p.holdings:
        return "Your wallet has no tokens yet."
    arrow = "▲" if p.change_24h_usd >= 0 else "▼"
    lines = [f"Your portfolio: {_usd(p.total_usd)}",
             f"Today: {arrow} {'+' if p.change_24h_usd >= 0 else '-'}{_usd(abs(p.change_24h_usd))} "
             f"({p.change_24h_pct:+.1f}%)", "", "Top holdings:"]
    for h in p.holdings[:5]:
        chg = h.pnl_pct if h.pnl_pct is not None else h.change_24h_pct
        lines.append(f"• {h.symbol} — {_usd(h.value_usd)}" + (f" ({chg:+.1f}%)" if chg is not None else ""))
    return "\n".join(lines)


# ---------- dispatch ----------

async def reply(ctx: BotContext, chat_id: str, text: str, markup: dict | None = None) -> None:
    ctx.sent.append({"text": text, "markup": markup})
    try:
        await ctx.sender.telegram(chat_id, text, markup)
    except DeliveryError as e:
        log.warning("bot_reply_failed", error=str(e))


async def handle_update(update: dict, ctx: BotContext) -> None:
    if cq := update.get("callback_query"):
        await _callback(cq, ctx)
        return
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    text = (msg.get("text") or "").strip()
    if not text or "id" not in chat:
        return
    chat_id = str(chat["id"])
    if chat.get("type") != "private":
        await reply(ctx, chat_id, "Please message me directly — I only work in private chats.")
        return
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    arg = arg.strip()
    user = await user_for_chat(ctx.db, chat_id)

    if cmd == "/start":
        await _start(ctx, chat_id, user, arg)
    elif CODE.match(text):  # 6-digit code from the app
        uid = await ctx.redis.getdel(f"tg:ucode:{text}")
        target = await ctx.db.get(User, int(uid)) if uid else None
        if target is None:
            await reply(ctx, chat_id, "That code is wrong or expired. Get a new one in Chainfolio → Profile → Telegram alerts.")
            return
        await link_chat(ctx.db, target, chat_id)
        await reply(ctx, chat_id, "✅ Connected. Alerts for your wallet will arrive here.")
    elif cmd == "/portfolio":
        await _portfolio(ctx, chat_id, user)
    elif cmd == "/safe":
        await _safe(ctx, chat_id, arg)
    elif cmd == "/copy":
        await _copy(ctx, chat_id, user)
    elif cmd == "/alerts":
        if user is None:
            await reply(ctx, chat_id, NOT_LINKED, webapp_button())
        else:
            on = user.telegram_alerts
            await reply(ctx, chat_id, f"Telegram alerts are {'ON' if on else 'OFF'}.", alerts_buttons(on))
    elif cmd == "/stop":
        if user is None:
            await reply(ctx, chat_id, "This chat isn't connected to any account.")
        else:
            user.telegram_chat_id = None
            await ctx.db.commit()
            await reply(ctx, chat_id, "Done — this chat is disconnected and I won't send alerts here. Send /start to reconnect.")
    else:
        await reply(ctx, chat_id, HELP)


async def _start(ctx: BotContext, chat_id: str, user: User | None, token: str) -> None:
    if token:
        uid = await ctx.redis.getdel(f"tg:tok:{token}")
        target = await ctx.db.get(User, int(uid)) if uid else None
        if target is None:
            await reply(ctx, chat_id, "That link has expired. Open Chainfolio → Profile → Telegram alerts to get a new one.",
                        webapp_button())
            return
        await link_chat(ctx.db, target, chat_id)
        await reply(ctx, chat_id, "✅ Connected. Alerts for your wallet will arrive here.")
        return
    if user is not None:
        await reply(ctx, chat_id, "Welcome back! This chat is connected — alerts will arrive here.\n\n" + HELP, webapp_button())
        return
    code = await new_code(ctx.redis, "tg:chatcode", chat_id)
    await reply(ctx, chat_id,
                "Hi! I'm Chainfolio. I'll message you when a token you hold turns risky, its price jumps, "
                "or a wallet you follow makes a trade.\n\n"
                "To connect: tap “Open Chainfolio” below — that's it.\n"
                f"Or enter this code in the app under Profile → Telegram alerts: {code}", webapp_button())


async def _portfolio(ctx: BotContext, chat_id: str, user: User | None) -> None:
    if user is None:
        await reply(ctx, chat_id, NOT_LINKED, webapp_button())
        return
    wallet = (await ctx.db.execute(select(Wallet.address).where(Wallet.user_id == user.id).limit(1))).scalar_one_or_none()
    if wallet is None or ctx.portfolio is None:
        await reply(ctx, chat_id, "Add a wallet in Chainfolio first, then I can show your portfolio.", webapp_button())
        return
    try:
        p = await ctx.portfolio(wallet)
    except Exception:  # noqa: BLE001
        await reply(ctx, chat_id, "I couldn't reach your balance right now. Your money is safe — try again in a minute.")
        return
    await reply(ctx, chat_id, format_portfolio(p))


async def _safe(ctx: BotContext, chat_id: str, arg: str) -> None:
    if not ADDRESS.match(arg):
        await reply(ctx, chat_id, "Send it like this: /safe 0x… (the token's contract address).")
        return
    if ctx.safety is None:
        await reply(ctx, chat_id, "Safety checks are unavailable right now.")
        return
    try:
        s = await ctx.safety(arg.lower())
    except Exception:  # noqa: BLE001
        await reply(ctx, chat_id, "I couldn't run the safety check right now. Try again in a minute.")
        return
    await reply(ctx, chat_id, format_safety(s))


async def _copy(ctx: BotContext, chat_id: str, user: User | None) -> None:
    items = await copy_service.feed(ctx.db, user.id if user else 0, 5, only_followed=False)
    if not items:
        await reply(ctx, chat_id, "No smart-wallet moves yet. Follow wallets in Chainfolio to see them here.", webapp_button())
        return
    lines = ["Latest smart-wallet moves:"]
    for it in items:
        a = it["address"]
        lines.append(f"\n• {a[:6]}…{a[-4:]} — {it['reason']}")
    await reply(ctx, chat_id, "\n".join(lines))


async def _callback(cq: dict, ctx: BotContext) -> None:
    chat_id = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
    data = cq.get("data") or ""
    user = await user_for_chat(ctx.db, chat_id) if chat_id else None
    text = "Connect your account first."
    if user is not None and data in ("alerts:on", "alerts:off"):
        user.telegram_alerts = data == "alerts:on"
        await ctx.db.commit()
        text = f"Telegram alerts are {'ON' if user.telegram_alerts else 'OFF'}."
    if hasattr(ctx.sender, "bot_api"):
        try:
            await ctx.sender.bot_api("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": text})
            if user is not None and (m := cq.get("message")):
                await ctx.sender.bot_api("editMessageText", {
                    "chat_id": chat_id, "message_id": m.get("message_id"), "text": text,
                    "reply_markup": alerts_buttons(user.telegram_alerts)})
        except DeliveryError as e:
            log.warning("bot_callback_failed", error=str(e))
    ctx.sent.append({"text": text, "markup": None})


async def safety_lookup(db: AsyncSession, redis: Redis, ca: str, wait_s: float = 25) -> SafetyOut:
    """SafetyService.get, waiting for a scan already running elsewhere to land in the cache."""
    from app.services.engine import get_engine
    from app.services.safety_service import SafetyService, ScanInProgress

    svc = SafetyService(db, redis, get_engine())
    deadline = asyncio.get_running_loop().time() + wait_s
    while True:
        try:
            return await svc.get(ca)
        except ScanInProgress:
            if asyncio.get_running_loop().time() > deadline:
                raise
            await asyncio.sleep(1.5)
