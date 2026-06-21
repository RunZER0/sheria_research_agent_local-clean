from __future__ import annotations

from pydantic import BaseModel, Field
from app.schemas.research_state import JurisdictionTarget, ReActActionType


class ToolSpec(BaseModel):
    name: ReActActionType
    purpose: str
    when_to_use: list[str]
    priority: int = Field(ge=1)
    allowed_jurisdictions: list[JurisdictionTarget]


class LegalToolRegistry:
    def __init__(self) -> None:
        self.registry: dict[ReActActionType, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self.registry[spec.name] = spec

    def get_available_tools(self, jurisdiction: JurisdictionTarget) -> list[ToolSpec]:
        tools = []
        for spec in self.registry.values():
            if JurisdictionTarget.UNKNOWN in spec.allowed_jurisdictions:
                tools.append(spec)
                continue
            if jurisdiction in spec.allowed_jurisdictions:
                tools.append(spec)
                continue
            if jurisdiction == JurisdictionTarget.COMPARATIVE and (
                JurisdictionTarget.KENYA in spec.allowed_jurisdictions
                or JurisdictionTarget.FOREIGN in spec.allowed_jurisdictions
            ):
                tools.append(spec)
        return sorted(tools, key=lambda t: t.priority)


def initialize_legal_registry() -> LegalToolRegistry:
    reg = LegalToolRegistry()

    reg.register(ToolSpec(
        name=ReActActionType.KENYALAW_JUDGMENT_SEARCH,
        purpose="Search Kenya Law/new Kenya Law for Kenyan judgments and case law.",
        when_to_use=[
            "Kenyan case law",
            "Kenyan party names",
            "Kenyan citations",
            "comparative query with a Kenyan case-law component",
        ],
        priority=1,
        allowed_jurisdictions=[JurisdictionTarget.KENYA, JurisdictionTarget.COMPARATIVE],
    ))

    reg.register(ToolSpec(
        name=ReActActionType.KENYALAW_LEGISLATION_SEARCH,
        purpose="Search Kenya Law/new Kenya Law for Kenyan statutes, sections, chapters, and subsidiary legislation.",
        when_to_use=[
            "Kenyan statute",
            "Act of Parliament",
            "section or chapter number",
            "comparative query with a Kenyan statute component",
        ],
        priority=1,
        allowed_jurisdictions=[JurisdictionTarget.KENYA, JurisdictionTarget.COMPARATIVE],
    ))

    reg.register(ToolSpec(
        name=ReActActionType.OFFICIAL_KENYA_DOMAIN_SEARCH,
        purpose="Search official Kenyan legal/institutional domains when native Kenya Law search is insufficient.",
        when_to_use=[
            "Kenya Law returned nothing",
            "Kenya Law result is corrupted or unreadable",
            "official Kenyan regulator, tribunal, judiciary, gazette, parliament, or KLRC source needed",
        ],
        priority=2,
        allowed_jurisdictions=[JurisdictionTarget.KENYA, JurisdictionTarget.COMPARATIVE],
    ))

    reg.register(ToolSpec(
        name=ReActActionType.BRAVE_SEARCH_FALLBACK,
        purpose="Wider web discovery for foreign law, comparative law, general legal theory, or fallback when native/official routes fail.",
        when_to_use=[
            "non-Kenyan law",
            "comparative law",
            "general legal theory",
            "fallback after native search fails",
        ],
        priority=3,
        allowed_jurisdictions=[
            JurisdictionTarget.KENYA,
            JurisdictionTarget.FOREIGN,
            JurisdictionTarget.COMPARATIVE,
            JurisdictionTarget.GENERAL,
            JurisdictionTarget.UNKNOWN,
        ],
    ))

    reg.register(ToolSpec(
        name=ReActActionType.KENYA_LAW_CASE_RESOLVE,
        purpose="Try to resolve a specific Kenyan case by reconstructing its AKN URL from citation metadata (court, year, number, date) and fetch it directly. Use when the user provides a neutral citation like [2026] KESC 45 (KLR).",
        when_to_use=[
            "neutral citation provided",
            "court code like KESC, KECA, KEHC, KEELRC provided",
            "year and judgment number available",
            "exact case requested with citation metadata",
        ],
        priority=1,
        allowed_jurisdictions=[JurisdictionTarget.KENYA, JurisdictionTarget.COMPARATIVE],
    ))

    reg.register(ToolSpec(
        name=ReActActionType.CASE_SPECIFIC_SEARCH,
        purpose="Run structured searches for a specific case using party name fragments, case numbers, and legal issue terms. More precise than a generic Kenya Law search.",
        when_to_use=[
            "party names known but no citation",
            "fuzzy case request where user remembers only part of the case name",
            "case number or petition number known",
            "exact or fuzzy case request",
        ],
        priority=1,
        allowed_jurisdictions=[JurisdictionTarget.KENYA, JurisdictionTarget.COMPARATIVE],
    ))

    return reg
