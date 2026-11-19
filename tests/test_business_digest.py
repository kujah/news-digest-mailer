import copy
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import shutil
import uuid
import unittest
from unittest.mock import Mock, patch

import digest
from news_digest.content import enrich, parse_date, parse_feed, parse_page
from news_digest.mail import addresses, build_message
from news_digest.models import Article, Match, RunResult, SourceStatus
from news_digest.network import FetchError, Response, WebClient, canonical_url, redact, validate_url
from news_digest.pipeline import balance, run, validate_config
from news_digest.rendering import render_html, render_text
from news_digest.rules import Classifier, capacities, contains
from news_digest.storage import History

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "sources.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 18, 5, tzinfo=timezone.utc)
TEST_TMP = Path(__file__).resolve().parents[1] / "data" / "test_tmp"
TEST_TMP.mkdir(parents=True, exist_ok=True)


@contextmanager
def test_directory():
    path = (TEST_TMP / uuid.uuid4().hex).resolve()
    if not path.is_relative_to(TEST_TMP.resolve()):
        raise ValueError("Test directory is outside the test workspace")
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path)


def article(title, content="", source_id="hankyung"):
    return Article(title, "https://www.hankyung.com/article/20261118001", NOW, content,
                   source_id, "한국경제", "https://www.hankyung.com/feed/economy", NOW, 200)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.classifier = Classifier(CONFIG)

    def test_all_requested_tracker_companies_are_recognized(self):
        for name in ["Nextpower", "GameChange Energy", "Ideematec", "PV Hardware", "Arctech", "Array Tech",
                     "Axial", "FTC Solar", "Gonvarri Solar Steel", "Powerway", "MetaloGalva", "Vergo Enerji"]:
            with self.subTest(name=name):
                selected = self.classifier.classify(article(f"{name} signs solar tracker supply contract"), ["solar_specialists"])
                self.assertEqual(selected.matches[0].reason, "지정 구조물·트래커 업체 동향")

    def test_generic_array_is_not_array_technologies(self):
        selected = self.classifier.classify(article("A solar array installed on a small home"), ["solar_specialists"])
        self.assertFalse(selected.matches)

    def test_capacity_units_and_korean_notation(self):
        result = capacities("0.1GW solar, 2GWh BESS and 5만kW, 10MW and 3MWh")
        self.assertIn((100, "MW", "100MW"), result)
        self.assertIn((2000, "MWh", "2000MWh"), result)
        self.assertIn((50, "MW", "50MW"), result)
        self.assertNotIn((3, "MW", "3MW"), result)

    def test_small_solar_project_is_excluded_and_large_included(self):
        self.assertFalse(self.classifier.classify(article("국내 태양광 발전소 2MW 착공"), ["kr_solar"]).matches)
        self.assertTrue(self.classifier.classify(article("국내 태양광 발전소 100MW 착공"), ["kr_solar"]).matches)

    def test_undisclosed_project_size_is_not_guessed(self):
        self.assertFalse(self.classifier.classify(article("태양광 신규 발전소 착공"), ["kr_solar"]).matches)

    def test_policy_outlook_is_not_subject_to_project_capacity_threshold(self):
        self.assertTrue(self.classifier.classify(article("태양광 시장 전망과 독일 정책 변화"), ["kr_solar"]).matches)

    def test_bess_is_not_general_ev_battery_news(self):
        self.assertFalse(self.classifier.classify(article("테슬라 전기차 수요 전망과 신규 투자"), ["kr_bess"]).matches)
        self.assertTrue(self.classifier.classify(article("삼성SDI 미국 BESS 200MWh 공급 계약"), ["kr_bess"]).matches)

    def test_bess_mw_threshold_is_only_used_when_energy_capacity_is_unknown(self):
        self.assertFalse(self.classifier.classify(article("BESS 60MW 80MWh project groundbreaking"), ["kr_bess"]).matches)
        self.assertTrue(self.classifier.classify(article("BESS 60MW project groundbreaking"), ["kr_bess"]).matches)

    def test_solar_research_does_not_become_macro_news_from_incidental_tail_words(self):
        item = replace(article("Researchers develop new solar cells", "Technology for educational toys."),
                       content="New solar cell research. " + "Technical details. " * 80 + "This may affect demand.")
        self.assertFalse(self.classifier.classify(item, ["solar_specialists"]).matches)

    def test_steel_mention_in_unrelated_banking_story_tail_is_not_trade_news(self):
        item = replace(article("Bank dollar liquidity reaches record", "Banking liquidity regulation."),
                       content="Bank liquidity rises. " * 60 + "Steel exports contributed to company deposits.")
        self.assertFalse(self.classifier.classify(item, ["kr_steel"]).matches)

    def test_blast_furnace_keyword_does_not_match_korean_superlative(self):
        self.assertFalse(contains("역대 최고로 강해졌다", "고로"))
        self.assertTrue(contains("제철소 고로를 재가동한다", "고로"))
        self.assertFalse(self.classifier.classify(article("은행 외화 대응력 역대 최고", "수출 호조로 대응력이 최고로 강해졌다."), ["kr_steel"]).matches)

    def test_generic_solar_equipment_launch_is_excluded_from_macro_section(self):
        self.assertFalse(self.classifier.classify(article("Sunkean unveils certified solar cable system", "For utility-scale solar projects and supply chain."), ["solar_specialists"]).matches)

    def test_storage_market_from_solar_media_is_grouped_under_bess(self):
        selected = self.classifier.classify(article("Italy energy storage market growth with solar systems"), ["solar_specialists", "global_bess"])
        self.assertEqual(selected.matches[0].category, "global_bess")

    def test_ess_has_word_boundaries(self):
        self.assertFalse(contains("business progress", "ESS"))
        self.assertTrue(contains("an ESS system", "ESS"))

    def test_global_solar_sector_news_included_product_roundup_excluded(self):
        self.assertTrue(self.classifier.classify(article("India floating solar sector outlook"), ["solar_specialists"]).matches)
        self.assertFalse(self.classifier.classify(article("A new photovoltaic module sets a cell efficiency record"), ["solar_specialists"]).matches)

    def test_steel_trade_and_producer_updates(self):
        self.assertTrue(self.classifier.classify(article("EU steel import quotas and CBAM tariffs"), ["kr_steel"]).matches)
        self.assertTrue(self.classifier.classify(article("현대제철 제철소 전기로 증설 투자"), ["kr_steel"]).matches)
        self.assertFalse(self.classifier.classify(article("Steel furniture chosen for a home"), ["kr_steel"]).matches)

    def test_construction_korean_spacing_and_english_equivalents(self):
        for title in ["스틸 커튼월 공사 착공", "설계 사무소 신규 프로젝트 기공식", "Free-form panels for a steel curtain wall", "Architectural firm wins roofing contract"]:
            with self.subTest(title=title):
                self.assertTrue(self.classifier.classify(article(title), ["kr_architecture"]).matches)

    def test_sourcing_groups_do_not_share_global_keyword_pool(self):
        item = article("스틸 커튼월 공사 수주")
        self.assertTrue(self.classifier.classify(item, ["global_architecture"]).matches)
        self.assertFalse(self.classifier.classify(item, ["solar_specialists"]).matches)

    def test_excerpt_picks_relevant_sentence_after_generic_opening(self):
        text = "The opening discusses unrelated equipment and background information. " + "Nextpower won a significant solar tracker supply contract in India."
        self.assertIn("Nextpower won", self.classifier.excerpt(text, ["Nextpower", "contract"]))

    def test_round_robin_preserves_smaller_sections_and_deduplicates_titles(self):
        solar = [replace(article(f"Solar story {i}", source_id=f"s{i}"), matches=(Match("kr_solar", "Solar", 90, "reason"),)) for i in range(10)]
        steel = replace(article("Steel trade news", source_id="steel"), matches=(Match("kr_steel", "Steel", 70, "reason"),))
        duplicate = replace(steel, url="https://www.mk.co.kr/other")
        result = balance(solar + [steel, duplicate], {**CONFIG, "max_items": 3})
        self.assertIn(steel, result)
        self.assertEqual(sum(item.title == steel.title for item in result), 1)

    def test_topics_and_publishers_have_no_equal_count_quota(self):
        solar = [replace(article(f"Solar story {i}", source_id="one_publisher"),
                         matches=(Match("kr_solar", "Solar", 90, "reason"),)) for i in range(40)]
        steel = replace(article("One steel story"), matches=(Match("kr_steel", "Steel", 70, "reason"),))
        selected = balance(solar + [steel], CONFIG)
        self.assertEqual(len(selected), 41)
        self.assertEqual(sum(item.source_id == "one_publisher" for item in selected), 40)


class ParsingAndSafetyTests(unittest.TestCase):
    def test_jsonld_article_metadata_and_void_html_elements(self):
        page = parse_page(b'<html><head><meta charset="utf-8"><link rel="canonical" href="https://example.com/a"><script type="application/ld+json">{"@type":"NewsArticle","headline":"Contract awarded","datePublished":"2026-11-18T12:00:00+09:00"}</script></head><article><p>A sufficiently detailed paragraph about the contract award.</p></article></html>')
        self.assertEqual(page.article_data()["headline"], "Contract awarded")
        self.assertIn("contract award", page.body())

    def test_actual_article_body_excludes_related_stories_even_without_paragraph_tags(self):
        page = parse_page(b'<article class="editor"><p>Unrelated editor recommendation and market news.</p></article><div class="article-contents"><div itemprop="articleBody">Actual steel tariff report.<br>Export quotas change.</div><div class="related-article"><p>Unrelated EV and battery market forecast story.</p></div></div>')
        self.assertIn("Export quotas", page.body(strict=True))
        self.assertNotIn("Unrelated", page.body(strict=True))

    def test_unverified_page_paragraphs_are_not_used_as_article_body(self):
        page = parse_page(b'<main><p>Related recommendations displayed while the actual story is unavailable.</p></main>')
        self.assertEqual(page.body(strict=True), "")

    def test_atom_content_and_korean_rss_timezone(self):
        data = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Solar contract</title><link href="https://example.com/a"/><published>2026-11-18T12:00:00+09:00</published><content type="html">Market outlook</content></entry></feed>'
        item = parse_feed(data)[0]
        self.assertEqual(item.content, "Market outlook")
        self.assertEqual(parse_date("2026-11-18 12:00:00", "Asia/Seoul"), datetime(2026, 11, 18, 3, tzinfo=timezone.utc))

    def test_robots_disallowed_prevents_article_request(self):
        client = WebClient()
        client.validated_hosts.add("example.com")
        from urllib.robotparser import RobotFileParser
        policy = RobotFileParser()
        policy.parse(["User-agent: *", "Disallow: /private"])
        client.policies["https://example.com"] = policy
        client.opener = Mock()
        with self.assertRaises(FetchError):
            client.get("https://example.com/private/article")
        client.opener.open.assert_not_called()

    def test_missing_robots_allowed_but_unverifiable_robots_fails_closed(self):
        client = WebClient()
        with patch.object(client, "get", side_effect=FetchError("not found", 404)):
            self.assertTrue(client._robots("https://example.com/a").can_fetch("*", "https://example.com/a"))
        client = WebClient()
        with patch.object(client, "get", side_effect=FetchError("unavailable", 503)):
            with self.assertRaises(FetchError):
                client._robots("https://example.com/a")

    def test_private_urls_and_credentials_are_rejected(self):
        for url in ["http://example.com", "https://127.0.0.1/a", "https://10.0.0.1/", "https://169.254.169.254/", "https://[::1]/", "https://user:password@example.com"]:
            with self.subTest(url=url), self.assertRaises(FetchError):
                validate_url(url)

    def test_dns_private_resolution_is_rejected(self):
        with patch("news_digest.network.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.10", 443))]):
            with self.assertRaises(FetchError):
                validate_url("https://public-looking.example")

    def test_tracking_parameters_do_not_defeat_dedup(self):
        self.assertEqual(canonical_url("https://example.com/article?idx=1&utm_source=mail#top"), "https://example.com/article?idx=1")

    def test_non_ascii_article_paths_are_percent_encoded(self):
        self.assertEqual(canonical_url("https://example.com/news/façade-design"), "https://example.com/news/fa%C3%A7ade-design")

    def test_legacy_http_feed_links_use_https_without_http_requests(self):
        data = b'<rss><channel><item><title>Curtain wall contract</title><link>http://example.com/a</link></item></channel></rss>'
        self.assertEqual(parse_feed(data)[0].url, "https://example.com/a")

    def test_header_newline_is_rejected_without_leaking_address(self):
        with self.assertRaisesRegex(ValueError, "single line") as result:
            addresses("recipient@example.com\nother@example.com", "MAIL_TO")
        self.assertNotIn("recipient@example.com", str(result.exception))

    def test_mail_is_multipart_and_uses_bare_address_values(self):
        with patch.dict(os.environ, {"SMTP_USER": "sender@example.com", "SMTP_PASSWORD": "dummy", "MAIL_TO": "one@example.com,two@example.com", "MAIL_FROM": ""}, clear=True):
            message, sender, recipients = build_message("text", "subject", "<p>html</p>")
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        self.assertEqual(len(recipients), 2)
        self.assertEqual(sender, "sender@example.com")

    def test_html_escapes_article_content(self):
        item = replace(article('<script>alert("x")</script>'), matches=(Match("kr_solar", "Solar", 80, "Matched"),))
        result = RunResult([item], [], NOW, 26, CONFIG["categories"])
        output = render_html(result)
        self.assertNotIn('<script>alert', output)
        self.assertIn("&lt;script&gt;", output)

    def test_mail_has_four_ordered_cards_and_merges_domestic_overseas(self):
        items = [replace(article(f"Unique headline {category}"),
                         matches=(Match(category, category, 80, "Selection evidence"),))
                 for category in ["kr_solar", "solar_specialists", "kr_steel", "steel_specialists",
                                  "kr_bess", "global_bess", "kr_architecture", "global_architecture"]]
        result = RunResult(items, [], NOW, 26, CONFIG["categories"])
        output = render_html(result)
        self.assertEqual(output.count('data-section="'), 4)
        positions = [output.index(f'data-section="{section}"')
                     for section in ["steel", "solar", "bess", "architecture"]]
        self.assertEqual(positions, sorted(positions))
        for item in items:
            self.assertEqual(output.count(item.title), 1)
        self.assertIn("[태양광] · 2건", render_text(result))
        self.assertIn("[BESS] · 2건", render_text(result))
        self.assertIn("[외장·건축] · 2건", render_text(result))

    def test_empty_topics_keep_their_cards(self):
        output = render_html(RunResult([], [], NOW, 26, CONFIG["categories"]))
        self.assertEqual(output.count('data-section="'), 4)
        self.assertEqual(output.count("새로 선정된 기사가 없습니다"), 4)

    def test_log_redaction(self):
        output = redact("Authorization: Bearer secret\nhttps://example.com/?sig=secret&token=hidden pwd=pass")
        self.assertNotIn("secret", output)
        self.assertNotIn("hidden", output)
        self.assertNotIn("pass", output)


class PipelineHistoryTests(unittest.TestCase):
    def test_history_survives_reopening_and_sent_title_reposts(self):
        with test_directory() as temp:
            path = Path(temp) / "history.sqlite3"
            history = History(path)
            history.mark_sent([article("Solar outlook")], NOW)
            history.close()
            history = History(path)
            self.assertTrue(history.contains(replace(article("Solar outlook"), url="https://example.com/reprint")))
            history.close()

    def test_article_body_can_supply_missing_business_event(self):
        cfg = copy.deepcopy(CONFIG)
        cfg["sources"] = [cfg["sources"][0]]
        candidate = article("국내 태양광 소식")
        enriched = replace(candidate, content="국내 태양광 신규 발전소 100MW 착공을 발표했다.")
        with test_directory() as temp:
            history = History(Path(temp) / "history.sqlite3")
            with patch("news_digest.pipeline.discover", return_value=[candidate]), patch("news_digest.pipeline.enrich", return_value=enriched):
                result = run(cfg, Mock(), history, NOW)
            self.assertEqual(len(result.articles), 1)
            history.close()

    def test_undated_articles_excluded_by_default(self):
        cfg = copy.deepcopy(CONFIG)
        cfg["sources"] = [next(item for item in cfg["sources"] if item["id"] == "windoor")]
        candidate = replace(article("스틸커튼월 공사 수주", source_id="windoor"), published=None)
        with test_directory() as temp:
            history = History(Path(temp) / "history.sqlite3")
            with patch("news_digest.pipeline.discover", return_value=[candidate]), patch("news_digest.pipeline.enrich", return_value=candidate):
                result = run(cfg, Mock(), history, NOW)
            self.assertFalse(result.articles)
            self.assertEqual(result.sources[0].undated, 1)
            history.close()

    def test_all_publishers_failed_is_not_a_successful_empty_digest(self):
        cfg = copy.deepcopy(CONFIG)
        cfg["sources"] = [cfg["sources"][0]]
        def failed(source, client, status):
            status.status = "unavailable"
            return []
        with test_directory() as temp:
            history = History(Path(temp) / "history.sqlite3")
            with patch("news_digest.pipeline.discover", side_effect=failed), self.assertRaisesRegex(RuntimeError, "All enabled"):
                run(cfg, Mock(), history, NOW)
            history.close()

    def test_failed_smtp_does_not_mark_history_and_preview_never_sends(self):
        with test_directory() as temp:
            root = Path(temp)
            (root / "sources.json").write_text(json.dumps(CONFIG), encoding="utf-8")
            selected = replace(article("Solar market outlook"), matches=(Match("kr_solar", "Solar", 80, "Policy"),))
            result = RunResult([selected], [], NOW, 26, CONFIG["categories"])
            with patch.object(digest, "BASE", root), patch.object(digest, "run", return_value=result), patch.object(digest, "fetch_gmail_history", return_value=[]), patch.object(digest, "send_mail", side_effect=RuntimeError("SMTP rejected")):
                self.assertEqual(digest.main([]), 1)
            history = History(root / "data/history.sqlite3")
            self.assertFalse(history.contains(selected))
            history.close()
            with patch.object(digest, "BASE", root), patch.object(digest, "run", return_value=result), patch.object(digest, "send_mail") as sender, patch("builtins.print"):
                self.assertEqual(digest.main(["--preview"]), 0)
                sender.assert_not_called()
            self.assertTrue((root / "data/preview/latest.html").exists())


if __name__ == "__main__":
    unittest.main()
