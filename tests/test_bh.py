# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
# -*- coding: utf-8 -*-
"""Selbsttest ohne fremde Pakete: python3 -m unittest discover -s tests

Geprueft wird, was still kaputtgehen kann: die Cent-Arithmetik, der Aufbau
eines Kontenrahmens aus Stammdaten, und ob eine vollstaendige Buchfuehrung
vom Kontenplan bis zur Bilanz durchlaeuft und aufgeht.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WURZEL)

from bhl import db, ledger                     # noqa: E402
from bhl.importers import commerzbank as coba  # noqa: E402
from bhl.kontenplan import lade                # noqa: E402


class Kontenrahmen(unittest.TestCase):
    def test_alle_rahmen_laden(self):
        for name in ("SKR04-HOLDING", "SKR04-OPERATIV", "PRIVAT-ESt"):
            r = lade(name)
            self.assertTrue(r.konten, f"{name} hat keine Konten")
            self.assertEqual(len(r.konten[0]), 6, "Konten haben sechs Spalten")

    def test_unbekannter_rahmen(self):
        with self.assertRaises(KeyError):
            lade("GIBTSNICHT")

    def test_kontonummern_eindeutig(self):
        for name in ("SKR04-HOLDING", "SKR04-OPERATIV", "PRIVAT-ESt"):
            nummern = [k[0] for k in lade(name).konten]
            self.assertEqual(len(nummern), len(set(nummern)),
                             f"{name}: doppelte Kontonummer")

    def test_posten_zeigen_auf_gliederung(self):
        """Jedes Konto muss in Bilanz oder GuV landen - sonst verschwindet
        sein Saldo lautlos aus der Auswertung."""
        for name in ("SKR04-HOLDING", "SKR04-OPERATIV", "PRIVAT-ESt"):
            r = lade(name)
            bekannt = {p for zeilen in (r.bilanz_aktiva, r.bilanz_passiva, r.guv)
                       for _, ps in zeilen if ps for p in ps}
            # Drei Posten stehen mit Absicht ausserhalb beider Rechenwerke:
            # der Saldenvortrag ist eine Technik, die private Lebensfuehrung
            # ist nach § 12 EStG unbeachtlich, und Lohnersatzleistungen
            # erhoehen nach § 32b EStG nur den Steuersatz, nicht das Einkommen.
            gewollt = {"EROEFFNUNG", "PRIV_LEBEN", "PV_LOHNERSATZ"}
            for nr, bez, _typ, posten, *_ in r.konten:
                if posten is None or posten in gewollt:
                    continue
                self.assertIn(posten, bekannt,
                              f"{name}: Konto {nr} ({bez}) zeigt auf '{posten}',"
                              " das in keiner Gliederungszeile vorkommt")

    def test_objekte_aus_stammdaten(self):
        """Mietobjekte kommen aus der mandant.json, nicht aus dem Code."""
        sd = {"objekte": [
            {"nr": 1, "name": "Teststraße 1", "ort": "10115 Berlin",
             "angeschafft": "2020-01-01", "afa_satz": 250,
             "darlehen": [{"suffix": "10", "bezeichnung": "Testbank"}]},
        ]}
        r = lade("PRIVAT-ESt", sd)
        bez = {k[0]: k[1] for k in r.konten}
        self.assertEqual(bez["0110"], "Grund und Boden Teststraße 1")
        self.assertEqual(bez["3110"], "Darlehen Testbank – Teststraße 1")
        self.assertEqual(len(r.anlagen), 1, "eine Anlage V je Objekt")
        # ohne Stammdaten bleiben die Beispielobjekte - und niemandes Adresse
        for _nr, b, *_ in lade("PRIVAT-ESt").konten:
            self.assertNotIn("Teststraße", b)

    def test_kontobezeichnung_ueberschreibbar(self):
        r = lade("SKR04-HOLDING", {"kontobezeichnungen": {"1800": "Meine Bank"}})
        self.assertEqual({k[0]: k[1] for k in r.konten}["1800"], "Meine Bank")


class Cent(unittest.TestCase):
    def test_betrag_ist_ganzzahlig(self):
        """Geld nie als Gleitkomma - 0,1 + 0,2 ist dort nicht 0,3."""
        for text, cent in (("1.234,56", 123456), ("0,01", 1), ("1.000.000,00", 100000000),
                           ("0,00", 0), ("999,99", 99999)):
            self.assertEqual(coba._c(text), cent)
            self.assertIsInstance(coba._c(text), int)

    def test_iban_pruefziffer(self):
        """Aus Kontonummer und BLZ gebildete IBAN muss Modulo 97 erfuellen."""
        iban = coba._iban("12040000", "500050000")
        self.assertTrue(iban.startswith("DE"))
        umgestellt = iban[4:] + iban[:4]
        zahl = int("".join(str(int(c, 36)) for c in umgestellt))
        self.assertEqual(zahl % 97, 1, f"{iban} hat eine falsche Pruefziffer")


class Durchlauf(unittest.TestCase):
    """Der Beispielmandant, vom leeren Kontenplan bis zur ausgeglichenen Bilanz."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbp = os.path.join(self.tmp, "probe.sqlite")

    def bh(self, *args):
        r = subprocess.run([sys.executable, os.path.join(WURZEL, "bh"), "--db", self.dbp, *args],
                           capture_output=True, text=True, cwd=WURZEL)
        self.assertEqual(r.returncode, 0, f"bh {' '.join(args)}\n{r.stdout}\n{r.stderr}")
        return r.stdout

    def test_beispielmandant(self):
        d = "mandanten/beispiel/2025"
        self.bh("init", "beispiel", "2025")
        self.bh("eb", "beispiel", "2025", f"{d}/eb.csv")
        self.bh("buchen", "beispiel", "2025", f"{d}/buchungen.csv")

        bilanz = self.bh("report", "beispiel", "2025", "bilanz")
        aktiva = [z for z in bilanz.splitlines() if z.startswith("Summe Aktiva")][0]
        passiva = [z for z in bilanz.splitlines() if z.startswith("Summe Passiva")][0]
        self.assertEqual(aktiva.split()[-1], passiva.split()[-1],
                         "Bilanzsumme geht nicht auf")

        self.assertIn("keine Beanstandungen", self.bh("pruefen", "beispiel", "2025"))

    def test_wiederholter_import_haengt_nichts_an(self):
        """Zweimal dieselbe Datei buchen darf den Bestand nicht verdoppeln."""
        d = "mandanten/beispiel/2025"
        self.bh("init", "beispiel", "2025")
        self.bh("buchen", "beispiel", "2025", f"{d}/buchungen.csv")
        con = db.connect(self.dbp)
        vorher = con.execute("SELECT count(*) FROM buchung").fetchone()[0]
        con.close()
        self.bh("buchen", "beispiel", "2025", f"{d}/buchungen.csv")
        con = db.connect(self.dbp)
        self.assertEqual(con.execute("SELECT count(*) FROM buchung").fetchone()[0], vorher)
        con.close()

    def test_soll_gleich_haben(self):
        d = "mandanten/beispiel/2025"
        self.bh("init", "beispiel", "2025")
        self.bh("eb", "beispiel", "2025", f"{d}/eb.csv")
        self.bh("buchen", "beispiel", "2025", f"{d}/buchungen.csv")
        con = db.connect(self.dbp)
        soll, haben = con.execute(
            "SELECT COALESCE(sum(soll_cent),0), COALESCE(sum(haben_cent),0)"
            " FROM buchungszeile").fetchone()
        con.close()
        self.assertEqual(soll, haben, "Summe Soll ungleich Summe Haben")


class Einkommensteuer(unittest.TestCase):
    """Der private Beispielmandant: Ueberschussrechnung und Anlage V."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbp = os.path.join(self.tmp, "privat.sqlite")

    def bh(self, *args, erwartet=0):
        r = subprocess.run([sys.executable, os.path.join(WURZEL, "bh"), "--db", self.dbp, *args],
                           capture_output=True, text=True, cwd=WURZEL)
        self.assertEqual(r.returncode, erwartet,
                         f"bh {' '.join(args)}\n{r.stdout}\n{r.stderr}")
        return r.stdout

    def buchen(self):
        d = "mandanten/beispiel-privat/2025"
        self.bh("init", "beispiel-privat", "2025")
        self.bh("eb", "beispiel-privat", "2025", f"{d}/eb.csv")
        self.bh("buchen", "beispiel-privat", "2025", f"{d}/buchungen.csv")

    def test_beanstandet_nur_den_platzhalter(self):
        """Der Beispielmandant traegt absichtlich einen offenen Platzhalter -
        er ist da, um die Abgabesperre zu zeigen. Sonst muss alles sauber
        sein: die Buchfuehrung selbst darf nichts zu meckern geben."""
        self.buchen()
        aus = self.bh("pruefen", "beispiel-privat", "2025", erwartet=1)
        self.assertIn("Platzhalter", aus)
        self.assertIn("1 Fehler", aus)

    def test_afa_loest_keine_zehn_tage_warnung_aus(self):
        """§ 11 Abs. 1 S. 2 EStG setzt einen Zu- oder Abfluss voraus. Die AfA
        steht immer zum 31.12. und ist keine Zahlung - sie darf die Regel
        nicht ausloesen, sonst warnt jede Buchfuehrung jedes Jahr grundlos."""
        self.buchen()
        aus = self.bh("pruefen", "beispiel-privat", "2025", erwartet=1)
        self.assertNotIn("Zehn-Tage", aus)
        self.assertIn("0 Hinweise", aus)

    def test_anlage_v_traegt_die_zeilen_des_vordrucks(self):
        self.buchen()
        a = self.bh("report", "beispiel-privat", "2025", "anlagen")
        for zeile in ("Zeile 15", "Zeile 33", "Zeile 46", "Zeile 55",
                      "Zeile 83", "Zeile 85"):
            self.assertIn(zeile, a, f"{zeile} fehlt in der Anlage V")
        self.assertIn("Anlage V", a)

    def test_vermoegensuebersicht_geht_auf(self):
        self.buchen()
        b = self.bh("report", "beispiel-privat", "2025", "bilanz")
        a = [z for z in b.splitlines() if z.startswith("Summe Aktiva")][0]
        p = [z for z in b.splitlines() if z.startswith("Summe Passiva")][0]
        self.assertEqual(a.split()[-1], p.split()[-1])


class Kartenabrechnung(unittest.TestCase):
    """Erfasste Kreditkartenabrechnungen: eingelesen wird nur, was aufgeht."""

    KOPF = ("konto;abrechnung;seite;zeile;art;kaufdatum;buchungsdatum;betrag;text;"
            "waehrung;fremdbetrag;kurs;quelle\n")
    KONTO = "KARTE-0000"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbp = os.path.join(self.tmp, "privat.sqlite")
        self.bh("init", "beispiel-privat", "2025")
        con = db.connect(self.dbp)
        con.execute("INSERT INTO bankkonto (mandant_id, iban, bezeichnung, konto_id, nur_archiv)"
                    " SELECT m.id, ?, 'Kreditkarte', k.id, 1 FROM mandant m JOIN konto k"
                    " ON k.mandant_id=m.id AND k.nummer='3900'", (self.KONTO,))
        con.commit()
        con.close()

    def bh(self, *args, erwartet=0):
        r = subprocess.run([sys.executable, os.path.join(WURZEL, "bh"), "--db", self.dbp, *args],
                           capture_output=True, text=True, cwd=WURZEL)
        self.assertEqual(r.returncode, erwartet,
                         f"bh {' '.join(args)}\n{r.stdout}\n{r.stderr}")
        return r.stdout

    def csv(self, *zeilen):
        pfad = os.path.join(self.tmp, "karte.csv")
        with open(pfad, "w", encoding="utf-8") as fh:
            fh.write(self.KOPF + "".join(f"{self.KONTO};{z}\n" for z in zeilen))
        return pfad

    def umsaetze(self):
        con = db.connect(self.dbp)
        rows = con.execute("SELECT buchungsdatum, betrag_cent, text FROM banktransaktion"
                           " ORDER BY id").fetchall()
        con.close()
        return [tuple(r) for r in rows]

    # Zwei gleiche Kaeufe am selben Tag, eine Gutschrift, ein Entgelt.
    MAERZ = (
        "20250310;;;saldo_alt;;2025-02-10;-100,00;Saldo;;;;",
        "20250310;1;1;umsatz;2025-02-10;2025-02-11;100,00;Lastschrift;;;;",
        "20250310;1;2;umsatz;2025-02-12;2025-02-13;-1,85;Baeckerei, Musterstadt;;;;",
        "20250310;1;3;umsatz;2025-02-12;2025-02-13;-1,85;Baeckerei, Musterstadt;;;;",
        "20250310;1;4;umsatz;2025-02-20;2025-02-21;-1.020,40;Reise, Beispielort;USD;1.100,00;1,0780;",
        "20250310;1;4;entgelt;2025-02-20;2025-02-21;-19,90;Waehrungsumrechnung;;;;",
        "20250310;2;1;umsatz;2025-03-01;2025-02-10;46,00;Baumarkt, Musterstadt;;;;",
        "20250310;;;saldo_neu;;2025-03-10;-998,00;Neuer Saldo;;;;",
    )

    def test_betrag_ohne_gleitkomma(self):
        from bhl.importers import kartenabrechnung as karte
        self.assertEqual(karte.cent("-1.234,56"), -123456)
        self.assertEqual(karte.cent("0,07"), 7)
        self.assertEqual(karte.cent("20.000,00"), 2000000)
        for falsch in ("1,5", "1.234.56", "12", "1,234.56"):
            with self.assertRaises(ValueError):
                karte.cent(falsch)

    def test_gleiche_kaeufe_bleiben_zwei_umsaetze(self):
        """Der Dublettenschutz haengt an der Fundstelle. Am Inhalt gemessen
        waeren zwei Broetchenkaeufe am selben Tag einer - und der Saldo
        der Abrechnung ginge im Bestand nicht mehr auf."""
        aus = self.bh("import-karte", "beispiel-privat", "0", self.csv(*self.MAERZ))
        self.assertIn("6 neue Umsaetze", aus)
        u = self.umsaetze()
        self.assertEqual(sum(1 for x in u if x[1] == -185), 2)
        self.assertEqual(-10000 + sum(x[1] for x in u), -99800)

    def test_wiederholter_import_haengt_nichts_an(self):
        pfad = self.csv(*self.MAERZ)
        self.bh("import-karte", "beispiel-privat", "0", pfad)
        aus = self.bh("import-karte", "beispiel-privat", "0", pfad)
        self.assertIn("0 neue Umsaetze, 6 bereits vorhanden, 0 berichtigt", aus)
        self.assertEqual(len(self.umsaetze()), 6)

    def test_berichtigter_lesefehler_wird_fortgeschrieben(self):
        self.bh("import-karte", "beispiel-privat", "0", self.csv(*self.MAERZ))
        besser = [z.replace("Baumarkt, Musterstadt", "Baumarkt 12, Musterstadt")
                  for z in self.MAERZ]
        aus = self.bh("import-karte", "beispiel-privat", "0", self.csv(*besser))
        self.assertIn("0 neue Umsaetze, 5 bereits vorhanden, 1 berichtigt", aus)
        self.assertEqual(len(self.umsaetze()), 6)
        self.assertIn("Baumarkt 12, Musterstadt", [x[2] for x in self.umsaetze()])

    def test_abrechnung_die_nicht_aufgeht_bleibt_draussen(self):
        """Ein verlesener Betrag (1,85 statt 1,35) darf nicht im Bestand landen."""
        falsch = list(self.MAERZ)
        falsch[2] = falsch[2].replace("-1,85", "-1,35")
        aus = self.bh("import-karte", "beispiel-privat", "0", self.csv(*falsch), erwartet=1)
        self.assertIn("geht nicht auf", aus)
        self.assertEqual(self.umsaetze(), [])

    def test_fehlende_abrechnung_faellt_auf(self):
        """Die Kette der Salden reisst, wenn ein Monat fehlt."""
        mai = (
            "20250512;;;saldo_alt;;2025-04-10;-40,00;Saldo;;;;",
            "20250512;1;1;umsatz;2025-04-10;2025-04-11;40,00;Lastschrift;;;;",
            "20250512;;;saldo_neu;;2025-05-12;0,00;Neuer Saldo;;;;",
        )
        aus = self.bh("import-karte", "beispiel-privat", "0",
                      self.csv(*self.MAERZ, *mai), erwartet=1)
        self.assertIn("zwischen Abrechnung 20250310 und 20250512 fehlt etwas", aus)

    def test_jahr_filtert_nach_buchungstag(self):
        aus = self.bh("import-karte", "beispiel-privat", "2024", self.csv(*self.MAERZ))
        self.assertIn("0 neue Umsaetze", aus)


class Abgabesperre(unittest.TestCase):
    """`bh abgabe` muss nein sagen koennen - sonst traegt die Arbeitsteilung nicht."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def vorbereiten(self, mandant):
        dbp = os.path.join(self.tmp, f"{mandant}.sqlite")
        d = f"mandanten/{mandant}/2025"
        for args in (("init", mandant, "2025"),
                     ("eb", mandant, "2025", f"{d}/eb.csv"),
                     ("buchen", mandant, "2025", f"{d}/buchungen.csv")):
            subprocess.run([sys.executable, os.path.join(WURZEL, "bh"), "--db", dbp, *args],
                           capture_output=True, text=True, cwd=WURZEL, check=True)
        return dbp

    def abgabe(self, mandant):
        dbp = self.vorbereiten(mandant)
        return subprocess.run([sys.executable, os.path.join(WURZEL, "bh"), "--db", dbp,
                               "abgabe", mandant, "2025"],
                              capture_output=True, text=True, cwd=WURZEL)

    def test_platzhalter_sperrt_mit_rueckgabewert(self):
        r = self.abgabe("beispiel-privat")
        self.assertEqual(r.returncode, 1, "Ein offener Platzhalter muss sperren")
        self.assertIn("GESPERRT", r.stdout)
        self.assertIn("PLATZHALTER", r.stdout)
        # Der Platzhalter muss sagen, was ihn aufloest - sonst ist er nutzlos.
        self.assertIn("aufzulösen durch", r.stdout)

    def test_ohne_platzhalter_frei(self):
        r = self.abgabe("beispiel")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("FREI", r.stdout)

    def test_offene_entscheidung_sperrt(self):
        """Ein offener Punkt mit `blockiert` sperrt wie ein Platzhalter (03.10.2026)."""
        pfad = os.path.join(WURZEL, "mandanten", "beispiel", "2025", "offene_punkte.json")
        self.assertFalse(os.path.exists(pfad), "Testdatei darf nicht schon existieren")
        with open(pfad, "w", encoding="utf-8") as fh:
            json.dump({"punkte": [{"id": "Q1", "titel": "Steuerbilanz klären", "status": "offen",
                                   "blockiert": ["koerperschaftsteuer"],
                                   "aktion": "entscheiden"}]}, fh)
        try:
            r = self.abgabe("beispiel")
        finally:
            os.remove(pfad)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ENTSCHEIDUNG Q1", r.stdout)
        self.assertIn("GESPERRT", r.stdout)


class Ueberleitung(unittest.TestCase):
    """Die steuerliche Ueberleitung muss gegen die Kontensalden gehalten werden.

    Bis zum 10.09.2026 rechnete `bhl.steuern.pruefen` die Abweichungen zwar
    aus, wurde von `bh abgabe` aber nie aufgerufen - nur die Weboberflaeche
    benutzte sie. Ein Zahlendreher in steuern.json kam damit ungeprueft durch,
    obwohl das README das Gegenteil zusagte. Dieser Test haelt die Verdrahtung
    fest.
    """

    SPEC = "mandanten/beispiel/2025/steuern.json"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbp = os.path.join(self.tmp, "probe.sqlite")
        self.pfad = os.path.join(WURZEL, self.SPEC)
        with open(self.pfad, encoding="utf-8") as fh:
            self.original = fh.read()
        d = "mandanten/beispiel/2025"
        for args in (("init", "beispiel", "2025"),
                     ("eb", "beispiel", "2025", f"{d}/eb.csv"),
                     ("buchen", "beispiel", "2025", f"{d}/buchungen.csv")):
            subprocess.run([sys.executable, os.path.join(WURZEL, "bh"), "--db", self.dbp, *args],
                           capture_output=True, text=True, cwd=WURZEL, check=True)

    def tearDown(self):
        with open(self.pfad, "w", encoding="utf-8") as fh:
            fh.write(self.original)

    def abgabe(self):
        return subprocess.run([sys.executable, os.path.join(WURZEL, "bh"), "--db", self.dbp,
                               "abgabe", "beispiel", "2025"],
                              capture_output=True, text=True, cwd=WURZEL)

    def test_unverfaelscht_frei(self):
        r = self.abgabe()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("FREI", r.stdout)

    def test_zahlendreher_sperrt(self):
        """Der Steuerbilanzgewinn traegt `quelle: guv` und wird gegen das
        tatsaechliche Jahresergebnis gehalten."""
        spec = json.loads(self.original)
        spec["abschnitte"][0]["zeilen"][0]["betrag"] = 999999   # statt 369000
        with open(self.pfad, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, ensure_ascii=False, indent=2)
        r = self.abgabe()
        self.assertEqual(r.returncode, 1, "Eine Abweichung muss sperren:\n" + r.stdout)
        self.assertIn("ABWEICHUNG", r.stdout)
        self.assertIn("GESPERRT", r.stdout)

    def test_kontengestuetzte_zeile_wird_geprueft(self):
        """Zeilen mit `konten` werden gegen die Summe dieser Salden gehalten."""
        spec = json.loads(self.original)
        for z in spec["abschnitte"][0]["zeilen"]:
            if z.get("konten") == ["4900"]:
                z["betrag"] = -123456          # echt: -500000
                break
        else:
            self.fail("Zeile mit konten=['4900'] nicht gefunden")
        with open(self.pfad, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, ensure_ascii=False, indent=2)
        r = self.abgabe()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ABWEICHUNG", r.stdout)


class KeineEchtdaten(unittest.TestCase):
    """Wache gegen den Fehler, der dieses Repository ruinieren wuerde.

    Geprueft wird auf *Muster*, nicht auf eine Liste von Namen: eine Liste
    verbotener Klarnamen muesste die Klarnamen enthalten und waere selbst das
    Leck, das sie verhindern soll. Namen faengt die Durchsicht im Pull Request,
    strukturierte Kennungen faengt dieser Test.
    """

    MUSTER = [
        (r"\bDE\d{2} ?\d{4} ?\d{4} ?\d{4} ?\d{4} ?\d{2}\b", "IBAN"),
        (r"\b\d{2}/\d{3}/\d{5}\b", "Steuernummer"),
        (r"\bDE[1-9]\d{8}\b", "USt-IdNr."),
        (r"\b(?:ING-DiBa|Commerzbank|Sparkasse|Postbank|DKB|N26)\s*\d{6,}", "Kontonummer"),
        (r"\b\d{3} \d{4} \d{2}\b", "Kontonummer in Vierergruppen"),
        (r"/Users/[a-z][a-z.\-]+", "Pfad mit Benutzernamen"),
        (r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}", "Stripe-Schlüssel"),
        (r"\bph[xc]_[A-Za-z0-9]{16,}", "PostHog-Schlüssel"),
        (r"\bgh[pousr]_[A-Za-z0-9]{16,}", "GitHub-Token"),
        (r"\b[A-Za-z0-9._%+\-]+@(?!example\.(?:org|com)\b)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
         "E-Mail-Adresse"),
    ]

    # Was ausdruecklich erlaubt ist: die offizielle Test-IBAN der Deutschen
    # Bundesbank und die Nullsteuernummer des Beispielmandanten.
    ERLAUBT = ("DE02120300000000202051", "00/000/00000")

    def dateien(self):
        # Geprueft wird, was veroeffentlicht wuerde: versioniert oder neu und
        # nicht ignoriert. Was .gitignore ausschliesst, liegt nur lokal.
        import subprocess
        try:
            liste = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=WURZEL, capture_output=True, text=True, check=True).stdout.split("\n")
        except (OSError, subprocess.CalledProcessError):
            liste = None
        if liste is not None:
            for rel in filter(None, liste):
                if rel.endswith((".py", ".js", ".html", ".css", ".md", ".json",
                                 ".csv", ".yml", ".yaml")) or rel == "bh":
                    yield os.path.join(WURZEL, rel)
            return
        for wurzel, dirs, namen in os.walk(WURZEL):
            dirs[:] = [d for d in dirs if d not in
                       ("__pycache__", ".git", ".venv", "out", "node_modules")]
            # Echte Mandanten sind ohnehin nicht im Repository - hier zaehlt,
            # was versioniert wird, also der Beispielmandant.
            if os.path.relpath(wurzel, WURZEL).startswith("mandanten") \
                    and "beispiel" not in wurzel and wurzel.rstrip("/").endswith("mandanten"):
                pass
            for f in namen:
                if f.endswith((".py", ".js", ".html", ".css", ".md", ".json",
                               ".csv", ".yml", ".yaml")) or f == "bh":
                    yield os.path.join(wurzel, f)

    def test_keine_strukturierten_kennungen(self):
        import re
        treffer = []
        for p in self.dateien():
            rel = os.path.relpath(p, WURZEL)
            if rel in ("LICENSE", "tests/test_bh.py"):
                continue
            if rel.startswith("mandanten") and "beispiel" not in rel:
                continue
            with open(p, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
            for muster, was in self.MUSTER:
                for m in re.finditer(muster, text):
                    if any(e in m.group(0) for e in self.ERLAUBT):
                        continue
                    treffer.append(f"{rel}: {was} -> {m.group(0)}")
        self.assertEqual(treffer, [],
                         "Echtdaten im Repository:\n  " + "\n  ".join(treffer))

    def test_beispielmandant_ohne_echte_iban(self):
        with open(os.path.join(WURZEL, "mandanten/beispiel/mandant.json"),
                  encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["steuernummer"], "00/000/00000")
        for b in cfg["bankkonten"]:
            self.assertTrue(b["iban"].startswith("DE02120300000000202051"),
                            "nur die offizielle Test-IBAN der Deutschen Bundesbank")


if __name__ == "__main__":
    unittest.main()


class Abgabepaket(unittest.TestCase):
    """Fristen, Ausfuellhilfe, E-Bilanz und Aufgabenliste - mit erfundenen Zahlen.

    Hinzugekommen am 03.10.2026, als die Anwendung den Jahresabschluss einer
    UG vollstaendig abbilden sollte: Feststellung nach § 42a GmbHG,
    Lohnsteuerbescheinigungen, ELSTER-Zeilen aus den Konten, Kontennachweis
    der E-Bilanz und eine Liste dessen, was ein Mensch tun muss.
    """

    def setUp(self):
        import datetime
        self.tmp = tempfile.mkdtemp()
        self.dbp = os.path.join(self.tmp, "probe.sqlite")
        for args in (("init", "beispiel", "2025"),
                     ("eb", "beispiel", "2025", "mandanten/beispiel/2025/eb.csv"),
                     ("buchen", "beispiel", "2025", "mandanten/beispiel/2025/buchungen.csv")):
            subprocess.run([sys.executable, os.path.join(WURZEL, "bh"), "--db", self.dbp, *args],
                           capture_output=True, text=True, cwd=WURZEL, check=True)
        self.con = db.connect(self.dbp)
        self.gj = self.con.execute(
            "SELECT g.id, g.beginn, g.ende FROM geschaeftsjahr g JOIN mandant m ON m.id = g.mandant_id"
            " WHERE m.kuerzel = 'beispiel' AND g.jahr = 2025").fetchone()
        self.heute = datetime.date(2026, 10, 3)
        self.mdir = os.path.join(self.tmp, "m")
        os.makedirs(os.path.join(self.mdir, "2025"))

    def mandant(self, **fristen):
        with open(os.path.join(self.mdir, "mandant.json"), "w", encoding="utf-8") as fh:
            json.dump({"rechtsform": "UG (haftungsbeschränkt)", "fristen": fristen}, fh)

    def test_feststellung_elf_monate_bei_kleiner_gmbh(self):
        from bhl import fristen
        self.mandant(groessenklasse="kleinst")
        t = {e["art"]: e for e in fristen.jahrespflichten(self.mdir, 2025, "2025-12-31", self.heute)}
        self.assertEqual(t["feststellung"]["faellig"], "2026-11-30")
        self.mandant(groessenklasse="mittel")
        t = {e["art"]: e for e in fristen.jahrespflichten(self.mdir, 2025, "2025-12-31", self.heute)}
        self.assertEqual(t["feststellung"]["faellig"], "2026-08-31")
        # Ausgeschrieben heisst die Rechtsform nicht "GmbH" - auch sie gehoert dazu.
        self.assertTrue(fristen.ist_gmbh("Gesellschaft mit beschränkter Haftung"))
        self.assertFalse(fristen.ist_gmbh("Aktiengesellschaft"))

    def test_lohnsteuerbescheinigung_nur_bei_lohnzahlung(self):
        from bhl import fristen
        self.mandant()
        ohne = fristen.jahrespflichten(self.mdir, 2027, "2027-12-31", self.heute)
        self.assertNotIn("lohnsteuerbescheinigung", [e["art"] for e in ohne])
        mit = {e["art"]: e for e in fristen.jahrespflichten(
            self.mdir, 2027, "2027-12-31", self.heute, lohnzahlung=True)}
        # 29.02.2028 ist ein Dienstag - Schaltjahr, kein Wochenende
        self.assertEqual(mit["lohnsteuerbescheinigung"]["faellig"], "2028-02-29")

    def test_ausfuellhilfe_rechnet_aus_den_konten(self):
        from bhl import elster
        spec = {"formulare": [{"id": "x", "titel": "X", "name": "X", "abgabe": "koerperschaftsteuer",
                               "felder": [
            {"id": "ertrag", "feld": "Ertrag", "wert": {"konten": ["4900"], "vorzeichen": -1}},
            {"id": "aufwand", "feld": "Aufwand", "wert": {"konten": ["6825", "6855"]}},
            {"id": "saldo", "feld": "Saldo", "wert": {"differenz": ["ertrag", "aufwand"]}},
            {"id": "drittel", "feld": "Drittel", "wert": {"konten": ["4900"], "vorzeichen": -1},
             "format": "cent"},
            {"id": "fuenf", "feld": "5 %", "wert": {"prozent": ["ertrag", 5]}},
            {"id": "teil", "feld": "anteilig", "wert": {"buchungen": {"konto": "6825"},
                                                       "anteil": [1, 3]}, "format": "cent"},
            {"feld": "haengt", "wert": {"text": "1 = Ja"}, "offen": "Q1"}]}],
            "gegenproben": [{"feld": "aufwand", "konten": ["6825", "6855"], "text": "Aufwand"}]}
        jd = os.path.join(self.mdir, "2025")
        json.dump(spec, open(os.path.join(jd, "elster.json"), "w", encoding="utf-8"))
        json.dump({"punkte": [{"id": "Q1", "titel": "offen", "status": "offen"}]},
                  open(os.path.join(jd, "offene_punkte.json"), "w", encoding="utf-8"))
        d = elster.auswerten(self.con, self.gj["id"], {}, jd,
                             {"beginn": self.gj["beginn"], "ende": self.gj["ende"]})
        f = {x.get("id") or x["feld"]: x for x in d["formulare"][0]["felder"]}
        self.assertEqual(f["ertrag"]["betrag_cent"], 500000)
        self.assertEqual(f["ertrag"]["eintrag"], 5000)
        self.assertEqual(f["aufwand"]["betrag_cent"], 131000)
        self.assertEqual(f["saldo"]["betrag_cent"], 369000)
        self.assertEqual(f["fuenf"]["betrag_cent"], 25000)
        self.assertEqual(f["teil"]["betrag_cent"], 39667)        # 1.190,00 / 3, gerundet
        # Ein Feld, das an einem offenen Punkt haengt, macht das Formular unfertig.
        self.assertEqual(f["haengt"]["offen_punkt"]["id"], "Q1")
        self.assertFalse(d["formulare"][0]["bereit"])
        self.assertTrue(d["gegenproben"][0]["ok"])
        self.assertEqual(elster.euro(-29905178), -299052)
        self.assertEqual(elster.euro(1050), 11)

    def test_ebilanz_meldet_fehlende_zuordnung(self):
        from bhl import ebilanz
        from bhl.ledger import salden
        konten = [r["nummer"] for r in salden(self.con, self.gj["id"]) if r["saldo"]]
        zeilen = ["konto;element;bezeichnung;seite;quelle;hinweis"]
        for n in konten:
            typ = self.con.execute("SELECT typ FROM konto k JOIN mandant m ON m.id = k.mandant_id"
                                   " WHERE m.kuerzel='beispiel' AND k.nummer=?", (n,)).fetchone()[0]
            seite, el = (("bilanz", "bs.ass.x") if typ == "A" else ("bilanz", "bs.eqLiab.x")
                         if typ == "P" else ("guv", "is.x"))
            zeilen.append(f"{n};{el}.{n};Konto {n};{seite};FFL-2024;")
        pfad = os.path.join(self.mdir, "ebilanz.csv")
        open(pfad, "w", encoding="utf-8").write("\n".join(zeilen) + "\n")
        d = ebilanz.auswerten(self.con, self.gj["id"], self.mdir)
        self.assertTrue(d["vollstaendig"], d["pruefungen"])
        # Ein Konto weglassen: die Zuordnung ist unvollstaendig.
        open(pfad, "w", encoding="utf-8").write("\n".join(zeilen[:-1]) + "\n")
        d = ebilanz.auswerten(self.con, self.gj["id"], self.mdir)
        self.assertFalse(d["vollstaendig"])
        self.assertEqual([k["nummer"] for k in d["fehlend"]], [konten[-1]])

    def test_vor_jahresende_ist_nichts_bereit(self):
        """Vor Jahresende stand der Jahresabschluss des laufenden Jahres als „bereit zum Absenden“ da -
        weil für ihn keine Prüfung ein Hindernis fand. Das Jahr lief aber noch."""
        import datetime
        from bhl import abgaben
        self.mandant(groessenklasse="kleinst")
        gj = {"beginn": self.gj["beginn"], "ende": self.gj["ende"]}

        def zustaende(heute):
            d = abgaben.paket(self.con, self.gj["id"], {}, self.mdir,
                              os.path.join(self.mdir, "2025"), 2025, gj, heute,
                              kapitalgesellschaft=True, umsatzsteuerpflichtig=False,
                              lohnzahlung=False)
            return {a["art"]: a for a in d["abgaben"]}, d["zaehler"]

        waehrend, zaehler = zustaende(datetime.date(2025, 12, 31))
        self.assertEqual(waehrend["jahresabschluss"]["zustand"], "laeuft")
        self.assertEqual(waehrend["jahresabschluss"]["hindernisse"][0]["art"], "zeitraum")
        self.assertEqual(zaehler["bereit"], 0)
        danach, _ = zustaende(datetime.date(2026, 1, 2))
        self.assertEqual(danach["jahresabschluss"]["zustand"], "bereit")

    def test_laufendes_jahr_ist_keine_aufgabe(self):
        from bhl import aufgaben
        paket = {"abgaben": [{"art": "jahresabschluss", "titel": "Jahresabschluss aufstellen 2026",
                              "bezeichnung": "Jahresabschluss 2026", "frist": "2027-06-30",
                              "tage": 251, "zustand": "laeuft",
                              "hindernisse": [{"text": "Geschäftsjahr läuft noch bis 31.12.2026",
                                               "art": "zeitraum"}],
                              "weg": "intern", "empfaenger": "GF", "schritt": None}]}
        d = aufgaben.sammeln(paket, {"punkte": [], "fehlende_belege": []}, {"eintraege": []},
                             None, "2026-10-22", 2026)
        self.assertEqual(d["aufgaben"], [])
        self.assertEqual([a["titel"] for a in d["im_blick"]], ["Jahresabschluss aufstellen 2026"])

    def test_lastschrift_ist_keine_aufgabe(self):
        from bhl import aufgaben
        t = {"art": "ustva_zahlung", "bezeichnung": "Umsatzsteuer-Zahllast Q3", "faellig": "2026-11-10",
             "tage": 19, "status": "offen", "grundlage": "§ 18", "lastschrift": True}
        d = aufgaben.sammeln(None, {"punkte": [], "fehlende_belege": []}, {"eintraege": [t]},
                             None, "2026-10-22", 2026)
        self.assertEqual(d["aufgaben"], [])
        self.assertIn("Lastschrift", d["im_blick"][0]["aktion"])
        # Frist verstrichen und nichts abgebucht: dann doch eine Aufgabe
        d = aufgaben.sammeln(None, {"punkte": [], "fehlende_belege": []},
                             {"eintraege": [{**t, "tage": -3, "status": "ueberfaellig"}]},
                             None, "2026-11-13", 2026)
        self.assertEqual(len(d["aufgaben"]), 1)
        # Ein Zeitraum, der noch laeuft, ist ebenfalls keine Aufgabe
        d = aufgaben.sammeln(None, {"punkte": [], "fehlende_belege": []},
                             {"eintraege": [{**t, "lastschrift": False, "vorlaeufig": True}]},
                             None, "2026-10-22", 2026)
        self.assertEqual(d["aufgaben"], [])

    def test_zahlung_aus_bank_erkannt(self):
        from bhl import umsatzsteuer as ust
        m = self.con.execute("SELECT id FROM mandant WHERE kuerzel='beispiel'").fetchone()["id"]
        k = self.con.execute("INSERT INTO bankkonto (mandant_id, iban, bezeichnung) VALUES (?,?,?)",
                             (m, "DE00TEST", "Test")).lastrowid
        self.con.execute("INSERT INTO banktransaktion (bankkonto_id, buchungsdatum, betrag_cent,"
                         " text, hash) VALUES (?,?,?,?,?)",
                         (k, "2026-11-10", -13100, "Finanzamt | STEUERNR 1 UMS.ST 3VJ.26 131,00EUR",
                          "h1"))
        self.assertEqual(ust.zahlung_aus_bank(self.con, m, ust.Zeitraum(2026, quartal=3))
                         ["buchungsdatum"], "2026-11-10")
        self.assertIsNone(ust.zahlung_aus_bank(self.con, m, ust.Zeitraum(2026, quartal=2)))

    def test_aufgabe_erbt_frist_der_blockierten_abgabe(self):
        from bhl import aufgaben
        paket = {"abgaben": [{"art": "koerperschaftsteuer", "titel": "KSt 2025",
                              "bezeichnung": "Körperschaftsteuererklärung 2025",
                              "frist": "2026-10-31", "tage": 28, "zustand": "blockiert",
                              "hindernisse": [{"punkt": "Q1", "text": "x"}], "weg": "Mein ELSTER",
                              "empfaenger": "Finanzamt", "schritt": None}]}
        offene = {"punkte": [
            {"id": "Q1", "titel": "Entscheiden", "status": "offen", "wer": "du",
             "blockiert": ["koerperschaftsteuer"], "aktion": "entscheiden"},
            {"id": "Q2", "titel": "Fonds liefern", "status": "offen", "wer": "extern"},
            {"id": "Q3", "titel": "ohne Zuständigkeit", "status": "offen"},
            {"id": "Q4", "titel": "erledigt", "status": "behoben", "wer": "du"}],
            "fehlende_belege": []}
        d = aufgaben.sammeln(paket, offene, {"eintraege": []}, None, "2026-10-03", 2025)
        q1 = next(a for a in d["aufgaben"] if a.get("quelle") == "Q1")
        self.assertEqual(q1["frist"], "2026-10-31")
        self.assertTrue(q1["frist_geerbt"])
        self.assertIn("Q3", [a.get("quelle") for a in d["aufgaben"]],
                      "ein Punkt ohne `wer` gehoert auf die Aufgabenliste")
        self.assertEqual([a["quelle"] for a in d["im_blick"]], ["Q2"])
        self.assertNotIn("Q4", [a.get("quelle") for a in d["aufgaben"]])

    def test_kommende_fristen_ausserhalb_des_pakets(self):
        """Voranmeldung, ZM und Abgaben des Vorjahres stehen auf der Liste,
        bevor sie ueberfaellig sind; was das Paket schon fuehrt, nicht doppelt."""
        from bhl import aufgaben
        paket = {"abgaben": [{"art": "ebilanz", "titel": "E-Bilanz 2026",
                              "bezeichnung": "E-Bilanz 2026 übermitteln",
                              "frist": "2027-08-02", "tage": 296, "zustand": "blockiert",
                              "hindernisse": [], "weg": "ERiC", "empfaenger": "Finanzamt",
                              "schritt": None}]}

        def termin(bez, faellig, tage, status="offen"):
            return {"art": "x", "bezeichnung": bez, "faellig": faellig, "tage": tage,
                    "status": status,
                    "grundlage": "§", "notiz": None, "verweis": None}
        termine = {"eintraege": [
            termin("Zusammenfassende Meldung 3. Kalendervierteljahr 2026", "2026-10-26", 16),
            termin("E-Bilanz 2025 übermitteln", "2026-11-30", 51, "verlaengert"),
            termin("Umsatzsteuer-Voranmeldung 2. Kalendervierteljahr 2026", "2026-08-10", -61,
                   "erledigt"),
            termin("E-Bilanz 2026 übermitteln", "2027-08-02", 296)]}
        d = aufgaben.sammeln(paket, {"punkte": [], "fehlende_belege": []}, termine, None,
                             "2026-10-10", 2026)
        titel = [a["titel"] for a in d["aufgaben"]]
        self.assertEqual(titel[:2], ["Zusammenfassende Meldung 3. Kalendervierteljahr 2026",
                                     "E-Bilanz 2025 übermitteln"])
        self.assertNotIn("E-Bilanz 2026 übermitteln", titel, "steht schon als Abgabe da")
        self.assertNotIn("Umsatzsteuer-Voranmeldung 2. Kalendervierteljahr 2026", titel)
        self.assertEqual(d["naechste_frist"], "2026-10-26")
        self.assertEqual(d["zaehler"]["ueberfaellig"], 0)


class Oberflaeche(unittest.TestCase):
    """`bh serve --ensure` stellt genau einen Server sicher und nennt den Link.

    AGENTS.md verlangt, dass jede Sitzung mit dem Link auf die laufende
    Oberflaeche endet. Ohne diese Zusicherungen waere das eine Bitte an den
    Agenten statt einer Eigenschaft der Anwendung.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbp = os.path.join(self.tmp, "probe.sqlite")
        for args in (("init", "beispiel", "2025"),
                     ("buchen", "beispiel", "2025",
                      "mandanten/beispiel/2025/buchungen.csv")):
            subprocess.run([sys.executable, os.path.join(WURZEL, "bh"),
                            "--db", self.dbp, *args],
                           capture_output=True, text=True, cwd=WURZEL, check=True)
        self.port = self._freier_port()
        self.kinder = []

    def tearDown(self):
        for p in self.kinder:
            try:
                os.killpg(os.getpgid(p), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    @staticmethod
    def _freier_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _pids(self):
        r = subprocess.run(["pgrep", "-f", f"serve beispiel 2025 --port {self.port}"],
                           capture_output=True, text=True)
        return [int(z) for z in r.stdout.split()]

    def ensure(self):
        return subprocess.run(
            [sys.executable, os.path.join(WURZEL, "bh"), "--db", self.dbp, "serve",
             "beispiel", "2025", "--port", str(self.port), "--ensure"],
            capture_output=True, text=True, cwd=WURZEL, timeout=30)

    def test_gibt_den_link_aus_und_startet_nur_einen(self):
        erst = self.ensure()
        self.assertEqual(erst.returncode, 0, erst.stderr)
        adresse = erst.stdout.strip()
        self.assertEqual(adresse, f"http://127.0.0.1:{self.port}/")
        self.kinder = self._pids()
        self.assertEqual(len(self.kinder), 1, "kalter Start ergab nicht genau einen Server")

        # Die Oberflaeche antwortet auch wirklich - ein Link auf einen toten
        # Server waere schlimmer als keiner.
        with urllib.request.urlopen(adresse, timeout=5) as r:
            self.assertEqual(r.status, 200)

        zweit = self.ensure()
        self.assertEqual(zweit.returncode, 0, zweit.stderr)
        self.assertEqual(zweit.stdout.strip(), adresse)
        self.kinder = self._pids()
        self.assertEqual(len(self.kinder), 1,
                         "der zweite Aufruf hat einen weiteren Server gestartet")

    def test_belegter_port_meldet_statt_abzustuerzen(self):
        erst = self.ensure()
        self.assertEqual(erst.returncode, 0, erst.stderr)
        self.kinder = self._pids()

        r = subprocess.run(
            [sys.executable, os.path.join(WURZEL, "bh"), "--db", self.dbp, "serve",
             "beispiel", "2025", "--port", str(self.port), "--kein-browser"],
            capture_output=True, text=True, cwd=WURZEL, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("belegt", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
