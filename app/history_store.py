"""
Supabase-backed execution history persistence.

Writes every ReAct loop turn to the `agent_execution_history` table so the
agent's short-term memory survives process crashes and stateless HTTP requests.

Reads back the recent history as a fallback when the in-memory
ObservabilityLedger trace is empty (e.g., after a cold restart).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.observability import LoopTurnRecord


class SupabaseExecutionHistoryStore:
    """Persists ReAct loop-turn records to Supabase and reads them back.

    The schema maps one-to-one with ``LoopTurnRecord``:

        agent_execution_history(
            id                  BIGSERIAL PRIMARY KEY,
            session_id          TEXT NOT NULL,
            turn_number         INT NOT NULL,
            selected_action     VARCHAR(100) NOT NULL,
            exact_tool_input    JSONB,
            raw_tool_status     VARCHAR(100),
            normalized_observation TEXT,
            error_details       TEXT,
            source_ids_affected TEXT[],
            created_at          TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (session_id, turn_number)
        )
    """

    def __init__(self, settings: Settings) -> None:
        from supabase import Client, create_client

        url = settings.supabase_url
        key = settings.supabase_anon_key

        if not url or not key:
            raise ValueError(
                "Supabase Execution History Store: credentials missing. "
                "Set SUPABASE_URL and SUPABASE_ANON_KEY in .env"
            )

        self._client: Client = create_client(url, key)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def record_turn(
        self,
        session_id: str,
        turn_number: int,
        selected_action: str,
        exact_tool_input: dict[str, Any] | None = None,
        raw_tool_status: str | None = None,
        normalized_observation: str | None = None,
        error_details: str | None = None,
        source_ids_affected: list[str] | None = None,
    ) -> None:
        """Insert a single turn record into Supabase.

        This is called by ``_execute_tool`` immediately after the in-memory
        observability ledger has been updated.
        """
        payload = {
            "session_id": session_id,
            "turn_number": turn_number,
            "selected_action": selected_action,
            "exact_tool_input": json.dumps(exact_tool_input) if exact_tool_input else None,
            "raw_tool_status": raw_tool_status,
            "normalized_observation": normalized_observation,
            "error_details": error_details,
            "source_ids_affected": source_ids_affected or [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._client.table("agent_execution_history").upsert(
            payload,
            on_conflict=["session_id", "turn_number"],
        ).execute()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_recent_history(
        self,
        session_id: str,
        before_turn: int | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return the last N turn records for a session, oldest-first.

        Arguments:
            session_id: The session to query.
            before_turn: If set, only return turns with ``turn_number < before_turn``.
            limit: Maximum number of records to return (default 5).
        """
        query = (
            self._client.table("agent_execution_history")
            .select(
                "turn_number, selected_action, exact_tool_input, "
                "raw_tool_status, normalized_observation, error_details, "
                "source_ids_affected"
            )
            .eq("session_id", session_id)
            .order("turn_number", desc=True)
            .limit(limit)
        )

        if before_turn is not None:
            query = query.lt("turn_number", before_turn)

        response = query.execute()
        records = response.data or []
        # Reverse so caller gets oldest-first
        records.reverse()
        return records

    def format_records_for_prompt(self, records: list[dict[str, Any]]) -> str:
        """Format a list of history records into the ``Recent Execution History`` prompt block.

        This produces the same shape as ``_build_recent_history`` so the
        LLM sees a consistent format regardless of whether the data came
        from memory or Supabase.
        """
        if not records:
            return ""

        lines: list[str] = []
        for rec in records:
            action = rec.get("selected_action", "(none)")
            inp_raw = rec.get("exact_tool_input")
            inp: dict[str, Any] = {}
            if isinstance(inp_raw, str):
                try:
                    inp = json.loads(inp_raw)
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(inp_raw, dict):
                inp = inp_raw

            inp_str = "; ".join(f"{k}={v}" for k, v in inp.items())
            if len(inp_str) > 200:
                inp_str = inp_str[:200] + "..."

            status = rec.get("raw_tool_status") or "(no status)"
            obs = rec.get("normalized_observation") or "(no observation)"
            err = rec.get("error_details") or ""
            src_ids = rec.get("source_ids_affected") or []
            src_str = f", sources affected: {src_ids}" if src_ids else ""

            lines.append(f"  Turn {rec.get('turn_number', '?')}: {action}")
            if inp_str:
                lines.append(f"    Input: {inp_str}")
            lines.append(f"    Status: {status}")
            lines.append(f"    Observation: {obs[:300]}")
            if err:
                lines.append(f"    Error: {err[:200]}")
            if src_str:
                lines.append(f"    {src_str}")

        return "\n".join(lines)
