"""
Website enrichment — fetch article → markdown → GPT-4o-mini → validated URL.

Pipeline per article:
  1. Fetch the article HTML (same requests session as before)
  2. Extract the main content element (<article>, <main>, etc.)
  3. Convert to clean markdown via html2text (strips nav/ads/footer)
  4. Truncate to first 600 words (company website is always in the lede)
  5. Send to gpt-4o-mini: "what is this company's website URL?"
  6. Validate the returned URL with a HEAD request — if it 404s, return None
  7. Cache result keyed by article URL

Key properties:
  - Model is reading grounded text, not recalling from memory → near-zero hallucination
  - HEAD validation catches any remaining bad URLs
  - API key prompted at runtime if not in environment (hidden input)
  - Concurrent with ThreadPoolExecutor (default 5 workers — OpenAI rate limits)
  - Cached to .enrichment_cache.json — reruns are free
"""
import getpass
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import html2text
import requests
from bs4 import BeautifulSoup
from openai import OpenAI


# ─────────────────────────────────────────────────────────────────────
# API key — environment variable or interactive prompt
# ─────────────────────────────────────────────────────────────────────

_client = None  # initialised lazily on first use


def _get_client():
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not api_key:
        print("\n  OpenAI API key not found in environment.")
        api_key = getpass.getpass("  Enter your OpenAI API key: ").strip()
    if not api_key:
        raise RuntimeError("No OpenAI API key provided — cannot run enrichment.")

    _client = OpenAI(api_key=api_key)
    return _client


# ─────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────

CACHE_PATH = Path('.enrichment_cache.json')


def _load_cache():
    if CACHE_PATH.exists():
        try:
            with CACHE_PATH.open('r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache):
    try:
        with CACHE_PATH.open('w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2)
    except OSError as e:
        print(f"  ⚠ Couldn't write cache: {e}")


# ─────────────────────────────────────────────────────────────────────
# Step 1: Fetch article HTML
# ─────────────────────────────────────────────────────────────────────

_FETCH_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}


def _fetch_html(url, timeout=15):
    try:
        r = requests.get(url, headers=_FETCH_HEADERS, timeout=timeout,
                         allow_redirects=True)
        r.raise_for_status()
        return BeautifulSoup(r.text, 'html.parser')
    except (requests.RequestException, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────
# Step 2 + 3: Extract body → clean markdown
# ─────────────────────────────────────────────────────────────────────

# html2text converter — configured once
_H2T = html2text.HTML2Text()
_H2T.ignore_links = False       # keep links — they're evidence for the model
_H2T.ignore_images = True       # drop image tags
_H2T.ignore_tables = False      # keep tables (sometimes have company info)
_H2T.body_width = 0             # no line wrapping
_H2T.unicode_snob = True
_H2T.skip_internal_links = True
_H2T.single_line_break = True   # compact output


def _find_article_body(soup):
    """Try common content selectors; fall back to <body>."""
    for selector in [
        'article',
        'main',
        '[class*="post-content"]',
        '[class*="entry-content"]',
        '[class*="article-body"]',
        '[class*="article-content"]',
        '[itemprop="articleBody"]',
    ]:
        node = soup.select_one(selector)
        if node and len(node.get_text(strip=True)) > 200:
            return node
    return soup.body or soup


def _to_markdown(soup):
    """Extract the article body and convert to compact markdown."""
    body = _find_article_body(soup)

    # Remove boilerplate elements before converting
    for tag in body.select('nav, footer, header, aside, script, style, '
                            '.ad, .ads, .advertisement, .related, .sidebar, '
                            '[class*="cookie"], [class*="newsletter"], '
                            '[class*="subscribe"], [class*="popup"]'):
        tag.decompose()

    html = str(body)
    md = _H2T.handle(html)

    # Collapse excess blank lines
    md = re.sub(r'\n{3,}', '\n\n', md).strip()
    return md


def _first_n_words(text, n=600):
    """Return the first n words of text."""
    words = text.split()
    return ' '.join(words[:n])


# ─────────────────────────────────────────────────────────────────────
# Step 4: Ask GPT-4o-mini
# ─────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a precise data extractor. Given a funding news article in markdown \
and the name of the company that received funding, find that company's \
official website URL.

Rules:
- Return ONLY the bare URL (e.g. https://acme.com) — no explanation, no markdown, no quotes.
- The URL must be the company's own website — not a news site, not social media,
  not a VC firm, not an app store link, not Wikipedia.
- Priority order:
  1. If the article contains an explicit link to the company website, return that URL.
  2. If the article clearly identifies the company, use your knowledge to return
     the most likely homepage (e.g. companyhq.com, company.io, company.com).
  3. Only return "none" if you truly have no idea what company this is.
- Prefer the root domain (https://acme.com) over deep paths.
"""


def _ask_llm(article_markdown, company_name):
    """Send article markdown + company name to GPT-4o-mini.

    Returns a URL string or None.
    """
    client = _get_client()

    user_prompt = (
        f"Company that received funding: {company_name}\n\n"
        f"Article:\n{article_markdown}"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=60,       # a URL is never longer than this
            temperature=0,       # deterministic — we want a fact, not creativity
        )
        raw = response.choices[0].message.content.strip().lower()
    except Exception as e:
        # Don't let an API error crash the whole enrichment run
        return None

    if raw in ('none', 'n/a', '', 'null', 'unknown'):
        return None

    # Basic URL sanity check
    if not raw.startswith('http'):
        raw = 'https://' + raw

    return raw


# ─────────────────────────────────────────────────────────────────────
# Step 5: Validate the URL with a HEAD request
# ─────────────────────────────────────────────────────────────────────

def _clean_url(url):
    """Remove redundant default ports (:443, :80) from URLs."""
    if not url:
        return url
    from urllib.parse import urlparse, urlunparse
    try:
        p = urlparse(url)
        if (p.scheme == 'https' and p.port == 443) or (p.scheme == 'http' and p.port == 80):
            # Rebuild without the port
            netloc = p.hostname
            if p.username:
                netloc = f"{p.username}@{netloc}"
            url = urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
    except Exception:
        pass
    return url


def _validate_url(url, timeout=8):
    """Check that the URL resolves to a real page.

    Returns the (possibly-redirected) final URL, or None if it fails.
    """
    if not url:
        return None
    try:
        r = requests.head(
            url,
            headers={'User-Agent': _FETCH_HEADERS['User-Agent']},
            timeout=timeout,
            allow_redirects=True,
        )
        # Accept 2xx and 3xx (redirects) and even 405 (method not allowed —
        # some servers reject HEAD but accept GET, which means the site exists)
        if r.status_code < 500:
            final = r.url if r.url else url
            return _clean_url(final)
    except (requests.RequestException, ValueError):
        pass
    return None


# ─────────────────────────────────────────────────────────────────────
# Per-entry pipeline
# ─────────────────────────────────────────────────────────────────────

def _enrich_one(entry):
    """Full pipeline for a single entry. Returns (entry, website_or_None, status)."""
    article_url = entry.get('url', '')
    company_name = entry.get('company_name', '')

    # Step 1: fetch
    soup = _fetch_html(article_url)
    if soup is None:
        return entry, None, 'fetch_failed'

    # Steps 2+3: article body → markdown → first 600 words
    md = _to_markdown(soup)
    truncated = _first_n_words(md, 600)

    if len(truncated.split()) < 30:
        # Almost no content — paywalled or JS-rendered page
        return entry, None, 'no_content'

    # Step 4: ask LLM
    raw_url = _ask_llm(truncated, company_name)
    if raw_url is None:
        return entry, None, 'llm_no_result'

    # Step 5: validate
    validated = _validate_url(raw_url)
    if validated is None:
        return entry, None, 'validation_failed'

    return entry, validated, 'ok'


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────

def enrich_entries(entries, max_workers=5, verbose=True):
    """Fill entry['company_website'] for each entry in-place.

    Uses a local cache (.enrichment_cache.json) so reruns are free.
    max_workers default is 5 — conservative for OpenAI rate limits.
    Increase to 10 if you have a paid tier with higher RPM.
    """
    # Prompt for API key once up-front, before spawning threads
    _get_client()

    cache = _load_cache()
    cache_hits = 0
    cache_misses = 0
    found = 0
    stats = {'fetch_failed': 0, 'no_content': 0,
             'llm_no_result': 0, 'validation_failed': 0, 'ok': 0}

    work = []
    for entry in entries:
        if not entry.get('company_name') or not entry.get('url'):
            continue
        url = entry['url']
        if url in cache:
            entry['company_website'] = cache[url]
            cache_hits += 1
            if cache[url]:
                found += 1
            continue
        work.append(entry)
        cache_misses += 1

    if verbose:
        print(f"  Cache hits: {cache_hits}, to process: {cache_misses}")

    if not work:
        if verbose:
            print(f"  Websites found: {found}/{cache_hits}")
        return entries

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_enrich_one, e): e for e in work}
        for i, future in enumerate(as_completed(futures), 1):
            try:
                entry, website, status = future.result()
            except Exception as e:
                if verbose:
                    print(f"  ⚠ Crashed: {type(e).__name__}: {e}")
                status = 'fetch_failed'
                entry = futures[future]
                website = None

            stats[status] = stats.get(status, 0) + 1
            entry['company_website'] = website
            cache[entry['url']] = website
            if website:
                found += 1

            if verbose and i % 10 == 0:
                print(f"  ... processed {i}/{len(work)}")

    _save_cache(cache)

    if verbose:
        total = cache_hits + cache_misses
        print(f"  Websites found:    {found}/{total}")
        print(f"  Breakdown:")
        print(f"    Fetch failed:    {stats.get('fetch_failed', 0)}")
        print(f"    No content:      {stats.get('no_content', 0)}")
        print(f"    LLM no result:   {stats.get('llm_no_result', 0)}")
        print(f"    URL invalid:     {stats.get('validation_failed', 0)}")
        print(f"    Found + valid:   {stats.get('ok', 0)}")

    return entries
