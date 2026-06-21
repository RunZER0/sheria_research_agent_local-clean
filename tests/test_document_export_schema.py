import pytest
from pydantic import ValidationError

from app.schemas.document_export import (
    DocumentConstraints,
    DocumentSpec,
    DocumentStyle,
    HeadingBlock,
    OutputFormat,
    PageCountConstraint,
    PageCountMode,
    ParagraphBlock,
    TableBlock,
    WordCountConstraint,
    WordCountScope,
)


@pytest.mark.document_export
def test_document_spec_accepts_discriminated_blocks():
    payload = {
        "title": "Test Legal Memo",
        "document_type": "legal_memo",
        "output_formats": ["docx", "pdf"],
        "style": {
            "font_family": "Times New Roman",
            "font_size_pt": 12,
            "line_spacing": 1.5,
            "page_size": "A4",
        },
        "constraints": {
            "word_count": {
                "target": 1000,
                "tolerance": 0,
                "scope": "body_only",
            },
            "page_count": {
                "mode": "exact",
                "value": 4,
            },
        },
        "blocks": [
            {
                "id": "h_1",
                "type": "heading",
                "level": 1,
                "text": "Executive Summary",
            },
            {
                "id": "p_1",
                "type": "paragraph",
                "text": "This is a test paragraph.",
            },
            {
                "id": "t_1",
                "type": "table",
                "caption": "Remedies Table",
                "columns": ["Remedy", "Basis"],
                "rows": [["Compensation", "Employment Act"]],
            },
        ],
    }

    spec = DocumentSpec.model_validate(payload)

    assert spec.title == "Test Legal Memo"
    assert spec.output_formats == [OutputFormat.docx, OutputFormat.pdf]
    assert isinstance(spec.blocks[0], HeadingBlock)
    assert isinstance(spec.blocks[1], ParagraphBlock)
    assert isinstance(spec.blocks[2], TableBlock)
    assert spec.constraints.word_count.target == 1000
    assert spec.constraints.page_count.value == 4


@pytest.mark.document_export
def test_document_style_rejects_invalid_page_size():
    with pytest.raises(ValidationError):
        DocumentStyle(page_size="LEGAL")


@pytest.mark.document_export
def test_document_constraints_model():
    constraints = DocumentConstraints(
        word_count=WordCountConstraint(
            target=500,
            tolerance=0,
            scope=WordCountScope.body_only,
        ),
        page_count=PageCountConstraint(
            mode=PageCountMode.exact,
            value=2,
        ),
    )

    assert constraints.word_count.target == 500
    assert constraints.page_count.mode == PageCountMode.exact
