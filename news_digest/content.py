"""RSS/Atom and public HTML parsing without browser automation or external packages."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import html
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from .models import Article, SourceStatus
from .network import FetchError, Response, WebClient, canonical_url


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def parse_date(value: str | None, default_timezone: str = "UTC") -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if "," in value:
        value = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", value)
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            found = re.search(r"(20\d{2})[.\-/년\s]+(\d{1,2})[.\-/월\s]+(\d{1,2})(?:일)?(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", value)
            if not found:
                return None
            try:
                result = datetime(*[int(part or 0) for part in found.groups()])
            except ValueError:
                return None
    if default_timezone not in {"UTC", "Asia/Seoul"}:
        raise ValueError("Publisher timezone must be UTC or Asia/Seoul")
    local_zone = timezone(timedelta(hours=9)) if default_timezone == "Asia/Seoul" else timezone.utc
    return result.replace(tzinfo=local_zone).astimezone(timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def decode_page(data: bytes, content_type: str = "") -> str:
    found = re.search(rb"charset\s*=\s*['\"]?([A-Za-z0-9_-]+)", data[:4096], re.IGNORECASE)
    header = re.search(r"charset=([A-Za-z0-9_-]+)", content_type, re.IGNORECASE)
    encoding = found.group(1).decode("ascii") if found else header.group(1) if header else "utf-8"
    try:
        return data.decode(encoding, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def parse_feed(data: bytes, default_timezone: str = "UTC") -> list[Article]:
    root = ET.fromstring(data)
    result: list[Article] = []
    for node in root.iter():
        kind = node.tag.rsplit("}", 1)[-1].lower()
        if kind not in {"item", "entry"}:
            continue
        children: dict[str, list] = {}
        for child in node:
            children.setdefault(child.tag.rsplit("}", 1)[-1].lower(), []).append(child)
        def text(key: str) -> str:
            return "".join(children[key][0].itertext()) if key in children else ""
        title = clean_text(text("title"))
        links = children.get("link", [])
        link = next((child.get("href", "") or (child.text or "") for child in links
                     if child.get("rel", "alternate") == "alternate"), "").strip()
        published = parse_date(text("pubdate") or text("published") or text("date") or text("updated"), default_timezone)
        summary = clean_text(text("description") or text("summary"))
        content = clean_text(text("encoded") or text("content"))
        if urlsplit(link).scheme == "http":
            link = "https://" + link[len("http://"):]
        if title and urlsplit(link).scheme == "https":
            result.append(Article(title, canonical_url(link), published, summary, content=content))
    return result


class PageParser(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "aside", "noscript", "form"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    BODY = re.compile(r"article[-_ ]?(?:body|content|view)|newsct_article|news[-_ ]?(?:body|text|content)|view[-_ ]?cont|content[-_ ]?body", re.I)

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.paragraphs: list[tuple[bool, str]] = []
        self.current: list[str] = []
        self.content_text: list[str] = []
        self.metadata: dict[str, str] = {}
        self.feeds: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.anchor: str = ""
        self.anchor_text: list[str] = []
        self.json_blocks: list[str] = []
        self.script: list[str] | None = None
        self.heading: list[str] = []
        self.title_parts: list[str] = []
        self.body_blocks: list[tuple[int, list[str]]] = []
        self.active_blocks: list[tuple[int, int]] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            if key:
                self.metadata[key.casefold()] = attrs.get("content", "")
        if tag == "time" and attrs.get("datetime"):
            self.metadata.setdefault("datepublished", attrs["datetime"])
        if tag == "link":
            if attrs.get("rel") == "canonical":
                self.metadata["canonical"] = attrs.get("href", "")
            if any(kind in attrs.get("type", "") for kind in ("rss", "atom")):
                self.feeds.append(attrs.get("href", ""))
        if tag == "a":
            self.anchor = attrs.get("href", "")
            self.anchor_text = [attrs.get("title", "")]
        if tag == "img" and self.anchor:
            self.anchor_text.append(attrs.get("alt", ""))
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.script = []
        if tag == "p":
            self.current = []
        region = tag == "article" or bool(self.BODY.search(attrs.get("id", "") + " " + attrs.get("class", "")))
        if tag not in self.VOID:
            self.stack.append((tag, region))
            marker = attrs.get("id", "") + " " + attrs.get("class", "")
            priority = (4 if attrs.get("itemprop") == "articleBody" else
                        3 if re.search(r"(?:article|entry|post)[-_](?:body|content)(?![-_a-z])|articletxt|articleText", marker, re.I) else
                        2 if self.BODY.search(marker) else 1 if tag == "article" else 0)
            if priority:
                self.body_blocks.append((priority, []))
                self.active_blocks.append((len(self.stack), len(self.body_blocks) - 1))

    def handle_endtag(self, tag):
        if tag == "a":
            title = clean_text(" ".join(self.anchor_text))
            if self.anchor and title:
                self.links.append((self.anchor, title))
            self.anchor = ""
        if tag == "script" and self.script is not None:
            self.json_blocks.append("".join(self.script))
            self.script = None
        if tag == "p" and not any(t in self.SKIP for t, _ in self.stack):
            value = clean_text(" ".join(self.current))
            if len(value) >= 25:
                self.paragraphs.append((any(region for _, region in self.stack), value))
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                self.active_blocks = [(depth, block) for depth, block in self.active_blocks if depth <= len(self.stack)]
                break

    def handle_data(self, value):
        if self.script is not None:
            self.script.append(value)
        if self.anchor:
            self.anchor_text.append(value)
        tags = [tag for tag, _ in self.stack]
        if "title" in tags:
            self.title_parts.append(value)
        if "h1" in tags:
            self.heading.append(value)
        if any(tag in self.SKIP for tag in tags):
            return
        for _, block in self.active_blocks:
            self.body_blocks[block][1].append(value)
        if "p" in tags:
            self.current.append(value)
        if any(region for _, region in self.stack):
            self.content_text.append(value)

    def article_data(self) -> dict:
        result: dict = {}
        def walk(node):
            if isinstance(node, list):
                for item in node:
                    walk(item)
            elif isinstance(node, dict):
                types = node.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if any(str(value).lower().endswith(("article", "blogposting")) for value in types):
                    for key in ("headline", "datePublished", "articleBody", "description"):
                        if isinstance(node.get(key), str):
                            result.setdefault(key, node[key])
                if "@graph" in node:
                    walk(node["@graph"])
        for block in self.json_blocks:
            try:
                walk(json.loads(block))
            except (ValueError, TypeError):
                continue
        return result

    def body(self, strict: bool = False) -> str:
        structured = self.article_data().get("articleBody", "")
        if structured:
            return clean_text(structured)
        if self.body_blocks:
            # Prefer the actual article body over surrounding related-story cards.
            priority = max(score for score, _ in self.body_blocks)
            block = next(text for score, text in self.body_blocks if score == priority)
            return clean_text(" ".join(block))
        if strict:
            return ""
        preferred = [text for region, text in self.paragraphs if region]
        if preferred:
            return "\n".join(dict.fromkeys(preferred))
        if self.content_text:
            return clean_text(" ".join(self.content_text))
        return "\n".join(dict.fromkeys(text for _, text in self.paragraphs))


def parse_page(data: bytes, content_type: str = "") -> PageParser:
    page = PageParser()
    page.feed(decode_page(data, content_type))
    return page


def article_excerpt(data: bytes, limit: int = 300) -> str:
    body = parse_page(data).body()
    return body[:limit].rstrip() + ("…" if len(body) > limit else "")


def host_allowed(url: str, source: dict) -> bool:
    return (urlsplit(url).hostname or "").casefold() in source["allowed_domains"]


def discover(source: dict, client: WebClient, status: SourceStatus) -> list[Article]:
    candidates: dict[str, Article] = {}
    feeds = list(source.get("feeds", []))
    pages = list(source.get("listings", []))
    queue = list(dict.fromkeys(feeds + pages))
    index = 0
    while index < len(queue):
        url = queue[index]
        index += 1
        if any(check["source_url"] == url for check in status.checks):
            continue
        try:
            response = client.get(url)
            status.checks.append({"source_url": url, "fetched_at": response.fetched_at.isoformat(),
                                  "http_status": response.http_status, "final_url": response.final_url})
            try:
                articles = parse_feed(response.data, source.get("timezone", "UTC"))
            except ET.ParseError:
                articles = []
            if articles:
                for article in articles:
                    if host_allowed(article.url, source):
                        candidates.setdefault(article.url, replace(article, source_id=source["id"],
                            source_name=source["name"], source_url=url, fetched_at=response.fetched_at,
                            http_status=response.http_status))
                continue
            if url in feeds:
                status.errors.append("RSS did not return article entries")
            page = parse_page(response.data, response.content_type)
            for href in page.feeds[:2]:
                feed = urljoin(response.final_url, href)
                allowed_feed_hosts = source.get("feed_domains", source["allowed_domains"])
                if ("comments" not in feed and feed not in queue and urlsplit(feed).scheme == "https"
                        and urlsplit(feed).hostname in allowed_feed_hosts and len(queue) < 12):
                    queue.append(feed)
            pattern = source.get("article_url_pattern")
            if pattern:
                for href, title in page.links:
                    link = canonical_url(urljoin(response.final_url, href))
                    if host_allowed(link, source) and re.search(pattern, link) and len(title) >= 8:
                        candidates.setdefault(link, Article(title, link, None, source_id=source["id"],
                            source_name=source["name"], source_url=url, fetched_at=response.fetched_at,
                            http_status=response.http_status))
        except FetchError as error:
            status.checks.append({"source_url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
                                  "http_status": error.status, "error": error.kind})
            status.errors.append(str(error))
    status.discovered = len(candidates)
    dates = [article.published for article in candidates.values() if article.published]
    status.latest_published = max(dates) if dates else None
    if not any(check.get("http_status") == 200 for check in status.checks):
        status.status = "unavailable"
    elif status.errors:
        status.status = "partial"
    if not candidates and status.status == "ok":
        status.status = "no_entries"
    return list(candidates.values())


def enrich(article: Article, source: dict, client: WebClient) -> Article:
    try:
        response = client.get(article.url)
        if not host_allowed(response.final_url, source):
            raise FetchError("Article redirected outside its configured publisher", kind="invalid_url")
        page = parse_page(response.data, response.content_type)
        structured = page.article_data()
        date = structured.get("datePublished") or page.metadata.get("article:published_time") or page.metadata.get("datepublished") or page.metadata.get("pubdate") or page.metadata.get("date")
        title = structured.get("headline") or page.metadata.get("og:title") or clean_text(" ".join(page.heading)) or article.title
        description = structured.get("description") or page.metadata.get("og:description") or page.metadata.get("description") or article.summary
        content = page.body(strict=True)
        if not date and source.get("published_pattern"):
            found = re.search(source["published_pattern"], decode_page(response.data, response.content_type), re.I)
            if found:
                date = found.group(1)
        published = article.published or parse_date(date, source.get("timezone", "UTC"))
        return replace(article, title=clean_text(title), published=published, summary=clean_text(description),
                       content=content or article.content, fetched_at=response.fetched_at, http_status=response.http_status,
                       content_origin="본문 발췌" if content else "RSS·메타정보 발췌")
    except FetchError as error:
        return replace(article, http_status=error.status,
                       fetched_at=datetime.now(timezone.utc), content_origin="RSS 발췌 · 본문 접근 제한")
