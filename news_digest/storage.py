"""Local article history and redacted collection reports. Never committed or uploaded."""

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .models import Article, RunResult
from .network import canonical_url, redact
from .events import EventMatcher, links


def title_key(title: str) -> str:
    normalized = re.sub(r"[^a-z0-9가-힣]+", "", title.casefold())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def inside(base: Path, path: Path) -> Path:
    base, path = base.resolve(), path.resolve()
    if not path.is_relative_to(base):
        raise ValueError("Path must stay inside the allowed project directory")
    return path


class History:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("CREATE TABLE IF NOT EXISTS sent (url TEXT PRIMARY KEY, title_key TEXT NOT NULL, sent_at TEXT NOT NULL)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS sent_title ON sent(title_key)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, profile TEXT NOT NULL, sent_at TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS event_urls (url TEXT PRIMARY KEY, event_id TEXT NOT NULL)")
        self.connection.commit()
        self.matcher = EventMatcher()

    def configure(self, config: dict) -> None:
        self.matcher = EventMatcher(config)

    def import_profiles(self, profiles: list[dict], sent_at: datetime) -> None:
        for record in profiles:
            self.connection.execute("INSERT OR REPLACE INTO events(event_id,profile,sent_at) VALUES(?,?,?)",
                (record['event_id'], json.dumps(record['profile'], ensure_ascii=False), sent_at.isoformat()))
            for url in record.get('urls', []):
                self.connection.execute("INSERT OR REPLACE INTO event_urls(url,event_id) VALUES(?,?)", (canonical_url(url), record['event_id']))
        self.connection.commit()

    def filter_new(self, articles: list[Article], now: datetime, *, lookback_days: int = 14) -> tuple[list[Article], list[Article]]:
        from datetime import timedelta
        from dataclasses import replace
        cutoff = (now - timedelta(days=lookback_days)).isoformat()
        rows = self.connection.execute("SELECT event_id,profile FROM events WHERE sent_at >= ? ORDER BY sent_at DESC", (cutoff,)).fetchall()
        records = [(key, json.loads(profile)) for key, profile in rows]
        retained, repeated = [], []
        for article in articles:
            profile = json.loads(article.event_profile) if article.event_profile else self.matcher.profile(article)
            for event_id, previous in records:
                if not self.matcher.same_event(previous, profile):
                    continue
                update = self.matcher.material_update(previous, profile)
                if update:
                    retained.append(replace(article, event_id=event_id, update_note=update))
                else:
                    repeated.append(article)
                break
            else:
                # Legacy exact URL/title records have no event evidence to compare.
                if self.contains(article) and not self.connection.execute("SELECT 1 FROM event_urls WHERE url=?", (canonical_url(article.url),)).fetchone():
                    repeated.append(article)
                else:
                    retained.append(article)
        return retained, repeated

    def contains(self, article: Article) -> bool:
        row = self.connection.execute("SELECT 1 FROM sent WHERE url = ? OR title_key = ? LIMIT 1",
                                      (canonical_url(article.url), title_key(article.title))).fetchone()
        return row is not None

    def mark_sent(self, articles: list[Article], sent_at: datetime) -> None:
        self.connection.executemany("INSERT OR REPLACE INTO sent(url, title_key, sent_at) VALUES (?, ?, ?)",
            [(canonical_url(item.url), title_key(item.title), sent_at.isoformat()) for article in articles for item in links(article)])
        for article in articles:
            event_id = article.event_id or title_key(article.title)
            self.connection.execute("INSERT OR REPLACE INTO events(event_id,profile,sent_at) VALUES(?,?,?)",
                (event_id, article.event_profile or json.dumps(self.matcher.profile(article), ensure_ascii=False), sent_at.isoformat()))
            for item in links(article):
                self.connection.execute("INSERT OR REPLACE INTO event_urls(url,event_id) VALUES(?,?)", (canonical_url(item.url), event_id))
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


def save_report(result: RunResult, directory: Path, *, delivery: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    report = {
        "fetched_at": result.fetched_at.isoformat(), "since_hours": result.since_hours,
        "delivery": delivery, "article_count": len(result.articles),
        "sources": [asdict(source) for source in result.sources],
        "articles": [{key: value for key, value in asdict(article).items() if key != "content"} for article in result.articles],
    }
    safe = json.dumps(report, ensure_ascii=False, indent=2, default=lambda value: value.isoformat())
    path = directory / (result.fetched_at.strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    path.write_text(redact(safe), encoding="utf-8")
    return path
