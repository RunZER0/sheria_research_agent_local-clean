from app.schemas.research_state import (
    ResearchState,
    EvidenceItem,
    JurisdictionTarget,
    QueryType,
    OutputFormat,
    BasisRole,
    BasisStrength,
    DocumentType,
    CoverageReport,
)


def test_research_state_defaults():
    state = ResearchState(
        original_user_query="Test query",
        normalized_query="Test query",
    )
    assert state.run_id is not None
    assert state.jurisdiction_target == JurisdictionTarget.UNKNOWN
    assert state.query_type == QueryType.UNKNOWN
    assert state.requested_outputs == [OutputFormat.CHAT]
    assert state.search_round == 0
    assert state.max_rounds == 5
    assert len(state.evidence_ledger) == 0
    assert state.final_answer_draft is None


def test_has_strong_or_moderate_basis():
    state = ResearchState(
        original_user_query="Test",
        normalized_query="Test",
    )
    assert state.has_strong_or_moderate_basis() is False

    state.evidence_ledger.append(
        EvidenceItem(
            source_title="Test",
            url="https://example.com",
            discovered_by="test",
            fetched_by="test",
            parsed_by="test",
            passage="Test",
            basis_role=BasisRole.PRIMARY_LEGISLATION,
            basis_strength=BasisStrength.STRONG,
        )
    )
    assert state.has_strong_or_moderate_basis() is True


def test_has_any_readable_evidence():
    state = ResearchState(
        original_user_query="Test",
        normalized_query="Test",
    )
    assert state.has_any_readable_evidence() is False

    state.evidence_ledger.append(
        EvidenceItem(
            source_title="Test",
            url="https://example.com",
            discovered_by="test",
            fetched_by="test",
            parsed_by="test",
            passage="Readable legal content here",
        )
    )
    assert state.has_any_readable_evidence() is True


def test_coverage_report_defaults():
    report = CoverageReport()
    assert report.successful_fetches == 0
    assert report.failed_fetches == 0
    assert report.unreadable_sources == 0
    assert report.unresolved_gaps == []
    assert report.fallback_reasons == []
    assert report.current_basis_summary == "No evidence gathered yet."


def test_output_format_enum():
    assert OutputFormat.CHAT.value == "chat"
    assert OutputFormat.DOCX.value == "docx"
    assert OutputFormat.PDF.value == "pdf"


def test_jurisdiction_target_enum():
    assert JurisdictionTarget.KENYA.value == "kenya"
    assert JurisdictionTarget.FOREIGN.value == "foreign"
    assert JurisdictionTarget.COMPARATIVE.value == "comparative"
