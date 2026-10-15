"""Alerts: generation (verdict drop, price move), delivery (Telegram/Web Push, quota), API, Telegram linking."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.api.alerts import get_sender
from app.core.config import get_settings
from app.main import app
from app.models import Alert, Position, PushSubscription, Token, User, Wallet
from app.services import alert_service
from app.services.notify import DeliveryError
from app.services.safety_service import SafetyService
from tests.test_safety_service import FakeEngine, report

CA = "0x" + "ab" * 20


class FakeSender:
    def __init__(self, fail_push_gone=False):
        self.tg: list[tuple[str, str]] = []
        self.push: list[dict] = []
        self.fail_push_gone = fail_push_gone

    async def telegram(self, chat_id, text):
        self.tg.append((chat_id, text))
        return 1000 + len(self.tg)

    async def webpush(self, sub, data):
        if self.fail_push_gone:
            raise DeliveryError("410 Gone", gone=True)
        self.push.append({"sub": sub, "data": data})


@pytest.fixture
def channels(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "telegram_bot_token", "test-token")
    monkeypatch.setattr(s, "vapid_private_key", "k")
    monkeypatch.setattr(s, "vapid_public_key", "pub")
    monkeypatch.setattr(s, "alerts_per_hour", 2)


async def holder(maker, address: str, token: str = CA, chat: str | None = "8122200450") -> int:
    async with maker() as db:
        u = User(email=None, telegram_chat_id=chat)
        db.add(u)
        await db.flush()
        db.add(Wallet(user_id=u.id, address=address))
        db.add(Position(wallet_address=address, token_address=token, amount=10, ts=datetime.now(timezone.utc)))
        await db.commit()
        return u.id


async def test_verdict_drop_alerts_holders_once(appdb, redis):
    uid = await holder(appdb, "0x" + "11" * 20)
    async with appdb() as db:
        await SafetyService(db, redis, FakeEngine(report())).scan(CA)  # CLEAN baseline: no alert
        await SafetyService(db, redis, FakeEngine(report(honeypot=True))).scan(CA)  # -> DANGER
        await SafetyService(db, redis, FakeEngine(report(honeypot=True))).scan(CA)  # same verdict: no repeat
        alerts = (await db.execute(select(Alert))).scalars().all()
    assert len(alerts) == 1
    a = alerts[0]
    assert (a.user_id, a.kind, a.title) == (uid, "safety_drop", "HOOD-X is now DANGER")
    assert a.body == "Our safety check moved HOOD-X from CLEAN to DANGER. You may not be able to sell this token."


async def test_price_move_alert_after_baseline(appdb, redis):
    uid = await holder(appdb, "0x" + "12" * 20)
    async with appdb() as db:
        db.add(Token(address=CA, symbol="HOODX", price_usd=1.0))
        await db.commit()
        assert await alert_service.price_moves(db, redis, 20) == 0  # sets baseline
        (await db.execute(select(Token))).scalar_one().price_usd = 1.3
        await db.commit()
        assert await alert_service.price_moves(db, redis, 20) == 1
        a = (await db.execute(select(Alert))).scalar_one()
    assert (a.user_id, a.title) == (uid, "HOODX up 30%")


async def test_delivery_telegram_push_and_quota(appdb, redis, channels):
    uid = await holder(appdb, "0x" + "13" * 20)
    async with appdb() as db:
        db.add(PushSubscription(user_id=uid, endpoint="https://push.example/1", p256dh="p", auth="a"))
        for i in range(3):
            db.add(Alert(user_id=uid, kind="price", title=f"t{i}", body="b", payload={"url": "/app/portfolio"}))
        await db.commit()
        sender = FakeSender()
        stats = await alert_service.deliver_pending(db, redis, sender)
        alerts = (await db.execute(select(Alert).order_by(Alert.id))).scalars().all()
    assert stats == {"telegram": 2, "push": 2, "throttled": 1, "failed": 0}  # quota = 2 per hour
    assert [c for c, _ in sender.tg] == ["8122200450", "8122200450"]
    assert sender.push[0]["data"] == {"title": "t0", "body": "b", "url": "/app/portfolio"}
    assert alerts[0].payload["tg_message_id"] == 1001 and alerts[0].delivered_tg_at and alerts[0].delivered_push_at
    assert alerts[2].payload["throttled"] is True and alerts[2].delivered_tg_at is None
    async with appdb() as db:  # second run: nothing left to send
        assert await alert_service.deliver_pending(db, redis, sender) == {"telegram": 0, "push": 0, "throttled": 0, "failed": 0}


async def test_expired_push_subscription_is_dropped(appdb, redis, channels):
    uid = await holder(appdb, "0x" + "14" * 20, chat=None)
    async with appdb() as db:
        db.add(PushSubscription(user_id=uid, endpoint="https://push.example/gone", p256dh="p", auth="a"))
        db.add(Alert(user_id=uid, kind="price", title="t", body="b", payload={}))
        await db.commit()
        stats = await alert_service.deliver_pending(db, redis, FakeSender(fail_push_gone=True))
        assert stats["failed"] == 1
        assert (await db.execute(select(PushSubscription))).scalars().all() == []


async def test_alerts_api_list_and_read(client, signed_in, appdb):
    headers, _, _ = signed_in
    uid = (await client.get("/auth/me", headers=headers)).json()["id"]
    async with appdb() as db:
        db.add_all([Alert(user_id=uid, kind="copy_event", title="a", body="b", payload={"url": "/app/copy"}),
                    Alert(user_id=uid + 999, kind="price", title="other user", body="b")])
        await db.commit()
    body = (await client.get("/alerts", headers=headers)).json()
    assert body["unread"] == 1 and [i["title"] for i in body["items"]] == ["a"] and body["items"][0]["url"] == "/app/copy"
    aid = body["items"][0]["id"]
    assert (await client.post(f"/alerts/{aid}/read", headers=headers)).status_code == 204
    assert (await client.get("/alerts", headers=headers)).json()["unread"] == 0
    assert (await client.post(f"/alerts/{aid + 1}/read", headers=headers)).status_code == 404  # someone else's


async def test_telegram_link_by_code(client, signed_in, redis, channels):
    headers, _, _ = signed_in
    sender = FakeSender()
    app.dependency_overrides[get_sender] = lambda: sender
    r = await client.post("/alerts/telegram/start", json={"chat_id": "8122200450"}, headers=headers)
    assert r.status_code == 202 and sender.tg[0][0] == "8122200450"
    code = sender.tg[0][1].split("code: ")[1][:6]
    bad = await client.post("/alerts/telegram/verify", json={"code": "000000" if code != "000000" else "111111"}, headers=headers)
    assert bad.status_code == 400
    ok = await client.post("/alerts/telegram/verify", json={"code": code}, headers=headers)
    assert ok.status_code == 200 and ok.json()["telegram"] is True


async def test_push_subscribe_and_key(client, signed_in, channels):
    headers, _, _ = signed_in
    assert (await client.get("/alerts/push/key")).json() == {"public_key": "pub"}
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "BPx", "auth": "xyz"}}
    assert (await client.post("/alerts/push/subscribe", json=sub, headers=headers)).status_code == 204
    assert (await client.get("/alerts/channels", headers=headers)).json()["push"] is True


async def test_telegram_network_error_does_not_leak_token(monkeypatch):
    import httpx

    from app.services.notify import LiveSender

    monkeypatch.setattr(get_settings(), "telegram_bot_token", "123:SECRET")

    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    sender = LiveSender(httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    with pytest.raises(DeliveryError) as e:
        await sender.telegram("1", "hi")
    assert "SECRET" not in str(e.value) and e.value.__cause__ is None


async def test_telegram_send_returns_message_id(monkeypatch):
    import httpx

    from app.services.notify import LiveSender

    monkeypatch.setattr(get_settings(), "telegram_bot_token", "123:abc")

    def ok(request):
        assert request.url.path == "/bot123:abc/sendMessage"
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 4242}})

    assert await LiveSender(httpx.AsyncClient(transport=httpx.MockTransport(ok))).telegram("8122200450", "hi") == 4242
