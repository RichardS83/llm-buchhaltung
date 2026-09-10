# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Steuerliche Ueberleitungsrechnung.

Die Rechnung selbst steht in `<mandant>/<jahr>/steuern.json` - im Klartext,
Zeile fuer Zeile, mit Paragraf und Begruendung. Dieses Modul rechnet nichts
nach, was dort steht, sondern **prueft** es: Jede Zeile, die auf Konten
verweist, wird gegen die Salden der Buchhaltung gehalten. Weicht etwas ab,
faellt es sofort auf, statt still in der Erklaerung zu landen.

    "konten":         Zeile wird gegen die Summe dieser Kontensalden geprueft
    "pruefgruppe":    mehrere Zeilen teilen sich dieselben Konten und werden
                      gemeinsam geprueft (z. B. zwei Beteiligungen auf einem Konto)
    "verweis_konten": nur verlinken, nicht pruefen (etwa Vorauszahlungen, die
                      im Kontensaldo mit der Erstattung verrechnet sind)

Eine Zeile darf ausserdem einen **Platzhalter** tragen: einen Betrag, der
angenommen und nicht belegt ist, damit die Rechnung vollstaendig durchgerechnet
werden kann, obwohl eine Unterlage fehlt.

    "platzhalter": {"annahme": ..., "grund": ..., "aufloesen_durch": ...}

Ein Platzhalter ist keine Anmerkung, sondern eine **Sperre**: solange einer in
der Datei steht, gilt die Erklaerung als nicht abgabefaehig. `pruefen` setzt
dafuer `spec["gesperrt"]`, `bh pruefen` meldet jeden einzelnen als Fehler und
`bh abgabe` endet mit Rueckgabewert 1. Der Grund ist derselbe wie bei den
fehlenden Belegen: eine angenommene Zahl, die nur im Fliesstext als "offen"
vermerkt ist, wandert irgendwann ungeprueft nach Elster.

Der Vorjahresvergleich wird nicht hinterlegt, sondern aus den `steuern.json`
der genannten Jahre gezogen - `"vergleich": {"aus_jahren": [2024, 2025]}`. So
steht jede Zahl genau einmal in der Ablage, naemlich in dem Jahr, zu dem sie
gehoert. Fehlt ein Jahr, bleibt seine Spalte leer statt falsch.
"""

from __future__ import annotations

import json
import os
import sqlite3

from .ledger import salden


def laden(jahr_dir: str) -> dict | None:
    pfad = os.path.join(jahr_dir, "steuern.json")
    if not os.path.exists(pfad):
        return None
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)


def platzhalter(spec: dict) -> list[dict]:
    """Alle Zeilen, deren Betrag angenommen und nicht belegt ist."""
    gefunden = []
    for abschnitt in spec.get("abschnitte", []):
        for z in abschnitt.get("zeilen", []):
            p = z.get("platzhalter")
            if not p:
                continue
            gefunden.append({"abschnitt": abschnitt.get("titel", ""),
                             "zeile": z.get("text", ""),
                             "betrag": z.get("betrag", 0), **p})
    return gefunden


def _kontensalden(con: sqlite3.Connection, gj_id: int) -> dict[str, dict]:
    return {r["nummer"]: {"nummer": r["nummer"], "bezeichnung": r["bezeichnung"],
                          "saldo": r["saldo"]}
            for r in salden(con, gj_id)}


def pruefen(con: sqlite3.Connection, gj_id: int, spec: dict, ergebnis_cent: int) -> dict:
    """Ergaenzt jede Zeile um den Kontensaldo und die Abweichung."""
    ks = _kontensalden(con, gj_id)
    gruppen: dict[str, dict] = {}

    def konto_info(nummern):
        return [{"nummer": n,
                 "bezeichnung": ks.get(n, {}).get("bezeichnung", "unbekannt"),
                 "saldo": ks.get(n, {}).get("saldo", 0)} for n in nummern]

    for abschnitt in spec.get("abschnitte", []):
        for z in abschnitt["zeilen"]:
            if z.get("verweis_konten"):
                z["konten_info"] = konto_info(z["verweis_konten"])
            if not z.get("konten"):
                continue
            z["konten_info"] = konto_info(z["konten"])
            saldo = sum(k["saldo"] for k in z["konten_info"])
            gruppe = z.get("pruefgruppe")
            if gruppe:
                g = gruppen.setdefault(gruppe, {"betrag": 0, "konten": set(), "zeilen": []})
                g["betrag"] += z["betrag"]
                g["konten"].update(z["konten"])
                g["zeilen"].append(z)
            else:
                z["konten_saldo"] = saldo
                z["abweichung"] = z["betrag"] - saldo

    for name, g in gruppen.items():
        saldo = sum(ks.get(n, {}).get("saldo", 0) for n in g["konten"])
        for z in g["zeilen"]:
            z["gruppen_betrag"] = g["betrag"]
            z["konten_saldo"] = saldo
            z["abweichung"] = g["betrag"] - saldo

    # Der Ausgangswert der Ueberleitung muss das Ergebnis der GuV sein.
    for abschnitt in spec.get("abschnitte", []):
        for z in abschnitt["zeilen"]:
            if z.get("quelle") == "guv":
                z["konten_saldo"] = ergebnis_cent
                z["abweichung"] = z["betrag"] - ergebnis_cent

    # Bilanzpositionen gegen die Konten halten
    for p in spec.get("positionen", []):
        saldo = ks.get(p["konto"], {}).get("saldo", 0)
        p["bezeichnung"] = ks.get(p["konto"], {}).get("bezeichnung", "")
        p["konten_saldo"] = abs(saldo)
        p["abweichung"] = p["betrag"] - abs(saldo)

    spec["abweichungen"] = sum(
        1 for a in spec.get("abschnitte", []) for z in a["zeilen"] if z.get("abweichung")
    ) + sum(1 for p in spec.get("positionen", []) if p.get("abweichung"))

    # Angenommene Werte sperren die Abgabe - nicht die Anzeige. Die Rechnung
    # wird vollstaendig gezeigt, damit man sieht, was der Platzhalter bewirkt.
    # Eine Zeile, die nicht gegen ihre Konten aufgeht, sperrt genauso: sie ist
    # entweder falsch abgeschrieben oder die Buchung fehlt.
    spec["platzhalter"] = platzhalter(spec)
    spec["gesperrt"] = bool(spec["platzhalter"]) or bool(spec["abweichungen"])
    return spec


VERGLEICHSZEILEN = [
    ("zve", "zu versteuerndes Einkommen"),
    ("kst", "Körperschaftsteuer"),
    ("gewst", "Gewerbesteuer"),
]


def vergleich_aufbauen(spec: dict, mandant_dir: str) -> None:
    """`vergleich.aus_jahren` in Spalten und Zeilen aufloesen.

    Gelesen wird die `steuern.json` des jeweiligen Jahres - dieselbe Datei, aus
    der auch dessen eigene Seite gebaut wird. Ein Jahr ohne Datei bekommt eine
    leere Spalte; das ist ehrlicher als eine abgeschriebene Zahl.
    """
    v = spec.get("vergleich")
    if not v or not v.get("aus_jahren"):
        return
    werte: dict[int, dict] = {}
    for jahr in v["aus_jahren"]:
        andere = laden(os.path.join(mandant_dir, str(jahr)))
        werte[jahr] = kennzahlen(andere) if andere else {}
    v["spalten"] = [str(j) for j in v["aus_jahren"]]
    v["zeilen"] = [{"text": text,
                    "werte": [werte[j].get(schluessel) for j in v["aus_jahren"]]}
                   for schluessel, text in VERGLEICHSZEILEN]


def kennzahlen(spec: dict) -> dict:
    """Die mit `kennzahl` markierten Zeilen fuer die Uebersicht einsammeln."""
    return {z["kennzahl"]: z["betrag"]
            for a in spec.get("abschnitte", []) for z in a["zeilen"] if z.get("kennzahl")}
