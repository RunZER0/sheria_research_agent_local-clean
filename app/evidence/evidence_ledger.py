"""
Evidence Ledger

Tracks source state transitions through the evidence pipeline.

Source states:
    discovered → opened → readable/unreadable → relevance_screened → accepted/rejected → cited

Search results are NOT evidence. A source cannot support the final answer until it has been:
1. discovered
2. opened
3. read
4. screened for relevance
5. accepted into the evidence ledger
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SourceState(str, Enum):
    DISCOVERED = "discovered"
    OPENED = "opened"
    READABLE = "readable"
    UNREADABLE = "unreadable"
    RELEVANCE_SCREENED = "relevance_screened"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CITED = "cited"


class AuthorityLevel(str, Enum):
    PRIMARY = "primary"          # Primary legislation, binding case law
    OFFICIAL = "official"        # Official government, regulator, tribunal
    PERSUASIVE = "persuasive"    # Foreign judgments, academic commentary
    BACKGROUND = "background"    # News, reports, general information
    UNVERIFIED = "unverified"    # Unable to verify authority
    UNKNOWN = "unknown"


class BasisStrength(str, Enum):
    STRONG = "strong_basis"
    MODERATE = "moderate_basis"
    LIMITED = "limited_basis"
    WEAK = "weak_or_unverified_basis"
    INSUFFICIENT = "insufficient_basis"


@dataclass
class AcceptedEvidence:
    """An evidence item that has been accepted into the ledger."""
    source_id: str
    title: str
    url: str
    jurisdiction: str
    source_type: str
    authority_level: AuthorityLevel
    basis_role: str
    relevant_issue: str
    supporting_excerpt: str
    read_status: str
    accepted_reason: str
    citation: str | None = None
    limitations: list[str] = field(default_factory=list)
    accepted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class RejectedSource:
    """A source that was rejected from the evidence ledger with reasons."""
    source_id: str
    title: str
    url: str
    rejected_reason: str
    relevance_issue: str | None = None
    read_status: str | None = None
    rejected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class DiscoveredCandidate:
    """A source that was discovered but not yet opened/read."""
    source_id: str
    title: str
    url: str
    snippet: str
    discovered_by: str  # Which tool discovered this
    jurisdiction_hint: str = "unknown"
    document_type_hint: str = "unknown"
    score: float = 0.0


class EvidenceLedger:
    """
    Evidence ledger managing source state transitions.

    Sources move through: discovered → opened → readable/unreadable → accepted/rejected → cited

    The ledger enforces that:
    - Search results are not evidence
    - A source must be read before acceptance
    - Accepted evidence must have supporting excerpts
    - Rejected sources have documented reasons
    """

    def __init__(self) -> None:
        # State buckets
        self._candidates: dict[str, DiscoveredCandidate] = {}
        self._opened: set[str] = set()
        self._readable: dict[str, dict[str, Any]] = {}
        self._unreadable: dict[str, dict[str, Any]] = {}
        self._accepted: dict[str, AcceptedEvidence] = {}
        self._rejected: dict[str, RejectedSource] = {}
        self._cited: set[str] = set()

    # ---- Source Registration ----

    def register_candidate(
        self,
        source_id: str,
        title: str,
        url: str,
        snippet: str = "",
        discovered_by: str = "unknown",
        jurisdiction_hint: str = "unknown",
        document_type_hint: str = "unknown",
        score: float = 0.0,
    ) -> DiscoveredCandidate:
        candidate = DiscoveredCandidate(
            source_id=source_id,
            title=title,
            url=url,
            snippet=snippet,
            discovered_by=discovered_by,
            jurisdiction_hint=jurisdiction_hint,
            document_type_hint=document_type_hint,
            score=score,
        )
        self._candidates[source_id] = candidate
        return candidate

    def register_candidates(self, candidates: list[DiscoveredCandidate]) -> None:
        for c in candidates:
            self._candidates[c.source_id] = c

    # ---- State Transitions ----

    def mark_opened(self, source_id: str) -> bool:
        """Mark a candidate as opened."""
        if source_id in self._candidates:
            self._opened.add(source_id)
            return True
        return False

    def mark_readable(self, source_id: str, read_result: dict[str, Any]) -> bool:
        """Mark a source as readable with read metadata."""
        if source_id not in self._candidates:
            return False
        self._opened.add(source_id)
        self._readable[source_id] = read_result
        if source_id in self._unreadable:
            del self._unreadable[source_id]
        return True

    def mark_unreadable(self, source_id: str, reason: str, read_attempt: dict[str, Any] | None = None) -> bool:
        """Mark a source as unreadable."""
        if source_id not in self._candidates:
            return False
        self._opened.add(source_id)
        self._unreadable[source_id] = {
            "reason": reason,
            "read_attempt": read_attempt or {},
        }
        return True

    def accept(
        self,
        source_id: str,
        title: str,
        url: str,
        jurisdiction: str,
        source_type: str,
        authority_level: AuthorityLevel,
        basis_role: str,
        relevant_issue: str,
        supporting_excerpt: str,
        read_status: str,
        accepted_reason: str,
        citation: str | None = None,
        limitations: list[str] | None = None,
    ) -> AcceptedEvidence | None:
        """Accept a source as evidence. Source must be readable and not previously rejected."""
        if source_id not in self._candidates:
            return None
        if source_id not in self._readable:
            return None
        if source_id in self._rejected:
            return None

        evidence = AcceptedEvidence(
            source_id=source_id,
            title=title,
            url=url,
            jurisdiction=jurisdiction,
            source_type=source_type,
            authority_level=authority_level,
            basis_role=basis_role,
            relevant_issue=relevant_issue,
            supporting_excerpt=supporting_excerpt,
            read_status=read_status,
            accepted_reason=accepted_reason,
            citation=citation,
            limitations=limitations or [],
        )
        self._accepted[source_id] = evidence
        return evidence

    def reject(
        self,
        source_id: str,
        title: str,
        url: str,
        rejected_reason: str,
        relevance_issue: str | None = None,
        read_status: str | None = None,
    ) -> RejectedSource | None:
        """Reject a source from evidence."""
        if source_id not in self._candidates:
            return None

        rejected = RejectedSource(
            source_id=source_id,
            title=title,
            url=url,
            rejected_reason=rejected_reason,
            relevance_issue=relevance_issue,
            read_status=read_status,
        )
        self._rejected[source_id] = rejected
        if source_id in self._accepted:
            del self._accepted[source_id]
        return rejected

    def mark_cited(self, source_id: str) -> bool:
        """Mark an accepted source as cited in the final answer."""
        if source_id in self._accepted:
            self._cited.add(source_id)
            return True
        return False

    # ---- Query Methods ----

    @property
    def candidates(self) -> list[DiscoveredCandidate]:
        return list(self._candidates.values())

    @property
    def openable_candidates(self) -> list[DiscoveredCandidate]:
        """Candidates that have not yet been opened."""
        return [c for c in self._candidates.values() if c.source_id not in self._opened]

    @property
    def readable_sources(self) -> list[dict[str, Any]]:
        result = []
        for sid, read_data in self._readable.items():
            candidate = self._candidates.get(sid)
            result.append({
                "source_id": sid,
                "title": candidate.title if candidate else "Unknown",
                "url": candidate.url if candidate else "",
                "read_data": read_data,
            })
        return result

    @property
    def unreadable_sources(self) -> list[dict[str, Any]]:
        result = []
        for sid, data in self._unreadable.items():
            candidate = self._candidates.get(sid)
            result.append({
                "source_id": sid,
                "title": candidate.title if candidate else "Unknown",
                "url": candidate.url if candidate else "",
                "reason": data.get("reason", "Unknown"),
            })
        return result

    @property
    def accepted(self) -> list[AcceptedEvidence]:
        return list(self._accepted.values())

    @property
    def rejected(self) -> list[RejectedSource]:
        return list(self._rejected.values())

    @property
    def cited(self) -> list[AcceptedEvidence]:
        return [ev for sid, ev in self._accepted.items() if sid in self._cited]

    def get_source_state(self, source_id: str) -> str | None:
        """Get the current state of a source by ID."""
        if source_id in self._cited:
            return SourceState.CITED.value
        if source_id in self._accepted:
            return SourceState.ACCEPTED.value
        if source_id in self._rejected:
            return SourceState.REJECTED.value
        if source_id in self._readable:
            return SourceState.READABLE.value
        if source_id in self._unreadable:
            return SourceState.UNREADABLE.value
        if source_id in self._opened:
            return SourceState.OPENED.value
        if source_id in self._candidates:
            return SourceState.DISCOVERED.value
        return None

    # ---- Basis Assessment ----

    def assess_basis_strength(self) -> BasisStrength:
        """
        Assess the overall basis strength from accepted evidence.

        Rules:
        - strong_basis: 2+ primary sources, no critical gaps
        - moderate_basis: 1+ primary OR 1+ official sources
        - limited_basis: only persuasive/background sources
        - weak_or_unverified_basis: unreadable or unverified sources only
        - insufficient_basis: no accepted evidence
        """
        if not self._accepted:
            return BasisStrength.INSUFFICIENT

        primary_count = sum(
            1 for ev in self._accepted.values()
            if ev.authority_level == AuthorityLevel.PRIMARY
        )
        official_count = sum(
            1 for ev in self._accepted.values()
            if ev.authority_level == AuthorityLevel.OFFICIAL
        )
        persuasive_count = sum(
            1 for ev in self._accepted.values()
            if ev.authority_level == AuthorityLevel.PERSUASIVE
        )

        if primary_count >= 2:
            return BasisStrength.STRONG
        if primary_count >= 1 or official_count >= 1:
            return BasisStrength.MODERATE
        if persuasive_count >= 1:
            return BasisStrength.LIMITED
        return BasisStrength.WEAK

    def counts_by_authority(self) -> dict[str, int]:
        """Return counts of accepted evidence by authority level."""
        counts: dict[str, int] = {}
        for ev in self._accepted.values():
            level = ev.authority_level.value
            counts[level] = counts.get(level, 0) + 1
        return counts

    # ---- Serialization ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_count": len(self._candidates),
            "opened_count": len(self._opened),
            "readable_count": len(self._readable),
            "unreadable_count": len(self._unreadable),
            "accepted_count": len(self._accepted),
            "rejected_count": len(self._rejected),
            "cited_count": len(self._cited),
            "accepted": [
                {
                    "source_id": ev.source_id,
                    "title": ev.title,
                    "url": ev.url,
                    "jurisdiction": ev.jurisdiction,
                    "authority_level": ev.authority_level.value,
                    "basis_role": ev.basis_role,
                    "relevant_issue": ev.relevant_issue,
                }
                for ev in self._accepted.values()
            ],
            "rejected": [
                {
                    "source_id": r.source_id,
                    "title": r.title,
                    "reason": r.rejected_reason,
                }
                for r in self._rejected.values()
            ],
            "basis_strength": self.assess_basis_strength().value,
        }
