from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

SourceType = Literal[
    "official_primary",
    "official_secondary",
    "regulator",
    "court_or_judiciary",
    "legal_database",
    "law_firm_commentary",
    "news_or_blog",
    "unknown",
]

AuthorityLevel = Literal[
    "primary",
    "official",
    "persuasive",
    "background",
    "unknown",
]


class SourceQuality(BaseModel):
    score: float = 0.0
    label: str = "Unknown"
    source_type: SourceType = "unknown"
    authority_level: AuthorityLevel = "unknown"
    trust_reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    should_use: bool = False


def evaluate_source(
    url: str,
    title: str = "",
    snippet: str = "",
    kenya_legal_mode: bool = True,
) -> SourceQuality:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")
    path = parsed.path.lower()
    title_l = title.lower()
    snippet_l = snippet.lower()

    score = 0.0
    trust_reasons: list[str] = []
    risk_flags: list[str] = []
    source_type: SourceType = "unknown"
    authority_level: AuthorityLevel = "unknown"

    if "new.kenyalaw.org" in domain or domain == "kenyalaw.org" or domain.endswith(".kenyalaw.org"):
        score += 100
        source_type = "legal_database"
        authority_level = "primary"
        trust_reasons.append("Kenya Law domain; preferred legal source for Kenyan law research.")

        if any(token in path for token in ["/judgments", "/legislation", "/gazettes"]):
            score += 20
            source_type = "official_primary"
            trust_reasons.append("Appears to be case law, legislation, or Gazette material.")

    elif domain.endswith(".go.ke"):
        score += 80
        source_type = "official_secondary"
        authority_level = "official"
        trust_reasons.append("Official Kenyan government domain.")

        if "judiciary" in domain or "court.go.ke" in domain or "causelist.court.go.ke" in domain:
            score += 15
            source_type = "court_or_judiciary"
            authority_level = "official"
            trust_reasons.append("Judiciary or court domain.")

        if any(reg in domain for reg in ["kra", "odpc", "ppra", "centralbank", "parliament", "statelaw"]):
            score += 10
            source_type = "regulator"
            authority_level = "official"
            trust_reasons.append("Official regulator, Parliament, or State Law Office source.")

    elif any(marker in domain for marker in ["oraro", "bowmanslaw", "ikm", "tripleoklaw", "cliffedekkerhofmeyr", "lawafrica"]):
        score += 35
        source_type = "law_firm_commentary"
        authority_level = "background"
        trust_reasons.append("Legal commentary source; useful for background, not final authority.")
        risk_flags.append("Not a primary legal authority.")

    elif any(marker in domain for marker in ["nation.africa", "standardmedia", "the-star.co.ke", "businessdailyafrica"]):
        score += 20
        source_type = "news_or_blog"
        authority_level = "background"
        trust_reasons.append("News source; may help with context.")
        risk_flags.append("Not legal authority; verify against primary sources.")

    else:
        score += 5
        risk_flags.append("Unknown source quality.")
        risk_flags.append("Not preferred for strict Kenyan legal research.")

    if any(word in title_l or word in snippet_l for word in ["judgment", "petition", "civil appeal", "criminal appeal", "employment and labour", "gazette", "act no.", "section"]):
        score += 8
        trust_reasons.append("Search result text contains legal-source indicators.")

    if kenya_legal_mode and authority_level in {"unknown", "background"}:
        should_use = score >= 45
    else:
        should_use = score >= 20

    if kenya_legal_mode and source_type in {"news_or_blog", "unknown"}:
        should_use = False

    label = _label_for_score(score, should_use, source_type)

    return SourceQuality(
        score=round(score, 2),
        label=label,
        source_type=source_type,
        authority_level=authority_level,
        trust_reasons=trust_reasons,
        risk_flags=risk_flags,
        should_use=should_use,
    )


def _label_for_score(score: float, should_use: bool, source_type: str) -> str:
    if not should_use:
        return "Rejected / weak source"
    if score >= 100:
        return "High trust primary source"
    if score >= 80:
        return "High trust official source"
    if score >= 45:
        return "Usable background source"
    return "Low trust source"
