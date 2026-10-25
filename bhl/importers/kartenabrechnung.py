# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Kreditkartenabrechnungen aus einer erfassten CSV-Datei.

Kartenabrechnungen gibt es oft nur auf Papier oder als Bild. Das Erfassen -
Scan, Texterkennung, Abgleich gegen das Blatt - geschieht ausserhalb der
Anwendung, weil es Urteilsarbeit ist. Hier kommt an, was dabei herauskam, und
die Anwendung rechnet nach, bevor sie etwas uebernimmt:

  * je Abrechnung:  alter Saldo + Summe der Umsaetze == neuer Saldo
  * ueber die Reihe: neuer Saldo einer Abrechnung == alter Saldo der naechsten

Eine Abrechnung, die nicht aufgeht, wird nicht eingelesen. Ein Lesefehler in
einem Betrag faellt damit auf, bevor er im Bestand steht; eine fehlende
Abrechnung zeigt sich als Bruch in der Kette.

Dateiformat: UTF-8, Semikolon, Kopfzeile. Eine Zeile je Umsatz, dazu je
Abrechnung eine Zeile `saldo_alt` und eine Zeile `saldo_neu`.

    konto          Kennung des Kartenkontos - dieselbe, die in der
                   mandant.json unter bankkonten als "iban" steht
    abrechnung     Abrechnungsnummer
    seite, zeile   Fundstelle auf der Abrechnung
    art            saldo_alt | umsatz | entgelt | saldo_neu
    kaufdatum      Belegdatum, JJJJ-MM-TT (leer bei den Saldozeilen)
    buchungsdatum  JJJJ-MM-TT
    betrag         mit Vorzeichen, "-1.234,56"; Belastung negativ
    text           Angabe des Unternehmens
    waehrung, fremdbetrag, kurs, meilen, quelle    frei, wandern in rohdaten

Das Vorzeichen folgt der Bank-Konvention der Tabelle `banktransaktion`:
Belastung der Karte negativ, Gutschrift und Ausgleich positiv. Ein Saldo
zulasten des Karteninhabers ist entsprechend negativ.

Fuer § 11 EStG zaehlt bei Kartenzahlung der Tag des Karteneinsatzes, nicht
der Tag der Abbuchung vom Girokonto - das Kaufdatum steht deshalb in
`rohdaten` jedes Umsatzes.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field

ARTEN = ("saldo_alt", "umsatz", "entgelt", "saldo_neu")
RE_BETRAG = re.compile(r"^([+-]?)(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})$")
RE_DATUM = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ZUSATZ = ("kaufdatum", "seite", "zeile", "art", "waehrung", "fremdbetrag", "kurs",
          "meilen", "quelle")


def cent(s: str) -> int:
    """'-1.234,56' -> -123456. Ohne Gleitkomma."""
    m = RE_BETRAG.match(s.strip())
    if not m:
        raise ValueError(f"kein Betrag: {s!r}")
    wert = int(m.group(2).replace(".", "")) * 100 + int(m.group(3))
    return -wert if m.group(1) == "-" else wert


@dataclass
class Umsatz:
    abrechnung: str
    seite: str
    zeile: str
    art: str
    buchungsdatum: str
    betrag_cent: int
    text: str
    zusatz: dict

    @property
    def hash(self) -> str:
        # Die Fundstelle, nicht der Inhalt: zwei gleiche Kaeufe am selben Tag
        # sind zwei Umsaetze, und ein berichtigter Lesefehler bleibt derselbe.
        roh = f"{self.abrechnung}|{self.seite}|{self.zeile}|{self.art}"
        return hashlib.sha1(roh.encode()).hexdigest()[:16]


@dataclass
class Abrechnung:
    konto: str
    nummer: str
    saldo_alt: int | None = None
    saldo_neu: int | None = None
    umsaetze: list[Umsatz] = field(default_factory=list)


def lies(pfad: str) -> list[Abrechnung]:
    """CSV lesen, nach Abrechnung gruppiert, in der Reihenfolge der Datei."""
    abr: dict[tuple[str, str], Abrechnung] = {}
    with open(pfad, encoding="utf-8-sig", newline="") as fh:
        for nr, r in enumerate(csv.DictReader(fh, delimiter=";"), start=2):
            art = r["art"].strip()
            if art not in ARTEN:
                raise ValueError(f"{pfad}:{nr}: unbekannte Art {art!r}")
            a = abr.setdefault((r["konto"], r["abrechnung"]),
                               Abrechnung(r["konto"], r["abrechnung"]))
            try:
                betrag = cent(r["betrag"])
            except ValueError as e:
                raise ValueError(f"{pfad}:{nr}: {e}") from None
            if art == "saldo_alt":
                a.saldo_alt = betrag
            elif art == "saldo_neu":
                a.saldo_neu = betrag
            else:
                if not RE_DATUM.match(r["buchungsdatum"]):
                    raise ValueError(f"{pfad}:{nr}: Buchungsdatum {r['buchungsdatum']!r}")
                a.umsaetze.append(Umsatz(
                    abrechnung=r["abrechnung"], seite=r["seite"], zeile=r["zeile"], art=art,
                    buchungsdatum=r["buchungsdatum"], betrag_cent=betrag,
                    text=" ".join(r["text"].split()),
                    zusatz={k: r[k] for k in ZUSATZ if r.get(k)}))
    return list(abr.values())


def pruefe(a: Abrechnung) -> tuple[bool, int]:
    """Kontrollrechnung: alter Saldo + Summe Umsaetze == neuer Saldo."""
    if a.saldo_alt is None or a.saldo_neu is None:
        return False, 0
    soll = a.saldo_alt + sum(u.betrag_cent for u in a.umsaetze)
    return soll == a.saldo_neu, a.saldo_neu - soll


def luecken(abrechnungen: list[Abrechnung]) -> list[str]:
    """Brueche in der Saldenkette je Konto - eine fehlende Abrechnung."""
    aus = []
    je_konto: dict[str, list[Abrechnung]] = {}
    for a in abrechnungen:
        je_konto.setdefault(a.konto, []).append(a)
    for reihe in je_konto.values():
        reihe.sort(key=lambda a: a.nummer)
        for vor, nach in zip(reihe, reihe[1:]):
            if vor.saldo_neu != nach.saldo_alt:
                aus.append(f"zwischen Abrechnung {vor.nummer} und {nach.nummer} "
                           f"fehlt etwas: neuer Saldo und alter Saldo weichen ab")
    return aus


def importieren(con: sqlite3.Connection, mandant_id: int, pfad: str, jahr: int) -> dict:
    """Umsaetze eines Kalenderjahres uebernehmen; Jahr 0 = alle.

    Wiederholbar: Was an derselben Fundstelle schon steht, wird nicht
    verdoppelt. Weicht der Inhalt ab - ein berichtigter Lesefehler -, wird die
    Zeile fortgeschrieben und gezaehlt.
    """
    z = {"neu": 0, "vorhanden": 0, "berichtigt": 0, "abrechnungen": 0,
         "unbekannt": [], "differenz": [], "luecken": []}
    abrechnungen = lies(pfad)
    z["luecken"] = luecken(abrechnungen)
    konten = {r["iban"]: r["id"] for r in con.execute(
        "SELECT id, iban FROM bankkonto WHERE mandant_id=?", (mandant_id,))}
    for a in abrechnungen:
        if a.konto not in konten:
            if a.konto not in z["unbekannt"]:
                z["unbekannt"].append(a.konto)
            continue
        ok, diff = pruefe(a)
        if not ok:
            z["differenz"].append((a.nummer, diff))
            continue
        z["abrechnungen"] += 1
        for u in a.umsaetze:
            if jahr and not u.buchungsdatum.startswith(str(jahr)):
                continue
            roh = json.dumps(u.zusatz, ensure_ascii=False, sort_keys=True)
            alt = con.execute(
                "SELECT id, buchungsdatum, betrag_cent, text, rohdaten FROM banktransaktion"
                " WHERE bankkonto_id=? AND hash=?", (konten[a.konto], u.hash)).fetchone()
            if alt is None:
                con.execute(
                    "INSERT INTO banktransaktion (bankkonto_id, auszug, buchungsdatum,"
                    " betrag_cent, text, hash, rohdaten) VALUES (?,?,?,?,?,?,?)",
                    (konten[a.konto], a.nummer, u.buchungsdatum, u.betrag_cent, u.text,
                     u.hash, roh))
                z["neu"] += 1
            elif (alt["buchungsdatum"], alt["betrag_cent"], alt["text"], alt["rohdaten"]) != (
                    u.buchungsdatum, u.betrag_cent, u.text, roh):
                con.execute(
                    "UPDATE banktransaktion SET buchungsdatum=?, betrag_cent=?, text=?,"
                    " rohdaten=? WHERE id=?",
                    (u.buchungsdatum, u.betrag_cent, u.text, roh, alt["id"]))
                z["berichtigt"] += 1
            else:
                z["vorhanden"] += 1
    return z
