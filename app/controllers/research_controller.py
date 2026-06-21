from __future__ import annotations

import inspect
import json

from app.controllers.react_decision import (
    build_orchestrator_prompt,
    parse_orchestrator_response,
)
from app.evidence.source_basis_evaluator import SourceBasisEvaluator
from app.controllers.work_state import status_for_action
from app.ingestion.fetch_manager import FetchManager
from app.routing.query_classifier import LegalQueryClassifier
from app.schemas.research_state import (
    EvidenceItem,
    OutputFormat,
    ReActActionType,
    ResearchState,
)
from app.synthesis.answer_synthesizer import AnswerSynthesizer
from app.document_export.document_export_skill import DocumentExportSkill as OldDocumentExportSkill
from app.skills.document_export_skill import DocumentExportSkill
from app.tools.kenya_law_client import KenyaLawClient
from app.tools.search_adapters import SearchAdapters
from app.tools.registry_definitions import initialize_legal_registry


async def _emit_safe(emitter, event_type: str, title: str, message: str, **kwargs) -> None:
    if emitter is None:
        return
    result = emitter.emit(event_type, title, message, **kwargs)
    if inspect.isawaitable(result):
        await result


def _action_display_name(action_str: str) -> str:
    mapping = {
        "kenyalaw_legislation_search": "Searching Kenya Law legislation...",
        "kenyalaw_judgment_search": "Searching Kenya Law judgments...",
        "official_kenya_domain_search": "Searching official Kenyan domains...",
        "brave_search_fallback": "Running wider web search...",
        "kenya_law_case_resolve": "Resolving case from citation metadata...",
        "case_specific_search": "Searching for specific case by parties and issues...",
        "fetch_url": "Reading source...",
        "synthesize_answer": "Synthesizing answer from evidence...",
        "stop_with_gaps": "Stopping research with gaps noted.",
    }
    return mapping.get(action_str, f"Executing {action_str}...")


class ResearchController:
    """
    Orchestrates the react_v2 ReAct loop.

    The controller is a THIN EXECUTOR. It does not decide what to do.
    It sends the full state to the LLM, the LLM decides the next action,
    the controller executes it, feeds the result back, and repeats.
    """

    def __init__(
        self,
        llm_client=None,
        settings=None,
        brave_client=None,
        kenyalaw_client=None,
        document_export_skill=None,
    ) -> None:
        self.llm = llm_client
        self.settings = settings
        self.registry = initialize_legal_registry()
        self.classifier = LegalQueryClassifier(llm_client)
        self.kenyalaw_client = kenyalaw_client or KenyaLawClient()
        self.search_adapters = SearchAdapters(
            brave_client=brave_client,
            kenyalaw_client=self.kenyalaw_client,
        )
        self.fetch_manager = FetchManager()
        self.source_evaluator = SourceBasisEvaluator()
        self.synthesizer = AnswerSynthesizer(llm_client)
        self.old_document_export_skill = document_export_skill or OldDocumentExportSkill()
        self.new_document_export_skill = None

    def set_document_export_skill(self, skill: DocumentExportSkill) -> None:
        self.new_document_export_skill = skill

    async def run_loop(self, user_prompt: str, emitter=None) -> ResearchState:
        """Run the ReAct loop. The LLM decides every action; this executes."""
        classification = await self.classifier.classify(user_prompt)

        state = ResearchState(
            original_user_query=user_prompt,
            normalized_query=classification.normalized_query,
            classification=classification,
            jurisdiction_target=classification.jurisdiction_target,
            query_type=classification.query_type,
            requested_outputs=classification.requested_outputs,
            unsupported_action_warnings=classification.unsupported_actions,
        )

        await _emit_safe(
            emitter, "research_work_state", "Query Classified",
            f"Detected {state.jurisdiction_target.value} / {state.query_type.value} request.",
        )
        await _emit_safe(
            emitter, "run_started", "Research run started",
            f"Starting react_v2 engine | {state.jurisdiction_target.value} / {state.query_type.value}",
        )

        # Request a concise, agent-generated working summary and initial plan from the LLM.
        # This summary is authoritative: it describes what the agent intends to accomplish
        # and the initial actions it will take. The frontend will render this as the
        # first narrative node (not hardcoded on the client).
        try:
            plan_prompt = [
                {
                    "role": "system",
                    "content": (
                        "You are a professional legal research assistant.\n"
                        "Produce a concise JSON object with two keys: 'summary' and 'actions'.\n"
                        "- 'summary' should be one to two first-person sentences describing what you will try to accomplish for the user's query.\n"
                        "- 'actions' should be a short array (2-6 items) of concrete first actions you will take, e.g. 'Search Kenya Law for judgments on X', 'Open case Y', 'Extract holding from case Z'.\n"
                        "Do NOT include chain-of-thought or internal deliberation. Keep the language professional and user-facing."
                    ),
                },
                {"role": "user", "content": f"User query: {user_prompt}"},
            ]
            raw_plan = await self.llm.complete(plan_prompt, response_format="json", max_tokens=300)
            # Debug: log working plan raw result for diagnostics
            try:
                print(f"[controller] raw_plan={raw_plan}")
            except Exception:
                pass
            # raw_plan may be a Python dict (when the client auto-parses) or a JSON string
            if isinstance(raw_plan, str):
                try:
                    plan = json.loads(raw_plan)
                except Exception:
                    plan = {"summary": raw_plan.strip(), "actions": []}
            else:
                plan = raw_plan or {}

            working_summary = (plan.get("summary") or plan.get("summary_text") or "")
            actions = plan.get("actions") or plan.get("steps") or []
            if working_summary:
                await _emit_safe(emitter, "research_work_state", "Working Summary", working_summary)

            # Emit each planned action as a work_event so the frontend work panel shows the plan
            for act in actions[:6]:
                try:
                    await _emit_safe(emitter, "work_event", "Plan action", str(act))
                except Exception:
                    pass
        except Exception:
            # If the plan generation fails, fall back to a simple generated narrative
            try:
                # Deterministic fallback generated by the controller (still agent-originated)
                j = state.jurisdiction_target.value if state.jurisdiction_target else "the target jurisdiction"
                short_q = (state.normalized_query or user_prompt or "the query").strip()
                fallback_summary = (
                    f"I will search {j} sources for authorities relevant to '{short_q}', fetch the most relevant cases and statutes, "
                    "extract the key holdings, and then verify citations before drafting a concise answer."
                )
                fallback_actions = [
                    f"Search Kenya Law judgments and legislation for '{short_q}'",
                    "Run a broader web search for commentary and secondary sources",
                    "Open the most promising case documents and extract holdings",
                    "Verify citations and reconcile any conflicts",
                    "Draft the final answer and list sources",
                ]
                await _emit_safe(emitter, "research_work_state", "Working Summary", fallback_summary)
                for act in fallback_actions[:6]:
                    try:
                        await _emit_safe(emitter, "work_event", "Plan action", act)
                    except Exception:
                        pass
            except Exception:
                pass

        # ---- ReAct loop: LLM decides, controller executes ----
        while state.search_round < state.max_rounds:
            state.search_round += 1

            # 1. Ask LLM what to do next
            messages = build_orchestrator_prompt(state)
            raw = await self.llm.complete(messages, response_format="json", max_tokens=800)
            decision = parse_orchestrator_response(raw)

            if not decision:
                # LLM returned bad JSON — fall back to synthesize
                decision = {
                    "action": "synthesize_answer",
                    "parameters": {},
                    "reason": "Could not parse decision. Defaulting to synthesis.",
                }

            action_name = decision.get("action", "synthesize_answer")
            params = decision.get("parameters", {})
            reason = decision.get("reason", "")
            doc_title = decision.get("document_title", "")
            final_answer = decision.get("final_answer", "")

            # Emit narrative from the LLM's reasoning
            narrative = reason or _action_display_name(action_name)
            await _emit_safe(emitter, "research_work_state", "Research Step", narrative)

            # 2. Handle terminal actions
            if action_name == "synthesize_answer":
                if doc_title:
                    if doc_title.lower().endswith(".docx"):
                        doc_title = doc_title[:-5]
                    elif doc_title.lower().endswith(".pdf"):
                        doc_title = doc_title[:-4]
                    # Store the LLM's proposed document title for export
                    if state.classification and hasattr(state.classification, "requested_document_constraints"):
                        state.classification.requested_document_constraints["llm_title"] = doc_title
                if final_answer:
                    state.final_answer_draft = final_answer
                break

            if action_name == "stop_with_gaps":
                gap_reason = params.get("reason", reason) or "No reliable sources found."
                state.coverage_report.unresolved_gaps.append(gap_reason)
                if final_answer:
                    state.final_answer_draft = final_answer
                break

            # 3. Execute search tools
            if action_name in (
                "kenyalaw_legislation_search",
                "kenyalaw_judgment_search",
                "official_kenya_domain_search",
                "brave_search_fallback",
                "kenya_law_case_resolve",
                "case_specific_search",
            ):
                query = params.get("query", state.normalized_query)
                # Convert action name to ReActActionType
                from app.schemas.research_state import ReActAction, ReActActionType, DocumentType
                try:
                    atype = ReActActionType(action_name)
                except ValueError:
                    continue

                action = ReActAction(action=atype, query=query, reason=reason)
                state.coverage_report.attempted_tools.append(action_name)

                candidates = await self.search_adapters.execute_search(action)
                if not candidates:
                    state.coverage_report.failed_fetches += 1
                    state.coverage_report.fallback_reasons.append(
                        f"{action_name} returned no candidates."
                    )
                    # Don't emit anything — LLM will see the empty result next round
                    continue

                state.source_candidates.extend(candidates)
                # Emit a brief work event to show search results were found
                try:
                    await _emit_safe(
                        emitter,
                        "source_found",
                        "Search results",
                        f"Found {len(candidates)} candidate(s) for query.",
                        payload={
                            "count": len(candidates),
                            "samples": [
                                {"title": c.get("title") if isinstance(c, dict) else "", "url": c.get("url") if isinstance(c, dict) else ""}
                                for c in (candidates[:4] if isinstance(candidates, list) else [])
                            ],
                        },
                    )
                except Exception:
                    pass
                # The LLM will see candidates in the next prompt and decide what to fetch
                continue

            # 4. Execute fetch_url — LLM specified a URL to read
            if action_name == "fetch_url":
                url = params.get("url", "")
                title = params.get("title", "Untitled")
                if not url:
                    continue

                # Sanitize URL
                url = url.strip().rstrip(".,;:!?)]}>")

                # Infer document type from URL
                url_lower = url.lower()
                if "/judgment/" in url_lower:
                    inferred_doc_type = DocumentType.JUDGMENT
                elif "/act/" in url_lower or "/statute/" in url_lower:
                    inferred_doc_type = DocumentType.STATUTE
                elif "/legislation/" in url_lower:
                    inferred_doc_type = DocumentType.STATUTE
                else:
                    inferred_doc_type = DocumentType.UNKNOWN

                # Try extraction ladder
                text = None
                fetch_method = "none"
                # Notify frontend that the controller is about to read the source
                try:
                    await _emit_safe(
                        emitter,
                        "reading_source",
                        "Reading source",
                        f"Reading: {title}",
                        payload={"url": url, "title": title},
                    )
                except Exception:
                    pass
                try:
                    import httpx, io, re
                    _headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    }
                    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:

                        # Ladder 1: Kenya Law source DOCX (/source)
                        if "new.kenyalaw.org" in url_lower and "/akn/" in url_lower:
                            source_url = url.rstrip("/") + "/source"
                            sr = await client.get(source_url, headers=_headers)
                            if sr.status_code == 200 and len(sr.text) > 200:
                                try:
                                    from docx import Document
                                    doc = Document(io.BytesIO(sr.content))
                                    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
                                    if text.strip():
                                        fetch_method = "kenyalaw_source_docx"
                                except Exception:
                                    pass

                        # Ladder 2: Kenya Law source PDF (/source.pdf)
                        if not text and "new.kenyalaw.org" in url_lower and "/akn/" in url_lower:
                            pdf_url = url.rstrip("/") + "/source.pdf"
                            pr = await client.get(pdf_url, headers=_headers)
                            if pr.status_code == 200 and len(pr.content) > 500:
                                try:
                                    from pypdf import PdfReader
                                    reader = PdfReader(io.BytesIO(pr.content))
                                    pdf_text = []
                                    for page in reader.pages:
                                        t = page.extract_text()
                                        if t:
                                            pdf_text.append(t)
                                    text = "\n".join(pdf_text)
                                    if text.strip():
                                        fetch_method = "kenyalaw_source_pdf"
                                except Exception:
                                    pass

                        # Ladder 3: Standard HTTP fetch + HTML text extraction
                        if not text:
                            resp = await client.get(url, headers=_headers)
                            if resp.status_code == 200:
                                raw_html = resp.text
                                # Look for document-content div first
                                doc_match = re.search(
                                    r'<div[^>]*id=[\'"]document-content[\'"][^>]*>(.*?)</div>',
                                    raw_html, re.DOTALL | re.IGNORECASE
                                )
                                if doc_match:
                                    text = re.sub(r'<[^>]+>', ' ', doc_match.group(1))
                                else:
                                    text = re.sub(r'<[^>]+>', ' ', raw_html)
                                text = re.sub(r'\s+', ' ', text).strip()
                                fetch_method = "http_html"

                        # Ladder 4: Playwright full render (last resort)
                        if not text or len(text) < 200:
                            try:
                                from playwright.async_api import async_playwright
                                async with async_playwright() as pw:
                                    browser = await pw.chromium.launch(headless=True)
                                    page = await browser.new_page()
                                    await page.goto(url, wait_until="networkidle")
                                    await page.wait_for_timeout(3000)
                                    body_text = await page.inner_text("body")
                                    await browser.close()
                                    if body_text and len(body_text) > len(text or ""):
                                        text = body_text
                                        fetch_method = "playwright"
                            except Exception:
                                pass

                except Exception as fetch_err:
                    state.coverage_report.failed_fetches += 1
                    state.coverage_report.fallback_reasons.append(
                        f"Fetch failed for {title}: {fetch_err}"
                    )
                    continue

                if text and len(text.strip()) > 50:
                    role, strength, limitations = self.source_evaluator.evaluate_authority(
                        url=url,
                        raw_content=text[:5000],
                        doc_type=inferred_doc_type,
                        jurisdiction_hint=state.jurisdiction_target.value,
                    )
                    state.evidence_ledger.append(EvidenceItem(
                        source_title=title,
                        url=url,
                        jurisdiction=state.jurisdiction_target.value,
                        basis_role=role,
                        basis_strength=strength,
                        passage=text[:2500],
                        limitations=limitations,
                        discovered_by="direct_url_fetch",
                        fetched_by=fetch_method,
                        parsed_by=fetch_method,
                    ))
                    state.coverage_report.successful_fetches += 1
                    # Emit evidence_created so the UI work panel can show extracted evidence
                    try:
                        await _emit_safe(
                            emitter,
                            "evidence_created",
                            "Evidence extracted",
                            f"Extracted evidence from {title}",
                            payload={"title": title, "url": url, "excerpt": (text or "")[:200]},
                        )
                    except Exception:
                        pass
                else:
                    state.coverage_report.failed_fetches += 1
                    state.coverage_report.fallback_reasons.append(
                        f"No usable text extracted from {title} via {fetch_method}"
                    )
                    try:
                        await _emit_safe(
                            emitter,
                            "source_unreadable",
                            "Source unreadable",
                            f"Could not extract readable text from {title}",
                            payload={"url": url, "title": title},
                        )
                    except Exception:
                        pass
                continue

                # Sanitize URL — remove any trailing non-URL characters
                url = url.strip().rstrip(".,;:!?)]}>")
                import urllib.parse
                # If URL has spaces or special chars, encode them
                parsed = urllib.parse.urlparse(url)
                if any(c in parsed.path for c in " \t\n"):
                    # Re-encode the path component
                    path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/@-_.~")
                    url = urllib.parse.urlunparse(parsed._replace(path=path))

                # Use direct HTTP fetch (Playwright is unreliable on this platform)
                try:
                    import httpx
                    # Infer document type from URL
                    url_lower = url.lower()
                    if "/judgment/" in url_lower:
                        inferred_doc_type = DocumentType.JUDGMENT
                    elif "/act/" in url_lower or "/statute/" in url_lower:
                        inferred_doc_type = DocumentType.STATUTE
                    elif "/legislation/" in url_lower:
                        inferred_doc_type = DocumentType.STATUTE
                    else:
                        inferred_doc_type = DocumentType.UNKNOWN

                    _fetch_headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.5",
                    }
                    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                        resp = await client.get(url, headers=_fetch_headers)
                        if resp.status_code == 200:
                            text = resp.text
                            role, strength, limitations = self.source_evaluator.evaluate_authority(
                                url=url,
                                raw_content=text,
                                doc_type=inferred_doc_type,
                                jurisdiction_hint=state.jurisdiction_target.value,
                            )
                            state.evidence_ledger.append(EvidenceItem(
                                source_title=title,
                                url=url,
                                jurisdiction=state.jurisdiction_target.value,
                                basis_role=role,
                                basis_strength=strength,
                                passage=text[:2500],
                                limitations=limitations,
                                discovered_by="direct_url_fetch",
                                fetched_by="httpx",
                                parsed_by="html_extract",
                            ))
                            state.coverage_report.successful_fetches += 1
                            try:
                                await _emit_safe(
                                    emitter,
                                    "evidence_created",
                                    "Evidence extracted",
                                    f"Extracted evidence from {title}",
                                    payload={"title": title, "url": url, "excerpt": (text or "")[:200]},
                                )
                            except Exception:
                                pass
                        else:
                            state.coverage_report.failed_fetches += 1
                            msg = f"HTTP {resp.status_code} for {title}"
                            print(f"[fetch_url] {msg}: {url[:120]}")
                            state.coverage_report.fallback_reasons.append(msg)
                except Exception as fetch_err:
                    state.coverage_report.failed_fetches += 1
                    msg = f"Fetch failed for {title}: {fetch_err}"
                    print(f"[fetch_url] {msg}")
                    state.coverage_report.fallback_reasons.append(msg)
                # The LLM will see the result in the next prompt
                continue

            # Unknown action — let LLM see it failed next round
            state.coverage_report.fallback_reasons.append(
                f"Unknown action requested: {action_name}"
            )

        # ---- Synthesis ----
        # If the orchestrator already provided a final_answer, use it directly.
        # Otherwise fall back to the synthesizer.
        if not state.final_answer_draft:
            await _emit_safe(
                emitter, "research_work_state", "Answer Synthesis",
                "Preparing grounded answer from fetched evidence.",
            )
            state.final_answer_draft = await self.synthesizer.synthesize(state)

        # Emit answer as a single token chunk
        if emitter is not None:
            token_result = emitter.emit(
                "answer_token", "Token", "Streaming",
                payload={"token": state.final_answer_draft or ""},
                visibility="internal",
            )
            # Debug: ensure answer_token emission is observable in server logs
            try:
                _tok = token_result.get("payload", {}).get("token", "") if isinstance(token_result, dict) else ""
                print(f"[controller] EMITTED answer_token payload_len={len(_tok)}")
            except Exception:
                pass
            if inspect.isawaitable(token_result):
                await token_result

        # Proactively expose final drafted answer to the frontend UI (fallback)
        if emitter is not None and state.final_answer_draft:
            await _emit_safe(
                emitter,
                "answer_replaced",
                "Answer ready",
                state.final_answer_draft or "",
                payload={"answer": state.final_answer_draft or ""},
            )

        # ---- Document export (if requested) ----
        if OutputFormat.DOCX in state.requested_outputs or OutputFormat.PDF in state.requested_outputs:
            await _emit_safe(
                emitter, "document_work_state", "Document Export",
                "Document export was requested. Building DocumentSpec from final answer and evidence.",
            )
            try:
                if self.new_document_export_skill is not None:
                    # Use LLM-proposed title if available
                    llm_title = ""
                    if state.classification and state.classification.requested_document_constraints:
                        llm_title = state.classification.requested_document_constraints.get("llm_title", "")
                    export_title = llm_title or f"Sheria Legal Research - {state.jurisdiction_target.value}"

                    export_result = await self.new_document_export_skill.run(
                        user_request=state.normalized_query,
                        grounded_answer=state.final_answer_draft or "",
                        title=export_title,
                    )
                    await _emit_safe(
                        emitter, "document_export_finished",
                        "Document Export Complete",
                        f"DOCX: {export_result.docx_path}" if export_result.docx_path else "Export completed",
                        payload={
                            "document_id": export_result.document_id,
                            "docx_path": export_result.docx_path,
                            "pdf_path": export_result.pdf_path,
                            "manifest_path": export_result.artifact_manifest_path,
                            "warnings": export_result.warnings,
                            "ok": export_result.ok,
                            "constraint_report": (
                                export_result.constraint_report.model_dump(mode="json")
                                if export_result.constraint_report and hasattr(export_result.constraint_report, "model_dump")
                                else None
                            ),
                        },
                    )
                else:
                    result = await self.old_document_export_skill.export_from_state(state, emitter=emitter)
                    if result.ok:
                        await _emit_safe(
                            emitter, "document_export_finished",
                            "Document Export Complete",
                            f"Generated: {', '.join(result.generated_files)}",
                            state_summary=f"Document compiled successfully: {result.title}",
                            payload={
                                "artifact_id": result.artifact_id,
                                "generated_files": result.generated_files,
                                "warnings": result.warnings,
                            },
                        )
                    else:
                        state.warnings.extend(result.warnings)
                        await _emit_safe(emitter, "error", "Document Export Failed", "; ".join(result.warnings))
            except Exception as exc:
                state.warnings.append(f"Document export failed: {exc}")
                await _emit_safe(emitter, "error", "Document Export Error", str(exc))

        await _emit_safe(
            emitter, "run_finished", "Done",
            f"react_v2 completed {state.search_round} rounds, "
            f"{state.coverage_report.successful_fetches} fetches.",
        )
        return state
