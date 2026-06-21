from __future__ import annotations

from urllib.parse import urlparse

from app.schemas.research_state import BasisRole, BasisStrength, DocumentType


def hostname_matches(hostname: str, allowed: set[str]) -> bool:
    host = hostname.lower().strip(".")
    for domain in allowed:
        domain = domain.lower().strip(".")
        if host == domain or host.endswith("." + domain):
            return True
    return False


KENYA_PRIMARY_DOMAINS = {
    "kenyalaw.org",
    "new.kenyalaw.org",
    "judiciary.go.ke",
}

KENYA_OFFICIAL_DOMAINS = {
    "parliament.go.ke",
    "klrc.go.ke",
    "ag.go.ke",
    "kenya.go.ke",
    "gazette.go.ke",
}

FOREIGN_PRIMARY_DOMAINS = {
    "legislation.gov.uk",
    "supremecourt.uk",
    "bailii.org",
    "saflii.org",
    "commonlii.org",
    "canlii.org",
    "austlii.edu.au",
    "law.cornell.edu",
}


class SourceBasisEvaluator:
    def evaluate_authority(
        self,
        url: str,
        raw_content: str,
        doc_type: DocumentType,
        jurisdiction_hint: str = "unknown",
    ) -> tuple[BasisRole, BasisStrength, list[str]]:
        limitations: list[str] = []

        if not url:
            return BasisRole.UNKNOWN, BasisStrength.WEAK, ["No source URL available."]

        if not raw_content or not raw_content.strip():
            return BasisRole.CONTEXT_ONLY, BasisStrength.UNREADABLE, ["Source was discovered but not readable."]

        parsed = urlparse(url)
        host = parsed.netloc.lower()
        text = raw_content[:5000].lower()

        kenya_primary = hostname_matches(host, KENYA_PRIMARY_DOMAINS)
        kenya_official = hostname_matches(host, KENYA_OFFICIAL_DOMAINS) or host.endswith(".go.ke")
        foreign_primary = hostname_matches(host, FOREIGN_PRIMARY_DOMAINS)

        judgment_signature = any(sig in text for sig in [
            "republic of kenya",
            "judgment",
            "ruling",
            "court of appeal",
            "supreme court",
            "high court",
            "employment and labour relations court",
        ])

        statute_signature = any(sig in text for sig in [
            "it is hereby enacted",
            "laws of kenya",
            "act no.",
            "section ",
            "arrangement of sections",
        ])

        gazette_signature = "gazette" in host or "kenya gazette" in text

        if doc_type == DocumentType.STATUTE or statute_signature:
            if kenya_primary or kenya_official or foreign_primary:
                return BasisRole.PRIMARY_LEGISLATION, BasisStrength.STRONG, limitations

        if doc_type == DocumentType.JUDGMENT or judgment_signature:
            if kenya_primary or foreign_primary or hostname_matches(host, {"judiciary.go.ke"}):
                return BasisRole.PRIMARY_CASE_LAW, BasisStrength.STRONG, limitations

        if gazette_signature and (kenya_official or kenya_primary):
            return BasisRole.OFFICIAL_SECONDARY, BasisStrength.MODERATE, limitations

        if kenya_official:
            limitations.append("Official domain, but content was not clearly primary law.")
            return BasisRole.OFFICIAL_SECONDARY, BasisStrength.MODERATE, limitations

        if foreign_primary:
            limitations.append("Recognized legal repository; verify jurisdiction relevance.")
            if judgment_signature:
                return BasisRole.PRIMARY_CASE_LAW, BasisStrength.STRONG, limitations
            if statute_signature:
                return BasisRole.PRIMARY_LEGISLATION, BasisStrength.STRONG, limitations
            return BasisRole.OFFICIAL_SECONDARY, BasisStrength.MODERATE, limitations

        if any(sig in host for sig in ["law", "legal", "advocates", "firm"]) or "analysis" in text:
            return BasisRole.PERSUASIVE, BasisStrength.PERSUASIVE, ["Commentary/background source, not primary authority."]

        return BasisRole.BACKGROUND, BasisStrength.WEAK, ["Source authority could not be verified."]
