"""
ReAct Agent Runtime

Architecture:
    observe → narrate → decide → act → observe → update state → repeat → verify → answer

The LLM receives the raw user query, formulates a goal and first action in one call.
After each tool execution, the LLM observes the results and decides the next action.
Code enables: fetches bytes, parses documents, enforces budgets, records observability.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from .workspace_subagent import WorkspaceSubagent

from app.config import Settings
from app.deepseek_client import DeepSeekClient
from app.events import EventEmitter
from app.evidence.evidence_ledger import (
    EvidenceLedger,
    DiscoveredCandidate,
    AuthorityLevel,
    BasisStrength,
)
from app.observability import ObservabilityLedger, RunTrace
from app.tools.tool_executor import ToolExecutor, ToolObservation, ReadObservation
from app.verification import FinalAnswerVerifier, VerificationResult
from app.history_store import SupabaseExecutionHistoryStore


# ---------------------------------------------------------------------------
# Run State
# ---------------------------------------------------------------------------

@dataclass
class GoalState:
    formulated_goal: str = ""
    current_objective: str = ""
    mode: str = "standard"
    loop_budget: int = 15
    turn_count: int = 0
    unresolved_gaps: list[str] = field(default_factory=list)
    stop_decision: str | None = None

    def can_continue(self) -> bool:
        return self.turn_count < self.loop_budget and self.stop_decision is None


# ---------------------------------------------------------------------------
# JSON extractor
# ---------------------------------------------------------------------------

_JSON_RE = re.compile(r'\{.*\}', re.DOTALL)


def _safe_json_loads(text: str) -> dict[str, Any]:
    m = _JSON_RE.search(text or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# The ReAct agent prompt
# The LLM decides everything. Code enables.
# ---------------------------------------------------------------------------

_SHERIA_SYSTEM_PROMPT = """\
You are Sheria, a policy-led legal research agent. Your job is to answer the user's legal question using accepted evidence.

## How You Work
You operate in turns. Each turn you: observe what happened from the previous action, narrate what you saw and what you'll do next, then pick an action. Code executes your action and reports back.

On the FIRST turn: read the user's query, formulate your understanding as a goal, and decide the first action.

On subsequent turns: read the execution history to see what your last action produced, then decide what to do next.

## Available Tools
{tools_desc}

## Tool Guidance
- **kenya_law_research**: Use for ALL Kenyan primary legal sources (statutes, cases, judgments). This tool handles discovery, reading, and verification in one call. Pass the query naturally — the tool internally uses LLM-guided classification and Brave site-restricted search.
- **brave_search**: Use for general web discovery, foreign law, comparative law, or when Kenya Law doesn't return useful results.
- **general_web_fetch**: Use only when you have a specific URL outside Kenya Law.
- **official_kenya_domain_search**: Use for Kenyan government/regulator sources outside Kenya Law.

## Kenya Law Best Practices
These are the techniques the tools use internally. Understand them so you can craft effective parameters:

**Search query construction:**
- For statutes: use the exact short title plus site restriction, e.g., `"Employment Act" site:new.kenyalaw.org/akn/ke/act`
- For case law: use party names plus site restriction, e.g., `"Nyutu Agrovet" "Airtel Networks" site:new.kenyalaw.org/akn/ke/judgment`
- For neutral citations: `"[2024] KECA 523" site:new.kenyalaw.org`
- For issue-based searches: `(procedural fairness termination) site:new.kenyalaw.org/akn/ke/judgment`

**AKN URL handling:**
- Kenya Law AKN URLs may have `@YYYY-MM-DD` date suffixes. These are stripped automatically when fetching.
- The code normalizes: `/eng@2024-01-01` → `/eng`

**Fetch mechanism:**
- Documents are fetched via /source endpoint first (returns PDF or DOCX)
- If /source fails, /source.pdf is tried
- If both fail, HTML page text is extracted
- All three happen automatically — you just provide the URL

**When queries are vague:**
- If the user's query mentions a case or statute colloquially, do a general Brave search first to get the exact name.
- Once you have the exact name, pass it to kenya_law_research with the proper site-restricted format.

## Page Fetching Guidance
- **html_read_browser_fetch**: Use for ALL web page fetching. It uses a real browser (Playwright) that handles JavaScript, Cloudflare, and cookie banners. Crucially, it returns a **page narrative with clickable links** so you can see what links are available on each page and navigate to them. Use this when you need to browse a website.
- **general_web_fetch**: Use only as fallback when html_read_browser_fetch fails or when you need pure text without link extraction.
- **pdf_read** / **docx_read**: Use for specific PDF or DOCX URLs.
- If a page is behind Cloudflare or a captcha, the browser tool will detect the block and report it. Try a different URL or search for the content elsewhere.

## Evidence Policy
- Search results are candidates, NOT evidence.
- You must read a source before accepting it as evidence.
- You may NOT cite a case, statute, or report unless it is in the accepted evidence ledger.

## Decision Rules
- If you have enough material to answer the question, choose "synthesize_answer" and provide the final_answer.
- If you need more information, choose a specific tool.
- If no useful sources remain and you cannot answer, choose "stop" with a clear reason.
- If candidates are available but you haven't read them yet, read one before searching again.
- If you tried the same tool 2+ times and it kept returning irrelevant results, switch to a different tool.

## Output Format (return JSON only)
On the FIRST turn only, include a "goal" field explaining your understanding.
On EVERY turn, include:
{{
  "goal": "Your understanding of what the user needs (FIRST TURN ONLY, otherwise omit)",
  "observation": "What you observed from the previous action's results. Professional narrative, no technical details. Omit on first turn.",
  "action": "tool_name | accept_evidence | synthesize_answer | stop",
  "parameters": {{ "query": "...", "url": "...", "kind": "legislation|case_law|auto" }},
  "reason": "What you are going to do next and why. Professional narrative in first person.",
  "objective": "New working objective if changing direction, or empty string to keep current.",
  "final_answer": "Your complete final answer. Include only if action is synthesize_answer or stop.",
  "stop": false
}}
"""


# ---------------------------------------------------------------------------
# ReAct Agent Runtime
# ---------------------------------------------------------------------------

class ReActAgentRuntime:
    """
    The core ReAct agent runtime.

    observe → narrate → decide → act → observe → repeat
    """

    def __init__(
        self,
        settings: Settings,
        llm: DeepSeekClient,
        emitter: EventEmitter,
        tool_executor: ToolExecutor | None = None,
        history_store: SupabaseExecutionHistoryStore | None = None,
        workspace_subagent: WorkspaceSubagent | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.emitter = emitter
        self.history_store = history_store

        self.tool_executor = tool_executor or ToolExecutor(settings, llm=llm)
        self.evidence_ledger = EvidenceLedger()
        self.observability = ObservabilityLedger()
        self.verifier: FinalAnswerVerifier | None = None
        self.workspace_subagent = workspace_subagent

        self.goal = GoalState()
        self.gaps: list[str] = []
        self._source_counter: int = 0

    def _next_source_id(self) -> str:
        self._source_counter += 1
        return f"S{self._source_counter}"

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    async def run(
        self,
        user_query: str,
        session_id: str = "default",
        mode: str = "standard",
        workspace_tree: list[dict] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Execute a complete ReAct research run.

        Yields event dicts that the controller forwards to the frontend.
        When workspace_tree is provided, the agent uses it for workspace-aware context.  
        """
        # Initialize state
        self.goal = GoalState(
            mode=mode,
            loop_budget=25 if mode == "extended" else 15,
        )
        # Store workspace context for the ReAct loop
        self.workspace_tree = workspace_tree or []

        trace = self.observability.create_run(
            session_id=session_id,
            user_query=user_query,
            formulated_goal="",  # Will be filled after first decision
            mode=mode,
        )
        self.verifier = FinalAnswerVerifier(self.evidence_ledger)

        is_first_turn = True

        # Main ReAct loop
        while self.goal.can_continue():
            self.goal.turn_count += 1

            # --- LLM OBSERVES + DECIDES ---
            action_decision = await self._decide_next_action(
                user_query, trace, is_first_turn
            )

            # On first turn, extract the goal and emit it as narrative
            if is_first_turn:
                goal_text = action_decision.get("goal", "")
                if goal_text:
                    trace.formulated_goal = goal_text
                    self.goal.formulated_goal = goal_text
                    yield self.emitter.emit(
                        "research_work_state",
                        "Research Goal",
                        goal_text,
                        state_summary=goal_text,
                    )
                is_first_turn = False

            # Check for stop
            if action_decision.get("stop"):
                self.goal.stop_decision = action_decision.get("reason", "Goal satisfied.")
                self.observability.record_turn(
                    turn_number=self.goal.turn_count,
                    goal_state=self.goal.formulated_goal,
                    current_objective=self.goal.current_objective,
                    active_narrative_node="",
                    selected_action="stop",
                    stop_decision=self.goal.stop_decision,
                    unresolved_gaps_before=list(self.gaps),
                    unresolved_gaps_after=list(self.gaps),
                    normalized_observation=self.goal.stop_decision,
                )
                break

            # --- EMIT OBSERVATION NARRATIVE (what the LLM observed from last action) ---
            observation = action_decision.get("observation", "")
            if observation:
                yield self.emitter.emit(
                    "research_work_state",
                    "Research Update",
                    observation,
                )

            # --- EMIT ACTION NARRATIVE (what the LLM will do next) ---
            action_name = action_decision.get("action", "")
            params = action_decision.get("parameters", {})
            reason = action_decision.get("reason", "")
            if reason:
                yield self.emitter.emit(
                    "research_work_state",
                    "Research Step",
                    reason,
                )

            # Update objective if changed
            new_objective = action_decision.get("objective", "")
            if new_objective:
                self.goal.current_objective = new_objective

            # Exit if synthesizing answer
            if action_name == "synthesize_answer":
                self.goal.stop_decision = "Synthesizing final answer."
                final_answer = action_decision.get("final_answer", "")
                if final_answer:
                    # Store it directly
                    pass
                break

            # --- EXECUTE THE ACTION ---
            gaps_before = list(self.gaps)

            is_known_tool = action_name in self.tool_executor.list_tools() or action_name.startswith("workspace_")
            if is_known_tool:
                result = await self._execute_tool(action_name, params, trace)
                for event in result.get("events", []):
                    yield event
            elif action_name == "accept_evidence":
                # LLM explicitly accepts a source into the evidence ledger
                source_id = params.get("source_id", "")
                title = params.get("title", "Accepted source")
                url = params.get("url", "")
                excerpt = params.get("excerpt", "")[:2000]
                source_type = params.get("source_type", "legal_source")
                accepted = self.evidence_ledger.accept(
                    source_id=source_id,
                    title=title,
                    url=url,
                    jurisdiction=params.get("jurisdiction", self.settings.default_jurisdiction),
                    source_type=source_type,
                    authority_level=AuthorityLevel.PRIMARY,
                    basis_role="primary legal source",
                    relevant_issue=params.get("issue", ""),
                    supporting_excerpt=excerpt,
                    read_status="read_success",
                    accepted_reason=f"LLM accepted: {params.get('reason', 'Evaluated and accepted')}",
                )
                if accepted:
                    obs_turn = self.observability.record_turn(
                        turn_number=self.goal.turn_count,
                        goal_state=self.goal.formulated_goal,
                        current_objective=self.goal.current_objective,
                        active_narrative_node="",
                        selected_action="accept_evidence",
                        exact_tool_input=params,
                        normalized_observation=f"Accepted: {title}",
                        source_ids_affected=[source_id],
                        evidence_decisions=[{"source_id": source_id, "decision": "accepted", "reason": params.get("reason", "")}],
                    )
                    yield self.emitter.emit(
                        "source_read",
                        "Evidence Accepted",
                        f"Accepted [{source_id}] {title}",
                        payload={"source_id": source_id, "title": title},
                    )
            else:
                # Unknown action — record error
                self.observability.record_turn(
                    turn_number=self.goal.turn_count,
                    goal_state=self.goal.formulated_goal,
                    current_objective=self.goal.current_objective,
                    active_narrative_node="",
                    selected_action=action_name,
                    error_details=f"Unknown or invalid action: {action_name}",
                    unresolved_gaps_before=gaps_before,
                    unresolved_gaps_after=list(self.gaps),
                    normalized_observation=f"Action '{action_name}' is not recognized.",
                )

        # --- SYNTHESIZE FINAL ANSWER ---
        final_answer = await self._synthesize_final_answer(user_query)

        # --- VERIFY ---
        verification = self.verifier.verify(
            final_answer,
            strict_mode=self.settings.sheria_research_engine != "legacy",
        )
        self.observability.set_verification_result(
            self.verifier.build_verification_dict(verification)
        )

        if not verification.passed:
            yield self.emitter.emit(
                "verification_result",
                "Verification",
                f"Verification {'passed' if verification.passed else 'has issues'}.",
                payload={
                    "status": verification.status,
                    "issues": verification.issues[:5],
                    "unsupported_citations": verification.unsupported_citations[:5],
                },
            )

        # --- COMPLETE ---
        summary = self._build_completion_summary(verification)
        self.observability.complete_run(summary)

        yield self.emitter.emit(
            "run_finished",
            "Research Complete",
            summary,
            state_summary=summary,
            payload={
                "final_answer": final_answer,
                "verification": self.verifier.build_verification_dict(verification),
                "evidence": self.evidence_ledger.to_dict(),
                "trace_summary": {
                    "run_id": trace.run_id,
                    "turn_count": trace.turn_count,
                    "accepted_evidence": trace.total_evidence_accepted,
                    "rejected_sources": trace.total_evidence_rejected,
                    "read_attempts": trace.total_read_attempts,
                },
            },
        )

        yield self.emitter.emit(
            "answer_token",
            "Final Answer",
            final_answer[:100] + "...",
            payload={"token": final_answer},
        )

    # -------------------------------------------------------------------
    # Decision Making — the LLM decides everything
    # -------------------------------------------------------------------

    async def _decide_next_action(
        self,
        user_query: str,
        trace: RunTrace,
        is_first_turn: bool = False,
    ) -> dict[str, Any]:
        """
        Ask the LLM to observe the current state and decide the next action.

        On the first turn, the LLM also formulates the goal.
        The LLM returns observation (what it saw), action (what to do),
        and reason (why).
        """
        evidence_summary = self._build_evidence_summary()
        candidate_summary = self._build_candidate_summary()

        tool_names = self.tool_executor.list_tools()
        tools_desc = "\n".join(f"  - {name}" for name in tool_names)

        # Dynamically augment with workspace tools (do not hardcode)
        try:
            from app.agents.tools import workspace_tools as _workspace_tools
            for attr in dir(_workspace_tools):
                if attr.startswith("handle_workspace_") and callable(getattr(_workspace_tools, attr)):
                    tools_desc += f"\n  - {attr.replace('handle_', '')}"
        except Exception:
            tools_desc += "\n  - workspace_list_files\n  - workspace_search_files\n  - workspace_read_file\n  - workspace_index_summary\n  - workspace_delegate"

        # Build the system prompt with the LLM's existing goal (if any)
        goal_line = trace.formulated_goal or "Not yet formulated."

        # Workspace context: list file paths so the LLM can reference them, but NOT their contents
        workspace_context = ""
        if self.workspace_tree:
            file_paths = []
            def _walk_paths(entries, prefix=""):
                for e in entries:
                    p = e.get("path", "")
                    if e.get("kind") == "file" and p:
                        file_paths.append(p)
                    if e.get("children"):
                        _walk_paths(e["children"], prefix)
            _walk_paths(self.workspace_tree)
            path_list = "\n".join(f"  - {p}" for p in sorted(file_paths))
            workspace_context = f"""
## Workspace Files Available
The user has these files in their workspace. Use these EXACT paths when calling workspace_read_file:
{path_list}

You do NOT have their contents here -- use workspace_list_files to list them and workspace_read_file to read a specific file.
If the user asks about their documents, files, or matters, you MUST use your workspace_* tools to discover and read their content.
"""

        system_prompt = f"""\
You are the {self.settings.agent_name} Agent — a LEGAL WORKSPACE AGENT living inside {self.settings.platform_name}. You interact with the user's workspace files using your built-in workspace tools.

## Your Identity
You live inside the user's legal IDE ({self.settings.platform_name}) in the Agent Panel. The user's workspace has a file tree (left pane), documents (workbench), and your conversation area (agent panel).

## CRITICAL: Workspace Access
You HAVE direct access to workspace files through your workspace_* tools. Use them when appropriate — the LLM decides which tool and when.
- Prefer workspace tools for any question about the user's files or their contents.
- Use web/legal tools only when workspace content is insufficient or the user asks for external authorities.

## Your Workspace Context
{goal_line}

## Current State Details
- Turn: {self.goal.turn_count}/{self.goal.loop_budget}
- Mode: {self.goal.mode}
- Current objective: {self.goal.current_objective or "Not yet set."}
{workspace_context}

## How You Interact with the Platform
1. Workspace files come FIRST: when the user asks about their files or matters, you MUST call workspace_list_files or workspace_search_files to discover files, and workspace_read_file to read their content. Do NOT guess what files contain from their names.
2. When reading a workspace file, emit a "context_gathering" event (include the path) so the UI shows you inspected the file.
3. If you create or modify a document, emit a "tab_open_request" event with kind="file", path, and title so the workbench opens it.
4. Provide concise narrative feedback after any workspace operation.
5. You MUST NOT give up or refuse to answer. Always try a tool (workspace_*, kenya_law_research, brave_search, etc.) before choosing synthesize_answer or stop. Start with workspace_list_files if the user asks about their workspace.

## Workspace Tools
- workspace_list_files: List files in the workspace.
- workspace_search_files: Search workspace files by name.
- workspace_read_file: Read a file and get its text content (text, PDF, DOCX supported).
- workspace_index_summary: Get an overview of the workspace structure.

## Available Tools
{tools_desc}

## Tool Guidance
- kenya_law_research: Use for Kenyan primary legal sources (statutes, cases). Prefer this for Kenyan authorities.
- brave_search: Use for broader web discovery, foreign law, comparative material.
- html_read_browser_fetch: Use for JS-heavy pages; returns a page narrative and links for navigation.
- general_web_fetch: Use as fallback for static pages.

## Evidence Policy
- Search results are candidates, NOT evidence.
- You must read and explicitly ACCEPT evidence before citing it in the final answer.
- Workspace files: AFTER reading a workspace file with workspace_read_file, you MUST call accept_evidence to add it to the evidence ledger. Use source_id "WS1", "WS2" etc for workspace files.

## Decision Rules
- On the FIRST TURN you MUST ALWAYS call a tool (workspace_list_files, workspace_read_file, kenya_law_research, brave_search, etc.). Investigate before you answer.
- If you need more information, choose a specific tool — do NOT skip to synthesize_answer.
- Choose "synthesize_answer" ONLY when you have gathered and accepted sufficient evidence.
- Choose "stop" only when no useful sources remain and you cannot proceed further.
- You must NOT refuse, give up, or say "I cannot answer." Always perform a concrete action (a tool call).

## Output Format
You MUST return valid JSON with these fields (goal only on first turn):
{{
  "goal": "(FIRST TURN ONLY)",
  "observation": "what you observed",
  "action": "tool_name | accept_evidence | synthesize_answer | stop",
  "parameters": {{...}},
  "reason": "why",
  "objective": "new objective or empty",
  "final_answer": "include when synthesizing",
  "stop": false
}}
"""

        evidence_text = evidence_summary or "No evidence gathered yet."
        candidates_text = candidate_summary or "No candidates discovered yet."

        gaps_text = ""
        if self.gaps:
            gaps_text = "\n".join(f"  - {g}" for g in self.gaps[:5])

        recent_history = self._build_recent_history(trace)

        # Build the user prompt section
        if is_first_turn:
            # First turn: no history, just the raw query
            user_prompt = f"""\
## User Query
{user_query}

## CRITICAL INSTRUCTION
This is your VERY FIRST turn. You MUST call a tool now. Options include:
- workspace_list_files or workspace_search_files (if the query is about the user's workspace or files)
- workspace_read_file (if a specific file is mentioned)
- kenya_law_research or brave_search (for legal research)

Do NOT choose 'synthesize_answer' or 'stop' on the first turn. Only a tool call is allowed.

Return JSON with "goal", "action", "parameters", and "reason".
"""
        else:
            user_prompt = f"""\
## User Query
{user_query}

## Evidence Gathered
{evidence_text}

## Candidates Available to Read
{candidates_text}

## Page Navigation (available links on fetched pages)
{self._build_page_nav_summary()}

## Unresolved Gaps
{gaps_text or "  No unresolved gaps."}

## Recent Execution History
{recent_history or "  No previous executions recorded."}

Observe what happened from your last action. Then decide the next action.
Return JSON with "observation", "action", "parameters", and "reason".
"""

        try:
            raw = await self.llm.complete(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format="json",
                max_tokens=800 if not is_first_turn else 1200,
            )
            parsed = _safe_json_loads(raw)
            return parsed
        except Exception as e:
            print(f"[ORBIT DEBUG] LLM call failed: {e}")
            return {
                "action": "brave_search",
                "parameters": {"query": user_query},
                "reason": "I need to search more broadly to find relevant legal sources.",
                "observation": "",
                "stop": False,
            }

    # -------------------------------------------------------------------
    # Tool Execution
    # -------------------------------------------------------------------

    async def _execute_tool(
        self,
        action_name: str,
        params: dict[str, Any],
        trace: RunTrace,
    ) -> dict[str, Any]:
        """Execute a named tool and record the observation."""
        obs_turn = self.observability.record_turn(
            turn_number=self.goal.turn_count,
            goal_state=self.goal.formulated_goal,
            current_objective=self.goal.current_objective,
            active_narrative_node="",
            selected_action=action_name,
            exact_tool_input=params,
        )

        # ---- Workspace subagent tools (intercepted before tool executor) ----
        if action_name.startswith("workspace_") and self.workspace_subagent:
            # Import workspace_tools module directly, then discover handlers dynamically
            from app.agents.tools import workspace_tools as _ws_tools
            handlers = {}
            for attr in dir(_ws_tools):
                if attr.startswith("handle_workspace_"):
                    tool_name = attr.replace("handle_", "")
                    candidates = [tool_name]
                    # Also allow bare "tool_name" as key
                    for key in candidates:
                        handlers[key] = getattr(_ws_tools, attr)

            handler = handlers.get(action_name)
            if handler:
                result_text = await handler(self.workspace_subagent, params)
                events = []
                # Emit a context_gathering event for reads so the UI can surface file-level context
                if action_name == "workspace_read_file":
                    file_path = params.get("path") or params.get("file") or params.get("dir_path") or ""
                    events.append(self.emitter.emit(
                        "context_gathering",
                        "Workspace File Read",
                        f"Read workspace file: {file_path}" if file_path else "Read workspace file",
                        payload={"path": file_path, "excerpt": result_text[:500]},
                        state_summary=result_text[:500],
                    ))
                # Always emit a workspace operation narrative
                events.append(self.emitter.emit(
                    "research_work_state",
                    "Workspace Operation",
                    result_text[:200] + ("…" if len(result_text) > 200 else ""),
                    state_summary=result_text[:500],
                ))
                obs_turn.normalized_observation = result_text
                return {"result": result_text, "events": events}
            return {"result": f"Unknown workspace tool: {action_name}", "events": []}

        result = await self.tool_executor.execute(action_name, params)

        obs_turn.raw_tool_status = result.status if isinstance(result, ToolObservation) else result.status
        obs_turn.tool_or_skill_called = action_name

        events: list[dict[str, Any]] = []

        if isinstance(result, ToolObservation):
            obs_turn.normalized_observation = result.message

            if result.candidates:
                for c in result.candidates:
                    source_id = c.get("source_id", self._next_source_id())
                    self.evidence_ledger.register_candidate(
                        source_id=source_id,
                        title=c.get("title", "Untitled"),
                        url=c.get("url", ""),
                        snippet=c.get("snippet", ""),
                        discovered_by=action_name,
                    )
                    obs_turn.source_ids_affected.append(source_id)

                events.append(self.emitter.emit(
                    "source_found",
                    "Candidates Found",
                    f"Found {len(result.candidates)} candidate(s).",
                    payload={"count": len(result.candidates), "tool": action_name},
                ))
            else:
                events.append(self.emitter.emit(
                    "research_work_state",
                    "Search Result",
                    result.message,
                ))
                obs_turn.normalized_observation = result.message

            if result.error_type:
                obs_turn.error_details = result.error_message

            # Note: evidence is NOT auto-accepted here. The LLM must call
            # accept_evidence explicitly when it decides the source is reliable.
            # Only mark readable so the LLM can inspect the content first.

        elif isinstance(result, ReadObservation):
            obs_turn.normalized_observation = f"Read status: {result.status}"
            obs_turn.raw_tool_status = result.status

            self.observability.record_read_attempt(
                source_id=params.get("source_id", "unknown"),
                url=result.url,
                method=action_name,
                status=result.status,
                chars_extracted=result.chars_extracted,
                error_message=result.error_message,
            )

            if result.status == "read_success":
                source_id = params.get("source_id", "")
                if not source_id:
                    # Generate a source ID and register the URL as a candidate
                    source_id = self._next_source_id()
                    self.evidence_ledger.register_candidate(
                        source_id=source_id,
                        title=result.title[:200] or "Read document",
                        url=result.url,
                        snippet=result.text_excerpt[:200],
                        discovered_by=action_name,
                    )
                    obs_turn.source_ids_affected.append(source_id)

                if source_id:
                    # Store full text + page navigation data in readable ledger
                    read_data = result.to_dict()
                    read_data["full_text"] = result.full_text[:50000]
                    read_data["links"] = result.links
                    read_data["page_narrative"] = result.page_narrative
                    read_data["forms"] = result.forms
                    self.evidence_ledger.mark_readable(source_id, read_data)
                    # NOT auto-accepted — LLM must call accept_evidence.
                    # But emit the read event so the LLM knows it's available.

                events.append(self.emitter.emit(
                    "source_read",
                    "Source Read",
                    f"Successfully read {result.chars_extracted} characters.",
                    payload={
                        "url": result.url,
                        "chars_extracted": result.chars_extracted,
                        "extraction_quality": result.extraction_quality,
                    },
                ))
            else:
                if params.get("source_id", ""):
                    self.evidence_ledger.mark_unreadable(
                        params["source_id"],
                        result.error_message or "Could not read source.",
                    )

                events.append(self.emitter.emit(
                    "source_unreadable",
                    "Source Unreadable",
                    f"Could not extract text: {result.error_message or 'Unknown error'}",
                    payload={"url": result.url, "error": result.error_message},
                ))

        # --- Persist to Supabase if configured ---
        if self.history_store is not None:
            try:
                await self.history_store.record_turn(
                    session_id=trace.session_id,
                    turn_number=self.goal.turn_count,
                    selected_action=action_name,
                    exact_tool_input=params,
                    raw_tool_status=obs_turn.raw_tool_status,
                    normalized_observation=obs_turn.normalized_observation,
                    error_details=obs_turn.error_details,
                    source_ids_affected=obs_turn.source_ids_affected,
                )
            except Exception:
                pass

        return {"events": events}

    # -------------------------------------------------------------------
    # Answer Synthesis
    # -------------------------------------------------------------------

    async def _synthesize_final_answer(self, user_query: str) -> str:
        """Synthesize the final answer from accepted evidence."""

        evidence_text = ""
        for ev in self.evidence_ledger.accepted:
            evidence_text += f"\n[{ev.source_id}] {ev.title}\n"
            evidence_text += f"    URL: {ev.url}\n"
            evidence_text += f"    Authority: {ev.authority_level.value}\n"
            # Get full text and provision from readable sources
            provision_text = ""
            full_source_text = ""
            for rs in self.evidence_ledger.readable_sources:
                if rs.get("source_id") == ev.source_id:
                    rd = rs.get("read_data", {})
                    full_source_text = rd.get("full_text", "")
                    provision_text = rd.get("provision", "")
                    break
            if provision_text:
                evidence_text += f"    Requested provision text:\n{provision_text[:5000]}\n"
            if full_source_text:
                evidence_text += f"    Full document text:\n{full_source_text[:50000]}\n"
            elif ev.supporting_excerpt:
                evidence_text += f"    Excerpt: {ev.supporting_excerpt[:2000]}\n"

        # Collect workspace files read during the ReAct loop from observability trace
        workspace_content = ""
        try:
            trace = self.observability.trace
            if trace and trace.loop_turns:
                for turn in trace.loop_turns:
                    if turn.selected_action == "workspace_read_file" and turn.normalized_observation:
                        path = (turn.exact_tool_input or {}).get('path', '?')
                        workspace_content += f"\n--- Turn {turn.turn_number}: read {path} ---\n"
                        workspace_content += turn.normalized_observation[:3000] + "\n"
        except Exception:
            pass

        system_prompt = f"""\
You are {self.settings.agent_name}, a legal workspace AI built into {self.settings.platform_name}. Your primary role is to answer the user's legal questions using accepted evidence, and to act as the Orbit legal-IDE companion.

Rules:
- Cite every source using bracketed IDs like [S1], [S2]. If a workspace file has content, cite it as [Workspace].
- Do NOT invent facts, cases, dates, laws, organizations, or URLs.
- Use ONLY accepted evidence for claims. If evidence is insufficient, explain what is missing and recommend specific next actions (which tools to call or which workspace files to read).
- Structure the answer: introduction, analysis by issue, conclusion, and source list.
- The LLM decides; code only executes the chosen tools. Do not hardcode investigative choices in the runtime.
- If there is no accepted evidence, DO NOT say "I cannot answer" or ask the user to accept evidence. Instead, explain specifically what you found in the workspace files you read, or what tools you would run next."""

        user_prompt = f"""\
## Question
{user_query}

## Accepted Evidence
{evidence_text or 'No accepted evidence.'}

## Workspace Files Read During Investigation
{workspace_content or 'No workspace files were read.'}

## Instructions
Write a thorough, well-structured answer. If you have workspace file content above, use it to answer the question directly. If the evidence is insufficient, list the gaps and which tools or files you would inspect next.
"""

        try:
            answer = await self.llm.complete(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=2000,
            )
            return answer.strip()
        except Exception as e:
            return f"I encountered an error while synthesizing the answer: {e}"

    # -------------------------------------------------------------------
    # State Summary Builders
    # -------------------------------------------------------------------

    def _build_evidence_summary(self) -> str:
        if not self.evidence_ledger.accepted:
            return ""
        lines = []
        for ev in self.evidence_ledger.accepted:
            lines.append(f"[{ev.source_id}] {ev.title}")
            lines.append(f"    Authority: {ev.authority_level.value}, Role: {ev.basis_role}")
            lines.append(f"    URL: {ev.url}")
            if ev.supporting_excerpt:
                lines.append(f"    Excerpt: {ev.supporting_excerpt[:400]}")
        return "\n".join(lines)

    def _build_candidate_summary(self) -> str:
        openable = self.evidence_ledger.openable_candidates
        if not openable:
            return ""
        lines = []
        for c in openable[:10]:
            lines.append(f"  - {c.title}")
            lines.append(f"    URL: {c.url}")
            lines.append(f"    Source: {c.discovered_by}")
        if len(openable) > 10:
            lines.append(f"  ... and {len(openable) - 10} more")
        return "\n".join(lines)

    def _build_page_nav_summary(self) -> str:
        """Show page navigation links from the last recently-fetched readable source."""
        readable = self.evidence_ledger.readable_sources
        if not readable:
            return "  No page data available."

        for rs in reversed(readable):
            rd = rs.get("read_data", {})
            links = rd.get("links", [])
            if links:
                lines = [f"  Page: {rs.get('url', '?')[:80]}"]
                lines.append(f"  Title: {rs.get('title', '?')[:60]}")
                lines.append(f"  Available links:")
                for i, link in enumerate(links[:15], 1):
                    lines.append(f"    [{i}] {link.get('text', '?')[:60]}")
                    lines.append(f"        → {link.get('href', '?')[:100]}")
                if len(links) > 15:
                    lines.append(f"    ... and {len(links) - 15} more links")
                return "\n".join(lines)

        return "  No page with extractable links."

    def _build_recent_history(self, trace: RunTrace, max_turns: int = 5) -> str:
        """Build recent execution history from the observability ledger."""
        recent = trace.loop_turns[-max_turns:] if trace.loop_turns else []
        if not recent:
            return ""

        lines: list[str] = []
        for turn in recent:
            action = turn.selected_action or "(none)"
            tool = turn.tool_or_skill_called or action
            inp = turn.exact_tool_input or {}
            inp_str = "; ".join(f"{k}={v}" for k, v in inp.items())
            if len(inp_str) > 200:
                inp_str = inp_str[:200] + "..."
            status = turn.raw_tool_status or "(no status)"
            obs = turn.normalized_observation or "(no observation)"
            err = turn.error_details or ""
            sources = turn.source_ids_affected
            src_str = f", sources affected: {sources}" if sources else ""

            lines.append(f"  Turn {turn.turn_number}: {tool}")
            if inp_str:
                lines.append(f"    Input: {inp_str}")
            lines.append(f"    Status: {status}")
            # Show more observation content for workspace reads so the LLM doesn't keep re-reading
            max_obs = 5000 if (action or "").startswith("workspace_") else 300
            lines.append(f"    Observation: {obs[:max_obs]}")
            if err:
                lines.append(f"    Error: {err[:200]}")
            if src_str:
                lines.append(f"    {src_str}")

        return "\n".join(lines)

    def _build_completion_summary(self, verification: VerificationResult) -> str:
        parts = []
        ev_count = len(self.evidence_ledger.accepted)
        rej_count = len(self.evidence_ledger.rejected)

        if ev_count > 0:
            parts.append(f"Reviewed {ev_count + rej_count} sources, accepted {ev_count} as evidence.")
        else:
            parts.append("Reviewed sources but could not find sufficient authoritative material.")

        if verification.passed:
            parts.append("All citations verified against accepted evidence.")
        else:
            issues = verification.issues[:3]
            if issues:
                parts.append(f"Note: {'; '.join(issues)}")

        parts.append(f"Completed in {self.goal.turn_count} research steps.")
        basis = self.evidence_ledger.assess_basis_strength()
        parts.append(f"Basis strength: {basis.value}.")
        return " ".join(parts)
