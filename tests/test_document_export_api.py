import pytest
from fastapi.testclient import TestClient

from app.main import app


class FakeDocumentExportResult:
    def model_dump(self, mode="json"):
        return {
            "ok": True,
            "document_id": "doc_test",
            "docx_path": "data/artifacts/generated/doc_test/doc_test.docx",
            "pdf_path": None,
            "artifact_manifest_path": "data/artifacts/generated/doc_test/artifact_manifest.json",
            "constraint_report": {
                "ok": True,
                "failures": [],
            },
            "warnings": [],
        }


class FakeDocumentExportService:
    async def export(self, payload):
        return FakeDocumentExportResult()


@pytest.mark.document_export
def test_export_document_endpoint(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(
        main_module,
        "document_export_service",
        FakeDocumentExportService(),
    )

    client = TestClient(app)

    response = client.post(
        "/api/documents/export",
        json={
            "user_request": "Return as DOCX.",
            "title": "API Test",
            "answer_text": "This is the answer.",
            "output_formats": ["docx"],
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["ok"] is True
    assert data["document_id"] == "doc_test"
    assert data["docx_path"].endswith(".docx")


@pytest.mark.document_export
def test_download_document_rejects_path_traversal():
    client = TestClient(app)

    response = client.get("/api/documents/download/../../bad/file.docx")

    assert response.status_code in {400, 404, 405}
