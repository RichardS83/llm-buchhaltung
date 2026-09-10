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
