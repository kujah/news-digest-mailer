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
        self.connection.commit()

    def contains(self, article: Article) -> bool:
        row = self.connection.execute("SELECT 1 FROM sent WHERE url = ? OR title_key = ? LIMIT 1",
                                      (canonical_url(article.url), title_key(article.title))).fetchone()
        return row is not None

    def mark_sent(self, articles: list[Article], sent_at: datetime) -> None:
        self.connection.executemany("INSERT OR REPLACE INTO sent(url, title_key, sent_at) VALUES (?, ?, ?)",
            [(canonical_url(article.url), title_key(article.title), sent_at.isoformat()) for article in articles])
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
