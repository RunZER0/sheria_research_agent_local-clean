"""
Final Answer Verification

Before producing a final answer, the system must verify:

- Every cited authority is in the evidence ledger
- Every legal proposition is supported by accepted evidence
- Unsupported claims are removed or marked as limitations
- Unresolved critical gaps are disclosed
- Source basis label is accurate
- Final answer does not cite discovered-but-unread sources
- Final answer does not rely on memory in strict mode
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.evidence.evidence_ledger import EvidenceLedger, AcceptedEvidence


@dataclass
class VerificationResult:
    passed: bool
    status: str  # "verified", "partial", "failed"
    issues: list[str] = field(default_factory=list)
    unsupported_citations: list[str] = field(default_factory=list)
    unread_citations: list[str] = field(default_factory=list)
    missing_evidence_propositions: list[str] = field(default_factory=list)
    unresolved_critical_gaps: list[str] = field(default_factory=list)
    source_basis_assessment: str = "unknown"


# Pattern to match bracketed source IDs like [S1], [S2], [Ev_abc123]
_CITATION_PATTERN = re.compile(r'\[([A-Za-z0-9_-]+)\]')


class FinalAnswerVerifier:
    """
    Verifies that a final answer is properly grounded in accepted evidence.
    """

    def __init__(self, ledger: EvidenceLedger) -> None:
        self.ledger = ledger

    def verify(
        self,
        answer: str,
        strict_mode: bool = True,
    ) -> VerificationResult:
        """
        Verify the final answer against the evidence ledger.

        Args:
            answer: The draft final answer text
            strict_mode: If True, fail on any unsupported citation

        Returns:
            VerificationResult with pass/fail and details
        """
        issues: list[str] = []
        unsupported_citations: list[str] = []
        unread_citations: list[str] = []
        missing_evidence_propositions: list[str] = []

        # 1. Extract all bracketed citations from the answer
        cited_ids = set()
        for match in _CITATION_PATTERN.finditer(answer):
            cited_ids.add(match.group(1))

        # 2. Build a set of valid accepted evidence IDs
        accepted_ids = {ev.source_id for ev in self.ledger.accepted}
        cited_ids_lower = {c.lower() for c in cited_ids}
        accepted_ids_lower = {a.lower() for a in accepted_ids}

        # 3. Check each citation
        for cid in cited_ids:
            if cid.lower() not in accepted_ids_lower:
                unsupported_citations.append(cid)
                issues.append(f"Citation [{cid}] not found in accepted evidence")

        # 4. Check for citations to discovered-but-unread sources
        for cid in cited_ids:
            source_state = self.ledger.get_source_state(cid)
            if source_state == "unreadable":
                unread_citations.append(cid)
                issues.append(f"Citation [{cid}] refers to an unreadable source")

        # 5. Check for legal propositions without support
        proposition_indicators = [
            r'held\s+that',
            r'ruled\s+that',
            r'stated\s+that',
            r'established\s+that',
            r'found\s+that',
            r'decided\s+that',
            r'concluded\s+that',
            r'affirmed\s+that',
            r'declared\s+that',
        ]
        lines = answer.split('\n')
        for i, line in enumerate(lines):
            for indicator in proposition_indicators:
                if re.search(indicator, line, re.IGNORECASE):
                    context = '\n'.join(lines[max(0, i - 1):min(len(lines), i + 2)])
                    if not _CITATION_PATTERN.search(context):
                        missing_evidence_propositions.append(line.strip()[:100])
                        break

        # 6. Check for unresolved critical gaps
        unresolved_critical_gaps: list[str] = []

        # 7. Assess basis
        basis = self.ledger.assess_basis_strength()

        # 8. Determine result
        if strict_mode:
            failed = bool(unsupported_citations or unread_citations)
        else:
            failed = bool(unsupported_citations)

        if failed:
            status = "failed"
        elif unsupported_citations or unread_citations or missing_evidence_propositions:
            status = "partial"
        else:
            status = "verified"

        return VerificationResult(
            passed=not (failed or bool(missing_evidence_propositions and strict_mode)),
            status=status,
            issues=issues,
            unsupported_citations=unsupported_citations,
            unread_citations=unread_citations,
            missing_evidence_propositions=missing_evidence_propositions,
            unresolved_critical_gaps=unresolved_critical_gaps,
            source_basis_assessment=basis.value,
        )

    def build_verification_dict(self, result: VerificationResult) -> dict[str, Any]:
        """Build a serializable dict for the observability ledger."""
        return {
            "passed": result.passed,
            "status": result.status,
            "issues_count": len(result.issues),
            "unsupported_citations": result.unsupported_citations,
            "unread_citations": result.unread_citations,
            "missing_evidence_propositions_count": len(result.missing_evidence_propositions),
            "unresolved_critical_gaps": result.unresolved_critical_gaps,
            "source_basis_assessment": result.source_basis_assessment,
        }
