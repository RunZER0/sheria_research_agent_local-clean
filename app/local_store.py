from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

from .config import Settings


class LocalFileStore:
    """Lightweight local JSON-backed fallback store used when Supabase is not configured.

    This implements the minimal API used by the application so the frontend can
    persist sessions, messages, and execution artifacts locally during development.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        base = Path(__file__).resolve().parent.parent
        data_dir = base / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._path = data_dir / "local_store.json"
        if not self._path.exists():
            self._dump({
                "sessions": [],
                "messages": [],
                "research_states": [],
                "evidence_ledger": [],
                "artifact_manifests": [],
            })
        self._load()

    def _load(self) -> None:
        with self._path.open("r", encoding="utf8") as fh:
            try:
                self._data: Dict[str, Any] = json.load(fh)
            except Exception:
                self._data = {"sessions": [], "messages": [], "research_states": [], "evidence_ledger": [], "artifact_manifests": []}

    def _dump(self, data: Dict[str, Any]) -> None:
        with self._path.open("w", encoding="utf8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def _save(self) -> None:
        self._dump(self._data)

    # --- Session management ---
    def ensure_session(self, session_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        sessions = self._data.setdefault("sessions", [])
        for s in sessions:
            if s.get("session_id") == session_id:
                s["updated_at"] = now
                self._save()
                return
        sessions.append({"session_id": session_id, "updated_at": now})
        self._save()

    def list_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        sessions = list(self._data.get("sessions", []))
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return sessions[:limit]

    # --- Chat messages ---
    def add_message(self, session_id: str, role: str, content: str) -> None:
        msgs = self._data.setdefault("messages", [])
        msgs.append({
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        self.ensure_session(session_id)
        self._save()

    def recent_messages(self, session_id: str, limit: int = 10) -> List[Dict[str, str]]:
        msgs = [m for m in self._data.get("messages", []) if m.get("session_id") == session_id]
        msgs = sorted(msgs, key=lambda m: m.get("created_at", ""), reverse=True)[:limit]
        return [{"role": m["role"], "content": m["content"]} for m in reversed(msgs)]

    # --- Research run state persistence ---
    def persist_execution_run(self, state_data: Dict[str, Any]) -> None:
        runs = self._data.setdefault("research_states", [])
        run_id = state_data.get("run_id")
        existing = None
        for r in runs:
            if r.get("run_id") == run_id:
                existing = r
                break
        payload = {
            "run_id": run_id,
            "session_id": state_data.get("session_id"),
            "query": state_data.get("query"),
            "query_intent": state_data.get("query_intent", "standard"),
            "deep_research_mode": state_data.get("deep_research_mode", False),
            "basis_strength": state_data.get("basis_strength", "unknown"),
            "final_answer": state_data.get("final_answer", ""),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if existing:
            existing.update(payload)
        else:
            runs.append(payload)
        self.ensure_session(state_data.get("session_id", "default"))
        self._save()

    def persist_evidence_ledger(self, run_id: str, cards: List[Dict[str, Any]]) -> None:
        ledger = self._data.setdefault("evidence_ledger", [])
        # Remove existing entries for run
        ledger[:] = [c for c in ledger if c.get("run_id") != run_id]
        for card in cards:
            ledger.append({
                "run_id": run_id,
                "source_title": card.get("title"),
                "source_url": card.get("url"),
                "authority_level": card.get("authority_level"),
                "excerpt": card.get("excerpt"),
            })
        self._save()

    def persist_artifact_manifest(self, run_id: str, manifest: Dict[str, Any]) -> None:
        manifests = self._data.setdefault("artifact_manifests", [])
        existing = None
        for m in manifests:
            if m.get("run_id") == run_id:
                existing = m
                break
        payload = {
            "artifact_id": manifest.get("artifact_id"),
            "run_id": run_id,
            "verification_passed": manifest.get("verification_passed", False),
            "generated_outputs": manifest.get("generated_outputs", []),
            "warning": manifest.get("warning", ""),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if existing:
            existing.update(payload)
        else:
            manifests.append(payload)
        self._save()
