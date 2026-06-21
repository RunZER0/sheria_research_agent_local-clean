from pathlib import Path

import pytest

from app.schemas.document_export import (
    ConstraintFailure,
    ConstraintReport,
    DocumentConstraints,
    DocumentExportRequest,
    DocumentSpec,
    OutputFormat,
    PageCountConstraint,
    PageCountMode,
    ParagraphBlock,
)
from app.services.document_export_service import DocumentExportService


class FakePdfPublisher:
    def convert_docx_to_pdf(self, docx_path: Path, output_dir: Path):
        fake_pdf = output_dir / f"{docx_path.stem}.pdf"
        fake_pdf.write_bytes(b"%PDF-FAKE")
        return fake_pdf


class FakePdfVerifier:
    def __init__(self):
        self.calls = 0

    def verify(self, pdf_path: Path, spec: DocumentSpec) -> ConstraintReport:
        self.calls += 1

        if self.calls == 1:
            return ConstraintReport(
                ok=False,
                failures=[
                    ConstraintFailure(
                        code="PAGE_COUNT_MISMATCH",
                        message="PDF has 5 pages; expected exactly 4.",
                        expected=4,
                        actual=5,
                        suggested_action="Condense content.",
                    )
                ],
            )

        return ConstraintReport(ok=True)


class FakePageRevisionAgent:
    def __init__(self):
        self.calls = []

    async def revise(self, spec: DocumentSpec, report: ConstraintReport) -> DocumentSpec:
        self.calls.append(report)
        spec.blocks = [
            ParagraphBlock(
                id="p_1",
                text="Condensed legal analysis that fits the requested page count.",
            )
        ]
        return spec


@pytest.mark.document_export
@pytest.mark.asyncio
async def test_page_count_failure_triggers_llm_revision_and_rerender(tmp_path):
    revision_agent = FakePageRevisionAgent()

    service = DocumentExportService(
        artifact_root=tmp_path,
        revision_callback=revision_agent.revise,
    )

    fake_pdf_verifier = FakePdfVerifier()
    service.pdf_publisher = FakePdfPublisher()
    service.pdf_verifier = fake_pdf_verifier

    request = DocumentExportRequest(
        user_request="Return as DOCX of exactly 4 pages.",
        title="Page Loop Test",
        answer_text="Long legal analysis.",
        output_formats=[OutputFormat.docx],
        constraints=DocumentConstraints(
            page_count=PageCountConstraint(
                mode=PageCountMode.exact,
                value=4,
            )
        ),
    )

    result = await service.export(request)

    assert result.ok is True
    assert len(revision_agent.calls) == 1
    assert revision_agent.calls[0].failures[0].code == "PAGE_COUNT_MISMATCH"
    assert fake_pdf_verifier.calls == 2
    assert result.docx_path is not None
