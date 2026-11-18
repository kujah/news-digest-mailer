"""Read-only, rate-limited public web access with robots and URL validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import ipaddress
import logging
import re
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

USER_AGENT = "NewsDigestMailer/2.0 (+business news monitoring; RSS and short excerpts)"
MAX_BYTES = 2_000_000
LOG = logging.getLogger("news_digest")


def redact(value: object) -> str:
    text = str(value)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)[^\r\n]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)([?&](?:sig|code|token|access_token|key|api_key|password)=)[^&\s]+", r"\1[redacted]", text)
    return re.sub(r"(?i)((?:password|pwd|client_secret)\s*=\s*)[^;\s]+", r"\1[redacted]", text)


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, kind: str = "unreachable"):
        super().__init__(redact(message))
        self.status = status
        self.kind = kind


@dataclass(frozen=True)
class Response:
    source_url: str
    final_url: str
    fetched_at: datetime
    http_status: int
    data: bytes
    content_type: str = ""


def validate_url(url: str, *, resolve_dns: bool = True) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise FetchError("Only public HTTPS URLs without embedded credentials are supported", kind="invalid_url")
    if parsed.port not in {None, 443}:
        raise FetchError("Publisher URLs must use HTTPS port 443", kind="invalid_url")
    if any(ord(char) < 32 or char.isspace() for char in url):
        raise FetchError("URL contains whitespace or control characters", kind="invalid_url")
    from urllib.parse import parse_qsl
    if any(key.casefold() in {"sig", "token", "access_token", "api_key", "password"} for key, _ in parse_qsl(parsed.query)):
        raise FetchError("Public publisher URLs must not contain credential parameters", kind="invalid_url")
    host = parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".local", ".internal")):
        raise FetchError("Private and metadata addresses are blocked", kind="invalid_url")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        if not resolve_dns:
            return url
        try:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
        except socket.gaierror as error:
            raise FetchError(f"DNS lookup failed for {host}") from error
    if not addresses or any(not address.is_global for address in addresses):
        raise FetchError("Private, loopback, link-local and metadata IP addresses are blocked", kind="invalid_url")
    return url


def canonical_url(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode

    parsed = urlsplit(url)
    tracking = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ad_source", "ad_medium", "ad_name"}
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in tracking]
    path = quote(parsed.path or "/", safe="/%:@-._~!$&'()*+,;=")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urlencode(sorted(query)), ""))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WebClient:
    def __init__(self, timeout: float = 15, retries: int = 1, min_interval: float = 1):
        self.timeout = timeout
        self.retries = retries
        self.min_interval = max(1, min_interval)
        self.opener = build_opener(NoRedirect())
        self.policies: dict[str, RobotFileParser] = {}
        self.next_request: dict[str, float] = {}
        self.delays: dict[str, float] = {}
        self.validated_hosts: set[str] = set()
        self.guard = threading.RLock()

    def validate(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        validate_url(url, resolve_dns=host not in self.validated_hosts)
        self.validated_hosts.add(host)

    def _wait(self, host: str) -> None:
        with self.guard:
            delay = max(self.min_interval, self.delays.get(host, 0))
            wait = self.next_request.get(host, 0) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.next_request[host] = time.monotonic() + delay

    def _robots(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.policies:
            policy = RobotFileParser(origin + "/robots.txt")
            try:
                response = self.get(policy.url, check_robots=False, max_bytes=512_000)
                if b"<html" in response.data[:1000].lower():
                    raise FetchError("robots.txt returned an HTML page instead of a policy", kind="robots_unavailable")
                policy.parse(response.data.decode("utf-8", "replace").splitlines())
            except FetchError as error:
                if error.status == 404:
                    policy.parse([])
                else:
                    raise FetchError(f"Cannot verify robots.txt for {parts.hostname}; source skipped",
                                     error.status, "robots_unavailable") from error
            self.policies[origin] = policy
            delay = policy.crawl_delay(USER_AGENT) or policy.crawl_delay("*") or 0
            self.delays[parts.hostname or ""] = float(delay)
        return self.policies[origin]

    def get(self, url: str, *, check_robots: bool = True, max_bytes: int = MAX_BYTES) -> Response:
        initial = url
        for redirect in range(7):
            parts = urlsplit(url)
            url = urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%:@-._~!$&'()*+,;="),
                             quote(parts.query, safe="=&%:@-._~!$'()*+,;/?"), ""))
            self.validate(url)
            host = urlsplit(url).hostname or ""
            if check_robots and not self._robots(url).can_fetch(USER_AGENT, url):
                raise FetchError(f"robots.txt disallows {url}", kind="robots_disallowed")
            redirected = False
            for attempt in range(self.retries + 1):
                self._wait(host)
                request = Request(url, headers={"User-Agent": USER_AGENT,
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/html;q=0.9"})
                try:
                    with self.opener.open(request, timeout=self.timeout) as response:
                        data = response.read(max_bytes + 1)
                        if len(data) > max_bytes:
                            raise FetchError(f"Response exceeds {max_bytes} bytes", kind="too_large")
                        return Response(initial, url, datetime.now(timezone.utc), response.status, data,
                                        response.headers.get("Content-Type", ""))
                except HTTPError as error:
                    if error.code in {301, 302, 303, 307, 308}:
                        location = error.headers.get("Location")
                        if not location:
                            raise FetchError("Redirect has no destination", error.code) from error
                        url = urljoin(url, location)
                        redirected = True
                        break
                    if error.code in {429, 500, 502, 503, 504} and attempt < self.retries:
                        after = error.headers.get("Retry-After", "")
                        try:
                            delay = float(after)
                        except ValueError:
                            try:
                                delay = (parsedate_to_datetime(after) - datetime.now(timezone.utc)).total_seconds()
                            except (TypeError, ValueError):
                                delay = 2 ** (attempt + 1)
                        if delay > 30:
                            raise FetchError("Retry-After exceeds this run's wait budget; retry on the next run",
                                             error.code, "retry_later") from error
                        time.sleep(max(0, delay))
                        continue
                    raise FetchError(f"HTTP {error.code} for {url}", error.code, "http_error") from error
                except (URLError, TimeoutError, OSError) as error:
                    if attempt < self.retries:
                        time.sleep(2 ** (attempt + 1))
                        continue
                    raise FetchError(f"Request failed for {host}: {type(error).__name__}") from error
            if not redirected:
                break
        raise FetchError("Too many redirects", kind="invalid_url")
