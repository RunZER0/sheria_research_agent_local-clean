from __future__ import annotations

from typing import List, Optional

from .config import Settings
from .brave_search import BraveSearchClient
from .browser_fetch import BrowserFetcher
from .source_quality import evaluate_source, SourceQuality
from .schemas import Source


class ToolRouter:
    """Simple router that exposes tool intents as async methods.

    This is intentionally lightweight: it wraps existing clients and provides
    a single seam to add more tools later.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        # Do not swallow initialization errors silently.
        try:
            self.brave = BraveSearchClient(settings)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize BraveSearchClient. Check BRAVE_API_KEY. Error: {e}")

        self.browser = BrowserFetcher(settings)

    async def search(self, query: str) -> List[Source]:
        if not self.brave:
            raise RuntimeError("BraveSearchClient is not available. Search cannot proceed.")
        # Allow the actual search exception to bubble up to the agent's error handler
        return await self.brave.search(query)

    async def fetch_text(self, url: str, prefer_browser: bool = True) -> str:
        # Prefer browser fetch for JS-heavy pages; fall back to httpx inside BrowserFetcher
        try:
            if prefer_browser:
                # BrowserFetcher.fetch_text already falls back to httpx
                return await self.browser.fetch_text(url)
            else:
                # call httpx path by calling the internal helper
                return await self.browser._fetch_with_httpx(url)
        except Exception:
            # Best-effort fallback
            try:
                return await self.browser._fetch_with_httpx(url)
            except Exception:
                return ""

    def classify(self, url: str, title: str = "", snippet: str = "", kenya_legal_mode: bool = True) -> SourceQuality:
        return evaluate_source(url, title=title, snippet=snippet, kenya_legal_mode=kenya_legal_mode)

    # Placeholder for PDF extraction tool - not implemented here
    async def pdf_text_extract(self, url: str) -> str:
        """Downloads a legal PDF directly into memory and extracts raw text string."""
        import io
        import httpx
        try:
            # Import pypdf dynamically to keep dependencies lightweight
            import pypdf
        except ImportError:
            return ""

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    return ""
                
                # Load raw bytes into an in-memory stream reader
                pdf_file = io.BytesIO(response.content)
                reader = pypdf.PdfReader(pdf_file)
                
                text_content = []
                # Extract up to the first 15 pages to stay within token limits
                for page in reader.pages[:15]:
                    page_text = page.extract_text()
                    if page_text:
                        text_content.append(page_text)
                        
                return "\n".join(text_content)
        except Exception:
            return "" # Graceful fallback to next tool in the recovery playbook
