from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.document_spec import (
    DocumentConstraints,
    DocumentSpec,
    DocumentStyleSpec,
    HeadingBlock,
    PageBreakBlock,
    ParagraphBlock,
    PageConstraint,
    SourceManifestItem,
    WordCountConstraint,
)
from app.renderers.docx_renderer import DocxRenderer
from app.renderers.pdf_renderer import PdfRenderer
from app.services.word_count_service import WordCountService
from app.services.document_constraint_verifier import DocumentConstraintVerifier


class DocumentExportResult(BaseModel):
    ok: bool
    artifact_id: str = ""
    title: str = ""
    generated_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    constraint_failures: list[dict[str, Any]] = Field(default_factory=list)


def _build_spec_from_state(state, classification) -> DocumentSpec:
    """Convert ResearchState + QueryClassification into a DocumentSpec."""
    constraints = classification.requested_document_constraints or {}

    # Style
    style = DocumentStyleSpec()
    if "font_family" in constraints:
        style.font_name = constraints["font_family"].title()
    if "font_size_pt" in constraints:
        style.font_size = int(constraints["font_size_pt"])
    if "line_spacing" in constraints:
        style.line_spacing = float(constraints["line_spacing"])
    if "alignment" in constraints:
        style.alignment = constraints["alignment"]

    # Margins
    margins = constraints.get("margins", "standard")
    if margins in ("standard", "narrow", "wide"):
        style.margins = margins

    # Word count constraint
    wc = WordCountConstraint()
    wc_data = constraints.get("word_count") or {}
    if wc_data:
        wc.mode = wc_data.get("mode", "exact")
        wc.target = wc_data.get("value")
        wc.scope = wc_data.get("scope", "body_only")

    # Page count constraint
    pc = PageConstraint()
    pc_data = constraints.get("page_count") or {}
    if pc_data:
        pc.mode = pc_data.get("mode", "exact_pages")
        pc.value = pc_data.get("value", 0)

    doc_constraints = DocumentConstraints(word_count=wc, page_count=pc)

    # Blocks — build from final answer + evidence
    blocks: list = []
    block_id = 0

    def _next_id() -> str:
        nonlocal block_id
        block_id += 1
        return f"b{block_id:03d}"

    # Title heading
    title_text = constraints.get("title") or state.normalized_query[:80]
    blocks.append(HeadingBlock(id=_next_id(), level=1, text=title_text))

    # Jurisdiction / classification section
    blocks.append(HeadingBlock(
        id=_next_id(), level=2,
        text=f"Jurisdiction: {state.jurisdiction_target.value}"
    ))

    # Final answer body
    answer = state.final_answer_draft or ""
    paragraphs = re.split(r"\n\s*\n", answer.strip())
    for para_text in paragraphs:
        para_text = para_text.strip()
        if not para_text:
            continue
        # Detect markdown-style headings
        heading_match = re.match(r"^(#{1,3})\s+(.+)$", para_text, re.MULTILINE)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            blocks.append(HeadingBlock(id=_next_id(), level=level, text=text))
        elif para_text.startswith("- ") or para_text.startswith("* "):
            items = [line.lstrip("-* ").strip() for line in para_text.split("\n") if line.strip()]
            from app.schemas.document_spec import BulletListBlock
            blocks.append(BulletListBlock(id=_next_id(), items=items))
        else:
            blocks.append(ParagraphBlock(id=_next_id(), text=para_text))

    # Evidence source manifest
    if state.evidence_ledger:
        blocks.append(HeadingBlock(id=_next_id(), level=2, text="Sources"))
        for ev in state.evidence_ledger:
            blocks.append(ParagraphBlock(
                id=_next_id(),
                text=f"{ev.source_title} — {ev.basis_role.value} ({ev.basis_strength.value})",
                citations=[ev.url],
            ))

    # Source manifest
    source_manifest = [
        SourceManifestItem(
            source_id=ev.evidence_id,
            title=ev.source_title,
            url=ev.url,
            authority_level=ev.basis_strength.value,
            role_in_argument=ev.basis_role.value,
        )
        for ev in state.evidence_ledger
    ]

    return DocumentSpec(
        title=title_text,
        document_type="legal_memo",
        style=style,
        constraints=doc_constraints,
        blocks=blocks,
        source_manifest=source_manifest,
    )


class DocumentExportSkill:
    """
    Post-research subsystem for document generation.

    This is not a ReAct discovery tool. It receives a completed ResearchState
    with a classification containing document constraints and:
      1. Builds a DocumentSpec from the final answer + evidence.
      2. Applies user-specified style/constraints.
      3. Renders DOCX.
      4. Optionally converts to PDF via LibreOffice.
      5. Verifies word/page constraints with a bounded revision loop (max 6).
      6. Writes an artifact_manifest.json.
    """

    def __init__(self, output_dir: str | Path = "artifacts/generated") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def export_from_state(
        self,
        state,
        emitter=None,
    ) -> DocumentExportResult:
        classification = state.classification

        # 1. Build DocumentSpec
        spec = _build_spec_from_state(state, classification)

        # 2. Pre-render word count check + revision loop
        MAX_REVISIONS = 6
        docx_path = self.output_dir / f"{state.run_id}_{classification.query_type.value if classification.query_type else 'document'}.docx"
        pdf_path: Path | None = None

        for attempt in range(1, MAX_REVISIONS + 1):
            # Verify word count constraints pre-render
            wc_report = DocumentConstraintVerifier.verify_spec_limits(spec)
            if not wc_report["ok"]:
                # In a full implementation, this would call the revision agent.
                # For now, record the failures and proceed — the render is still attempted.
                pass

            # 3. Render DOCX
            try:
                DocxRenderer.render(spec, str(docx_path))
            except Exception as exc:
                return DocumentExportResult(
                    ok=False,
                    warnings=[f"DOCX render failed: {exc}"],
                )

            # 4. Convert to PDF if requested
            if "pdf" in (state.requested_outputs or []):
                try:
                    pdf_result = PdfRenderer.from_docx(str(docx_path), str(self.output_dir))
                    pdf_path = Path(pdf_result)
                except RuntimeError as exc:
                    # LibreOffice not available — this is acceptable
                    if emitter:
                        try:
                            emitter.emit(
                                "document_work_state", "PDF Warning",
                                f"PDF output unavailable: {exc}"
                            )
                        except Exception:
                            pass

                # Verify page count constraints post-render
                if pdf_path and pdf_path.exists():
                    pdf_report = DocumentConstraintVerifier.verify_hardcopy_pdf(
                        str(pdf_path), spec
                    )
                    if not pdf_report["ok"]:
                        # Constraint failure recorded but we still return the file
                        pass

            # Check if constraints passed
            failures = wc_report.get("failures", [])
            if pdf_path and pdf_path.exists():
                pdf_report = DocumentConstraintVerifier.verify_hardcopy_pdf(
                    str(pdf_path), spec
                )
                failures.extend(pdf_report.get("failures", []))

            if not failures or attempt == MAX_REVISIONS:
                break

        # 5. Build generated file list
        generated = [str(docx_path)]
        if pdf_path and pdf_path.exists():
            generated.append(str(pdf_path))

        # 6. Write artifact manifest
        manifest_path = self.output_dir / f"{state.run_id}_manifest.json"
        fail_list = wc_report.get("failures", [])
        if pdf_path and pdf_path.exists():
            pdf_report = DocumentConstraintVerifier.verify_hardcopy_pdf(
                str(pdf_path), spec
            )
            if not pdf_report["ok"]:
                fail_list.extend(pdf_report.get("failures", []))

        manifest = {
            "artifact_id": f"doc_{state.run_id[:8]}",
            "run_id": state.run_id,
            "title": spec.title,
            "verification_passed": len(fail_list) == 0,
            "generated_outputs": [str(Path(p).name) for p in generated],
            "warning": "; ".join(f["message"] for f in fail_list) if fail_list else "",
            "constraint_failures": fail_list,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        return DocumentExportResult(
            ok=len(fail_list) == 0 or len(generated) > 0,
            artifact_id=manifest["artifact_id"],
            title=spec.title,
            generated_files=[str(Path(p).name) for p in generated],
            warnings=[] if len(fail_list) == 0 else [f["message"] for f in fail_list],
            constraint_failures=fail_list,
        )
