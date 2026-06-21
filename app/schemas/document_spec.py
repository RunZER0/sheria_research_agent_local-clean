from __future__ import annotations
from typing import List, Literal, Optional, Dict, Any, Union
from pydantic import BaseModel, Field


class DocumentStyleSpec(BaseModel):
    font_name: str = "Times New Roman"
    font_size: int = 12
    line_spacing: float = 1.15
    margins: Literal["standard", "narrow", "wide"] = "standard"
    alignment: Literal["left", "justify"] = "left"


class WordCountConstraint(BaseModel):
    target: Optional[int] = None
    mode: Literal["exact", "max", "min", "none"] = "none"
    tolerance: int = 0
    scope: Literal["body_only", "entire_document", "exclude_tables"] = "body_only"


class PageConstraint(BaseModel):
    mode: Literal["exact_pages", "max_pages", "min_pages", "none"] = "none"
    value: int = 0


class PagePlanItem(BaseModel):
    page_number: int
    purpose: str
    must_include_text: List[str] = Field(default_factory=list)


class DocumentConstraints(BaseModel):
    word_count: WordCountConstraint = Field(default_factory=WordCountConstraint)
    page_count: PageConstraint = Field(default_factory=PageConstraint)
    page_plan: List[PagePlanItem] = Field(default_factory=list)
    required_sections: List[str] = Field(default_factory=list)


# --- Document Blocks (Union Polymorphism) ---

class HeadingBlock(BaseModel):
    id: str
    type: Literal["heading"] = "heading"
    level: int
    text: str


class ParagraphBlock(BaseModel):
    id: str
    type: Literal["paragraph"] = "paragraph"
    text: str
    citations: List[str] = Field(default_factory=list)


class TableBlock(BaseModel):
    id: str
    type: Literal["table"] = "table"
    caption: Optional[str] = None
    columns: List[str]
    rows: List[List[str]]


class PageBreakBlock(BaseModel):
    id: str
    type: Literal["page_break"] = "page_break"


class BulletListBlock(BaseModel):
    id: str
    type: Literal["bullet_list"] = "bullet_list"
    items: List[str]


DocumentBlock = Union[HeadingBlock, ParagraphBlock, TableBlock, PageBreakBlock, BulletListBlock]


class SourceManifestItem(BaseModel):
    source_id: str
    title: str
    url: str
    authority_level: str
    role_in_argument: str


class DocumentSpec(BaseModel):
    title: str
    document_type: str  # e.g., legal_memo, demand_letter, case_brief
    output_formats: List[Literal["docx", "pdf"]] = ["docx"]
    style: DocumentStyleSpec = Field(default_factory=DocumentStyleSpec)
    constraints: DocumentConstraints = Field(default_factory=DocumentConstraints)
    blocks: List[DocumentBlock] = Field(default_factory=list)
    source_manifest: List[SourceManifestItem] = Field(default_factory=list)
