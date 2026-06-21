from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from app.schemas.document_export import ConstraintReport, DocumentSpec


class JSONChatModel(Protocol):
    async def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        ...


class DocumentRevisionAgent:
    """
    LLM-guided repair agent for failed document constraints.

    It receives:
    - current DocumentSpec
    - code-generated ConstraintReport

    It returns:
    - revised valid DocumentSpec JSON

    It does not render DOCX/PDF.
    It does not verify counts/pages.
    It only revises content/structure according to the verifier failure.
    """

    def __init__(
        self,
        model: JSONChatModel,
        *,
        max_repair_attempts: int = 3,
    ) -> None:
        self.model = model
        self.max_repair_attempts = max_repair_attempts

    async def revise(
        self,
        spec: DocumentSpec,
        report: ConstraintReport,
    ) -> DocumentSpec:
        messages = self._build_messages(spec, report)
        last_error: str | None = None

        for _ in range(self.max_repair_attempts):
            current_messages = list(messages)

            if last_error:
                current_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous JSON did not validate against DocumentSpec.\n\n"
                            f"Validation error:\n{last_error}\n\n"
                            "Return corrected JSON only. No markdown. No commentary."
                        ),
                    }
                )

            raw = await self.model.complete_json(current_messages)

            try:
                revised = DocumentSpec.model_validate(raw)
            except ValidationError as exc:
                last_error = str(exc)
                continue

            return revised

        raise RuntimeError(
            "DocumentRevisionAgent failed to produce valid DocumentSpec JSON. "
            f"Last validation error: {last_error}"
        )

    def _build_messages(
        self,
        spec: DocumentSpec,
        report: ConstraintReport,
    ) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are the Document Revision Agent for Sheria Research Agent.\n\n"
                    "You revise structured legal documents after deterministic code verification fails.\n\n"
                    "Return JSON only. The JSON must match the existing DocumentSpec schema exactly.\n\n"
                    "Core rules:\n"
                    "1. Preserve legal meaning.\n"
                    "2. Preserve source grounding.\n"
                    "3. Do not invent statutes, cases, citations, authorities, URLs, or facts.\n"
                    "4. Do not remove legal caveats or source-basis warnings.\n"
                    "5. Keep existing block ids stable unless a block truly must be added or split.\n"
                    "6. Do not change requested style constraints unless the failure report explicitly permits it.\n"
                    "7. If word count is too low, expand with substantive legal explanation, not filler.\n"
                    "8. If word count is too high, condense without damaging legal reasoning.\n"
                    "9. If page count is too high, shorten dense analysis/tables before touching structure.\n"
                    "10. If page count is too low, add useful analysis, examples, caveats, or source-basis explanation.\n"
                    "11. If page-specific text is missing, move or add the required content to the intended section.\n"
                    "12. Return the full revised DocumentSpec JSON object, not a patch.\n\n"
                    "Example JSON shape:\n"
                    "{\n"
                    '  "title": "Sheria Legal Research Document",\n'
                    '  "document_type": "sheria_generated_document",\n'
                    '  "output_formats": ["docx"],\n'
                    '  "style": {\n'
                    '    "font_family": "Times New Roman",\n'
                    '    "font_size_pt": 12,\n'
                    '    "line_spacing": 1.5,\n'
                    '    "page_size": "A4",\n'
                    '    "margin_top_cm": 2.54,\n'
                    '    "margin_bottom_cm": 2.54,\n'
                    '    "margin_left_cm": 2.54,\n'
                    '    "margin_right_cm": 2.54\n'
                    "  },\n"
                    '  "constraints": {},\n'
                    '  "blocks": [\n'
                    '    {"id": "p_1", "type": "paragraph", "text": "Revised text.", "citations": []}\n'
                    "  ],\n"
                    '  "metadata": {}\n'
                    "}"
                ),
            },
            {
                "role": "user",
                "content": (
                    "Revise this DocumentSpec JSON so that it satisfies the verification report.\n\n"
                    "Verification report JSON:\n"
                    f"{report.model_dump_json(indent=2)}\n\n"
                    "Current DocumentSpec JSON:\n"
                    f"{spec.model_dump_json(indent=2)}\n\n"
                    "Return only the revised full DocumentSpec JSON object."
                ),
            },
        ]
