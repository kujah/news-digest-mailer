"""SMTP delivery with checked addresses, TLS and no credential logging."""

from email.message import EmailMessage
from email.utils import getaddresses
import os
import re
import smtplib
import ssl


def addresses(value: str, variable: str, multiple: bool = True) -> list[str]:
    value = value.strip()
    if "\r" in value or "\n" in value or value.startswith(variable + "="):
        raise ValueError(f"{variable} must contain email addresses only, on a single line")
    parsed = getaddresses([value])
    result = [address for _, address in parsed]
    if not result or (not multiple and len(result) != 1) or any(not re.fullmatch(r"[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+", item) for item in result):
        raise ValueError(f"{variable} contains an invalid email address")
    return result


def build_message(body: str, subject: str, html_body: str | None = None) -> tuple[EmailMessage, str, list[str]]:
    required = ["SMTP_USER", "SMTP_PASSWORD", "MAIL_TO"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    user = addresses(os.environ["SMTP_USER"], "SMTP_USER", multiple=False)[0]
    sender = addresses(os.environ.get("MAIL_FROM") or user, "MAIL_FROM", multiple=False)[0]
    recipients = addresses(os.environ["MAIL_TO"], "MAIL_TO")
    if "\r" in subject or "\n" in subject:
        raise ValueError("Mail subject must be a single line")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    return message, sender, recipients


def send_mail(body: str, subject: str, html_body: str | None = None) -> None:
    message, sender, recipients = build_message(body, subject, html_body)
    security = os.environ.get("SMTP_SECURITY") or "ssl"
    security = security.strip().lower()
    if security not in {"ssl", "starttls"}:
        raise ValueError("SMTP_SECURITY must be ssl or starttls")
    host = (os.environ.get("SMTP_HOST") or "smtp.gmail.com").strip()
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ValueError("SMTP_HOST must be a hostname")
    try:
        port = int(os.environ.get("SMTP_PORT") or ("465" if security == "ssl" else "587"))
    except ValueError as error:
        raise ValueError("SMTP_PORT must be a valid port number") from error
    if not 1 <= port <= 65535:
        raise ValueError("SMTP_PORT is invalid")
    context = ssl.create_default_context()
    if security == "ssl":
        connection = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
    else:
        connection = smtplib.SMTP(host, port, timeout=30)
    with connection as client:
        if security == "starttls":
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(os.environ["SMTP_USER"].strip(), os.environ["SMTP_PASSWORD"].strip())
        refused = client.send_message(message, from_addr=sender, to_addrs=recipients)
        if refused:
            raise RuntimeError("SMTP refused one or more recipients; delivery was partial")
