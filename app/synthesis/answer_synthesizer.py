from __future__ import annotations

from app.schemas.research_state import BasisStrength, ResearchState


class AnswerSynthesizer:
    def __init__(self, llm_client=None) -> None:
        self.llm = llm_client

    async def synthesize(self, state: ResearchState) -> str:
        if not state.evidence_ledger:
            return self._no_evidence_answer(state)

        evidence_summary = "\n\n".join(
            f"[{i+1}] {ev.source_title}\n"
            f"URL: {ev.url}\n"
            f"Role: {ev.basis_role.value}\n"
            f"Strength: {ev.basis_strength.value}\n"
            f"Passage: {ev.passage[:1800]}"
            for i, ev in enumerate(state.evidence_ledger)
        )

        # Build source gap warnings for unreadable or weak sources
        gap_warnings = []
        for ev in state.evidence_ledger:
            if ev.basis_strength == BasisStrength.UNREADABLE:
                gap_warnings.append(
                    f"- {ev.source_title}: candidate was found at {ev.url} but could not be read; "
                    "no direct primary text was verified."
                )
            elif ev.basis_strength in {BasisStrength.WEAK, BasisStrength.PERSUASIVE} and ev.limitations:
                gap_warnings.append(
                    f"- {ev.source_title}: {ev.limitations[0]}"
                )
        gap_text = "\n".join(gap_warnings)

        if self.llm is None:
            return self._template_answer(state)

        prompt = f"""
You are Sheria Research Agent. Answer the user's legal question using only the evidence below.

Rules:
- State the jurisdiction clearly.
- Use primary sources first.
- Do not cite sources that are unreadable.
- If evidence is weak or incomplete, say so explicitly.
- If any source was found but could not be read, note it as a limitation.
- Show a short Source Basis section.
- Do not invent cases or statutes.
- Do not mention internal chain-of-thought.

User question:
{state.normalized_query}

Evidence:
{evidence_summary}

{f"Source Limitations:\n{gap_text}" if gap_text else ""}
""".strip()

        try:
            answer = await self.llm.complete([{"role": "user", "content": prompt}], max_tokens=1800)
            return answer.strip()
        except Exception as exc:
            import logging
            logging.getLogger("sheria.synthesis").warning(
                "LLM synthesize failed: %s", exc
            )
            return self._template_answer(state)

    def _template_answer(self, state: ResearchState) -> str:
        lines = [
            f"Jurisdiction: {state.jurisdiction_target.value}",
            "",
            "I found the following usable source basis:",
        ]
        for ev in state.evidence_ledger:
            lines.append(f"- {ev.source_title} — {ev.basis_role.value} — {ev.basis_strength.value}")
        lines.extend([
            "",
            "A full narrative answer could not be synthesized because the LLM synthesizer was unavailable.",
        ])
        return "\n".join(lines)

    def _no_evidence_answer(self, state: ResearchState) -> str:
        gaps = state.coverage_report.unresolved_gaps or [
            "No reliable readable source was found in the bounded search loop."
        ]
        return (
            f"Jurisdiction: {state.jurisdiction_target.value}\n\n"
            "I could not verify a reliable source within the bounded research loop.\n\n"
            "Unresolved gaps:\n"
            + "\n".join(f"- {gap}" for gap in gaps)
            + "\n\nThe answer should be treated as provisional until primary or official sources are located."
        )
