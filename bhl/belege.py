# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Belegablage.

Grundgedanke: Jeder Beleg wird einmal ins Archiv kopiert (Original bleibt, wo es
ist), bekommt eine fortlaufende Belegnummer und einen Datenbankeintrag. Die
Belegnummer steht spaeter im Buchungssatz (Feld `belegfeld`), damit jede Buchung
zum Papier zurueckverfolgt werden kann.

Archivstruktur:
    mandanten/<kuerzel>/belege/<jahr>/<kategorie>/B<jahr>-<nnnn>_<datum>_<slug>.<ext>

Die Zuordnung Kategorie/Aussteller/Datum/Betrag erfolgt regelbasiert
(Dateiname + Volltext), ohne LLM. Alles laesst sich beim Import ueberschreiben.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import unicodedata

KATEGORIEN = [
    "bank",             # Kontoauszuege, Depotauszuege
    "wertpapiere",      # Abrechnungen, Steuermitteilungen
    "beteiligungen",    # Capital Calls, Distributions, Reports, Vertraege
    "steuern",          # Bescheide, Anmeldungen, Erklaerungen
    "lohn",             # Lohnabrechnungen, Lohnjournal, Meldungen
    "eingangsrechnung", # Lieferantenrechnungen
    "ausgangsrechnung", # eigene Rechnungen an Kunden
    "gutschrift",       # eigene Gutschriften (Entgeltminderung §17 UStG)
    "vertraege",        # Darlehen, Gesellschaftsvertraege, Beschluesse
    "jahresabschluss",  # JA, E-Bilanz, Offenlegung
    "sonstiges",
]

# (Regex auf Dateiname+Text, Kategorie, Aussteller)
REGELN: list[tuple[str, str, str | None]] = [
    (r"Kontoauszug|Kontoumsatz|Umsatzdetails|DirektDepot|Darlehenskontoauszug", "bank", "Commerzbank"),
    # "Wertpapier-Ertrag" muss vor der allgemeinen Rechnungsregel stehen: die
    # Nullavise der Commerzbank enthalten im Text das Wort "Rechnung" und
    # landeten sonst bei den Eingangsrechnungen - also dort, wo eine fehlende
    # Buchung nach einer Luecke aussieht.
    (r"Wertpapierverkauf|Wertpapierkauf|Wertpapier-Ertrag|GESCH.FTSABRECHNUNG|Steuerliche Behandlung|Orderprotokoll|Ex-Ante", "wertpapiere", "Commerzbank"),
    (r"Jahressteuerbescheinigung|Steuerbescheinigung", "wertpapiere", "Commerzbank"),
    (r"Drawdown Notice|Kapitalabruf|Capital Call|Distribution Notice|Ergebnismitteilung|Quarterly Report|CapCall", "beteiligungen", None),
    (r"Bescheid|Finanzamt|Steuererkl.rung|ELSTER|Vorauszahlung", "steuern", "Finanzamt"),
    (r"Lohnabrechnung|Lohnjournal|Entgeltabrechnung|Lohnsteuerbescheinigung|Beitragsnachweis", "lohn", None),
    (r"Bundesanzeiger", "eingangsrechnung", "Bundesanzeiger Verlag GmbH"),
    (r"Rechnung|Invoice|Receipt|Quittung|Beleg", "eingangsrechnung", None),
    (r"Vertrag|Beschluss|Urkunde|UVZ-Nr|Nachtrag|Vereinbarung", "vertraege", None),
    (r"Jahresabschluss|E-Bilanz|Offenlegung|Erstellungsbericht", "jahresabschluss", None),
]

RE_DATUM = [
    re.compile(r"(\d{2})\.(\d{2})\.(\d{4})"),
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
]

# Monatsnamen im Dateinamen, z. B. "Juli 2025", "Dez25", "Mar26", "Sept 2025"
MONATE = {"jan": 1, "feb": 2, "mar": 3, "mär": 3, "apr": 4, "mai": 5, "may": 5,
          "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "oct": 10,
          "nov": 11, "dez": 12, "dec": 12}
RE_MONAT = re.compile(
    r"(jan|feb|mar|mär|apr|mai|may|jun|jul|aug|sep|okt|oct|nov|dez|dec)[a-zä]*\.?\s*'?(\d{2}|\d{4})\b",
    re.IGNORECASE)
RE_YMD = re.compile(r"\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b")


def _slug(s: str, n: int = 48) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
           .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue").replace("ß", "ss"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s[:n] or "beleg"


def sha256(pfad: str) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def volltext(pfad: str, max_zeichen: int = 8000) -> str:
    if pfad.lower().endswith(".pdf"):
        try:
            return subprocess.run(["pdftotext", "-layout", "-l", "3", pfad, "-"],
                                  capture_output=True, text=True, timeout=30).stdout[:max_zeichen]
        except Exception:
            return ""
    if pfad.lower().endswith((".txt", ".csv", ".md")):
        try:
            return open(pfad, encoding="utf-8", errors="replace").read(max_zeichen)
        except Exception:
            return ""
    return ""


def klassifiziere(dateiname: str, text: str, eigene=()) -> tuple[str, str | None]:
    """Kategorie und Aussteller aus Dateiname und Text raten.

    `eigene` sind die Regeln des Mandanten aus dem Block `belegregeln` seiner
    mandant.json - Geschaeftspartner, die nur er hat. Sie greifen vor den
    allgemeinen Regeln, weil sie den konkreteren Fall beschreiben.
    """
    heu = dateiname + "\n" + text
    for muster, kat, aussteller in list(eigene) + REGELN:
        if re.search(muster, heu, re.IGNORECASE):
            return kat, aussteller
    return "sonstiges", None


def rate_datum(dateiname: str, text: str, jahr: int | None = None) -> str | None:
    for quelle in (dateiname, text):
        for rx in RE_DATUM:
            for m in rx.finditer(quelle):
                g = m.groups()
                d = f"{g[2]}-{g[1]}-{g[0]}" if len(g[0]) == 2 else f"{g[0]}-{g[1]}-{g[2]}"
                if 1990 < int(d[:4]) < 2100 and (jahr is None or int(d[:4]) == jahr):
                    return d
    if m := RE_YMD.search(dateiname):
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def rate_jahr(dateiname: str, text: str, pfad: str | None = None) -> int:
    """Jahr fuer die Ablage: Datum > Monatsname im Dateinamen > Dateidatum."""
    if d := rate_datum(dateiname, text):
        return int(d[:4])
    if m := RE_MONAT.search(dateiname):
        j = m.group(2)
        return int(j) if len(j) == 4 else 2000 + int(j)
    if pfad and os.path.exists(pfad):
        import datetime
        return datetime.date.fromtimestamp(os.path.getmtime(pfad)).year
    return 0


def archiv_wurzel(mandant_dir: str) -> str:
    return os.path.join(mandant_dir, "belege")


def erfassen(con: sqlite3.Connection, mandant_id: int, mandant_dir: str, quelle: str,
             jahr: int | None = None, datum: str | None = None, kategorie: str | None = None,
             aussteller: str | None = None, bezeichnung: str | None = None,
             betrag_cent: int | None = None, notiz: str | None = None,
             kopieren: bool = True) -> tuple[int, str, bool]:
    """Legt einen Beleg im Archiv ab. Rueckgabe: (id, belegnr, neu?)."""
    quelle = os.path.abspath(quelle)
    digest = sha256(quelle)
    vorhanden = con.execute(
        "SELECT id, belegnr FROM beleg WHERE mandant_id=? AND sha256=?",
        (mandant_id, digest)).fetchone()
    if vorhanden:
        return vorhanden[0], vorhanden[1], False

    name = os.path.basename(quelle)
    text = volltext(quelle)
    if not kategorie or not aussteller:
        eigene = con.execute("SELECT stammdaten FROM mandant WHERE id=?",
                             (mandant_id,)).fetchone()
        eigene = (json.loads(eigene[0]).get("belegregeln") or []) if eigene and eigene[0] else []
        k, a = klassifiziere(name, text, [tuple(r) for r in eigene])
        kategorie = kategorie or k
        aussteller = aussteller or a
    datum = datum or rate_datum(name, text, jahr)
    if jahr is None:
        jahr = int(datum[:4]) if datum else rate_jahr(name, text, quelle)
    bezeichnung = bezeichnung or os.path.splitext(name)[0]

    laufnr = con.execute(
        "SELECT COALESCE(MAX(laufnr),0)+1 FROM beleg WHERE mandant_id=? AND jahr=?",
        (mandant_id, jahr)).fetchone()[0]
    belegnr = f"B{jahr}-{laufnr:04d}"

    ext = os.path.splitext(name)[1].lower() or ".bin"
    teile = [belegnr, datum or "ohne-datum"]
    if aussteller:
        teile.append(_slug(aussteller, 28))
    teile.append(_slug(bezeichnung))
    rel = os.path.join("belege", str(jahr), kategorie, "_".join(teile) + ext)
    ziel = os.path.join(mandant_dir, rel)
    os.makedirs(os.path.dirname(ziel), exist_ok=True)
    if kopieren:
        shutil.copy2(quelle, ziel)

    cur = con.execute(
        "INSERT INTO beleg (mandant_id, jahr, laufnr, belegnr, datum, kategorie, aussteller,"
        " bezeichnung, betrag_cent, pfad, quelle_pfad, sha256, notiz)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mandant_id, jahr, laufnr, belegnr, datum, kategorie, aussteller, bezeichnung,
         betrag_cent, rel, quelle, digest, notiz))
    return cur.lastrowid, belegnr, True


def scannen(con: sqlite3.Connection, mandant_id: int, mandant_dir: str, ordner: str,
            jahr: int | None = None, endungen=(".pdf", ".PDF", ".png", ".jpg", ".txt", ".csv",
                                               ".docx", ".xlsx")) -> tuple[int, int]:
    """Bulk-Import eines Ordners. Rueckgabe: (neu, uebersprungen)."""
    neu = alt = 0
    for wurzel, dirs, dateien in os.walk(ordner):
        dirs[:] = [d for d in dirs if d not in (".git", "belege", "__pycache__")]
        for d in sorted(dateien):
            if d.startswith(".") or not d.endswith(tuple(endungen)):
                continue
            _, _, ist_neu = erfassen(con, mandant_id, mandant_dir,
                                     os.path.join(wurzel, d), jahr=jahr)
            neu += ist_neu
            alt += not ist_neu
    return neu, alt


def verknuepfen(con: sqlite3.Connection, buchung_id: int, beleg_id: int) -> None:
    con.execute("INSERT OR IGNORE INTO buchung_beleg (buchung_id, beleg_id) VALUES (?,?)",
                (buchung_id, beleg_id))


def beleg_id_von_nr(con: sqlite3.Connection, mandant_id: int, belegnr: str) -> int | None:
    r = con.execute("SELECT id FROM beleg WHERE mandant_id=? AND belegnr=?",
                    (mandant_id, belegnr)).fetchone()
    return r[0] if r else None
