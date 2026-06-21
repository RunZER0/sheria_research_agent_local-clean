"""Tests for the LLM orchestrator prompt builder.

Since the LLM is the brain that decides all actions, these tests verify
the prompt builder produces correct context from different state scenarios,
and that the response parser handles valid and invalid JSON correctly.
"""

import json

from app.controllers.react_decision import (
    build_orchestrator_prompt,
    parse_orchestrator_response,
)
from app.schemas.research_state import (
    EvidenceItem,
    BasisRole,
    BasisStrength,
    JurisdictionTarget,
    QueryType,
    ResearchState,
)


def test_prompt_includes_query_and_jurisdiction():
    state = ResearchState(
        original_user_query="Explain section 45 of the Employment Act in Kenya.",
        normalized_query="Explain section 45 of the Employment Act in Kenya.",
        jurisdiction_target=JurisdictionTarget.KENYA,
        query_type=QueryType.STATUTE,
    )
    messages = build_orchestrator_prompt(state)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "Kenya" in messages[1]["content"]
    assert "Employment Act" in messages[1]["content"]


def test_prompt_includes_evidence_when_present():
    state = ResearchState(
        original_user_query="Test query",
        normalized_query="Test query",
        jurisdiction_target=JurisdictionTarget.KENYA,
        query_type=QueryType.STATUTE,
    )
    state.evidence_ledger.append(EvidenceItem(
        source_title="Employment Act Cap 226",
        url="https://example.com",
        discovered_by="test",
        fetched_by="test",
        parsed_by="test",
        passage="Section 45 applies to unfair termination.",
        basis_role=BasisRole.PRIMARY_LEGISLATION,
        basis_strength=BasisStrength.STRONG,
    ))
    messages = build_orchestrator_prompt(state)
    user_msg = messages[1]["content"]

    assert "Employment Act Cap 226" in user_msg
    assert "Section 45 applies" in user_msg
    assert "STRONG" in user_msg or "strong" in user_msg


def test_prompt_includes_attempted_tools():
    state = ResearchState(
        original_user_query="Test",
        normalized_query="Test",
        jurisdiction_target=JurisdictionTarget.GENERAL,
        query_type=QueryType.THEORY,
    )
    state.coverage_report.attempted_tools = ["brave_search_fallback"]
    state.coverage_report.fallback_reasons = ["brave_search_fallback returned no candidates."]

    messages = build_orchestrator_prompt(state)
    user_msg = messages[1]["content"]

    assert "brave_search_fallback" in user_msg
    assert "returned no candidates" in user_msg


def test_parse_valid_json():
    raw = '{"action": "kenyalaw_judgment_search", "parameters": {"query": "unfair termination Kenya"}, "reason": "Let me check Kenya Law for case law on unfair termination."}'
    result = parse_orchestrator_response(raw)

    assert result is not None
    assert result["action"] == "kenyalaw_judgment_search"
    assert result["parameters"]["query"] == "unfair termination Kenya"
    assert result["reason"] is not None


def test_parse_invalid_json_returns_none():
    raw = "not json at all"
    result = parse_orchestrator_response(raw)
    assert result is None


def test_parse_missing_action_returns_none():
    raw = '{"reason": "I have no idea what to do."}'
    result = parse_orchestrator_response(raw)
    assert result is None


def test_stop_with_gaps_includes_reason():
    raw = '{"action": "stop_with_gaps", "parameters": {"reason": "No reliable sources found for this obscure point."}, "reason": "I have exhausted all available search tools."}'
    result = parse_orchestrator_response(raw)
    assert result is not None
    assert result["action"] == "stop_with_gaps"
    assert result["parameters"]["reason"] == "No reliable sources found for this obscure point."
