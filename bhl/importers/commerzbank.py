# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Parser fuer Commerzbank-Kontoauszuege (PDF -> pdftotext -layout -> Text).

Die Auszuege haben je Umsatz eine Kopfzeile
    <Empfaenger/Verwendung>   <Valuta dd.mm.>   <Betrag>[-]
gefolgt von beliebig vielen Fortsetzungszeilen. Lastschriften/Ueberweisungen
zu Lasten des Kontos tragen ein nachgestelltes Minus.

Zusaetzlich wird der CSV-Export ("Buchungstag;Wertstellung;...") unterstuetzt.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import re
import subprocess
from dataclasses import dataclass, field

RE_AUSZUG = re.compile(r"^Auszug-Nr\.\s+(\d+)\s")
RE_IBAN = re.compile(r"^IBAN\s*:\s*([A-Z]{2}[\d ]+)")
RE_KONTONR = re.compile(r"^Kontonummer\s*:\s*([\d ]+?)\s{2,}")
RE_BLZ = re.compile(r"^Bankleitzahl\s*:\s*([\d ]+)")


def _iban(blz: str, konto: str) -> str:
    """IBAN aus Bankleitzahl und Kontonummer (DE, Pruefziffer nach ISO 13616)."""
    bban = blz + konto.rjust(10, "0")
    rest = int("".join(str(int(c, 36)) for c in bban + "DE00")) % 97
    return f"DE{98 - rest:02d}{bban}"
RE_ALT = re.compile(r"^\s*Alter Kontostand vom (\d{2}\.\d{2}\.\d{4})\s+([\d.]+,\d{2})(-?)\s*$")
RE_NEU = re.compile(r"^\s*Neuer Kontostand vom (\d{2}\.\d{2}\.\d{4})\s+([\d.]+,\d{2})(-?)\s*$")
RE_BDAT = re.compile(r"^\s*Buchungsdatum:\s*(\d{2}\.\d{2}\.\d{4})\s*$")
# Kopfzeile eines Umsatzes: Text, Valuta (dd.mm.), Betrag, optional '-'.
# Aeltere Vordrucke setzen die Valuta ohne abschliessenden Punkt.
RE_UMSATZ = re.compile(r"^\s+(\S.*?)\s{2,}(\d{2}\.\d{2})\.?\s{2,}([\d.]+,\d{2})(-?)\s*$")

# Zeilen des Vordrucks, die kein Umsatztext sind. Der Kopf eines Auszugs
# traegt daneben die Anschrift des Kontoinhabers und den Namen des Beraters -
# beides steht je Mandant im Block `kontoauszug_kopfzeilen` der mandant.json,
# weil es von Kunde zu Kunde verschieden ist und hier nichts verloren hat.
SKIP_PREFIXE = (
    "Kontoauszug vom", "Auszug-Nr.", "IBAN :", "BIC :", "Ihr Ansprechpartner",
    "Telefon", "Kontowährung", "zu Ihren Lasten", "Angaben zu den Umsätzen",
    "Folgeseite vorhanden", "Unternehmerkundenberatung", "USt-IdNr.",
    "Guthaben sind als Einlagen", "können dem", "Der angegebene Kontostand",
    "Betrag nicht dem", "möglicherweise Zinsen",
)


def _c(s: str) -> int:
    return int(round(float(s.replace(".", "").replace(",", ".")) * 100))


@dataclass
class Umsatz:
    buchungsdatum: str          # YYYY-MM-DD
    valuta: str
    betrag_cent: int            # + Eingang, - Ausgang
    text: str
    auszug: str = ""
    zeilen: list[str] = field(default_factory=list)

    @property
    def hash(self) -> str:
        roh = f"{self.buchungsdatum}|{self.betrag_cent}|{self.text}"
        return hashlib.sha1(roh.encode()).hexdigest()[:16]


@dataclass
class Auszug:
    iban: str
    nummer: str
    alter_stand: int | None
    alter_stand_datum: str | None
    neuer_stand: int | None
    neuer_stand_datum: str | None
    umsaetze: list[Umsatz]


# --- Sperrsatz -------------------------------------------------------------
# Aeltere Vordrucke (und alle Auszuege der Privatkonten) sind gesperrt gesetzt:
# jeder Buchstabe steht einzeln. `pdftotext -layout` kann Buchstaben- und
# Wortabstand nicht unterscheiden und macht daraus Woerter aus einem Zeichen.
# Aus den Glyphenkoordinaten laesst sich beides trennen - der Buchstabenabstand
# liegt bei 1 bis 2 Punkt, der Wortabstand bei 6 bis 8.

RE_WORD = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>')
RE_SEITE = re.compile(r'<page width="[\d.]+" height="[\d.]+">')

WORTLUECKE = 3.5       # ab hier ein Leerzeichen statt Wortfortsetzung
SPALTENLUECKE = 12.0   # ab hier eine eigene Spalte
ZEILENHOEHE = 3.0      # Grundlinien, die naeher liegen, sind dieselbe Zeile
ZEICHEN = 5.0          # Punkt je Spalte
ANKER = ("Kontoauszug vom", "Auszug-Nr.", "IBAN", "BIC", "Alter Kontostand",
         "Neuer Kontostand", "Buchungsdatum:", "Angaben zu den")


def _rand(zeilen: list[str]) -> int:
    """Linker Rand des Auszugs, gemessen an den festen Zeilen des Vordrucks."""
    anker = [len(z) - len(z.lstrip()) for z in zeilen if z.strip().startswith(ANKER)]
    return min(anker) if anker else min(
        (len(z) - len(z.lstrip()) for z in zeilen if z.strip()), default=0)


def _sperrsatz_text(pfad: str) -> str:
    """Auszugstext aus den Glyphenkoordinaten zusammensetzen."""
    roh = subprocess.run(["pdftotext", "-bbox-layout", pfad, "-"],
                         capture_output=True, text=True).stdout
    teile = RE_SEITE.split(roh)
    aus: list[str] = []
    for seite in (teile[1:] if len(teile) > 1 else [roh]):
        woerter = [(float(a), float(b), float(c), html.unescape(t))
                   for a, b, c, t in RE_WORD.findall(seite)]
        if not woerter:
            continue
        x0 = min(w[0] for w in woerter)
        zeilen: list[list] = []
        for w in sorted(woerter, key=lambda w: (w[1], w[0])):
            if zeilen and abs(zeilen[-1][0][1] - w[1]) < ZEILENHOEHE:
                zeilen[-1].append(w)
            else:
                zeilen.append([w])
        gesetzt: list[str] = []
        for zl in zeilen:
            zl.sort(key=lambda w: w[0])
            zeile, prev = "", None
            for xmin, _, xmax, t in zl:
                if prev is None:
                    zeile = " " * int(round((xmin - x0) / ZEICHEN))
                elif xmin - prev >= SPALTENLUECKE:
                    zeile += " " * max(2, int(round((xmin - x0) / ZEICHEN)) - len(zeile))
                elif xmin - prev >= WORTLUECKE:
                    zeile += " "
                zeile += t
                prev = xmax
            gesetzt.append(zeile.rstrip())
        rand = _rand(gesetzt)
        aus += [("  " + z[rand:] if z.strip() else "") for z in gesetzt]
    return "\n".join(aus) + "\n"


RE_GESPERRT = re.compile(r"(?:\S ){10}")


def _pdf_text(pfad: str) -> str:
    text = subprocess.run(
        ["pdftotext", "-layout", pfad, "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    # Zehn Zeichen in Folge, jedes fuer sich: das ist Sperrsatz, kein Text.
    if RE_GESPERRT.search(text):
        return _sperrsatz_text(pfad)
    return text


def _iso(d: str) -> str:
    t, m, j = d.split(".")
    return f"{j}-{m}-{t}"


def parse_auszug(pfad: str, kopfzeilen: tuple = ()) -> Auszug:
    """Liest einen Kontoauszug (PDF oder bereits extrahierter Text).

    `kopfzeilen` sind zusaetzliche Zeilenanfaenge, die zum Briefkopf gehoeren
    und nicht zum Umsatztext - Anschrift und Berater des Kontoinhabers.
    """
    text = _pdf_text(pfad) if pfad.lower().endswith(".pdf") else open(pfad, encoding="utf-8").read()
    iban = nummer = kontonr = ""
    alt = neu = None
    alt_d = neu_d = None
    umsaetze: list[Umsatz] = []
    aktuelles_datum: str | None = None
    letzter: Umsatz | None = None

    for zeile in text.splitlines():
        roh = zeile.rstrip()
        if not roh.strip():
            continue
        s = roh.strip()

        if not iban and (m := RE_IBAN.match(s)):
            iban = m.group(1).replace(" ", "")
        if not iban and (m := RE_KONTONR.match(s + "  ")):
            kontonr = m.group(1).replace(" ", "")
        if not iban and kontonr and (m := RE_BLZ.match(s)):
            iban = _iban(m.group(1).replace(" ", ""), kontonr)
        if not nummer and (m := RE_AUSZUG.match(s)):
            nummer = m.group(1)

        if m := RE_ALT.match(roh):
            if alt is None:
                alt_d, alt = _iso(m.group(1)), _c(m.group(2)) * (-1 if m.group(3) else 1)
            letzter = None
            continue
        if m := RE_NEU.match(roh):
            neu_d, neu = _iso(m.group(1)), _c(m.group(2)) * (-1 if m.group(3) else 1)
            letzter = None
            continue
        if m := RE_BDAT.match(roh):
            aktuelles_datum = _iso(m.group(1))
            letzter = None
            continue

        if (m := RE_UMSATZ.match(roh)) and aktuelles_datum:
            betrag = _c(m.group(3)) * (-1 if m.group(4) else 1)
            letzter = Umsatz(
                buchungsdatum=aktuelles_datum,
                valuta=m.group(2) + "." + aktuelles_datum[:4],
                betrag_cent=betrag,
                text=m.group(1).strip(),
                auszug=nummer,
            )
            umsaetze.append(letzter)
            continue

        if any(s.startswith(p) for p in SKIP_PREFIXE + tuple(kopfzeilen)):
            continue
        if letzter is not None:
            letzter.zeilen.append(s)

    for u in umsaetze:
        if u.zeilen:
            u.text = (u.text + " " + " ".join(u.zeilen)).strip()
    return Auszug(iban, nummer, alt, alt_d, neu, neu_d, umsaetze)


def parse_csv(pfad: str) -> list[Umsatz]:
    """Commerzbank-CSV-Export."""
    with open(pfad, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    out = []
    for r in rows:
        betrag = int(round(float(r["Betrag"].replace(",", ".")) * 100))
        out.append(Umsatz(
            buchungsdatum=_iso(r["Buchungstag"]),
            valuta=r["Wertstellung"],
            betrag_cent=betrag,
            text=" ".join(r["Buchungstext"].split()),
        ))
    return out


def pruefe(a: Auszug) -> tuple[bool, int]:
    """Kontrollrechnung: Anfangssaldo + Summe Umsaetze == Endsaldo."""
    if a.alter_stand is None or a.neuer_stand is None:
        return False, 0
    soll = a.alter_stand + sum(u.betrag_cent for u in a.umsaetze)
    return soll == a.neuer_stand, a.neuer_stand - soll
