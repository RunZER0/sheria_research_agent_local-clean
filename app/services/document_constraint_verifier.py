from app.schemas.document_spec import DocumentSpec
from app.services.word_count_service import WordCountService


class DocumentConstraintVerifier:
    @staticmethod
    def verify_spec_limits(spec: DocumentSpec) -> dict:
        """Pre-rendering constraint validation."""
        failures = []
        actual_words = WordCountService.verify_spec(spec)
        wc_constraint = spec.constraints.word_count

        if wc_constraint.mode == "exact":
            delta = actual_words - wc_constraint.target
            if abs(delta) > wc_constraint.tolerance:
                failures.append({
                    "code": "WORD_COUNT_MISMATCH",
                    "severity": "error",
                    "message": (
                        f"Deterministic count returned {actual_words} words. "
                        f"Target is exactly {wc_constraint.target}."
                    ),
                    "expected": wc_constraint.target,
                    "actual": actual_words
                })

        if wc_constraint.mode == "max" and actual_words > wc_constraint.target:
            failures.append({
                "code": "WORD_COUNT_EXCEEDS_MAX",
                "severity": "error",
                "message": (
                    f"Deterministic count returned {actual_words} words. "
                    f"Maximum allowed is {wc_constraint.target}."
                ),
                "expected": wc_constraint.target,
                "actual": actual_words
            })

        if wc_constraint.mode == "min" and actual_words < wc_constraint.target:
            failures.append({
                "code": "WORD_COUNT_BELOW_MIN",
                "severity": "error",
                "message": (
                    f"Deterministic count returned {actual_words} words. "
                    f"Minimum required is {wc_constraint.target}."
                ),
                "expected": wc_constraint.target,
                "actual": actual_words
            })

        return {"ok": len(failures) == 0, "failures": failures}

    @staticmethod
    def verify_hardcopy_pdf(pdf_path: str, spec: DocumentSpec) -> dict:
        """Post-rendering execution check targeting page and layout integrity."""
        failures = []
        try:
            import pypdf
            reader = pypdf.PdfReader(pdf_path)
            total_pages = len(reader.pages)
            page_constraint = spec.constraints.page_count

            if page_constraint.mode == "exact_pages" and total_pages != page_constraint.value:
                failures.append({
                    "code": "PAGE_COUNT_MISMATCH",
                    "severity": "error",
                    "message": (
                        f"Rendered hardcopy file spans {total_pages} pages. "
                        f"Constraint dictates exactly {page_constraint.value}."
                    ),
                    "expected": page_constraint.value,
                    "actual": total_pages
                })

            if page_constraint.mode == "max_pages" and total_pages > page_constraint.value:
                failures.append({
                    "code": "PAGE_COUNT_EXCEEDS_MAX",
                    "severity": "error",
                    "message": (
                        f"Rendered hardcopy file spans {total_pages} pages. "
                        f"Maximum allowed is {page_constraint.value}."
                    ),
                    "expected": page_constraint.value,
                    "actual": total_pages
                })

            if page_constraint.mode == "min_pages" and total_pages < page_constraint.value:
                failures.append({
                    "code": "PAGE_COUNT_BELOW_MIN",
                    "severity": "error",
                    "message": (
                        f"Rendered hardcopy file spans {total_pages} pages. "
                        f"Minimum required is {page_constraint.value}."
                    ),
                    "expected": page_constraint.value,
                    "actual": total_pages
                })

            # Check page-specific intent compliance
            for plan_item in spec.constraints.page_plan:
                if plan_item.page_number <= total_pages:
                    page_text = reader.pages[plan_item.page_number - 1].extract_text()
                    for text_trigger in plan_item.must_include_text:
                        if text_trigger.lower() not in page_text.lower():
                            failures.append({
                                "code": "PAGE_CONTENT_MISSING",
                                "severity": "error",
                                "message": (
                                    f"Page {plan_item.page_number} layout missing "
                                    f"required ground anchor: '{text_trigger}'"
                                ),
                                "block_id": None
                            })
        except ImportError:
            failures.append({
                "code": "PDF_CORRUPT",
                "severity": "error",
                "message": "pypdf is not installed. Cannot verify PDF output."
            })
        except Exception as e:
            failures.append({"code": "PDF_CORRUPT", "severity": "error", "message": str(e)})

        return {"ok": len(failures) == 0, "failures": failures}
