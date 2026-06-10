"""create_mcp_server() factory - turns a JurisdictionBase into an MCP server.

Usage (per-jurisdiction entry point):

    from core.mcp import create_mcp_server
    from jurisdictions.nz_tenancy.jurisdiction import NZTenancyJurisdiction

    server = create_mcp_server(NZTenancyJurisdiction())

    if __name__ == "__main__":
        server.run("stdio")

Claude Desktop config (~/.claude_desktop_config.json):

    {
      "mcpServers": {
        "nz-tenancy": {
          "command": "python3",
          "args": ["-m", "jurisdictions.nz_tenancy.mcp_server"],
          "cwd": "/path/to/astraea"
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
import os

from mcp.server.fastmcp import FastMCP

from core.jurisdiction import JurisdictionBase
from core.pipeline import RAGPipeline
from core.retriever import VectorStore
from core.service import JurisdictionService, ServiceError

logger = logging.getLogger(__name__)


def create_mcp_server(jurisdiction: JurisdictionBase) -> FastMCP:
    """Create a FastMCP server for a jurisdiction.

    Registers 4 core read-only tools:
        legal_search          - vector search, no generation
        legal_ask             - full RAG (retrieve + generate)
        legal_get_source      - fetch source/case chunk by ID
        legal_get_legislation - fetch legislation section by ID (if leg_collection set)

    Jurisdictions may add extra tools by implementing register_mcp_tools(mcp, service).
    """
    corpus = jurisdiction.corpus
    pipeline = RAGPipeline(
        collection=corpus.qdrant_collection,
        system_prompt=jurisdiction.system_prompt,
        courts=corpus.courts or None,
    )
    leg_store: VectorStore | None = None
    if corpus.leg_collection:
        leg_store = VectorStore(collection=corpus.leg_collection)

    service = JurisdictionService(jurisdiction, pipeline, leg_store)

    mcp = FastMCP(
        name=f"astraea-{jurisdiction.name}",
        instructions=(
            f"{jurisdiction.description}. "
            "Use legal_search to find relevant cases and legislation. "
            "Use legal_ask for a synthesised answer with citations. "
            "All results are AI-assisted research - verify with a qualified lawyer."
        ),
    )

    # ------------------------------------------------------------------
    # Tool: legal_search
    # ------------------------------------------------------------------

    async def legal_search(query: str, top_k: int = 5) -> str:
        """Search for relevant legal sources by semantic similarity.

        Args:
            query: The legal question or topic to search for.
            top_k: Number of results to return (1-20, default 5).

        Returns JSON: {count, sources: [{source_id, title, court_name, date, url, _score}]}
        """
        try:
            sources = await service.search(query, top_k=top_k)
            return json.dumps({"count": len(sources), "sources": sources}, indent=2)
        except ServiceError as e:
            return json.dumps({"error": str(e)})
        except Exception:
            logger.exception("legal_search failed for query=%r", query)
            return json.dumps({"error": "Internal search error."})

    mcp.add_tool(
        legal_search,
        name="legal_search",
        description=(
            f"Search {jurisdiction.description} for relevant cases and documents. "
            "Returns sources with title, court, date, URL, and relevance score. "
            "Use this to find raw sources - use legal_ask for a synthesised answer."
        ),
    )

    # ------------------------------------------------------------------
    # Tool: legal_ask
    # ------------------------------------------------------------------

    async def legal_ask(question: str) -> str:
        """Ask a legal question and receive a researched answer with citations.

        Args:
            question: The legal question to answer.

        Returns JSON: {answer: str, sources: [...]}
        """
        try:
            result = await service.ask(question)
            return json.dumps(result, indent=2)
        except ServiceError as e:
            return json.dumps({"error": str(e)})
        except Exception:
            logger.exception("legal_ask failed for question=%r", question)
            return json.dumps({"error": "Internal error generating answer."})

    mcp.add_tool(
        legal_ask,
        name="legal_ask",
        description=(
            f"Ask a legal question and get a researched answer from {jurisdiction.description}. "
            "Retrieves relevant case law and generates a synthesised answer with section citations. "
            "Returns {answer, sources}. Not legal advice - verify with a qualified lawyer."
        ),
    )

    # ------------------------------------------------------------------
    # Tool: legal_get_source
    # ------------------------------------------------------------------

    async def legal_get_source(source_id: str) -> str:
        """Retrieve the full text of a case or document by its source ID.

        Args:
            source_id: The source/case ID from a legal_search result.

        Returns JSON: {source_id, title, court_name, date, url, text}
        """
        try:
            result = await service.get_source(source_id)
            if result is None:
                return json.dumps({"error": f"Source '{source_id}' not found."})
            return json.dumps(result, indent=2)
        except Exception:
            logger.exception("legal_get_source failed for source_id=%r", source_id)
            return json.dumps({"error": "Failed to retrieve source."})

    mcp.add_tool(
        legal_get_source,
        name="legal_get_source",
        description=(
            "Retrieve the full text of a case or document by its source_id. "
            "Source IDs come from legal_search results. "
            "Returns {source_id, title, court_name, date, url, text}."
        ),
    )

    # ------------------------------------------------------------------
    # Tool: legal_get_legislation (only when leg_collection is configured)
    # ------------------------------------------------------------------

    if leg_store is not None:
        async def legal_get_legislation(section_id: str) -> str:
            """Retrieve the text of a legislation section by its ID.

            Args:
                section_id: Section ID in SOURCE/ACT/sN format (e.g. NZLEG/RTA/s42A).

            Returns JSON: {section_id, title, text, url}
            """
            try:
                result = await service.get_legislation(section_id)
                if result is None:
                    return json.dumps({"error": f"Section '{section_id}' not found."})
                return json.dumps(result, indent=2)
            except Exception:
                logger.exception("legal_get_legislation failed for section_id=%r", section_id)
                return json.dumps({"error": "Failed to retrieve legislation."})

        mcp.add_tool(
            legal_get_legislation,
            name="legal_get_legislation",
            description=(
                "Retrieve the text of a specific legislation section by its ID. "
                "Section IDs follow the pattern SOURCE/ACT/sN (e.g. NZLEG/RTA/s42A). "
                "Returns {section_id, title, text, url}."
            ),
        )

    # ------------------------------------------------------------------
    # Jurisdiction-specific tools
    # ------------------------------------------------------------------

    jurisdiction.register_mcp_tools(mcp, service)

    return mcp
