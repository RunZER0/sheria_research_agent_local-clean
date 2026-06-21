from __future__ import annotations

from urllib.parse import quote_plus

from app.schemas.research_state import (
    DocumentType,
    JurisdictionTarget,
    ReActAction,
    ReActActionType,
    SourceCandidate,
)
from app.discovery import (
    ParsedCaseQuery,
    generate_akn_candidates,
    score_candidate,
    classify_score,
)


OFFICIAL_KENYA_DOMAINS = [
    "new.kenyalaw.org",
    "kenyalaw.org",
    "judiciary.go.ke",
    "parliament.go.ke",
    "klrc.go.ke",
    "ag.go.ke",
    "gazette.go.ke",
]


class SearchAdapters:
    def __init__(self, brave_client=None, kenyalaw_client=None) -> None:
        self.brave = brave_client
        self.kenyalaw = kenyalaw_client

    async def execute_search(self, action: ReActAction) -> list[SourceCandidate]:
        if action.action == ReActActionType.KENYALAW_JUDGMENT_SEARCH:
            return await self._kenyalaw_judgment_search(action.query or "")

        if action.action == ReActActionType.KENYALAW_LEGISLATION_SEARCH:
            return await self._kenyalaw_legislation_search(action.query or "")

        if action.action == ReActActionType.KENYA_LAW_CASE_RESOLVE:
            return await self._kenya_law_case_resolve(action.query or "")

        if action.action == ReActActionType.CASE_SPECIFIC_SEARCH:
            return await self._case_specific_search(action.query or "")

        if action.action == ReActActionType.OFFICIAL_KENYA_DOMAIN_SEARCH:
            return await self._official_kenya_domain_search(action.query or "")

        if action.action == ReActActionType.BRAVE_SEARCH_FALLBACK:
            return await self._brave_search(action.query or "")

        return []

    async def _kenya_law_case_resolve(self, query: str) -> list[SourceCandidate]:
        """Try AKN URL reconstruction from citation metadata, then fetch to verify."""
        parsed = ParsedCaseQuery(query)
        candidates = []

        for akn_url in generate_akn_candidates(parsed):
            candidates.append(SourceCandidate(
                title=f"AKN candidate: {akn_url.split('/')[-1]}",
                url=akn_url,
                snippet=f"Reconstructed from {parsed.query_type()} request: {parsed.neutral_citation or query}",
                discovered_by="kenya_law_case_resolve",
                document_type_hint=DocumentType.JUDGMENT,
                confidence=0.7,
                score=85,
                score_label="akn_candidate",
            ))

        return candidates

    async def _case_specific_search(self, query: str) -> list[SourceCandidate]:
        """Run structured Kenya Law search with party fragments + issue terms, then score."""
        parsed = ParsedCaseQuery(query)
        all_results = []

        # Try native Kenya Law search first
        if self.kenyalaw and hasattr(self.kenyalaw, "search_judgments"):
            try:
                native_results = await self.kenyalaw.search_judgments(query)
                if native_results:
                    all_results.extend(native_results)
            except Exception:
                pass

        # If nothing found, try Brave site search
        if not all_results:
            all_results = await self._brave_search(
                f"site:new.kenyalaw.org {' '.join(parsed.party_fragments[:2])}",
                discovered_by="case_specific_search",
            )

        # Score and label each candidate
        for result in all_results:
            raw_score = score_candidate(result.title, parsed)
            label = classify_score(raw_score)
            result.score = raw_score
            result.score_label = label
            result.confidence = min(0.95, raw_score / 100.0)

        # Sort by score descending
        all_results.sort(key=lambda r: getattr(r, "score", 0), reverse=True)
        return all_results

    async def _kenyalaw_judgment_search(self, query: str) -> list[SourceCandidate]:
        """Native Kenya Law judgment search with Brave site-search fallback."""
        if self.kenyalaw and hasattr(self.kenyalaw, "search_judgments"):
            try:
                native_results = await self.kenyalaw.search_judgments(query)
                if native_results:
                    return native_results
            except Exception:
                pass

        # Step 2: Brave site-search fallback when native produces nothing
        return await self._brave_search(
            f"site:new.kenyalaw.org/judgments {query}",
            discovered_by="kenyalaw_judgment_search",
        )

    async def _kenyalaw_legislation_search(self, query: str) -> list[SourceCandidate]:
        """Native Kenya Law legislation search with Brave site-search fallback."""
        if self.kenyalaw and hasattr(self.kenyalaw, "search_legislation"):
            try:
                native_results = await self.kenyalaw.search_legislation(query)
                if native_results:
                    return native_results
            except Exception:
                pass

        return await self._brave_search(
            f"site:new.kenyalaw.org/legislation {query}",
            discovered_by="kenyalaw_legislation_search",
        )

    async def _official_kenya_domain_search(self, query: str) -> list[SourceCandidate]:
        site_filter = " OR ".join(f"site:{d}" for d in OFFICIAL_KENYA_DOMAINS)
        return await self._brave_search(
            f"({site_filter}) {query}",
            discovered_by="official_kenya_domain_search",
        )

    async def _brave_search(
        self, query: str, discovered_by: str = "brave_search_fallback"
    ) -> list[SourceCandidate]:
        if not self.brave:
            return []

        results = await self.brave.search(query)
        return [self._candidate_from_result(r, discovered_by, DocumentType.UNKNOWN) for r in results]

    def _candidate_from_result(
        self, result, discovered_by: str, doc_type: DocumentType
    ) -> SourceCandidate:
        if isinstance(result, dict):
            title = result.get("title") or result.get("name") or "Untitled source"
            url = result.get("url") or result.get("link") or ""
            snippet = result.get("snippet") or result.get("description") or ""
        else:
            title = getattr(result, "title", "Untitled source")
            url = getattr(result, "url", "")
            snippet = getattr(result, "snippet", "")

        return SourceCandidate(
            title=title,
            url=url,
            snippet=snippet,
            discovered_by=discovered_by,
            document_type_hint=doc_type,
            confidence=0.5,
        )
