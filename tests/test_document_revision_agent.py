import pytest

from app.agents.document_revision_agent import DocumentRevisionAgent
from app.schemas.document_export import (
    ConstraintFailure,
    ConstraintReport,
    DocumentSpec,
    ParagraphBlock,
)


class FakeJSONModel:
    async def complete_json(self, messages):
        return {
            "title": "Revised",
            "document_type": "sheria_generated_document",
            "output_formats": ["docx"],
            "style": {
                "font_family": "Times New Roman",
                "font_size_pt": 12,
                "line_spacing": 1.5,
                "page_size": "A4",
                "margin_top_cm": 2.54,
                "margin_bottom_cm": 2.54,
                "margin_left_cm": 2.54,
                "margin_right_cm": 2.54,
            },
            "constraints": {},
            "blocks": [
                {
                    "id": "p_1",
                    "type": "paragraph",
                    "text": "This revised paragraph preserves legal meaning and fixes the failed constraint.",
                    "citations": [],
                }
            ],
            "metadata": {},
        }


@pytest.mark.asyncio
async def test_document_revision_agent_returns_valid_document_spec():
    agent = DocumentRevisionAgent(model=FakeJSONModel())

    spec = DocumentSpec(
        title="Original",
        blocks=[
            ParagraphBlock(
                id="p_1",
                text="Too short.",
            )
        ],
    )

    report = ConstraintReport(
        ok=False,
        failures=[
            ConstraintFailure(
                code="WORD_COUNT_TOO_LOW",
                message="Word count is too low.",
                expected=20,
                actual=2,
            )
        ],
    )

    revised = await agent.revise(spec, report)

    assert isinstance(revised, DocumentSpec)
    assert revised.title == "Revised"
    assert revised.blocks[0].type == "paragraph"
