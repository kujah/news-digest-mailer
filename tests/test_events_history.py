from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email import policy
import imaplib
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import Mock, patch

from test_business_digest import CONFIG, NOW, article, test_directory
from news_digest.events import EventMatcher, group_articles
from news_digest.models import Match, RunResult
from news_digest.rendering import render_html
from news_digest.rules import Classifier
from news_digest.sent_mail import decode_history, fetch_gmail_history, records_from_message
from news_digest.storage import History


def story(title, body, source='hankyung', category='kr_steel', slug='first', day=0):
    return replace(article(title), url=f'https://example.com/{slug}', published=NOW+timedelta(days=day),
                   summary=body, content=body, source_id=source, source_name=source,
                   content_origin='본문 발췌', matches=(Match(category, category, 80, 'Evidence'),))


FIRST = '한국과 EU 산업계가 철강 제조업 녹색전환 규제 부담을 줄이기 위해 공동 정책제언을 발표했다. 철강 규제 적용 시기를 유연하게 조정하고 중복 보고 부담을 완화할 것을 요구했다.'


class EventGroupingTests(unittest.TestCase):
    def setUp(self):
        self.matcher = EventMatcher(CONFIG)

    def test_different_media_one_event_one_card_and_all_source_links(self):
        left = story('한·EU 산업계, 철강 녹색규제 부담 줄이자', FIRST)
        right = story('한국·EU 산업계 철강 녹색규제 부담 완화 공동 제언', FIRST + ' 공동 대응을 이어갈 방침이다.', 'herald', slug='second')
        grouped = group_articles([left, right], self.matcher)
        self.assertEqual(len(grouped), 1)
        self.assertEqual({item.url for item in grouped[0].coverage}, {left.url, right.url})
        output = render_html(RunResult(grouped, [], NOW, 26, CONFIG['categories']))
        self.assertEqual(output.count('같은 사건의 보도 매체:'), 1)
        self.assertIn(left.url, output)
        self.assertIn(right.url, output)

    def test_different_named_projects_of_same_company_are_not_merged(self):
        first = story('Nextpower wins Project Orion solar contract', 'Nextpower won the "Orion" solar project supply contract in India for 100MW.', category='solar_specialists')
        second = story('Nextpower wins Project Vega solar contract', 'Nextpower won the "Vega" solar project supply contract in India for 100MW.', category='solar_specialists', slug='vega')
        self.assertEqual(len(group_articles([first, second], self.matcher)), 2)

    def test_different_capacity_projects_without_update_signal_are_not_merged(self):
        first = story('Nextpower wins India solar supply contract', 'Nextpower awarded a solar contract in India for 100MW.', category='solar_specialists')
        second = story('Nextpower wins India solar supply contract for another site', 'Nextpower awarded a solar contract in India for 300MW.', category='solar_specialists', slug='another')
        self.assertEqual(len(group_articles([first, second], self.matcher)), 2)

    def test_country_and_trade_mechanism_differences_are_not_one_event(self):
        first = story('EU steel CBAM policy announcement', 'EU CBAM steel policy changes for local producers.')
        second = story('US steel safeguard policy announcement', 'US safeguard steel policy changes for local producers.', slug='us')
        self.assertEqual(len(group_articles([first, second], self.matcher)), 2)

    def test_representative_prefers_accessible_complete_body(self):
        first = replace(story('EU steel regulation proposal', FIRST), http_status=403, content_origin='RSS 발췌 · 본문 접근 제한')
        second = story('EU steel regulation proposal', FIRST + ' 공개 본문에서 구체적인 정책 제언의 내용을 설명했다.', 'herald', slug='second')
        grouped = group_articles([first, second], self.matcher)
        self.assertEqual(grouped[0].url, second.url)

    def test_new_actual_milestone_is_kept(self):
        first = story('Solar project Orion plans construction', 'The "Orion" solar project planned construction of a 100MW facility.', category='kr_solar')
        second = story('Solar project Orion construction begins', 'The "Orion" solar project construction begins on the same 100MW facility.', category='kr_solar', slug='second', day=1)
        old, new = self.matcher.profile(first), self.matcher.profile(second)
        self.assertTrue(self.matcher.same_event(old, new))
        self.assertIn('착공', self.matcher.material_update(old, new))

    def test_later_publication_and_additional_background_numbers_are_not_updates(self):
        first = story('EU steel import tariffs announced', 'EU steel import tariffs of 16% were announced. POSCO exports steel to Europe.')
        second = story('EU steel import tariffs announced', 'EU steel import tariffs of 16% were announced. Other countries face 27.9%. POSCO exports steel to Europe.', slug='second', day=1)
        self.assertEqual(self.matcher.material_update(self.matcher.profile(first), self.matcher.profile(second)), '')

    def test_explicit_tariff_change_is_an_update(self):
        first = story('EU steel import tariff', 'EU steel import tariff is 16%.')
        second = story('EU steel import tariff increased', 'EU steel import tariff increased to 20%.', slug='second')
        self.assertTrue(self.matcher.material_update(self.matcher.profile(first), self.matcher.profile(second)))

    def test_before_and_after_numbers_in_one_article_still_count_as_update(self):
        first = story('EU steel import tariff', 'EU steel import tariff is 16%.')
        second = story('EU steel import tariff increased', 'EU steel import tariff increased from 16% to 20%.', slug='second')
        self.assertTrue(self.matcher.material_update(self.matcher.profile(first), self.matcher.profile(second)))

    def test_same_topic_without_event_evidence_remains_separate(self):
        first = story('Germany solar demand outlook', 'Germany installed capacity forecast and solar demand outlook are improving.', category='solar_specialists')
        second = story('India floating solar auctions', 'India awarded new floating solar auctions for utility-scale installations.', category='solar_specialists', slug='second')
        self.assertEqual(len(group_articles([first, second], self.matcher)), 2)

    def test_different_headlines_for_same_bank_capacity_programme_are_grouped(self):
        first = story('ADB targets investment in Bangladesh solar-plus-storage programme', 'The Asian Development Bank will provide advisory services for five additional solar and battery storage projects in Bangladesh with more than 1GW capacity.', category='global_bess')
        second = story('ADB supports 1GW solar storage pipeline in Bangladesh', 'The Asian Development Bank is expanding support for solar and battery storage projects in Bangladesh, providing advisory services for five additional projects in a 1GW programme.', category='global_bess', source='pv_magazine', slug='second')
        self.assertEqual(len(group_articles([first, second], self.matcher)), 1)

    def test_different_earnings_quarters_are_distinct_even_if_company_and_amount_match(self):
        first = story('삼성SDI 3분기 BESS 사업 실적', '삼성SDI BESS 사업 실적과 투자를 발표했다. 매출은 100억원이다.', category='kr_bess')
        second = story('삼성SDI 4분기 BESS 사업 실적', first.summary, category='kr_bess', slug='second')
        self.assertEqual(len(group_articles([first, second], self.matcher)), 2)

    def test_same_earnings_quarter_with_optional_year_is_grouped(self):
        first = story('삼성SDI 3분기 BESS 사업 실적', '삼성SDI BESS 사업 실적과 투자를 발표했다. 매출은 100억원이다.', category='kr_bess')
        second = story('삼성SDI 2026년 3분기 BESS 사업 실적', first.summary, category='kr_bess', slug='second')
        self.assertEqual(len(group_articles([first, second], self.matcher)), 1)


class EventHistoryTests(unittest.TestCase):
    def test_next_day_new_media_reprint_is_removed_after_reopening(self):
        with test_directory() as folder:
            path = Path(folder) / 'history.sqlite3'
            first = story('한·EU 산업계, 철강 녹색규제 부담 줄이자', FIRST)
            second = story('한국·EU 산업계 철강 녹색규제 부담 완화 공동 제언', FIRST, 'herald', slug='second', day=1)
            history = History(path)
            history.configure(CONFIG)
            history.mark_sent(group_articles([first], history.matcher), NOW)
            history.close()
            history = History(path)
            history.configure(CONFIG)
            fresh, repeated = history.filter_new(group_articles([second], history.matcher), NOW+timedelta(days=1))
            self.assertEqual(fresh, [])
            self.assertEqual(len(repeated), 1)
            history.close()

    def test_same_url_with_new_milestone_is_not_removed_by_exact_url_history(self):
        with test_directory() as folder:
            history = History(Path(folder) / 'history.sqlite3')
            history.configure(CONFIG)
            first = story('Solar project Orion construction', 'The "Orion" solar project planned construction of 100MW.', category='kr_solar')
            second = replace(first, summary='The "Orion" solar project construction begins for 100MW.', content='The "Orion" solar project construction begins for 100MW.')
            history.mark_sent(group_articles([first], history.matcher), NOW)
            fresh, repeated = history.filter_new(group_articles([second], history.matcher), NOW+timedelta(days=1))
            self.assertEqual(len(fresh), 1)
            self.assertTrue(fresh[0].update_note)
            self.assertEqual(repeated, [])
            history.close()

    def test_all_grouped_urls_are_recorded_after_delivery(self):
        with test_directory() as folder:
            history = History(Path(folder) / 'history.sqlite3')
            history.configure(CONFIG)
            first = story('EU steel policy proposal', FIRST)
            second = story('EU steel policy proposal', FIRST, 'herald', slug='second')
            history.mark_sent(group_articles([first, second], history.matcher), NOW)
            self.assertTrue(history.contains(first))
            self.assertTrue(history.contains(second))
            history.close()


def digest_message(html, subject='산업 뉴스 브리핑 | 2026-11-18 | 1건'):
    message = EmailMessage()
    message['Subject'] = subject
    message['Date'] = 'Wed, 18 Nov 2026 04:00:00 +0900'
    message.set_content('산업 뉴스 브리핑')
    message.add_alternative(html, subtype='html')
    return message.as_bytes(policy=policy.SMTP)


class SentMailParsingTests(unittest.TestCase):
    def setUp(self):
        self.item = story('EU steel policy proposal', FIRST)
        self.result = RunResult(group_articles([self.item], EventMatcher(CONFIG)), [], NOW, 26, CONFIG['categories'])

    def test_new_mail_metadata_roundtrip_and_cross_day_import(self):
        raw = digest_message(render_html(self.result))
        records, sent_at = records_from_message(raw, CONFIG, fallback_date=NOW)
        self.assertEqual(records[0]['event_id'], self.result.articles[0].event_id)
        with test_directory() as folder:
            history = History(Path(folder) / 'history.sqlite3')
            history.configure(CONFIG)
            history.import_profiles(records, sent_at)
            today = replace(self.item, source_id='herald', url='https://example.com/today')
            fresh, repeated = history.filter_new(group_articles([today], history.matcher), NOW+timedelta(days=1))
            self.assertEqual(fresh, [])
            self.assertEqual(len(repeated), 1)
            history.close()

    def test_old_html_cards_without_metadata_can_be_imported(self):
        old_html = re.sub(r'<!--news-digest-events-v1:.*?-->', '', render_html(self.result))
        records, _ = records_from_message(digest_message(old_html), CONFIG, fallback_date=NOW)
        self.assertEqual(len(records), 1)
        self.assertIn(self.item.url, records[0]['urls'])

    def test_numbered_legacy_text_mail_can_be_imported(self):
        message = EmailMessage()
        message['Subject'] = '산업 뉴스 브리핑 | 1건'
        message.set_content('[철강]\n1. EU steel policy proposal\n' + FIRST + '\nhttps://example.com/story\n')
        records, _ = records_from_message(message.as_bytes(), CONFIG, fallback_date=NOW)
        self.assertEqual(len(records), 1)

    def test_non_digest_mail_is_not_imported(self):
        self.assertIsNone(records_from_message(digest_message('<p>Meeting</p>', 'Meeting agenda'), CONFIG, fallback_date=NOW))

    def test_unparseable_prior_briefing_does_not_silently_disable_dedup(self):
        with self.assertRaisesRegex(RuntimeError, 'could not be parsed'):
            records_from_message(digest_message('<p>Unknown format</p>'), CONFIG, fallback_date=NOW)

    def test_empty_briefing_is_valid_history(self):
        records, _ = records_from_message(digest_message('<p>신규 선별 0건</p>', '산업 뉴스 브리핑 | 0건'), CONFIG, fallback_date=NOW)
        self.assertEqual(records, [])

    def test_invalid_metadata_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'invalid event history'):
            decode_history('<!--news-digest-events-v1:AAAA-->')


class GmailReadOnlyTests(unittest.TestCase):
    def client(self, subject='산업 뉴스 브리핑 | 1건'):
        client = Mock()
        client.list.return_value = ('OK', [b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"'])
        client.select.return_value = ('OK', [b'1'])
        header = EmailMessage()
        header['Subject'] = subject
        header['Date'] = 'Wed, 18 Nov 2026 04:00:00 +0900'
        item = story('EU steel policy proposal', FIRST)
        result = RunResult(group_articles([item], EventMatcher(CONFIG)), [], NOW, 26, CONFIG['categories'])
        raw = digest_message(render_html(result), subject)
        client.uid.side_effect = [('OK', [b'10']), ('OK', [(b'1 (UID 10 BODY[HEADER.FIELDS] {100}', header.as_bytes()), b')']), ('OK', [(b'1 (BODY[] {100}', raw), b')'])]
        return client

    def test_sent_folder_is_examined_read_only_and_bodies_use_peek(self):
        client = self.client()
        with patch.dict(os.environ, {'SMTP_USER': 'sender@example.com', 'SMTP_PASSWORD': 'dummy'}, clear=True), patch('news_digest.sent_mail.imaplib.IMAP4_SSL', return_value=client):
            result = fetch_gmail_history(CONFIG, NOW)
        self.assertEqual(len(result), 1)
        client.select.assert_called_once_with(b'"[Gmail]/Sent Mail"', readonly=True)
        self.assertTrue(all(call.args[0] in {'SEARCH', 'FETCH'} for call in client.uid.call_args_list))
        self.assertTrue(all('BODY.PEEK' in str(call.args) for call in client.uid.call_args_list if call.args[0] == 'FETCH'))
        client.store.assert_not_called()
        client.expunge.assert_not_called()
        client.logout.assert_called_once()

    def test_non_digest_mail_body_is_not_fetched(self):
        client = self.client('Meeting agenda')
        with patch.dict(os.environ, {'SMTP_USER': 'sender@example.com', 'SMTP_PASSWORD': 'dummy'}, clear=True), patch('news_digest.sent_mail.imaplib.IMAP4_SSL', return_value=client):
            self.assertEqual(fetch_gmail_history(CONFIG, NOW), [])
        self.assertEqual(client.uid.call_count, 2)

    def test_incomplete_header_response_fails(self):
        client = self.client()
        client.uid.side_effect = [('OK', [b'10 11']), ('OK', [(b'1 (UID 10 BODY[HEADER.FIELDS] {0}', b'Subject: Meeting\r\n'), b')'])]
        with patch.dict(os.environ, {'SMTP_USER': 'sender@example.com', 'SMTP_PASSWORD': 'dummy'}, clear=True), patch('news_digest.sent_mail.imaplib.IMAP4_SSL', return_value=client), self.assertRaisesRegex(RuntimeError, 'incomplete'):
            fetch_gmail_history(CONFIG, NOW)

    def test_authentication_error_never_exposes_password(self):
        client = self.client()
        client.login.side_effect = imaplib.IMAP4.error('dummy sensitive password')
        with patch.dict(os.environ, {'SMTP_USER': 'sender@example.com', 'SMTP_PASSWORD': 'dummy'}, clear=True), patch('news_digest.sent_mail.imaplib.IMAP4_SSL', return_value=client), self.assertRaises(RuntimeError) as caught:
            fetch_gmail_history(CONFIG, NOW)
        self.assertNotIn('dummy', str(caught.exception))
        self.assertNotIn('sensitive', str(caught.exception))


class RelevanceRegressionTests(unittest.TestCase):
    def setUp(self):
        self.classifier = Classifier(CONFIG)

    def test_apartment_sales_with_bipv_amenity_and_nearby_ess_factory_are_excluded(self):
        item = story('포스코이앤씨 새 주거단지 분양, 산단 직주근접', '아파트 분양을 시작한다. ESS를 생산하는 삼성SDI 사업장과 가까워 직주근접 수요가 기대된다. 단지 외관에 태양광 BIPV를 일부 도입한다.', category='kr_solar')
        self.assertFalse(self.classifier.classify(item, ['kr_solar', 'kr_bess']).matches)

    def test_copper_foil_outlook_with_incidental_solar_policy_is_not_a_solar_story(self):
        item = story('중국 저가 공세 둔화, 동박 업황 반등', '전지박 가격이 상승하며 국내 동박 업체 수익성 개선 기대감이 커졌다. ' + '중국 동박 공장 가동률과 가격 변화를 분석했다. ' * 20 + '배터리와 태양광 등 전략 산업에 공급과잉 억제 정책을 적용했다.', category='kr_solar')
        self.assertFalse(self.classifier.classify(item, ['kr_solar', 'kr_bess']).matches)

    def test_unrelated_market_sentence_does_not_bypass_small_solar_project_threshold(self):
        item = story('태양광 2MW 발전소 착공', '태양광 2MW 발전소가 착공했다. 은행과 자동차 업계는 수요 전망과 정책 변화를 분석했다.', category='kr_solar')
        self.assertFalse(self.classifier.classify(item, ['kr_solar']).matches)

    def test_real_solar_project_of_same_contractor_is_retained(self):
        item = story('포스코이앤씨 태양광 100MW 발전소 착공', '태양광 100MW 발전소를 착공했다.', category='kr_solar')
        self.assertTrue(self.classifier.classify(item, ['kr_solar']).matches)

    def test_real_storage_market_story_is_retained(self):
        item = story('북미 BESS 시장 전망과 삼성SDI 증설', '북미 BESS 수요 전망이 개선됐다. 삼성SDI는 ESS 생산시설을 증설하며 공급망을 강화했다.', category='kr_bess')
        self.assertTrue(self.classifier.classify(item, ['kr_bess']).matches)

    def test_heat_system_product_which_complements_pv_is_not_solar_macro_news(self):
        item = story('Austrian company develops small heat and power system to complement solar PV', 'A company develops a 1.2kW heat and power system to complement solar PV demand in winter.', category='solar_specialists')
        self.assertFalse(self.classifier.classify(item, ['solar_specialists']).matches)

    def test_tracker_excerpt_does_not_add_unrelated_product_sentences(self):
        body = 'Another company unveiled an unrelated software product at the exhibition. FTC Solar introduced a new solar tracker platform in India. A third company discussed generic hardware products.'
        excerpt = self.classifier.excerpt(body, ['FTC Solar'])
        self.assertIn('FTC Solar', excerpt)
        self.assertNotIn('Another company', excerpt)
        self.assertNotIn('third company', excerpt)

    def test_us_dollar_prefix_is_not_a_us_market_mention(self):
        from news_digest.rules import contains
        self.assertFalse(contains('US$1 billion in Bangladesh', 'US'))
        self.assertTrue(contains('US energy storage market', 'US'))


if __name__ == '__main__':
    unittest.main()
