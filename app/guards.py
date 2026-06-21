import re
from dataclasses import dataclass

from .schemas import EvidenceCard


@dataclass
class GuardResult:
    status: str
    passed: bool
    notes: list[str]
    cited_sources: list[str]
    missing_sources: list[str]


SOURCE_RE = re.compile(r"\[S(\d+)\]")


def verify_answer(answer: str, evidence: list[EvidenceCard], strict_mode: bool = True) -> GuardResult:
    known = {card.source_id for card in evidence}
    cited = sorted({f"S{m}" for m in SOURCE_RE.findall(answer)}, key=lambda s: int(s[1:]))

    notes: list[str] = []
    missing = [sid for sid in cited if sid not in known]

    if missing:
        notes.append(f"Answer cites unknown source ids: {', '.join(missing)}.")

    if strict_mode and not cited:
        notes.append("No source citations found. Strict mode requires citations like [S1].")

    if strict_mode and "insufficient" not in answer.lower() and "cannot verify" not in answer.lower():
        claim_lines = [
            line for line in answer.splitlines()
            if line.strip()
            and not line.strip().startswith(("#", "-", "*"))
            and len(line.strip()) > 80
        ]
        uncited_claim_lines = [line for line in claim_lines if not SOURCE_RE.search(line)]
        if uncited_claim_lines:
            notes.append(
                f"{len(uncited_claim_lines)} substantial paragraph(s) appear uncited."
            )

    passed = not missing and (bool(cited) or not strict_mode) and not any("uncited" in note for note in notes)

    if passed:
        status = "verified"
    elif cited and not missing:
        status = "partially_verified"
    else:
        status = "needs_repair"

    return GuardResult(
        status=status,
        passed=passed,
        notes=notes,
        cited_sources=cited,
        missing_sources=missing,
    )


def build_repair_prompt(answer: str, guard: GuardResult, evidence: list[EvidenceCard]) -> str:
    evidence_text = "\n\n".join(
        f"[{card.source_id}] {card.title}\nURL: {card.url}\nExcerpt:\n{card.excerpt}"
        for card in evidence
    )

    return f"""
The previous answer failed citation verification.

Guard status: {guard.status}
Guard notes:
{chr(10).join("- " + note for note in guard.notes)}

Repair rules:
- Keep only claims supported by the evidence below.
- Every factual or legal claim must cite one or more sources like [S1].
- If evidence is insufficient, say exactly what cannot be verified.
- Do not invent cases, statutes, URLs, facts, or dates.

Evidence:
{evidence_text}

Previous answer:
{answer}

Return the repaired final answer only.
""".strip()
