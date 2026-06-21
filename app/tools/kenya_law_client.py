"""Native Kenya Law navigation client.

Searches the new Kenya Law website (new.kenyalaw.org) for judgments
and legislation, parses result listings, fetches document pages, and
extracts structured metadata.

Falls back to Brave site-search only when the native search returns
no candidates or the site is unreachable.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from app.schemas.research_state import (
    DocumentType,
    SourceCandidate,
)


# Kenya Law URLs
KENYA_LAW_BASE = "https://new.kenyalaw.org"
JUDGMENT_SEARCH_URL = f"{KENYA_LAW_BASE}/judgments"
LEGISLATION_URL = f"{KENYA_LAW_BASE}/legislation"

# Request headers that mimic a standard browser
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9,sw;q=0.8",
}

# ---------------------------------------------------------------------------
# Metadata extraction helpers
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_CITATION_RE = re.compile(
    r"(?i)(?:\[?\d{4}\]?\s*(?:KEHC|KECA|KESCR|KLR|eKLR|ELRC|KESC|KACC)\s*\d+)"
)
_SECTION_RE = re.compile(r"(?i)(?:section|s\.|sec\.|art\.|article)\s+(\d+[A-Za-z]?)")
_STATUTE_RE = re.compile(
    r"(?i)(?:the\s+)?([A-Z][A-Za-z\s]+(?:act|code|rules|regulations))\b"
)


def _extract_year(text: str) -> str | None:
    match = _YEAR_RE.search(text)
    return match.group(0) if match else None


def _extract_citations(text: str) -> list[str]:
    return _CITATION_RE.findall(text)


def _extract_sections(text: str) -> list[str]:
    matches = _SECTION_RE.findall(text)
    return list(set(matches))


def _extract_statutes(text: str) -> list[str]:
    matches = _STATUTE_RE.findall(text)
    return list(set(matches))


def _extract_document_type_from_url(url: str) -> DocumentType:
    lower = url.lower()
    if "/judgment" in lower or "/judgements" in lower:
        return DocumentType.JUDGMENT
    if "/akn/ke/act" in lower or "/act/" in lower:
        return DocumentType.STATUTE
    if "/gazette" in lower:
        return DocumentType.GAZETTE
    return DocumentType.UNKNOWN


# ---------------------------------------------------------------------------
# Candidate ranking
# ---------------------------------------------------------------------------

def _rank_candidates(
    candidates: list[SourceCandidate],
    query: str,
) -> list[SourceCandidate]:
    """Rank candidates by relevance to the query."""
    q_lower = query.lower()
    q_tokens = set(q_lower.split())

    for c in candidates:
        score = 0.0
        title_lower = c.title.lower()
        snippet_lower = c.snippet.lower()

        # Exact title match
        if title_lower == q_lower:
            score += 10.0
        # All query tokens found in title
        if q_tokens and q_tokens.issubset(set(title_lower.split())):
            score += 5.0
        # Partial token overlap in title
        overlap = len(q_tokens & set(title_lower.split()))
        score += overlap * 1.5

        # Citation match in snippet
        citations = _extract_citations(c.snippet)
        if citations:
            score += 3.0 * len(citations)

        # Year match
        year = _extract_year(c.snippet or c.title)
        if year:
            score += 1.0

        # Statute name match
        statutes = _extract_statutes(c.snippet or c.title)
        if statutes:
            score += 2.0 * len(statutes)

        # Section match
        sections = _extract_sections(c.snippet or c.title)
        if sections:
            score += 2.0 * len(sections)

        # Court domain bonus
        url_lower = c.url.lower()
        if "kenyalaw.org" in url_lower:
            score += 3.0
        if "judiciary.go.ke" in url_lower:
            score += 2.0
        if any(d in url_lower for d in ["parliament.go.ke", "klrc.go.ke"]):
            score += 1.5

        c.confidence = min(score / 10.0, 1.0)

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Document metadata extraction (from fetched content)
# ---------------------------------------------------------------------------

class KenyaLawMetadata(BaseModel):
    title: str = ""
    court: str = ""
    date: str | None = None
    neutral_citation: str | None = None
    statute_name: str | None = None
    section: str | None = None
    document_type: DocumentType = DocumentType.UNKNOWN
    downloadable_pdf: str | None = None
    downloadable_docx: str | None = None


def _parse_judgment_metadata(soup: BeautifulSoup, url: str) -> KenyaLawMetadata:
    meta = KenyaLawMetadata(document_type=DocumentType.JUDGMENT)

    # Title from h1
    h1 = soup.find("h1")
    if h1:
        meta.title = h1.get_text(strip=True)

    # Try common judgment metadata patterns
    for prop in ("head-matter", "judgment-header", "document-meta"):
        div = soup.find("div", class_=re.compile(prop, re.I))
        if div:
            text = div.get_text(" ", strip=True)
            meta.court = _extract_court(text) or meta.court
            meta.date = _extract_date(text) or meta.date
            meta.neutral_citation = meta.neutral_citation or _extract_first_citation(text)

    # Fallback: extract from the page text
    page_text = soup.get_text(" ", strip=True)[:3000]
    if not meta.court:
        meta.court = _extract_court(page_text) or ""
    if not meta.date:
        meta.date = _extract_date(page_text)
    if not meta.neutral_citation:
        citations = _extract_citations(page_text)
        if citations:
            meta.neutral_citation = citations[0]

    # Downloadable files
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.endswith(".pdf"):
            meta.downloadable_pdf = href if href.startswith("http") else f"{KENYA_LAW_BASE}{href}"
        if href.endswith(".docx"):
            meta.downloadable_docx = href if href.startswith("http") else f"{KENYA_LAW_BASE}{href}"

    return meta


def _parse_legislation_metadata(soup: BeautifulSoup, url: str) -> KenyaLawMetadata:
    meta = KenyaLawMetadata(document_type=DocumentType.STATUTE)

    h1 = soup.find("h1")
    if h1:
        meta.title = h1.get_text(strip=True)

    # AKN metadata block
    for tag in soup.find_all(["div", "pre"], class_=re.compile(r"(akn|metadata)", re.I)):
        text = tag.get_text(" ", strip=True)
        statutes = _extract_statutes(text)
        if statutes:
            meta.statute_name = statutes[0]
        sections = _extract_sections(text)
        if sections:
            meta.section = sections[0]

    page_text = soup.get_text(" ", strip=True)[:2000]
    if not meta.statute_name:
        statutes = _extract_statutes(page_text)
        if statutes:
            meta.statute_name = statutes[0]
    meta.date = _extract_date(page_text)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.endswith(".pdf"):
            meta.downloadable_pdf = href if href.startswith("http") else f"{KENYA_LAW_BASE}{href}"

    return meta


def _extract_court(text: str) -> str:
    court_patterns = [
        (r"supreme\s+court\s+of\s+kenya", "Supreme Court of Kenya"),
        (r"court\s+of\s+appeal\s+of\s+kenya", "Court of Appeal of Kenya"),
        (r"high\s+court\s+of\s+kenya", "High Court of Kenya"),
        (r"employment\s+and\s+labour\s+relations\s+court", "Employment and Labour Relations Court"),
        (r"environment\s+and\s+land\s+court", "Environment and Land Court"),
        (r"kadhis?\s+court", "Kadhi's Court"),
        (r"industrial\s+court", "Industrial Court"),
        (r"constitutional\s+court", "Constitutional Court"),
    ]
    for pattern, name in court_patterns:
        if re.search(pattern, text, re.I):
            return name
    return ""


def _extract_date(text: str) -> str | None:
    # Match "12 January 2023" or "12th January 2023"
    match = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(\d{4})\b",
        text, re.I,
    )
    if match:
        return f"{match.group(1)} {match.group(2)} {match.group(3)}"

    # Match ISO date 2023-01-12
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if match:
        return match.group(0)

    return None


def _extract_first_citation(text: str) -> str | None:
    citations = _extract_citations(text)
    return citations[0] if citations else None


def _extract_first_statute(text: str) -> str | None:
    statutes = _extract_statutes(text)
    return statutes[0] if statutes else None


# ---------------------------------------------------------------------------
# HTML / XML parsers for search results
# ---------------------------------------------------------------------------

def _parse_judgment_search_results(html: str) -> list[dict[str, Any]]:
    """Parse judgment listing HTML from new.kenyalaw.org/judgments."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []

    # Try multiple common listing patterns
    article_selectors = [
        "article", "div.result", "li.judgment", "tr", "div.card",
        "div[class*=judgment]", "div[class*=result]", "li[class*=judgment]",
    ]

    articles = []
    for sel in article_selectors:
        articles = soup.select(sel)
        if len(articles) >= 2:
            break

    if not articles or len(articles) < 2:
        # Fallback: look for any link containing judgment indicators
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if not text or len(text) < 10:
                continue
            if "/judgment" in href.lower() or "v." in text.lower():
                download_pdf, download_docx = _find_downloads_in_container(a)
                results.append({
                    "title": text,
                    "url": href if href.startswith("http") else f"{KENYA_LAW_BASE}{href}",
                    "snippet": _get_parent_text(a),
                    "download_pdf": download_pdf,
                    "download_docx": download_docx,
                })

    for art in articles:
        link = art.find("a", href=True)
        if not link:
            continue
        title = link.get_text(strip=True)
        href = link["href"]
        if not title or len(title) < 5:
            continue
        url = href if href.startswith("http") else f"{KENYA_LAW_BASE}{href}"
        snippet = _get_parent_text(art)
        snippet = snippet.replace(title, "", 1).strip()[:300]
        download_pdf, download_docx = _find_downloads_in_container(art)
        date = _extract_date(snippet) or _extract_date(title)
        results.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "download_pdf": download_pdf,
            "download_docx": download_docx,
            "date": date,
        })

    return results


def _find_downloads_in_container(container) -> tuple[str | None, str | None]:
    """Look for PDF/DOCX download links inside a search result container."""
    download_pdf = None
    download_docx = None
    for a in container.find_all("a", href=True) if hasattr(container, "find_all") else []:
        href = a["href"]
        if href.endswith(".pdf"):
            download_pdf = href if href.startswith("http") else f"{KENYA_LAW_BASE}{href}"
        elif href.endswith(".docx"):
            download_docx = href if href.startswith("http") else f"{KENYA_LAW_BASE}{href}"
    return download_pdf, download_docx


def _parse_legislation_search_results(html: str) -> list[dict[str, Any]]:
    """Parse legislation listing HTML from new.kenyalaw.org/legislation."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []

    # Primary pattern: card elements with AKN links
    for card in soup.find_all(class_=re.compile(r"card", re.I)):
        link = card.find("a", href=True)
        if not link:
            continue
        href = link["href"]
        title = link.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        # Filter to legal content
        if not any(t in str(href) for t in ["/akn/ke/", "/legislation/"]) and not any(
            t in title.lower() for t in ["act", "constitution", "cap.", "code", "rules", "order"]
        ):
            continue
        url = href if str(href).startswith("http") else f"{KENYA_LAW_BASE}{href}"
        snippet = card.get_text(" ", strip=True).replace(title, "", 1).strip()[:300]
        results.append({"title": title, "url": url, "snippet": snippet})

    if results:
        return results

    # Fallback: any link with /akn/ke/act in the href
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/akn/ke/act" not in str(href):
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        url = href if str(href).startswith("http") else f"{KENYA_LAW_BASE}{href}"
        results.append({"title": title, "url": url, "snippet": title[:300]})

    return results


def _get_parent_text(tag) -> str:
    """Get text from parent container, excluding the tag's own direct text if redundant."""
    texts = []
    for child in tag.children:
        if hasattr(child, "get_text"):
            texts.append(child.get_text(strip=True))
        elif child:
            texts.append(str(child).strip())
    return " ".join(t for t in texts if t)


# ---------------------------------------------------------------------------
# Main client class
# ---------------------------------------------------------------------------

class KenyaLawClient:
    """Native Kenya Law navigation client.

    Searches new.kenyalaw.org for judgments and legislation, fetches
    document pages, and extracts structured metadata.  Uses BeautifulSoup
    for HTML parsing and httpx for async HTTP.
    """

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    # ---- Public search API ----

    async def search_judgments(self, query: str) -> list[SourceCandidate]:
        """Search new.kenyalaw.org for judgments matching the query."""
        try:
            html = await self._fetch_page(JUDGMENT_SEARCH_URL, query)
            raw = _parse_judgment_search_results(html)
        except Exception:
            return []

        candidates = [
            SourceCandidate(
                title=r.get("title", "Untitled Judgment"),
                url=r.get("url", ""),
                snippet=self._format_snippet(r),
                discovered_by="kenyalaw_judgment_search",
                document_type_hint=DocumentType.JUDGMENT,
                jurisdiction_hint="kenya",
                confidence=0.6,
            )
            for r in raw
            if r.get("url")
        ]

        return _rank_candidates(candidates, query)

    async def search_legislation(self, query: str) -> list[SourceCandidate]:
        """Search new.kenyalaw.org for legislation matching the query."""
        try:
            html = await self._fetch_page(LEGISLATION_URL, query)
            raw = _parse_legislation_search_results(html)
        except Exception:
            return []

        candidates = [
            SourceCandidate(
                title=r.get("title", "Untitled Statute"),
                url=r.get("url", ""),
                snippet=self._format_snippet(r),
                discovered_by="kenyalaw_legislation_search",
                document_type_hint=DocumentType.STATUTE,
                jurisdiction_hint="kenya",
                confidence=0.6,
            )
            for r in raw
            if r.get("url")
        ]

        return _rank_candidates(candidates, query)

    # ---- Alternative URL finding for readability recovery ----

    def find_alternative_urls(self, candidate: SourceCandidate) -> list[str]:
        """Generate alternative source URLs when the primary document page is blocked.

        Returns a prioritized list of URLs to attempt in the read ladder.
        """
        urls: list[str] = []
        title = candidate.title
        url = candidate.url

        # 1. PDF version of the same document (common Kenya Law pattern)
        if url.endswith("/"):
            urls.append(f"{url.rstrip('/')}/download/pdf")
        if "/akn/" in url:
            # AKN documents often have /download/pdf and /download/docx paths
            urls.append(f"{url.rstrip('/')}/download/pdf")
            urls.append(f"{url.rstrip('/')}/download/docx")

        # 2. Old Kenya Law (eKLR) mirror — derive from case title or neutral citation
        citation = _extract_first_citation(title)
        if citation:
            term = citation.replace(" ", "+")
            urls.append(f"https://kenyalaw.org/caselaw/cases/view/{term}")

        # 3. Extract party names from "Party A v Party B" pattern
        parties_match = re.match(r"^([A-Z][A-Za-z\s'.-]+?)\s+v[.\s]\s*([A-Z][A-Za-z\s'.-]+)", title)
        if parties_match:
            p1 = parties_match.group(1).strip().replace(" ", "+")
            p2 = parties_match.group(2).strip().replace(" ", "+")
            urls.append(
                f"https://new.kenyalaw.org/search?q={p1}+v+{p2}"
            )

        # 4. Statute name — try old Kenya Law legislation path
        statute = _extract_first_statute(title)
        if statute:
            term = statute.replace(" ", "+")
            urls.append(f"https://kenyalaw.org/lex/act.xhtml?actName={term}")

        return urls

    def _format_snippet(self, raw_result: dict) -> str:
        """Format a search result's extra metadata into a useful snippet."""
        parts = []
        if raw_result.get("snippet"):
            parts.append(raw_result["snippet"])
        if raw_result.get("download_pdf"):
            parts.append(f"[PDF: {raw_result['download_pdf']}]")
        if raw_result.get("download_docx"):
            parts.append(f"[DOCX: {raw_result['download_docx']}]")
        if raw_result.get("date"):
            parts.append(f"[Date: {raw_result['date']}]")
        return " | ".join(parts)

    # ---- Document fetch API ----

    async def fetch_document(self, url: str) -> KenyaLawMetadata:
        """Fetch a Kenya Law document page and extract metadata + text."""
        doc_type = _extract_document_type_from_url(url)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
            ) as client:
                response = await client.get(url, headers=_HEADERS)
                response.raise_for_status()
                html = response.text
        except Exception:
            return KenyaLawMetadata(document_type=doc_type)

        soup = BeautifulSoup(html, "html.parser")

        if doc_type == DocumentType.JUDGMENT:
            return _parse_judgment_metadata(soup, url)
        elif doc_type in (DocumentType.STATUTE,):
            return _parse_legislation_metadata(soup, url)
        else:
            # Generic fallback
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else url
            return KenyaLawMetadata(title=title, document_type=doc_type)

    # ---- Internal helpers ----

    async def _fetch_page(self, base_url: str, query: str) -> str:
        """Fetch a search results page from the Kenya Law site."""
        params: dict[str, Any] = {}
        if query:
            params["search"] = query
        elif "judgment" in base_url:
            params["order"] = "date"

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=True,
        ) as client:
            response = await client.get(base_url, params=params, headers=_HEADERS)
            response.raise_for_status()
            return response.text

    async def fetch_akn_xml(self, url: str) -> str:
        """Fetch AKN XML document and return the raw XML text."""
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
            ) as client:
                response = await client.get(url, headers={
                    **_HEADERS,
                    "Accept": "application/xml,text/xml,*/*",
                })
                response.raise_for_status()
                return response.text
        except Exception:
            return ""
