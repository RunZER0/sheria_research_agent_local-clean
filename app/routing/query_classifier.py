from __future__ import annotations

import json
import re
from typing import Any

from app.schemas.research_state import (
    JurisdictionTarget,
    OutputFormat,
    QueryClassification,
    QueryType,
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


def _fallback_classification(user_prompt: str) -> QueryClassification:
    """Minimal fallback when the LLM is unavailable. No keyword heuristics."""
    return QueryClassification(
        normalized_query=user_prompt.strip(),
        jurisdiction_target=JurisdictionTarget.UNKNOWN,
        query_type=QueryType.UNKNOWN,
        requested_outputs=[OutputFormat.CHAT],
        requested_document_constraints={},
        confidence=0.0,
    )


_CLASSIFY_SYSTEM_PROMPT = """\
You are a legal query classifier. Return JSON only.

Rules:
- jurisdiction_target must be one of: kenya, foreign, comparative, general, unknown
- query_type must be one of: statute, case_law, mixed, theory, document_only, unsupported, unknown
- requested_outputs must be a list from: chat, docx, pdf, txt
- requested_document_constraints captures formatting hints:
  { "font_family": "...", "font_size_pt": 12, "line_spacing": 1.5, "page_count": {"mode": "exact", "value": 4}, "word_count": {"mode": "exact", "target": 1000, "scope": "body_only"} }
- detected_statutes, detected_sections, detected_cases, detected_citations: list of extracted references
- unsupported_actions: list of any action the user requested that Sheria cannot do
- confidence: 0.0 to 1.0 (how certain you are about jurisdiction and query_type)
- normalized_query: cleaned version of the user request

Do not invent jurisdiction or query_type. Use "unknown" when uncertain.
"""


class LegalQueryClassifier:
    def __init__(self, llm_client=None) -> None:
        self.llm = llm_client

    async def classify(self, user_prompt: str) -> QueryClassification:
        fallback = _fallback_classification(user_prompt)

        if self.llm is None:
            return fallback

        prompt = f"""{_CLASSIFY_SYSTEM_PROMPT}

User request:
{user_prompt}

Return:
{{
  "normalized_query": "...",
  "jurisdiction_target": "...",
  "jurisdictions": [],
  "query_type": "...",
  "requested_outputs": ["chat"],
  "requested_document_constraints": {{}},
  "detected_statutes": [],
  "detected_sections": [],
  "detected_cases": [],
  "detected_citations": [],
  "unsupported_actions": [],
  "confidence": 0.0
}}""".strip()

        try:
            raw = await self.llm.complete([{"role": "user", "content": prompt}], max_tokens=700)
            data = _safe_json_loads(raw)
            if not data:
                return fallback

            llm_classification = QueryClassification.model_validate(data)
            return llm_classification

        except Exception:
            return fallback
