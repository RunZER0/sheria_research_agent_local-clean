"""
Maps internal Sheria backend events to the frontend SSE event contract.

Each function takes a raw event dict (as emitted by EventEmitter or the
react_v2 controller) and returns either a frontend-compatible event dict
or None (if the event should be filtered out).

Keep this adapter shallow. Do not move research logic here.
"""

from __future__ import annotations

from typing import Any

# Narrative counter — incremented each time a research_work_state becomes a narrative
_narrative_index = 0


def _next_narrative_id() -> int:
    global _narrative_index
    _narrative_index += 1
    return _narrative_index


def to_frontend_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a raw Sheria backend event to a frontend SSE event.

    Returns None for events that have no frontend representation
    (e.g. internal debug events).
    """
    event_type = event.get("type")
    payload = event.get("payload") or {}
    summary = event.get("summary") or ""
    state_summary = event.get("state_summary") or ""
    text = event.get("text") or payload.get("text") or ""
    token = payload.get("token") or event.get("token") or ""
    message = event.get("message") or event.get("summary") or ""
    title = event.get("title") or ""
    sequence = event.get("sequence")

    # --- helper to check if text contains debug artifacts ---
    def _has_debug_artifact(text_val: str) -> bool:
        return "Detected unknown" in text_val

    # Special-case streaming answer tokens: the frontend expects
    # { type: 'answer_token', payload: { token: '...' } }
    # Allow answer tokens to pass even if flagged internal (they are user-facing)
    if event_type == "answer_token":
        token_val = token or text or message
        if _has_debug_artifact(token_val):
            return None
        return {"type": "answer_token", "payload": {"token": token_val}}

    # Filter out LLM debug output artifacts from other event types
    event_texts = [text, summary, state_summary, message]
    if any(_has_debug_artifact(t) for t in event_texts if t):
        return None

    # Map research work state updates to narrative nodes (high-level intent updates)
    if event_type == "research_work_state":
        narrative_text = state_summary or summary or message or text or payload.get("text", "")
        return {"type": "narrative", "id": _next_narrative_id(), "text": narrative_text}

    # If the event is internal-only, drop it
    if event.get("visibility") == "internal":
        return None

    # --- source_found / source_selected → work_event ---
    if event_type in ("source_found", "source_selected"):
        source_title = payload.get("title") or event.get("title") or "Source"
        return {
            "type": "work_event",
            "text": f"Discovered: {source_title}",
            "status": "success",
            "icon": "source",
        }

    # --- source_rejected → coverage_warning ---
    if event_type == "source_rejected":
        round_num = payload.get("round", event.get("round", 1))
        reason = payload.get("rejection_reason") or "Source rejected."
        return {
            "type": "coverage_warning",
            "text": reason,
        }

    # --- reading_source → micro_step info ---
    if event_type == "reading_source":
        return {
            "type": "work_event",
            "status": "info",
            "text": state_summary or summary or f"Reading: {title}",
        }

    # --- evidence_created → micro_step success ---
    if event_type == "evidence_created":
        round_num = payload.get("round", event.get("round", 1))
        return {
            "type": "work_event",
            "status": "success",
            "text": state_summary or summary or "Evidence extracted.",
        }

    # --- source_unreadable → coverage_warning ---
    if event_type == "source_unreadable":
        round_num = payload.get("round", event.get("round", 1))
        return {
            "type": "coverage_warning",
            "text": state_summary or summary or "Source could not be read.",
        }

    # --- gap_opened → coverage_warning ---
    if event_type == "gap_opened":
        round_num = payload.get("round", event.get("round", 1))
        desc = payload.get("description") or summary or "Investigative gap opened."
        return {
            "type": "coverage_warning",
            "text": f"Gap: {desc}",
        }

    # --- gap_filled / gap_unresolved → micro_step ---
    if event_type == "gap_filled":
        round_num = payload.get("round", event.get("round", 1))
        return {
            "type": "work_event",
            "status": "success",
            "text": state_summary or summary or "Investigative gap resolved.",
        }

    if event_type == "gap_unresolved":
        round_num = payload.get("round", event.get("round", 1))
        desc = payload.get("description") or summary or "Investigative gap unresolved."
        return {
            "type": "coverage_warning",
            "text": f"Unresolved gap: {desc}",
        }

    # --- basis_assessed → narrative_summary ---
    if event_type == "basis_assessed":
        strength = payload.get("basis_strength", "unknown").replace("_", " ")
        return {
            "type": "narrative_summary",
            "lines": [
                f"I have evaluated the evidence and assessed the basis strength as {strength.upper()}.",
            ],
        }

    # --- verification_result → micro_step ---
    if event_type == "verification_result":
        passed = payload.get("passed")
        status_val = payload.get("status", "unknown")
        if passed is True:
            return {
                "type": "work_event",
                "round": payload.get("round", event.get("round", 1)),
                "status": "success",
                "text": f"Verification passed: {status_val}",
            }
        return {
            "type": "coverage_warning",
            "round": payload.get("round", event.get("round", 1)),
            "text": f"Verification {status_val}: {summary or 'Check flagged.'}",
        }

    # --- coverage_warning passthrough ---
    # Treat coverage warnings as work events (warning status) so they appear
    # inside the work panels as evidence of active validation/obstacles.
    if event_type == "coverage_warning":
        return {
            "type": "work_event",
            "status": "warning",
            "round": payload.get("round", event.get("round", 1)),
            "text": message or text or "Source coverage warning.",
        }

    # --- followup_searching / searching → micro_step ---
    if event_type in ("followup_searching", "searching"):
        round_num = payload.get("round", event.get("round", 1))
        return {
            "type": "work_event",
            "status": "info",
            "text": state_summary or summary or message or "Searching...",
        }

    # --- plan_ready → micro_step ---
    if event_type == "plan_ready":
        return {
            "type": "work_event",
            "status": "success",
            "text": "Research plan ready.",
        }

    # --- answering → micro_step ---
    if event_type == "answering":
        round_num = payload.get("round", event.get("round", 1))
        return {
            "type": "work_event",
            "status": "info",
            "text": state_summary or summary or "Synthesizing answer...",
        }

    # --- verifying / repairing → micro_step ---
    if event_type == "verifying":
        round_num = payload.get("round", event.get("round", 1))
        return {
            "type": "work_event",
            "status": "info",
            "text": state_summary or summary or "Verifying evidence...",
        }

    if event_type == "repairing":
        round_num = payload.get("round", event.get("round", 1))
        return {
            "type": "work_event",
            "status": "warning",
            "text": state_summary or summary or "Repairing source fetch...",
        }

    # --- source_warning passthrough ---
    if event_type == "source_warning":
        return {
            "type": "source_warning",
            "text": message or text or "Source warning.",
        }

    # --- narrative_summary passthrough ---
    if event_type == "narrative_summary":
        return {
            "type": "narrative_summary",
            "lines": payload.get("lines", event.get("lines", [])),
        }

    # --- document_work_state → micro_step ---
    if event_type == "document_work_state":
        return {
            "type": "work_event",
            "round": payload.get("round", event.get("round", 99)),
            "status": "info",
            "text": state_summary or summary or "Document work in progress.",
        }

    # --- document_work_finished → micro_step success ---
    if event_type == "document_work_finished":
        return {
            "type": "work_event",
            "round": payload.get("round", event.get("round", 99)),
            "status": "success",
            "text": state_summary or summary or "Document work complete.",
        }

    # --- document_export_finished → passthrough with full download payload ---
    # Handles both:
    #   react_v2 path (old): artifact_id + generated_files
    #   new path:           document_id + docx_path/pdf_path
    if event_type == "document_export_finished":
        docx_path = payload.get("docx_path") or event.get("docx_path")
        pdf_path = payload.get("pdf_path") or event.get("pdf_path")
        manifest_path = payload.get("manifest_path") or event.get("artifact_manifest_path")
        document_id = payload.get("document_id") or event.get("document_id")

        # Handle react_v2 old path: extract from generated_files
        generated_files = payload.get("generated_files", event.get("generated_files", []))
        if not docx_path and generated_files:
            for f in generated_files:
                if f.endswith(".docx"):
                    docx_path = f
                elif f.endswith(".pdf"):
                    pdf_path = f
        if not document_id and payload.get("artifact_id"):
            document_id = payload["artifact_id"]
        if not document_id and event.get("artifact_id"):
            document_id = event["artifact_id"]

        result = {
            "type": "document_export_finished",
            "document_id": document_id or "",
            "ok": payload.get("ok", event.get("ok", True)),
            "docx_path": docx_path,
            "pdf_path": pdf_path,
            "manifest_path": manifest_path,
            "warnings": payload.get("warnings", event.get("warnings", [])),
        }
        # Attach constraint_report if present
        constraint_report = payload.get("constraint_report") or event.get("constraint_report")
        if constraint_report:
            result["constraint_report"] = constraint_report
        return result

    # --- run_finished passthrough ---
    if event_type == "run_finished":
        return {
            "type": "run_finished",
        }

    # --- error passthrough ---
    if event_type == "error":
        # Prefer the actual exception message from the payload if available
        error_msg = (payload.get("message")
                     or message
                     or summary
                     or "Research failed.")
        return {
            "type": "error",
            "text": error_msg,
        }

    # By default, preserve the original event type and pass through the
    # common fields the frontend UI expects (state_summary, summary, payload).
    return {
        "type": event_type,
        "title": title,
        "summary": summary,
        "state_summary": state_summary,
        "payload": payload or {},
        "sequence": sequence,
    }
