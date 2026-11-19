"""Read-only Gmail Sent history, plus backwards-compatible digest MIME parsing."""
import base64
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
import gzip
import hashlib
from html.parser import HTMLParser
import imaplib
import json
import os
import re
import ssl
import zlib

from .events import EventMatcher, group_articles, links
from .models import Article, Match, RunResult
from .network import validate_url

MARKER = 'news-digest-events-v1:'
MAX_MESSAGE = 8 * 1024 * 1024


def event_records(result: RunResult) -> list[dict]:
    matcher = EventMatcher()
    return [{'event_id': article.event_id or hashlib.sha256(article.title.encode('utf-8')).hexdigest(),
             'profile': json.loads(article.event_profile) if article.event_profile else matcher.profile(article),
             'urls': [item.url for item in links(article)]} for article in result.articles]


def history_comment(result: RunResult) -> str:
    payload = json.dumps({'version': 1, 'events': event_records(result)}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_MESSAGE:
        raise RuntimeError('Digest history metadata exceeds the supported mail size')
    encoded = base64.b64encode(gzip.compress(payload, mtime=0)).decode('ascii')
    return f'<!--{MARKER}{encoded}-->'


def decode_history(html: str) -> list[dict] | None:
    match = re.search(r'<!--' + re.escape(MARKER) + r'([A-Za-z0-9+/=\r\n]+)-->', html)
    if not match:
        return None
    try:
        compressed = base64.b64decode(match.group(1), validate=True)
        decoder = zlib.decompressobj(31)
        raw = decoder.decompress(compressed, MAX_MESSAGE + 1)
        if len(raw) > MAX_MESSAGE or not decoder.eof or decoder.unused_data:
            raise ValueError('Invalid history payload size')
        payload = json.loads(raw)
        if payload['version'] != 1 or not isinstance(payload['events'], list) or len(payload['events']) > 1000:
            raise ValueError('Invalid history version')
        for record in payload['events']:
            if not re.fullmatch(r'[a-f0-9]{64}', record['event_id']):
                raise ValueError('Invalid event ID')
            profile = record['profile']
            for field in ('section', 'title'):
                if not isinstance(profile[field], str) or len(profile[field]) > 4000:
                    raise ValueError('Invalid event profile')
            for field in ('title_grams', 'title_tokens', 'body_grams', 'actors', 'countries', 'projects', 'policies', 'stages'):
                if not isinstance(profile[field], list) or len(profile[field]) > 15000 or any(not isinstance(value, str) or len(value) > 4000 for value in profile[field]):
                    raise ValueError('Invalid event evidence')
            if not isinstance(profile['facts'], dict) or not isinstance(profile['update_word'], bool):
                raise ValueError('Invalid event facts')
            for key, values in profile['facts'].items():
                if not isinstance(key, str) or not isinstance(values, list) or len(values) > 1000 or any(not isinstance(value, str) or len(value) > 100 for value in values):
                    raise ValueError('Invalid numeric event facts')
            if not isinstance(record['urls'], list) or len(record['urls']) > 1000:
                raise ValueError('Invalid event URLs')
            for url in record['urls']:
                validate_url(url, resolve_dns=False)
        return payload['events']
    except (ValueError, KeyError, TypeError, zlib.error, RuntimeError) as error:
        raise RuntimeError('Sent digest contains invalid event history metadata') from None


def category_for(label: str) -> str:
    if 'BESS' in label or 'ESS' in label:
        return 'kr_bess'
    if '태양광' in label or label == 'solar':
        return 'kr_solar'
    if '철강' in label or label == 'steel':
        return 'kr_steel'
    if '건축' in label or '외장' in label or label == 'architecture':
        return 'kr_architecture'
    if label == 'bess':
        return 'kr_bess'
    return ''


class LegacyDigestHTML(HTMLParser):
    """Recover previously delivered cards which have no embedded event metadata."""
    def __init__(self, published):
        super().__init__(convert_charrefs=True)
        self.published = published
        self.category = ''
        self.headline = None
        self.heading = None
        self.paragraph = None
        self.pending = None
        self.articles = []

    def flush(self):
        if self.pending and self.pending['category']:
            item = self.pending
            self.articles.append(Article(item['title'], item['url'], self.published, item.get('summary', ''),
                matches=(Match(item['category'], item['category'], 0, 'Previously sent'),)))
        self.pending = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('data-section'):
            self.flush()
            self.category = category_for(attrs['data-section'])
        if tag == 'h2':
            self.flush()
            self.heading = []
        if tag == 'a' and attrs.get('href', '').startswith('https://') and ('font-size:18px' in attrs.get('style', '') or 'font-size: 18px' in attrs.get('style', '')):
            self.flush()
            self.headline = (attrs['href'], [])
        if tag == 'p' and 'font-size:14px' in attrs.get('style', ''):
            self.paragraph = []

    def handle_data(self, value):
        if self.headline is not None:
            self.headline[1].append(value)
        if self.heading is not None:
            self.heading.append(value)
        if self.paragraph is not None:
            self.paragraph.append(value)

    def handle_endtag(self, tag):
        if tag == 'h2' and self.heading is not None:
            self.category = category_for(''.join(self.heading))
            self.heading = None
        if tag == 'a' and self.headline is not None:
            url, title = self.headline
            validate_url(url, resolve_dns=False)
            self.pending = {'url': url, 'title': ''.join(title).strip(), 'category': self.category}
            self.headline = None
        if tag == 'p' and self.paragraph is not None:
            if self.pending:
                self.pending['summary'] = ''.join(self.paragraph).strip()
            self.paragraph = None


def records_from_message(raw: bytes, config: dict, *, fallback_date: datetime) -> tuple[list[dict], datetime] | None:
    if len(raw) > MAX_MESSAGE:
        raise RuntimeError('Sent digest exceeds the supported history import size')
    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = str(message.get('Subject', ''))
    if not (subject.startswith('산업 뉴스 브리핑') or subject.startswith('News digest')):
        return None
    try:
        sent_at = parsedate_to_datetime(str(message.get('Date', '')))
        sent_at = sent_at.replace(tzinfo=timezone.utc) if sent_at.tzinfo is None else sent_at.astimezone(timezone.utc)
    except (ValueError, TypeError):
        sent_at = fallback_date
    if message.is_multipart():
        html_part = message.get_body(preferencelist=('html',))
        text_part = message.get_body(preferencelist=('plain',))
    else:
        html_part = message if message.get_content_type() == 'text/html' else None
        text_part = message if message.get_content_type() == 'text/plain' else None
    html = html_part.get_content() if html_part else ''
    if html:
        embedded = decode_history(html)
        if embedded is not None:
            return embedded, sent_at
        parser = LegacyDigestHTML(sent_at)
        parser.feed(html)
        parser.flush()
        articles = parser.articles
    else:
        articles = []
        text = text_part.get_content() if text_part else ''
        category = ''
        pending = None
        summary = []
        for line in text.splitlines():
            if line.startswith('[') and ']' in line:
                category = category_for(line.split(']')[0])
            found = re.match(r'^\d+\.\s+(.+)', line)
            if found:
                pending = found.group(1)
                summary = []
            elif pending and line.startswith('https://'):
                validate_url(line.strip(), resolve_dns=False)
                if category:
                    articles.append(Article(pending, line.strip(), sent_at, '\n'.join(summary), matches=(Match(category, category, 0, 'Previously sent'),)))
                pending = None
            elif pending:
                summary.append(line)
        if not articles:
            # Early flat-format mail: title, URL, then an excerpt.
            lines_text = text.splitlines()
            from .rules import Classifier
            classifier = Classifier(config)
            for index, line in enumerate(lines_text):
                if index and line.startswith('https://'):
                    validate_url(line.strip(), resolve_dns=False)
                    item = Article(lines_text[index-1].strip(), line.strip(), sent_at,
                        '\n'.join(lines_text[index+1:index+4]))
                    item = classifier.classify(item, [item['id'] for item in config['categories']])
                    if not item.matches:
                        from .rules import hits
                        inferred = next((category for topic, category in [('bess', 'kr_bess'), ('solar', 'kr_solar'), ('steel', 'kr_steel')]
                                         if hits(item.title + '\n' + item.summary, config['term_groups'].get(topic, []))), 'legacy')
                        from dataclasses import replace
                        item = replace(item, matches=(Match(inferred, inferred, 0, 'Previously sent'),))
                    if item.matches:
                        articles.append(item)
    if not articles:
        # An explicitly empty briefing is valid; a nonempty unparsed mail is not.
        body_text = html or (text_part.get_content() if text_part else '')
        if re.search(r'(?:신규 선별|선별)\s*0건|0\s*articles|\|\s*0건', body_text) or '| 0건' in subject:
            return [], sent_at
        raise RuntimeError('A prior briefing could not be parsed; history comparison is incomplete')
    matcher = EventMatcher(config)
    grouped = group_articles(articles, matcher)
    return event_records(RunResult(grouped, [], sent_at, 0, config['categories'])), sent_at


def fetch_gmail_history(config: dict, now: datetime) -> list[tuple[list[dict], datetime]]:
    user = os.environ.get('HISTORY_IMAP_USER') or os.environ.get('SMTP_USER', '')
    password = os.environ.get('HISTORY_IMAP_PASSWORD') or os.environ.get('SMTP_PASSWORD', '')
    if not user or not password or '\n' in user or '\r' in user:
        raise RuntimeError('Gmail history requires HISTORY_IMAP_USER/PASSWORD or SMTP_USER/PASSWORD')
    settings = config.get('history', {})
    days = int(settings.get('lookback_days', 14))
    limit = int(settings.get('max_mailbox_messages', 2000))
    if not 1 <= days <= 90 or not 1 <= limit <= 20000:
        raise ValueError('Invalid Gmail history lookback or message limit')
    since = (now - timedelta(days=days)).strftime('%d-%b-%Y')
    client = None
    try:
        client = imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ssl.create_default_context(), timeout=30)
        client.login(user, password)
        status, folders = client.list()
        if status != 'OK':
            raise RuntimeError('Gmail Sent folder list unavailable')
        mailbox = None
        for folder in folders or []:
            if isinstance(folder, bytes) and re.search(rb'\\Sent(?:\s|\))', folder, re.I):
                found = re.search(rb'("(?:[^"\\]|\\.)*"|[^ ]+)$', folder)
                if found:
                    mailbox = found.group(1)
                    break
        if mailbox is None:
            raise RuntimeError('Gmail special-use Sent folder was not found')
        if client.select(mailbox, readonly=True)[0] != 'OK':
            raise RuntimeError('Gmail Sent folder cannot be opened read-only')
        status, response = client.uid('SEARCH', None, 'SINCE', since)
        if status != 'OK' or not response or not isinstance(response[0], bytes):
            raise RuntimeError('Gmail history search failed')
        uids = response[0].split()
        if any(not uid.isdigit() for uid in uids) or len(uids) > limit:
            raise RuntimeError('Gmail history message limit exceeded; increase max_mailbox_messages')
        digests = []
        for offset in range(0, len(uids), 100):
            batch = set(uids[offset:offset+100])
            selected = b','.join(sorted(batch, key=int))
            status, headers = client.uid('FETCH', selected, '(UID BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])')
            if status != 'OK':
                raise RuntimeError('Gmail history headers unavailable')
            seen = set()
            for item in headers or []:
                if not isinstance(item, tuple) or len(item) != 2:
                    continue
                found = re.search(rb'UID\s+(\d+)', item[0])
                if not found:
                    raise RuntimeError('Gmail history response has no message UID')
                if found.group(1) not in batch:
                    raise RuntimeError('Gmail history returned an unexpected message UID')
                seen.add(found.group(1))
                header = BytesParser(policy=policy.default).parsebytes(item[1])
                subject = str(header.get('Subject', ''))
                if subject.startswith('산업 뉴스 브리핑') or subject.startswith('News digest'):
                    digests.append(found.group(1))
            if seen != batch:
                raise RuntimeError('Gmail history header retrieval was incomplete')
        results = []
        for uid in digests:
            status, parts = client.uid('FETCH', uid, '(BODY.PEEK[])')
            if status != 'OK':
                raise RuntimeError('Gmail sent briefing body unavailable')
            bodies = [part[1] for part in parts or [] if isinstance(part, tuple) and isinstance(part[1], bytes)]
            if len(bodies) != 1:
                raise RuntimeError('Gmail sent briefing body incomplete')
            parsed = records_from_message(bodies[0], config, fallback_date=now)
            if parsed is not None:
                results.append(parsed)
        return sorted(results, key=lambda item: item[1])
    except (imaplib.IMAP4.error, OSError, TimeoutError):
        raise RuntimeError('Gmail read-only history connection failed; check account, app password and IMAP policy') from None
    finally:
        if client is not None:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
