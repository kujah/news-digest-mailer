"""Plain-text and HTML mail with topic grouping, sourcing evidence and coverage."""

from datetime import timedelta, timezone
from html import escape

from .models import Article, RunResult

KST = timezone(timedelta(hours=9))
STATUS = {"ok": "수집 완료", "partial": "일부 접근 제한", "unavailable": "접근 제한",
          "no_entries": "기사 목록 미확인", "disabled": "연결 확인 필요", "stale": "조회 범위에 최근 기사 없음"}

SECTIONS = (
    ("steel", "철강", "#334155", {"kr_steel", "steel_specialists"}),
    ("solar", "태양광", "#b45309", {"kr_solar", "solar_specialists"}),
    ("bess", "BESS", "#047857", {"kr_bess", "global_bess"}),
    ("architecture", "외장·건축", "#4338ca", {"kr_architecture", "global_architecture"}),
)


def mail_sections(result: RunResult):
    """Merge sourcing categories into four ordered reader-facing sections."""
    for section_id, label, color, category_ids in SECTIONS:
        articles = [article for article in result.articles
                    if article.matches and article.matches[0].category in category_ids]
        yield section_id, label, color, articles


def article_details(article: Article) -> str:
    date = article.published.astimezone(KST).strftime("%m/%d %H:%M") if article.published else "게시일 미확인"
    parts = [article.source_name, date]
    if article.markets:
        parts.append(" · ".join(article.markets[:5]))
    if article.capacities:
        parts.append(" / ".join(article.capacities[:4]))
    return " | ".join(parts)


def render_text(result: RunResult) -> str:
    lines = [f"산업 뉴스 브리핑 | {result.fetched_at.astimezone(KST):%Y-%m-%d %H:%M KST}",
             f"최근 {result.since_hours}시간 · 신규 선별 {len(result.articles)}건", ""]
    for _, label, _, articles in mail_sections(result):
        lines.extend([f"[{label}] · {len(articles)}건", ""])
        if not articles:
            lines.extend(["수집 범위 안에서 새로 선정된 기사가 없습니다.", ""])
        for index, article in enumerate(articles, 1):
            lines.extend([f"{index}. {article.title}", article_details(article),
                          f"선별 근거: {article.matches[0].reason}",
                          f"{article.content_origin}: {article.summary or '공개 발췌 없음'}", article.url, ""])
    lines.extend(["[매체 수집 상태]", ""])
    for source in result.sources:
        note = f" · 게시일 미확인 {source.undated}건 제외" if source.undated else ""
        limited = " · 조회 상한 적용" if source.limited else ""
        latest = f" · 최근 게시 {source.latest_published:%Y-%m-%d}" if source.status == "stale" and source.latest_published else ""
        lines.append(f"{source.name}: {STATUS.get(source.status, source.status)} · 반영 {source.selected}건{note}{limited}{latest}")
    lines.extend(["", "공개 RSS·기사 목록의 최근 항목을 선별한 결과입니다. 전체 기사 검색 결과는 아닙니다.",
                  "선별 근거는 설정된 키워드·규모 기준입니다. 발췌는 기사 문장을 골라 표시하며 AI 요약이나 번역이 아닙니다.",
                  "수집·접근 제한 또는 해당 기간의 기사가 없으면 섹션이 비어 있을 수 있습니다."])
    return "\n".join(lines) + "\n"


def render_html(result: RunResult) -> str:
    date = result.fetched_at.astimezone(KST).strftime("%Y년 %m월 %d일")
    pieces = ["<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        "<title>산업 뉴스 브리핑</title></head><body style=\"margin:0;background:#f0f3f7;font-family:Arial,'Malgun Gothic',sans-serif;color:#172438\">",
        "<div style=\"max-width:820px;margin:0 auto;padding:28px 16px\">",
        f"<div style=\"background:#14344e;color:white;padding:28px;border-radius:12px\"><div style=\"font-size:13px;color:#a9cfdf\">{escape(date)} · INDUSTRY BRIEFING</div>",
        "<h1 style=\"font-size:28px;margin:10px 0\">산업 뉴스 브리핑</h1>",
        f"<p style=\"margin:0;color:#d4e1ea\">철강 / 태양광 / BESS / 외장·건축<br>최근 {result.since_hours}시간 / 신규 선별 {len(result.articles)}건</p></div>"]
    for section_id, label, color, articles in mail_sections(result):
        pieces.append(f'<table role="presentation" data-section="{section_id}" width="100%" cellspacing="0" cellpadding="0" style="width:100%;margin-top:24px;background:white;border:1px solid #dce3ea;border-top:5px solid {color};border-radius:12px"><tr><td style="padding:20px">')
        pieces.append(f"<h2 style=\"font-size:21px;margin:0 0 16px;color:{color}\">{escape(label)} <span style=\"font-size:13px;color:#638396\">{len(articles)}건</span></h2>")
        if not articles:
            pieces.append("<p style=\"font-size:13px;color:#647589\">수집 범위 안에서 새로 선정된 기사가 없습니다.</p>")
        for article in articles:
            related = " · ".join(match.label for match in article.matches[1:])
            pieces.extend(["<div style=\"background:white;border:1px solid #dce3ea;border-radius:10px;padding:20px;margin:10px 0\">",
                f"<div style=\"font-size:12px;color:#62758b;margin-bottom:8px\">{escape(article_details(article))}</div>",
                f"<a href=\"{escape(article.url, quote=True)}\" style=\"font-size:18px;font-weight:bold;line-height:1.55;color:#173e58;text-decoration:none\">{escape(article.title)}</a>",
                f"<p style=\"font-size:12px;color:#19756d;margin:10px 0\">{escape(article.matches[0].reason)}</p>",
                f"<p style=\"font-size:14px;line-height:1.85;margin:12px 0\">{escape(article.summary or '공개 발췌 없음')}</p>",
                f"<div style=\"font-size:11px;color:#728091\">{escape(article.content_origin)}{(' · 관련: ' + escape(related)) if related else ''}</div>",
                f"<a href=\"{escape(article.url, quote=True)}\" style=\"display:inline-block;font-size:13px;color:#17608a;margin-top:12px\">원문 읽기 →</a></div>"])
        pieces.append("</td></tr></table>")
    pieces.append("<h2 style=\"font-size:18px;margin-top:32px\">매체 수집 상태</h2><table style=\"width:100%;border-collapse:collapse;font-size:12px\"><thead><tr><th align=\"left\" style=\"padding:8px;border-bottom:1px solid #cbd6df\">매체</th><th align=\"left\">상태</th><th align=\"right\">반영</th></tr></thead><tbody>")
    for source in result.sources:
        suffix = " / 조회 상한 적용" if source.limited else ""
        pieces.append(f"<tr><td style=\"padding:8px;border-bottom:1px solid #dce3ea\">{escape(source.name)}</td><td>{escape(STATUS.get(source.status, source.status) + suffix)}</td><td align=\"right\">{source.selected}</td></tr>")
    pieces.append("</tbody></table><p style=\"font-size:11px;line-height:1.8;color:#738195;margin-top:20px\">공개 RSS·기사 목록의 최근 항목을 선별한 결과이며 전체 기사 검색 결과는 아닙니다. 선별 근거는 키워드·규모 기준이고, 발췌는 기사 문장입니다. AI 요약·번역을 수행하지 않습니다. 접근 제한 매체의 전체 기사나 유료 본문은 포함하지 않습니다.</p></div></body></html>")
    return "".join(pieces)
