"""StatuteRoute definitions for German traffic law (Verkehrsrecht / Fahrerlaubnisrecht).

Corpus: StVO, StVG, OWiG, BKatV (Bussgeldkatalog), FeV (Fahrerlaubnis-Verordnung).
All federal - uniform across all Bundeslaender.
"""

from core.routing import StatuteRoute

ROUTES: list[StatuteRoute] = [
    # --- Bussgeld / Geschwindigkeitsueberschreitung ---
    StatuteRoute(
        intent="geschwindigkeit",
        include_any=[
            "geschwindigkeit", "zu schnell", "geblitzt", "tempolimit", "tempo",
            "speed", "km/h", "blitzer", "radar", "streckenradar",
            "innerorts", "ausserorts", "autobahn",
        ],
        forced_sections=[],
        case_synthetic_query="Geschwindigkeitsueberschreitung Bussgeld Punkte Fahrverbot",
    ),

    # --- Punkte / Flensburg ---
    StatuteRoute(
        intent="punkte_flensburg",
        include_any=[
            "punkte", "flensburg", "punktestand", "fahreignungsregister",
            "fare register", "punktabzug", "punktereduzierung", "fahreignung",
        ],
        forced_sections=[],
        case_synthetic_query="Punkte Fahreignungsregister Flensburg Abbau Tilgung",
    ),

    # --- Fahrverbot ---
    StatuteRoute(
        intent="fahrverbot",
        include_any=[
            "fahrverbot", "fahren verboten", "fuehrerschein abgeben",
            "fuehrerscheinentzug", "entzug", "fahren darf nicht",
        ],
        forced_sections=[],
        case_synthetic_query="Fahrverbot Dauer Vollstreckung Ausnahme Beruf",
    ),

    # --- MPU ---
    StatuteRoute(
        intent="mpu",
        include_any=[
            "mpu", "medizinisch-psychologisch", "idiotentest",
            "fahreignungspruefung", "begutachtung", "eignungspruefung",
            "psychologisch", "gutachten fahrer",
        ],
        forced_sections=[],
        case_synthetic_query="MPU medizinisch-psychologische Untersuchung Fahrerlaubnis Wiedererteilung",
    ),

    # --- Fahrerlaubnis / Fuehrerschein ---
    StatuteRoute(
        intent="fahrerlaubnis",
        include_any=[
            "fuehrerschein", "fahrerlaubnis", "fahrschein", "klasse b",
            "klasse a", "fuehrerscheinklasse", "fahrberechtigung",
            "fuehrerscheinantrag", "umschreiben", "auslaendischer fuehrerschein",
        ],
        forced_sections=[],
        case_synthetic_query="Fahrerlaubnis Erteilung Entzug Wiedererteilung FeV Voraussetzungen",
    ),

    # --- Einspruch gegen Bussgeld ---
    StatuteRoute(
        intent="einspruch_bussgeld",
        include_any=[
            "einspruch", "widerspruch bussgeld", "bussgeldbescheid",
            "anfechten", "nicht zahlen", "bussgeld ablehnen",
            "bussgeld widersprechen", "bescheid anfechten",
        ],
        forced_sections=[],
        case_synthetic_query="Einspruch Bussgeldverfahren OWiG Frist Verjaehrung",
    ),

    # --- Alkohol / Drogen am Steuer ---
    StatuteRoute(
        intent="alkohol_drogen",
        include_any=[
            "alkohol", "promille", "trunkenheit", "betrunken",
            "drogen", "cannabis", "thc", "fahrt unter einfluss",
            "0.5 promille", "0,5 promille", "1.6 promille", "1,6 promille",
        ],
        forced_sections=[],
        case_synthetic_query="Alkohol Drogen Steuer Promillegrenze StVG Fahrerlaubnisentzug",
    ),

    # --- Unfall / Haftpflicht ---
    StatuteRoute(
        intent="unfall",
        include_any=[
            "unfall", "auffahrunfall", "parkschaden", "zusammenstoss",
            "haftpflicht", "versicherung unfall", "schadensersatz unfall",
            "schuld unfall", "unfallhergang",
        ],
        forced_sections=[],
        case_synthetic_query="Verkehrsunfall Haftung Schadensersatz StVO Schuldfrage",
    ),

    # --- Parken / Halteverbote ---
    StatuteRoute(
        intent="parken",
        include_any=[
            "parken", "parkverbot", "halteverbot", "falsch geparkt",
            "knoellchen", "parkticket", "abschleppen", "parkscheibe",
            "parkzone", "kurzparkzone",
        ],
        forced_sections=[],
        case_synthetic_query="Parken Halteverbot Parkverbot Bussgeld StVO",
    ),

    # --- Handynutzung am Steuer ---
    StatuteRoute(
        intent="handy_steuer",
        include_any=[
            "handy", "telefon steuer", "smartphone fahren",
            "ablenkung steuer", "handy am steuer", "mobiltelefon",
        ],
        forced_sections=[],
        case_synthetic_query="Handynutzung Steuer Bussgeld StVO Ablenkung",
    ),
]

LOW_PRIORITY_SECTIONS: dict[str, tuple[str, ...]] = {}
