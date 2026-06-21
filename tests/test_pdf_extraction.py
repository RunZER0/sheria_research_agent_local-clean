import pytest
from app.tool_router import ToolRouter
from app.config import Settings

@pytest.mark.asyncio
async def test_real_kenya_law_pdf_extraction():
    # 1. Initialize router with default settings
    settings = Settings()
    # Provide a dummy Brave key so the ToolRouter initializes cleanly.
    settings.brave_api_key = "test"
    router = ToolRouter(settings)
    
    # 2. Target a known, static Kenya Law PDF (Employment Act Cap 226)
    target_url = "http://www.kenyalaw.org/kl/fileadmin/pdfdownloads/Acts/EmploymentAct_Cap226-No11of2007_01.pdf"
    
    # 3. Execute the extraction
    extracted_text = await router.pdf_text_extract(target_url)
    
    # 4. Assertions
    assert extracted_text is not None, "Extractor returned None instead of string."
    assert len(extracted_text) > 500, f"Extraction failed or returned too little text. Length: {len(extracted_text)}"
    assert "Employment" in extracted_text or "EMPLOYMENT" in extracted_text, "Failed to parse the actual content of the document."
    
    print("\n--- EXTRACTION SUCCESS ---")
    print(f"Extracted {len(extracted_text)} characters.")
    print(f"Preview: {extracted_text[:200]}...")
