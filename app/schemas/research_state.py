from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, HttpUrl
import uuid


class JurisdictionTarget(str, Enum):
    KENYA = "kenya"
    FOREIGN = "foreign"
    COMPARATIVE = "comparative"
    GENERAL = "general"
    UNKNOWN = "unknown"


class QueryType(str, Enum):
    STATUTE = "statute"
    CASE_LAW = "case_law"
    MIXED = "mixed"
    THEORY = "theory"
    DOCUMENT_ONLY = "document_only"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class OutputFormat(str, Enum):
    CHAT = "chat"
    DOCX = "docx"
    PDF = "pdf"
    TXT = "txt"


class BasisRole(str, Enum):
    PRIMARY_LEGISLATION = "primary legislation"
    PRIMARY_CASE_LAW = "primary case law"
    OFFICIAL_SECONDARY = "official secondary"
    PERSUASIVE = "persuasive"
    BACKGROUND = "background"
    CONTEXT_ONLY = "context-only"
    UNKNOWN = "unknown"


class BasisStrength(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    PERSUASIVE = "persuasive"
    WEAK = "weak"
    UNREADABLE = "unreadable"


class DocumentType(str, Enum):
    STATUTE = "statute"
    JUDGMENT = "judgment"
    GAZETTE = "gazette"
    REGULATOR_DECISION = "regulator_decision"
    COMMENTARY = "commentary"
    THEORY = "theory"
    USER_DOCUMENT = "user_document"
    UNKNOWN = "unknown"


class SourceCandidate(BaseModel):
    source_id: str = Field(default_factory=lambda: f"src_{uuid.uuid4().hex[:8]}")
    title: str = "Untitled source"
    url: str
    snippet: str = ""
    discovered_by: str
    jurisdiction_hint: JurisdictionTarget = JurisdictionTarget.UNKNOWN
    document_type_hint: DocumentType = DocumentType.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    score: int = 0
    score_label: str = "unscored"


class FetchResult(BaseModel):
    ok: bool
    url: str
    final_url: str | None = None
    status_code: int | None = None
    content_type: str = ""
    parser_used: str = ""
    fetch_method: str = ""
    title: str = "Fetched source"
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class EvidenceItem(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:8]}")
    source_title: str
    url: str
    final_url: str | None = None
    jurisdiction: str = "unknown"
    document_type: DocumentType = DocumentType.UNKNOWN
    basis_role: BasisRole = BasisRole.UNKNOWN
    basis_strength: BasisStrength = BasisStrength.WEAK
    discovered_by: str
    fetched_by: str
    parsed_by: str
    passage: str
    pinpoint_citation: str | None = None
    supports_claim: str = ""
    limitations: list[str] = Field(default_factory=list)


class CoverageReport(BaseModel):
    attempted_tools: list[str] = Field(default_factory=list)
    successful_fetches: int = 0
    failed_fetches: int = 0
    unreadable_sources: int = 0
    unresolved_gaps: list[str] = Field(default_factory=list)
    fallback_reasons: list[str] = Field(default_factory=list)
    current_basis_summary: str = "No evidence gathered yet."


class QueryClassification(BaseModel):
    normalized_query: str
    jurisdiction_target: JurisdictionTarget = JurisdictionTarget.UNKNOWN
    jurisdictions: list[str] = Field(default_factory=list)
    query_type: QueryType = QueryType.UNKNOWN
    requested_outputs: list[OutputFormat] = Field(default_factory=lambda: [OutputFormat.CHAT])
    requested_document_constraints: dict[str, Any] = Field(default_factory=dict)
    detected_statutes: list[str] = Field(default_factory=list)
    detected_sections: list[str] = Field(default_factory=list)
    detected_cases: list[str] = Field(default_factory=list)
    detected_citations: list[str] = Field(default_factory=list)
    unsupported_actions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ReActActionType(str, Enum):
    KENYALAW_JUDGMENT_SEARCH = "kenyalaw_judgment_search"
    KENYALAW_LEGISLATION_SEARCH = "kenyalaw_legislation_search"
    OFFICIAL_KENYA_DOMAIN_SEARCH = "official_kenya_domain_search"
    BRAVE_SEARCH_FALLBACK = "brave_search_fallback"
    KENYA_LAW_CASE_RESOLVE = "kenya_law_case_resolve"
    CASE_SPECIFIC_SEARCH = "case_specific_search"
    FETCH_URL = "fetch_url"
    SYNTHESIZE_ANSWER = "synthesize_answer"
    STOP_WITH_GAPS = "stop_with_gaps"


class ReActAction(BaseModel):
    action: ReActActionType
    query: str | None = None
    url: str | None = None
    reason: str = ""
    target_title: str | None = None
    expected_document_type: DocumentType = DocumentType.UNKNOWN


class ResearchState(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = "default_session"

    original_user_query: str
    normalized_query: str
    classification: QueryClassification | None = None

    jurisdiction_target: JurisdictionTarget = JurisdictionTarget.UNKNOWN
    query_type: QueryType = QueryType.UNKNOWN
    requested_outputs: list[OutputFormat] = Field(default_factory=lambda: [OutputFormat.CHAT])

    search_round: int = 0
    max_rounds: int = 5

    source_candidates: list[SourceCandidate] = Field(default_factory=list)
    evidence_ledger: list[EvidenceItem] = Field(default_factory=list)
    coverage_report: CoverageReport = Field(default_factory=CoverageReport)

    final_answer_draft: str | None = None
    unsupported_action_warnings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def has_strong_or_moderate_basis(self) -> bool:
        return any(
            ev.basis_strength in {BasisStrength.STRONG, BasisStrength.MODERATE}
            for ev in self.evidence_ledger
        )

    def has_any_readable_evidence(self) -> bool:
        return any(ev.passage.strip() for ev in self.evidence_ledger)
