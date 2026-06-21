import pytest

from app.research_state import ResearchState, ResearchGap
from app.tool_registry import default_registry
import app.source_lifecycle as sl


def test_research_state_gap_open_and_fill():
    state = ResearchState(query="Employment Act sections 41,43,45")
    gap = state.open_gap("Text of Employment Act section 41", priority="critical", related_queries=["Employment Act 2007 section 41"])
    assert gap in state.gaps
    assert gap.status == "open"

    filled = state.fill_gap(gap.gap_id, note="Found in Employment Act PDF")
    assert filled is True
    assert gap.status == "filled"
    assert "Found in Employment Act PDF" in gap.notes


def test_tool_registry_has_core_tools():
    reg = default_registry()
    names = {t.name for t in reg.list_tools()}
    assert "brave_search_fallback" in names
    assert "new_kenyalaw_native" in names
    assert "browser_fetch_firefox" in names
    assert "pdf_text_extract" in names


def test_source_lifecycle_constants():
    assert sl.FOUND == "found"
    assert sl.EVIDENCE_ACCEPTED == "evidence_accepted"
    assert sl.UNREADABLE == "unreadable"
