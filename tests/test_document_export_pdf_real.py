import shutil

import pytest

from app.schemas.document_export import (
    DocumentConstraints,
    DocumentExportRequest,
    OutputFormat,
    PageCountConstraint,
    PageCountMode,
)
from app.services.document_export_service import DocumentExportService


@pytest.mark.document_export
@pytest.mark.libreoffice
@pytest.mark.asyncio
async def test_real_pdf_conversion_if_libreoffice_available(tmp_path):
    if not (shutil.which("soffice") or shutil.which("libreoffice")):
        pytest.skip("LibreOffice/soffice not installed")

    service = DocumentExportService(
        artifact_root=tmp_path,
        revision_callback=None,
    )

    request = DocumentExportRequest(
        user_request="Create DOCX and PDF.",
        title="PDF Conversion Test",
        answer_text="This is a short legal document.",
        output_formats=[OutputFormat.docx, OutputFormat.pdf],
    )

    result = await service.export(request)

    assert result.ok is True
    assert result.docx_path is not None
    assert result.pdf_path is not None
