# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""E-Bilanz (§ 5b EStG): Kontennachweis und Taxonomie-Zuordnung.

Mein ELSTER hat kein Formular fuer die E-Bilanz. Uebermittelt wird ein
XBRL-Datensatz nach der HGB-Taxonomie, und seit den Wirtschaftsjahren ab
2025 gehoert der **Kontennachweis** dazu: jedes Sachkonto mit seinem Saldo und
der Taxonomieposition, in die es eingeht (BMF vom 10.06.2025).

Dieses Modul baut genau diesen Nachweis aus der Buchfuehrung und prueft ihn.
Die Zuordnung Konto -> Position steht je Mandant in `ebilanz.csv` - sie haengt
am Kontenrahmen des Mandanten und an dem, was das Finanzamt in den Vorjahren
angenommen hat, nicht an einer Regel, die sich im Code festschreiben liesse.

Geprueft wird, was vor einer Uebermittlung stimmen muss:

* jedes Konto mit Saldo ist zugeordnet - ein fehlendes Konto fehlt im
  Datensatz, und die Bilanz geht dort nicht auf;
* Bestandskonten stehen in der Bilanz (`bs.`), Erfolgskonten in der GuV (`is.`);
* jede neu vergebene Position ist als ungeprueft markiert, bis jemand den
  Elementnamen gegen die Taxonomie gehalten hat - ein falscher Name faellt
  sonst erst bei der Plausibilitaetspruefung der Finanzverwaltung auf.

Das Jahresergebnis ist kein Konto; es geht als errechnete Position
`bs.eqLiab.equity.netIncome` in die Bilanz ein.
"""

from __future__ import annotations

import csv
import os
import sqlite3

from . import reports
from .ledger import salden

JAHRESERGEBNIS = "bs.eqLiab.equity.netIncome"


def laden(mandant_dir: str) -> dict[str, dict] | None:
    pfad = os.path.join(mandant_dir, "ebilanz.csv")
    if not os.path.exists(pfad):
        return None
    with open(pfad, encoding="utf-8") as fh:
        zeilen = csv.DictReader((z for z in fh if not z.startswith("#")), delimiter=";")
        # Ein Semikolon im Hinweis erzeugt Ueberhangspalten (Schluessel None);
        # sie gehoeren zum Hinweis und werden wieder angehaengt.
        aus = {}
        for r in zeilen:
            if not (r.get("konto") or "").strip():
                continue
            rest = r.pop(None, None) or []
            e = {k: (v or "").strip() for k, v in r.items()}
            if rest:
                e["hinweis"] = "; ".join([e.get("hinweis", "")] + [x.strip() for x in rest])
            aus[e["konto"]] = e
        return aus


def auswerten(con: sqlite3.Connection, gj_id: int, mandant_dir: str) -> dict | None:
    zuordnung = laden(mandant_dir)
    if zuordnung is None:
        return None
    konten, fehlend, falsche_seite = [], [], []
    for r in salden(con, gj_id):
        if not r["saldo"]:
            continue
        z = zuordnung.get(r["nummer"])
        typ = r["typ"] if "typ" in r.keys() else None
        eintrag = {"nummer": r["nummer"], "bezeichnung": r["bezeichnung"],
                   "saldo": r["saldo"], "typ": typ,
                   "element": z["element"] if z else None,
                   "position": z["bezeichnung"] if z else None,
                   "seite": z["seite"] if z else None,
                   "quelle": z["quelle"] if z else None,
                   "hinweis": z["hinweis"] if z else None}
        if not z:
            fehlend.append(eintrag)
        else:
            erwartet = "bilanz" if typ in ("A", "P") else "guv" if typ in ("E", "X") else None
            praefix = "bs." if z["seite"] == "bilanz" else "is."
            if (erwartet and erwartet != z["seite"]) or not z["element"].startswith(praefix):
                falsche_seite.append(eintrag)
        konten.append(eintrag)

    ergebnis = reports.ergebnis(con, gj_id)
    positionen: dict[str, dict] = {}
    for k in konten:
        if not k["element"]:
            continue
        p = positionen.setdefault(k["element"], {
            "element": k["element"], "bezeichnung": k["position"], "seite": k["seite"],
            "summe": 0, "konten": [], "ungeprueft": False})
        p["summe"] += k["saldo"]
        p["konten"].append(k["nummer"])
        p["ungeprueft"] = p["ungeprueft"] or k["quelle"] == "neu"
    # Das Jahresergebnis steht auf der Passivseite: ein Fehlbetrag mindert das
    # Eigenkapital. Im Soll-minus-Haben-Vorzeichen der Konten ist es das
    # Negative des GuV-Ergebnisses.
    positionen[JAHRESERGEBNIS] = {
        "element": JAHRESERGEBNIS, "bezeichnung": "Jahresüberschuss/Jahresfehlbetrag",
        "seite": "bilanz", "summe": -ergebnis, "konten": ["GuV"], "ungeprueft": False,
        "errechnet": True}

    bilanz = [p for p in positionen.values() if p["seite"] == "bilanz"]
    guv = [p for p in positionen.values() if p["seite"] == "guv"]
    aktiva = sum(p["summe"] for p in bilanz if p["element"].startswith("bs.ass"))
    passiva = -sum(p["summe"] for p in bilanz if p["element"].startswith("bs.eqLiab"))
    guv_summe = -sum(p["summe"] for p in guv)
    ungeprueft = sorted({p["element"] for p in positionen.values() if p["ungeprueft"]})
    pruefungen = [
        {"text": "Jedes Konto mit Saldo ist einer Taxonomieposition zugeordnet",
         "ok": not fehlend, "detail": f"{len(fehlend)} Konto/Konten ohne Zuordnung"},
        {"text": "Bestandskonten in der Bilanz, Erfolgskonten in der GuV",
         "ok": not falsche_seite, "detail": f"{len(falsche_seite)} Konto/Konten auf der falschen Seite"},
        {"text": "Aktiva = Passiva nach Taxonomiepositionen",
         "ok": aktiva == passiva, "detail": f"Differenz {aktiva - passiva} Cent"},
        {"text": "GuV-Positionen ergeben das Jahresergebnis",
         "ok": guv_summe == ergebnis, "detail": f"Differenz {guv_summe - ergebnis} Cent"},
        {"text": "Neu vergebene Positionen gegen die Taxonomie geprüft",
         "ok": not ungeprueft, "detail": f"{len(ungeprueft)} Elementname(n) ungeprüft"},
    ]
    return {
        "konten": konten, "fehlend": fehlend, "falsche_seite": falsche_seite,
        "bilanz": sorted(bilanz, key=lambda p: p["element"]),
        "guv": sorted(guv, key=lambda p: p["element"]),
        "aktiva": aktiva, "passiva": passiva, "ergebnis": ergebnis,
        "ungeprueft": ungeprueft, "pruefungen": pruefungen,
        "vollstaendig": all(p["ok"] for p in pruefungen[:4]),
        "bereit": all(p["ok"] for p in pruefungen),
    }
