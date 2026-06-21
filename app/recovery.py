from typing import List


class RecoveryManager:
    """RecoveryManager contains playbooks for common failure modes.

    These are declarative sequences of tool attempts; actual execution is performed
    by the ResearchController via the ToolRegistry/ToolRouter.
    """

    def unreadable_pdf_playbook(self) -> List[str]:
        return [
            "pdf_text_extract",
            "parent_url_fetch",
            "exact_title_search",
            "exact_citation_search",
            "site_search",
        ]

    def unreadable_page_playbook(self) -> List[str]:
        return [
            "browser_fetch_firefox",
            "http_fetch",
            "parent_url_fetch",
            "exact_title_search",
        ]

    def missing_statute_section_playbook(self) -> List[str]:
        return [
            "statute_section_search",
            "exact_title_search",
            "site_search",
            "brave_search",
        ]

    def quality_unclear_playbook(self) -> List[str]:
        return [
            "browser_fetch_firefox",
            "source_quality_evaluate",
            "exact_title_search",
        ]
