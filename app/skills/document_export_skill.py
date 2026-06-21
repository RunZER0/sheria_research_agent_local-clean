"""
DocumentExportSkill — agent skill that infers document formatting, constraints,
and output formats from a natural-language user request and orchestrates the
DocumentExportService pipeline with an LLM revision callback.
"""

from __future__ import annotations

import re
from typing import Optional

from app.schemas.document_export import (
    ConstraintReport,
    DocumentConstraints,
    DocumentExportRequest,
    DocumentExportResult,
    DocumentSpec,
    DocumentStyle,
    OutputFormat,
    PageCountConstraint,
    PageCountMode,
    WordCountConstraint,
    WordCountScope,
)
from app.services.document_export_service import DocumentExportService, RevisionCallback


def wants_document_export(text: str) -> bool:
    """Return True if the user explicitly requests a document export."""
    lowered = text.lower()
    return any(x in lowered for x in ["docx", "word document", "pdf", "as a document"])


def infer_output_formats(text: str) -> list[OutputFormat]:
    """Parse the user request for output format hints."""
    lowered = text.lower()
    formats: list[OutputFormat] = []

    if "docx" in lowered or "word document" in lowered:
        formats.append(OutputFormat.docx)

    if "pdf" in lowered:
        formats.append(OutputFormat.pdf)

    return formats or [OutputFormat.docx]


def infer_style(text: str) -> DocumentStyle:
    """Parse the user request for font, size, spacing and page size hints."""
    lowered = text.lower()

    font_family = "Times New Roman"
    if "arial" in lowered:
        font_family = "Arial"
    elif "calibri" in lowered:
        font_family = "Calibri"

    font_size = 12
    match = re.search(r"(?:font size|size)\s*(\d{1,2})", lowered)
    if match:
        font_size = int(match.group(1))

    line_spacing = 1.5
    match = re.search(r"(\d(?:\.\d)?)\s*(?:spacing|line spacing)", lowered)
    if match:
        line_spacing = float(match.group(1))

    page_size = "A4"
    if "letter" in lowered:
        page_size = "LETTER"

    return DocumentStyle(
        font_family=font_family,
        font_size_pt=font_size,
        line_spacing=line_spacing,
        page_size=page_size,  # type: ignore[arg-type]
    )


def infer_constraints(text: str) -> DocumentConstraints:
    """Parse the user request for word-count and page-count constraints."""
    lowered = text.lower()

    page_count = None
    word_count = None

    page_match = re.search(r"(?:exactly\s*)?(\d+)\s*pages?", lowered)
    if page_match:
        page_count = PageCountConstraint(
            mode=PageCountMode.exact,
            value=int(page_match.group(1)),
        )

    word_match = re.search(r"(?:exactly\s*)?(\d+)\s*words?", lowered)
    if word_match:
        word_count = WordCountConstraint(
            target=int(word_match.group(1)),
            tolerance=0,
            scope=WordCountScope.body_only,
        )

    return DocumentConstraints(
        page_count=page_count,
        word_count=word_count,
    )


class DocumentExportSkill:
    """
    Agent skill that takes a user request and a grounded answer, infers
    formatting/constraint requirements, and runs the DocumentExportService.
    """

    def __init__(
        self,
        export_service: Optional[DocumentExportService] = None,
        revision_callback: Optional[RevisionCallback] = None,
    ) -> None:
        self._service = export_service or DocumentExportService(
            revision_callback=revision_callback,
        )

    async def run(
        self,
        user_request: str,
        grounded_answer: str,
        title: str = "Sheria Legal Research Document",
    ) -> DocumentExportResult:
        output_formats = infer_output_formats(user_request)
        style = infer_style(user_request)
        constraints = infer_constraints(user_request)

        export_request = DocumentExportRequest(
            user_request=user_request,
            title=title,
            answer_text=grounded_answer,
            output_formats=output_formats,
            style=style,
            constraints=constraints,
        )

        return await self._service.export(export_request)
