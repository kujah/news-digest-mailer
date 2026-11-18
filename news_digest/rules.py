"""Transparent, source-specific selection rules and evidence-based excerpts."""

from dataclasses import replace
import re
import unicodedata

from .content import clean_text
from .models import Article, Match


def normal(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("–", "-").replace("—", "-")


def contains(text: str, term: str) -> bool:
    text, term = normal(text), normal(term)
    if re.search(r"[가-힣]", term):
        pattern = r"\s*".join(re.escape(char) for char in term if not char.isspace())
        if term == "고로":
            pattern = r"(?<![가-힣])" + pattern
    else:
        pattern = re.escape(term).replace(r"\ ", r"[\s-]+").replace(r"\-", r"[\s-]*")
        pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
    return bool(re.search(pattern, text))


def hits(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if contains(text, term)]


def capacities(text: str) -> list[tuple[float, str, str]]:
    text = unicodedata.normalize("NFKC", text)
    pattern = r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(만|억)?\s*(GWh|MWh|kWh|GW|MW|kW|기가와트시|메가와트시|기가와트|메가와트|킬로와트)(?![A-Za-z])"
    output = []
    units = {"gwh": (1000, "MWh"), "mwh": (1, "MWh"), "kwh": (.001, "MWh"),
             "gw": (1000, "MW"), "mw": (1, "MW"), "kw": (.001, "MW"),
             "기가와트시": (1000, "MWh"), "메가와트시": (1, "MWh"),
             "기가와트": (1000, "MW"), "메가와트": (1, "MW"), "킬로와트": (.001, "MW")}
    for found in re.finditer(pattern, text, re.I):
        multiplier, unit = units[found.group(3).casefold()]
        value = float(found.group(1).replace(",", "")) * multiplier * {None: 1, "만": 10000, "억": 100000000}[found.group(2)]
        output.append((value, unit, f"{value:g}{unit}"))
    return list(dict.fromkeys(output))


class Classifier:
    def __init__(self, config: dict):
        self.categories = {item["id"]: item for item in config["categories"]}
        self.groups = config["term_groups"]
        self.entities = config.get("entities", [])
        self.markets = config.get("markets", [])
        self.thresholds = config.get("project_thresholds", {})
        self.excerpt_chars = int(config.get("excerpt_chars", 500))

    def may_match(self, article: Article, category_ids: list[str]) -> bool:
        text = f"{article.title}\n{article.summary}\n{article.content}"
        for category_id in category_ids:
            category = self.categories[category_id]
            topic = category.get("topic", "")
            if hits(text, self.groups.get(topic, [])):
                return True
            families = {topic, "tracker"} if topic == "solar" else {topic}
            if any(item["family"] in families and hits(text, item["aliases"]) for item in self.entities):
                return True
        return False

    def classify(self, article: Article, category_ids: list[str]) -> Article:
        text = f"{article.title}\n{article.summary}\n{article.content}"
        all_hits = {name: hits(text, terms) for name, terms in self.groups.items()}
        entity_matches = [item for item in self.entities if hits(text, item["aliases"])]
        capacity = capacities(text)
        matches = []
        for category_id in category_ids:
            category = self.categories[category_id]
            family = category["family"]
            topic = category.get("topic", "")
            selected = []
            reason = ""
            score = 0
            topic_hits = all_hits.get(topic, [])
            families = {topic, "tracker"} if topic == "solar" else {topic}
            family_entities = [item for item in entity_matches if item["family"] in families]
            if family == "construction":
                selected = all_hits.get("construction", [])
                if selected:
                    reason = "건축·설계사무소 / 착공·수주 / 외장공사 키워드 일치"
                    score = 50 + min(len(selected), 5) * 5
            elif family == "solar_specialist":
                tracker_entities = [item for item in entity_matches if item["family"] == "tracker"]
                lead = f"{article.title}\n{article.summary}\n{article.content[:600]}"
                macro = hits(lead, self.groups.get("solar_sectors", [])) + hits(lead, self.groups.get("market_policy", []))
                if tracker_entities:
                    selected = [item["name"] for item in tracker_entities]
                    reason = "지정 구조물·트래커 업체 동향"
                    score = 90
                elif topic_hits and macro and not (re.search(r"\b(?:unveils?|introduces?|launches?|debuts?|certified)\b", article.title, re.I)
                                                   and not re.search(r"\b(?:factory|facility|plant|manufacturing|auction|policy)\b", article.title, re.I)):
                    selected = topic_hits + macro
                    reason = "태양광 국가·지역 전망 / 정책 / 수상·영농형 등 산업 동향"
                    score = 75
            elif family == "business":
                if topic in {"bess", "steel"} and not topic_hits:
                    continue
                if topic == "steel" and not hits(f"{article.title}\n{article.summary}\n{article.content[:600]}", self.groups.get("steel", [])):
                    continue
                if not topic_hits and not family_entities:
                    continue
                market = all_hits.get("market_policy", [])
                company = all_hits.get("company_events", [])
                project = all_hits.get("project_events", [])
                if topic == "steel":
                    trade = all_hits.get("trade", [])
                    if trade:
                        selected = topic_hits + trade
                        reason = "철강 수출입·통상 동향"
                        score = 85
                    elif family_entities and (company or market or project):
                        selected = [item["name"] for item in family_entities] + company + market + project
                        reason = "주요 철강사 사업·경영 동향"
                        score = 75
                elif topic in {"solar", "bess"}:
                    if market:
                        selected = topic_hits + market
                        reason = "시장 전망·정책 변화·산업 동향"
                        score = 85
                    elif family_entities and company:
                        selected = [item["name"] for item in family_entities] + company
                        reason = "주요 업체의 생산·투자·수주·경영 동향"
                        score = 80
                    elif project:
                        if topic == "solar":
                            large = any(unit == "MW" and value >= self.thresholds.get("solar_min_mw", 50) for value, unit, _ in capacity)
                        else:
                            has_energy = any(unit == "MWh" for _, unit, _ in capacity)
                            large = any(unit == ("MWh" if has_energy else "MW") and
                                        value >= self.thresholds.get("bess_min_mwh" if has_energy else "bess_min_mw", 100 if has_energy else 50)
                                        for value, unit, _ in capacity)
                        if large:
                            selected = topic_hits + project + [display for _, _, display in capacity]
                            reason = "설정한 최소 규모 이상의 신규 프로젝트·착공·수주"
                            score = 90
                        elif category.get("allow_unknown_project_size", False) and not capacity:
                            selected = topic_hits + project
                            reason = "프로젝트 소식 · 규모 미공개"
                            score = 55
            if selected:
                title_hits = sum(contains(article.title, term) for term in selected)
                matches.append(Match(category_id, category["label"], score + min(title_hits, 3) * 3,
                                     reason, tuple(dict.fromkeys(selected))[:8]))
        matches.sort(key=lambda match: -match.score)
        if not matches:
            return replace(article, matches=())
        keywords = [word for match in matches for word in match.keywords]
        snippet = self.excerpt(article.content or article.summary, keywords)
        markets = tuple(item["name"] for item in self.markets if hits(text, item["aliases"]))
        return replace(article, summary=snippet, matches=tuple(matches),
                       entities=tuple(item["name"] for item in entity_matches), markets=markets,
                       capacities=tuple(display for _, _, display in capacity))

    def excerpt(self, text: str, keywords: list[str]) -> str:
        sentences = [clean_text(value) for value in re.split(r"(?<=[.!?])\s+|\n+", text)]
        sentences = [sentence for sentence in sentences if len(sentence) >= 25 and
                     not re.search(r"구독|무단\s*전재|재배포\s*금지|subscribe|all rights reserved", sentence, re.I)]
        ranked = sorted(enumerate(sentences), key=lambda item: (-sum(contains(item[1], word) for word in keywords), item[0]))
        chosen = sorted(ranked[:3])
        value = " ".join(sentence for _, sentence in chosen) or clean_text(text)
        if len(value) <= self.excerpt_chars:
            return value
        shortened = value[: self.excerpt_chars].rsplit(" ", 1)[0]
        return shortened.rstrip() + "…"
