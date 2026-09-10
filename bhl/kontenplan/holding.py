"""Kontenplan einer Beteiligungsholding ohne Umsatzsteuer (SKR04-nah).

Die Nummern folgen dem Kreis, den Steuerkanzleien fuer diesen Mandantentyp
ueblicherweise vergeben, damit Vorjahresvergleich und Uebergabe an den
Steuerberater ohne Umschluesselung funktionieren. Kontobezeichnungen, die
einen konkreten Vertrag oder ein Geldinstitut nennen, gehoeren in den Block
`kontobezeichnungen` der mandant.json und nicht hierher.

posten  -> Gliederungsposten fuer Bilanz/GuV (siehe reports.py)
steuer_tag -> Kennzeichen fuer die steuerliche Ueberleitungsrechnung
"""

# (nummer, bezeichnung, typ, posten, steuer_tag)
KONTEN = [
    # ---------------------------------------------------------- AKTIVA
    ("0500", "Betriebs- und Geschäftsausstattung",        "A", "AV_SACH_BGA",      None),
    ("0850", "Beteiligungen an Kapitalgesellschaft",      "A", "AV_FIN_BETEIL",    None),
    ("0860", "Beteiligungen an Personengesellschaft",     "A", "AV_FIN_BETEIL",    None),
    ("0900", "Wertpapiere des Anlagevermögens",           "A", "AV_FIN_WP",        None),
    ("1305", "Forderungen gegen verbundene Unternehmen",   "A", "UV_FORD_VERB",     None),
    # Ein Konto je Darlehensvertrag. Zusammen sind sie die Forderung gegen den
    # Gesellschafter nach § 42 Abs. 3 GmbHG; getrennt tragen sie den jeweils
    # vereinbarten Zinssatz und erlauben eine Tilgungsbestimmung, die ein
    # gemischtes Konto nicht hergibt.
    ("1309", "Ges.er-Darlehen Rahmenvertrag 2018 (1,00 %)", "A", "UV_SONST_VG",   None),
    ("1310", "Ges.er-Darlehen Immobilie 2021 (1,20 %)",   "A", "UV_SONST_VG",      None),
    ("1366", "Wandeldarlehen Beteiligung",                "A", "UV_SONST_VG",      None),
    ("1363", "Darlehen Beteiligung in Liquidation",       "A", "UV_SONST_VG",      None),
    ("1370", "Sonstige Vermögensgegenstände",             "A", "UV_SONST_VG",      None),
    ("1460", "Guthaben Kreditkartenkonto",                "A", "UV_SONST_ZD",      None),
    ("1545", "Steuererstattungsansprüche Körperschaftsteuer", "A", "UV_SONST_VG",  None),
    ("1546", "Steuererstattungsansprüche Gewerbesteuer", "A", "UV_SONST_VG",      None),
    ("1548", "Steuererstattungsansprüche Lohnsteuer",     "A", "UV_SONST_VG",      None),
    ("1800", "Geschäftskonto",                            "A", "UV_BANK",          None),
    ("1803", "Geschäftskonto 2",                          "A", "UV_BANK",          None),

    # ---------------------------------------------------------- PASSIVA
    ("1801", "Kontokorrentkredit",                        "P", "VB_KREDITINST",    None),
    ("2900", "Gezeichnetes Kapital",                      "P", "EK_GEZ",           None),
    ("2930", "Gesetzliche Rücklage",                      "P", "EK_RUECKLAGE",     None),
    ("2970", "Gewinnvortrag vor Verwendung",              "P", "EK_BILANZGEWINN",  None),
    ("3035", "Gewerbesteuerrückstellung § 4 (5b) EStG",   "P", "RST_STEUERN",      None),
    ("3040", "Körperschaftsteuerrückstellung",            "P", "RST_STEUERN",      None),
    ("3095", "Rückstellungen für Abschluss u. Prüfung",   "P", "RST_SONSTIGE",     None),
    ("3500", "Sonstige Verbindlichkeiten",                "P", "VB_SONSTIGE",      None),
    ("3560", "Darlehen (sonstige VB)",                    "P", "VB_SONSTIGE",      None),
    ("3700", "Verbindl. Steuern und Abgaben",             "P", "VB_SONSTIGE",      None),
    ("3720", "Verbindlichkeiten Lohn- und Kirchensteuer", "P", "VB_SONSTIGE",      None),

    # ---------------------------------------------------------- ERTRÄGE
    ("4840", "Erträge aus der Währungsumrechnung",        "E", "SBE_UEBRIGE",      None),
    ("4845", "Erträge aus Kursdifferenzen",               "E", "SBE_UEBRIGE",      None),
    ("4852", "Erlöse Verkäufe Finanzanl. z.T.stfr, BG",   "E", "SBE_ABGANG_AV",    "8b_2"),
    ("4853", "Erlöse Verkäufe Investmentanteile BG",      "E", "SBE_ABGANG_AV",    "invstg"),
    ("4855", "Erlöse Verkäufe Finanzanlagen, BG",         "E", "SBE_ABGANG_AV",    None),
    ("4858", "Abgänge Finanzanlagen RBW z.T.stf., BG",    "E", "SBE_ABGANG_AV",    "8b_2"),
    ("4859", "Anlagenabgänge Investmentanteile BG",       "E", "SBE_ABGANG_AV",    "invstg"),
    ("4900", "Sonstige betriebliche Erträge",             "E", "SBE_UEBRIGE",      None),
    ("4930", "Erträge aus der Auflösung von Rückstellungen", "E", "SBE_UEBRIGE",   None),

    # ---------------------------------------------------------- AUFWAND
    ("6020", "Löhne und Gehälter",                        "X", "PERSONAL_LOEHNE",  None),
    ("6110", "Gesetzliche soziale Aufwendungen",          "X", "PERSONAL_SOZIAL",  None),
    ("6220", "Abschreibungen auf Sachanlagen",            "X", "ABSCHREIBUNGEN",   None),
    ("6300", "Sonstige betriebliche Aufwendungen",        "X", "SBA_VERSCHIEDENE", None),
    ("6420", "Beiträge",                                  "X", "SBA_VERS_BEITR",   None),
    ("6430", "Sonstige Abgaben",                          "X", "SBA_VERS_BEITR",   None),
    ("6600", "Werbekosten",                               "X", "SBA_VERSCHIEDENE", None),
    ("6805", "Telefon / Internet",                        "X", "SBA_VERSCHIEDENE", None),
    ("6815", "Bürobedarf",                                "X", "SBA_VERSCHIEDENE", None),
    ("6825", "Rechts- und Beratungskosten",               "X", "SBA_VERSCHIEDENE", None),
    ("6827", "Abschluss- und Prüfungskosten",             "X", "SBA_VERSCHIEDENE", None),
    ("6830", "Buchführungskosten",                        "X", "SBA_VERSCHIEDENE", None),
    ("6837", "Aufwendungen für Lizenzen, Konzessionen",   "X", "SBA_VERSCHIEDENE", None),
    ("6855", "Nebenkosten des Geldverkehrs",              "X", "SBA_VERSCHIEDENE", None),
    ("6880", "Aufwendungen aus Währungsumrechnungen",     "X", "SBA_UEBRIGE",      None),
    ("6891", "Erlöse Verkäufe Finanzanlagen, BV",         "X", "SBA_ABGANG_AV",    None),
    ("6892", "Erlöse Verkäufe Finanzanl. z.T.stfr, BV",   "X", "SBA_ABGANG_AV",    "8b_3"),
    ("6897", "Abgänge Finanzanlagen Restbuchwert, BV",    "X", "SBA_ABGANG_AV",    None),
    ("6898", "Abgänge Finanzanlagen RBW z.T.stf., BV",    "X", "SBA_ABGANG_AV",    "8b_3"),

    # ---------------------------------------------------------- FINANZERGEBNIS
    ("7010", "Erträge Wertpapiere/Ausleihungen FAV",      "E", "ERTRAG_WP_FAV",    "streubesitz"),
    ("7014", "Erträge aus Investmentanteile FAV",         "E", "ERTRAG_WP_FAV",    "invstg"),
    ("7100", "Sonstige Zinsen und ähnliche Erträge",      "E", "ZINSERTRAG",       None),
    ("7205", "Abschreibungen Finanzanlagen z.T.stfr.",    "X", "FIN_ABSCHREIBUNG", "8b_3"),
    ("7302", "N. abzugsf. and.Nebenleistg §4 (5b) EStG",  "X", "ZINSAUFWAND",      "nabzb"),
    ("7310", "Zinsaufwendungen f.kfr.Verbindlichkeit.",   "X", "ZINSAUFWAND",      None),
    ("7320", "Zinsaufwendungen f.lfr.Verbindlichkeit.",   "X", "ZINSAUFWAND",      None),
    ("7330", "Zinsähnliche Aufwendungen",                 "X", "ZINSAUFWAND",      None),

    # ---------------------------------------------------------- STEUERN
    ("7600", "Körperschaftsteuer",                        "X", "STEUERN_EE",       "nabzb"),
    ("7603", "Körperschaftsteuer für Vorjahre",           "X", "STEUERN_EE",       "nabzb"),
    ("7608", "Solidaritätszuschlag",                      "X", "STEUERN_EE",       "nabzb"),
    ("7609", "Solidaritätszuschlag für Vorjahre",         "X", "STEUERN_EE",       "nabzb"),
    ("7610", "Gewerbesteuer",                             "X", "STEUERN_EE",       "nabzb"),
    ("7630", "Kapitalertragsteuer 25 % (KapG)",           "X", "STEUERN_EE",       "nabzb"),
    ("7633", "SolZ auf Kapitalertragsteuer 25 % (KapG)",  "X", "STEUERN_EE",       "nabzb"),
    ("7639", "Anrechn./Abzug ausländ. Quellensteuer",     "X", "STEUERN_EE",       "auslst"),
    ("7641", "GewSt-NZ/Erstattung VJ § 4 (5b) EStG",      "X", "STEUERN_EE",       "nabzb"),

    # ---------------------------------------------------------- ABSCHLUSS
    ("7700", "Gewinnvortrag nach Verwendung",             "P", "EK_BILANZGEWINN",  None),
    ("7765", "Einstellungen gesetzliche Rücklage",        "P", "EK_ERGEBNISVERW",  None),
    ("9000", "Saldenvorträge Sachkonten",                 "P", "EROEFFNUNG",       None),
]

# Gliederung Bilanz (Reihenfolge = Ausweisreihenfolge)
BILANZ_AKTIVA = [
    ("A. Anlagevermögen", None),
    ("  I. Sachanlagen", None),
    ("    1. andere Anlagen, Betriebs- und Geschäftsausstattung", ["AV_SACH_BGA"]),
    ("  II. Finanzanlagen", None),
    ("    1. Beteiligungen", ["AV_FIN_BETEIL"]),
    ("    2. Wertpapiere des Anlagevermögens", ["AV_FIN_WP"]),
    ("B. Umlaufvermögen", None),
    ("  I. Forderungen und sonstige Vermögensgegenstände", None),
    ("    1. Forderungen gegen verbundene Unternehmen", ["UV_FORD_VERB"]),
    ("    2. sonstige Vermögensgegenstände", ["UV_SONST_VG", "UV_SONST_ZD"]),
    ("  II. Kassenbestand, Guthaben bei Kreditinstituten", ["UV_BANK"]),
]

BILANZ_PASSIVA = [
    ("A. Eigenkapital", None),
    ("  I. Gezeichnetes Kapital", ["EK_GEZ"]),
    ("  II. Gewinnrücklagen / gesetzliche Rücklage", ["EK_RUECKLAGE"]),
    ("  III. Bilanzgewinn", ["EK_BILANZGEWINN", "EK_ERGEBNISVERW"]),
    ("B. Rückstellungen", None),
    ("    1. Steuerrückstellungen", ["RST_STEUERN"]),
    ("    2. sonstige Rückstellungen", ["RST_SONSTIGE"]),
    ("C. Verbindlichkeiten", None),
    ("    1. Verbindlichkeiten gegenüber Kreditinstituten", ["VB_KREDITINST"]),
    ("    2. sonstige Verbindlichkeiten", ["VB_SONSTIGE"]),
]

# Gliederung GuV (Gesamtkostenverfahren, kleine KapG)
GUV = [
    ("1. sonstige betriebliche Erträge", None),
    ("   a) Erträge aus dem Abgang von Gegenständen des Anlagevermögens", ["SBE_ABGANG_AV"]),
    ("   b) übrige sonstige betriebliche Erträge", ["SBE_UEBRIGE"]),
    ("2. Personalaufwand", None),
    ("   a) Löhne und Gehälter", ["PERSONAL_LOEHNE"]),
    ("   b) soziale Abgaben", ["PERSONAL_SOZIAL"]),
    ("3. Abschreibungen auf Sachanlagen", ["ABSCHREIBUNGEN"]),
    ("4. sonstige betriebliche Aufwendungen", None),
    ("   a) Versicherungen, Beiträge und Abgaben", ["SBA_VERS_BEITR"]),
    ("   b) verschiedene betriebliche Kosten", ["SBA_VERSCHIEDENE"]),
    ("   c) Verluste aus dem Abgang von Gegenständen des Anlagevermögens", ["SBA_ABGANG_AV"]),
    ("   d) übrige sonstige betriebliche Aufwendungen", ["SBA_UEBRIGE"]),
    ("5. Erträge aus anderen Wertpapieren und Ausleihungen des Finanzanlagevermögens",
     ["ERTRAG_WP_FAV"]),
    ("6. sonstige Zinsen und ähnliche Erträge", ["ZINSERTRAG"]),
    ("7. Abschreibungen auf Finanzanlagen und auf Wertpapiere des Umlaufvermögens",
     ["FIN_ABSCHREIBUNG"]),
    ("8. Zinsen und ähnliche Aufwendungen", ["ZINSAUFWAND"]),
    ("9. Steuern vom Einkommen und vom Ertrag", ["STEUERN_EE"]),
]

ERTRAGS_POSTEN = {"SBE_ABGANG_AV", "SBE_UEBRIGE", "ERTRAG_WP_FAV", "ZINSERTRAG"}
