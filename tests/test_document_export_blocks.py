import pytest

from app.schemas.document_export import (
    BulletListBlock,
    HeadingBlock,
    NumberedListBlock,
    ParagraphBlock,
    TableBlock,
    WordCountScope,
)
from app.services.document_export_service import (
    count_words,
    extract_countable_text,
    split_answer_to_blocks,
)
from app.schemas.document_export import DocumentSpec


@pytest.mark.document_export
def test_count_words_handles_basic_text():
    assert count_words("one two three") == 3
    assert count_words("court's decision was well-reasoned") == 4
    assert count_words("Section 45 applies.") == 3


@pytest.mark.document_export
def test_split_answer_to_blocks_parses_markdown_structure():
    answer = """
# Executive Summary

This is the first paragraph.

| Remedy | Legal Basis |
|---|---|
| Compensation | Employment Act |
| Reinstatement | Employment Act |

- First point
- Second point

1. First step
2. Second step
""".strip()

    blocks = split_answer_to_blocks(answer)

    assert any(isinstance(block, HeadingBlock) for block in blocks)
    assert any(isinstance(block, ParagraphBlock) for block in blocks)
    assert any(isinstance(block, TableBlock) for block in blocks)
    assert any(isinstance(block, BulletListBlock) for block in blocks)
    assert any(isinstance(block, NumberedListBlock) for block in blocks)

    table = next(block for block in blocks if isinstance(block, TableBlock))
    assert table.columns == ["Remedy", "Legal Basis"]
    assert table.rows[0] == ["Compensation", "Employment Act"]


@pytest.mark.document_export
def test_extract_countable_text_body_only_excludes_heading_and_table():
    spec = DocumentSpec(
        title="Test",
        blocks=split_answer_to_blocks(
            """
# Heading

one two three

| A | B |
|---|---|
| four | five |
""".strip()
        ),
    )

    text = extract_countable_text(spec, WordCountScope.body_only)

    assert "one two three" in text
    assert "Heading" not in text
    assert "four" not in text
    assert count_words(text) == 3


@pytest.mark.document_export
def test_extract_countable_text_include_tables_counts_table_text():
    spec = DocumentSpec(
        title="Test",
        blocks=split_answer_to_blocks(
            """
# Heading

one two three

| A | B |
|---|---|
| four | five |
""".strip()
        ),
    )

    text = extract_countable_text(spec, WordCountScope.include_tables)

    assert "one two three" in text
    assert "four" in text
    assert "five" in text
