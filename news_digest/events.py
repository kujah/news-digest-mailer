"""Conservative event grouping and material-update checks without an external model."""
from dataclasses import replace
import hashlib
import json
import re
import unicodedata

from .models import Article, Coverage
from .rules import contains

SECTION = {'kr_solar': 'solar', 'solar_specialists': 'solar', 'kr_bess': 'bess',
           'global_bess': 'bess', 'kr_steel': 'steel', 'steel_specialists': 'steel',
           'kr_architecture': 'architecture', 'global_architecture': 'architecture'}
STOP = {'news', 'said', 'says', 'market', 'today', 'yesterday', 'report', 'reports',
        '기자', '뉴스', '오늘', '어제', '지난', '관련', '있다', '했다', '밝혔다', '전했다',
        '이번', '올해', '내년', '전망', '시장', '산업', '업계', '통해', '따르면', '대한', '위한'}
STAGES = {
    'planned': (r'계획|예정|추진|planned|proposed|proposal', '계획·추진'),
    'awarded': (r'수주|낙찰|계약\s*체결|contract awarded|wins? .*contract|awarded', '수주·계약'),
    'construction': (r'착공|기공식|groundbreaking|construction begins|began construction', '착공'),
    'operating': (r'상업\s*운전|가동\s*개시|준공|commissioned|commercial operation|begins operation', '준공·가동'),
    'approved': (r'최종\s*승인|승인\s*완료|법안\s*통과|approved|enacted', '승인·법안 확정'),
    'cancelled': (r'취소|철회|중단\s*확정|cancelled|canceled|withdrawn', '취소·철회'),
}
UPDATE = re.compile(r'상향|하향|인상|인하|확대|축소|추가\s*(?:수주|투자|증설|계약|물량)|변경|수정|전환|재개|최종\s*(?:승인|결정)|revised|increased|decreased|expanded|additional (?:contract|capacity|investment)|amended|resumed', re.I)


def normalized(value: str) -> str:
    value = unicodedata.normalize('NFKC', value).casefold()
    value = re.sub(r'\[[^\]]*\]', ' ', value)
    value = re.sub(r'\s*[-|]\s*(?:헤럴드경제|머니투데이|매일경제|한국경제|서울경제|pv magazine.*)$', '', value)
    return re.sub(r'\s+', ' ', value).strip()


def tokens(value: str) -> set[str]:
    output = set()
    for word in re.findall(r'[가-힣]{2,}|[a-z][a-z0-9]{2,}', normalized(value)):
        for ending in ('했습니다', '했다고', '한다고', '하면서', '에서는', '으로', '에서', '했다', '한다', '이다', '까지', '부터', '대한', '위한', '들이', '하고', '하며', '는', '은', '을', '를', '이', '가'):
            if word.endswith(ending) and len(word) - len(ending) >= 2:
                word = word[:-len(ending)]
                break
        if word not in STOP:
            output.add(word)
    return output


def grams(value: str) -> set[str]:
    return {word[i:i+3] for word in tokens(value) for i in range(max(1, len(word)-2))}


def dice(left, right) -> float:
    left, right = set(left), set(right)
    return 2 * len(left & right) / (len(left) + len(right)) if left and right else 0.0


def reporting_period(title: str) -> tuple[set[str], set[str]]:
    quarters = set(re.findall(r'(?<!\d)([1-4])\s*분기', title))
    quarters.update(re.findall(r'\bq([1-4])\b', title, re.I))
    quarters.update(re.findall(r'\b([1-4])q\b', title, re.I))
    years = set(re.findall(r'(20\d{2}).{0,25}(?:실적|전망|매출|earnings|outlook|forecast|revenue)', title, re.I))
    if quarters:
        years.update(re.findall(r'\b(20\d{2})\s*년?', title))
    return quarters, years


def links(article: Article) -> tuple[Coverage, ...]:
    primary = Coverage(article.title, article.url, article.source_id, article.source_name,
                       article.published, article.source_url, article.fetched_at, article.http_status)
    unique = {primary.url: primary}
    for item in article.coverage:
        unique.setdefault(item.url, item)
    return tuple(unique.values())


class EventMatcher:
    def __init__(self, config: dict | None = None):
        self.entities = (config or {}).get('entities', [])
        self.markets = (config or {}).get('markets', [])

    def profile(self, article: Article) -> dict:
        lead = f'{article.title}\n{article.summary}\n{article.content[:2400]}'
        text = normalized(lead)
        actors = set(article.entities)
        for item in self.entities:
            if any(contains(lead, alias) for alias in item['aliases']):
                actors.add(item['name'])
                for alias in item['aliases']:
                    text = text.replace(normalized(alias), normalized(item['name']))
        countries = set()
        for item in self.markets:
            if any(contains(lead, alias) for alias in item['aliases']):
                countries.add(item['name'])
        projects = set()
        for found in re.finditer(r'[“‘\"\']([^”’\"\']{3,70})[”’\"\']', lead):
            name = found.group(1)
            nearby = lead[max(0, found.start()-25):found.end()+25]
            if re.search(r'프로젝트|발전소|단지|태양광\s*발전|project|solar farm|power plant', nearby, re.I):
                projects.add(re.sub(r'[^a-z0-9가-힣]', '', normalized(name)))
        facts = {}
        # Dates, publication times and untyped counts are not update signals.
        for number, unit in re.findall(r'(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(GWh|MWh|GW|MW|%|억원|조원|million dollars|billion dollars|million tonnes|만톤|톤)', lead, re.I):
            unit = unit.casefold()
            value = float(number.replace(',', ''))
            if unit in {'gw', 'gwh'}:
                value *= 1000
                unit = 'mw' if unit == 'gw' else 'mwh'
            facts.setdefault(unit, set()).add(f'{value:g}')
        stage_text = f'{article.title}\n{article.summary[:300]}\n{article.content[:500]}'
        stages = set()
        for stage, (pattern, _) in STAGES.items():
            if re.search(pattern, stage_text, re.I):
                if stage in {'construction', 'operating'} and re.search(r'(?:착공|준공|가동|운전).{0,10}(?:예정|계획)|planned .*construction|scheduled .*operation', stage_text, re.I):
                    continue
                stages.add(stage)
        return {'version': 1, 'section': SECTION.get(article.matches[0].category, article.matches[0].category) if article.matches else '',
                'sections': sorted({SECTION.get(match.category, match.category) for match in article.matches}),
                'named': sorted(set(re.findall(r'\b[A-Z][A-Z0-9]{2,11}\b', article.title)) - {'BESS', 'ESS', 'EPC', 'LFP', 'NCA', 'USD', 'GWH', 'MWH'}),
                'title': normalized(article.title), 'title_grams': sorted(grams(article.title)),
                'title_tokens': sorted(tokens(article.title)), 'body_grams': sorted(grams(text)),
                'actors': sorted(actors), 'countries': sorted(countries), 'projects': sorted(projects),
                'policies': sorted(set(re.findall(r'cbam|trq|세이프가드|반덤핑|세액공제|ira|ppa|rps', text))),
                'facts': {key: sorted(values) for key, values in facts.items()}, 'stages': sorted(stages),
                'update_word': bool(UPDATE.search(stage_text))}

    def same_event(self, left: dict, right: dict) -> bool:
        if left['section'] != right['section'] and not set(left.get('sections', [left['section']])) & set(right.get('sections', [right['section']])):
            return False
        left_quarters, left_years = reporting_period(left['title'])
        right_quarters, right_years = reporting_period(right['title'])
        if left_quarters and right_quarters and not left_quarters & right_quarters:
            return False
        if left_years and right_years and not left_years & right_years:
            return False
        lp, rp = set(left['projects']), set(right['projects'])
        if lp and rp and not lp & rp:
            return False
        lc, rc = set(left['countries']), set(right['countries'])
        if lc and rc and not lc & rc:
            return False
        lpol, rpol = set(left['policies']), set(right['policies'])
        if lpol and rpol and not lpol & rpol:
            return False
        if left['title'] == right['title']:
            return True
        if not left['update_word'] and not right['update_word'] and not lp & rp:
            if left['section'] in {'solar', 'bess'} or {'awarded', 'construction', 'operating'} & set(left['stages']) & set(right['stages']):
                for unit in ('mw', 'mwh'):
                    lvalues, rvalues = set(left['facts'].get(unit, [])), set(right['facts'].get(unit, []))
                    if lvalues and rvalues and not lvalues & rvalues:
                        return False
        title = dice(left['title_grams'], right['title_grams'])
        words = dice(left['title_tokens'], right['title_tokens'])
        body = dice(left['body_grams'], right['body_grams'])
        anchors = (set(left['actors']) & set(right['actors'])) | (set(left['policies']) & set(right['policies'])) | (lp & rp) | (set(left.get('named', [])) & set(right.get('named', [])))
        if title >= .72 and (body >= .18 or words >= .65):
            return True
        if body >= .68 and (title >= .18 or anchors):
            return True
        if anchors and title >= .38 and body >= .30:
            return True
        if anchors and words >= .48 and body >= .28:
            return True
        return title >= .53 and body >= .42

    def material_update(self, old: dict, new: dict) -> str:
        new_stages = set(new['stages']) - set(old['stages']) - {'planned'}
        if new_stages:
            return '새 내용: ' + ' · '.join(STAGES[stage][1] for stage in sorted(new_stages))
        changed_units = []
        for unit, values in new['facts'].items():
            prior = set(old['facts'].get(unit, []))
            current = set(values)
            if prior and current and current - prior:
                changed_units.append(unit)
        if new['update_word'] and changed_units:
            return '새 내용: 규모·금액·세율 등 수치 변경'
        return ''


def representative_rank(article: Article):
    return (article.http_status == 200 and article.content_origin == '본문 발췌',
            min(len(article.content or article.summary), 2400),
            article.matches[0].score if article.matches else 0)


def group_articles(articles: list[Article], matcher: EventMatcher) -> list[Article]:
    groups: list[tuple[Article, dict]] = []
    for article in articles:
        profile = matcher.profile(article)
        for index, (existing, existing_profile) in enumerate(groups):
            if not matcher.same_event(existing_profile, profile):
                continue
            newer = matcher.material_update(existing_profile, profile)
            reverse = matcher.material_update(profile, existing_profile)
            selected = article if (newer and not reverse) or (not reverse and representative_rank(article) > representative_rank(existing)) else existing
            coverage = {item.url: item for item in links(existing) + links(article)}
            chosen_profile = profile if selected is article else existing_profile
            groups[index] = (replace(selected, coverage=tuple(coverage.values()), event_id=existing.event_id), chosen_profile)
            break
        else:
            event_id = hashlib.sha256((profile['section'] + ':' + profile['title']).encode('utf-8')).hexdigest()
            groups.append((replace(article, coverage=links(article), event_id=event_id), profile))
    return [replace(article, event_profile=json.dumps(profile, ensure_ascii=False)) for article, profile in groups]
