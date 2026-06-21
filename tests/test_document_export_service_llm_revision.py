import pytest

from app.schemas.document_export import (
    ConstraintReport,
    DocumentConstraints,
    DocumentExportRequest,
    DocumentSpec,
    OutputFormat,
    ParagraphBlock,
    WordCountConstraint,
    WordCountScope,
)
from app.services.document_export_service import DocumentExportService


class FakeLLMRevisionAgent:
    def __init__(self):
        self.calls = []

    async def revise(self, spec: DocumentSpec, report: ConstraintReport) -> DocumentSpec:
        self.calls.append(report)

        first_failure = report.failures[0]

        if first_failure.code == "WORD_COUNT_TOO_LOW":
            spec.blocks = [
                ParagraphBlock(
                    id="p_1",
                    text="one two three four",
                )
            ]
            return spec

        if first_failure.code == "WORD_COUNT_TOO_HIGH":
            spec.blocks = [
                ParagraphBlock(
                    id="p_1",
                    text="one two three",
                )
            ]
            return spec

        return spec


@pytest.mark.document_export
@pytest.mark.asyncio
async def test_export_service_uses_llm_revision_callback_for_word_count(tmp_path):
    fake_revision_agent = FakeLLMRevisionAgent()

    service = DocumentExportService(
        artifact_root=tmp_path,
        revision_callback=fake_revision_agent.revise,
    )

    request = DocumentExportRequest(
        user_request="Create a DOCX with exactly 4 words.",
        title="Exact Word Test",
        answer_text="one two",
        output_formats=[OutputFormat.docx],
        constraints=DocumentConstraints(
            word_count=WordCountConstraint(
                target=4,
                tolerance=0,
                scope=WordCountScope.body_only,
            )
        ),
    )

    result = await service.export(request)

    assert result.ok is True
    assert result.docx_path is not None
    assert result.artifact_manifest_path is not None
    assert len(fake_revision_agent.calls) == 1
    assert fake_revision_agent.calls[0].failures[0].code == "WORD_COUNT_TOO_LOW"


@pytest.mark.document_export
@pytest.mark.asyncio
async def test_export_service_requires_revision_callback_when_constraint_fails(tmp_path):
    service = DocumentExportService(
        artifact_root=tmp_path,
        revision_callback=None,
    )

    request = DocumentExportRequest(
        user_request="Create a DOCX with exactly 4 words.",
        title="Missing Revision Callback",
        answer_text="one two",
        output_formats=[OutputFormat.docx],
        constraints=DocumentConstraints(
            word_count=WordCountConstraint(
                target=4,
                tolerance=0,
                scope=WordCountScope.body_only,
            )
        ),
    )

    with pytest.raises(RuntimeError, match="revision_callback"):
        await service.export(request)


@pytest.mark.document_export
@pytest.mark.asyncio
async def test_export_service_does_not_call_revision_when_constraints_pass(tmp_path):
    fake_revision_agent = FakeLLMRevisionAgent()

    service = DocumentExportService(
        artifact_root=tmp_path,
        revision_callback=fake_revision_agent.revise,
    )

    request = DocumentExportRequest(
        user_request="Create a DOCX with exactly 4 words.",
        title="Already Valid",
        answer_text="one two three four",
        output_formats=[OutputFormat.docx],
        constraints=DocumentConstraints(
            word_count=WordCountConstraint(
                target=4,
                tolerance=0,
                scope=WordCountScope.body_only,
            )
        ),
    )

    result = await service.export(request)

    assert result.ok is True
    assert len(fake_revision_agent.calls) == 0
    assert result.docx_path is not None
