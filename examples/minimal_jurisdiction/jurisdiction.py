from core.jurisdiction import CorpusConfig, JurisdictionBase, SmokeFixture
from core.routing import StatuteRoute


class MinimalJurisdiction(JurisdictionBase):

    @property
    def name(self) -> str:
        return "minimal-example"

    @property
    def corpus(self) -> CorpusConfig:
        return CorpusConfig(
            qdrant_collection="my_collection",
            courts=["MY_COURT"],
        )

    @property
    def system_prompt(self) -> str:
        return (
            "You are a legal research assistant. "
            "Answer only from the provided sources. "
            "Cite every claim with [SN] notation. "
            "If the context is insufficient, say so clearly."
        )

    @property
    def routes(self) -> list[StatuteRoute]:
        return []

    def get_scraper(self):
        raise NotImplementedError("Replace with your jurisdiction scraper.")


jurisdiction = MinimalJurisdiction()
