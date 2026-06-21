import pytest

from app.schemas.document_export import OutputFormat, PageCountMode
from app.skills.document_export_skill import DocumentExportSkill


class FakeExportService:
    def __init__(self):
        self.last_request = None

    async def export(self, request):
        self.last_request = request

        class Result:
            ok = True
            document_id = "doc_fake"
            docx_path = "data/artifacts/generated/doc_fake/doc_fake.docx"
            pdf_path = None
            artifact_manifest_path = "data/artifacts/generated/doc_fake/artifact_manifest.json"
            warnings = []

        return Result()


@pytest.mark.document_export
@pytest.mark.asyncio
async def test_document_export_skill_infers_docx_style_and_page_constraints():
    fake_service = FakeExportService()
    skill = DocumentExportSkill(export_service=fake_service)

    await skill.run(
        user_request=(
            "Return your answer as a DOCX of exactly 4 pages, "
            "Times New Roman, font size 12, 1.5 spacing."
        ),
        grounded_answer="This is the grounded legal answer.",
        title="Sheria Memo",
    )

    request = fake_service.last_request

    assert request.title == "Sheria Memo"
    assert request.output_formats == [OutputFormat.docx]
    assert request.style.font_family == "Times New Roman"
    assert request.style.font_size_pt == 12
    assert request.style.line_spacing == 1.5
    assert request.constraints.page_count.mode == PageCountMode.exact
    assert request.constraints.page_count.value == 4


@pytest.mark.document_export
@pytest.mark.asyncio
async def test_document_export_skill_infers_pdf_and_word_count():
    fake_service = FakeExportService()
    skill = DocumentExportSkill(export_service=fake_service)

    await skill.run(
        user_request="Return as DOCX and PDF. Body must be exactly 1000 words.",
        grounded_answer="This is the grounded legal answer.",
    )

    request = fake_service.last_request

    assert request.output_formats == [OutputFormat.docx, OutputFormat.pdf]
    assert request.constraints.word_count.target == 1000
    assert request.constraints.word_count.tolerance == 0
