from .base import BaseFetcher


class EUStartupsFetcher(BaseFetcher):
    """European startup ecosystem coverage."""
    name = "EU-Startups"
    feed_url = "https://www.eu-startups.com/feed/"
