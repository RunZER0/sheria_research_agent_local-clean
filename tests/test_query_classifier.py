import pytest

from app.routing.query_classifier import LegalQueryClassifier
from app.schemas.research_state import JurisdictionTarget, QueryType, OutputFormat


class FakeClassifyLLM:
    def __init__(self, response_json: str):
        self.response_json = response_json

    async def complete(self, messages, **kwargs):
        return self.response_json


class FakeLLMFromCallable:
    def __init__(self, fn):
        self.fn = fn

    async def complete(self, messages, **kwargs):
        return self.fn(messages)


def make_classifier(json_response: str) -> LegalQueryClassifier:
    return LegalQueryClassifier(llm_client=FakeClassifyLLM(json_response))


@pytest.mark.asyncio
async def test_non_kenyan_query_not_forced_to_kenya():
    classifier = make_classifier(
        '{"normalized_query": "Explain promissory estoppel under English law.", "jurisdiction_target": "foreign", "query_type": "theory", "requested_outputs": ["chat"], "requested_document_constraints": {}, "confidence": 0.9}'
    )
    c = await classifier.classify("Explain promissory estoppel under English law.")
    assert c.jurisdiction_target == JurisdictionTarget.FOREIGN
    assert c.query_type == QueryType.THEORY


@pytest.mark.asyncio
async def test_kenyan_statute_query_classified_correctly():
    classifier = make_classifier(
        '{"normalized_query": "Explain section 45 of the Employment Act in Kenya.", "jurisdiction_target": "kenya", "query_type": "statute", "requested_outputs": ["chat"], "requested_document_constraints": {}, "confidence": 0.9}'
    )
    c = await classifier.classify("Explain section 45 of the Employment Act in Kenya.")
    assert c.jurisdiction_target == JurisdictionTarget.KENYA
    assert c.query_type == QueryType.STATUTE


@pytest.mark.asyncio
async def test_docx_constraints_detected():
    classifier = make_classifier(
        '{"normalized_query": "Return your answer as a DOCX of 4 pages, Times New Roman, font size 12, 1.5 spacing.", "jurisdiction_target": "general", "query_type": "document_only", "requested_outputs": ["docx"], "requested_document_constraints": {"page_count": {"mode": "exact", "value": 4}, "font_family": "times new roman", "font_size_pt": 12, "line_spacing": 1.5}, "confidence": 0.85}'
    )
    c = await classifier.classify(
        "Return your answer as a DOCX of 4 pages, Times New Roman, font size 12, 1.5 spacing."
    )
    assert OutputFormat.DOCX in c.requested_outputs
    assert c.requested_document_constraints.get("page_count", {}).get("value") == 4
    assert c.requested_document_constraints.get("font_family") == "times new roman"
    assert c.requested_document_constraints.get("font_size_pt") == 12
    assert c.requested_document_constraints.get("line_spacing") == 1.5


@pytest.mark.asyncio
async def test_fallback_when_llm_returns_empty():
    classifier = make_classifier("")
    c = await classifier.classify("Some random query.")
    assert c.jurisdiction_target == JurisdictionTarget.UNKNOWN
    assert c.query_type == QueryType.UNKNOWN
    assert c.confidence == 0.0


@pytest.mark.asyncio
async def test_general_query_not_forced_to_kenya():
    classifier = make_classifier(
        '{"normalized_query": "Explain the meaning of promissory estoppel.", "jurisdiction_target": "unknown", "query_type": "theory", "requested_outputs": ["chat"], "requested_document_constraints": {}, "confidence": 0.6}'
    )
    c = await classifier.classify("Explain the meaning of promissory estoppel.")
    assert c.jurisdiction_target == JurisdictionTarget.UNKNOWN


@pytest.mark.asyncio
async def test_word_count_detection():
    classifier = make_classifier(
        '{"normalized_query": "Write exactly 1000 words on legal ethics.", "jurisdiction_target": "general", "query_type": "theory", "requested_outputs": ["chat"], "requested_document_constraints": {"word_count": {"mode": "exact", "target": 1000, "scope": "body_only"}}, "confidence": 0.85}'
    )
    c = await classifier.classify("Write exactly 1000 words on legal ethics.")
    assert "word_count" in c.requested_document_constraints
    assert c.requested_document_constraints["word_count"].get("target") == 1000
