"""
Research Controller (Refactored)

Thin executor that coordinates the ReAct agent runtime with the web layer.

The controller:
1. Initializes the runtime and subsystems
2. Delegates all decisions to the ReActAgentRuntime
3. Forwards events from the runtime to the HTTP response stream
4. Handles clean shutdown and error boundaries
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from app.config import Settings
from app.deepseek_client import DeepSeekClient
from app.events import EventEmitter
from app.schemas import ChatRequest
from app.tools.tool_executor import ToolExecutor
from app.agents import ReActAgentRuntime
from app.agents.workspace_subagent import WorkspaceIndex, SubagentPool


async def _emit_safe(emitter, event_type: str, title: str, message: str, **kwargs) -> None:
    if emitter is None:
        return
    result = emitter.emit(event_type, title, message, **kwargs)
    if inspect.isawaitable(result):
        await result


class ResearchController:
    """
    Orchestrates the ReAct agent loop.

    This is a THIN EXECUTOR. It does not make legal strategy decisions.
    It initializes subsystems and delegates all decisions to the agent runtime.
    """

    def __init__(self, settings: Settings, store=None, workspace_watcher=None) -> None:
        self.settings = settings
        self.store = store
        self._llm: DeepSeekClient | None = None
        self._tool_executor: ToolExecutor | None = None

    def _ensure_llm(self) -> DeepSeekClient:
        if self._llm is None:
            self._llm = DeepSeekClient(self.settings)
        return self._llm

    def _ensure_tool_executor(self) -> ToolExecutor:
        if self._tool_executor is None:
            self._tool_executor = ToolExecutor(self.settings)
        return self._tool_executor

    async def run(self, request: ChatRequest) -> AsyncIterator[dict[str, Any]]:
        """
        Execute a complete research run.

        Yields event dicts consumed by the frontend.
        """
        # 1. Initialize
        llm = self._ensure_llm()
        tool_executor = self._ensure_tool_executor()
        emitter = EventEmitter()

        # Determine mode
        mode = "extended" if request.deep_research else "standard"

        # 2. Emit run start
        yield emitter.emit(
            "run_started",
            "Research Started",
            f"Starting {'deep ' if request.deep_research else ''}research run.",
            state_summary=f"I'm starting a new research session to answer your legal question.",
            payload={
                "session_id": request.session_id,
                "mode": mode,
                "loop_budget": 25 if mode == "extended" else 15,
            },
        )

        # 3. Initialize workspace subagent pool (for file-aware operations)
        workspace_index = WorkspaceIndex(tree=request.workspace_tree or [])
        workspace_pool = SubagentPool(index=workspace_index, llm_client=llm)

        # Load workspace content from Supabase into the index
        if self.store and request.session_id:
            try:
                supabase_files = self.store.list_workspace_files(request.session_id)
                staleness_threshold_hours = 1
                stale_count = 0
                for sf in supabase_files:
                    fp = sf.get("file_path", "")
                    fn = sf.get("file_name", "")
                    # Check staleness
                    synced_at = sf.get("synced_at")
                    if synced_at:
                        try:
                            sync_dt = datetime.fromisoformat(synced_at.replace("Z", "+00:00"))
                            age_hours = (datetime.now(timezone.utc) - sync_dt).total_seconds() / 3600
                            if age_hours > staleness_threshold_hours:
                                stale_count += 1
                        except Exception:
                            pass
                    if fp and not workspace_pool.index.get_file(fp):
                        chunks = self.store.get_workspace_chunks(request.session_id, fp)
                        full_text = "\n\n".join(chunks)
                        entry = {
                            "name": fn,
                            "path": fp,
                            "kind": "text",
                            "content": full_text,
                        }
                        workspace_pool.index.build_index([entry])

                if stale_count > 0:
                    yield emitter.emit(
                        "research_work_state",
                        "Workspace Stale",
                        f"Note: {stale_count} of your workspace file(s) haven't been synced in over {staleness_threshold_hours} hour(s). "
                        f"I'm working with the last synced version. Use the Sync button for the latest.",
                        state_summary=f"Workspace: {stale_count} file(s) stale (last sync >{staleness_threshold_hours}h ago)",
                    )
            except Exception:
                pass

        runtime = ReActAgentRuntime(
            settings=self.settings,
            llm=llm,
            emitter=emitter,
            tool_executor=tool_executor,
            workspace_subagent=workspace_pool,
        )

        # 4. Run the ReAct loop
        user_query = request.normalized_query
        try:
            async for event in runtime.run(
                user_query=user_query,
                session_id=request.session_id,
                mode=mode,
                workspace_tree=request.workspace_tree,
            ):
                yield event

                # If the runtime emitted a final answer, persist it
                if event.get("type") == "answer_token":
                    token = event.get("payload", {}).get("token", "")
                    if token and self.store:
                        try:
                            self.store.add_message(request.session_id, "assistant", token)
                        except Exception:
                            pass
        except Exception as e:
            # Handle runtime errors gracefully
            yield emitter.emit(
                "error",
                "Research Error",
                f"An error occurred during research: {e}",
                payload={"error": str(e)},
            )
            yield emitter.emit(
                "answer_token",
                "Error",
                f"I encountered an error: {e}",
                payload={"token": f"I'm sorry, but I encountered an error while researching your question: {e}"},
            )

        # 5. Emit run finished (safety net in case runtime didn't emit it)
        yield emitter.emit(
            "run_finished",
            "Done",
            "Research complete.",
        )
