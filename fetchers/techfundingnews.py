from .base import BaseFetcher


class TechFundingNewsFetcher(BaseFetcher):
    """Global tech funding, strong EU coverage."""
    name = "Tech Funding News"
    feed_url = "https://techfundingnews.com/feed/"
