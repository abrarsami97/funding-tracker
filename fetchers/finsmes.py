"""
FinSMEs fetcher.

Global VC/PE/debt financing deals. Country and sector agnostic.

FinSMEs has aggressive bot protection that returns 403 to many UAs.
We override `fetch()` to send a full set of browser-like headers and
give a clearer message when still blocked.
"""
import feedparser
import requests

from .base import BaseFetcher, _XML_INVALID_CHARS


class FinSMEsFetcher(BaseFetcher):
    name = "FinSMEs"
    feed_url = "https://www.finsmes.com/feed"

    _EXTRA_HEADERS = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
    }

    def fetch(self, since_date):
        headers = {
            'User-Agent': self.user_agent,
            **self._EXTRA_HEADERS,
        }
        try:
            response = requests.get(
                self.feed_url,
                headers=headers,
                timeout=self.request_timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                print(f"\n    ⚠ FinSMEs returned 403 (bot protection). "
                      f"This source can't be reached from this network. "
                      f"Try running from a residential IP, or remove it "
                      f"from FETCHERS in main.py.")
                return []
            raise

        content = _XML_INVALID_CHARS.sub(b'', response.content)
        feed = feedparser.parse(content)
        if not feed.entries and feed.bozo:
            raise RuntimeError(f"Feed parse error: {feed.bozo_exception}")

        results = []
        for entry in feed.entries:
            parsed = self.parse_entry(entry)
            if parsed is None or parsed['published_date'] is None:
                continue
            if parsed['published_date'] >= since_date:
                results.append(parsed)
        return results
