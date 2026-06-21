from app.evidence.source_basis_evaluator import SourceBasisEvaluator
from app.schemas.research_state import BasisStrength, DocumentType


def test_official_judiciary_pdf_can_be_strong_even_if_discovered_by_brave():
    evaluator = SourceBasisEvaluator()
    role, strength, limitations = evaluator.evaluate_authority(
        "https://www.judiciary.go.ke/example.pdf",
        "Republic of Kenya High Court Judgment...",
        DocumentType.JUDGMENT,
    )
    assert strength == BasisStrength.STRONG


def test_unreadable_source_not_authority():
    evaluator = SourceBasisEvaluator()
    role, strength, limitations = evaluator.evaluate_authority(
        "https://new.kenyalaw.org/example",
        "",
        DocumentType.JUDGMENT,
    )
    assert strength.value in {"unreadable", "weak"}


def test_kenya_law_statute_is_strong():
    evaluator = SourceBasisEvaluator()
    role, strength, limitations = evaluator.evaluate_authority(
        "https://new.kenyalaw.org/akn/ke/act/2007/employment",
        "LAWS OF KENYA It is hereby enacted by Parliament... Section 45",
        DocumentType.STATUTE,
    )
    assert strength == BasisStrength.STRONG
    assert "primary legislation" in role.value


def test_law_firm_commentary_is_persuasive_not_primary():
    evaluator = SourceBasisEvaluator()
    role, strength, limitations = evaluator.evaluate_authority(
        "https://examplelawfirm.co.ke/articles/employment",
        "This article analyzes section 45 of the Employment Act...",
        DocumentType.COMMENTARY,
    )
    assert strength == BasisStrength.PERSUASIVE


def test_unknown_domain_fallback_to_background():
    evaluator = SourceBasisEvaluator()
    role, strength, limitations = evaluator.evaluate_authority(
        "https://random-blog.example.com/kenya-law",
        "Some discussion about Kenyan law...",
        DocumentType.UNKNOWN,
    )
    assert strength in (BasisStrength.WEAK, BasisStrength.PERSUASIVE)


def test_saflii_judgment_is_strong():
    evaluator = SourceBasisEvaluator()
    role, strength, limitations = evaluator.evaluate_authority(
        "https://www.saflii.org/za/cases/ZACC/2023/1.html",
        "Judgment delivered by the Constitutional Court...",
        DocumentType.JUDGMENT,
    )
    assert strength == BasisStrength.STRONG
