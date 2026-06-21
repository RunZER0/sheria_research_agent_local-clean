import os
import sys

# Ensure project root is on path for test imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.source_quality import evaluate_source


def test_kenyalaw_high_trust():
    q = evaluate_source("https://kenyalaw.org/judgments/some-case", title="Some Judgment", kenya_legal_mode=True)
    assert q.source_type in ("legal_database", "official_primary")
    assert q.authority_level == "primary"
    assert q.should_use


def test_go_ke_judiciary_trusted():
    q = evaluate_source("https://judiciary.go.ke/some-judgment", title="Judgment", kenya_legal_mode=True)
    assert q.source_type == "court_or_judiciary" or q.authority_level == "official"
    assert q.should_use or q.score >= 80


def test_law_firm_background():
    q = evaluate_source("https://oraro.co.ke/insight/article", title="Commentary on law", kenya_legal_mode=True)
    assert q.source_type == "law_firm_commentary"
    assert q.authority_level == "background"
    assert not (q.source_type == "official_primary")


def test_unknown_rejected_in_strict_mode():
    q = evaluate_source("https://someunknownblog.example/post", title="Opinion", kenya_legal_mode=True)
    assert q.source_type == "unknown" or q.score < 20
    assert q.should_use is False
