import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


def _send_sync(to: str, subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.mail_from, to, subject
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


async def send_mail(to: str, subject: str, body: str) -> None:
    if not get_settings().smtp_host:
        log.info("mail_dev_mode", to=to, subject=subject, body=body)
        return
    await asyncio.to_thread(_send_sync, to, subject, body)
