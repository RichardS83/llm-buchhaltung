# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
# -*- coding: utf-8 -*-
"""Abgleiche, die still falsch werden können: python3 -m unittest discover -s tests

* Zahllast gegen Abbuchung — bezahlt ist erst, was gedeckt ist.
* Saldenkette der Kartenabrechnungen — numerisch sortiert, nicht als Text.
* Wiederholter Kartenimport — ein gebuchter Umsatz wird nicht still berichtigt.
* Aufgaben erben die Frist der Abgabe, die sie blockieren — auch wenn die
  Bezeichnung nicht auf das Jahr endet.

Alle Zahlen, Konten und Namen sind erfunden.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WURZEL)

from bhl import aufgaben, db, fristen          # noqa: E402
from bhl import umsatzsteuer as ust            # noqa: E402
from bhl.importers import kartenabrechnung as karte  # noqa: E402


def _leere_db(tmp: str):
    """Mandant `beispiel` mit Geschäftsjahr 2026, ohne Kontenrahmen."""
    con = db.connect(os.path.join(tmp, "probe.sqlite"))
    m = con.execute("INSERT INTO mandant (kuerzel, name, steuernummer) VALUES (?,?,?)",
                    ("beispiel", "Beispiel GmbH", "00/000/00000")).lastrowid
    gj = con.execute("INSERT INTO geschaeftsjahr (mandant_id, jahr, beginn, ende)"
                     " VALUES (?,?,?,?)", (m, 2026, "2026-01-01", "2026-12-31")).lastrowid
    con.commit()
    return con, m, gj


class ZahllastGegenAbbuchung(unittest.TestCase):
    """Eine Teilzahlung darf die Zahllast nicht als bezahlt erscheinen lassen."""

    Q3 = ust.Zeitraum(2026, quartal=3)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.con, self.m, self.gj = _leere_db(self.tmp)
        self.bk = self.con.execute(
            "INSERT INTO bankkonto (mandant_id, iban, bezeichnung) VALUES (?,?,?)",
            (self.m, "DE00TEST", "Girokonto")).lastrowid

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def abbuchung(self, datum: str, cent: int, n: int):
        self.con.execute(
            "INSERT INTO banktransaktion (bankkonto_id, buchungsdatum, betrag_cent, text, hash)"
            " VALUES (?,?,?,?,?)",
            (self.bk, datum, -cent, f"Finanzamt | STEUERNR 0 UMS.ST 3VJ.26 {cent // 100},00EUR",
             f"h{n}"))

    def angemeldet(self, kz83: str):
        ust.festhalten(self.con, self.m, self.Q3, "abgegeben", abgegeben_am="2026-10-05",
                       kennzahlen_werte={"Kz81": "1000", "Kz83": kz83})

    def test_teilzahlung_laesst_rest_offen(self):
        self.angemeldet("190.00")
        self.abbuchung("2026-11-10", 10000, 1)
        z = ust.zahlung_aus_bank(self.con, self.m, self.Q3)
        self.assertFalse(z["gedeckt"])
        self.assertIsNone(z["buchungsdatum"], "nicht bezahlt, solange ein Rest offen ist")
        self.assertEqual((z["bezahlt_cent"], z["zahllast_cent"], z["offen_cent"]),
                         (10000, 19000, 9000))
        # Der Rest kommt nach: bezahlt am Tag der zweiten Abbuchung
        self.abbuchung("2026-11-20", 9000, 2)
        z = ust.zahlung_aus_bank(self.con, self.m, self.Q3)
        self.assertTrue(z["gedeckt"])
        self.assertEqual(z["buchungsdatum"], "2026-11-20")
        self.assertEqual(z["offen_cent"], 0)

    def test_berichtigte_hoehere_anmeldung_oeffnet_den_zeitraum_wieder(self):
        self.angemeldet("190.00")
        self.abbuchung("2026-11-10", 19000, 1)
        self.assertEqual(ust.zahlung_aus_bank(self.con, self.m, self.Q3)["buchungsdatum"],
                         "2026-11-10")
        self.angemeldet("250.00")
        z = ust.zahlung_aus_bank(self.con, self.m, self.Q3)
        self.assertIsNone(z["buchungsdatum"])
        self.assertEqual(z["offen_cent"], 6000)

    def test_ohne_uebermittelte_werte_gilt_die_zahllast_der_buchfuehrung(self):
        ust.festhalten(self.con, self.m, self.Q3, "abgegeben", abgegeben_am="2026-10-05")
        self.abbuchung("2026-11-10", 10000, 1)
        z = ust.zahlung_aus_bank(self.con, self.m, self.Q3, zahllast_buch=19000)
        self.assertEqual(z["offen_cent"], 9000)
        self.assertIsNone(z["buchungsdatum"])

    def test_uebersicht_zeigt_teilbezahlten_zeitraum_als_offen(self):
        from bhl import web
        erl = self.con.execute(
            "INSERT INTO konto (mandant_id, nummer, bezeichnung, typ, ustva_kz)"
            " VALUES (?,?,?,?,?)", (self.m, "4400", "Erlöse 19 %", "E", "81")).lastrowid
        bank = self.con.execute(
            "INSERT INTO konto (mandant_id, nummer, bezeichnung, typ) VALUES (?,?,?,?)",
            (self.m, "1800", "Bank", "A")).lastrowid
        b = self.con.execute(
            "INSERT INTO buchung (gj_id, nummer, datum, buchungstext) VALUES (?,?,?,?)",
            (self.gj, 1, "2026-08-15", "Ausgangsrechnung")).lastrowid
        self.con.execute("INSERT INTO buchungszeile (buchung_id, konto_id, soll_cent)"
                         " VALUES (?,?,?)", (b, bank, 100000))
        self.con.execute("INSERT INTO buchungszeile (buchung_id, konto_id, haben_cent)"
                         " VALUES (?,?,?)", (b, erl, 100000))
        self.angemeldet("190.00")
        self.abbuchung("2026-11-10", 10000, 1)
        self.con.commit()
        k = web.Kontext(os.path.join(self.tmp, "probe.sqlite"), self.tmp, "beispiel", 2026)
        zeile = next(r for r in web.ust_uebersicht(k)["zeitraeume"] if r["label"] == "2026-Q3")
        self.assertEqual(zeile["kz83"], 19000)
        self.assertTrue(zeile["zahlung_offen"])
        self.assertIsNone(zeile["zahlung_am"])
        self.assertEqual(zeile["zahlung_offen_cent"], 9000, "offen ist nur der Rest")
        eintrag = next(e for e in fristen.laufend([zeile], datetime.date(2026, 11, 1))
                       if e["art"] == "ustva_zahlung")
        self.assertEqual(eintrag["betrag_cent"], 9000)


class Saldenkette(unittest.TestCase):
    """'10' steht als Text vor '9' - die Kette muss numerisch laufen."""

    @staticmethod
    def abr(nummer: str, alt: int, neu: int) -> karte.Abrechnung:
        return karte.Abrechnung("KARTE-0000", nummer, saldo_alt=alt, saldo_neu=neu)

    def test_keine_falsche_luecke_zwischen_9_und_10(self):
        reihe = [self.abr("10", -2000, -3000), self.abr("9", -1000, -2000)]
        self.assertEqual(karte.luecken(reihe), [])

    def test_echte_luecke_zwischen_den_richtigen_nachbarn(self):
        reihe = [self.abr("11", -4000, -5000), self.abr("9", -1000, -2000),
                 self.abr("8", 0, -1000)]
        self.assertEqual(len(karte.luecken(reihe)), 1)
        self.assertIn("zwischen Abrechnung 9 und 11", karte.luecken(reihe)[0])

    def test_nummern_mit_buchstaben_natuerlich_sortiert(self):
        reihe = [self.abr("A10", -2000, -3000), self.abr("A9", -1000, -2000)]
        self.assertEqual(karte.luecken(reihe), [])
        reihe = [self.abr("2026-10", -2000, -3000), self.abr("2026-9", -1000, -2000),
                 self.abr("X", -3000, -3500)]
        self.assertEqual(karte.luecken(reihe), [])


class KartenimportGebuchterUmsatz(unittest.TestCase):
    """Ein wiederholter Import berichtigt keinen Umsatz, der schon gebucht ist."""

    KOPF = "konto;abrechnung;seite;zeile;art;kaufdatum;buchungsdatum;betrag;text\n"
    KONTO = "KARTE-0000"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.con, self.m, self.gj = _leere_db(self.tmp)
        self.bk = self.con.execute(
            "INSERT INTO bankkonto (mandant_id, iban, bezeichnung) VALUES (?,?,?)",
            (self.m, self.KONTO, "Kreditkarte")).lastrowid

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def csv(self, kauf: str = "-12,50", tanken: str = "-40,00", text: str = "Buchhandlung"):
        neu_txt = db.eur(karte.cent(kauf) + karte.cent(tanken))
        zeilen = (
            "7;;;saldo_alt;;2026-03-01;0,00;Saldo",
            f"7;1;1;umsatz;2026-03-02;2026-03-03;{kauf};{text}, Musterstadt",
            f"7;1;2;umsatz;2026-03-04;2026-03-05;{tanken};Tankstelle, Beispielort",
            f"7;;;saldo_neu;;2026-03-31;{neu_txt};Neuer Saldo",
        )
        pfad = os.path.join(self.tmp, "karte.csv")
        with open(pfad, "w", encoding="utf-8") as fh:
            fh.write(self.KOPF + "".join(f"{self.KONTO};{z}\n" for z in zeilen))
        return pfad

    def umsatz(self, zeile: str):
        return self.con.execute(
            "SELECT * FROM banktransaktion WHERE hash=?",
            (karte.Umsatz("7", "1", zeile, "umsatz", "", 0, "", {}).hash,)).fetchone()

    def buchen(self, belegfeld: str | None = None) -> int:
        return self.con.execute(
            "INSERT INTO buchung (gj_id, nummer, datum, buchungstext, belegfeld)"
            " VALUES (?,?,?,?,?)", (self.gj, 1, "2026-03-03", "Fachbuch", belegfeld)).lastrowid

    def test_verknuepfter_umsatz_wird_abgelehnt(self):
        z = karte.importieren(self.con, self.m, self.csv(), 0)
        self.assertEqual(z["neu"], 2)
        t = self.umsatz("1")
        self.con.execute("UPDATE banktransaktion SET buchung_id=? WHERE id=?",
                         (self.buchen(), t["id"]))
        z = karte.importieren(self.con, self.m, self.csv(kauf="-15,20"), 0)
        self.assertEqual(z["berichtigt"], 0)
        self.assertEqual(len(z["abgelehnt"]), 1)
        meldung = z["abgelehnt"][0]
        for teil in ("Abrechnung 7, Seite 1, Zeile 1", "-12,50", "-15,20", "verknuepft"):
            self.assertIn(teil, meldung)
        self.assertEqual(self.umsatz("1")["betrag_cent"], -1250, "Betrag bleibt stehen")

    def test_gebucht_ueber_belegfeld_vor_bh_link_wird_abgelehnt(self):
        karte.importieren(self.con, self.m, self.csv(), 0)
        self.buchen(f"KA{self.umsatz('1')['id']}")
        z = karte.importieren(self.con, self.m, self.csv(kauf="-15,20"), 0)
        self.assertEqual(len(z["abgelehnt"]), 1)
        self.assertIn("Belegfeld KA", z["abgelehnt"][0])
        self.assertEqual(self.umsatz("1")["betrag_cent"], -1250)

    def test_unverknuepfter_umsatz_wird_berichtigt_und_gemeldet(self):
        karte.importieren(self.con, self.m, self.csv(), 0)
        t = self.umsatz("1")
        self.con.execute("UPDATE banktransaktion SET buchung_id=? WHERE id=?",
                         (self.buchen(), t["id"]))
        # Zeile 2 ist frei: sie darf berichtigt werden, Zeile 1 bleibt unverändert
        z = karte.importieren(self.con, self.m, self.csv(tanken="-41,00"), 0)
        self.assertEqual((z["berichtigt"], z["abgelehnt"]), (1, []))
        self.assertIn("Seite 1, Zeile 2", z["berichtigte"][0])
        self.assertIn("-40,00", z["berichtigte"][0])
        self.assertEqual(self.umsatz("2")["betrag_cent"], -4100)

    def test_textberichtigung_am_gebuchten_umsatz_bleibt_erlaubt(self):
        karte.importieren(self.con, self.m, self.csv(), 0)
        t = self.umsatz("1")
        self.con.execute("UPDATE banktransaktion SET buchung_id=? WHERE id=?",
                         (self.buchen(), t["id"]))
        z = karte.importieren(self.con, self.m, self.csv(text="Buchhandlung 3"), 0)
        self.assertEqual((z["berichtigt"], z["abgelehnt"]), (1, []))
        self.assertIn("Buchhandlung 3", self.umsatz("1")["text"])


class GeerbteFrist(unittest.TestCase):
    """Offene Punkte erben die Frist auch von Abgaben, deren Bezeichnung nicht
    auf das Jahr endet - Feststellung und Eröffnungsbilanz."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "mandant.json"), "w", encoding="utf-8") as fh:
            json.dump({"rechtsform": "UG (haftungsbeschränkt)",
                       "gesellschaftsvertrag": "01.07.2025",
                       "fristen": {"groessenklasse": "kleinst"}}, fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_frist_aus_art_und_jahr(self):
        heute = datetime.date(2026, 3, 1)
        # Das Vorjahr steht davor: seine Feststellung ist frueher faellig und
        # darf trotzdem nicht vererbt werden.
        eintraege = (fristen.jahrespflichten(self.tmp, 2024, "2024-12-31", heute)
                     + fristen.jahrespflichten(self.tmp, 2025, "2025-12-31", heute))
        soll = {e["art"]: e["faellig"] for e in eintraege
                if e["art"] in ("feststellung", "eroeffnungsbilanz")
                and "2025" in e["bezeichnung"]}
        self.assertEqual(set(soll), {"feststellung", "eroeffnungsbilanz"})
        offene = {"punkte": [
            {"id": "P1", "titel": "Beschluss vorbereiten", "status": "offen", "wer": "du",
             "blockiert": ["feststellung"]},
            {"id": "P2", "titel": "Eröffnungswerte klären", "status": "offen", "wer": "du",
             "blockiert": ["eroeffnungsbilanz"]},
        ], "fehlende_belege": []}
        d = aufgaben.sammeln(None, offene, {"eintraege": eintraege}, None,
                             heute.isoformat(), 2025)
        frist = {a["quelle"]: a for a in d["aufgaben"] if a["art"] == "punkt"}
        self.assertEqual(frist["P1"]["frist"], soll["feststellung"])
        self.assertEqual(frist["P2"]["frist"], soll["eroeffnungsbilanz"])
        self.assertTrue(frist["P1"]["frist_geerbt"] and frist["P2"]["frist_geerbt"])


if __name__ == "__main__":
    unittest.main()
