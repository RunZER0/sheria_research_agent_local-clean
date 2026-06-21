from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

EventType = Literal[
    "run_started",
    "planning",
    "plan_ready",
    "searching",
    "source_found",
    "source_rejected",
    "source_selected",
    "reading_source",
    "source_read",
    "evidence_created",
    "answering",
    "answer_token",
    "verifying",
    "verification_result",
    "repairing",
    "answer_replaced",
    "run_finished",
    # Additional operational events for resilient runs
    "gap_opened",
    "gap_filled",
    "gap_unresolved",
    "source_provisionally_classified",
    "source_queued",
    "source_not_selected",
    "source_opened",
    "source_inspected",
    "source_unreadable",
    "evidence_candidate",
    "evidence_accepted",
    "evidence_rejected",
    "recovery_attempted",
    "followup_searching",
    "basis_assessed",
    "clarification_requested",
    "generating_case_summary",
    "case_summary_ready",
    # Document export pipeline events
    "document_work_state",
    "document_work_finished",
    "document_export_success",
    "document_export_finished",
    # New bounded ReAct architecture events
    "research_work_state",
    "error",
    # Workspace and UI integration events
    "context_gathering",
    "tab_open_request",
]

Visibility = Literal["public", "debug", "internal"]


class WorkEvent(BaseModel):
    run_id: str
    sequence: int
    type: EventType
    title: str
    summary: str
    state_summary: str = ""
    next_action: str = ""
    details: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    visibility: Visibility = "public"


class EventEmitter:
    def __init__(self, run_id: str | None = None, queue=None):
        self.run_id = run_id or str(uuid4())
        self.sequence = 0
        self.events: list[dict[str, Any]] = []
        self._queue = queue  # Optional asyncio.Queue for real-time SSE streaming

    def emit(
        self,
        event_type: EventType,
        title: str,
        summary: str,
        *,
        state_summary: str = "",
        next_action: str = "",
        details: str = "",
        payload: dict[str, Any] | None = None,
        visibility: Visibility = "public",
    ) -> dict[str, Any]:
        self.sequence += 1
        # default the public-friendly state_summary to the short summary when omitted
        if not state_summary:
            state_summary = summary

        event = WorkEvent(
            run_id=self.run_id,
            sequence=self.sequence,
            type=event_type,
            title=title,
            summary=summary,
            state_summary=state_summary,
            next_action=next_action,
            details=details,
            payload=payload or {},
            created_at=datetime.now(timezone.utc).isoformat(),
            visibility=visibility,
        )
        event_dict = event.model_dump()
        self.events.append(event_dict)
        # Push to real-time streaming queue if attached
        if self._queue is not None:
            try:
                self._queue.put_nowait(event_dict)
            except Exception:
                pass
        return event_dict
