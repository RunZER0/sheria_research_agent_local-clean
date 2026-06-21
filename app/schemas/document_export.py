from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union
from pydantic import BaseModel, Field


class OutputFormat(str, Enum):
    docx = "docx"
    pdf = "pdf"


class PageCountMode(str, Enum):
    exact = "exact"
    max = "max"
    min = "min"


class WordCountScope(str, Enum):
    body_only = "body_only"
    entire_document = "entire_document"
    include_tables = "include_tables"


class DocumentStyle(BaseModel):
    font_family: str = "Times New Roman"
    font_size_pt: int = 12
    line_spacing: float = 1.5
    page_size: Literal["A4", "LETTER"] = "A4"
    margin_top_cm: float = 2.54
    margin_bottom_cm: float = 2.54
    margin_left_cm: float = 2.54
    margin_right_cm: float = 2.54


class WordCountConstraint(BaseModel):
    target: int
    tolerance: int = 0
    scope: WordCountScope = WordCountScope.body_only


class PageCountConstraint(BaseModel):
    mode: PageCountMode = PageCountMode.exact
    value: int


class PageSpecificRequirement(BaseModel):
    page_number: int
    must_include_text: list[str] = Field(default_factory=list)
    must_include_block_ids: list[str] = Field(default_factory=list)


class DocumentConstraints(BaseModel):
    word_count: Optional[WordCountConstraint] = None
    page_count: Optional[PageCountConstraint] = None
    page_requirements: list[PageSpecificRequirement] = Field(default_factory=list)


class HeadingBlock(BaseModel):
    id: str
    type: Literal["heading"] = "heading"
    level: int = Field(default=1, ge=1, le=4)
    text: str


class ParagraphBlock(BaseModel):
    id: str
    type: Literal["paragraph"] = "paragraph"
    text: str
    citations: list[str] = Field(default_factory=list)


class TableBlock(BaseModel):
    id: str
    type: Literal["table"] = "table"
    caption: Optional[str] = None
    columns: list[str]
    rows: list[list[str]]


class BulletListBlock(BaseModel):
    id: str
    type: Literal["bullet_list"] = "bullet_list"
    items: list[str]


class NumberedListBlock(BaseModel):
    id: str
    type: Literal["numbered_list"] = "numbered_list"
    items: list[str]


class PageBreakBlock(BaseModel):
    id: str
    type: Literal["page_break"] = "page_break"


class SourceBasisItem(BaseModel):
    source: str
    role: str
    strength: Literal["strong", "moderate", "weak", "context_only"]


class SourceBasisBlock(BaseModel):
    id: str
    type: Literal["source_basis"] = "source_basis"
    items: list[SourceBasisItem]


DocumentBlock = Annotated[
    Union[
        HeadingBlock,
        ParagraphBlock,
        TableBlock,
        BulletListBlock,
        NumberedListBlock,
        PageBreakBlock,
        SourceBasisBlock,
    ],
    Field(discriminator="type"),
]


class DocumentSpec(BaseModel):
    title: str
    document_type: str = "legal_document"
    output_formats: list[OutputFormat] = Field(default_factory=lambda: [OutputFormat.docx])
    style: DocumentStyle = Field(default_factory=DocumentStyle)
    constraints: DocumentConstraints = Field(default_factory=DocumentConstraints)
    blocks: list[DocumentBlock]
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentExportRequest(BaseModel):
    user_request: str
    answer_text: str
    title: str = "Sheria Legal Research Document"
    output_formats: list[OutputFormat] = Field(default_factory=lambda: [OutputFormat.docx])
    style: DocumentStyle = Field(default_factory=DocumentStyle)
    constraints: DocumentConstraints = Field(default_factory=DocumentConstraints)


class ConstraintFailure(BaseModel):
    code: str
    severity: Literal["error", "warning"] = "error"
    message: str
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    block_id: Optional[str] = None
    suggested_action: Optional[str] = None


class ConstraintReport(BaseModel):
    ok: bool
    failures: list[ConstraintFailure] = Field(default_factory=list)


class DocumentExportResult(BaseModel):
    ok: bool
    document_id: str
    docx_path: Optional[str] = None
    pdf_path: Optional[str] = None
    artifact_manifest_path: Optional[str] = None
    constraint_report: ConstraintReport
    warnings: list[str] = Field(default_factory=list)
