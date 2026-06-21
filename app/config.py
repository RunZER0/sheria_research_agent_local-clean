from __future__ import annotations
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Existing Client Credentials ---
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_thinking: str = "enabled"
    deepseek_reasoning_effort: str = "medium"

    brave_api_key: str = ""
    groq_api_key: str = ""

    # --- Supabase Dashboard Parameters ---
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    max_search_queries: int = 4
    max_search_rounds: int = 3
    max_search_results_per_query: int = 5
    max_fetch_pages: int = 6
    max_sources_to_inspect: int = 10
    max_evidence_chars: int = 2600
    request_timeout_seconds: int = 30

    kenya_legal_domains: str = (
        "new.kenyalaw.org,kenyalaw.org,judiciary.go.ke,court.go.ke,"
        "causelist.court.go.ke,parliament.go.ke,statelaw.go.ke,odpc.go.ke,"
        "kra.go.ke,ppra.go.ke,cma.or.ke,centralbank.go.ke"
    )

    sqlite_path: str = "./storage/research_agent.sqlite3"

    # Research engine: "legacy" uses old node-based pipeline; "react_v2" uses new ReAct loop; "sheria_react" uses policy-led ReAct agent
    sheria_research_engine: str = "sheria_react"

    # Research engine: "legacy" uses old node-based pipeline; "react_v2" uses new ReAct loop; "sheria_react" uses policy-led ReAct agent
    sheria_research_engine: str = "sheria_react"

    # Agent / platform identity (configurable so runtime prompts are not hardcoded)
    agent_name: str = "Orbit"
    platform_name: str = "Orbit Legal IDE"
    default_jurisdiction: str = "Kenya"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def legal_domains(self) -> list[str]:
        # Ensure the highest-priority Kenya law domains are explicitly ordered
        return [
            "new.kenyalaw.org",
            "kenyalaw.org",
            "judiciary.go.ke",
            "parliament.go.ke",
        ]

    @property
    def sqlite_file(self) -> Path:
        return Path(self.sqlite_path)
    
    @sqlite_file.setter
    def sqlite_file(self, value: str | Path) -> None:
        # Allow tests to override sqlite file convenience property
        self.sqlite_path = str(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
