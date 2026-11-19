"""Discover, enrich, classify and balance stories across publisher groups."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
import re
from urllib.parse import urlsplit

from .content import discover, enrich
from .models import Article, RunResult, SourceStatus
from .network import WebClient, validate_url
from .rules import Classifier, contains
from .storage import History, title_key
from .events import EventMatcher, group_articles, links

LOG = logging.getLogger("news_digest")


def validate_config(config: dict) -> None:
    if config.get("version") != 2:
        raise ValueError("sources.json must use version 2")
    if not isinstance(config.get("sources"), list) or not config["sources"]:
        raise ValueError("At least one publisher source is required")
    categories = {item["id"] for item in config.get("categories", [])}
    if len(categories) != len(config.get("categories", [])):
        raise ValueError("Category IDs must be unique")
    source_ids = set()
    for source in config["sources"]:
        if source["id"] in source_ids:
            raise ValueError("Source IDs must be unique")
        source_ids.add(source["id"])
        if not source.get("categories") or set(source["categories"]) - categories:
            raise ValueError(f"Invalid categories for source {source['id']}")
        if source.get("article_url_pattern"):
            re.compile(source["article_url_pattern"])
        for url in [source["homepage"]] + source.get("feeds", []) + source.get("listings", []):
            validate_url(url, resolve_dns=False)
        if not source.get("allowed_domains"):
            raise ValueError("Every source requires explicit allowed_domains")
        if (urlsplit(source["homepage"]).hostname or "") not in source["allowed_domains"]:
            raise ValueError("Source homepage must be in allowed_domains")
    if not 1 <= int(config.get("max_items", 500)) <= 1000:
        raise ValueError("max_items must be between 1 and 1000")
    if not 1 <= int(config.get("since_hours", 48)) <= 24 * 31:
        raise ValueError("since_hours must be between 1 and 744")


def run(config: dict, client: WebClient, history: History, now: datetime | None = None) -> RunResult:
    validate_config(config)
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=int(config.get("since_hours", 48)))
    classifier = Classifier(config)
    matcher = EventMatcher(config)
    history.configure(config)
    statuses = []
    classified: list[Article] = []
    excluded_titles = config.get("exclude_title_terms", [])
    seen_urls = set()
    for source in config["sources"]:
        status = SourceStatus(source["id"], source["name"], source["homepage"])
        statuses.append(status)
        if not source.get("enabled", True):
            status.status = "disabled"
            status.errors.append(source.get("availability_note", "Source needs verification"))
            continue
        LOG.info("Discovering publisher %s", source["id"])
        candidates = discover(source, client, status)
        limit = int(source.get("max_candidates", 1000))
        if len(candidates) > limit:
            status.limited = True
        # Dated RSS entries first; HTML listing order is retained for undated entries.
        candidates.sort(key=lambda article: article.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        fetch_budget = int(source.get("max_article_fetches", 150))
        for article in candidates[:limit]:
            if article.url in seen_urls:
                status.duplicate += 1
                continue
            if any(contains(article.title, term) for term in excluded_titles):
                continue
            if article.published and (article.published < cutoff or article.published > now + timedelta(hours=2)):
                status.old += 1
                continue
            preliminary = classifier.classify(article, source["categories"])
            if not preliminary.matches and not source.get("scan_article_body", False) and not classifier.may_match(article, source["categories"]):
                continue
            if status.fetched >= fetch_budget:
                status.limited = True
                if not preliminary.matches or article.published is None:
                    continue
                enriched = replace(article, content_origin="RSS 발췌 · 본문 조회 상한")
            else:
                enriched = enrich(article, source, client)
                status.fetched += 1
                if enriched.http_status != 200:
                    status.errors.append("Article body unavailable; RSS excerpt used when available")
            if enriched.published and (status.latest_published is None or enriched.published > status.latest_published):
                status.latest_published = enriched.published
            if any(contains(enriched.title, term) for term in excluded_titles):
                continue
            gate = source.get("source_gate_terms", [])
            if gate and not any(contains(f"{enriched.title} {enriched.summary} {enriched.content}", term) for term in gate):
                continue
            if enriched.published is None:
                status.undated += 1
                if not config.get("include_undated", False):
                    continue
            elif enriched.published < cutoff or enriched.published > now + timedelta(hours=2):
                status.old += 1
                continue
            enriched = classifier.classify(enriched, source["categories"])
            if not enriched.matches:
                continue
            status.matched += 1
            seen_urls.add(enriched.url)
            classified.append(enriched)
        status.errors = list(dict.fromkeys(status.errors))
        if status.errors and status.status == "ok":
            status.status = "partial"
        if status.latest_published and status.latest_published < cutoff and status.status == "ok":
            status.status = "stale"
        LOG.info("Publisher %s: discovered=%s matched=%s status=%s", source["id"], status.discovered, status.matched, status.status)
    if not any(status.status in {"ok", "partial", "no_entries", "stale"} for status in statuses):
        raise RuntimeError("All enabled publishers failed or were blocked; no successful collection")
    grouped = group_articles(classified, matcher)
    fresh, repeated = history.filter_new(grouped, now, lookback_days=int(config.get('history', {}).get('lookback_days', 14)))
    articles = balance(fresh, config)
    lookup = {status.source_id: status for status in statuses}
    for article in repeated:
        for item in links(article):
            if item.source_id in lookup:
                lookup[item.source_id].repeated_event += 1
    for article in articles:
        for item in links(article):
            if item.source_id in lookup:
                lookup[item.source_id].selected += 1
                if item.url != article.url:
                    lookup[item.source_id].grouped += 1
    selected_urls = {item.url for article in articles for item in links(article)}
    selected_titles = {title_key(article.title) for article in articles}
    for article in fresh:
        if article.url not in selected_urls and title_key(article.title) not in selected_titles:
            lookup[article.source_id].limited = True
    return RunResult(articles, statuses, now, int(config.get("since_hours", 48)), config["categories"])


def balance(articles: list[Article], config: dict) -> list[Article]:
    ordered = sorted(articles, key=lambda article: (-article.matches[0].score,
        -(article.published.timestamp() if article.published else 0)))
    buckets = {category["id"]: [] for category in config["categories"]}
    for article in ordered:
        buckets[article.matches[0].category].append(article)
    selected = []
    titles = set()
    category_counts: dict[str, int] = {}
    source_counts: dict[tuple[str, str], int] = {}
    maximum = int(config.get("max_items", 500))
    # Interleave topics without requiring equal counts or per-topic quotas.
    while len(selected) < maximum:
        progress = False
        for category in config["categories"]:
            key = category["id"]
            if category.get("max_items") is not None and category_counts.get(key, 0) >= category["max_items"]:
                continue
            while buckets[key]:
                article = buckets[key].pop(0)
                fingerprint = title_key(article.title)
                source_key = (key, article.source_id)
                source_maximum = config.get("max_per_source_per_category")
                if fingerprint in titles or (source_maximum is not None and source_counts.get(source_key, 0) >= source_maximum):
                    continue
                selected.append(article)
                titles.add(fingerprint)
                category_counts[key] = category_counts.get(key, 0) + 1
                source_counts[source_key] = source_counts.get(source_key, 0) + 1
                progress = True
                break
            if len(selected) >= maximum:
                break
        if not progress:
            break
    return selected
