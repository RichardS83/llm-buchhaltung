# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Kontenrahmen einer operativen Kapitalgesellschaft (SKR04-nah).

Anders als die Beteiligungsholding führt sie Umsatzsteuer. Die letzte Spalte
`ustva_kz` sagt, auf welche Kennzahl der Voranmeldung ein Konto einzahlt:

    81  Erlöse 19 %                      4400
    86  Erlöse 7 %                       4300
    21  sonstige Leistungen EU-B2B       4336   (zugleich Zusammenfassende Meldung)
    45  nicht steuerbare Umsätze         4338
    47  Steuer § 13b Abs. 1 (EU)         3837
    85  Steuer § 13b Abs. 2 (Drittland)  3839
    66  Vorsteuer aus Rechnungen         1406
    67  Vorsteuer § 13b                  1407
    81s Steuer auf die Erlöse 19 %       3806   (rechnet Elster selbst — nur Kontrolle)

Die Bemessungsgrundlagen **46 und 84** haben bewusst kein eigenes Konto: sie sind
das Netto der Eingangsleistung und liegen damit auf dem jeweiligen Aufwandskonto.
Sie kommen über den `ust_schluessel` der einzelnen Buchungszeile — siehe
`bhl/umsatzsteuer.py`.

Die Nummern folgen demselben Kreis wie der Holding-Rahmen,
damit beide Mandanten sich gleich lesen.
"""

# (nummer, bezeichnung, typ, posten, steuer_tag, ustva_kz)
KONTEN = [
    # ---------------------------------------------------------------- AKTIVA
    ("0500", "Betriebs- und Geschäftsausstattung",         "A", "AV_SACH_BGA",   None, None),
    ("1200", "Forderungen aus Lieferungen und Leistungen", "A", "UV_FORD_LL",    None, None),
    ("1406", "Abziehbare Vorsteuer 19 %",                  "A", "UV_SONST_VG",   None, "66"),
    ("1407", "Abziehbare Vorsteuer nach § 13b UStG",       "A", "UV_SONST_VG",   None, "67"),
    ("1420", "Umsatzsteuerforderungen",                    "A", "UV_SONST_VG",   None, None),
    # Eigener Posten, obwohl er in derselben Bilanzzeile landet wie die uebrigen
    # sonstigen Vermoegensgegenstaende: Stripe Payments Europe Ltd ist ein
    # E-Geld-Institut, kein Kreditinstitut, das Guthaben gehoert deshalb nicht
    # unter § 266 Abs. 2 B IV HGB. Wirtschaftlich ist es trotzdem jederzeit
    # abrufbares Geld - der eigene Schluessel macht es fuer die Kennzahl
    # "verfuegbare Mittel" auffindbar, ohne die Gliederung anzutasten.
    ("1460", "Guthaben bei Zahlungsdienstleistern",        "A", "UV_SONST_ZD",   None, None),
    ("1545", "Steuererstattungsansprüche Körperschaftsteuer", "A", "UV_SONST_VG", None, None),
    ("1546", "Steuererstattungsansprüche Gewerbesteuer",   "A", "UV_SONST_VG",   None, None),
    ("1800", "Geschäftskonto",                             "A", "UV_BANK",       None, None),

    # --------------------------------------------------------------- PASSIVA
    ("2900", "Gezeichnetes Kapital",                       "P", "EK_GEZ",           None, None),
    ("2910", "Ausstehende Einlagen auf das gezeichnete Kapital", "P", "EK_AUSSTEHEND", None, None),
    ("2970", "Gewinnvortrag vor Verwendung",               "P", "EK_BILANZGEWINN",  None, None),
    ("3035", "Gewerbesteuerrückstellung",                  "P", "RST_STEUERN",      None, None),
    ("3040", "Körperschaftsteuerrückstellung",             "P", "RST_STEUERN",      None, None),
    ("3095", "Rückstellungen für Abschluss und Prüfung",   "P", "RST_SONSTIGE",     None, None),
    ("3300", "Verbindlichkeiten aus Lieferungen und Leistungen", "P", "VB_LL",      None, None),
    ("3500", "Sonstige Verbindlichkeiten",                 "P", "VB_SONSTIGE",      None, None),
    ("3700", "Verbindlichkeiten Steuern und Abgaben",      "P", "VB_SONSTIGE",      None, None),
    ("3806", "Umsatzsteuer 19 %",                          "P", "VB_SONSTIGE",      None, "81s"),
    ("3820", "Umsatzsteuer-Zahllast / Erstattungsanspruch", "P", "VB_SONSTIGE",     None, None),
    ("3837", "Umsatzsteuer nach § 13b Abs. 1 UStG (EU)",   "P", "VB_SONSTIGE",      None, "47"),
    ("3839", "Umsatzsteuer nach § 13b Abs. 2 UStG (Drittland)", "P", "VB_SONSTIGE", None, "85"),

    # --------------------------------------------------------------- ERTRÄGE
    ("4300", "Erlöse zum Steuersatz 7 %",                  "E", "UMSATZ",        None, "86"),
    ("4336", "Erlöse sonstige Leistungen EU, Steuerschuldnerschaft des Leistungsempfängers",
                                                            "E", "UMSATZ",        None, "21"),
    ("4338", "Nicht steuerbare Umsätze (Leistungsort nicht im Inland)",
                                                            "E", "UMSATZ",        None, "45"),
    ("4400", "Erlöse zum Steuersatz 19 %",                 "E", "UMSATZ",        None, "81"),
    ("4840", "Erträge aus der Währungsumrechnung",         "E", "SBE_UEBRIGE",   None, None),
    ("4900", "Sonstige betriebliche Erträge",              "E", "SBE_UEBRIGE",   None, None),
    ("4930", "Erträge aus der Auflösung von Rückstellungen", "E", "SBE_UEBRIGE", None, None),

    # --------------------------------------------------------------- AUFWAND
    ("6020", "Löhne und Gehälter",                         "X", "PERSONAL_LOEHNE",  None, None),
    ("6110", "Gesetzliche soziale Aufwendungen",           "X", "PERSONAL_SOZIAL",  None, None),
    ("6220", "Abschreibungen auf Sachanlagen",             "X", "ABSCHREIBUNGEN",   None, None),
    ("6300", "Sonstige betriebliche Aufwendungen",         "X", "SBA_VERSCHIEDENE", None, None),
    ("6301", "Gründungskosten",                            "X", "SBA_VERSCHIEDENE", None, None),
    ("6420", "Beiträge",                                   "X", "SBA_VERS_BEITR",   None, None),
    ("6430", "Sonstige Abgaben",                           "X", "SBA_VERS_BEITR",   None, None),
    ("6805", "Telefon und Internet",                       "X", "SBA_VERSCHIEDENE", None, None),
    ("6815", "Bürobedarf",                                 "X", "SBA_VERSCHIEDENE", None, None),
    ("6825", "Rechts- und Beratungskosten",                "X", "SBA_VERSCHIEDENE", None, None),
    ("6827", "Abschluss- und Prüfungskosten",              "X", "SBA_VERSCHIEDENE", None, None),
    ("6837", "Aufwendungen für Software, Lizenzen und Cloud-Dienste",
                                                            "X", "SBA_VERSCHIEDENE", None, None),
    ("6840", "Werbe- und Reisekosten",                     "X", "SBA_VERSCHIEDENE", None, None),
    ("6855", "Nebenkosten des Geldverkehrs",               "X", "SBA_VERSCHIEDENE", None, None),
    ("6880", "Aufwendungen aus Währungsumrechnungen",      "X", "SBA_UEBRIGE",      None, None),

    # ---------------------------------------------------------- FINANZERGEBNIS
    ("7100", "Sonstige Zinsen und ähnliche Erträge",       "E", "ZINSERTRAG",    None, None),
    ("7310", "Zinsaufwendungen für kurzfristige Verbindlichkeiten",
                                                            "X", "ZINSAUFWAND",   None, None),

    # ---------------------------------------------------------------- STEUERN
    ("7600", "Körperschaftsteuer",                         "X", "STEUERN_EE",    "nabzb", None),
    ("7608", "Solidaritätszuschlag",                       "X", "STEUERN_EE",    "nabzb", None),
    ("7610", "Gewerbesteuer",                              "X", "STEUERN_EE",    "nabzb", None),

    # -------------------------------------------------------------- ABSCHLUSS
    ("7700", "Gewinnvortrag nach Verwendung",              "P", "EK_BILANZGEWINN", None, None),
    ("9000", "Saldenvorträge Sachkonten",                  "P", "EROEFFNUNG",      None, None),
]

# Gliederung Bilanz (Reihenfolge = Ausweisreihenfolge)
BILANZ_AKTIVA = [
    ("A. Anlagevermögen", None),
    ("  I. Sachanlagen", None),
    ("    1. andere Anlagen, Betriebs- und Geschäftsausstattung", ["AV_SACH_BGA"]),
    ("B. Umlaufvermögen", None),
    ("  I. Forderungen und sonstige Vermögensgegenstände", None),
    ("    1. Forderungen aus Lieferungen und Leistungen", ["UV_FORD_LL"]),
    ("    2. sonstige Vermögensgegenstände", ["UV_SONST_VG", "UV_SONST_ZD"]),
    ("  II. Kassenbestand, Guthaben bei Kreditinstituten", ["UV_BANK"]),
]

BILANZ_PASSIVA = [
    ("A. Eigenkapital", None),
    ("  I. Gezeichnetes Kapital", ["EK_GEZ"]),
    # § 272 Abs. 1 Satz 3 HGB: der nicht eingeforderte Teil wird offen vom
    # gezeichneten Kapital abgesetzt, nicht als Forderung aktiviert.
    ("  II. abzüglich nicht eingeforderter ausstehender Einlagen", ["EK_AUSSTEHEND"]),
    ("  III. Bilanzgewinn / Bilanzverlust", ["EK_BILANZGEWINN", "EK_ERGEBNISVERW"]),
    ("B. Rückstellungen", None),
    ("    1. Steuerrückstellungen", ["RST_STEUERN"]),
    ("    2. sonstige Rückstellungen", ["RST_SONSTIGE"]),
    ("C. Verbindlichkeiten", None),
    ("    1. Verbindlichkeiten aus Lieferungen und Leistungen", ["VB_LL"]),
    ("    2. sonstige Verbindlichkeiten", ["VB_SONSTIGE"]),
]

# Gliederung GuV (Gesamtkostenverfahren, § 275 Abs. 2 HGB, kleine KapG)
GUV = [
    ("1. Umsatzerlöse", ["UMSATZ"]),
    ("2. sonstige betriebliche Erträge", ["SBE_UEBRIGE"]),
    ("3. Personalaufwand", None),
    ("   a) Löhne und Gehälter", ["PERSONAL_LOEHNE"]),
    ("   b) soziale Abgaben", ["PERSONAL_SOZIAL"]),
    ("4. Abschreibungen auf Sachanlagen", ["ABSCHREIBUNGEN"]),
    ("5. sonstige betriebliche Aufwendungen", None),
    ("   a) Versicherungen, Beiträge und Abgaben", ["SBA_VERS_BEITR"]),
    ("   b) verschiedene betriebliche Kosten", ["SBA_VERSCHIEDENE"]),
    ("   c) übrige sonstige betriebliche Aufwendungen", ["SBA_UEBRIGE"]),
    ("6. sonstige Zinsen und ähnliche Erträge", ["ZINSERTRAG"]),
    ("7. Zinsen und ähnliche Aufwendungen", ["ZINSAUFWAND"]),
    ("8. Steuern vom Einkommen und vom Ertrag", ["STEUERN_EE"]),
]

ERTRAGS_POSTEN = {"UMSATZ", "SBE_UEBRIGE", "ZINSERTRAG"}
