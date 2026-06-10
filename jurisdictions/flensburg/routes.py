"""StatuteRoute definitions for German traffic law (Verkehrsrecht / Fahrerlaubnisrecht).

Corpus: StVO, StVG, OWiG, BKatV, FeV, StGB (traffic sections), PflVG.
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
        forced_sections=[
            "DELEG/StVO/3",         # Geschwindigkeit - the primary rule
            "DELEG/BKatV/anlage",   # fine table with all speed thresholds
            "DELEG/BKatV/4",        # Regelfahrverbot thresholds
        ],
        case_synthetic_query="Geschwindigkeitsueberschreitung Bussgeld Punkte Fahrverbot",
    ),

    # --- Punkte / Flensburg ---
    StatuteRoute(
        intent="punkte_flensburg",
        include_any=[
            "punkte", "flensburg", "punktestand", "fahreignungsregister",
            "fare register", "punktabzug", "punktereduzierung", "fahreignung",
            "punktetilgung", "tilgung",
        ],
        forced_sections=[
            "DELEG/StVG/4",         # Fahreignungs-Bewertungssystem (the full points table)
            "DELEG/StVG/29",        # Tilgung der Eintragungen
            "DELEG/StVG/4a",        # Fahreignungsseminar
        ],
        case_synthetic_query="Punkte Fahreignungsregister Flensburg Abbau Tilgung Schwellenwert",
    ),

    # --- Fahrverbot ---
    StatuteRoute(
        intent="fahrverbot",
        include_any=[
            "fahrverbot", "fahren verboten", "fuehrerschein abgeben",
            "fuehrerscheinentzug", "entzug", "fahren darf nicht",
            "fahrverbot antreten", "fahrverbot aufschieben",
        ],
        forced_sections=[
            "DELEG/StVG/25",        # Fahrverbot - Dauer, Vollstreckung, Aufschieben
            "DELEG/BKatV/4",        # Regelfahrverbot - Schwellenwerte
            "DELEG/BKatV/3",        # Bussgeldbegresaetze allgemein
        ],
        case_synthetic_query="Fahrverbot Dauer Vollstreckung Aufschieben Beruf Haerte",
    ),

    # --- MPU ---
    StatuteRoute(
        intent="mpu",
        include_any=[
            "mpu", "medizinisch-psychologisch", "idiotentest",
            "fahreignungspruefung", "begutachtung", "eignungspruefung",
            "psychologisch", "gutachten fahrer", "fahreignungsgutachten",
            "wiedererteilung", "wiedererlangung fuehrerschein",
        ],
        forced_sections=[
            "DELEG/FeV/11",         # Eignung - MPU-Anordnungsvoraussetzungen
            "DELEG/FeV/13",         # Eignungszweifel bei Alkohol
            "DELEG/FeV/13a",        # Eignungszweifel bei Cannabis
            "DELEG/FeV/46",         # Entziehung, Beschraenkung, Auflagen
        ],
        case_synthetic_query="MPU medizinisch-psychologische Untersuchung Fahrerlaubnis Wiedererteilung Alkohol",
    ),

    # --- Fahrerlaubnis / Fuehrerschein ---
    StatuteRoute(
        intent="fahrerlaubnis",
        include_any=[
            "fuehrerschein", "fahrerlaubnis", "klasse b",
            "klasse a", "fuehrerscheinklasse", "fahrberechtigung",
            "fuehrerscheinantrag", "umschreiben", "auslaendischer fuehrerschein",
            "fuehrerschein umtauschen", "fuehrerschein verloren",
        ],
        forced_sections=[
            "DELEG/StVG/2",         # Fahrerlaubnis und Fuehrerschein
            "DELEG/StVG/3",         # Entziehung der Fahrerlaubnis
            "DELEG/FeV/6",          # Fahrerlaubnisklassen
            "DELEG/FeV/20",         # Neuerteilung nach Entzug
        ],
        case_synthetic_query="Fahrerlaubnis Erteilung Entzug Wiedererteilung FeV Klassen Voraussetzungen",
    ),

    # --- Einspruch gegen Bussgeld ---
    StatuteRoute(
        intent="einspruch_bussgeld",
        include_any=[
            "einspruch", "widerspruch bussgeld", "bussgeldbescheid",
            "anfechten", "nicht zahlen", "bussgeld ablehnen",
            "bussgeld widersprechen", "bescheid anfechten",
            "einspruch einlegen", "frist einspruch",
        ],
        forced_sections=[
            "DELEG/OWiG/67",        # Form und Frist - 2-Wochen-Frist nach Zustellung
            "DELEG/OWiG/66",        # Inhalt des Bussgeldbescheides
            "DELEG/OWiG/69",        # Zwischenverfahren nach Einspruch
            "DELEG/OWiG/31",        # Verfolgungsverjaehrung
        ],
        case_synthetic_query="Einspruch Bussgeldverfahren OWiG Frist Zustellung Zwischenverfahren",
    ),

    # --- Alkohol / Drogen am Steuer ---
    StatuteRoute(
        intent="alkohol_drogen",
        include_any=[
            "alkohol", "promille", "trunkenheit", "betrunken",
            "drogen", "cannabis", "thc", "fahrt unter einfluss",
            "0.5 promille", "0,5 promille", "1.6 promille", "1,6 promille",
            "berauscht", "unter einfluss",
        ],
        forced_sections=[
            "DELEG/StVG/24a",       # 0,5-Promille-Grenze und THC-Grenzwert
            "DELEG/StVG/24c",       # Alkohol- und Cannabisverbot Fahranfaenger
            "DELEG/StGB/316",       # Straftat: Trunkenheit >= 1,6 Promille
            "DELEG/StGB/315c",      # Gefaehrdung des Strassenverkehrs
            "DELEG/FeV/13",         # Eignungszweifel Alkohol -> MPU
            "DELEG/FeV/13a",        # Eignungszweifel Cannabis -> MPU
        ],
        case_synthetic_query="Alkohol Promille Strassenverkehr Ordnungswidrigkeit Straftat Fahrerlaubnisentzug",
    ),

    # --- Unfall / Haftpflicht ---
    StatuteRoute(
        intent="unfall",
        include_any=[
            "unfall", "auffahrunfall", "parkschaden", "zusammenstoss",
            "haftpflicht", "versicherung unfall", "schadensersatz unfall",
            "schuld unfall", "unfallhergang", "unfallflucht",
            "vom unfallort entfernt", "fahrerflucht",
        ],
        forced_sections=[
            "DELEG/StVO/34",        # Verhalten bei Unfall - Wartepflicht
            "DELEG/StGB/142",       # Unerlaubtes Entfernen vom Unfallort (Straftat)
            "DELEG/StVG/7",         # Haftung des Halters
            "DELEG/PflVG/1",        # Versicherungspflicht
        ],
        case_synthetic_query="Verkehrsunfall Haftung Wartepflicht Unfallflucht Versicherung Schadensersatz",
    ),

    # --- Parken / Halteverbote ---
    StatuteRoute(
        intent="parken",
        include_any=[
            "parken", "parkverbot", "halteverbot", "falsch geparkt",
            "knoellchen", "parkticket", "abschleppen", "parkscheibe",
            "parkzone", "kurzparkzone", "eingeschraenktes halteverbot",
            "absolutes halteverbot",
        ],
        forced_sections=[
            "DELEG/StVO/12",        # Halten und Parken
            "DELEG/StVO/13",        # Einrichtungen zur Ueberwachung der Parkzeit
            "DELEG/StVO/15a",       # Abschleppen von Fahrzeugen
        ],
        case_synthetic_query="Parken Halteverbot Parkverbot Bussgeld Abschleppen StVO",
    ),

    # --- Handynutzung am Steuer ---
    StatuteRoute(
        intent="handy_steuer",
        include_any=[
            "handy", "telefon steuer", "smartphone fahren",
            "ablenkung steuer", "handy am steuer", "mobiltelefon",
            "telefonieren fahren", "tippen fahren",
        ],
        forced_sections=[
            "DELEG/StVO/23",        # Sonstige Pflichten - Handyverbot in Abs. 1a
            "DELEG/BKatV/anlage",   # Bussgeldtabelle: Handynutzung lfd. Nr. 246
        ],
        case_synthetic_query="Handynutzung Mobiltelefon Steuer Bussgeld Punkte StVO 23",
    ),
]

LOW_PRIORITY_SECTIONS: dict[str, tuple[str, ...]] = {}
