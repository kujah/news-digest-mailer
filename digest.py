"""Collect business news from configured publishers and send a grouped digest.

Python 3.12+, standard library only. Run with --preview before delivery.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import smtplib
import sys
from xml.etree import ElementTree as ET

from news_digest.content import PageParser as ParagraphExtractor
from news_digest.content import article_excerpt, clean_text, discover, parse_date, parse_feed
from news_digest.mail import send_mail
from news_digest.models import Article, RunResult, SourceStatus
from news_digest.network import RedactionFilter, WebClient
from news_digest.pipeline import run, validate_config
from news_digest.rendering import render_html, render_text
from news_digest.storage import History, inside, save_report

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("news_digest")
_web = WebClient()


def fetch(url: str) -> bytes:
    """Compatibility entry point; production collection uses a run-scoped client."""
    return _web.get(url).data


def collect(config: dict, now: datetime | None = None) -> list[Article]:
    """Compatibility for previous callers with flat RSS settings.

The CLI and version-2 configuration use the grouped pipeline instead.
"""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=int(config.get("since_hours", 26)))
    keywords = [str(word).casefold() for word in config.get("keywords", [])]
    unique = {}
    for feed_url in config["feeds"]:
        try:
            items = parse_feed(fetch(feed_url))
        except (OSError, ValueError, ET.ParseError, RuntimeError):
            continue
        for item in items:
            if item.published and item.published < cutoff:
                continue
            if keywords and not any(word in f"{item.title} {item.summary}".casefold() for word in keywords):
                continue
            unique.setdefault(item.url, item)
    selected = sorted(unique.values(), key=lambda item: item.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    result = []
    for item in selected[:int(config.get("max_items", 10))]:
        try:
            excerpt = article_excerpt(fetch(item.url)) or item.summary[:300]
        except (OSError, ValueError, RuntimeError):
            excerpt = item.summary[:300]
        result.append(replace(item, summary=excerpt))
    return result


def render(articles: list[Article], now: datetime) -> str:
    """Compatibility plain-text rendering for previous programmatic callers."""
    return "\n\n".join([f"News digest | {now:%Y-%m-%d}"] +
        [f"{article.title}\n{article.url}\n{article.summary}" for article in articles]) + "\n"


class RunLock:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as error:
            raise RuntimeError("Another digest run is active, or a previous interrupted run left data/digest.lock") from error
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(os.getpid()))
        return self

    def __exit__(self, *args):
        self.path.unlink(missing_ok=True)


def setup_logging(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(), logging.FileHandler(directory / "digest.log", encoding="utf-8")]
    for handler in handlers:
        handler.addFilter(RedactionFilter())
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.setLevel(logging.INFO)
    for previous in LOG.handlers:
        previous.close()
    LOG.handlers = handlers
    LOG.propagate = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=BASE / "sources.json")
    parser.add_argument("--dry-run", action="store_true", help="Collect and print without sending or marking articles as sent")
    parser.add_argument("--preview", action="store_true", help="Save an HTML and text preview under data/preview without sending")
    parser.add_argument("--check-sources", action="store_true", help="Check robots, feeds and listings without retrieving article bodies or sending mail")
    parser.add_argument("--sources", help="Comma-separated publisher IDs to collect")
    parser.add_argument("--since-hours", type=int)
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--state-dir", type=Path, default=BASE / "data", help="Local state directory inside this project")
    args = parser.parse_args(argv)
    try:
        path = inside(BASE, args.config if args.config.is_absolute() else BASE / args.config)
        state = inside(BASE / "data", args.state_dir if args.state_dir.is_absolute() else BASE / args.state_dir)
        state.mkdir(parents=True, exist_ok=True)
        setup_logging(state / "logs")
        config = json.loads(path.read_text(encoding="utf-8"))
        if args.since_hours is not None:
            config["since_hours"] = args.since_hours
        if args.max_items is not None:
            config["max_items"] = args.max_items
        if args.sources:
            wanted = set(args.sources.split(","))
            known = {source["id"] for source in config["sources"]}
            if wanted - known:
                raise ValueError("Unknown source IDs: " + ", ".join(sorted(wanted - known)))
            config["sources"] = [source for source in config["sources"] if source["id"] in wanted]
        validate_config(config)
        client = WebClient(timeout=config.get("request_timeout", 15), retries=config.get("request_retries", 1))
        now = datetime.now(timezone.utc)
        if args.check_sources:
            statuses = []
            for source in config["sources"]:
                status = SourceStatus(source["id"], source["name"], source["homepage"])
                if source.get("enabled", True):
                    discover(source, client, status)
                else:
                    status.status = "disabled"
                    status.errors.append(source.get("availability_note", "Source needs verification"))
                statuses.append(status)
                print(f"{source['id']}: {status.status}; article entries={status.discovered}", flush=True)
            report = save_report(RunResult([], statuses, now, config["since_hours"], config["categories"]),
                                 state / "runs", delivery="source_check")
            LOG.info("Source report saved to %s", report)
            return 0 if any(item.status in {"ok", "partial"} for item in statuses) else 1
        with RunLock(state / "digest.lock"):
            history = History(state / "history.sqlite3")
            try:
                if os.environ.get("GITHUB_ACTIONS") == "true":
                    LOG.info("History is local. GitHub-hosted runners start with fresh storage; cross-run deduplication requires persistent tenant storage or a persistent runner workspace.")
                result = run(config, client, history, now)
                text = render_text(result)
                html = render_html(result)
                if args.preview:
                    preview = state / "preview"
                    preview.mkdir(parents=True, exist_ok=True)
                    (preview / "latest.html").write_text(html, encoding="utf-8")
                    (preview / "latest.txt").write_text(text, encoding="utf-8")
                    LOG.info("Mail preview saved to %s", preview / "latest.html")
                if args.dry_run or args.preview:
                    report = save_report(result, state / "runs", delivery="preview")
                    if args.dry_run:
                        print(text)
                    LOG.info("Preview report saved to %s", report)
                    return 0
                if not result.articles and not config.get("send_empty_digest", True):
                    save_report(result, state / "runs", delivery="no_new_articles")
                    LOG.info("No new matching articles; delivery disabled for empty digests")
                    return 0
                save_report(result, state / "runs", delivery="ready")
                send_mail(text, f"산업 뉴스 브리핑 | {now:%Y-%m-%d} | {len(result.articles)}건", html)
                history.mark_sent(result.articles, datetime.now(timezone.utc))
                save_report(result, state / "runs", delivery="sent")
                LOG.info("Digest accepted by SMTP; articles=%s", len(result.articles))
                return 0
            finally:
                history.close()
    except smtplib.SMTPAuthenticationError:
        LOG.error("SMTP authentication failed; check SMTP_USER, SMTP_PASSWORD and provider policy")
        return 1
    except smtplib.SMTPException:
        LOG.error("SMTP delivery failed; history was not marked as sent")
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        LOG.error("Digest failed: %s", error)
        return 1
    finally:
        for handler in LOG.handlers:
            handler.close()
        LOG.handlers = []


if __name__ == "__main__":
    raise SystemExit(main())
