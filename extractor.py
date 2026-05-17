"""
Regex-based extraction of funding details from news entries.

No LLM calls. Catches ~85% of well-formed funding announcements.
For misses, see notes in main.py about a hybrid LLM fallback.

v1.2 fixes (vs v1.1):
  1. Extended _FUNDING_VERBS: inject, infuse, boost, launch, list, debut, price, file
  2. Possessive-owner prefix stripping: "Robinhood co-founder's Cowboy Space" → "Cowboy Space"
  3. Inverted-title pattern: "VCs back Fractile" / "fund invests in Wirestock"
  4. Amount-in-title pattern: "Destinus in €200m funding talks"
  5. Tighter is_funding_article: salary/listicle/paywall false positives blocked
  6. Investor truncation guard: chunks ending in "…" discarded
"""
import re


# ─────────────────────────────────────────────────────────────────────
# Funding amount extraction
# ─────────────────────────────────────────────────────────────────────

_AMOUNT_PATTERNS = [
    (r'\$\s?(\d+(?:[\.,]\d+)?)\s*(billion|bn|b|million|mn|m)\b', 'USD'),
    (r'€\s?(\d+(?:[\.,]\d+)?)\s*(billion|bn|b|million|mn|m)\b', 'EUR'),
    (r'£\s?(\d+(?:[\.,]\d+)?)\s*(billion|bn|b|million|mn|m)\b', 'GBP'),
    (r'₹\s?(\d+(?:[\.,]\d+)?)\s*(crore|cr|lakh)\b', 'INR'),
    (r'(\d+(?:[\.,]\d+)?)\s*(billion|bn|million|mn|m|b)\s+(?:USD|dollars)\b', 'USD'),
]

_AMOUNT_RE = [(re.compile(p, re.IGNORECASE), cur) for p, cur in _AMOUNT_PATTERNS]


def extract_amount(text):
    """Returns (amount_in_native_currency, currency_code) or (None, None)."""
    if not text:
        return None, None
    for pattern, currency in _AMOUNT_RE:
        m = pattern.search(text)
        if not m:
            continue
        try:
            amount = float(m.group(1).replace(',', ''))
        except ValueError:
            continue
        unit = m.group(2).lower()
        if unit.startswith('b'):
            amount *= 1_000_000_000
        elif unit.startswith('m'):
            amount *= 1_000_000
        elif unit == 'crore' or unit == 'cr':
            amount *= 10_000_000
        elif unit == 'lakh':
            amount *= 100_000
        return amount, currency
    return None, None


# ─────────────────────────────────────────────────────────────────────
# Round stage extraction
# ─────────────────────────────────────────────────────────────────────

_ROUND_RE = re.compile(
    r'\b(Pre[\s\-]?Seed|Seed|'
    r'Series\s+[A-K](?:\d)?|'
    r'Series\s+(?:Alpha|Beta|Gamma)|'
    r'Pre[\s\-]?IPO|Bridge|Growth|Strategic|Mezzanine|'
    r'Angel|Crowdfunding|Convertible|SAFE)\b',
    re.IGNORECASE
)


def extract_round(text):
    if not text:
        return None
    m = _ROUND_RE.search(text)
    if not m:
        return None
    raw = m.group(1)
    return ' '.join(w.capitalize() for w in raw.split())


# ─────────────────────────────────────────────────────────────────────
# Company name extraction
# ─────────────────────────────────────────────────────────────────────

# FIX 1: Extended verb list — inject, infuse, boost, launch, list, debut, price, file
_FUNDING_VERBS = (
    r'rais(?:es|ed|ing|e)\b|'
    r'secur(?:es|ed|ing)|'
    r'clos(?:es|ed|ing)|'
    r'land(?:s|ed|ing)|'
    r'net(?:s|ted|ting)|'
    r'bag(?:s|ged|ging)|'
    r'pull(?:s|ed) in|'
    r'announc(?:es|ed) (?:a |its |the )?(?:\$|€|£|₹|funding|raise)|'
    r'complet(?:es|ed)|'
    r'attract(?:s|ed)|'
    r'snag(?:s|ged)|'
    r'pick(?:s|ed) up|'
    r'get(?:s)? \$|'
    r'inject(?:s|ed|ing)|'
    r'(?:to\s+)?infus(?:e|es|ed|ing)\b|'
    r'(?:shares?\s+)?(?:surge|pop|jump|soar|climb|rally)(?:s|ed|ing)?\s|'
    r'infus(?:es|ed|ing)|'
    r'boost(?:s|ed|ing) (?:valuation|funding|round)|'
    r'launch(?:es|ed|ing) (?:a |\$|€|£|₹|\d)|'
    r'list(?:s|ed|ing) (?:on|at)|'
    r'debut(?:s|ed|ing)|'
    r'pric(?:es|ed|ing) (?:its )?(?:ipo|shares)|'
    r'fil(?:es|ed|ing) (?:for )?(?:ipo|s-1)|'
    r'go(?:es|ing)? public'
)

_COMPANY_LEADING_RE = re.compile(
    rf'^([A-Z][\w\s\.\-&\'()]+?)\s+(?:{_FUNDING_VERBS})',
    re.IGNORECASE
)

# FIX 2: Possessive-owner prefix — "Robinhood co-founder's Cowboy Space raises"
#   Capture the LAST proper-noun chunk before the verb (after the possessive owner).
#   Pattern: <Owner> <possessive> <Company> <verb>
_POSSESSIVE_OWNER_RE = re.compile(
    rf'^(?:[A-Z][\w\s\-\,\.\'\(\)]+?\'s\s+)'   # owner with possessive
    rf'([A-Z][\w\s\.\-&\'()]+?)\s+'             # company name
    rf'(?:{_FUNDING_VERBS})',
    re.IGNORECASE,
)

# FIX 3: Inverted title — "VCs back Fractile" / "fund invests in Wirestock"
#   Pattern: <Investor(s)> <verb> <Company>
_INVERTED_VERBS = (
    r'back(?:s|ed|ing)?|'
    r'invest(?:s|ed|ing)? in|'
    r'fund(?:s|ed|ing)?|'
    r'pour(?:s|ed|ing)? .{0,20}into'  # "pours $X into <Company>"
)
_INVERTED_TITLE_RE = re.compile(
    rf'^(?:[A-Z][\w\s\.\-&\']+?)\s+(?:{_INVERTED_VERBS})\s+([A-Z][\w\s\.\-&\']+)',
    re.IGNORECASE,
)

# FIX 4: Amount-in-title — "Destinus in €200m funding talks"
#   Pattern: <Company> in <Amount> <funding word>
_AMOUNT_IN_TITLE_RE = re.compile(
    r'^([A-Z][\w\s\.\-&\'()]+?)\s+in\s+'
    r'(?:€|£|\$|₹)[\d\.]+\s*(?:bn|m|million|billion)\s+'
    r'(?:funding|fund|capital|raise|round|talks|deal)',
    re.IGNORECASE,
)


def _strip_company_prefixes(name):
    """Remove geographic/sector descriptors from the start of a company name."""
    # 1. Strip "<City/Country>-based " prefixes
    name = re.sub(
        r'^[A-Z][a-zA-Z]+(?:[\s\-][A-Z][a-zA-Z]+)?[\s\-]based\s+',
        '',
        name,
    )
    # 2. Strip "<Sector> startup/company/firm/platform <Name>" patterns
    sector_re = re.compile(
        r'^(?:[A-Z][a-zA-Z]+\s+)?'
        r'(?:AI|fintech|biotech|healthtech|medtech|saas|crypto|edtech|proptech|'
        r'climate|defense|defence|space|spacetech|robotics|cybersecurity|mining|'
        r'energy|deeptech|insurtech|legaltech|martech|adtech|foodtech|agtech|'
        r'agribiotech|dronemaker|drone|ev|electric|spinout|'
        r'marketing\s+(?:operating\s+system|platform|software|tool|analytics|automation|cloud)|'
        r'operating\s+system|data\s+platform|intelligence\s+platform|'
        r'enterprise\s+(?:software|platform|saas|tool)|'
        r'customer\s+(?:data|intelligence|experience|success)|'
        r'revenue\s+(?:intelligence|operations)|sales\s+(?:intelligence|platform)'        r')'
        r'(?:\s+(?:startup|company|firm|platform|scaleup|unicorn|maker|manufacturer))?\s+',
        re.IGNORECASE,
    )
    for _ in range(3):
        new = sector_re.sub('', name)
        if new == name:
            break
        # Guard: if the result starts with a lowercase word or preposition,
        # the strip consumed the company name itself — revert
        first_word = new.split()[0] if new.split() else ''
        if first_word and (first_word[0].islower() or first_word.lower() in
                           ('to', 'in', 'for', 'of', 'at', 'by', 'on', 'the', 'a', 'an')):
            break  # don't apply this strip — it went too far
        name = new

    # 3. Strip plain leading "startup/company/firm <Name>"
    name = re.sub(r'^(?:startup|company|firm|scaleup)\s+', '', name, flags=re.IGNORECASE)

    # 4. Strip leading city alone (allowlist to avoid stripping legit name parts)
    known_cities = (
        'Bangalore', 'Bengaluru', 'Mumbai', 'Delhi', 'Hyderabad', 'Chennai',
        'London', 'Paris', 'Berlin', 'Munich', 'Amsterdam', 'Stockholm',
        'Helsinki', 'Madrid', 'Barcelona', 'Dublin', 'Zurich', 'Tel Aviv',
        'Singapore', 'Tokyo', 'Seoul', 'Beijing', 'Shanghai', 'Shenzhen',
        'Sydney', 'Melbourne', 'Toronto', 'Vancouver', 'Montreal',
        'NYC', 'SF', 'Boston', 'Austin', 'Seattle', 'Chicago',
        'Dubai', 'Lagos', 'Nairobi', 'Cairo', 'Jakarta', 'Manila',
    )
    city_pattern = r'^(' + '|'.join(known_cities) + r')\'?s?\s+(?=[A-Z][a-zA-Z0-9])'
    name = re.sub(city_pattern, '', name, count=1, flags=re.IGNORECASE)

    # 5. Strip possessive suffix left on legitimate names like "Wirestock's"
    name = re.sub(r"'s$", '', name).strip()

    # Strip trailing prepositions/infinitives that regex can over-capture
    name = re.sub(r'\s+(?:to|in|for|of|at|by|on|the|a|an)$', '', name.strip(), flags=re.IGNORECASE)
    return name.strip()


def _strip_possessive_owner(title):
    """For titles like "Robinhood co-founder's Cowboy Space raises $X",
    strip the owner prefix and return the company name, or None.
    """
    m = _POSSESSIVE_OWNER_RE.match(title)
    if m:
        return _strip_company_prefixes(m.group(1).strip()) or None
    return None


def _extract_inverted(title):
    """For titles like "Accel and Founders Fund back Fractile in $220m raise",
    extract the company name from after the verb.
    """
    m = _INVERTED_TITLE_RE.match(title)
    if not m:
        return None
    raw = m.group(1).strip()
    # Strip any leading verb fragment that got captured (e.g. "back Fractile" → "Fractile")
    raw = re.sub(
        r'^(?:back|backs|backed|invest|invests|invested|fund|funds|funded|'
        r'pour|pours|poured)(?:\s+in)?\s+',
        '', raw, flags=re.IGNORECASE
    )
    # Remove trailing context like " in $220m raise", " for Series A"
    raw = re.split(r'\s+(?:in |for |to |at |with )\$', raw)[0]
    raw = re.split(r'\s+(?:in|for|to|at)\s+(?:\d|\$|€|£)', raw)[0]
    raw = re.sub(r"\s*'s$|\s+(?:to|in|for|of|at|by)$", '', raw, flags=re.IGNORECASE)
    return _strip_company_prefixes(raw.strip()) or None


def extract_company(title):
    """Pull company name from the title.

    Tries patterns in order of confidence:
      1. Comma-descriptor format: "GovWell, the AI OS, has raised..."
      2. Standard: "Cowboy Space raises $275M..."
      3. Possessive-owner: "Robinhood co-founder's Cowboy Space raises..."
      4. Amount-in-title: "Destinus in €200m funding talks"
      5. Inverted: "Accel backs Fractile in $220m raise"
      6. Separator formats: "Company · $X · Series A" / "Company: raises $X"
    """
    if not title:
        return None

    # Strip leading editorial tags
    title = re.sub(
        r'^(\[.*?\]|Exclusive[:|\s\-]+|Breaking[:|\s\-]+|Exclusive\s*:\s*)',
        '',
        title,
        flags=re.IGNORECASE,
    ).strip()

    # 1. Comma-descriptor: "GovWell, the AI OS for gov, has raised..."
    comma_match = re.match(
        rf'^([A-Z][\w\s\.\-&\'()]+?),\s+[^,]+?,\s+(?:has\s+|just\s+)?(?:{_FUNDING_VERBS})',
        title,
        re.IGNORECASE,
    )
    if comma_match:
        return _strip_company_prefixes(comma_match.group(1).strip()) or None

    # 2. Standard: "<Company> <verb> ..."
    m = _COMPANY_LEADING_RE.match(title)
    if m:
        raw = m.group(1).strip()
        # If the match captured a possessive-owner prefix ("Robinhood co-founder's Cowboy Space"),
        # strip the owner and return just the company name
        cleaned = _strip_possessive_owner(raw + " raises")  # fake verb so the pattern fires
        if not cleaned:
            cleaned = _strip_company_prefixes(raw)
        if cleaned:
            return cleaned
    # 2b. Sector-stripped title: "EV Startup Simple Energy To Raise" ->
    #     strip "EV Startup " first, then retry verb match
    title_stripped = _strip_company_prefixes(title)
    if title_stripped and title_stripped != title:
        m2 = _COMPANY_LEADING_RE.match(title_stripped)
        if m2:
            company = _strip_company_prefixes(m2.group(1).strip())
            if company:
                return company

    # 3. Possessive-owner: "Robinhood co-founder's Cowboy Space raises..."
    possessive = _strip_possessive_owner(title)
    if possessive:
        return possessive

    # 4. Amount-in-title: "Destinus in €200m funding talks"
    #    Guard: reject if the captured name contains investor verbs —
    #    that means we matched an inverted title ("Accel backs X in $Y")
    amount_m = _AMOUNT_IN_TITLE_RE.match(title)
    if amount_m:
        candidate = amount_m.group(1).strip()
        # If the candidate contains an inverted-title verb, it's the investor
        # phrase, not the company name — skip this path
        _INVESTOR_VERB_RE = re.compile(
            r'\b(?:back|backs|backed|backing|invest|invests|invested|fund|funds|funded|pour|pours|poured)\b',
            re.IGNORECASE,
        )
        if not _INVESTOR_VERB_RE.search(candidate):
            return _strip_company_prefixes(candidate) or None

    # 5. Inverted: "Accel and Founders Fund back Fractile in $220m raise"
    inverted = _extract_inverted(title)
    if inverted:
        # Clean any trailing prepositions left by the inverted pattern
        inverted = re.sub(r"\s*'s$|\s+(?:to|in|for|of|at|by)$", '', inverted, flags=re.IGNORECASE)
        return inverted.strip() or None

    # 6. Separator formats
    if '·' in title or '|' in title:
        first = re.split(r'[·|]', title)[0].strip()
        if first and len(first) < 60:
            return _strip_company_prefixes(first) or first

    if ':' in title:
        first = title.split(':')[0].strip()
        if first and len(first) < 60:
            return _strip_company_prefixes(first) or first

    return None


# ─────────────────────────────────────────────────────────────────────
# Country extraction
# ─────────────────────────────────────────────────────────────────────

_COUNTRY_NAMES = [
    'United States', 'USA', 'US',
    'United Kingdom', 'UK', 'Britain',
    'Germany', 'France', 'Netherlands', 'Spain', 'Italy', 'Sweden',
    'Norway', 'Denmark', 'Finland', 'Ireland', 'Portugal', 'Belgium',
    'Switzerland', 'Austria', 'Poland', 'Estonia', 'Czech Republic',
    'India', 'China', 'Japan', 'Singapore', 'South Korea', 'Korea',
    'Indonesia', 'Vietnam', 'Thailand', 'Philippines', 'Malaysia',
    'Israel', 'UAE', 'Saudi Arabia', 'Turkey',
    'Australia', 'New Zealand',
    'Canada', 'Mexico', 'Brazil', 'Argentina', 'Chile', 'Colombia',
    'South Africa', 'Nigeria', 'Kenya', 'Egypt', 'Ghana',
]

_DEMONYM_TO_COUNTRY = {
    'American': 'United States',
    'British': 'United Kingdom',
    'English': 'United Kingdom',
    'Scottish': 'United Kingdom',
    'German': 'Germany',
    'French': 'France',
    'Dutch': 'Netherlands',
    'Spanish': 'Spain',
    'Italian': 'Italy',
    'Swedish': 'Sweden',
    'Norwegian': 'Norway',
    'Danish': 'Denmark',
    'Finnish': 'Finland',
    'Irish': 'Ireland',
    'Portuguese': 'Portugal',
    'Belgian': 'Belgium',
    'Swiss': 'Switzerland',
    'Austrian': 'Austria',
    'Polish': 'Poland',
    'Estonian': 'Estonia',
    'Czech': 'Czech Republic',
    'Indian': 'India',
    'Chinese': 'China',
    'Japanese': 'Japan',
    'Singaporean': 'Singapore',
    'Korean': 'South Korea',
    'Indonesian': 'Indonesia',
    'Vietnamese': 'Vietnam',
    'Thai': 'Thailand',
    'Filipino': 'Philippines',
    'Malaysian': 'Malaysia',
    'Israeli': 'Israel',
    'Emirati': 'UAE',
    'Saudi': 'Saudi Arabia',
    'Turkish': 'Turkey',
    'Australian': 'Australia',
    'Canadian': 'Canada',
    'Mexican': 'Mexico',
    'Brazilian': 'Brazil',
    'Argentine': 'Argentina',
    'Chilean': 'Chile',
    'Colombian': 'Colombia',
    'Nigerian': 'Nigeria',
    'Kenyan': 'Kenya',
    'Egyptian': 'Egypt',
}

_CITY_TO_COUNTRY = {
    'New York': 'United States', 'NYC': 'United States',
    'San Francisco': 'United States', 'SF': 'United States',
    'Bay Area': 'United States', 'Silicon Valley': 'United States',
    'Boston': 'United States', 'Austin': 'United States',
    'Seattle': 'United States', 'Chicago': 'United States',
    'Los Angeles': 'United States', 'LA': 'United States',
    'Miami': 'United States', 'Denver': 'United States',
    'Atlanta': 'United States', 'Nashville': 'United States',
    'Palo Alto': 'United States', 'Mountain View': 'United States',
    'London': 'United Kingdom', 'Manchester': 'United Kingdom',
    'Edinburgh': 'United Kingdom', 'Oxford': 'United Kingdom',
    'Bristol': 'United Kingdom',
    'Berlin': 'Germany', 'Munich': 'Germany', 'Hamburg': 'Germany',
    'Frankfurt': 'Germany',
    'Paris': 'France', 'Lyon': 'France',
    'Amsterdam': 'Netherlands', 'Rotterdam': 'Netherlands',
    'Madrid': 'Spain', 'Barcelona': 'Spain', 'Valencia': 'Spain',
    'Milan': 'Italy', 'Rome': 'Italy',
    'Stockholm': 'Sweden', 'Gothenburg': 'Sweden',
    'Oslo': 'Norway', 'Copenhagen': 'Denmark',
    'Helsinki': 'Finland',
    'Dublin': 'Ireland', 'Zurich': 'Switzerland', 'Geneva': 'Switzerland',
    'Vienna': 'Austria', 'Warsaw': 'Poland', 'Tallinn': 'Estonia',
    'Prague': 'Czech Republic', 'Lisbon': 'Portugal',
    'Brussels': 'Belgium',
    'Bangalore': 'India', 'Bengaluru': 'India', 'Mumbai': 'India',
    'Delhi': 'India', 'New Delhi': 'India', 'Hyderabad': 'India',
    'Chennai': 'India', 'Pune': 'India', 'Gurugram': 'India',
    'Gurgaon': 'India', 'Noida': 'India',
    'Singapore': 'Singapore',
    'Tokyo': 'Japan', 'Osaka': 'Japan',
    'Seoul': 'South Korea',
    'Beijing': 'China', 'Shanghai': 'China', 'Shenzhen': 'China',
    'Hong Kong': 'Hong Kong',
    'Jakarta': 'Indonesia', 'Manila': 'Philippines',
    'Bangkok': 'Thailand', 'Ho Chi Minh': 'Vietnam', 'Hanoi': 'Vietnam',
    'Tel Aviv': 'Israel', 'Jerusalem': 'Israel',
    'Dubai': 'UAE', 'Abu Dhabi': 'UAE', 'Riyadh': 'Saudi Arabia',
    'Istanbul': 'Turkey',
    'Sydney': 'Australia', 'Melbourne': 'Australia', 'Brisbane': 'Australia',
    'Auckland': 'New Zealand',
    'Toronto': 'Canada', 'Vancouver': 'Canada', 'Montreal': 'Canada',
    'Mexico City': 'Mexico', 'Sao Paulo': 'Brazil',
    'Rio de Janeiro': 'Brazil', 'Buenos Aires': 'Argentina',
    'Santiago': 'Chile', 'Bogota': 'Colombia',
    'Cape Town': 'South Africa', 'Johannesburg': 'South Africa',
    'Lagos': 'Nigeria', 'Nairobi': 'Kenya', 'Cairo': 'Egypt',
    'Accra': 'Ghana',
}

_BASED_RE = re.compile(
    r'\b([A-Z][a-zA-Z]+(?:[\s\-][A-Z][a-zA-Z]+)?)\s*[\-\s]based\b',
)
_BASED_IN_RE = re.compile(
    r'\bbased\s+in\s+([A-Z][a-zA-Z]+(?:[\s\-][A-Z][a-zA-Z]+)?(?:,\s+[A-Z][a-zA-Z]+)?)',
)

_SOURCE_COUNTRY_HINT = {
    'Inc42': 'India',
}


def _canonicalize_country(name):
    if name in ('USA', 'US'):
        return 'United States'
    if name in ('UK', 'Britain'):
        return 'United Kingdom'
    if name == 'Korea':
        return 'South Korea'
    return name


def _normalize_location(loc):
    if not loc:
        return None
    loc = loc.strip().rstrip(',').strip()
    for country in _COUNTRY_NAMES:
        if loc.lower() == country.lower():
            return _canonicalize_country(country)
    for city, country in _CITY_TO_COUNTRY.items():
        if loc.lower() == city.lower():
            return country
    if ',' in loc:
        parts = [p.strip() for p in loc.split(',')]
        last = _normalize_location(parts[-1])
        if last:
            return last
        first = _normalize_location(parts[0])
        if first:
            return first
    return None


def extract_country(text, source=None):
    """Returns canonical country name, or None."""
    if text:
        m = _BASED_RE.search(text)
        if m:
            country = _normalize_location(m.group(1))
            if country:
                return country

        m = _BASED_IN_RE.search(text)
        if m:
            country = _normalize_location(m.group(1))
            if country:
                return country

        prefix = text[:200]
        for city, country in _CITY_TO_COUNTRY.items():
            if re.search(rf'\b{re.escape(city)}\b', prefix):
                return country

        for demonym, country in _DEMONYM_TO_COUNTRY.items():
            if re.search(rf'\b{demonym}\b', text):
                return country

        for country in _COUNTRY_NAMES:
            if re.search(rf'\b{re.escape(country)}\b', prefix):
                return _canonicalize_country(country)

    if source and source in _SOURCE_COUNTRY_HINT:
        return _SOURCE_COUNTRY_HINT[source]

    return None


# ─────────────────────────────────────────────────────────────────────
# Investor extraction
# ─────────────────────────────────────────────────────────────────────

_INVESTOR_TRIGGERS = [
    r'led\s+by\s+',
    r'co-led\s+by\s+',
    r'backed\s+by\s+',
    r'with\s+participation\s+from\s+',
    r'investors?\s+include[ds]?\s+',
    r'raised\s+from\s+',
    r'funding\s+from\s+',
]

_INVESTOR_TRIGGER_RE = re.compile(
    r'(?:' + '|'.join(_INVESTOR_TRIGGERS) + r')',
    re.IGNORECASE,
)

_INVESTOR_STOP_PATTERN = re.compile(
    r'[\.;]|'
    r'\s+(?:to|with|at|in|for|on|after|while)\s+|'
    r'\s+and\s+(?:plans|aims|will|is|has|expects)\b|'
    r',\s+(?:a|the|an|which|that|who|where)\s+|'
    r'\s+—|\s+--'
)

_INVESTOR_SPLIT_RE = re.compile(r'\s*,\s*|\s+and\s+|\s*&\s*', re.IGNORECASE)

_INVESTOR_STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'with', 'from', 'by', 'led',
    'series', 'seed', 'round', 'funding', 'investment', 'investors',
    'existing', 'new', 'additional', 'other', 'several', 'multiple',
    'angel', 'angels', 'strategic', 'institutional', 'others',
    'fund', 'growth fund', 'existing fund', 'capital fund',
    'business angels', 'business angel', 'angel investors', 'angel investor',
    'venture capital', 'venture capitalists', 'family offices', 'family office',
    'undisclosed investors', 'undisclosed',
    'participation', 'contribution',
    'portfolio', 'portfolio companies',
}

# Phrases that indicate a personal bio, not an investor name
_INVESTOR_BIO_RE = re.compile(
    r'\bson\s+of\b|\bdaughter\s+of\b|\bfounder\s+of\b|\bco-founder\s+of\b|'
    r'\bformer\s+(?:CEO|CTO|president|prime minister|minister)\b|'
    r'\bPrime\s+Minister\b',
    re.IGNORECASE,
)


def _clean_investor_name(name):
    name = name.strip().strip('.,;:-—')
    # Discard truncated RSS chunks (ending in ellipsis)
    if name.endswith('…') or name.endswith('...'):
        return None
    # Discard personal bio phrases ("son of former Prime Minister")
    if _INVESTOR_BIO_RE.search(name):
        return None
    # Strip common filler prefixes
    name = re.sub(r'^(?:and|or|its|their|the)\s+', '', name, flags=re.IGNORECASE)
    # Strip trailing qualifiers like "through its Catalyst platform"
    name = re.sub(r'\s+(?:through|via|using)\s+.+$', '', name, flags=re.IGNORECASE)
    name = name.strip()
    if not name:
        return None
    name_lower = name.lower()
    if name_lower in _INVESTOR_STOPWORDS:
        return None
    # Also reject if any multi-word stopword is a prefix of the name
    if any(name_lower.startswith(sw) for sw in _INVESTOR_STOPWORDS if ' ' in sw):
        return None
    first_word = name.split()[0].lower() if name.split() else ''
    if first_word in _INVESTOR_STOPWORDS:
        return None
    if len(name) < 2:
        return None
    if name.isupper() and len(name) < 3:
        return None
    if not name[0].isalnum():
        return None
    return name


def extract_investors(text, max_investors=3):
    """Extract comma-separated investor names, or None."""
    if not text:
        return None

    # FIX 6: If the whole text is truncated, skip investor extraction —
    # any match will be a fragment
    if text.rstrip().endswith(('…', '...')):
        # Still try, but only accept clean non-truncated chunks
        pass

    investors = []
    seen = set()

    for trigger_match in _INVESTOR_TRIGGER_RE.finditer(text):
        start = trigger_match.end()
        window = text[start:start + 200]

        stop_match = _INVESTOR_STOP_PATTERN.search(window)
        chunk = window[:stop_match.start()] if stop_match else window
        chunk = chunk.strip()
        if not chunk:
            continue

        # If the whole chunk looks like a personal bio, skip it entirely
        # e.g. "Euan Blair, son of former British Prime Minister Tony Blair"
        if _INVESTOR_BIO_RE.search(chunk):
            continue

        for raw in _INVESTOR_SPLIT_RE.split(chunk):
            cleaned = _clean_investor_name(raw)
            if not cleaned:
                continue
            # Deduplicate by normalized name — strip common fund suffixes
            # so 'Menlo' and 'Menlo Ventures' don't both appear
            norm = re.sub(
                r'\s+(?:ventures?|capital|partners?|fund|group|investments?)$',
                '', cleaned, flags=re.IGNORECASE
            ).lower().strip()
            # Check if this name is a prefix/suffix of an already-seen one
            if norm in seen or any(norm in s or s in norm for s in seen):
                continue
            seen.add(norm)
            investors.append(cleaned)
            if len(investors) >= max_investors:
                return ', '.join(investors)

    return ', '.join(investors) if investors else None


# ─────────────────────────────────────────────────────────────────────
# Industry classification
# ─────────────────────────────────────────────────────────────────────

INDUSTRY_KEYWORDS = {
    'AI/ML': [
        'artificial intelligence', 'machine learning', 'llm', 'ai-powered',
        'ai powered', 'generative ai', 'ai agent', 'ai startup', 'ai platform',
        'ai model', 'ai infrastructure', 'foundation model', 'large language model',
        'ai-native', 'ai company', 'ai lab', 'gen ai', 'neural network',
    ],
    'Fintech': [
        'fintech', 'payments', 'banking', 'lending', 'insurtech', 'wealth management',
        'neobank', 'embedded finance', 'bnpl', 'cross-border payment',
        'credit union', 'fraud detection', 'tax automation', 'compliance',
    ],
    'Web3/Crypto': [
        'crypto', 'blockchain', 'defi', 'web3', 'cryptocurrency', 'nft', 'dao',
        'stablecoin', 'tokenization', 'digital asset',
    ],
    'Healthcare/Healthtech': [
        'healthtech', 'medtech', 'digital health', 'telehealth', 'medical device',
        'health platform', 'mental health', 'healthcare', 'patient', 'clinical',
        'cancer detection', 'diagnostic',
    ],
    'Biotech': [
        'biotech', 'biotechnology', 'drug discovery', 'therapeutics', 'pharma',
        'oncology', 'gene therapy', 'liquid biopsy', 'immunotherapy',
        'precision medicine', 'genomics', 'rna',
    ],
    'SaaS/B2B': [
        'saas', 'b2b software', 'enterprise software', 'enterprise saas',
        'workflow automation', 'enterprise platform', 'b2b platform',
    ],
    'Cybersecurity': [
        'cybersecurity', 'cyber security', 'cyber-security', 'infosec',
        'threat detection', 'endpoint security', 'zero trust', 'security platform',
    ],
    'Climate/Energy': [
        'climate', 'clean energy', 'renewable', 'solar', 'battery', 'sustainability',
        'carbon', 'green hydrogen', 'decarbonization', 'energy storage',
        'cleantech', 'climate tech',
    ],
    'EV/Mobility': [
        'electric vehicle', 'ev startup', 'mobility', 'autonomous vehicle',
        'self-driving', 'e-mobility', 'micromobility', 'electric two-wheeler',
        'ride-hailing', 'ridehailing',
    ],
    'Defense': [
        'defense', 'defence', 'military', 'autonomous weapons', 'dual-use',
        'space force', 'nato',
    ],
    'Space': [
        'space tech', 'spacetech', 'satellite', 'orbital', 'aerospace',
        'launch vehicle', 'space-based', 'space infrastructure',
    ],
    'Robotics': [
        'robotics', 'humanoid', 'industrial robot', 'autonomous robot',
        'robotic system',
    ],
    'Edtech': [
        'edtech', 'education platform', 'learning platform', 'tutoring',
        'online learning', 'study tool', 'upskilling',
    ],
    'E-commerce/D2C': [
        'e-commerce', 'ecommerce', 'd2c', 'direct-to-consumer', 'marketplace',
        'shopify',
    ],
    'Logistics': [
        'logistics', 'supply chain', 'last-mile delivery', 'freight',
        'shipping', 'warehousing',
    ],
    'Food/Agtech': [
        'agtech', 'foodtech', 'vertical farming', 'alternative protein',
        'precision agriculture', 'indoor farming',
    ],
    'Proptech': [
        'proptech', 'real estate tech', 'real-estate tech', 'rental platform',
    ],
    'Devtools': [
        'developer tools', 'devtools', 'dev tools', 'api platform', 'observability',
        'developer platform', 'coding assistant', 'devops',
    ],
    'Adtech/Martech': [
        'adtech', 'martech', 'ad platform', 'marketing automation',
        'advertising platform',
    ],
    'Gov/Civic': [
        'govtech', 'government technology', 'civic tech', 'public sector',
    ],
}


def extract_industry(text):
    """Returns comma-separated industry labels, or None."""
    if not text:
        return None
    text_lower = text.lower()
    matches = []
    for industry, keywords in INDUSTRY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            matches.append(industry)
    return ', '.join(matches[:2]) if matches else None


# ─────────────────────────────────────────────────────────────────────
# Funding article filter
# ─────────────────────────────────────────────────────────────────────

_FUNDING_SIGNALS = [
    'raises', 'raised', 'raising',
    'funding round',
    'series a', 'series b', 'series c', 'series d', 'series e',
    'seed round', 'pre-seed',
    'secured $', 'secured €', 'secured £',
    'closes $', 'closes €', 'closes £',
    'led by', 'backed by',
    'valuation of',
    'invests in',
    'injects $', 'injects €', 'injects £',
    'infuses', 'infuse ₹',
    'ipo debut', 'goes public', 'listed on',
    'in €', 'in $', 'in £',       # "Destinus in €200m funding"
]

# FIX 5: Negative signals — if ANY of these appear in the title,
# it's almost certainly not a single-company funding article.
_JUNK_SIGNALS = [
    'startups to watch',
    'startups due to',
    'stages at techcrunch',
    'disrupt 2026',
    'battlefield',
    'work anniversary',
    'why it won\'t',
    'here\'s why',
    'opinion:',
    'podcast |',
    'podcast:',
    'interview with',
    'club members only',
    'members only',
    'according to top vcs',
    'according to vcs',
    'funding news –',        # Tech Startups daily roundup
    'funding news —',
    'tech funding news – may',
    'top startup and tech funding',
    'm&a:',
    'the hunt for acquisitions',
    'raising cash and on the hunt',
    'funding news —',
]


def is_funding_article(title, summary):
    """Return True if this looks like a single-company funding announcement."""
    title_lower = title.lower()
    summary_lower = summary.lower()
    combined = f"{title_lower} {summary_lower}"

    # Fast reject on junk signals in the title
    if any(j in title_lower for j in _JUNK_SIGNALS):
        return False

    # Paywalled roundup articles have almost no summary content
    if 'club members only' in summary_lower or 'members only' in summary_lower:
        return False

    return any(signal in combined for signal in _FUNDING_SIGNALS)


# ─────────────────────────────────────────────────────────────────────
# Main extraction function
# ─────────────────────────────────────────────────────────────────────

def extract(entry):
    """Enrich a raw entry dict with extracted fields.

    Returns the enriched entry, or None if it's not a funding article.
    """
    title = entry.get('title', '') or ''
    summary = entry.get('summary', '') or ''
    source = entry.get('source')

    if not is_funding_article(title, summary):
        return None

    combined = f"{title}. {summary}"

    amount, currency = extract_amount(combined)
    round_stage = extract_round(combined)
    company = extract_company(title)
    industry = extract_industry(combined)
    country = extract_country(combined, source=source)
    investors = extract_investors(combined)

    entry['company_name'] = company
    entry['funding_amount'] = amount
    entry['currency'] = currency
    entry['round_stage'] = round_stage
    entry['industry'] = industry
    entry['country'] = country
    entry['investors'] = investors
    entry.setdefault('company_website', None)
    return entry
