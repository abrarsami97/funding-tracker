"""
Base fetcher class. All source-specific fetchers inherit from this.

Each subclass only needs to set `name` and `feed_url`. Override
`fetch()` entirely for sources that don't expose RSS (e.g. HTML scrapers).
"""
import re
import feedparser
import requests
from datetime import datetime, timezone
from dateutil import parser as date_parser
from bs4 import BeautifulSoup


# Strip XML-invalid control chars (anything outside the legal XML 1.0 range).
# Some WordPress feeds (e.g. FinSMEs) emit raw control chars that crash strict
# parsers like expat.
_XML_INVALID_CHARS = re.compile(
    rb'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]'
)


class BaseFetcher:
    name = "base"
    feed_url = ""
    # Spoof a real browser UA. Many feeds return 403 or HTML to bot UAs.
    user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    request_timeout = 30

    def fetch(self, since_date):
        """Fetch entries from feed published on or after since_date.

        Args:
            since_date: timezone-aware datetime

        Returns:
            list of dicts with keys: source, title, url, published_date, summary
        """
        # Use requests to fetch — gives us redirect handling, gzip, and bypasses
        # feedparser's strict content-type check.
        response = requests.get(
            self.feed_url,
            headers={
                'User-Agent': self.user_agent,
                'Accept': 'application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8',
            },
            timeout=self.request_timeout,
        )
        response.raise_for_status()

        # Pre-clean invalid XML chars before feedparser
        content = _XML_INVALID_CHARS.sub(b'', response.content)

        feed = feedparser.parse(content)

        # If the response wasn't actually RSS/Atom, feedparser returns empty entries
        # without raising. Detect that and fail loudly so users know.
        if not feed.entries and feed.bozo:
            err = feed.bozo_exception
            raise RuntimeError(f"Feed parse error: {err}")

        results = []
        for entry in feed.entries:
            parsed = self.parse_entry(entry)
            if parsed is None or parsed['published_date'] is None:
                continue
            if parsed['published_date'] >= since_date:
                results.append(parsed)
        return results

    def parse_entry(self, entry):
        """Convert a feedparser entry into our standard dict format."""
        return {
            'source': self.name,
            'title': (entry.get('title') or '').strip(),
            'url': (entry.get('link') or '').strip(),
            'published_date': self._parse_date(entry),
            'summary': self._clean_html(entry.get('summary', '')),
        }

    @staticmethod
    def _parse_date(entry):
        """Try multiple date fields and formats."""
        if entry.get('published_parsed'):
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        if entry.get('updated_parsed'):
            return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
        for field in ['published', 'updated', 'pubDate', 'dc:date']:
            raw = entry.get(field)
            if raw:
                try:
                    dt = date_parser.parse(raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except (ValueError, TypeError):
                    continue
        return None

    @staticmethod
    def _clean_html(html):
        """Strip HTML tags from a summary, collapse whitespace."""
        if not html:
            return ''
        text = BeautifulSoup(html, 'html.parser').get_text(separator=' ')
        return ' '.join(text.split())

    def _fetch_html(self, url):
        """Helper for HTML-scraping fetchers (e.g. VC News Daily)."""
        response = requests.get(
            url,
            headers={'User-Agent': self.user_agent},
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
