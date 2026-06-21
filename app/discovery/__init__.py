from __future__ import annotations

import re
from datetime import date
from typing import Optional


class ParsedCaseQuery:
    raw_query: str
    case_name: str | None = None
    party_fragments: list[str]
    applicant_party: str | None = None
    respondent_party: str | None = None
    citation_year: int | None = None
    neutral_citation: str | None = None
    court_code: str | None = None
    klr_number: int | None = None
    case_number: str | None = None
    decision_date: date | None = None
    legal_issue_terms: list[str]

    def __init__(self, query: str):
        self.raw_query = query
        self.party_fragments = []
        self.legal_issue_terms = []
        self._parse()

    def _parse(self):
        q = self.raw_query.strip()
        # Extract neutral citation like [2026] KESC 45 (KLR)
        citation_match = re.search(r'\[(\d{4})\]\s*([A-Z]+)\s+(\d+)\s*\(KLR\)', q)
        if citation_match:
            self.citation_year = int(citation_match.group(1))
            self.court_code = citation_match.group(2)
            self.klr_number = int(citation_match.group(3))
            self.neutral_citation = citation_match.group(0)

        # Extract case number like Petition E041 of 2025 or Civil Appeal 123 of 2024
        case_num_match = re.search(r'([A-Z][a-zA-Z\s]+?)\s+(E?\d+\s+of\s+\d{4})', q)
        if case_num_match:
            self.case_number = case_num_match.group(2).strip()

        # Split on "v " or " v. " to get parties
        party_split = re.split(r'\s+v(?:\.|\s)\s*', q, maxsplit=1)
        if len(party_split) > 1:
            self.applicant_party = party_split[0].strip().rstrip(",")
            # Extract respondent up to the first citation marker or end
            resp = party_split[1].strip()
            # Stop at citation or case number
            stop = re.search(r'(?:\[|\()', resp)
            if stop:
                resp = resp[:stop.start()].strip()
            self.respondent_party = resp
            self.party_fragments = [self.applicant_party] + ([self.respondent_party] if self.respondent_party else [])
        else:
            self.party_fragments = [q]

        # Extract legal issue terms (words indicating legal concepts)
        legal_markers = [
            "injunction", "temporary", "interlocutory", "compensation", "damages",
            "breach", "unfair", "termination", "dismissal", "remedy", "discrimination",
            "constitutional", "fundamental", "right", "fair hearing", "unconstitutional",
            "landmark", "jurisdiction", "appeal", "ruling", "judgment", "petition",
            "unfair dismissal", "redundancy", "reinstatement", "probate", "succession",
        ]
        lower_q = q.lower()
        for marker in legal_markers:
            if marker in lower_q:
                self.legal_issue_terms.append(marker)

    def query_type(self) -> str:
        """Classify: exact, fuzzy, topic, landmark"""
        q_lower = self.raw_query.lower()
        if self.neutral_citation and self.party_fragments:
            return "exact"
        if self.party_fragments and any(len(p) > 3 for p in self.party_fragments):
            return "fuzzy"
        if any(w in q_lower for w in ["landmark", "famous", "notable", "major", "significant", "example of"]):
            return "landmark"
        return "topic"

    def __repr__(self) -> str:
        return (
            f"ParsedCaseQuery(type={self.query_type()}, "
            f"applicant={self.applicant_party}, "
            f"respondent={self.respondent_party}, "
            f"citation={self.neutral_citation}, "
            f"court={self.court_code}, "
            f"year={self.citation_year}, "
            f"num={self.klr_number}, "
            f"case_num={self.case_number}, "
            f"issues={self.legal_issue_terms})"
        )


# --- Court code to AKN slug mapping ---
COURT_AKN_MAP = {
    "KESC": "kesc",
    "KECA": "keca",
    "KEHC": "kehc",
    "KEELRC": "keelrc",
    "KET": "ket",
    "KEKRC": "kekrc",
    "KEMC": "kemc",
}


def generate_akn_candidates(parsed: ParsedCaseQuery) -> list[str]:
    """Generate candidate AKN URLs from parsed case metadata."""
    base = "https://new.kenyalaw.org/akn/ke/judgment"
    candidates = []

    courts = [COURT_AKN_MAP.get(parsed.court_code, parsed.court_code.lower())] if parsed.court_code else []
    years = [parsed.citation_year] if parsed.citation_year else []
    numbers = [parsed.klr_number] if parsed.klr_number else []
    dates = [parsed.decision_date] if parsed.decision_date else []

    for court in courts:
        for year in years:
            for number in numbers:
                base_path = f"{base}/{court}/{year}/{number}"
                candidates.append(f"{base_path}/eng")
                for d in dates:
                    candidates.append(f"{base_path}/eng@{d.isoformat()}")
                    candidates.append(f"{base_path}/eng@{d.isoformat()}/source")
                    candidates.append(f"{base_path}/eng@{d.isoformat()}/source.pdf")
                # Also try without date
                candidates.append(f"{base_path}/eng/source")
                candidates.append(f"{base_path}/eng/source.pdf")

    return list(dict.fromkeys(candidates))  # deduplicate preserving order


def score_candidate(title: str, parsed: ParsedCaseQuery) -> int:
    """Score a candidate source against the parsed query."""
    score = 0
    title_lower = title.lower()

    # Party matching
    if parsed.applicant_party:
        parts = parsed.applicant_party.lower().split()
        match_count = sum(1 for p in parts if len(p) > 3 and p in title_lower)
        if match_count >= 2:
            score += 30
        elif match_count >= 1:
            score += 15

    if parsed.respondent_party:
        parts = parsed.respondent_party.lower().split()
        match_count = sum(1 for p in parts if len(p) > 3 and p in title_lower)
        if match_count >= 2:
            score += 30
        elif match_count >= 1:
            score += 15

    # Year matching
    if parsed.citation_year and str(parsed.citation_year) in title:
        score += 15

    # Court matching
    if parsed.court_code and parsed.court_code.lower() in title_lower:
        score += 15

    # KLR number matching
    if parsed.klr_number and str(parsed.klr_number) in title:
        score += 20

    # Case number matching
    if parsed.case_number:
        parts = parsed.case_number.lower().split()
        if any(p in title_lower for p in parts):
            score += 20

    # Legal issue terms
    if parsed.legal_issue_terms:
        for term in parsed.legal_issue_terms:
            if term in title_lower:
                score += 10

    # Penalties for wrong matches
    if parsed.citation_year and str(parsed.citation_year) not in title:
        score -= 10
    if parsed.party_fragments:
        # Check if title seems completely unrelated
        all_parts = " ".join(parsed.party_fragments).lower().split()
        significant = [p for p in all_parts if len(p) > 4]
        if significant and not any(p in title_lower for p in significant):
            score -= 30

    return max(0, score)


def classify_score(score: int) -> str:
    if score >= 90:
        return "exact_match"
    if score >= 70:
        return "strong"
    if score >= 45:
        return "near_match"
    return "rejected"
