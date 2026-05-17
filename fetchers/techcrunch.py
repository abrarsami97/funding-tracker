from .base import BaseFetcher


class TechCrunchFundingFetcher(BaseFetcher):
    """TechCrunch's venture/funding tag feed."""
    name = "TechCrunch"
    feed_url = "https://techcrunch.com/category/venture/feed/"
