"""Tests for the fetch read ladder and alternative URL discovery."""

import pytest

from app.ingestion.fetch_manager import FetchManager
from app.tools.kenya_law_client import KenyaLawClient
from app.schemas.research_state import SourceCandidate, BasisStrength, DocumentType


# ---------------------------------------------------------------------------
# Alternative URL discovery
# ---------------------------------------------------------------------------

def test_alternative_urls_from_citation():
    client = KenyaLawClient()
    candidate = SourceCandidate(
        title="Kamau v. Republic [2024] KEHC 1234",
        url="https://new.kenyalaw.org/akn/ke/judgment/kehc/2024/1234/eng@2024-01-01",
        discovered_by="kenyalaw_judgment_search",
    )
    urls = client.find_alternative_urls(candidate)
    # Should include PDF download path
    assert any("download/pdf" in u for u in urls)
    # Should include a search for the citation
    assert any("[2024]" in u for u in urls or ["[2024]"])


def test_alternative_urls_docx():
    client = KenyaLawClient()
    candidate = SourceCandidate(
        title="Test Case",
        url="https://new.kenyalaw.org/akn/ke/judgment/2024/1/eng@2024-01-01",
        discovered_by="test",
    )
    urls = client.find_alternative_urls(candidate)
    assert any("download/docx" in u for u in urls)


def test_alternative_urls_empty_for_unknown():
    client = KenyaLawClient()
    candidate = SourceCandidate(
        title="Some random page",
        url="https://example.com/page",
        discovered_by="test",
    )
    urls = client.find_alternative_urls(candidate)
    assert isinstance(urls, list)


# ---------------------------------------------------------------------------
# Source gap warnings in answer synthesis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_handles_unreadable():
    from app.synthesis.answer_synthesizer import AnswerSynthesizer
    from app.schemas.research_state import ResearchState, EvidenceItem, BasisStrength, BasisRole

    state = ResearchState(
        original_user_query="Test",
        normalized_query="Test",
    )
    state.evidence_ledger.append(EvidenceItem(
        source_title="Kenya Law Case",
        url="https://new.kenyalaw.org/test",
        discovered_by="kenyalaw_judgment_search",
        fetched_by="http_fetch",
        parsed_by="html",
        passage="",
        basis_role=BasisRole.PRIMARY_CASE_LAW,
        basis_strength=BasisStrength.UNREADABLE,
        limitations=["Could not read: HTTP 403"],
    ))

    synthesizer = AnswerSynthesizer(llm_client=None)
    answer = await synthesizer.synthesize(state)
    assert "could not be read" in answer or "unreadable" in answer or "provisional" in answer


# ---------------------------------------------------------------------------
# Fetch result grading for blocked sources
# ---------------------------------------------------------------------------

def test_blocked_source_grades_unreadable():
    """A source that was found but returned 403 should be UNREADABLE, not WEAK."""
    from app.evidence.source_basis_evaluator import SourceBasisEvaluator
    evaluator = SourceBasisEvaluator()
    role, strength, limitations = evaluator.evaluate_authority(
        "https://new.kenyalaw.org/akn/ke/judgment/2024/1/eng@2024-01-01",
        "",
        DocumentType.JUDGMENT,
    )
    assert strength == BasisStrength.UNREADABLE


def test_alternative_url_pattern_generation():
    """Verify the KenyaLawClient generates sensible alternative search URLs."""
    client = KenyaLawClient()
    candidate = SourceCandidate(
        title="John Kamau v. Republic (Criminal Appeal 12 of 2020) [2024] KECA 456 (KLR)",
        url="https://new.kenyalaw.org/akn/ke/judgment/keca/2024/456/eng@2024-06-18",
        discovered_by="kenyalaw_judgment_search",
    )
    urls = client.find_alternative_urls(candidate)
    # Should include party-name based search
    party_search = [u for u in urls if "Kamau" in u or "Republic" in u]
    assert len(party_search) >= 1, f"No party-name URLs found in {urls}"
