from pydantic import BaseModel, Field
from typing import List, Dict


class ToolSpec(BaseModel):
    name: str
    purpose: str
    when_to_use: List[str] = Field(default_factory=list)
    failure_modes: List[str] = Field(default_factory=list)
    recovery_tools: List[str] = Field(default_factory=list)


class ToolRegistry:
    def __init__(self):
        self._registry: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec):
        self._registry[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._registry.get(name)

    def list_tools(self) -> List[ToolSpec]:
        return list(self._registry.values())


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()

    reg.register(ToolSpec(
        name="new_kenyalaw_native",
        purpose="Direct, official access to the new Kenya Law Akoma Ntoso (AKN) repository and advanced search listings.",
        when_to_use=[
            "CRITICAL: Always use this tool FIRST whenever searching for Kenyan case law, judgments, or local statutes.",
            "Resolving explicit Kenyan citations or party names."
        ],
        failure_modes=["no_results", "network_error", "empty_result_set"],
        recovery_tools=["brave_search_fallback", "exact_title_search"],
    ))

    reg.register(ToolSpec(
        name="brave_search_fallback",
        purpose="Broad internet search engine optimized for open-ended web lookups and secondary commentary.",
        when_to_use=[
            "Handling general query definitions or non-case legal theory requests.",
            "FALLBACK ONLY: Use for case lookups ONLY after 'new_kenyalaw_native' returns zero matching records."
        ],
        failure_modes=["no_results", "network_error"],
        recovery_tools=["exact_title_search", "site_search"],
    ))

    reg.register(ToolSpec(
        name="browser_fetch_firefox",
        purpose="Headless browser fetch for dynamic pages and JS rendering",
        when_to_use=["open_source", "inspect_quality"],
        failure_modes=["page_timeout", "webdriver_error"],
        recovery_tools=["http_fetch", "pdf_text_extract", "parent_url_fetch"],
    ))
    reg.register(ToolSpec(
        name="http_fetch",
        purpose="Simple HTTP GET for static pages",
        when_to_use=["fallback_open"],
        failure_modes=["404", "timeout"],
        recovery_tools=["browser_fetch_firefox", "pdf_text_extract"],
    ))
    reg.register(ToolSpec(
        name="pdf_text_extract",
        purpose="Extract text from PDFs",
        when_to_use=["pdf_url"],
        failure_modes=["garbled_text", "ocr_needed"],
        recovery_tools=["parent_url_fetch", "exact_title_search"],
    ))
    return reg
