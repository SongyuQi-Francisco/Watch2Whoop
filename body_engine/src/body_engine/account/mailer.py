"""Sending the sign-in code e-mail. Provider-agnostic: plain SMTP (any mailbox or transactional provider) or a `console` dev mode.

Configuration (environment; the password lives in a secrets file, never in env or git):

    BODY_MAIL_MODE            smtp | console | off      (default: off → e-mail sign-in answers 503)
    BODY_MAIL_FROM            "Watch2Whoop <no-reply@example.com>"   (must be a sender the provider has verified)
    BODY_SMTP_HOST            e.g. smtp-relay.brevo.com / smtp.resend.com / smtp.gmail.com
    BODY_SMTP_PORT            587 (STARTTLS, default) or 465 (implicit TLS)
    BODY_SMTP_SECURITY        starttls (default) | ssl
    BODY_SMTP_USER            login name (Resend: "resend"; Brevo: the SMTP login shown in its console)
    BODY_SMTP_PASSWORD_FILE   file with the SMTP password / API key (default secrets/smtp_password)

`console` mode does not send anything: it logs the code at WARNING level and keeps the last messages in OUTBOX
(tests and the Quick Start demo read them). Never use it on a public server.
Port 25 is not used (many clouds, Aliyun among them, block outbound 25); 587 / 465 go out normally.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from collections import deque
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path

from ..config import REPO_ROOT

log = logging.getLogger(__name__)
OUTBOX: deque = deque(maxlen=50)   # console mode only: (to, subject, body)
TIMEOUT_SECONDS = 20


class MailNotConfigured(RuntimeError):
    pass


def mode() -> str:
    return (os.environ.get("BODY_MAIL_MODE") or "off").strip().lower()


def configured() -> bool:
    return mode() in ("smtp", "console")


def code_message(code: str, minutes: int, lang: str = "zh") -> tuple[str, str]:
    """Subject and plain-text body of the sign-in code e-mail, Chinese and English in one message."""
    subject = f"Watch2Whoop 登录验证码 / sign-in code: {code}"
    body = (f"你的 Watch2Whoop 登录验证码是：{code}\n"
            f"{minutes} 分钟内有效，只能用一次。不是你本人操作的话，忽略这封邮件即可。\n\n"
            f"Your Watch2Whoop sign-in code is: {code}\n"
            f"It is valid for {minutes} minutes and works once. If you did not ask for it, ignore this e-mail.\n")
    if lang == "en":
        body = "\n\n".join(reversed(body.split("\n\n")))
    return subject, body


def _password() -> str:
    path = Path(os.environ.get("BODY_SMTP_PASSWORD_FILE", REPO_ROOT / "secrets" / "smtp_password"))
    if not path.exists():
        raise MailNotConfigured(f"SMTP password file missing: {path.name}")
    return path.read_text().strip()


def send(to: str, subject: str, body: str) -> None:
    """Send one plain-text e-mail. Raises MailNotConfigured / smtplib errors; the caller logs and answers generically."""
    m = mode()
    if m == "console":
        OUTBOX.append((to, subject, body))
        log.warning("mail (console mode, not sent) to=%s subject=%s", to, subject)
        return
    if m != "smtp":
        raise MailNotConfigured("BODY_MAIL_MODE is not smtp / console")
    host = os.environ.get("BODY_SMTP_HOST")
    sender = os.environ.get("BODY_MAIL_FROM")
    if not host or not sender:
        raise MailNotConfigured("BODY_SMTP_HOST and BODY_MAIL_FROM are required")
    security = (os.environ.get("BODY_SMTP_SECURITY") or "starttls").lower()
    port = int(os.environ.get("BODY_SMTP_PORT") or (465 if security == "ssl" else 587))
    user = os.environ.get("BODY_SMTP_USER")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(parseaddr(sender)[1].split("@")[-1] or None))
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if security == "ssl":
        smtp = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS, context=ctx)
    else:
        smtp = smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS)
    with smtp:
        if security != "ssl":
            smtp.starttls(context=ctx)
        if user:
            smtp.login(user, _password())
        smtp.send_message(msg)
