"""Telegram: Mini App initData auth, deep-link + code linking, webhook, bot commands."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from app.api import telegram as tg_api
from app.api.alerts import get_sender
from app.core.config import get_settings
from app.main import app
from app.models import Alert, User
from app.schemas.safety import Metric, SafetyOut
from app.services import alert_service, telegram_bot
from app.services.telegram_auth import InitDataError, validate_init_data
from tests.test_alerts import FakeSender

TOKEN = "123456:TEST-bot-token"
SECRET = "w" * 40
CHAT = 8122200450


def init_data(user: dict, age: int = 0, token: str = TOKEN, extra: dict | None = None) -> str:
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "AAH", "user": json.dumps(user), **(extra or {})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


@pytest.fixture
def bot(monkeypatch, client):
    s = get_settings()
    for k, v in {"telegram_bot_token": TOKEN, "telegram_bot_username": "chainfolio_bot",
                 "telegram_webhook_secret": SECRET, "frontend_url": "https://212.113.101.238.nip.io"}.items():
        monkeypatch.setattr(s, k, v)
    sender = FakeSender()
    app.dependency_overrides[get_sender] = lambda: sender
    monkeypatch.setattr(tg_api, "_sender", sender)
    return sender


async def send(client, text: str, chat: int = CHAT, update_id: list = [0]) -> int:  # noqa: B006
    update_id[0] += 1
    r = await client.post(f"/telegram/webhook/{SECRET}", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
                          json={"update_id": update_id[0], "message": {"message_id": 1, "text": text,
                                                                      "chat": {"id": chat, "type": "private"}}})
    return r.status_code


# ---------- initData ----------

def test_init_data_valid_bad_and_expired():
    user = {"id": CHAT, "username": "alex", "allows_write_to_pm": True}
    assert validate_init_data(init_data(user), TOKEN)["id"] == CHAT
    assert validate_init_data(init_data(user, extra={"signature": "sig"}), TOKEN)["username"] == "alex"
    with pytest.raises(InitDataError, match="bad hash"):
        validate_init_data(init_data(user, token="999:other"), TOKEN)
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(init_data(user, age=301), TOKEN)
    with pytest.raises(InitDataError):
        validate_init_data(init_data(user).replace("alex", "mallory"), TOKEN)  # tampered


# ---------- C1: Mini App ----------

async def test_mini_app_fresh_user_is_signed_in_and_connected(client, bot):
    r = await client.post("/alerts/telegram/link-webapp", json={"init_data": init_data({"id": CHAT, "username": "alex"})})
    body = r.json()
    assert r.status_code == 200 and body["linked"] and body["chat"] == "@alex" and body["can_message"]
    assert body["access_token"] and str(CHAT) not in json.dumps(body)
    ch = (await client.get("/alerts/channels", headers={"Authorization": f"Bearer {body['access_token']}"})).json()
    assert ch["telegram"] is True and ch["telegram_alerts"] is True
    assert bot.tg == [(str(CHAT), "✅ Connected. Alerts for your wallet will arrive here.")]
    # opening the Mini App again signs in to the same account
    again = (await client.post("/alerts/telegram/link-webapp", json={"init_data": init_data({"id": CHAT})})).json()
    assert again["chat"] == "your Telegram"  # no username or first name: generic, never the id
    from app.core.security import decode_jwt
    assert decode_jwt(again["access_token"]) == decode_jwt(body["access_token"])


async def test_mini_app_links_signed_in_wallet_account(client, bot, signed_in, appdb):
    headers, address, _ = signed_in
    r = await client.post("/alerts/telegram/link-webapp", json={"init_data": init_data({"id": CHAT, "first_name": "Alex"})},
                          headers=headers)
    assert r.json() == {"linked": True, "chat": "Alex", "can_message": True, "access_token": None}
    assert (await client.get("/alerts/channels", headers=headers)).json()["telegram"] is True


async def test_mini_app_rejects_forged_init_data(client, bot):
    r = await client.post("/alerts/telegram/link-webapp", json={"init_data": init_data({"id": CHAT}, token="1:forged")})
    assert r.status_code == 401


# ---------- C2: deep link + code ----------

async def test_deep_link_start_token_links_once(client, bot, signed_in):
    headers, _, _ = signed_in
    link = (await client.post("/alerts/telegram/link", headers=headers)).json()
    assert link["url"].startswith("https://t.me/chainfolio_bot?start=") and len(link["code"]) == 6
    assert link["expires_in"] == 600
    tok = link["url"].split("start=")[1]
    assert await send(client, f"/start {tok}") == 200
    assert bot.tg[-1][1] == "✅ Connected. Alerts for your wallet will arrive here."
    assert (await client.get("/alerts/channels", headers=headers)).json()["telegram"] is True
    await send(client, f"/start {tok}", chat=CHAT + 1)  # single use
    assert bot.tg[-1][1].startswith("That link has expired")


async def test_code_from_app_typed_into_bot(client, bot, signed_in):
    headers, _, _ = signed_in
    code = (await client.post("/alerts/telegram/link", headers=headers)).json()["code"]
    await send(client, code)
    assert bot.tg[-1][1].startswith("✅ Connected")
    assert (await client.get("/alerts/channels", headers=headers)).json()["telegram"] is True


async def test_plain_start_gives_code_and_app_button_then_app_verifies(client, bot, signed_in):
    headers, _, _ = signed_in
    await send(client, "/start")
    text, markup = bot.tg[-1][1], bot.markups[-1]
    assert "tap “Open Chainfolio” below" in text and str(CHAT) not in text
    assert markup == {"inline_keyboard": [[{"text": "Open Chainfolio", "web_app": {"url": "https://212.113.101.238.nip.io"}}]]}
    code = text.rsplit(": ", 1)[1]
    r = await client.post("/alerts/telegram/verify", json={"code": code}, headers=headers)
    assert r.status_code == 200 and r.json()["telegram"] is True


async def test_old_chat_id_endpoint_is_gone(client, bot, signed_in):
    headers, _, _ = signed_in
    assert (await client.post("/alerts/telegram/start", json={"chat_id": "1"}, headers=headers)).status_code in (404, 405)


# ---------- C3: commands ----------

async def link_via_bot(client, headers):
    tok = (await client.post("/alerts/telegram/link", headers=headers)).json()["url"].split("start=")[1]
    await send(client, f"/start {tok}")


async def test_webhook_rejects_wrong_secret(client, bot):
    r = await client.post(f"/telegram/webhook/{SECRET}", headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}, json={})
    assert r.status_code == 404
    assert (await client.post("/telegram/webhook/guess", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}, json={})).status_code == 404


async def test_safe_command_replies_with_verdict_and_four_metrics(client, bot, monkeypatch):
    out = SafetyOut(address="0x05517f08e0bb2d7d8ab25f98e5c2828fdba6386c", symbol="HOODX", name="Hood X", score=82,
                    verdict="CLEAN", summary="Looks healthy.", risks=[], buy_recommended=True, scanned_at="2026-10-17T00:00:00Z",
                    metrics=[Metric(key=k, label=l, emoji=e, value=v, tone="ok", hint=h) for k, l, e, v, h in [
                        ("holders", "Holders", "👥", "18,420", "People who own it"),
                        ("real_hands", "Real hands", "✋", "94%", "Not bots or linked wallets"),
                        ("top_holder_sell", "If top holder sells", "📉", "-6%", "Price could drop about 6%"),
                        ("liquidity", "Liquidity", "💧", "$2.4M", "Easy to sell without a big loss")]])

    async def lookup(db, redis, ca, wait_s=25):
        assert ca == "0x05517f08e0bb2d7d8ab25f98e5c2828fdba6386c"
        return out

    monkeypatch.setattr(telegram_bot, "safety_lookup", lookup)
    await send(client, "/safe 0x05517f08e0bb2d7d8ab25f98e5c2828fdba6386c")
    assert bot.tg[-1][1] == ("Hood X (HOODX) — ✅ CLEAN\nLooks healthy.\n\n"
                             "👥 Holders: 18,420 — People who own it\n✋ Real hands: 94% — Not bots or linked wallets\n"
                             "📉 If top holder sells: -6% — Price could drop about 6%\n"
                             "💧 Liquidity: $2.4M — Easy to sell without a big loss")
    await send(client, "/safe hello")
    assert bot.tg[-1][1] == "Send it like this: /safe 0x… (the token's contract address)."


async def test_portfolio_needs_link_then_shows_totals(client, bot, signed_in, monkeypatch):
    from app.schemas.portfolio import Holding, PortfolioOut

    await send(client, "/portfolio")
    assert bot.tg[-1][1] == telegram_bot.NOT_LINKED
    headers, address, _ = signed_in
    await link_via_bot(client, headers)

    class FakePortfolio:
        def __init__(self, *a):
            pass

        async def get(self, addr):
            assert addr == address
            return PortfolioOut(address=addr, total_usd=12480.52, change_24h_usd=312.4, change_24h_pct=2.6,
                                updated_at="2026-10-17T00:00:00Z", holdings=[Holding(
                                    token="0x1", symbol="HOODX", name=None, amount=4.2, price_usd=1247.6, value_usd=5240.1,
                                    change_24h_pct=3.0, pnl_pct=8.4)])

    monkeypatch.setattr(tg_api, "PortfolioService", FakePortfolio)
    await send(client, "/portfolio")
    assert bot.tg[-1][1] == "Your portfolio: $12,480.52\nToday: ▲ +$312.40 (+2.6%)\n\nTop holdings:\n• HOODX — $5,240.10 (+8.4%)"


async def test_alerts_toggle_stop_and_help(client, bot, signed_in, appdb, redis):
    headers, _, _ = signed_in
    await link_via_bot(client, headers)
    await send(client, "/alerts")
    assert bot.tg[-1][1] == "Telegram alerts are ON." and bot.markups[-1]["inline_keyboard"][0][0]["callback_data"] == "alerts:off"
    await client.post(f"/telegram/webhook/{SECRET}", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}, json={
        "update_id": 9999, "callback_query": {"id": "cb1", "data": "alerts:off",
                                              "message": {"message_id": 5, "chat": {"id": CHAT, "type": "private"}}}})
    assert ("answerCallbackQuery", {"callback_query_id": "cb1", "text": "Telegram alerts are OFF."}) in bot.api
    async with appdb() as db:
        user = (await db.execute(select(User).where(User.telegram_chat_id == str(CHAT)))).scalar_one()
        assert user.telegram_alerts is False
        db.add(Alert(user_id=user.id, kind="price", title="t", body="b", payload={}))
        await db.commit()
        n = len(bot.tg)
        await alert_service.deliver_pending(db, redis, bot)  # alerts off -> nothing sent to Telegram
        assert len(bot.tg) == n
    await send(client, "/help")
    assert bot.tg[-1][1] == telegram_bot.HELP
    await send(client, "/stop")
    assert bot.tg[-1][1].startswith("Done — this chat is disconnected")
    assert (await client.get("/alerts/channels", headers=headers)).json()["telegram"] is False


async def test_blocked_bot_unlinks_and_stops_sending(appdb, redis, monkeypatch):
    from app.services.notify import DeliveryError

    monkeypatch.setattr(get_settings(), "telegram_bot_token", TOKEN)

    class Blocked(FakeSender):
        async def telegram(self, chat_id, text, reply_markup=None):
            raise DeliveryError("Forbidden: bot was blocked by the user", gone=True)

    async with appdb() as db:
        u = User(telegram_chat_id=str(CHAT))
        db.add(u)
        await db.flush()
        db.add(Alert(user_id=u.id, kind="price", title="t", body="b", payload={}))
        await db.commit()
        await alert_service.deliver_pending(db, redis, Blocked())
        await db.refresh(u)
        assert u.telegram_chat_id is None
        # next run: no chat -> no retry
        assert await alert_service.deliver_pending(db, redis, Blocked()) == {"telegram": 0, "push": 0, "throttled": 0, "failed": 0}


async def test_no_reply_ever_contains_the_chat_id(client, bot, signed_in):
    headers, _, _ = signed_in
    for text in ("/start", "/help", "/alerts", "/copy", "/portfolio", "/stop", "hello"):
        await send(client, text)
    await link_via_bot(client, headers)
    for text in ("/alerts", "/copy", "/stop"):
        await send(client, text)
    assert all(str(CHAT) not in t for _, t in bot.tg)
