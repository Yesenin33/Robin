"""Register the bot webhook (uses TELEGRAM_BOT_TOKEN / TELEGRAM_WEBHOOK_SECRET / FRONTEND_URL from .env).

    docker compose exec api python -m scripts.telegram_setup          # set webhook
    docker compose exec api python -m scripts.telegram_setup --info   # show status
    docker compose exec api python -m scripts.telegram_setup --delete # back to no receiver

Never prints the token. Note: a webhook and getUpdates polling can't coexist on one bot.
"""
import asyncio
import sys

from app.core.config import get_settings
from app.services.notify import LiveSender


async def main(arg: str) -> None:
    s = get_settings()
    bot = LiveSender()
    if arg == "--info":
        body, _ = await bot.bot_api("getWebhookInfo", {})
        info = body.get("result", {})
        url = info.get("url", "")
        print({"url": url.rsplit("/", 1)[0] + "/<secret>" if url else "", "pending": info.get("pending_update_count"),
               "last_error": info.get("last_error_message")})
        return
    if arg == "--delete":
        print((await bot.bot_api("deleteWebhook", {}))[0])
        return
    if len(s.telegram_webhook_secret) < 32:
        sys.exit("Set TELEGRAM_WEBHOOK_SECRET (32+ chars of [A-Za-z0-9_-]) in .env first")
    url = f"{s.frontend_url.rstrip('/')}/api/telegram/webhook/{s.telegram_webhook_secret}"
    body, _ = await bot.bot_api("setWebhook", {"url": url, "secret_token": s.telegram_webhook_secret,
                                              "allowed_updates": ["message", "callback_query"]})
    print({"ok": body.get("ok"), "description": body.get("description")})


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))
