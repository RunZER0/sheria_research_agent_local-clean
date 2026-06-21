"""
Internal Observability Ledger

Records every operational fact during a Sheria research run.
This is the internal truth layer — NOT shown to the normal human user.
Available only in developer/audit mode.

A run without observability records is invalid.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class LoopTurnRecord:
    """Record of one complete ReAct loop turn."""
    turn_number: int
    goal_state: str
    current_objective: str
    active_narrative_node: str
    selected_action: str
    tool_or_skill_called: str | None
    exact_tool_input: dict[str, Any] | None
    raw_tool_status: str | None
    normalized_observation: str
    error_details: str | None
    source_ids_affected: list[str]
    evidence_decisions: list[dict[str, Any]]
    unresolved_gaps_before: list[str]
    unresolved_gaps_after: list[str]
    stop_decision: str | None
    timestamp: str


@dataclass
class ReadAttemptRecord:
    """Record of one attempt to read a source document."""
    source_id: str
    url: str
    method: str
    status: str
    chars_extracted: int
    error_message: str | None
    timestamp: str


@dataclass
class EvidenceDecisionRecord:
    """Record of accepting or rejecting a source as evidence."""
    source_id: str
    title: str
    url: str
    decision: str  # "accepted" or "rejected"
    reason: str
    relevance_issue: str | None
    read_status: str | None
    timestamp: str


@dataclass
class RunTrace:
    """
    Complete trace of one Sheria research run.

    This is the single source of operational truth.
    """
    run_id: str
    session_id: str
    user_query: str
    formulated_goal: str
    mode: str  # "standard" or "extended"
    loop_budget: int
    started_at: str

    loop_turns: list[LoopTurnRecord] = field(default_factory=list)
    read_attempts: list[ReadAttemptRecord] = field(default_factory=list)
    evidence_decisions: list[EvidenceDecisionRecord] = field(default_factory=list)

    current_objective: str = ""
    active_narrative_node: str = ""
    unresolved_gaps: list[str] = field(default_factory=list)

    stop_decision: str | None = None
    verification_result: dict[str, Any] | None = None

    completion_summary: str | None = None
    final_answer_preview: str | None = None

    def add_loop_turn(self, record: LoopTurnRecord) -> None:
        self.loop_turns.append(record)

    def add_read_attempt(self, record: ReadAttemptRecord) -> None:
        self.read_attempts.append(record)

    def add_evidence_decision(self, record: EvidenceDecisionRecord) -> None:
        self.evidence_decisions.append(record)

    @property
    def turn_count(self) -> int:
        return len(self.loop_turns)

    @property
    def total_read_attempts(self) -> int:
        return len(self.read_attempts)

    @property
    def total_evidence_accepted(self) -> int:
        return sum(1 for d in self.evidence_decisions if d.decision == "accepted")

    @property
    def total_evidence_rejected(self) -> int:
        return sum(1 for d in self.evidence_decisions if d.decision == "rejected")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "user_query": self.user_query,
            "formulated_goal": self.formulated_goal,
            "mode": self.mode,
            "loop_budget": self.loop_budget,
            "started_at": self.started_at,
            "turn_count": self.turn_count,
            "total_read_attempts": self.total_read_attempts,
            "total_evidence_accepted": self.total_evidence_accepted,
            "total_evidence_rejected": self.total_evidence_rejected,
            "stop_decision": self.stop_decision,
            "verification_result": self.verification_result,
            "completion_summary": self.completion_summary,
            "loop_turns": [t.__dict__ for t in self.loop_turns],
            "read_attempts": [r.__dict__ for r in self.read_attempts],
            "evidence_decisions": [d.__dict__ for d in self.evidence_decisions],
        }


class ObservabilityLedger:
    """
    Manages the internal observability ledger for a research run.

    Usage:
        ledger = ObservabilityLedger()
        trace = ledger.create_run("session_123", "user query", "standard")
        # ... during run:
        trace.add_loop_turn(turn_record)
        trace.add_read_attempt(read_record)
        # ... at end:
        ledger.complete_run("Enough material found.")
    """

    def __init__(self) -> None:
        self._trace: RunTrace | None = None

    def create_run(
        self,
        session_id: str,
        user_query: str,
        formulated_goal: str,
        mode: str = "standard",
    ) -> RunTrace:
        budget = 25 if mode == "extended" else 15
        self._trace = RunTrace(
            run_id=str(uuid.uuid4()),
            session_id=session_id,
            user_query=user_query,
            formulated_goal=formulated_goal,
            mode=mode,
            loop_budget=budget,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        return self._trace

    @property
    def trace(self) -> RunTrace | None:
        return self._trace

    def record_turn(
        self,
        turn_number: int,
        goal_state: str,
        current_objective: str,
        active_narrative_node: str,
        selected_action: str,
        tool_or_skill_called: str | None = None,
        exact_tool_input: dict[str, Any] | None = None,
        raw_tool_status: str | None = None,
        normalized_observation: str = "",
        error_details: str | None = None,
        source_ids_affected: list[str] | None = None,
        evidence_decisions: list[dict[str, Any]] | None = None,
        unresolved_gaps_before: list[str] | None = None,
        unresolved_gaps_after: list[str] | None = None,
        stop_decision: str | None = None,
    ) -> LoopTurnRecord:
        if self._trace is None:
            raise RuntimeError("No active run trace. Call create_run first.")

        record = LoopTurnRecord(
            turn_number=turn_number,
            goal_state=goal_state,
            current_objective=current_objective,
            active_narrative_node=active_narrative_node,
            selected_action=selected_action,
            tool_or_skill_called=tool_or_skill_called,
            exact_tool_input=exact_tool_input,
            raw_tool_status=raw_tool_status,
            normalized_observation=normalized_observation,
            error_details=error_details,
            source_ids_affected=source_ids_affected or [],
            evidence_decisions=evidence_decisions or [],
            unresolved_gaps_before=unresolved_gaps_before or [],
            unresolved_gaps_after=unresolved_gaps_after or [],
            stop_decision=stop_decision,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._trace.add_loop_turn(record)
        self._trace.unresolved_gaps = unresolved_gaps_after or []
        return record

    def record_read_attempt(
        self,
        source_id: str,
        url: str,
        method: str,
        status: str,
        chars_extracted: int = 0,
        error_message: str | None = None,
    ) -> ReadAttemptRecord:
        if self._trace is None:
            raise RuntimeError("No active run trace.")

        record = ReadAttemptRecord(
            source_id=source_id,
            url=url,
            method=method,
            status=status,
            chars_extracted=chars_extracted,
            error_message=error_message,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._trace.add_read_attempt(record)
        return record

    def record_evidence_decision(
        self,
        source_id: str,
        title: str,
        url: str,
        decision: str,
        reason: str,
        relevance_issue: str | None = None,
        read_status: str | None = None,
    ) -> EvidenceDecisionRecord:
        if self._trace is None:
            raise RuntimeError("No active run trace.")

        record = EvidenceDecisionRecord(
            source_id=source_id,
            title=title,
            url=url,
            decision=decision,
            reason=reason,
            relevance_issue=relevance_issue,
            read_status=read_status,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._trace.add_evidence_decision(record)
        return record

    def set_verification_result(self, result: dict[str, Any]) -> None:
        if self._trace is None:
            raise RuntimeError("No active run trace.")
        self._trace.verification_result = result

    def complete_run(self, summary: str) -> None:
        if self._trace is None:
            raise RuntimeError("No active run trace.")
        self._trace.completion_summary = summary
