import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import digest


RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Market update</title><link>https://example.com/a</link>
<pubDate>Sat, 14 Nov 2026 00:00:00 GMT</pubDate>
<description><![CDATA[<p>Stocks rose today.</p>]]></description></item>
<item><title>Old article</title><link>https://example.com/old</link>
<pubDate>Thu, 12 Nov 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""


class DigestTests(unittest.TestCase):
    def test_collect_filters_old_items_and_extracts_article(self):
        page = b"<html><nav><p>Navigation links and unrelated material here.</p></nav><article><p>" + (
            b"Useful article paragraph with enough words to keep. " * 3
        ) + b"</p></article></html>"

        def fake_fetch(url):
            return RSS if url.endswith("rss.xml") else page

        with patch.object(digest, "fetch", side_effect=fake_fetch):
            articles = digest.collect(
                {"feeds": ["https://example.com/rss.xml"], "since_hours": 26},
                datetime(2026, 11, 14, 9, tzinfo=timezone.utc),
            )
        self.assertEqual([article.title for article in articles], ["Market update"])
        self.assertIn("Useful article", articles[0].summary)
        self.assertNotIn("Navigation", articles[0].summary)

    def test_falls_back_to_feed_summary_when_article_fails(self):
        with patch.object(digest, "fetch", side_effect=[RSS, OSError("unavailable")]):
            articles = digest.collect(
                {"feeds": ["https://example.com/rss.xml"], "keywords": ["market"]},
                datetime(2026, 11, 14, 9, tzinfo=timezone.utc),
            )
        self.assertEqual(articles[0].summary, "Stocks rose today.")

    def test_mail_requires_credentials_before_connection(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "SMTP_HOST"):
                digest.send_mail("body", "subject")

    def test_extracts_korean_legacy_encoding(self):
        paragraph = "기사를 제대로 읽기 위한 한글 문장입니다. " * 3
        page = f'<meta charset="euc-kr"><article><p>{paragraph}</p></article>'.encode("euc-kr")
        self.assertIn("기사를 제대로", digest.article_excerpt(page))


if __name__ == "__main__":
    unittest.main()

