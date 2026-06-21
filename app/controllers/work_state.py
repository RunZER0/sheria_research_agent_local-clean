from __future__ import annotations

from app.schemas.research_state import ReActAction, ReActActionType


def status_for_action(action: ReActAction) -> str:
    if action.action == ReActActionType.KENYALAW_JUDGMENT_SEARCH:
        return "Searching Kenya Law judgments for Kenyan case law."
    if action.action == ReActActionType.KENYALAW_LEGISLATION_SEARCH:
        return "Searching Kenya Law legislation for statutory authority."
    if action.action == ReActActionType.OFFICIAL_KENYA_DOMAIN_SEARCH:
        return "Searching official Kenyan domains after native discovery was insufficient."
    if action.action == ReActActionType.BRAVE_SEARCH_FALLBACK:
        return "Running wider discovery to locate additional legal sources."
    if action.action == ReActActionType.FETCH_URL:
        return "Fetching and ingesting the selected source."
    if action.action == ReActActionType.SYNTHESIZE_ANSWER:
        return "Preparing the grounded answer from the evidence ledger."
    if action.action == ReActActionType.STOP_WITH_GAPS:
        return "Stopping research with unresolved gaps and preparing a flagged answer."
    return "Continuing legal research."
