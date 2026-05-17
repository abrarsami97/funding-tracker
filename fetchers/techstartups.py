from .base import BaseFetcher


class TechStartupsFetcher(BaseFetcher):
    """Daily funding roundups with sector commentary."""
    name = "Tech Startups"
    feed_url = "https://techstartups.com/feed/"
