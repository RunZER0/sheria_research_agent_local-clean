from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class UploadedAttachmentRef(BaseModel):
    """Reference to an uploaded file, sent by the frontend with the chat request."""
    id: str
    filename: str
    mime_type: str
    size_bytes: int


class ChatRequest(BaseModel):
    # Accept both 'message' (frontend) and 'query' (legacy backend) for backward compat
    message: Optional[str] = None
    query: Optional[str] = None
    session_id: str = "default"
    kenya_legal_mode: bool = True
    strict_mode: bool = True
    deep_research: bool = False
    clarification_answers: Optional[dict[str, str]] = None

    # Frontend Phase 2 fields
    model: Optional[str] = None
    reasoning_effort: Optional[Literal["high", "max"]] = None
    attachments: list[UploadedAttachmentRef] = Field(default_factory=list)

    # Workspace contextual awareness
    workspace_tree: list[dict] = Field(default_factory=list, description="File tree from the user's workspace for agent awareness")

    @property
    def normalized_query(self) -> str:
        text = self.message or self.query or ""
        return text.strip()


class Source(BaseModel):
    id: str
    title: str
    url: str
    snippet: str = ""
    text: str = ""
    score: float = 0.0
    quality_score: float = 0.0
    quality_label: str = ""
    source_type: str = ""
    authority_level: str = ""
    trust_reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    selected: bool = False
    rejection_reason: str = ""


class EvidenceCard(BaseModel):
    source_id: str
    title: str
    url: str
    excerpt: str
    domain: str
    quality_score: float = 0.0
    quality_label: str = ""
    source_type: str = ""
    authority_level: str = ""
    trust_reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    # Extended fields for resilient architecture
    basis_role: str = "weak_or_unverified"
    can_support_legal_claim: bool = False
    can_support_context: bool = True
    fallback_only: bool = True
    readability_status: str = "unreadable"
    relevance_score: float = 0.0
    supports_gaps: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    extraction_method: str = ""
