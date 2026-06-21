import asyncio
from typing import AsyncIterator

from .config import Settings
from .deepseek_client import DeepSeekClient, extract_json_object
from .events import EventEmitter
from .source_quality import evaluate_source
from .guards import build_repair_prompt, verify_answer
from .schemas import ChatRequest, EvidenceCard, Source
from .store import SupabaseStore
from .research_state import ResearchState
from .evidence_ledger import EvidenceLedger
from .tool_router import ToolRouter
from .browser_fetch import domain_of
from .recovery import RecoveryManager


# ---------------------------------------------------------------------------
# Dynamic status generator — replaces hardcoded summary strings with
# lightweight LLM-generated operational one-liners.
# ---------------------------------------------------------------------------

async def generate_dynamic_status(llm: DeepSeekClient, action_context: str) -> str:
    """Ask the LLM to produce a short, natural, first-person working-step update.

    Requirements:
    - Produce a complete thought (1-2 short sentences) in first person (I will..., I'm..., I'll...).
    - Use natural phrasing and optional contractions to sound human ("I'll", "I'm").
    - When appropriate, mention the next fallback step (e.g. "If that fails, I'll try X.").
    - Keep it concise but not clipped (roughly 10-40 words). Avoid robotic lists or fragments.
    """
    prompt = (
        "You are a legal research assistant writing live working-step summaries for a user-facing activity log. "
        "Write 1-2 short, complete sentences in the first person that clearly explain the immediate action you are taking or the result you just observed. "
        "When relevant, mention the next fallback step you will try if this attempt fails (for example: 'If that fails, I'll try ...'). "
        "Avoid robotic shorthand; use natural language and optional contractions. "
        f"Context: {action_context}"
    )
    try:
        response = await llm.complete([{"role": "user", "content": prompt}], max_tokens=40)
        return response.strip().replace('"', '')
    except Exception:
        return "Processing background legal parameters..."



def _bias_queries_for_kenya_legal(queries: list[str], settings: Settings) -> list[str]:
    if not queries:
        return []

    biased = []
    for query in queries:
        # Keep the clean discovery query intact
        biased.append(query)
        if len(biased) >= settings.max_search_queries:
            break
                
        # If the planner didn't write an explicit site operator, inject a dual-domain constraint
        if not any("site:" in q for q in queries):
            # Forces search engines to look inside the primary legal repositories exclusively
            legal_filter = "(site:new.kenyalaw.org OR site:kenyalaw.org OR site:judiciary.go.ke)"
            
            # Wrap original terms in exact quotes if they look like specific names or numbers
            biased.append(f"{query} {legal_filter}")
            
        if len(biased) >= settings.max_search_queries:
            break
    return biased[: settings.max_search_queries]


PLANNER_SYSTEM = """You are a legal research planning agent.
Analyze the user's query complexity and clarify missing elements before executing.
Return strict JSON only. No markdown.

JSON schema:
{
  "query_intent": "light" | "standard" | "detailed",
  "requires_clarification": true | false,
  "clarification_questions": ["Short, simple question 1?", "Short, simple question 2?"],
  "search_queries": ["query 1"],
  "gaps": ["gap 1"],
  "must_verify": ["item 1"]
}

CLARIFICATION THRESHOLD:
- Never ask clarifying questions for casual interactions, greeting text, or general informational inquiries.
- Only flag 'requires_clarification: true' if a core legal parameter is completely absent, meaning that guessing a default search path will result in a fundamentally wrong or dangerously misleading legal position. If you can attempt an initial search to resolve ambiguity, do the search first.
- Evaluate message text: brief informational checks should be classed as "light". Complex analytical issues are "detailed".
- Use at most 4 search queries. Prefer official, primary, and authoritative sources.
"""

RESEARCH_SYSTEM = """You are an autonomous ReAct legal research agent.
You evaluate an EvidenceLedger and invoke tools dynamically to fill research gaps.

YOUR TOOL POLICY (preferential, not mutually exclusive):
1. Prefer a prioritized discovery sequence when searching for Kenyan primary authorities: first attempt 'new_kenyalaw_native' for native case law/indexed authorities; if that returns no usable results or a corrupt set, then try 'official_kenya_domain_search'; if both fail to produce reliable material, fall back to 'brave_search_fallback' for wider web coverage.
2. For general legal concepts, foreign jurisprudence, or comparative references, 'brave_search_fallback' is appropriate earlier in the sequence. These are preferred orderings — try the next option only when the prior attempt produced no usable results.
3. When attempting tools, emit clear working-step updates describing the attempt and, on failure, state the fallback being tried next.

CLARIFICATION THRESHOLD:
- Never ask clarifying questions for casual interactions, greeting text, or general informational inquiries.
- Only flag 'requires_clarification: true' if a core legal parameter is completely absent, meaning that guessing a default search path will result in a fundamentally wrong or dangerously misleading legal position. If you can attempt an initial search to resolve ambiguity, do the search first.
"""

CASE_SUMMARY_SYSTEM = """You are a legal document analyst.
Generate a structured case summary based on the provided text fragment.
Structure your answer exactly like this:
- **Case Name & Citation**: 
- **Key Legal Issue**: 
- **Holding / Ruling**: 
- **Advocate Takeaway / Core Lesson**: 
"""

ANSWER_SYSTEM = """You are a source-grounded legal research agent.
You are not allowed to invent facts, cases, dates, laws, organizations, or URLs.
Use only the evidence cards provided by the application.
Every factual claim must cite source ids like [S1].
If the evidence is insufficient, say so clearly.
"""

class PlannerNode:
    @staticmethod
    async def execute_initial(request: ChatRequest, state: ResearchState, llm: DeepSeekClient, emitter: EventEmitter, settings: Settings) -> AsyncIterator[dict]:
        # Emit a human-style working-step update immediately as planning starts
        try:
            dyn_start = await generate_dynamic_status(llm, f"Starting planning for user query: {request.message}")
        except Exception:
            dyn_start = "Evaluating query complexity, intent boundaries, and missing parameters."
        yield emitter.emit("planning", "Planning", dyn_start)
        
        state.deep_research_mode = getattr(request, "deep_research", False)
        mode = "Kenya legal mode is ON." if request.kenya_legal_mode else "General research mode is ON."
        deep_status = "Deep Research is ENABLED." if state.deep_research_mode else "Deep Research is DISABLED."
        
        text = await llm.complete(
            [
                {"role": "system", "content": PLANNER_SYSTEM},
                {"role": "user", "content": f"{mode}\n{deep_status}\n\nUser query:\n{request.message}"},
            ],
            response_format="json",
        )
        
        try:
            plan = extract_json_object(text)
        except Exception:
            plan = {"query_intent": "standard", "requires_clarification": False, "search_queries": [request.message], "gaps": ["Core lookup"], "must_verify": []}

        state.query_intent = plan.get("query_intent", "standard")
        state.budget.configure_for_intent(state.query_intent, state.deep_research_mode)

        if plan.get("requires_clarification") and not state.clarification.provided_answers:
            state.clarification.requires_input = True
            state.clarification.questions = plan.get("clarification_questions", [])
            yield emitter.emit(
                "clarification_requested", "Clarification Required", "The agent requires user input before proceeding.",
                state_summary="I need a few missing details to execute an accurate research strategy.",
                next_action="Awaiting user choices.",
                payload={"questions": state.clarification.questions}
            )
            return

        # Regular gap mapping proceeds if no clarification is required or answers are already present
        queries = plan.get("search_queries", [])[: settings.max_search_queries]
        if request.kenya_legal_mode and queries:
            queries = _bias_queries_for_kenya_legal(queries, settings)

        state.current_queries = queries[:settings.max_search_queries]

        # Open structural gaps dynamically
        for g in plan.get("gaps", [])[:5]:
            gap = state.open_gap(g, priority="critical")
            yield emitter.emit(
                "gap_opened", "Research gap opened", f"Tracking: {g}",
                state_summary=f"I'm tracking the need for {g}.", payload={"gap_id": gap.gap_id, "description": gap.description}
            )

        yield emitter.emit(
            "plan_ready", "Research plan", "Plan ready with search queries and verification goals.",
            payload={"queries": state.current_queries, "must_verify": plan.get("must_verify", [])}
        )

    @staticmethod
    async def execute_followup(request: ChatRequest, state: ResearchState, emitter: EventEmitter) -> AsyncIterator[dict]:
        unresolved = [g.description for g in state.gaps if g.status == "open" and g.priority in {"critical", "important"}]
        followup_queries = []
        for desc in unresolved:
            followup_queries.extend([f"{desc} Kenya Employment Act", f"site:kenyalaw.org {desc}"])
        
        state.current_queries = followup_queries[: state.budget.max_queries_per_round]
        
        yield emitter.emit(
            "followup_searching", "Follow-up searching", f"Running {len(state.current_queries)} targeted queries.",
            state_summary="I’m running targeted follow-up searches to fill unresolved gaps.", payload={"queries": state.current_queries}
        )


class SearchNode:
    @staticmethod
    async def execute(request: ChatRequest, state: ResearchState, router: ToolRouter, llm: DeepSeekClient, emitter: EventEmitter, settings: Settings) -> AsyncIterator[dict]:
        dyn = await generate_dynamic_status(llm, f"Executing {len(state.current_queries)} discovery queries across legal repositories")
        yield emitter.emit("searching", "Searching", dyn, payload={"queries": state.current_queries})

        # Bias queries for Kenyan legal index structures when requested
        if request.kenya_legal_mode:
            state.current_queries = _bias_queries_for_kenya_legal(state.current_queries, settings)

        tasks = [router.search(query) for query in state.current_queries]
        groups = await asyncio.gather(*tasks, return_exceptions=True)

        raw_sources = []
        for idx, group in enumerate(groups):
            if isinstance(group, Exception):
                yield emitter.emit("error", "Search failed", "A query failed.", payload={"error": str(group)})
                continue
            if request.kenya_legal_mode:
                kenya_hits = [r for r in group if any(d in (getattr(r, 'url', '')) for d in settings.legal_domains)]
                raw_sources.extend(kenya_hits if kenya_hits else group)
            else:
                raw_sources.extend(group)

        # Dedupe and Rank
        by_url = {}
        for s in raw_sources:
            clean = s.url.split("#")[0]
            if clean not in by_url or s.score > by_url[clean].score:
                s.url = clean
                by_url[clean] = s

        ranked = sorted(by_url.values(), key=lambda s: s.score, reverse=True)
        for index, s in enumerate(ranked, start=1):
            s.id = f"R{state.search_round}_S{index}"

        # Evaluate and Select
        state.selected_sources = []
        for s in ranked[:settings.max_sources_to_inspect]:
            try:
                q = evaluate_source(s.url, title=s.title, snippet=getattr(s, "snippet", ""), kenya_legal_mode=request.kenya_legal_mode)
                s.quality_label = q.label
                s.authority_level = q.authority_level
                yield emitter.emit("source_provisionally_classified", "Provisional Classify", f"Classified {s.title}: {q.label}")
                state.selected_sources.append(s)
            except Exception:
                pass


class EvaluatorNode:
    @staticmethod
    async def execute(request: ChatRequest, state: ResearchState, ledger: EvidenceLedger, router: ToolRouter, llm: DeepSeekClient, emitter: EventEmitter, recovery: RecoveryManager, settings: Settings) -> AsyncIterator[dict]:
        dyn = await generate_dynamic_status(llm, f"Reading and evaluating {len(state.selected_sources)} shortlisted legal sources")
        yield emitter.emit("reading_source", "Reading sources", dyn, payload={"count": len(state.selected_sources)})

        for s in state.selected_sources:
            text = await router.fetch_text(s.url)
            
            # Recovery Loop
            if not text:
                for tool_name in recovery.unreadable_page_playbook():
                    recovery_dyn = await generate_dynamic_status(llm, f"Attempting recovery tool '{tool_name}' for unreadable page: {s.url}")
                    yield emitter.emit("recovery_attempted", "Recovery", recovery_dyn)
                    if tool_name == "pdf_text_extract":
                        text = await router.pdf_text_extract(s.url)
                    elif tool_name in ("browser_fetch_firefox", "http_fetch"):
                        text = await router.fetch_text(s.url, prefer_browser=(tool_name=="browser_fetch_firefox"))
                    if text: break

            if not text:
                yield emitter.emit("source_unreadable", "Unreadable", f"Skipping {s.id}")
                # Log the scraping obstacle directly into the state tracking object
                state.recovery_notes.append(f"Failed to extract readable contents from authority {s.id} ({s.url})")
                continue

            read_dyn = await generate_dynamic_status(llm, f"Successfully extracted text from legal authority: {s.title}")
            yield emitter.emit("source_read", "Source read", read_dyn)

            # Extract Evidence
            card = EvidenceCard(
                source_id=s.id, title=s.title, url=s.url, domain=domain_of(s.url),
                excerpt=text[: settings.max_evidence_chars], quality_label=getattr(s, "quality_label", ""),
                authority_level=getattr(s, "authority_level", "")
            )
            ledger.add_candidate(card)
            evidence_dyn = await generate_dynamic_status(llm, f"Extracted evidence card from source {s.id} covering key legal points")
            yield emitter.emit("evidence_created", "Evidence created", evidence_dyn)

            # Fill Gaps
            for g in state.gaps:
                if g.status == "open":
                    hits = ledger.supports_gap(g.description)
                    if hits:
                        g.status = "filled"
                        gap_dyn = await generate_dynamic_status(llm, f"Resolved research gap: {g.description}")
                        yield emitter.emit("gap_filled", "Gap filled", gap_dyn, payload={"gap_id": g.gap_id})

        # Calculate Basis
        counts = ledger.counts_by_authority()
        unresolved_critical = [g.description for g in state.gaps if g.status == "open" and g.priority == "critical"]

        if counts.get("primary", 0) >= 2 and not unresolved_critical: basis = "strong_basis"
        elif counts.get("primary", 0) >= 1 or (counts.get("official", 0) >= 1 and not unresolved_critical): basis = "moderate_basis"
        elif counts.get("persuasive", 0) >= 1: basis = "limited_basis"
        else: basis = "weak_basis"

        state.basis_strength = basis
        basis_dyn = await generate_dynamic_status(llm, f"Assessed evidence basis as '{basis}' with {counts.get('primary', 0)} primary sources")
        yield emitter.emit("basis_assessed", "Basis assessed", basis_dyn, payload={"basis_strength": basis, "unresolved": unresolved_critical})


class DraftingNode:
    @staticmethod
    async def execute(request: ChatRequest, state: ResearchState, ledger: EvidenceLedger, llm: DeepSeekClient, emitter: EventEmitter, store: SupabaseStore, settings: Settings) -> AsyncIterator[dict]:
        # Volitional Case Summarizer Interceptor
        for card in ledger.accepted:
            excerpt_l = (card.excerpt or "").lower()
            if "v." in excerpt_l or "versus" in excerpt_l or "judgment" in excerpt_l:
                yield emitter.emit("generating_case_summary", "Case Summarizer", f"Generating an isolated brief for referenced authority: {card.title}")
                summary_text = await llm.complete([
                    {"role": "system", "content": CASE_SUMMARY_SYSTEM},
                    {"role": "user", "content": f"Source Excerpt:\n{card.excerpt}"}
                ])
                yield emitter.emit("case_summary_ready", "Case Brief Compiled", card.title, payload={"summary": summary_text})

        # Proceed to generation as normal
        answer_dyn = await generate_dynamic_status(llm, "Synthesizing grounded legal answer from accepted evidence cards")
        yield emitter.emit("answering", "Answering", answer_dyn)

        recent = store.recent_messages(request.session_id, limit=6)
        history = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in recent)
        evidence_text = "\n\n".join(f"[{c.source_id}] {c.title}\nURL: {c.url}\nExcerpt: {c.excerpt}" for c in ledger.accepted)
        
        # Force the LLM to use explicit bracketed source citations in the answer
        citation_instruction = (
            "When you refer to evidence, cite sources inline using bracketed IDs exactly like [S1], [S2]. "
            "Do not invent source IDs; only use IDs present in the Evidence list."
        )

        prompt = (
            f"Context:\n{history}\n\nQuery:\n{request.message}\n\nEvidence:\n{evidence_text}\n"
            f"{citation_instruction}\nAnswer using ONLY evidence."
        )
        
        answer = ""
        async for token in llm.stream([{"role": "system", "content": ANSWER_SYSTEM}, {"role": "user", "content": prompt}]):
            answer += token
            yield emitter.emit("answer_token", "Token", "Streaming", payload={"token": token}, visibility="internal")

        verify_dyn = await generate_dynamic_status(llm, "Running strict citation verification on drafted answer")
        yield emitter.emit("verifying", "Verifying", verify_dyn)
        guard = verify_answer(answer, ledger.accepted, strict_mode=request.strict_mode)

        if not guard.passed and request.strict_mode:
            repair_dyn = await generate_dynamic_status(llm, "Citation coverage gaps detected; executing targeted repair pass")
            yield emitter.emit("repairing", "Repairing", repair_dyn)
            repair_prompt = build_repair_prompt(answer, guard, ledger.accepted)
            answer = await llm.complete([{"role": "system", "content": ANSWER_SYSTEM}, {"role": "user", "content": repair_prompt}])
            guard = verify_answer(answer, ledger.accepted, strict_mode=request.strict_mode)
            yield emitter.emit("answer_replaced", "Repaired", "Answer repaired.", payload={"answer": answer})

        store.add_message(request.session_id, "assistant", answer)
        result_dyn = await generate_dynamic_status(llm, f"Verification completed with outcome: {guard.status}")
        yield emitter.emit("verification_result", "Result", result_dyn)
        yield emitter.emit("run_finished", "Done", "Research complete.")


class CriticNode:
    @staticmethod
    def assess(state: ResearchState, ledger: EvidenceLedger) -> dict:
        """The Critic reflects on the ledger state before drafting begins."""
        unresolved = [g for g in state.gaps if g.status == "open" and g.priority == "critical"]
        primary_sources = ledger.counts_by_authority().get("primary", 0)
        
        if not unresolved and primary_sources > 0:
            return {"status": "READY", "reason": "All critical gaps filled with primary sources."}
        if state.search_round >= state.budget.max_search_rounds:
            return {"status": "BUDGET_EXHAUSTED", "reason": "Budget limits reached."}
        return {"status": "CONTINUE", "reason": "Missing critical evidence."}


class ResearchAgent:
    def __init__(self, settings: Settings):
        self.settings = settings

    def decide_action(self, state: ResearchState, ledger: EvidenceLedger) -> str:
        assessment = CriticNode.assess(state, ledger)
        if assessment["status"] in {"READY", "BUDGET_EXHAUSTED"}:
            return "draft"
        return "search"

    def process_results(self, sources: list[Source], ledger: EvidenceLedger):
        # Minimal mapping: convert sources into EvidenceCard candidates via lightweight excerpts
        for s in sources:
            card = EvidenceCard(source_id=getattr(s, "id", ""), title=getattr(s, "title", ""), url=getattr(s, "url", ""), domain=domain_of(getattr(s, "url", "")), excerpt=getattr(s, "snippet", "")[:200])
            ledger.add_candidate(card)
