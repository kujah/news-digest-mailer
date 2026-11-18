"""Data shared by discovery, classification, storage and mail rendering."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Match:
    category: str
    label: str
    score: int
    reason: str
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    published: datetime | None
    summary: str = ""
    source_id: str = ""
    source_name: str = ""
    source_url: str = ""
    fetched_at: datetime | None = None
    http_status: int | None = None
    content: str = ""
    content_origin: str = "RSS 발췌"
    matches: tuple[Match, ...] = ()
    entities: tuple[str, ...] = ()
    markets: tuple[str, ...] = ()
    capacities: tuple[str, ...] = ()


@dataclass
class SourceStatus:
    source_id: str
    name: str
    source_url: str
    status: str = "ok"
    discovered: int = 0
    fetched: int = 0
    matched: int = 0
    selected: int = 0
    old: int = 0
    undated: int = 0
    duplicate: int = 0
    limited: bool = False
    latest_published: datetime | None = None
    checks: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    articles: list[Article]
    sources: list[SourceStatus]
    fetched_at: datetime
    since_hours: int
    categories: list[dict]
