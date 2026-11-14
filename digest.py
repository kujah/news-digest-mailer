"""Collect RSS articles, extract readable text, and email a link digest."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

USER_AGENT = "NewsDigestMailer/1.0 (+personal RSS reader)"
MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    published: datetime | None
    summary: str = ""


def fetch(url: str) -> bytes:
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError(f"Only HTTP(S) URLs are supported: {url}")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/html"})
    with urlopen(request, timeout=15) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"Response exceeds {MAX_BYTES} bytes: {url}")
    return data


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"[^>]+>", " ", value))).strip()


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def parse_feed(data: bytes) -> list[Article]:
    root = ET.fromstring(data)
    articles: list[Article] = []
    if root.tag.endswith("rss") or root.tag.endswith("RDF"):
        for item in root.findall(".//item"):
            title = clean_text(item.findtext("title") or "")
            link = (item.findtext("link") or "").strip()
            date = parse_date(item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date"))
            summary = clean_text(item.findtext("description") or "")
            if title and urlparse(link).scheme in {"http", "https"}:
                articles.append(Article(title, link, date, summary))
    else:
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.findall(f".//{ns}entry"):
            title = clean_text(entry.findtext(f"{ns}title") or "")
            links = entry.findall(f"{ns}link")
            link = next((node.get("href", "") for node in links if node.get("rel", "alternate") == "alternate"), "")
            date = parse_date(entry.findtext(f"{ns}published") or entry.findtext(f"{ns}updated"))
            summary = clean_text(entry.findtext(f"{ns}summary") or "")
            if title and urlparse(link).scheme in {"http", "https"}:
                articles.append(Article(title, link, date, summary))
    return articles


class ParagraphExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "aside", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.current: list[str] = []
        self.paragraphs: list[tuple[bool, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack.append(tag)
        if tag == "p":
            self.current = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and "p" in self.stack and not any(t in self.SKIP for t in self.stack):
            paragraph = clean_text("".join(self.current))
            if len(paragraph) >= 40:
                self.paragraphs.append((any(t in {"article", "main"} for t in self.stack), paragraph))
        if tag in self.stack:
            while self.stack:
                if self.stack.pop() == tag:
                    break

    def handle_data(self, data: str) -> None:
        if "p" in self.stack and not any(t in self.SKIP for t in self.stack):
            self.current.append(data)


def article_excerpt(data: bytes, limit: int = 300) -> str:
    parser = ParagraphExtractor()
    declared = re.search(rb"charset\s*=\s*['"]?([A-Za-z0-9_-]+)", data[:4096], re.IGNORECASE)
    encoding = declared.group(1).decode("ascii") if declared else "utf-8"
    try:
        page = data.decode(encoding, errors="replace")
    except LookupError:
        page = data.decode("utf-8", errors="replace")
    parser.feed(page)
    preferred = [text for in_article, text in parser.paragraphs if in_article]
    paragraphs = preferred or [text for _, text in parser.paragraphs]
    text = " ".join(paragraphs)
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def collect(config: dict, now: datetime | None = None) -> list[Article]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=int(config.get("since_hours", 26)))
    keywords = [str(word).casefold() for word in config.get("keywords", [])]
    unique: dict[str, Article] = {}
    for feed_url in config["feeds"]:
        try:
            items = parse_feed(fetch(feed_url))
        except (OSError, ValueError, ET.ParseError) as error:
            print(f"Feed skipped ({feed_url}): {error}", file=sys.stderr)
            continue
        for item in items:
            if item.published is not None and item.published >= cutoff:
                continue
            if keywords and not any(word in f"{item.title} {item.summary}".casefold() for word in keywords):
                continue
            unique.setdefault(item.url, item)
    selected = sorted(unique.values(), key=lambda item: item.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    result: list[Article] = []
    for item in selected[: int(config.get("max_items", 10))]:
        try:
            excerpt = article_excerpt(fetch(item.url)) or item.summary[:300]
        except (OSError, ValueError, UnicodeError) as error:
            print(f"Article fallback ({item.url}): {error}", file=sys.stderr)
            excerpt = item.summary[:300]
        result.append(Article(item.title, item.url, item.published, excerpt))
    return result


def render(articles: list[Article], now: datetime) -> str:
 header = f"News digest — { ow.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M KST')}"
 parts = [header, ""]
    for umber, article in enumerate(articles, 1):
 parts.extend([f"{ umber}. {article.title}", article.url, article.summary or "(본문을 추출하지 못했습니다.)", ""])
    return "\n".join(parts).strip() + "\n"


def send_mail(body: str, subject: str) -> None:
 required = ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_TO"]
 missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
 security = os.environ.get("SMTP_SECURITY", "ssl").lower()
    if security not in {"ssl", "starttls"}:
        raise ValueError("SMTP_SECURITY must be ssl or starttls")
 port = int(os.environ.get("SMTP_PORT", "465" if security == "ssl" else "587"))
 message = EmailMessage()
 message["Subject"] = subject
 message["From"] = os.environ["MAIL_FROM"]
 message["To"] = os.environ["MAIL_TO"]
 message.set_content(body)
    if security == "ssl":
        with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], port, timeout=20) as client:
 client.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
 client.send_message(message)
    else:
        with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=20) as client:
 client.starttls()
 client.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
 client.send_message(message)


def main() -> int:
 parser = argparse.ArgumentParser(description=__doc__)
 parser.add_argument("--config", type=Path, default=Path("sources.json"))
 parser.add_argument("--dry-run", action="store_true", help="Print the email without sending it")
 args = parser.parse_args()
 config = json.loads(args.config.read_text(encoding="utf-8"))
 articles = collect(config)
    if not articles:
        print("No matching articles; no email sent.")
        return 0
 ow = datetime.now(timezone.utc)
 body = render(articles, now)
    if args.dry_run:
        print(body)
    else:
        send_mail(body, f"News digest: {len(articles)} articles ({ ow.date()})")
        print(f"Sent {len(articles)} articles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
