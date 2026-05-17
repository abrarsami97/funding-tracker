from .base import BaseFetcher


class Inc42Fetcher(BaseFetcher):
    """Indian startup ecosystem coverage. Funding-focused tag feed."""
    name = "Inc42"
    feed_url = "https://inc42.com/buzz/feed/"
