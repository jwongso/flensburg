"""Flensburg jurisdiction - German traffic law Q&A.

Covers: StVO, StVG, OWiG, BKatV, FeV.
All federal - uniform across all 16 Bundeslaender.
"""

from core.jurisdiction import (
    CorpusConfig,
    JurisdictionBase,
    LegislationSource,
    RouteFixture,
    SmokeFixture,
)
from core.routing import StatuteRoute
from jurisdictions.flensburg.prompt import SYSTEM_PROMPT
from jurisdictions.flensburg.routes import LOW_PRIORITY_SECTIONS, ROUTES


class FlensburgJurisdiction(JurisdictionBase):

    @property
    def name(self) -> str:
        return "flensburg"

    @property
    def description(self) -> str:
        return "Deutsches Verkehrsrecht Q&A - Bussgeld, Fahrerlaubnis, MPU, Punkte"

    @property
    def corpus(self) -> CorpusConfig:
        return CorpusConfig(
            qdrant_collection="flensburg",
            courts=["BGH", "OLG", "MANUAL"],
            leg_collection="de_legal",
            pg_database="de_legal",
        )

    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    @property
    def routes(self) -> list[StatuteRoute]:
        return ROUTES

    @property
    def leg_sources(self) -> list[LegislationSource]:
        return [
            LegislationSource(
                act_id="StVO",
                court_name="Strassenverkehrs-Ordnung",
                default_top_k=6,
                boost_top_k=10,
            ),
            LegislationSource(
                act_id="StVG",
                court_name="Strassenverkehrsgesetz",
                default_top_k=4,
                boost_top_k=8,
            ),
            LegislationSource(
                act_id="OWiG",
                court_name="Gesetz ueber Ordnungswidrigkeiten",
                default_top_k=4,
                boost_top_k=6,
            ),
            LegislationSource(
                act_id="FeV",
                court_name="Fahrerlaubnis-Verordnung",
                default_top_k=4,
                boost_top_k=8,
            ),
            LegislationSource(
                act_id="BKatV",
                court_name="Bussgeldkatalog-Verordnung",
                default_top_k=4,
                boost_top_k=8,
            ),
        ]

    @property
    def low_priority_sections(self) -> dict[str, tuple[str, ...]]:
        return LOW_PRIORITY_SECTIONS

    @property
    def legislation(self):
        return None  # legislation pre-ingested into de_legal Qdrant collection

    @property
    def web_verify(self):
        return None  # not needed initially

    @property
    def leg_ce_min_score(self) -> float:
        return 0.50

    @property
    def log_route_decisions(self) -> bool:
        return True

    @property
    def max_question_chars(self) -> int:
        return 1200

    @property
    def smoke_fixtures(self) -> list[SmokeFixture]:
        return [
            SmokeFixture(
                question="Ich wurde mit 30 km/h zu schnell innerorts geblitzt. Welches Bussgeld bekomme ich?",
                expected_sections=[],
                min_sources=3,
                description="geschwindigkeit route - Tempolimit innerorts",
            ),
            SmokeFixture(
                question="Ich habe 8 Punkte in Flensburg. Was passiert als naechstes?",
                expected_sections=[],
                min_sources=3,
                description="punkte_flensburg route - 8 Punkte Schwelle",
            ),
            SmokeFixture(
                question="Ich soll zur MPU. Was muss ich tun und wie bereite ich mich vor?",
                expected_sections=[],
                min_sources=3,
                description="mpu route - MPU Vorbereitung",
            ),
            SmokeFixture(
                question="Ich habe einen Bussgeldbescheid erhalten. Wie lege ich Einspruch ein?",
                expected_sections=[],
                min_sources=3,
                description="einspruch_bussgeld route - Einspruchsverfahren",
            ),
        ]

    @property
    def route_fixtures(self) -> list[RouteFixture]:
        return [
            RouteFixture(
                question="Ich wurde mit 25 km/h zu schnell geblitzt.",
                expected_routes=["geschwindigkeit"],
                forbidden_routes=["fahrverbot"],
                description="geschwindigkeit positive - geblitzt",
            ),
            RouteFixture(
                question="Mein Fuehrerschein wurde entzogen. Wie bekomme ich ihn zurueck?",
                expected_routes=["fahrerlaubnis", "fahrverbot"],
                forbidden_routes=["parken"],
                description="fahrerlaubnis positive - Fuehrerscheinentzug",
            ),
            RouteFixture(
                question="Ich habe einen Bussgeldbescheid bekommen und moechte Einspruch einlegen.",
                expected_routes=["einspruch_bussgeld"],
                forbidden_routes=["mpu"],
                description="einspruch positive",
            ),
            RouteFixture(
                question="Ich habe 0.8 Promille gehabt und wurde kontrolliert.",
                expected_routes=["alkohol_drogen"],
                forbidden_routes=["parken"],
                description="alkohol positive - Promille Kontrolle",
            ),
            RouteFixture(
                question="Ich habe falsch geparkt und ein Knoellchen bekommen.",
                expected_routes=["parken"],
                forbidden_routes=["geschwindigkeit"],
                description="parken positive - Knoellchen",
            ),
        ]

    def format_source_label(self, source: dict) -> str:
        court = source.get("court_name", "Gericht")
        date = source.get("date", "")
        return f"{court} - {date}" if date else court

    def get_scraper(self):
        return None


jurisdiction = FlensburgJurisdiction()
