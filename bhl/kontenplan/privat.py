# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Kontenrahmen fuer die private Einkommensteuer (Zusammenveranlagung).

Doppelte Buchfuehrung als *Erfassungstechnik*, Ueberschussrechnung als
*Auswertung*. Das ist der ganze Trick: gebucht wird wie bei einer
Kapitalgesellschaft - jeder Vorgang beruehrt zwei Konten, der Bankbestand
kontrolliert die Vollstaendigkeit -, ausgewertet wird aber nach § 11 EStG
(Zufluss/Abfluss) und nicht nach dem Realisationsprinzip.

Daraus folgen drei Regeln, die dieser Rahmen durchhaelt:

1. **Erfolgswirksam nur bei Zahlung.** Keine Forderungen, keine
   Verbindlichkeiten aus Lieferung und Leistung, keine Rueckstellungen, keine
   Rechnungsabgrenzung. Ein Mietrueckstand ist keine Buchung, sondern schlicht
   kein Zufluss. Geprueft wird das von `bhl.pruefung.paragraf_11`.
2. **Zwei Ausnahmen von Regel 1**, beide gesetzlich erzwungen: die AfA nach
   § 7 EStG und die Verteilung von Erhaltungsaufwand nach § 82b EStDV. Beide
   Konten tragen deshalb das Kennzeichen `ohne_zahlung`.
3. **Die Kontonummer sagt, wo der Betrag im Formular landet.** Erfolgskonten
   sind nach dem Muster `<4|6><Objekt><Zeile der Anlage V>` nummeriert:

       4115   Objekt 1, Anlage V Zeile 15   Mieteinnahmen
       6646   Objekt 6, Anlage V Zeile 46   Schuldzinsen

   Die Zeilennummern stammen aus der Anlage V 2024 (DATEV, ausgefertigt
   26.09.2025). Aendert das Formular seine Zeilen, aendert sich hier eine
   Zahl - nicht die Systematik.

Was der Rahmen **nicht** kann und bewusst nicht koennen soll: Umsatzsteuer
(Vermietung zu Wohnzwecken ist nach § 4 Nr. 12a UStG steuerfrei) und alles,
was mit Bilanzierung zu tun hat.

Die "Bilanz" heisst hier Vermoegensuebersicht. Sie ist steuerlich bedeutungslos
- niemand verlangt sie -, aber sie ist das, was die Buchfuehrung
selbstkontrollierend macht: solange sie aufgeht, fehlt keine Buchung.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Objekt:
    """Ein Mietobjekt = eine Anlage V.

    `afa_satz` in Hundertstel Prozent (250 = 2,50 %), damit auch hier nichts
    als Gleitkommazahl gefuehrt wird. Die AfA-Bemessungsgrundlage steht
    absichtlich *nicht* hier: sie ist ein Betrag und gehoert damit in die
    Eroeffnungsbilanz, nicht in den Kontenrahmen.
    """
    nr: int
    name: str
    ort: str
    angeschafft: str | None            # ISO, None = aus der Erklaerung 2024 nicht ersichtlich
    afa_satz: int                      # 250 = 2,50 %
    aktenzeichen: str | None = None
    darlehen: tuple[tuple[str, str], ...] = ()   # (Endziffern, Bezeichnung)

    @classmethod
    def aus_json(cls, d: dict) -> "Objekt":
        """Ein Objekt aus dem Block `objekte` der mandant.json.

        Adressen, Darlehensnummern und Aktenzeichen sind Stammdaten des
        Mandanten und stehen deshalb in seiner Konfiguration, nicht hier.
        """
        return cls(
            nr=int(d["nr"]),
            name=d["name"],
            ort=d.get("ort", ""),
            angeschafft=d.get("angeschafft"),
            afa_satz=int(d.get("afa_satz", 200)),
            aktenzeichen=d.get("aktenzeichen"),
            # Jedes Darlehen bringt die letzten beiden Ziffern seiner
            # Kontonummer mit. Fehlen sie, wird der Reihe nach 10, 20, 30
            # vergeben - so bleibt eine gewachsene Nummerierung erhalten,
            # ohne dass der Code die Geldgeber kennen muss.
            darlehen=tuple(
                (str(x.get("suffix") or f"{(i + 1) * 10:02d}"), x["bezeichnung"])
                for i, x in enumerate(d.get("darlehen") or [])
            ),
        )


# Beispielobjekte. Der tatsaechliche Bestand eines Mandanten steht im Block
# `objekte` seiner mandant.json und wird beim `bh init` uebernommen; diese
# Liste traegt nur den Fall, dass keine Stammdaten hinterlegt sind.
OBJEKTE_BEISPIEL = [
    Objekt(1, "Beispielstraße 1", "10115 Berlin", "2019-01-01", 200, None,
           (("10", "Baufinanzierung"),)),
    Objekt(2, "Beispielweg 2 (MFH)", "10115 Berlin", "2020-01-01", 250, None,
           (("10", "Baufinanzierung"), ("90", "Gesellschafterdarlehen"))),
]

# Zeilen der Anlage V. (Zeile, Bezeichnung, Kennzeichen)
# 'ohne_zahlung' = darf ohne Geldkonto gebucht werden (§ 7 EStG, § 82b EStDV).
V_EINNAHMEN = [
    (15, "Mieteinnahmen für Wohnungen (ohne Umlagen)", None),
    (18, "Mieteinnahmen für andere Räume (ohne Umlagen)", None),
    (20, "Umlagen: laufende Neben- und Betriebskosten", None),
    (21, "Umlagen: Nachzahlungen und Erstattungen", None),
    (29, "Öffentliche Zuschüsse und sonstige Einnahmen", None),
]

V_WERBUNGSKOSTEN = [
    (33, "Absetzung für Abnutzung, linear", "ohne_zahlung"),
    (46, "Schuldzinsen (ohne Tilgung)", None),
    (55, "Erhaltungsaufwand, im Jahr voll abziehbar", None),
    (56, "Erhaltungsaufwand, verteilt nach § 82b EStDV", "ohne_zahlung"),
    (73, "Umgelegte Kosten (Grundsteuer, Wasser, Hauswart …)", None),
    (76, "Nicht umgelegte Kosten (Verwaltung, Kontoführung …)", None),
]

# Summenzeilen der Anlage V - reine Rechenzeilen, keine Konten.
V_SUMMEN = {"einnahmen": 32, "werbungskosten": 83, "ergebnis": 85}


def konto_nr(objekt: int, zeile: int, ertrag: bool) -> str:
    """`4<Objekt><Zeile>` fuer Einnahmen, `6<Objekt><Zeile>` fuer Werbungskosten."""
    return f"{4 if ertrag else 6}{objekt}{zeile:02d}"


def posten(objekt: int, zeile: int) -> str:
    return f"V{objekt}_Z{zeile:02d}"


def _objektkonten(objekte) -> list[tuple]:
    """Je Objekt: Bestandskonten, Darlehen und die Zeilen der Anlage V."""
    k = []
    for o in objekte:
        n = o.nr
        k += [
            # Grund und Boden wird nicht abgeschrieben - deshalb ein eigenes
            # Konto und nicht ein gemeinsames mit dem Gebaeude.
            (f"0{n}10", f"Grund und Boden {o.name}", "A", "VM_GRUND", None, None),
            (f"0{n}20", f"Gebäude {o.name}", "A", "VM_GEBAEUDE", None, None),
            (f"0{n}29", f"Kumulierte AfA {o.name}", "A", "VM_AFA_KUM", None, None),
            # Traeger fuer den noch nicht abgezogenen Teil eines nach § 82b
            # EStDV verteilten Erhaltungsaufwands. Die Zahlung ist im Jahr des
            # Abflusses gebucht; abgezogen wird nur die Jahrestranche ueber
            # Zeile 56. Der Rest steht bis zu seinem Abzug hier.
            (f"0{n}80", f"Noch nicht abgezogener Erhaltungsaufwand § 82b EStDV "
                        f"{o.name}", "A", "VM_ERHALT_82B", None, None),
        ]
        for suffix, bez in o.darlehen:
            k.append((f"3{n}{suffix}", f"Darlehen {bez} – {o.name}",
                      "P", "VB_DARLEHEN", None, None))
        for zeile, bez, kz in V_EINNAHMEN:
            k.append((konto_nr(n, zeile, True), f"{o.name}: {bez}", "E",
                      posten(n, zeile), kz, None))
        for zeile, bez, kz in V_WERBUNGSKOSTEN:
            k.append((konto_nr(n, zeile, False), f"{o.name}: {bez}", "X",
                      posten(n, zeile), kz, None))
    return k


# (nummer, bezeichnung, typ, posten, steuer_tag, ustva_kz)
KONTEN_BASIS = [
    # ------------------------------------------------- selbstgenutzte Wohnung
    # Kein Objekt der Anlage V, gehoert aber in die Buchfuehrung: von hier
    # kommen die Ermaessigungen nach § 35a und § 35c EStG, und der private
    # Teil des Gesellschafterdarlehens haengt daran.
    ("0010", "Grund und Boden selbstgenutzte Wohnung", "A", "VM_GRUND", None, None),
    ("0020", "Gebäude selbstgenutzte Wohnung", "A", "VM_GEBAEUDE", None, None),

    # ------------------------------------------------------------ Geldkonten
    # Werden beim Import der Kontoauszuege den IBANs zugeordnet
    # (mandant.json -> bankkonten). Bezeichnungen sind vorlaeufig, solange
    # nicht feststeht, welche Konten es 2025 tatsaechlich gab.
    ("1200", "Girokonto privat", "A", "VM_BANK", None, None),
    ("1201", "Tagesgeldkonto", "A", "VM_BANK", None, None),
    ("1210", "Gemeinschaftskonto", "A", "VM_BANK", None, None),
    ("1220", "Mietkonto / Objektkonto", "A", "VM_BANK", None, None),
    ("1290", "Verrechnungskonto ungeklärt", "A", "VM_BANK", None, None),
    ("1300", "Wertpapierdepot", "A", "VM_DEPOT", None, None),

    # ------------------------------------------- anrechenbare Steuerabzuege
    # Keine Aufwendungen, sondern Vorauszahlungen auf die eigene Steuerschuld:
    # sie mindern nicht die Einkuenfte, sondern werden auf die festgesetzte
    # Steuer angerechnet. Als Forderung an den Fiskus gefuehrt.
    ("1510", "Einkommensteuer-Vorauszahlungen", "A", "VM_STEUER", "anrechnung_est", None),
    ("1511", "Solidaritätszuschlag-Vorauszahlungen", "A", "VM_STEUER", "anrechnung_solz", None),
    ("1520", "Einbehaltene Lohnsteuer", "A", "VM_STEUER", "anrechnung_lst", None),
    ("1521", "Einbehaltener Solidaritätszuschlag (Lohn)", "A", "VM_STEUER", "anrechnung_solz", None),
    ("1530", "Einbehaltene Kapitalertragsteuer", "A", "VM_STEUER", "anrechnung_kap", None),

    # ------------------------------------------------------------- Passiva
    ("2000", "Privatvermögen (Saldo)", "P", "VM_EIGEN", None, None),
    ("2100", "Ergebnisvortrag", "P", "VM_EIGEN", None, None),
    ("3090", "Gesellschafterdarlehen – selbstgenutzte Wohnung",
     "P", "VB_DARLEHEN", None, None),
    ("3800", "Erhaltene Mietkautionen", "P", "VB_KAUTION", None, None),
    ("3900", "Sonstige private Verbindlichkeiten", "P", "VB_SONSTIGE", None, None),

    # ------------------------------------------ Anlage N (nichtselbständig)
    # Gebucht wird brutto: das Netto geht auf die Bank, die einbehaltenen
    # Steuern auf die Anrechnungskonten, die Sozialabgaben in die
    # Sonderausgaben. Nur so stimmt der Bankbestand.
    ("4010", "Bruttoarbeitslohn Ehemann", "E", "N1_BRUTTO", None, None),
    ("4020", "Bruttoarbeitslohn Ehefrau", "E", "N2_BRUTTO", None, None),
    ("4030", "Lohnersatzleistungen (Progressionsvorbehalt § 32b EStG)",
     "E", "PV_LOHNERSATZ", "32b", None),
    ("6010", "Werbungskosten Ehemann", "X", "N1_WK", None, None),
    ("6020", "Werbungskosten Ehefrau", "X", "N2_WK", None, None),

    # --------------------------------------------- Anlage S (selbständig)
    ("4100", "Betriebseinnahmen Ehefrau (§ 18 EStG)", "E", "S2_EINNAHMEN", None, None),
    ("6100", "Betriebsausgaben Ehefrau (§ 18 EStG)", "X", "S2_AUSGABEN", None, None),

    # ------------------------------------------------- Anlage KAP (§ 20 EStG)
    ("4200", "Kapitalerträge", "E", "KAP_ERTRAG", None, None),
    ("4210", "Veräußerungsgewinne Wertpapiere", "E", "KAP_ERTRAG", None, None),
    ("6200", "Veräußerungsverluste Wertpapiere", "X", "KAP_VERLUST", None, None),

    # ---------------------------------------------------------- Sonderausgaben
    ("6300", "Vorsorgeaufwendungen (Kranken- und Pflegeversicherung)",
     "X", "SA_VORSORGE", None, None),
    ("6310", "Altersvorsorgeaufwendungen", "X", "SA_VORSORGE", None, None),
    ("6320", "Übrige Vorsorgeaufwendungen", "X", "SA_VORSORGE", None, None),
    ("6330", "Kirchensteuer", "X", "SA_UEBRIGE", None, None),
    ("6340", "Spenden und Mitgliedsbeiträge", "X", "SA_UEBRIGE", None, None),
    ("6390", "Sonstige abzugsfähige Sonderausgaben", "X", "SA_UEBRIGE", None, None),
    ("6350", "Kinderbetreuungskosten", "X", "SA_KINDER", None, None),
    ("6360", "Außergewöhnliche Belastungen", "X", "AG_BELASTUNG", None, None),

    # ------------------------------------ Steuerermaessigungen § 35a / § 35c
    # Keine Werbungskosten und keine Sonderausgaben, sondern ein Abzug von der
    # Steuer selbst. Sie stehen deshalb in keiner Einkunftsart - die
    # Auswertung liest sie ueber `steuer_tag`.
    ("6400", "Haushaltsnahe Dienstleistungen (§ 35a Abs. 2 EStG)",
     "X", "PRIV_LEBEN", "35a_dienstleistung", None),
    ("6410", "Handwerkerleistungen (§ 35a Abs. 3 EStG)",
     "X", "PRIV_LEBEN", "35a_handwerker", None),
    ("6420", "Energetische Maßnahmen (§ 35c EStG)",
     "X", "PRIV_LEBEN", "35c_energetisch", None),

    # ------------------------------------------------- private Lebensfuehrung
    # § 12 EStG: steuerlich unbeachtlich, fuer die Vollstaendigkeit der
    # Buchfuehrung aber unverzichtbar - ohne diese Konten geht der
    # Bankbestand nicht auf.
    ("6900", "Private Lebenshaltung", "X", "PRIV_LEBEN", None, None),
    ("6910", "Selbstgenutzte Wohnung", "X", "PRIV_LEBEN", None, None),
    ("6920", "Zinsen selbstgenutzte Wohnung (nicht abziehbar)", "X", "PRIV_LEBEN", None, None),
    ("6930", "Einkommensteuer-Nachzahlung / Erstattung (§ 12 Nr. 3 EStG)",
     "X", "PRIV_LEBEN", None, None),

    # -------------------------------------------------------------- Abschluss
    ("9000", "Saldenvorträge Sachkonten", "P", "EROEFFNUNG", None, None),
]


# ---------------------------------------------------------------------------
# Vermoegensuebersicht (die "Bilanz" - steuerlich ohne Bedeutung, aber sie
# macht die Buchfuehrung kontrollierbar)

BILANZ_AKTIVA = [
    ("A. Grundvermögen", None),
    ("    1. Grund und Boden", ["VM_GRUND"]),
    ("    2. Gebäude (Anschaffungskosten)", ["VM_GEBAEUDE"]),
    ("    3. abzüglich kumulierter Absetzungen für Abnutzung", ["VM_AFA_KUM"]),
    ("    4. noch nicht abgezogener Erhaltungsaufwand § 82b EStDV",
     ["VM_ERHALT_82B"]),
    ("B. Geldvermögen", None),
    ("    1. Bankguthaben", ["VM_BANK"]),
    ("    2. Wertpapiere", ["VM_DEPOT"]),
    ("C. Anrechenbare Steuerabzüge", ["VM_STEUER"]),
]

# Die Zeile, die das Ergebnis des Jahres aufnimmt (es steckt noch in den
# Erfolgskonten und auf keinem Bestandskonto) - bei der Kapitalgesellschaft
# heisst sie Bilanzgewinn, hier Privatvermögen.
ERGEBNIS_ZEILE = "Privatvermögen"

TITEL_BILANZ = "Vermögensübersicht"
TITEL_GUV = "Ermittlung des zu versteuernden Einkommens"
TITEL_ERGEBNIS = "Zu versteuerndes Einkommen"
TITEL_STEUERN = "Einkommensteuererklärung"

BILANZ_PASSIVA = [
    ("A. Privatvermögen", ["VM_EIGEN"]),
    ("B. Verbindlichkeiten", None),
    ("    1. Darlehen", ["VB_DARLEHEN"]),
    ("    2. erhaltene Mietkautionen", ["VB_KAUTION"]),
    ("    3. sonstige Verbindlichkeiten", ["VB_SONSTIGE"]),
]


# ---------------------------------------------------------------------------
# Ueberschussrechnung: die Zusammenfassung ueber alle Einkunftsarten.
# Das Gegenstueck zur GuV, aber nach § 2 Abs. 2 EStG gegliedert.

def _vv_posten(objekte, zeile: int) -> list[str]:
    """Dieselbe Zeile der Anlage V ueber alle Objekte."""
    return [posten(o.nr, zeile) for o in objekte]


def _vv_alle(objekte, zeilen) -> list[str]:
    return [p for z, _, _ in zeilen for p in _vv_posten(objekte, z)]


def _guv(objekte) -> list[tuple]:
  return [
    ("1. Einkünfte aus nichtselbständiger Arbeit (§ 19 EStG)", None),
    ("   a) Bruttoarbeitslohn", ["N1_BRUTTO", "N2_BRUTTO"]),
    ("   b) Werbungskosten", ["N1_WK", "N2_WK"]),
    ("2. Einkünfte aus selbständiger Arbeit (§ 18 EStG)", None),
    ("   a) Betriebseinnahmen", ["S2_EINNAHMEN"]),
    ("   b) Betriebsausgaben", ["S2_AUSGABEN"]),
    ("3. Einkünfte aus Vermietung und Verpachtung (§ 21 EStG)", None),
    ("   a) Einnahmen", _vv_alle(objekte, V_EINNAHMEN)),
    ("   b) Werbungskosten", _vv_alle(objekte, V_WERBUNGSKOSTEN)),
    ("4. Einkünfte aus Kapitalvermögen (§ 20 EStG)", None),
    ("   a) Erträge", ["KAP_ERTRAG"]),
    ("   b) Verluste", ["KAP_VERLUST"]),
    ("5. Sonderausgaben und außergewöhnliche Belastungen", None),
    ("   a) Vorsorgeaufwendungen", ["SA_VORSORGE"]),
    ("   b) übrige Sonderausgaben", ["SA_UEBRIGE"]),
    ("   c) Kinderbetreuungskosten", ["SA_KINDER"]),
    ("   d) außergewöhnliche Belastungen", ["AG_BELASTUNG"]),
  ]

# Posten, die im Haben stehen und deshalb positiv auszuweisen sind.
def _ertrags_posten(objekte) -> set:
    return ({"N1_BRUTTO", "N2_BRUTTO", "S2_EINNAHMEN", "KAP_ERTRAG",
             "PV_LOHNERSATZ"} | set(_vv_alle(objekte, V_EINNAHMEN)))


# ---------------------------------------------------------------------------
# Anlagen: je Objekt eine Anlage V, gegliedert wie das Formular.

def anlage_v(o: Objekt) -> list[tuple]:
    """Die Gliederung einer Anlage V - Zeilennummern wie im Formular.

    Die Summenzeilen 32, 83 und 85 stehen bewusst nicht darin: sie sind
    Rechenzeilen ohne Konto und werden von der Auswertung selbst gebildet.
    """
    g = [("Einnahmen", None)]
    for zeile, bez, _ in V_EINNAHMEN:
        g.append((f"  Zeile {zeile}  {bez}", [posten(o.nr, zeile)]))
    g.append(("Werbungskosten", None))
    for zeile, bez, _ in V_WERBUNGSKOSTEN:
        g.append((f"  Zeile {zeile}  {bez}", [posten(o.nr, zeile)]))
    return g


def _anlagen(objekte) -> list[dict]:
  return [
    {"id": f"v{o.nr}", "art": "V", "nr": o.nr,
     "titel": f"{o.nr}. Anlage V – {o.name}",
     "objekt": o, "gliederung": anlage_v(o),
     "einnahmen": [posten(o.nr, z) for z, _, _ in V_EINNAHMEN],
     "werbungskosten": [posten(o.nr, z) for z, _, _ in V_WERBUNGSKOSTEN],
     # Aus der AfA laesst sich die Bemessungsgrundlage zurueckrechnen, solange
     # das Anlagenverzeichnis fehlt - siehe `reports.anlage_daten`.
     "afa_posten": posten(o.nr, 33)}
    for o in objekte
  ]


# Konten, die ohne Geldkonto bebucht werden duerfen (§ 7 EStG, § 82b EStDV).
def _ohne_zahlung(objekte) -> frozenset:
    return frozenset(
        konto_nr(o.nr, z, False)
        for o in objekte for z, _, kz in V_WERBUNGSKOSTEN if kz
    )


def baue(stammdaten: dict) -> dict:
    """Die objektabhaengigen Teile des Rahmens fuer einen Mandanten.

    `stammdaten` ist der Inhalt von mandant.json. Fehlt der Block `objekte`,
    greifen die Beispielobjekte - dann rechnet der Rahmen, aber er beschreibt
    niemanden.
    """
    roh = stammdaten.get("objekte")
    objekte = [Objekt.aus_json(d) for d in roh] if roh else OBJEKTE_BEISPIEL
    return {
        "konten": KONTEN_BASIS + _objektkonten(objekte),
        "guv": _guv(objekte),
        "ertrags_posten": _ertrags_posten(objekte),
        "anlagen": _anlagen(objekte),
        "ohne_zahlung": _ohne_zahlung(objekte),
    }

# Konten, ueber die Geld fliesst. Jede erfolgswirksame Buchung muss eines
# davon beruehren - das ist die Pruefung auf § 11 EStG.
GELDKONTEN = frozenset(["1200", "1201", "1210", "1220", "1290", "1300"])
