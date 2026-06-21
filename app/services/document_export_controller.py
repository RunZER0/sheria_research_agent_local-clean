import os
import json
from typing import AsyncIterator, Optional

from app.schemas.document_spec import DocumentSpec
from app.services.document_constraint_verifier import DocumentConstraintVerifier
from app.renderers.docx_renderer import DocxRenderer
from app.renderers.pdf_renderer import PdfRenderer
from app.events import EventEmitter


class DocumentExportController:
    def __init__(self, planner_agent, content_draft_agent, revision_agent, settings):
        self.planner = planner_agent
        self.drafter = content_draft_agent
        self.reviser = revision_agent
        self.settings = settings

    async def execute_export_pipeline(
        self, user_request: str, output_dir: str, emitter: EventEmitter
    ) -> AsyncIterator[dict]:
        """
        Executes the full document export pipeline:
        1. Structural planning → DocumentSpec skeleton
        2. Content drafting with grounded legal references
        3. Bounded verification loop (up to 6 revisions)
        4. Final artifact manifest
        """
        # 1. Structural Planning & Intent Extraction
        yield emitter.emit(
            "document_work_state", "Planning Document",
            "Compiling DocumentSpec block skeleton and styling matrix.",
            state_summary="Planning document structure and layout constraints."
        )

        spec: DocumentSpec = await self.planner.plan_spec(user_request)

        # Two-pass initialization: adjust target downwards for safety margins if exact limits exist
        original_target = None
        if spec.constraints.word_count.mode == "exact":
            original_target = spec.constraints.word_count.target
            spec.constraints.word_count.target = int(original_target * 0.96)

        yield emitter.emit(
            "document_work_state", "Drafting Content",
            "Hydrating document block elements with grounded legal references.",
            state_summary="Drafting content with source-grounded citations."
        )
        spec = await self.drafter.draft_initial_content(spec)

        # Reset target back to strict constraint value for loop verification evaluations
        if original_target is not None:
            spec.constraints.word_count.target = original_target

        # 2. Bounded Verification Loop Execution (Max 6 attempts)
        MAX_REVISIONS = 6
        os.makedirs(output_dir, exist_ok=True)
        docx_path = os.path.join(output_dir, "draft_output.docx")
        pdf_path: Optional[str] = None

        for attempt in range(1, MAX_REVISIONS + 1):
            yield emitter.emit(
                "document_work_state", "Verifying Limits",
                f"Verification Pass [{attempt}/{MAX_REVISIONS}]: Analyzing constraints.",
                state_summary=f"Running pass {attempt} of {MAX_REVISIONS} structural checks."
            )

            # Run pre-render check
            spec_report = DocumentConstraintVerifier.verify_spec_limits(spec)
            if not spec_report["ok"]:
                yield emitter.emit(
                    "document_work_state", "Revising Content",
                    f"Word count delta error detected. Invoking Revision subagent.",
                    payload={"failures": spec_report["failures"]}
                )
                spec = await self.reviser.execute_revisions(spec, spec_report["failures"])
                continue

            # Render physical artifacts
            DocxRenderer.render(spec, docx_path)

            if "pdf" in spec.output_formats:
                try:
                    pdf_path = PdfRenderer.from_docx(docx_path, output_dir)
                    pdf_report = DocumentConstraintVerifier.verify_hardcopy_pdf(pdf_path, spec)

                    if not pdf_report["ok"]:
                        yield emitter.emit(
                            "document_work_state", "Revising Layout",
                            f"Page constraint mismatch on hardcopy output. Refining block density.",
                            payload={"failures": pdf_report["failures"]}
                        )
                        spec = await self.reviser.execute_revisions(spec, pdf_report["failures"])
                        continue
                except RuntimeError as e:
                    yield emitter.emit(
                        "document_work_state", "Rendering Warning",
                        f"PDF rendering skipped: {str(e)}",
                        state_summary="PDF output unavailable; DOCX generated successfully."
                    )
                    break
                except Exception as e:
                    yield emitter.emit("error", "Rendering Fault", str(e))
                    break

            # If all validations clear cleanly, break out of loop immediately
            yield emitter.emit(
                "document_work_state", "Compilation Successful",
                "All constraints verified. Writing final artifact manifest.",
                state_summary="Document compiled successfully with all constraints satisfied."
            )
            yield emitter.emit(
                "document_work_finished", "Document Ready",
                "Output files are available for download.",
                payload=self._build_manifest(spec, docx_path, pdf_path, success=True)
            )
            return

        # Loop exhausted without success
        yield emitter.emit(
            "document_work_finished", "Document Ready (with warnings)",
            "Constraints unmet within revision bounds.",
            state_summary="Document generated with warnings — some constraints could not be satisfied.",
            payload=self._build_manifest(
                spec, docx_path, pdf_path,
                success=False,
                warning="Constraints unmet within revision bounds."
            )
        )

    def _build_manifest(
        self,
        spec: DocumentSpec,
        docx: str,
        pdf: Optional[str],
        success: bool,
        warning: str = ""
    ) -> dict:
        generated = [os.path.basename(f) for f in [docx, pdf] if f and os.path.exists(f)]
        return {
            "artifact_id": f"doc_{os.getpid()}",
            "title": spec.title,
            "document_type": spec.document_type,
            "verification_passed": success,
            "warning": warning,
            "generated_outputs": generated,
            "source_basis": [m.model_dump() for m in spec.source_manifest]
        }
