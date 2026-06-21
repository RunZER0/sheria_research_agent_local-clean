from urllib.parse import urlparse

import httpx

from .config import Settings
from .schemas import Source


class BraveSearchClient:
    def __init__(self, settings: Settings):
        if not settings.brave_api_key:
            raise RuntimeError("BRAVE_API_KEY is missing. Add it to .env.")
        self.settings = settings

    async def search(self, query: str) -> list[Source]:
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.settings.brave_api_key,
        }
        params = {
            "q": query,
            "count": self.settings.max_search_results_per_query,
            "text_decorations": "false",
            "safesearch": "moderate",
        }

        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        results = []
        web_results = data.get("web", {}).get("results", [])
        for item in web_results:
            url = item.get("url", "")
            title = item.get("title", "") or url
            snippet = item.get("description", "") or ""
            if not url.startswith(("http://", "https://")):
                continue
            results.append(
                Source(
                    id="",
                    title=title,
                    url=url,
                    snippet=snippet,
                    score=_score_url(url),
                )
            )
        return results


def _score_url(url: str) -> float:
    domain = urlparse(url).netloc.lower()
    score = 0.0
    if domain.endswith(".go.ke"):
        score += 2.0
    if "kenyalaw.org" in domain:
        score += 3.0
    if "court.go.ke" in domain or "judiciary.go.ke" in domain:
        score += 2.0
    return score
