from .base import BaseFetcher


class SiftedFetcher(BaseFetcher):
    """Europe-focused startup news. Strong on funding rounds."""
    name = "Sifted"
    feed_url = "https://sifted.eu/feed"
