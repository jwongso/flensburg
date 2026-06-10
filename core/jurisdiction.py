"""JurisdictionBase - the single interface every jurisdiction module must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.routing import StatuteRoute

if TYPE_CHECKING:
    from fastapi import FastAPI


@dataclass
class CorpusConfig:
    """Pointers to the data stores for this jurisdiction."""
    qdrant_collection: str          # primary collection (cases + decisions)
    courts: list[str]               # Qdrant payload filter values, e.g. ["NZTT"]
    leg_collection: str | None = None  # separate collection for legislation chunks
    pg_database: str | None = None  # PostgreSQL database name, None = no SQL path


@dataclass
class LegislationConfig:
    """Live legislation extraction settings."""
    acts: dict[str, str]            # act_id -> URL, e.g. {"RTA": "https://..."}
    cache_ttl_seconds: int = 3600


@dataclass
class WebVerifyConfig:
    """Web search verification settings."""
    search_prefix: str              # e.g. "NZ residential tenancy law"
    max_results: int = 3
    cache_ttl_seconds: int = 604800  # 7 days


@dataclass
class LegislationSource:
    """A single registered Act for federated per-source legislation retrieval.

    When a jurisdiction returns a non-empty list from leg_sources, _retrieve_anchor
    runs one Qdrant search per source (in parallel) instead of a single global search.
    Each source gets its own top_k quota so smaller Acts are not crowded out by larger ones.
    """
    act_id: str             # short key, e.g. "RTA", "HHS2019" - matches NZLEG/<act_id>/ in case_id
    court_name: str         # exact value of the court_name payload field in Qdrant
    default_top_k: int = 4  # candidates per search when this Act is not route-boosted
    boost_top_k: int = 8    # candidates per search when a matched route targets this Act


@dataclass
class ConfidenceConfig:
    """Confidence level thresholds and messages for a jurisdiction.

    Legislation-based jurisdictions should use lower thresholds and replace
    'decisions' language with 'sources' or 'legislation sections'.
    """
    high_score: float = 0.82
    high_n: int = 4
    medium_score: float = 0.77
    medium_n: int = 2
    messages: dict[str, str] = field(default_factory=lambda: {
        "high": "Found {n} directly relevant decisions.",
        "medium": "Found {n} relevant decisions - review the cited sources carefully.",
        "low": "Found only {n} loosely related decisions - verify independently before acting.",
        "none": "No relevant decisions found.",
    })


@dataclass
class SmokeFixture:
    """A single smoke test case for Tier 1 retrieval testing."""
    question: str
    expected_sections: list[str]    # section IDs that MUST appear in retrieval
    forbidden_sections: list[str] = field(default_factory=list)
    description: str = ""
    min_sources: int = 0            # if > 0, assert at least this many case sources returned
    expected_guidance_sources: list[str] = field(default_factory=list)  # MANUAL case_ids that MUST be injected


@dataclass
class RouteFixture:
    """A pure routing test - no Qdrant, no HTTP, no LLM.

    Tests that build_route_decision() fires or suppresses specific routes.
    One positive + one negative per route catches keyword collision regressions
    before they reach users.
    """
    question: str
    expected_routes: list[str] = field(default_factory=list)   # MUST fire
    forbidden_routes: list[str] = field(default_factory=list)  # MUST NOT fire
    description: str = ""
    rewritten: str = ""  # if empty, question is used as both original and rewritten


class JurisdictionBase(ABC):
    """Base class for a legal RAG jurisdiction.

    A jurisdiction module must implement 4 things:
        name            - short slug, used in logs and service names
        corpus          - which Qdrant collection and courts to search
        system_prompt   - the full LLM system prompt (jurisdiction owns this entirely)
        routes          - statute route table (may be empty list if no routing needed)

    Everything else has a working default. Start with just the 4 required properties
    and add optional overrides as you discover what your jurisdiction needs.
    """

    # -------------------------------------------------------------------------
    # Required (must implement)
    # -------------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Short slug. e.g. 'nz-tenancy', 'nsw-tenancy'"""

    @property
    @abstractmethod
    def corpus(self) -> CorpusConfig: ...

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Full LLM system prompt. Jurisdiction owns this entirely - core does not modify it."""

    @property
    @abstractmethod
    def routes(self) -> list[StatuteRoute]:
        """Statute route table. Return [] if no routing is needed."""

    # -------------------------------------------------------------------------
    # Optional - override as needed
    # -------------------------------------------------------------------------

    @property
    def description(self) -> str:
        return f"{self.name} legal research tool"

    @property
    def legislation(self) -> LegislationConfig | None:
        """None = no live legislation anchor (falls back to vector store only)."""
        return None

    @property
    def web_verify(self) -> WebVerifyConfig | None:
        """None = no web search verification step."""
        return None

    def preprocess_question(self, question: str, **context) -> str:
        """Optional: transform the question before retrieval and generation.

        Called once per request immediately after sanitization.
        context may include 'address' (str | None) or other request-level fields.
        Default is identity - override to inject zone info, session context, etc.
        """
        return question

    @property
    def leg_sources(self) -> list[LegislationSource]:
        """Override to enable federated per-Act legislation retrieval.

        When non-empty, _retrieve_anchor runs one Qdrant search per source in
        parallel, giving each Act a guaranteed candidate quota before re-ranking.
        When empty (default), falls back to a single global legislation search.
        """
        return []

    @property
    def low_priority_sections(self) -> dict[str, tuple[str, ...]]:
        """Sections suppressed unless the query explicitly mentions listed terms."""
        return {}

    @property
    def rewrite_prompt(self) -> str | None:
        """Custom query rewrite prompt. None = use core default. Return '' = skip rewrite."""
        return None

    @property
    def max_question_chars(self) -> int:
        """Maximum allowed question length. Requests exceeding this get 400."""
        return 1200

    @property
    def forbidden_topics(self) -> tuple[str, ...]:
        """Topics outside this jurisdiction's scope, referenced in system prompt enforcement."""
        return ()

    @property
    def confidence_config(self) -> ConfidenceConfig:
        """Confidence thresholds and messages. Override for legislation-based jurisdictions."""
        return ConfidenceConfig()

    @property
    def smoke_fixtures(self) -> list[SmokeFixture]:
        """Tier 1 retrieval smoke test fixtures. Core test suite runs these automatically."""
        return []

    @property
    def leg_ce_min_score(self) -> float:
        """Minimum cross-encoder score for a legislation section to reach LLM context.

        Sections scoring below this threshold are dropped after retrieval.
        Route-forced sections always pass regardless of score.

        Scores from BAAI/bge-reranker-v2-m3 are sigmoid-normalised [0, 1]:
          - Clearly relevant:   > 0.5
          - Borderline:         0.15 - 0.5
          - Clearly irrelevant: < 0.15

        Lower this if legitimate sections are being dropped. Raise it to tighten
        precision on jurisdictions with broad legislation corpora.
        """
        return 0.15

    @property
    def log_route_decisions(self) -> bool:
        """Write routing decision to data/route_debug.jsonl for every real question.

        Set True while the route table is being tuned. Disable once the
        jurisdiction is stable to avoid accumulating unnecessary log data.
        Requests with X-No-Log: 1 are always skipped.
        """
        return False

    @property
    def route_fixtures(self) -> list[RouteFixture]:
        """Pure routing test fixtures - no Qdrant, no HTTP.

        One positive + one negative per route. The test runner calls
        build_route_decision() directly and asserts expected/forbidden intents.
        """
        return []

    def extract_section(self, act_id: str, section: str, full_text: str) -> str | None:
        """Extract a section excerpt from live Act text.

        Return None to use the core default heading-aware extractor.
        Override only if the legislation site has unusual formatting.
        """
        return None

    def format_source_label(self, source: dict) -> str:
        """How to render a source in the frontend. Override for jurisdiction-specific labels."""
        court = source.get("court_name") or source.get("court", "Unknown")
        date = source.get("date", "")
        return f"{court} - {date}" if date else court

    def register_routes(self, app: "FastAPI") -> None:
        """Optional: register jurisdiction-specific extra routes on the FastAPI app.

        Called at the end of create_app() after all core routes are registered.
        Route handlers can access pipeline and store via request.app.state.
        """

    def register_mcp_tools(self, mcp, service) -> None:
        """Optional: register jurisdiction-specific MCP tools on the server.

        Called by create_mcp_server() after the 4 core tools are registered.
        Use mcp.add_tool(fn, name=..., description=...) to add tools.
        The service parameter provides access to pipeline helpers.

        Args:
            mcp: FastMCP server instance.
            service: JurisdictionService with search/ask/get_source/get_legislation.
        """

    @abstractmethod
    def get_scraper(self):
        """Return a scraper instance for offline corpus ingestion.

        The scraper is not part of the API runtime - it runs offline to populate Qdrant.
        See schemas/qdrant_payload.schema.json for the required payload structure.
        """
