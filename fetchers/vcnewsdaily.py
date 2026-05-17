"""
VC News Daily fetcher.

VC News Daily doesn't expose an RSS feed. We scrape the homepage.
Their HTML structure has changed in the past, so we try multiple
title-element selectors (h5, h4, h2 inside articles) and fall back
to article-level <a> tags if none match.

If no entries can be parsed we print a clear warning rather than
silently returning 0.
"""
import re
from datetime import datetime, timezone
from dateutil import parser as date_parser

from .base import BaseFetcher


class VCNewsDailyFetcher(BaseFetcher):
    name = "VC News Daily"
    homepage_url = "https://vcnewsdaily.com/"
    feed_url = "https://vcnewsdaily.com/"  # used by base error messages

    # "15 May 2026" or "May 15, 2026" or "2026-05-15"
    _DATE_PATTERNS = [
        re.compile(r'\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\b'),
        re.compile(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b'),
        re.compile(r'\b\d{4}-\d{2}-\d{2}\b'),
    ]

    # Selectors to try, in order of preference
    _TITLE_SELECTORS = [
        'h5 a[href]',
        'h4 a[href]',
        'article h2 a[href]',
        'article h3 a[href]',
        '.post-title a[href]',
        '.entry-title a[href]',
    ]

    def fetch(self, since_date):
        soup = self._fetch_html(self.homepage_url)
        results = []

        title_links = self._find_title_links(soup)
        if not title_links:
            print(f"\n    ⚠ VC News Daily: no article titles found on homepage. "
                  f"Site structure may have changed. Returning 0 entries.")
            return results

        for link in title_links:
            title = link.get_text(strip=True)
            href = link.get('href', '')
            if not title or not href:
                continue
            if not href.startswith('http'):
                href = 'https://vcnewsdaily.com/' + href.lstrip('/')

            pub_date = self._find_date_near(link)
            if pub_date is None or pub_date < since_date:
                continue

            summary = self._find_summary_near(link)

            results.append({
                'source': self.name,
                'title': title,
                'url': href,
                'published_date': pub_date,
                'summary': summary,
            })

        return results

    def _find_title_links(self, soup):
        """Try selectors in order; return the first non-empty match list."""
        for selector in self._TITLE_SELECTORS:
            matches = soup.select(selector)
            if matches:
                return matches
        return []

    def _find_date_near(self, link):
        """Search nearby siblings for a date — bounded so we don't drift
        into the next article's date.
        """
        anchor = link.parent or link
        candidates = [anchor]
        node = anchor
        for _ in range(5):
            node = node.find_next_sibling()
            if node is None:
                break
            candidates.append(node)

        for node in candidates:
            if hasattr(node, 'get_text'):
                text = node.get_text(separator=' ')
            else:
                text = str(node)
            for pat in self._DATE_PATTERNS:
                m = pat.search(text)
                if m:
                    try:
                        dt = date_parser.parse(m.group(0))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt
                    except (ValueError, TypeError):
                        continue
        return None

    def _find_summary_near(self, link):
        node = link.parent or link
        for _ in range(5):
            node = node.find_next(['p'])
            if node is None:
                break
            text = node.get_text(separator=' ', strip=True)
            if text and len(text) > 30:
                return text
        return ''
