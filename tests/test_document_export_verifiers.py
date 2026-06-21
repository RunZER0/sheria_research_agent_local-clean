import pytest

from app.schemas.document_export import (
    DocumentConstraints,
    DocumentSpec,
    ParagraphBlock,
    WordCountConstraint,
    WordCountScope,
)
from app.services.document_export_service import WordCountVerifier


@pytest.mark.document_export
def test_word_count_verifier_passes_exact_count():
    spec = DocumentSpec(
        title="Exact Count",
        blocks=[
            ParagraphBlock(
                id="p_1",
                text="one two three four",
            )
        ],
        constraints=DocumentConstraints(
            word_count=WordCountConstraint(
                target=4,
                tolerance=0,
                scope=WordCountScope.body_only,
            )
        ),
    )

    report = WordCountVerifier().verify(spec)

    assert report.ok is True
    assert report.failures == []


@pytest.mark.document_export
def test_word_count_verifier_reports_too_low():
    spec = DocumentSpec(
        title="Too Low",
        blocks=[
            ParagraphBlock(
                id="p_1",
                text="one two three",
            )
        ],
        constraints=DocumentConstraints(
            word_count=WordCountConstraint(
                target=5,
                tolerance=0,
                scope=WordCountScope.body_only,
            )
        ),
    )

    report = WordCountVerifier().verify(spec)

    assert report.ok is False
    assert report.failures[0].code == "WORD_COUNT_TOO_LOW"
    assert report.failures[0].expected == 5
    assert report.failures[0].actual == 3


@pytest.mark.document_export
def test_word_count_verifier_reports_too_high():
    spec = DocumentSpec(
        title="Too High",
        blocks=[
            ParagraphBlock(
                id="p_1",
                text="one two three four five",
            )
        ],
        constraints=DocumentConstraints(
            word_count=WordCountConstraint(
                target=3,
                tolerance=0,
                scope=WordCountScope.body_only,
            )
        ),
    )

    report = WordCountVerifier().verify(spec)

    assert report.ok is False
    assert report.failures[0].code == "WORD_COUNT_TOO_HIGH"
    assert report.failures[0].expected == 3
    assert report.failures[0].actual == 5
