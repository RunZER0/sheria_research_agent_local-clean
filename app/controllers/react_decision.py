from __future__ import annotations

import json
import re
from typing import Any

from app.schemas.research_state import (
    ResearchState,
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _safe_json_loads(text: str) -> dict[str, Any]:
    match = _JSON_RE.search(text or "")
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def build_orchestrator_prompt(state: ResearchState) -> list[dict[str, str]]:
    system_prompt = """\
You are Sheria, a legal research agent. You answer the user's question directly.

## Available tools
1. **kenya_law_case_resolve**(query: str) — Resolve a specific Kenyan case from citation metadata (court, year, number, date). Use when user provides a neutral citation like [2026] KESC 45 (KLR).
2. **case_specific_search**(query: str) — Search for a specific case using party names, case numbers, and legal issue terms. Better than a generic search when you have partial case info.
3. **kenyalaw_legislation_search**(query: str) — Search Kenya Law for statutes, acts, sections.
4. **kenyalaw_judgment_search**(query: str) — Search Kenya Law for case law, judgments, rulings.
5. **official_kenya_domain_search**(query: str) — Search official Kenyan government domains (.go.ke) via Brave.
6. **brave_search_fallback**(query: str) — General web search via Brave.
7. **fetch_url**(url: str, title: str) — Download and read the full text of a specific URL.
8. **synthesize_answer**() — You have enough information and can now write the final answer. You MUST include a `final_answer` field with the full answer text.
9. **stop_with_gaps**(reason: str) — No useful sources remain and you cannot answer.

## Discovery ladder (use in this priority for case law)
- For exact case requests (user provided citation): use **kenya_law_case_resolve** first to reconstruct the AKN URL.
- For fuzzy case requests (partial case name): use **case_specific_search** with party fragments and issue terms.
- If case_resolve returns AKN candidates, try **fetch_url** on each to verify content.
- If specific search scores candidates, fetch the highest-scoring one first.
- **Always fetch and read a candidate before treating it as evidence.** Search results are candidates only.
- Use Kenya Law tools before browser fallbacks.
- Use brave_search_fallback for current/time-sensitive information or when Kenya Law tools fail.

## Output format
Return JSON only:
{
  "action": "tool_name",
  "parameters": { "key": "value" },
  "reason": "Your explanation of what you're doing.",
  "final_answer": "When action is synthesize_answer, include the full answer here directly.",
  "document_title": "Optional proposed filename if generating a document."
}
For synthesize_answer or stop_with_gaps, always include final_answer with your response text.
For stop_with_gaps, explain what gaps remain."""

    evidence_lines = []
    for i, ev in enumerate(state.evidence_ledger):
        evidence_lines.append(f"[{i+1}] {ev.source_title}")
        evidence_lines.append(f"    Role: {ev.basis_role.value} | Strength: {ev.basis_strength.value}")
        evidence_lines.append(f"    URL: {ev.url}")
        if ev.passage:
            evidence_lines.append(f"    Excerpt: {ev.passage[:600]}")
        evidence_lines.append("")

    evidence_text = "\n".join(evidence_lines) if evidence_lines else "No evidence gathered yet."

    candidate_lines = []
    fetched_urls = {ev.url for ev in state.evidence_ledger}
    unfetched = [c for c in state.source_candidates if c.url not in fetched_urls][:15]
    if unfetched:
        for c in unfetched[:10]:
            candidate_lines.append(f"  - {c.title}")
            candidate_lines.append(f"    URL: {c.url}")
            candidate_lines.append(f"    Type: {c.document_type_hint.value}, Source: {c.discovered_by}")
        if len(unfetched) > 10:
            candidate_lines.append(f"  ... and {len(unfetched) - 10} more")
    else:
        candidate_lines.append("  No unfetched candidates available.")
    candidates_text = "\n".join(candidate_lines)

    error_lines = []
    if state.coverage_report.failed_fetches > 0:
        error_lines.append(f"- {state.coverage_report.failed_fetches} failed fetches")
    if state.coverage_report.unresolved_gaps:
        for g in state.coverage_report.unresolved_gaps:
            error_lines.append(f"- Gap: {g}")
    if state.coverage_report.fallback_reasons:
        for r in state.coverage_report.fallback_reasons:
            error_lines.append(f"- Tool feedback: {r}")
    errors_text = "\n".join(error_lines) if error_lines else "No errors."

    attempted = state.coverage_report.attempted_tools
    attempted_text = ", ".join(attempted) if attempted else "none"

    user_prompt = f"""\
## Current state
- Query: {state.normalized_query}
- Jurisdiction: {state.jurisdiction_target.value}
- Query type: {state.query_type.value}
- Round: {state.search_round}/{state.max_rounds}
- Tools attempted this run: {attempted_text}

## Search candidates available to fetch
{candidates_text}

## Sources evaluated so far
{evidence_text}

## Tool feedback / errors
{errors_text}

## What do you do now?
Answer the user's question. Either use a tool to find information, or synthesize your answer directly."""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_orchestrator_response(raw_json: str) -> dict[str, Any] | None:
    data = _safe_json_loads(raw_json)
    if not data or "action" not in data:
        return None
    return data
