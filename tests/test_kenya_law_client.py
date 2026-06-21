"""Tests for the native Kenya Law navigation client."""

from app.tools.kenya_law_client import (
    _extract_citations,
    _extract_year,
    _extract_sections,
    _extract_statutes,
    _extract_court,
    _extract_date,
    _rank_candidates,
    _parse_judgment_search_results,
    _parse_legislation_search_results,
)
from app.schemas.research_state import SourceCandidate, DocumentType


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

def test_extract_citations():
    result = _extract_citations("See [2024] KEHC 1234 and [2023] KECA 456")
    assert "[2024] KEHC 1234" in result
    assert "[2023] KECA 456" in result


def test_extract_year():
    assert _extract_year("Judgment of 2024") == "2024"
    assert _extract_year("No year here") is None


def test_extract_sections():
    result = _extract_sections("Under section 45 and article 10")
    assert "45" in result
    assert "10" in result


def test_extract_statutes():
    result = _extract_statutes("The Employment Act and the Constitution of Kenya")
    assert "Employment Act" in result or "employment act" in result.lower()


def test_extract_court():
    text = "IN THE SUPREME COURT OF KENYA at Nairobi"
    assert "Supreme Court of Kenya" in _extract_court(text)


def test_extract_date():
    result = _extract_date("Delivered on 12 March 2024")
    assert result == "12 March 2024"
    result2 = _extract_date("Filed 2023-01-15")
    assert result2 == "2023-01-15"


# ---------------------------------------------------------------------------
# Candidate ranking
# ---------------------------------------------------------------------------

def test_ranking_exact_title_match():
    candidates = [
        SourceCandidate(title="Wrong Case", url="https://example.com/1", snippet="", discovered_by="test"),
        SourceCandidate(title="Promissory Estoppel", url="https://example.com/2", snippet="", discovered_by="test"),
    ]
    ranked = _rank_candidates(candidates, "Promissory Estoppel")
    assert ranked[0].title == "Promissory Estoppel"
    assert ranked[0].confidence >= ranked[1].confidence


def test_ranking_citation_boost():
    candidates = [
        SourceCandidate(title="Case A", url="https://example.com/1", snippet="General text", discovered_by="test"),
        SourceCandidate(title="Case B", url="https://example.com/2", snippet="See [2024] KEHC 1234", discovered_by="test"),
    ]
    ranked = _rank_candidates(candidates, "Some query")
    assert ranked[0].title == "Case B"  # citation boost


def test_ranking_kenyalaw_domain_boost():
    candidates = [
        SourceCandidate(title="External", url="https://random.blog/post", snippet="", discovered_by="test"),
        SourceCandidate(title="Kenya Law", url="https://new.kenyalaw.org/judgments/123", snippet="", discovered_by="test"),
    ]
    ranked = _rank_candidates(candidates, "test")
    assert ranked[0].title == "Kenya Law"


# ---------------------------------------------------------------------------
# Search result parsers
# ---------------------------------------------------------------------------

def test_parse_judgment_search_results_empty():
    results = _parse_judgment_search_results("<html><body>No results</body></html>")
    assert isinstance(results, list)


def test_parse_judgment_search_results_simple():
    html = """
    <html>
      <body>
        <article>
          <a href="/judgments/123">John K. Kamau v. Republic [2024] KEHC 456</a>
          <span>Employment law appeal</span>
        </article>
      </body>
    </html>
    """
    results = _parse_judgment_search_results(html)
    assert len(results) >= 1
    if results:
        assert "Kamau" in results[0]["title"] or "Kamau" in results[0]["snippet"]


def test_parse_legislation_search_results_simple():
    html = """
    <html>
      <body>
        <div class="card">
          <a href="/akn/ke/act/2007/employment">Employment Act, 2007</a>
          <span>Laws of Kenya</span>
        </div>
        <div class="card">
          <a href="/akn/ke/act/2010/constitution">Constitution of Kenya, 2010</a>
          <span>Supreme law</span>
        </div>
      </body>
    </html>
    """
    results = _parse_legislation_search_results(html)
    assert len(results) == 2
    assert "Employment Act" in results[0]["title"]
    assert "Constitution" in results[1]["title"]


def test_parse_legislation_search_results_no_act_prefix():
    """Legislation links without '/akn/ke/act' but with 'Act' in title should be included."""
    html = """
    <html>
      <body>
        <div class="card">
          <a href="/some/other/path">The Evidence Act, Cap 80</a>
        </div>
      </body>
    </html>
    """
    results = _parse_legislation_search_results(html)
    assert len(results) >= 1


# ---------------------------------------------------------------------------
# Integration via SearchAdapters
# ---------------------------------------------------------------------------

def test_search_adapters_import():
    from app.tools.search_adapters import SearchAdapters
    adapters = SearchAdapters()
    assert adapters is not None
    assert adapters.brave is None
    assert adapters.kenyalaw is None
