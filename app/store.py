from __future__ import annotations

from datetime import datetime, timezone

from supabase import Client, create_client

from app.config import Settings


class SupabaseStore:
    """Production data interface for Supabase-backed research persistence.

    Requires SUPABASE_URL and SUPABASE_ANON_KEY in .env.
    Optionally uses SUPABASE_SERVICE_ROLE_KEY for admin operations.
    Raises ``ValueError`` at construction when credentials are missing or invalid.
    """

    def __init__(self, settings: Settings) -> None:
        url = settings.supabase_url
        anon_key = settings.supabase_anon_key

        if not url or not anon_key:
            raise ValueError(
                "Supabase Connection Exception: Credentials missing. "
                "Ensure SUPABASE_URL and SUPABASE_ANON_KEY are set inside your .env file."
            )
        try:
            self.client: Client = create_client(url, anon_key)
        except Exception as exc:
            raise ValueError(
                f"Supabase Connection Exception: Failed to initialize client — {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def ensure_session(self, session_id: str) -> None:
        """Maintains an idempotent tracking index of the parent user session identifier."""
        self.client.table("research_sessions").upsert({
            "session_id": session_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

    # ------------------------------------------------------------------
    # Chat message history (used by DraftingNode)
    # ------------------------------------------------------------------

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Appends a chat message to the conversation history."""
        self.client.table("research_messages").insert({
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

    def recent_messages(self, session_id: str, limit: int = 10) -> list[dict[str, str]]:
        """Returns the most recent messages for a session, oldest-first."""
        response = (
            self.client.table("research_messages")
            .select("role, content")
            .eq("session_id", session_id)
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        rows = response.data or []
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    # ------------------------------------------------------------------
    # Research state persistence
    # ------------------------------------------------------------------

    def persist_execution_run(self, state_data: dict) -> None:
        """Stores or syncs the core ReAct execution states."""
        self.ensure_session(state_data.get("session_id", "default"))

        payload = {
            "run_id": state_data.get("run_id"),
            "session_id": state_data.get("session_id"),
            "query": state_data.get("query"),
            "query_intent": state_data.get("query_intent", "standard"),
            "deep_research_mode": state_data.get("deep_research_mode", False),
            "basis_strength": state_data.get("basis_strength", "unknown"),
            "final_answer": state_data.get("final_answer", ""),
        }
        self.client.table("research_states").upsert(payload).execute()

    # ------------------------------------------------------------------
    # Evidence ledger persistence
    # ------------------------------------------------------------------

    def persist_evidence_ledger(self, run_id: str, cards: list[dict]) -> None:
        """Atomically synchronizes the verified evidence grounding set."""
        # Clear existing entries under the specific run to maintain transaction state cleanliness
        self.client.table("evidence_ledger").delete().eq("run_id", run_id).execute()

        if not cards:
            return

        payloads = [
            {
                "run_id": run_id,
                "source_title": card.get("title", "Unknown Authority Link"),
                "source_url": card.get("url", ""),
                "authority_level": card.get("authority_level", "unknown"),
                "excerpt": card.get("excerpt", ""),
            }
            for card in cards
        ]
        self.client.table("evidence_ledger").insert(payloads).execute()

    # ------------------------------------------------------------------
    # Artifact manifest persistence
    # ------------------------------------------------------------------

    def persist_artifact_manifest(self, run_id: str, manifest: dict) -> None:
        """Logs the outcomes derived from the Document Export Controller pipeline iterations."""
        payload = {
            "artifact_id": manifest.get("artifact_id"),
            "run_id": run_id,
            "verification_passed": manifest.get("verification_passed", False),
            "generated_outputs": manifest.get("generated_outputs", []),
            "warning": manifest.get("warning", ""),
        }
        self.client.table("artifact_manifests").upsert(payload).execute()

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """Return recent sessions (session_id, updated_at) ordered by updated_at desc."""
        response = self.client.table("research_sessions").select("session_id, updated_at").order("updated_at", desc=True).limit(limit).execute()
        return response.data or []

    # ------------------------------------------------------------------
    # Workspace Sync Pipeline (Local-to-Cloud Markdown)
    # ------------------------------------------------------------------

    def ensure_user_quota(self, user_id: str, tier: str = "free") -> dict:
        """Ensure a quota row exists for this user. Returns { user_id, tier, max_bytes, used_bytes }."""
        result = self.client.rpc("ensure_user_quota", {"p_user_id": user_id, "p_tier": tier}).execute()
        return result.data[0] if result.data else {}

    def compute_workspace_diff(self, user_id: str, local_manifest: list[dict]) -> dict:
        """Compare local file manifest against server. Returns { files_to_add, files_to_update, files_to_delete }."""
        result = self.client.rpc("compute_workspace_diff", {
            "p_user_id": user_id,
            "p_local_manifest": local_manifest,
        }).execute()
        return result.data if result.data else {"files_to_add": [], "files_to_update": [], "files_to_delete": []}

    def check_and_reserve_quota(self, user_id: str, new_bytes: int) -> bool:
        """Atomically check and reserve quota. Returns True if allowed."""
        result = self.client.rpc("check_and_reserve_quota", {
            "p_user_id": user_id,
            "p_new_bytes": new_bytes,
        }).execute()
        return bool(result.data) if result.data else False

    def release_quota(self, user_id: str, bytes_to_release: int) -> None:
        """Release quota bytes (e.g. when a file is deleted)."""
        self.client.rpc("release_quota", {
            "p_user_id": user_id,
            "p_bytes": bytes_to_release,
        }).execute()

    def upsert_workspace_file(self, user_id: str, file_path: str, file_name: str,
                              file_size: int, file_hash: str, last_modified: int,
                              markdown_size: int) -> None:
        """Insert or update a workspace manifest entry."""
        self.client.table("workspace_manifest").upsert({
            "user_id": user_id,
            "file_path": file_path,
            "file_name": file_name,
            "file_size": file_size,
            "file_hash": file_hash,
            "last_modified": last_modified,
            "markdown_size": markdown_size,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

    def delete_workspace_file(self, user_id: str, file_path: str) -> None:
        """Delete a workspace file's manifest and chunks. Returns markdown_size for quota release."""
        # Get markdown_size before deleting
        response = self.client.table("workspace_manifest") \
            .select("markdown_size") \
            .eq("user_id", user_id) \
            .eq("file_path", file_path) \
            .execute()
        md_size = response.data[0]["markdown_size"] if response.data else 0

        # Delete chunks (cascade would be cleaner but table has no FK)
        self.client.table("workspace_chunks") \
            .delete() \
            .eq("user_id", user_id) \
            .eq("file_path", file_path) \
            .execute()

        # Delete manifest
        self.client.table("workspace_manifest") \
            .delete() \
            .eq("user_id", user_id) \
            .eq("file_path", file_path) \
            .execute()

        # Release quota
        if md_size > 0:
            self.release_quota(user_id, md_size)

    def insert_workspace_chunks(self, user_id: str, file_path: str, chunks: list[str]) -> int:
        """Insert markdown chunks for a file. Returns total char count."""
        payloads = [
            {
                "user_id": user_id,
                "file_path": file_path,
                "chunk_index": i,
                "chunk_text": text,
                "char_count": len(text),
            }
            for i, text in enumerate(chunks)
        ]
        total_chars = sum(len(t) for t in chunks)
        if payloads:
            self.client.table("workspace_chunks").insert(payloads).execute()
        return total_chars

    def get_workspace_chunks(self, user_id: str, file_path: str) -> list[str]:
        """Retrieve all markdown chunks for a file, ordered by chunk_index."""
        response = self.client.table("workspace_chunks") \
            .select("chunk_text") \
            .eq("user_id", user_id) \
            .eq("file_path", file_path) \
            .order("chunk_index", desc=False) \
            .execute()
        return [row["chunk_text"] for row in (response.data or [])]

    def list_workspace_files(self, user_id: str) -> list[dict]:
        """List all synced workspace files for a user."""
        response = self.client.table("workspace_manifest") \
            .select("file_path, file_name, file_size, markdown_size, synced_at") \
            .eq("user_id", user_id) \
            .order("file_path", desc=False) \
            .execute()
        return response.data or []

    def get_user_quota(self, user_id: str) -> dict:
        """Get user quota info."""
        response = self.client.table("user_quotas") \
            .select("tier, max_bytes, used_bytes") \
            .eq("user_id", user_id) \
            .execute()
        return response.data[0] if response.data else {"tier": "free", "max_bytes": 52428800, "used_bytes": 0}
