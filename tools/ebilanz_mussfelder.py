#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Mussfelder der E-Bilanz gegen das ERiC-Plugin ermitteln.

Welche Positionen die Taxonomie als Mussfeld führt, sagt das Plugin
`checkBilanz_<version>` erst bei der Prüfung - zwanzig Meldungen auf einmal.
Dieses Werkzeug baut einen erfundenen Abschluss (halb eingezahltes Stammkapital,
Notar, Bankgebühren), prüft ihn lokal und trägt jedes gemeldete Mussfeld und
jede gemeldete Oberposition in `bhl/taxonomie/ebilanz_<version>.json` ein, bis
das Plugin nichts mehr vermisst. Es wird nichts gesendet.

Ein Mussfeld, das im Datensatz einen Wert hat, meldet das Plugin nicht. Deshalb
läuft der Abschluss in mehreren Varianten mit verschiedenen Zielpositionen.

    ERIC_PFAD=... ERIC_HERSTELLER_ID=... tools/ebilanz_mussfelder.py [6.9]
"""

import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bhl import db, ebilanz_xbrl, eric, eric_ansicht, ledger  # noqa: E402

KONTEN = (("1420", "A"), ("1800", "A"), ("2900", "P"), ("2910", "P"), ("6301", "X"),
          ("6855", "X"))
FEST = {"2900": "bs.eqLiab.equity.subscribed.corp", "2910": "bs.eqLiab.equity.subscribed.unpaidCap"}
OC = "is.netIncome.regular.operatingTC.otherCost."
# Je Variante andere Zielpositionen für Forderung, Bank und die zwei Aufwände
VARIANTEN = [
    ("bs.ass.currAss.receiv.other.vat", "bs.ass.currAss.cashEquiv.bank",
     OC + "legalConsulting", OC + "otherOrdinary"),
    ("bs.ass.currAss.receiv.other.other", "bs.ass.currAss.cashEquiv.cash",
     OC + "insurance", OC + "concessLicenses"),
    ("bs.ass.currAss.receiv.other.otherTaxRec", "bs.ass.currAss.cashEquiv.bank",
     OC + "miscellaneous", OC + "fees"),
]
MUSS = re.compile(r"zum Mussfeld '\{[^}]+\}([^']+)' berichtet")
OBEN = re.compile(r"Die der Position '([^']+)' durch das Presentation Network '[^']+' "
                  r"zugeordnete Oberposition '\{[^}]+\}([^']+)' wurde nicht berichtet")


def abschluss(tmp, ziele):
    con = db.connect(os.path.join(tmp, "probe.sqlite"))
    m = con.execute("INSERT INTO mandant (kuerzel, name) VALUES ('probe', 'Probe GmbH')").lastrowid
    gj = con.execute("INSERT INTO geschaeftsjahr (mandant_id, jahr, beginn, ende)"
                     " VALUES (?, 2025, '2025-10-07', '2025-12-31')", (m,)).lastrowid
    for nr, typ in KONTEN:
        con.execute("INSERT INTO konto (mandant_id, nummer, bezeichnung, typ) VALUES (?,?,?,?)",
                    (m, nr, f"Konto {nr}", typ))
    for d, t, z in (("2025-10-07", "Gründung", [("2910", 2500000, 0), ("2900", 0, 2500000)]),
                    ("2025-10-14", "Einzahlung", [("1800", 1250000, 0), ("2910", 0, 1250000)]),
                    ("2025-10-21", "Notar", [("6301", 64000, 0), ("1420", 12160, 0),
                                             ("1800", 0, 76160)]),
                    ("2025-12-31", "Gebühren", [("6855", 4500, 0), ("1800", 0, 4500)])):
        ledger.buchen(con, gj, m, d, t, z)
    zuordnung = {**FEST, "1420": ziele[0], "1800": ziele[1], "6301": ziele[2], "6855": ziele[3]}
    mdir = os.path.join(tmp, "m")
    os.makedirs(mdir, exist_ok=True)
    with open(os.path.join(mdir, "ebilanz.csv"), "w", encoding="utf-8") as fh:
        fh.write("konto;element;bezeichnung;seite;quelle;hinweis\n")
        for nr, el in zuordnung.items():
            fh.write(f"{nr};{el};x;{'bilanz' if el.startswith('bs.') else 'guv'};probe;\n")
    return con, gj, mdir


def main(version):
    pfad = os.path.join(os.path.dirname(ebilanz_xbrl.__file__), "taxonomie",
                        f"ebilanz_{version.replace('.', '_')}.json")
    with eric.Eric(os.path.expanduser(os.environ["ERIC_PFAD"]), tempfile.mkdtemp()) as er:
        stnr = er.steuernummer("198/113/10231", "9198")          # Teststeuernummer
        for ziele in VARIANTEN:
            for runde in range(60):
                tax = ebilanz_xbrl.taxonomie(version)
                with tempfile.TemporaryDirectory() as tmp:
                    con, gj, mdir = abschluss(tmp, ziele)
                    xml = ebilanz_xbrl.datensatz(
                        ebilanz_xbrl.abschluss(con, gj, mdir, tax),
                        stamm={"name": "Probe GmbH", "rechtsform": "GmbH", "strasse": "Weg 1",
                               "plz": "10115", "ort": "Berlin"},
                        gj={"beginn": "2025-10-07", "ende": "2025-12-31"}, steuernummer=stnr,
                        bufa="9198", hersteller_id=os.environ["ERIC_HERSTELLER_ID"],
                        produkt="lernen", produktversion="1", testmerker="700000004", tax=tax)
                    con.close()
                r = er.bearbeite(xml, tax["datenart_version"])
                texte = [m["text"] for m in eric_ansicht.meldungen(r.antwort)]
                neu = [m for t in texte for m in MUSS.findall(t) if m not in tax["mussfelder"]]
                eltern = {k: v for t in texte for k, v in OBEN.findall(t)}
                if not neu and not eltern:
                    print(f"{ziele[2].rsplit('.', 1)[1]}: {'OK' if r.ok else texte[:3]}")
                    break
                tax["mussfelder"] = sorted(set(tax["mussfelder"]) | set(neu))
                tax["eltern"].update(eltern)
                with open(pfad, "w", encoding="utf-8") as fh:
                    json.dump(tax, fh, ensure_ascii=False, indent=1)
                print(f"  Runde {runde}: {len(neu)} Mussfelder, {len(eltern)} Oberpositionen")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ebilanz_xbrl.TAXONOMIE)
