from __future__ import annotations
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
import uuid

from .schemas import Source, EvidenceCard


class ResearchBudget(BaseModel):
    max_search_rounds: int = 3
    max_queries_per_round: int = 5
    max_sources_to_inspect: int = 10
    max_recovery_attempts_per_source: int = 3
    max_total_evidence_cards: int = 8

    def configure_for_intent(self, intent: str, deep_research: bool = False):
        """Dynamically scales the agentic budget boundaries based on intent and research depth."""
        if intent == "light":
            self.max_search_rounds = 1
            self.max_queries_per_round = 2
            self.max_sources_to_inspect = 3
            self.max_total_evidence_cards = 4
        elif deep_research:
            # Grant the agent extended runtime limits to navigate complex paths
            self.max_search_rounds = 8
            self.max_queries_per_round = 8
            self.max_sources_to_inspect = 15
            self.max_total_evidence_cards = 20


class ResearchGap(BaseModel):
    gap_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str
    priority: str = "important"  # critical, important, optional
    status: str = "open"  # open, filled, unresolved
    related_queries: List[str] = Field(default_factory=list)
    related_source_ids: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class ClarificationState(BaseModel):
    requires_input: bool = False
    questions: List[str] = Field(default_factory=list)
    provided_answers: Dict[str, str] = Field(default_factory=dict)


class ResearchState(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = "default"
    query: str = ""
    kenya_legal_mode: bool = True
    strict_mode: bool = True
    deep_research_mode: bool = False
    query_intent: str = "standard"  # light, standard, detailed

    budget: ResearchBudget = ResearchBudget()
    clarification: ClarificationState = ClarificationState()

    search_round: int = 0
    current_queries: List[str] = Field(default_factory=list)
    gaps: List[ResearchGap] = Field(default_factory=list)
    sources: List[Source] = Field(default_factory=list)
    selected_sources: List[Source] = Field(default_factory=list)
    evidence_cards: List[EvidenceCard] = Field(default_factory=list)
    basis_strength: str = "unknown"
    grounding_status: str = "unknown"
    recovery_notes: List[str] = Field(default_factory=list)
    final_answer: str = ""

    def open_gap(self, description: str, priority: str = "important", related_queries: list | None = None) -> ResearchGap:
        gap = ResearchGap(description=description, priority=priority)
        if related_queries:
            gap.related_queries = related_queries
        self.gaps.append(gap)
        return gap

    def fill_gap(self, gap_id: str, note: str | None = None) -> bool:
        for g in self.gaps:
            if g.gap_id == gap_id:
                g.status = "filled"
                if note:
                    g.notes.append(note)
                return True
        return False
