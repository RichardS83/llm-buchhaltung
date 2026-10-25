# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""ERiC-Anbindung ohne ERiC: python3 -m unittest discover -s tests

Die Bibliothek der Finanzverwaltung liegt nicht im Repository und darf es
nicht. Geprüft wird deshalb, was ohne sie prüfbar ist — der Datensatz, die
Flags, das Festhalten — gegen eine Attrappe mit derselben Schnittstelle.

Ist ein SDK eingerichtet (ERIC_PFAD und ERIC_HERSTELLER_ID gesetzt), prüft
`EchteBibliothek` zusätzlich einen erfundenen Datensatz mit dem echten ERiC.
Lokal, ohne zu senden.
"""
import ctypes as ct
import json
import datetime
import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WURZEL)

from bhl import eric, eric_ansicht, eric_ertrag, eric_klartext, eric_vorgang, eric_xml  # noqa: E402

NS = "{http://www.elster.de/elsterxml/schema/v11}"
STAMM = {"name": "Muster Software GmbH", "strasse": "Beispielweg 7b", "plz": "10115",
         "ort": "Berlin", "steuernummer": "00/000/00000", "ust_idnr": "DE000000000"}
HID = "12345"                        # erfunden — die echte steht nie im Repository


def kennzahlen(**kz) -> dict:
    """Ergebnis von umsatzsteuer.kennzahlen() in der Form, die eric_xml liest."""
    basen = {k: 0 for k in ("81", "86", "21", "45", "46", "84")}
    steuern = {k: 0 for k in ("47", "85", "66", "67", "83")}
    for k, v in kz.items():
        (basen if k[2:] in basen else steuern)[k[2:]] = v
    return {"code": "43", "von": "2026-07-01", "bis": "2026-09-30",
            "basen": basen, "steuern": steuern}


def ustva_xml(**kw) -> str:
    k = kw.pop("k", None) or kennzahlen(kz81=800, kz21=2000, kz46=1200, kz47=22800,
                                         kz66=2100, kz67=22800, kz83=13100)
    args = dict(stamm=STAMM, steuernummer="1121081508150", bufa="1121", hersteller_id=HID,
                produkt="llm-buchhaltung", produktversion="1.0",
                heute=datetime.date(2026, 10, 21))
    args.update(kw)
    return eric_xml.ustva(k, **args)


class Datensatz(unittest.TestCase):
    def test_betraege_ohne_gleitkomma(self):
        self.assertEqual(eric_xml.betrag(13100), "131.00")
        self.assertEqual(eric_xml.betrag(-512), "-5.12")
        self.assertEqual(eric_xml.betrag(5), "0.05")
        self.assertEqual(eric_xml.euro_abgeschnitten(150099), 1500)
        self.assertEqual(eric_xml.euro_abgeschnitten(-150099), -1500)

    def test_hausnummer_getrennt(self):
        self.assertEqual(eric_xml.strasse_hausnummer("Musterweg 10"), ("Musterweg", "10", None))
        self.assertEqual(eric_xml.strasse_hausnummer("Beispielweg 7b"), ("Beispielweg", "7", "b"))
        self.assertEqual(eric_xml.strasse_hausnummer("Am Markt"), ("Am Markt", None, None))

    def test_kz83_steht_auch_bei_null(self):
        w = eric_xml.ustva_werte(kennzahlen())
        self.assertEqual(w, {"Kz83": "0.00"})

    def test_reihenfolge_folgt_dem_schema(self):
        """Das Schema ist eine xs:sequence — Kz 83 vor Kz 81 lehnt ERiC ab."""
        va = ET.fromstring(ustva_xml()).find(".//{*}Umsatzsteuervoranmeldung")
        kz = [e.tag.split("}")[1] for e in va if e.tag.split("}")[1].startswith("Kz")]
        self.assertEqual(kz, sorted(kz, key=eric_xml.USTVA_FELDER.index))
        self.assertEqual(kz[0], "Kz09")
        self.assertEqual(va.find("{*}Kz09").text, HID)
        self.assertEqual(va.find("{*}Kz83").text, "131.00")
        self.assertEqual(va.find("{*}Zeitraum").text, "43")

    def test_testmerker_nur_im_test(self):
        mit = ET.fromstring(ustva_xml(testmerker=eric_xml.TESTMERKER))
        ohne = ET.fromstring(ustva_xml())
        self.assertEqual(mit.find(f"{NS}TransferHeader/{NS}Testmerker").text, "700000004")
        self.assertIsNone(ohne.find(f"{NS}TransferHeader/{NS}Testmerker"))

    def test_widnr_ohne_unterscheidungsmerkmal(self):
        widnr = deutsche_idnr("12345678")             # zur Laufzeit, nicht im Quelltext
        xml = ustva_xml(stamm={**STAMM, "widnr": f"{widnr}-00001"})
        va = next(e for e in ET.fromstring(xml.encode()).iter()
                  if e.tag.endswith("}Umsatzsteuervoranmeldung"))
        namen = [e.tag.rsplit("}", 1)[-1] for e in va]
        self.assertEqual(namen[2:4], ["Steuernummer", "WIdNr"], "Reihenfolge des Schemas")
        self.assertEqual(va[3].text, widnr)
        with self.assertRaises(eric_xml.DatenFehlen):
            ustva_xml(stamm={**STAMM, "widnr": "123"})

    def test_berichtigung_setzt_kz10(self):
        va = ET.fromstring(ustva_xml(berichtigt=True)).find(".//{*}Umsatzsteuervoranmeldung")
        self.assertEqual(va.find("{*}Kz10").text, "1")

    def test_nutzdaten_tragen_ihren_namensraum(self):
        """ERiC liest die Nutzdaten als eigenes Dokument — ohne xmlns am
        Wurzelelement bricht die ZM mit einem Parse-Fehler ab."""
        text = eric_xml.zm({"kunden": [{"ust_idnr": "CZ00000000", "betrag": 200000}]},
                           jahr=2026, quartal=3, stamm=STAMM, hersteller_id=HID)
        self.assertIn('<zm xmlns="http://www.elster.de/elsterxml/schema/v11"', text)
        self.assertIn('<Anmeldungssteuern xmlns="http://finkonsens.de/elster/elsteranmeldung'
                      '/ustva/v2026"', ustva_xml())
        self.assertNotIn("ns0:", ustva_xml())

    def test_ungueltige_angaben_halten_an(self):
        with self.assertRaises(eric_xml.DatenFehlen):
            ustva_xml(hersteller_id="74931x")
        with self.assertRaises(eric_xml.DatenFehlen):
            ustva_xml(steuernummer="00/000/00000")
        with self.assertRaises(eric_xml.DatenFehlen):
            ustva_xml(stamm={**STAMM, "plz": ""})

    def test_zm_zeilen(self):
        z = ET.fromstring(eric_xml.zm(
            {"kunden": [{"ust_idnr": "CZ00000000", "betrag": 200099},
                        {"ust_idnr": "FR00000000000", "betrag": 20000}]},
            jahr=2026, monat=8, stamm=STAMM, hersteller_id=HID, berichtigt=True))
        zeilen = z.find(".//{*}zm-zeilen")
        self.assertEqual(zeilen.get("meldeart"), "11")
        self.assertEqual(zeilen.find("{*}mzr/{*}quart").text, "28")
        self.assertEqual([(e.find("{*}knre").text, e.find("{*}betrag").text, e.get("umsatzart"))
                          for e in zeilen.findall("{*}zeile")],
                         [("CZ00000000", "2000", "S"), ("FR00000000000", "200", "S")])
        self.assertEqual(z.find(".//{*}zulassNr/{*}zulnr1").text, "N")
        self.assertEqual(z.find(".//{*}Empfaenger").text, "BF")

    def test_zm_ohne_gueltige_idnr_haelt_an(self):
        for kunde in ({"ust_idnr": "ohne USt-IdNr.", "betrag": 100},
                      {"ust_idnr": "DE000000000", "betrag": 100}):
            with self.assertRaises(eric_xml.DatenFehlen):
                eric_xml.zm({"kunden": [kunde]}, jahr=2026, quartal=3, stamm=STAMM,
                            hersteller_id=HID)
        with self.assertRaises(eric_xml.DatenFehlen):
            eric_xml.zm({"kunden": []}, jahr=2026, quartal=3, stamm=STAMM, hersteller_id=HID)


# ------------------------------------------------ Ertragsteuererklärungen
#
# Erfundene GmbH im Gründungsjahr mit einem Verlust von 1.234,56 EUR.

def ueberleitung(verlust=-123456, **abweichend) -> dict:
    werte = {**dict(guv=verlust, einkuenfte_gewerbe=verlust, zve=verlust,
                    verlustvortrag=verlust, gewinn_gewerbe=verlust, gewerbeverlust=verlust),
             **abweichend}
    zeilen = [{"text": "Ergebnis", "betrag": werte.pop("guv"), "quelle": "guv"}]
    zeilen += [{"text": k, "betrag": v, "kennzahl": k} for k, v in werte.items()]
    return {"abschnitte": [{"id": "a", "zeilen": zeilen}], "gesperrt": False}


ANGABEN = {"neu_gegruendet": True, "vertraege_mit_anteilseignern": False,
           "gegenstand": "Software", "betriebsstaette": {"plz": "10115", "ort": "Berlin"},
           "kst1f": {"eintritt_steuerpflicht": "2025-03-01", "eigenkapital": 25000,
                     "nennkapital": 25000, "einlagekonto": 0, "ausschuettbarer_gewinn": 0,
                     "einlagekonto_anfang": 0, "einlagekonto_ende": 0}}
GMBH = {**STAMM, "rechtsform": "Gesellschaft mit beschränkter Haftung",
        "anteilseigner": [{"name": "Beispiel Holding GmbH", "steuernummer": "00/000/00000",
                           "bufa": "1121", "strasse": "Beispielweg 1", "plz": "10115",
                           "ort": "Berlin", "nennbetrag": "25.000,00", "prozent": "100,00",
                           "seit": "2025-03-01"}]}
GJ = {"beginn": "2025-03-01", "ende": "2025-12-31"}


def kst_xml(spec=None, angaben=None, **kw) -> str:
    args = dict(stamm=GMBH, gj=GJ, jahr=2025, steuernummer="1121081508150", bufa="1121",
                elster_stnr=lambda stnr, bufa: "1121081508151", hersteller_id=HID,
                produkt="bh", produktversion="1")
    args.update(kw)
    return eric_ertrag.kst(spec or ueberleitung(), angaben or ANGABEN, **args)


def gewst_xml(spec=None, angaben=None, **kw) -> str:
    args = dict(stamm=GMBH, jahr=2025, steuernummer="1121081508150", bufa="1121",
                hersteller_id=HID, produkt="bh", produktversion="1")
    args.update(kw)
    return eric_ertrag.gewst(spec or ueberleitung(), angaben or ANGABEN, **args)


class Ertragsteuer(unittest.TestCase):
    def test_ausschuettbarer_gewinn(self):
        """§ 27 Abs. 1 Satz 5 KStG: Eigenkapital − Nennkapital − Einlagekonto."""
        eng = re.sub(r">\s+<", "><", kst_xml())
        self.assertIn("<E8201001>25000</E8201001><E8201101>25000</E8201101>"
                      "<E8201201>0</E8201201><E8201301>0</E8201301>", eng)
        falsch = {**ANGABEN, "kst1f": {**ANGABEN["kst1f"], "ausschuettbarer_gewinn": 25000}}
        with self.assertRaises(eric_xml.DatenFehlen):
            kst_xml(angaben=falsch)

    def test_berichtigung_in_ergaenzenden_angaben(self):
        """Keine Kennzahl „berichtigt“: der Hinweis steht in Zeile 13 bzw. 21."""
        ang = {**ANGABEN, "ergaenzende_angaben": {"grund": "3", "kst": "Berichtigt K.",
                                                   "gewst": "Berichtigt G."}}
        eng = lambda x: re.sub(r">\s+<", "><", x)  # noqa: E731
        k = eng(kst_xml(angaben=ang))
        self.assertIn("<E8001305>3</E8001305><E8001304>Berichtigt K.</E8001304>", k)
        self.assertLess(k.index("<Qualifiz>"), k.index("<Wirtschaftsjahr>"), "Schemareihenfolge")
        self.assertGreater(k.index("<Qualifiz>"), k.index("</Steuerpflicht>"))
        g = eng(gewst_xml(angaben=ang))
        self.assertIn("<E4003204>3</E4003204><E4003205>Berichtigt G.</E4003205>", g)
        self.assertLess(g.index("<Erg_Ang>"), g.index("<Ang_Gew_Betr>"))
        self.assertNotIn("Qualifiz", kst_xml(), "ohne Angabe kein Abschnitt")
        with self.assertRaises(eric_xml.DatenFehlen):
            kst_xml(angaben={**ANGABEN, "ergaenzende_angaben": {"kst": "x" * 1000}})

    def test_verlust_auf_volle_euro_abgerundet(self):
        self.assertEqual(eric_ertrag.euro(-123456), -1235)
        self.assertEqual(eric_ertrag.euro(123456), 1234)
        self.assertEqual(eric_ertrag.werte(ueberleitung()),
                         {"ergebnis": -1235, "zve": -1235, "verlust": 1235})

    def test_kst_traegt_einkommen_und_verlustvortrag(self):
        b = eric_ertrag.betraege_aus_xml(kst_xml(), "kst")
        for feld in ("E8002001", "E8109901", "E8004904", "E8005401", "E8006901", "E8007501"):
            self.assertEqual(b[feld], "-1235", feld)
        self.assertEqual((b["E8008001"], b["E8009201"], b["E8008102"]), ("1235", "1235", "0"))

    def test_kst_anteilseigner_mit_dezimalkomma(self):
        xml = kst_xml()
        self.assertIn("<E8122104>25000,00</E8122104>", xml)
        self.assertIn("<E8122105>100</E8122105>", xml)
        self.assertIn("<E8122109>1121081508151</E8122109>", xml)
        self.assertIn("<E8001105>350</E8001105>", xml)
        self.assertIn("<Unterfallart>30</Unterfallart>", xml)

    def test_ohne_verlust_keine_anlage_verluste(self):
        self.assertNotIn("<Verluste>", kst_xml(ueberleitung(0)))

    def test_gewst(self):
        xml = gewst_xml()
        self.assertEqual(eric_ertrag.betraege_aus_xml(xml, "gewst"), {"E4001703": "-1235"})
        self.assertIn("<E4001110>10115</E4001110>", xml)
        self.assertIn("<Unterfallart>20</Unterfallart>", xml)

    def test_bescheidabholung_nur_mit_internet(self):
        self.assertIn("<Bescheid>2</Bescheid>", kst_xml())
        xml = kst_xml(angaben={**ANGABEN, "bescheiddaten_abholen": True})
        self.assertIn("<ArtRueckuebermittlung>INTERNET</ArtRueckuebermittlung>", xml)

    def test_testmerker_nur_im_test(self):
        self.assertNotIn("Testmerker", kst_xml())
        self.assertIn(eric_xml.TESTMERKER, gewst_xml(testmerker=eric_xml.TESTMERKER))

    def test_haelt_an_statt_unvollstaendig(self):
        with self.assertRaises(eric_xml.DatenFehlen):          # gesperrte Überleitung
            kst_xml({**ueberleitung(), "gesperrt": True})
        with self.assertRaises(eric_xml.DatenFehlen):          # Hinzurechnung
            kst_xml(ueberleitung(zve=-100000, einkuenfte_gewerbe=-100000, gewinn_gewerbe=-100000))
        with self.assertRaises(eric_xml.DatenFehlen):          # Vortrag aus dem Vorjahr
            kst_xml(ueberleitung(verlustvortrag=-200000))
        with self.assertRaises(eric_xml.DatenFehlen):
            kst_xml(stamm={**GMBH, "anteilseigner": []})
        with self.assertRaises(eric_xml.DatenFehlen):
            gewst_xml(angaben={**ANGABEN, "betriebsstaette": {}})
        with self.assertRaises(eric_xml.DatenFehlen):
            kst_xml(stamm={**GMBH, "rechtsform": "Aktiengesellschaft"})

    def test_null_ist_eine_angabe(self):
        # Einlagekonto 0 ist eingetragen, nicht vergessen
        self.assertIn("<E8245601>0</E8245601>", kst_xml())
        ohne = {**ANGABEN, "kst1f": {k: v for k, v in ANGABEN["kst1f"].items()
                                     if k != "einlagekonto_ende"}}
        with self.assertRaises(eric_xml.DatenFehlen):
            kst_xml(angaben=ohne)

    def test_reihenfolge_der_anlagen_folgt_dem_schema(self):
        e30 = next(e for e in ET.fromstring(kst_xml().encode()).iter() if e.tag.endswith("E30"))
        self.assertEqual([k.tag.rpartition("}")[2] for k in e30],
                         ["KSt1", "GK", "ZVE", "Verluste", "WA", "KSt1F", "Vorsatz"])

    def test_veranlagungszeitraum(self):
        z = eric_vorgang.Veranlagungszeitraum(2025, "2025-03-01", "2025-12-31")
        self.assertEqual((z.code, z.label), ("VZ", "2025-VZ"))
        self.assertIn("Veranlagungszeitraum 2025", z.bezeichnung)
        self.assertIn("01.03.–31.12.2025", z.bezeichnung)


class Attrappe:
    """Dieselben Funktionsnamen wie libericapi, ohne Finanzverwaltung."""

    def __init__(self, rc=0, antwort=b"", server=b""):
        self.rc, self.antwort, self.server = rc, antwort, server
        self.aufrufe, self.puffer, self.geschlossen = [], {}, []

    def EricInitialisiere(self, plugins, log):
        self.aufrufe.append(("init", plugins, log))
        return 0

    def EricBeende(self):
        self.aufrufe.append(("beende",))
        return 0

    def EricRueckgabepufferErzeugen(self):
        h = len(self.puffer) + 1
        self.puffer[h] = b""
        return h

    def EricRueckgabepufferInhalt(self, h):
        return self.puffer[h]

    def EricRueckgabepufferFreigeben(self, h):
        return 0

    def EricHoleFehlerText(self, code, h):
        self.puffer[h] = f"Text zu {code}".encode()
        return 0

    def EricGetHandleToCertificate(self, h, pins, pfad):
        h._obj.value = 7
        return 0

    def EricVersion(self, h):
        self.puffer[h] = (b"<EricVersion><Bibliothek><Name>libericapi.dylib</Name>"
                          b"<Produktversion>44, 3, 4, 0</Produktversion></Bibliothek></EricVersion>")
        return 0

    def EricCloseHandleToCertificate(self, h):
        self.geschlossen.append(h)
        return 0

    def EricBearbeiteVorgang(self, daten, dv, flags, druck, krypto, antwort, server):
        pin = ct.cast(krypto, ct.POINTER(eric.VerschluesselungsParameter)).contents.pin \
            if krypto else None
        self.aufrufe.append(("vorgang", dv, flags, druck is not None, pin))
        self.puffer[antwort], self.puffer[server] = self.antwort, self.server
        return self.rc


SERVER = (b'<?xml version="1.0"?><Elster xmlns="http://www.elster.de/elsterxml/schema/v11">'
          b'<TransferHeader><TransferTicket>et0000000000</TransferTicket></TransferHeader>'
          b'</Elster>')
PRUEF = (b'<EricBearbeiteVorgang xmlns="http://www.elster.de/EricXML/1.2/EricBearbeiteVorgang">'
         b'<FehlerRegelpruefung><Feldidentifikator>Kz83</Feldidentifikator>'
         b'<Text>Kz 83 stimmt nicht.</Text></FehlerRegelpruefung>'
         b'<Hinweis><Text>Bitte W-IdNr. angeben.</Text></Hinweis></EricBearbeiteVorgang>')


class Sitzung(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def eric(self, lib):
        return eric.Eric(os.path.join(self.tmp.name, "lib"),
                         os.path.join(self.tmp.name, "protokoll"), lib=lib)

    def test_pruefen_sendet_nicht(self):
        lib = Attrappe(rc=eric.HINWEISE, antwort=PRUEF)
        with self.eric(lib) as er:
            r = er.bearbeite("<x/>", "UStVA_2026")
        _, dv, flags, druck, pin = lib.aufrufe[1]
        self.assertEqual(flags, eric.VALIDIERE | eric.PRUEFE_HINWEISE)
        self.assertFalse(flags & eric.SENDE)
        self.assertIsNone(pin)
        self.assertTrue(r.ok)
        self.assertFalse(r.gesendet)
        self.assertEqual(r.hinweise, ["Bitte W-IdNr. angeben."])
        self.assertEqual(r.fehler, ["Kz 83 stimmt nicht. (Feld Kz83)"])
        self.assertEqual(lib.aufrufe[-1], ("beende",))

    def test_senden_mit_zertifikat(self):
        lib = Attrappe(server=SERVER)
        with self.eric(lib) as er:
            r = er.bearbeite("<x/>", "ZMDO", senden=True, zertifikat="/z.pfx", pin="0000")
        _, _, flags, _, pin = lib.aufrufe[1]
        self.assertEqual(flags, eric.VALIDIERE | eric.SENDE)
        self.assertEqual(pin, b"0000")
        self.assertEqual(lib.geschlossen, [7], "Zertifikat-Handle wird wieder freigegeben")
        self.assertTrue(r.gesendet)
        self.assertEqual(r.transferticket, "et0000000000")

    def test_senden_ohne_zertifikat_geht_nicht(self):
        with self.eric(Attrappe()) as er, self.assertRaises(eric.EricFehler):
            er.bearbeite("<x/>", "UStVA_2026", senden=True)

    def test_fehlgeschlagener_versand_ist_nicht_gesendet(self):
        with self.eric(Attrappe(rc=eric.FEHLER_NHEADER, server=SERVER)) as er:
            r = er.bearbeite("<x/>", "UStVA_2026", senden=True, zertifikat="/z", pin="0")
        self.assertFalse(r.ok)
        self.assertFalse(r.gesendet)
        self.assertEqual(r.text, f"Text zu {eric.FEHLER_NHEADER}")

    def test_schlanke_plugins(self):
        quelle = os.path.join(self.tmp.name, "lib", "plugins")
        os.makedirs(quelle)
        for n in ("libcommonData.dylib", "libcheckUStVA_2026.dylib", "libcheckZMDO.dylib",
                  "libcheckBilanz_6_9.dylib", "libcheckESt_2025.dylib", "libcheckLStA_2026.dylib",
                  "libcheckKSt_2025.dylib", "libcheckKStZ_2025.dylib", "libcheckGewSt_2025.dylib"):
            open(os.path.join(quelle, n), "w").close()
        gewaehlt = eric.einrichten(os.path.join(self.tmp.name, "lib"))
        self.assertEqual(gewaehlt, ["libcheckBilanz_6_9.dylib", "libcheckGewSt_2025.dylib",
                                    "libcheckKSt_2025.dylib", "libcheckUStVA_2026.dylib",
                                    "libcheckZMDO.dylib", "libcommonData.dylib"])
        self.assertEqual(self.eric(Attrappe()).plugin_pfad,
                         os.path.join(self.tmp.name, eric.PLUGIN_VERZEICHNIS))

    def test_zertifikatsdatum(self):
        self.assertEqual(eric._utc_datum("230817152116Z"), "2023-08-17")    # Doku
        self.assertEqual(eric._utc_datum("20290725175300Z"), "2029-07-25")  # echtes Zertifikat

    def test_mindestversion(self):
        self.assertEqual(eric.mindestversion(datetime.date(2026, 10, 21)), (43, 45))
        self.assertEqual(eric.mindestversion(datetime.date(2026, 4, 26)), (41, 43))
        self.assertEqual(eric.mindestversion(datetime.date(2027, 5, 1)), (45, 47))


class Ablage(unittest.TestCase):
    """Jeder Lauf in einem eigenen Ordner, nichts wird überschrieben."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ds = eric_vorgang.Datensatz("ustva", eric_vorgang.ust.Zeitraum(2026, quartal=3),
                                         "<x>ä</x>", "UStVA_2026", 13100, [("Kz 83", "131,00")])
        self.jetzt = datetime.datetime(2026, 10, 22, 10, 14, 0)

    def tearDown(self):
        self.tmp.cleanup()

    def eric(self, lib):
        return eric.Eric(os.path.join(self.tmp.name, "lib"),
                         os.path.join(self.tmp.name, "p"), lib=lib)

    def test_vorpruefung_holt_hinweise_ohne_zu_senden(self):
        lib = Attrappe(rc=eric.HINWEISE, antwort=PRUEF)
        with self.eric(lib) as er:
            r = eric_vorgang.vorpruefen(er, self.ds)
        _, _, flags, _, pin = lib.aufrufe[1]
        self.assertEqual(flags, eric.VALIDIERE | eric.PRUEFE_HINWEISE)
        self.assertIsNone(pin)
        self.assertEqual(r.hinweise, ["Bitte W-IdNr. angeben."])

    def test_lauf_mit_vorpruefung_und_pruefsumme(self):
        with self.eric(Attrappe(rc=eric.HINWEISE, antwort=PRUEF)) as er:
            vp = eric_vorgang.vorpruefen(er, self.ds)
        lib = Attrappe(server=SERVER)
        with self.eric(lib) as er:
            lauf = eric_vorgang.ausfuehren(er, self.ds, "test", self.tmp.name, zertifikat="/z.pfx",
                                           pin="0", vorpruefung=vp, jetzt=self.jetzt)
            zweiter = eric_vorgang.ausfuehren(er, self.ds, "test", self.tmp.name,
                                              zertifikat="/z.pfx", pin="0", jetzt=self.jetzt)
        self.assertTrue(lauf.ordner.endswith("2026/elster/2026-Q3-ustva/20261022-101400-test"))
        self.assertTrue(zweiter.ordner.endswith("-test-2"), "gleiche Sekunde, eigener Ordner")
        self.assertEqual(sorted(os.listdir(lauf.ordner)),
                         ["datensatz.xml", "lauf.json", "server.xml", "vorpruefung.xml"],
                         "leere ERiC-Rückgabe wird nicht abgelegt")
        d = eric_ansicht.laeufe(self.tmp.name)
        self.assertEqual(len(d), 2)
        l = next(x for x in d if x["id"].endswith("101400-test"))
        self.assertEqual(l["datensatz"]["sha256"], eric_vorgang.sha256("<x>ä</x>"))
        self.assertEqual(l["datensatz"]["bytes"], 9)
        self.assertEqual(l["eric"], "44.3.4.0")
        self.assertEqual(l["zertifikat"], "z.pfx")
        self.assertEqual(l["ergebnis"]["transferticket"], "et0000000000")
        self.assertEqual(l["vorpruefung"]["code"], eric.HINWEISE)
        with open(os.path.join(lauf.ordner, "datensatz.xml"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "<x>ä</x>")

    def test_abgebrochen_ruft_eric_nicht_auf(self):
        lib = Attrappe()
        with self.eric(lib) as er:
            lauf = eric_vorgang.ausfuehren(er, self.ds, "senden", self.tmp.name,
                                           abbrechen="Fehler in der Plausibilitätsprüfung",
                                           jetzt=self.jetzt)
        self.assertFalse([a for a in lib.aufrufe if a[0] == "vorgang"])
        self.assertIsNone(lauf.ergebnis)
        self.assertEqual(eric_ansicht.laeufe(self.tmp.name)[0]["abgebrochen"],
                         "Fehler in der Plausibilitätsprüfung")


class Vorschlaege(unittest.TestCase):
    """Vorgelegte Prüfungen: warum, was sich ändert, wie weit."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.z = eric_vorgang.ust.Zeitraum(2026, quartal=3)
        self.ds = eric_vorgang.Datensatz("ustva", self.z, "<x>ä</x>", "UStVA_2026", 13100,
                                         [("Kz 83", "131,00")])

    def tearDown(self):
        self.tmp.cleanup()

    def lauf(self, modus, minute, ds=None, rc=0, server=b""):
        er = eric.Eric(os.path.join(self.tmp.name, "lib"), os.path.join(self.tmp.name, "p"),
                       lib=Attrappe(rc=rc, server=server))
        with er:
            return eric_vorgang.ausfuehren(er, ds or self.ds, modus, self.tmp.name,
                                           zertifikat=None if modus == "pruefen" else "/z.pfx",
                                           pin=None if modus == "pruefen" else "0",
                                           jetzt=datetime.datetime(2026, 10, 23, 9, minute))

    def stand(self):
        return eric_ansicht.vorschlaege(self.tmp.name)

    def test_nur_fehlerfreie_pruefung_mit_grund(self):
        kaputt = self.lauf("pruefen", 1, rc=610001002)
        with self.assertRaises(eric_vorgang.VorgangFehler):
            eric_vorgang.vorschlagen(kaputt.ordner, "Grund")
        test = self.lauf("test", 2, server=SERVER)
        with self.assertRaises(eric_vorgang.VorgangFehler):
            eric_vorgang.vorschlagen(test.ordner, "Grund")
        gut = self.lauf("pruefen", 3)
        with self.assertRaises(eric_vorgang.VorgangFehler):
            eric_vorgang.vorschlagen(gut.ordner, "  ")
        self.assertEqual(self.stand(), [])

    def test_von_vorgelegt_bis_abgegeben(self):
        p = self.lauf("pruefen", 1)
        eric_vorgang.vorschlagen(p.ordner, "Berichtigung nach Abgrenzung",
                                 [eric_vorgang.aenderung("Kz 83 | 121,00 | 131,00")])
        (v,) = self.stand()
        self.assertEqual(v["stand"], "offen")
        self.assertEqual(v["vorschlag"]["aenderungen"],
                         [{"was": "Kz 83", "bisher": "121,00", "neu": "131,00"}])
        self.assertIsNone(v["test"])
        self.assertTrue(v["freigabe_lauf"].endswith("-pruefen"))
        self.lauf("test", 2, server=SERVER)
        (v,) = self.stand()
        self.assertEqual(v["stand"], "offen")
        self.assertTrue(v["test"]["id"].endswith("-test"))
        self.lauf("senden", 3, server=SERVER)
        (v,) = self.stand()
        self.assertEqual(v["stand"], "abgegeben")
        self.assertEqual(v["abgabe"]["transferticket"], "et0000000000")

    def test_neuere_pruefung_uebernimmt_oder_ueberholt(self):
        p = self.lauf("pruefen", 1)
        eric_vorgang.vorschlagen(p.ordner, "Grund")
        gleich = self.lauf("pruefen", 2)
        (v,) = self.stand()
        self.assertEqual(v["stand"], "offen", "derselbe Datensatz, neu geprüft")
        self.assertTrue(v["freigabe_lauf"].endswith(os.path.basename(gleich.ordner)))
        anders = eric_vorgang.Datensatz("ustva", self.z, "<x>ö</x>", "UStVA_2026", 1, [])
        neu = self.lauf("pruefen", 3, ds=anders)
        (v,) = self.stand()
        self.assertEqual(v["stand"], "überholt")
        self.assertTrue(v["neuere_pruefung"].endswith(os.path.basename(neu.ordner)))
        self.assertTrue(v["freigabe_lauf"].endswith(os.path.basename(p.ordner)))

    def test_anderer_tag_ist_kein_anderer_datensatz(self):
        def ds(tag):
            xml = (f"<x><de-gcd:genInfo.doc.id.generationDate contextRef=\"D\">2026-10-{tag}"
                   f"</de-gcd:genInfo.doc.id.generationDate><b>1</b></x>")
            return eric_vorgang.Datensatz("ebilanz", self.z, xml, "Bilanz_6.9", 1, [])
        p = self.lauf("pruefen", 1, ds=ds(23))
        eric_vorgang.vorschlagen(p.ordner, "Grund", pruefpunkte=["Summen stimmen", " "])
        neu = self.lauf("pruefen", 2, ds=ds(24))
        (v,) = self.stand()
        self.assertEqual(v["stand"], "offen")
        self.assertTrue(v["freigabe_lauf"].endswith(os.path.basename(neu.ordner)))
        self.assertEqual(v["vorschlag"]["pruefpunkte"], ["Summen stimmen"])

    def test_neuerer_vorschlag_ersetzt_den_alten(self):
        alt = self.lauf("pruefen", 1)
        eric_vorgang.vorschlagen(alt.ordner, "erster")
        neu = self.lauf("pruefen", 2)
        eric_vorgang.vorschlagen(neu.ordner, "zweiter")
        stand = {v["vorschlag"]["grund"]: v["stand"] for v in self.stand()}
        self.assertEqual(stand, {"erster": "ersetzt", "zweiter": "offen"})

    def test_verwerfen(self):
        p = self.lauf("pruefen", 1)
        with self.assertRaises(eric_vorgang.VorgangFehler):
            eric_vorgang.verwerfen(p.ordner)
        eric_vorgang.vorschlagen(p.ordner, "Grund")
        eric_vorgang.verwerfen(p.ordner, "doch nicht")
        (v,) = self.stand()
        self.assertEqual(v["stand"], "verworfen")
        self.assertEqual(v["vorschlag"]["verworfen"]["grund"], "doch nicht")

    def test_aenderung_lesen(self):
        self.assertEqual(eric_vorgang.aenderung("Anlage GK Zeile 11|-1.235|-1.535"),
                         {"was": "Anlage GK Zeile 11", "bisher": "-1.235", "neu": "-1.535"})
        for schlecht in ("ohne Trenner", "a|b", "|1|2"):
            with self.assertRaises(eric_vorgang.VorgangFehler):
                eric_vorgang.aenderung(schlecht)


def ods(pfad, blaetter):
    """Eine minimale OpenDocument-Tabelle: {blattname: [[zelle, …], …]}."""
    import zipfile
    from xml.sax.saxutils import escape
    t = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    x = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    tabellen = "".join(
        f'<table:table table:name="{escape(name)}">' + "".join(
            "<table:table-row>" + "".join(
                f"<table:table-cell><text:p>{escape(z)}</text:p></table:table-cell>" for z in reihe)
            + "</table:table-row>" for reihe in reihen) + "</table:table>"
        for name, reihen in blaetter.items())
    with zipfile.ZipFile(pfad, "w") as z:
        z.writestr("content.xml", f'<office:document-content xmlns:office="urn:oasis:names:tc:'
                   f'opendocument:xmlns:office:1.0" xmlns:table="{t}" xmlns:text="{x}">'
                   f"<office:body><office:spreadsheet>{tabellen}</office:spreadsheet>"
                   f"</office:body></office:document-content>")


class Klartext(unittest.TestCase):
    """Feldnamen -> Vordruck, Zeile, Wortlaut — aus einer erfundenen Jahresdokumentation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.doku = os.path.join(self.tmp.name, "Dokumentation", "Datenarten")
        ordner = os.path.join(self.doku, "ElsterErklaerung", "KSt", "Jahresdokumentation")
        os.makedirs(ordner)
        kopf = ["Kontext", "Name", "Beschreibung", "max. Zeilen", "Format"]
        ods(os.path.join(ordner, "Jahresdokumentation_E30_2025.ods"), {
            "GK - Felder": [kopf, ["Bilanz", "E8002001", "Jahresüberschuss", "1", "Ganzzahl"],
                            ["Weiteres", "E8001007", "Frage", "1", "JaNein (1|2)"],
                            ["Weiteres", "E8001105", "Rechtsform", "1",
                             "Benutzerdefinierte Typdefinition: RF - 3"]],
            "GK - Texte": [["", "Anlage GK ($WJ.Wert$)", "B GK1", "GK", "/GK[1]"],
                           ["", "", "Bilanzielles Ergebnis", "B Bilanz2", "GK/Bilanz", "/GK/Bilanz"],
                           ["11", "", "", "Jahresüberschuss laut Bilanz im Jahr $VZ$",
                            "FK E8002001", "GK/Bilanz", "/GK/Bilanz"]],
            "Benutzerdefinierte Typen": [["Name", "Version", "Beschreibung", "Format"],
                                         ["RF", "3", "", "Enumeration - 350 [GmbH] - 370 [UG]"]],
        })
        ods(os.path.join(self.tmp.name, "Dokumentation", "Finanzamtsdaten.ods"),
            {"Bayern": [["Finanzamtsnummer", "Finanzamtsname"], ["9198", "Musterstadt"]]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_vordruck_zeile_und_auswahl(self):
        xml = ("<Elster><DatenTeil><Nutzdatenblock><NutzdatenHeader><Empfaenger>9198</Empfaenger>"
               "</NutzdatenHeader><Nutzdaten><E30><GK><Bilanz><E8002001>-1535</E8002001></Bilanz>"
               "<Weiteres><E8001007>2</E8001007><E8001105>350</E8001105></Weiteres></GK>"
               "</E30></Nutzdaten></Nutzdatenblock></DatenTeil></Elster>")
        k = eric_klartext.lesbar(xml, "kst", 2025, "KSt_30_2025", doku=self.doku)
        self.assertEqual(k["ohne_text"], 0)
        gk = next(g for g in k["gruppen"] if g["feld"] == "GK")
        self.assertEqual(gk["titel"], "Anlage GK")
        z = {r["feld"]: r for r in gk["zeilen"]}
        self.assertEqual((z["E8002001"]["zeile"], z["E8002001"]["text"]),
                         ("11", "Jahresüberschuss laut Bilanz im Jahr 2025"))
        self.assertEqual(z["E8002001"]["abschnitt"], "Bilanzielles Ergebnis")
        self.assertEqual(z["E8001007"]["wert_text"], "Nein")
        self.assertEqual(z["E8001105"]["wert_text"], "GmbH")
        technik = next(g for g in k["gruppen"] if g.get("technisch"))
        self.assertEqual(technik["zeilen"][0]["wert_text"], "Finanzamt Musterstadt (Bayern)")

    def test_ohne_sdk_bleiben_feldnamen(self):
        k = eric_klartext.lesbar("<Elster><E30><GK><E8002001>1</E8002001></GK></E30></Elster>",
                                 "kst", 2025, doku="")
        self.assertIsNone(k["quelle"])

    def test_sdk_neben_der_bibliothek_finden(self):
        sdk = os.path.join(self.tmp.name, "v", "sdk", "ERiC-1", "Darwin-universal", "lib")
        os.makedirs(sdk)
        doku = os.path.join(self.tmp.name, "v", "doku", "ERiC-1", "Dokumentation", "Datenarten")
        os.makedirs(doku)
        self.assertEqual(eric_klartext.dokumentation(os.path.join(sdk, "libericapi.dylib")), doku)

    def test_ebilanz_eigene_bezeichnungen_und_naeherung(self):
        xml = ('<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
               'xmlns:de-gaap-ci="http://www.xbrl.de/taxonomies/de-gaap-ci-2025-04-01" '
               'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
               '<de-gaap-ci:bs.ass.currAss.cashEquiv.bank unitRef="EUR">10.00</de-gaap-ci:bs.ass.currAss.cashEquiv.bank>'
               '<de-gaap-ci:bs.ass.currAss.receiv.other.commPart.atyp xsi:nil="true"/>'
               '</xbrli:xbrl>')
        k = eric_klartext.lesbar(xml, "ebilanz", 2025, "Bilanz_6.9", doku="")
        aktiva = next(g for g in k["gruppen"] if g["titel"] == "Bilanz — Aktiva")
        bank, atyp = aktiva["zeilen"]
        self.assertEqual(bank["text"], "Guthaben bei Kreditinstituten")
        self.assertTrue(bank["betrag"])
        self.assertTrue(atyp["leer"])
        self.assertTrue(atyp["ungefaehr"])
        self.assertTrue(atyp["text"].startswith("Sonstige Vermögensgegenstände ›"))


class Ansicht(unittest.TestCase):
    def test_meldungen_feld_fuer_feld(self):
        m = eric_ansicht.meldungen(PRUEF.decode())
        self.assertEqual([x["art"] for x in m], ["fehler", "hinweis"])
        self.assertEqual(m[0]["feld_kurz"], "Kz83")
        self.assertEqual(m[1]["text"], "Bitte W-IdNr. angeben.")
        self.assertEqual(eric_ansicht.meldungen(""), [])

    def test_serverantwort(self):
        xml = ('<Elster xmlns="http://www.elster.de/elsterxml/schema/v11"><TransferHeader>'
               '<TransferTicket>et1</TransferTicket><Testmerker>700000004</Testmerker>'
               '<EingangsDatum>20261021135624</EingangsDatum><RC><Rueckgabe><Code>0</Code>'
               '<Text>Daten wurden erfolgreich angenommen.</Text></Rueckgabe><Stack><Code/>'
               '<Text/></Stack></RC></TransferHeader></Elster>')
        s = eric_ansicht.serverantwort(xml)
        self.assertEqual(s["eingang"], "2026-10-21T13:56:24")
        self.assertEqual(s["rueckgaben"], [{"code": "0",
                                            "text": "Daten wurden erfolgreich angenommen."}])
        self.assertTrue(s["angenommen"])

    def test_ohne_testmerker(self):
        mit = ustva_xml(testmerker=eric_xml.TESTMERKER)
        self.assertEqual(eric_ansicht.ohne_testmerker(mit), ustva_xml())

    def test_lauf_id_bleibt_im_mandanten(self):
        with tempfile.TemporaryDirectory() as d:
            for schlecht in ("../x", "2026/elster/../..", "2026/elster/a/..", "/etc/passwd",
                             "2026/elster/a/b/../../.."):
                with self.assertRaises(eric_ansicht.NichtGefunden, msg=schlecht):
                    eric_ansicht.ordner(d, schlecht)
            with self.assertRaises(eric_ansicht.NichtGefunden):
                eric_ansicht.datei(d, "2026/elster/a/b", "../../mandant.json")

    def test_vergleich(self):
        k = kennzahlen(kz81=800, kz21=2000, kz83=15200)
        xml = ustva_xml(k=k)
        gesendet = eric_ansicht.werte_aus_xml(xml, "ustva")
        buch = eric_xml.ustva_werte(k)
        self.assertTrue(all(r["gleich"] for r in eric_ansicht.vergleich("ustva", gesendet, buch)))
        buch["Kz81"] = "700"
        z = eric_ansicht.vergleich("ustva", gesendet, buch)
        self.assertEqual([r["kz"] for r in z], ["81", "21", "83"], "Reihenfolge des Vordrucks")
        self.assertEqual([r["kz"] for r in z if not r["gleich"]], ["81"])
        zm = [("CZ12345678", "2000", "S")]
        self.assertEqual([r["gleich"] for r in eric_ansicht.vergleich(
            "zm", zm, {"CZ12345678": "2000", "AT1": "5"})], [False, True])


class Festhalten(unittest.TestCase):
    def test_eintragen_ersetzt_den_zeitraum(self):
        with tempfile.TemporaryDirectory() as d:
            pfad = os.path.join(d, "voranmeldungen.csv")
            with open(pfad, "w", encoding="utf-8") as fh:
                fh.write("# Kommentar bleibt\n" + eric_vorgang.KOPF + "\n"
                         "2026;42;ustva;abgegeben;alt;2026-07-26;;erste\n")
            eric_vorgang.eintragen(d, 2026, "43", "zm", "t1", "2026-10-21", "neu; mit Semikolon")
            eric_vorgang.eintragen(d, 2026, "42", "ustva", "t2", "2026-10-22", "berichtigt")
            with open(pfad, encoding="utf-8") as fh:
                zeilen = fh.read().splitlines()
        self.assertEqual(zeilen[0], "# Kommentar bleibt")
        self.assertEqual(zeilen[2], "2026;42;ustva;abgegeben;t2;2026-10-22;;berichtigt")
        self.assertEqual(zeilen[3], "2026;43;zm;abgegeben;t1;2026-10-21;;neu, mit Semikolon")
        self.assertEqual(len(zeilen), 4)

    def test_stand(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(eric_vorgang.stand(d, 2026, "43", "zm"))
            eric_vorgang.eintragen(d, 2026, "43", "zm", "t1", "2026-10-21", "x")
            self.assertEqual(eric_vorgang.stand(d, 2026, "43", "zm")["transferticket"], "t1")
            self.assertIsNone(eric_vorgang.stand(d, 2026, "43", "ustva"))


def deutsche_idnr(acht: str) -> str:
    """Erfundene, aber prüfziffergültige USt-IdNr. (ISO 7064, MOD 11,10) — ERiC
    rechnet die Prüfziffer nach. Zur Laufzeit gebildet, damit im Quelltext
    keine Nummer steht, die wie eine echte aussieht."""
    produkt = 10
    for ziffer in acht:
        summe = (int(ziffer) + produkt) % 10 or 10
        produkt = (2 * summe) % 11
    pruef = 11 - produkt
    return f"DE{acht}{0 if pruef == 10 else pruef}"


@unittest.skipUnless(os.environ.get("ERIC_PFAD") and os.environ.get("ERIC_HERSTELLER_ID"),
                     "kein ERiC-SDK eingerichtet (ERIC_PFAD, ERIC_HERSTELLER_ID)")
class EchteBibliothek(unittest.TestCase):
    """Erfundene Zahlen, Teststeuernummer, nur Plausibilitätsprüfung — es wird
    nichts gesendet."""

    def test_ustva_und_zm_sind_plausibel(self):
        hid = os.environ["ERIC_HERSTELLER_ID"]
        with tempfile.TemporaryDirectory() as log, \
                eric.Eric(os.path.expanduser(os.environ["ERIC_PFAD"]), log) as er:
            stnr = er.steuernummer("198/113/10231", "9198")      # Teststeuernummer, Bayern
            u = er.bearbeite(ustva_xml(steuernummer=stnr, bufa="9198", hersteller_id=hid,
                                       testmerker=eric_xml.TESTMERKER), "UStVA_2026")
            self.assertTrue(u.ok, (u.text, u.fehler))
            z = er.bearbeite(eric_xml.zm({"kunden": [{"ust_idnr": "CZ12345678",
                                                      "betrag": 200000}]},
                                         jahr=2026, quartal=3,
                                         stamm={**STAMM, "ust_idnr": deutsche_idnr("10000000")},
                                         hersteller_id=hid, testmerker=eric_xml.TESTMERKER),
                             "ZMDO")
            self.assertTrue(z.ok, (z.text, z.fehler))

    def test_kst_und_gewst_sind_plausibel(self):
        hid = os.environ["ERIC_HERSTELLER_ID"]
        with tempfile.TemporaryDirectory() as log, \
                eric.Eric(os.path.expanduser(os.environ["ERIC_PFAD"]), log) as er:
            stnr = er.steuernummer("198/113/10231", "9198")      # Teststeuernummer, Bayern
            gemeinsam = dict(steuernummer=stnr, bufa="9198", hersteller_id=hid,
                             testmerker=eric_xml.TESTMERKER)
            k = er.bearbeite(kst_xml(elster_stnr=lambda s_, b: stnr, **gemeinsam), "KSt_30_2025")
            self.assertTrue(k.ok, (k.text, k.fehler))
            g = er.bearbeite(gewst_xml(**gemeinsam), "GewSt_2025")
            self.assertTrue(g.ok, (g.text, g.fehler))


class Weboberflaeche(unittest.TestCase):
    """Abgeben aus dem Browser: nur mit Token, nur von hier, nur die geprüfte Fassung."""

    def setUp(self):
        from bhl import web
        self.web = web
        self.alt = (web.eric_vorgang.durchfuehren, web.eric_vorgang.Einstellung.laden)
        web.eric_vorgang.Einstellung.laden = classmethod(lambda cls: None)
        self.ds = eric_vorgang.Datensatz("zm", eric_vorgang.ust.Zeitraum(2026, quartal=3),
                                         "<zm/>", "ZMDO", 200000, [])
        self.pruefung = {"id": "2026/elster/2026-Q3-zm/x-pruefen",
                         "datensatz": {"sha256": eric_vorgang.sha256("<zm/>")}}
        self.gesendet = []

        def durchfuehren(con, mid, kuerzel, md, z, art, modus, e, freigabe=None, **kw):
            freigabe(self.ds, None, self.pruefung)
            self.gesendet.append(modus)
            raise eric_vorgang.VorgangFehler("bis hierher")

        web.eric_vorgang.durchfuehren = durchfuehren

        class K:
            mandant_id, kuerzel, mandant_dir = 1, "x", "/tmp"
            con = staticmethod(lambda: None)
        self.k = K()

    def tearDown(self):
        self.web.eric_vorgang.durchfuehren, self.web.eric_vorgang.Einstellung.laden = self.alt

    def senden(self, **daten):
        d = {"art": "zm", "jahr": 2026, "zeitraum": "43", "pruefung": self.pruefung["id"],
             "gelesen": True, "betrag": "2.000,00"}
        d.update(daten)
        return self.web.eric_aktion(self.k, "senden", d)

    def test_freigabe_mit_allem(self):
        antwort, status = self.senden()
        self.assertEqual(self.gesendet, ["senden"])
        self.assertEqual(antwort["fehler"], "bis hierher")

    def test_ohne_freigabe_wird_nicht_gesendet(self):
        for falsch in ({"betrag": "1500"}, {"gelesen": False}, {"pruefung": "andere"}):
            antwort, status = self.senden(**falsch)
            self.assertEqual(status, 409, falsch)
        self.pruefung["datensatz"]["sha256"] = "veraltet"
        antwort, _ = self.senden()
        self.assertIn("weicht von der geprüften Fassung ab", antwort["fehler"])
        self.assertEqual(self.gesendet, [])

    def test_fremde_anfragen_abgewiesen(self):
        import http.client
        import threading
        web = self.web
        web.Handler.basis = {"dbs": [], "wurzel": "/tmp", "kuerzel": "x", "jahr": 2026,
                             "port": 0, "token": "geheim"}
        srv = web.Server(("127.0.0.1", 0), web.Handler)
        port = srv.server_address[1]
        web.Handler.basis["port"] = port
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def post(**kopf):
                c = http.client.HTTPConnection("127.0.0.1", port)
                h = {"Content-Type": "application/json", "Host": f"127.0.0.1:{port}"}
                h.update(kopf)
                c.request("POST", "/api/eric/senden", body=b"{}", headers=h)
                return c.getresponse().status
            self.assertEqual(post(), 403, "ohne Token")
            self.assertEqual(post(**{"X-BH-Token": "falsch"}), 403)
            self.assertEqual(post(**{"X-BH-Token": "geheim", "Origin": "https://boese.example"}), 403)
            self.assertEqual(post(**{"X-BH-Token": "geheim", "Host": f"boese.example:{port}"}), 403)
            c = http.client.HTTPConnection("127.0.0.1", port)
            c.request("GET", "/")
            self.assertIn(b'<meta name="bh-token" content="geheim">', c.getresponse().read())
        finally:
            srv.shutdown()
            srv.server_close()


ANFRAGE_ANTWORT = """<Elster xmlns="http://www.elster.de/elsterxml/schema/v11"><DatenTeil><Nutzdatenblock>
<Nutzdaten><Datenabholung xmlns="http://finkonsens.de/elster/elsterdatenabholung/v3" version="32">
<PostfachAnfrageAntwort einschraenkung="alle" max="1000">
 <DatenartBereitstellung name="EPMitteilung" anzahltreffer="1"><Bereitstellungen>
  <Bereitstellung id="be1" bereitstellungsticket="et1" groesse="10">
   <MetaInformationen><Meta name="steuernummer">9198011310231</Meta>
    <Meta name="verfuegbarbis">20300101</Meta>
    <MetaInfoMaps><MetaInfoMap typ="ANTWORT"><Meta name="antwortfrist">30.10.2026</Meta>
     <MetaList typ="antwortbetreff"><MetaListElement>Betreff</MetaListElement></MetaList>
    </MetaInfoMap></MetaInfoMaps></MetaInformationen>
   <Datenpaket><ReferenzId>r1</ReferenzId><Encoding>UTF-8</Encoding><Groesse>99</Groesse></Datenpaket>
   <Anhaenge><Anhang><DateinameMitErweiterung>../../boese.pdf</DateinameMitErweiterung>
    <Dateibezeichnung>Mitteilung</Dateibezeichnung><Dateityp>application/pdf</Dateityp>
    <DateiReferenzId>r2</DateiReferenzId><DateiGroesse>5</DateiGroesse></Anhang></Anhaenge>
  </Bereitstellung></Bereitstellungen></DatenartBereitstellung>
</PostfachAnfrageAntwort></Datenabholung></Nutzdaten></Nutzdatenblock></DatenTeil></Elster>"""


class Postfach(unittest.TestCase):
    def test_anfrage_lesen(self):
        from bhl import eric_postfach as ep
        b, = ep.bereitstellungen_lesen(ANFRAGE_ANTWORT)
        self.assertEqual((b["id"], b["datenart"], b["bereitstellungsticket"]), ("be1", "EPMitteilung", "et1"))
        self.assertEqual(b["meta"]["steuernummer"], "9198011310231")
        self.assertEqual(b["metainfomaps"][0]["listen"]["antwortbetreff"], ["Betreff"])
        self.assertEqual(b["datenpaket"], {"referenz": "r1", "encoding": "UTF-8", "groesse": 99})
        self.assertEqual(b["anhaenge"][0]["referenz"], "r2")

    def test_status_und_bestaetigung_lesen(self):
        from bhl import eric_postfach as ep
        ns = 'xmlns="http://finkonsens.de/elster/elsterdatenabholung/v3"'
        s = ep.status_lesen(f'<Datenabholung {ns}><PostfachStatusAntwort einschraenkung="alle">'
                            '<DatenartBereitstellung name="WIdNrVA" anzahltreffer="1"/>'
                            '</PostfachStatusAntwort></Datenabholung>')
        self.assertEqual(s, [{"datenart": "WIdNrVA", "anzahl": 1, "rolle": None}])
        b = ep.bestaetigung_lesen(
            f'<Datenabholung {ns}><PostfachBestaetigungAntwort><Bereitstellungen>'
            '<Bereitstellung id="be1"><Rueckgabe><Code>0</Code><Text>ok</Text></Rueckgabe>'
            '</Bereitstellung></Bereitstellungen></PostfachBestaetigungAntwort></Datenabholung>')
        self.assertEqual(b, {"be1": {"code": "0", "text": "ok"}})

    def test_ablage_bleibt_im_postfach(self):
        from bhl import eric_postfach as ep
        self.assertEqual(ep._sicher("../../boese.pdf"), "boese.pdf")
        self.assertEqual(ep._sicher(".."), "anhang")
        with tempfile.TemporaryDirectory() as d:
            o = os.path.join(ep.ablage(d), "EPMitteilung", "be1")
            os.makedirs(o)
            with open(os.path.join(o, "meta.json"), "w", encoding="utf-8") as fh:
                json.dump({"id": "be1", "datenart": "EPMitteilung", "meta": {}, "anhaenge": [
                    {"datei": "boese.pdf"}], "datenpaket": {"encoding": "UTF-8"}}, fh)
            with open(os.path.join(o, "datenpaket"), "wb") as fh:
                fh.write("<x>ä</x>".encode())
            self.assertEqual([n["pfad"] for n in ep.nachrichten(d)], ["EPMitteilung/be1"])
            self.assertEqual(ep.nachricht(d, "EPMitteilung/be1")["inhalt"], "<x>ä</x>")
            for schlecht in ("../x", "EPMitteilung/..", "a/b/c", "EPMitteilung/be2"):
                with self.assertRaises(ep.PostfachFehler, msg=schlecht):
                    ep.ordner(d, schlecht)
            with self.assertRaises(ep.PostfachFehler):
                ep.datei(d, "EPMitteilung/be1", "meta.json")
            self.assertTrue(ep.datei(d, "EPMitteilung/be1", "boese.pdf").endswith("be1/boese.pdf"))


EBILANZ_CSV = """konto;element;bezeichnung;seite;quelle;hinweis
1420;bs.ass.currAss.receiv.other.vat;Forderungen aus Umsatzsteuer;bilanz;plugin-6.9;
1800;bs.ass.currAss.cashEquiv.bank;Guthaben bei Kreditinstituten;bilanz;plugin-6.9;
2900;bs.eqLiab.equity.subscribed.corp;gezeichnetes Kapital;bilanz;plugin-6.9;
2910;bs.eqLiab.equity.subscribed.unpaidCap;nicht eingeforderte ausstehende Einlagen;bilanz;plugin-6.9;
6301;is.netIncome.regular.operatingTC.otherCost.legalConsulting;Rechts- und Beratungskosten;guv;plugin-6.9;
6855;is.netIncome.regular.operatingTC.otherCost.otherOrdinary;andere sonstige Aufwendungen;guv;plugin-6.9;
"""


class EBilanz(unittest.TestCase):
    """Ein Rumpfjahr wie das einer frisch gegründeten GmbH: halb eingezahltes
    Stammkapital, Notar, Bankgebühren - erfundene Beträge."""

    def setUp(self):
        from bhl import db, ledger
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(os.path.join(self.tmp.name, "probe.sqlite"))
        m = self.con.execute("INSERT INTO mandant (kuerzel, name) VALUES ('probe', 'Probe GmbH')"
                             ).lastrowid
        self.gj = self.con.execute("INSERT INTO geschaeftsjahr (mandant_id, jahr, beginn, ende)"
                                   " VALUES (?, 2025, '2025-10-07', '2025-12-31')", (m,)).lastrowid
        for nr, bez, typ in (("1420", "Umsatzsteuerforderungen", "A"), ("1800", "Bank", "A"),
                             ("2900", "Gezeichnetes Kapital", "P"),
                             ("2910", "Ausstehende Einlagen", "P"),
                             ("6301", "Gründungskosten", "X"), ("6855", "Bankgebühren", "X")):
            self.con.execute("INSERT INTO konto (mandant_id, nummer, bezeichnung, typ)"
                             " VALUES (?,?,?,?)", (m, nr, bez, typ))
        b = lambda d, t, z: ledger.buchen(self.con, self.gj, m, d, t, z)  # noqa: E731
        ledger.buchen(self.con, self.gj, m, "2025-10-07", "Gründung",
                      [("2910", 2500000, 0), ("2900", 0, 2500000)], art="eb")
        b("2025-10-14", "Einzahlung", [("1800", 1250000, 0), ("2910", 0, 1250000)])
        b("2025-10-21", "Notar", [("6301", 64000, 0), ("1420", 12160, 0), ("1800", 0, 76160)])
        b("2025-12-31", "Gebühren", [("6855", 4500, 0), ("1800", 0, 4500)])
        self.mdir = os.path.join(self.tmp.name, "m")
        os.makedirs(self.mdir)
        with open(os.path.join(self.mdir, "ebilanz.csv"), "w", encoding="utf-8") as fh:
            fh.write(EBILANZ_CSV)
        self.gjd = {"beginn": "2025-10-07", "ende": "2025-12-31"}

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def abschluss(self):
        from bhl import ebilanz_xbrl
        return ebilanz_xbrl.abschluss(self.con, self.gj, self.mdir)

    def test_vorzeichen_und_summen(self):
        a = self.abschluss()
        konten = {nr: c for nr, _, _, c in a.konten}
        self.assertEqual(konten["2900"], 2500000, "Passiva positiv")
        self.assertEqual(konten["2910"], 1250000, "Gegenposten positiv, wird abgezogen")
        self.assertEqual(konten["6301"], 64000, "Aufwand positiv, wird abgezogen")
        self.assertEqual(a.ergebnis, -68500)
        self.assertEqual(a.werte["bs.eqLiab.equity.subscribed"], 1250000)
        self.assertEqual(a.werte["bs.eqLiab.equity"], 1250000 - 68500)
        self.assertEqual(a.bilanzsumme, a.werte["bs.eqLiab"])
        self.assertEqual(a.werte["is.netIncome"], a.ergebnis)
        self.assertEqual(a.werte["is.netIncome.regular.operatingTC.otherCost"], 68500)
        self.assertIsNone(a.werte["bs.ass.fixAss.intan.goodwill"], "Mussfeld, leer berichtet")

    def test_eroeffnungsbilanz(self):
        """Eingeforderte Einlagen als Forderung, der Rest offen abgesetzt, keine GuV."""
        from bhl import ebilanz_xbrl
        stamm = {**STAMM, "rechtsform": "Gesellschaft mit beschränkter Haftung",
                 "einlagen": {"eingefordert_bei_gruendung": "12.500,00"}}
        a = ebilanz_xbrl.eroeffnung(self.con, self.gj, self.mdir, stamm)
        self.assertEqual(a.bilanzsumme, 1250000)
        self.assertEqual(a.werte[ebilanz_xbrl.EINGEFORDERT], 1250000)
        self.assertEqual(a.werte["bs.ass.currAss.receiv"], 1250000, "nicht doppelt gezählt")
        self.assertIsNone(a.werte["bs.ass.currAss.receiv.other"])
        self.assertEqual(a.werte["bs.eqLiab.equity.subscribed"], 1250000)
        self.assertFalse([k for k in a.werte if k.startswith("is.")], "Eröffnungsbilanz ohne GuV")
        self.assertEqual(sorted((nr, bez) for nr, bez, _, _ in a.konten),
                         [("2900", "Gezeichnetes Kapital"),
                          ("2910", "Ausstehende Einlagen, eingefordert"),
                          ("2910", "Ausstehende Einlagen, nicht eingefordert")])
        xml = ebilanz_xbrl.datensatz(
            a, stamm=stamm, gj=self.gjd, steuernummer="9198011310231", bufa="9198",
            hersteller_id=HID, produkt="p", produktversion="1",
            heute=datetime.date(2026, 10, 23), eroeffnung=True)
        self.assertIn("<ebilanz:stichtag>20251007</ebilanz:stichtag>", xml)
        self.assertIn("<xbrli:instant>2025-10-07</xbrli:instant>", xml)
        self.assertIn("statementType.statementType.EB", xml)
        self.assertNotIn("BVV.profitLoss", xml)
        wurzel = ET.fromstring(xml.encode())
        nil = {e.tag.rsplit(".", 1)[-1] for e in wurzel.iter()
               if ".reportElements." in e.tag and e.get(
                   "{http://www.w3.org/2001/XMLSchema-instance}nil") == "true"}
        self.assertTrue({"GuV", "BVV", "STU"} <= nil)
        # Ohne Angabe zum eingeforderten Teil bleibt alles beim Eigenkapital
        ohne = ebilanz_xbrl.eroeffnung(self.con, self.gj, self.mdir, {})
        self.assertEqual(ohne.bilanzsumme, 0)

    def test_fehlende_zuordnung_haelt_an(self):
        from bhl import ebilanz_xbrl
        with open(os.path.join(self.mdir, "ebilanz.csv"), "w", encoding="utf-8") as fh:
            fh.write(EBILANZ_CSV.replace("6855;", "#6855;"))
        with self.assertRaises(ebilanz_xbrl.EBilanzFehler):
            self.abschluss()

    def test_datensatz(self):
        from bhl import ebilanz_xbrl
        xml = ebilanz_xbrl.datensatz(
            self.abschluss(), stamm={**STAMM, "rechtsform": "Gesellschaft mit beschränkter Haftung"},
            gj=self.gjd, steuernummer="9198011310231", bufa="9198", hersteller_id=HID,
            produkt="p", produktversion="1", testmerker=eric_xml.TESTMERKER,
            heute=datetime.date(2026, 10, 22))
        wurzel = ET.fromstring(xml.encode())
        namen = [e.tag.rsplit("}", 1)[-1] for e in wurzel.iter()]
        self.assertIn("Testmerker", namen)
        fakt = {e.tag.rsplit("}", 1)[-1]: e.text for e in wurzel.iter() if e.get("decimals")}
        self.assertEqual(fakt["BVV.profitLoss"], "-685.00")
        self.assertEqual(fakt["BVV.profitLoss.contribution"], "12500.00", "eingezahlte Einlage")
        self.assertEqual(fakt["bs.ass"], fakt["bs.eqLiab"])
        idno = next(e for e in wurzel.iter() if e.tag.endswith("}genInfo.company.id.idNo"))
        self.assertEqual([k.tag.rsplit(".", 1)[1] for k in idno][:2], ["UID", "ST13"],
                         "Reihenfolge des Schemas")
        self.assertEqual(sum(1 for _ in wurzel.iter("{http://www.xbrl.de/taxonomies/"
                                                    "de-gaap-ci-2025-04-01}detailedInformation."
                                                    "accountBalances")), 6, "Kontennachweis")

    @unittest.skipUnless(os.environ.get("ERIC_PFAD") and os.environ.get("ERIC_HERSTELLER_ID"),
                         "kein ERiC-SDK eingerichtet")
    def test_plugin_nimmt_den_datensatz_an(self):
        from bhl import ebilanz_xbrl
        with tempfile.TemporaryDirectory() as log, \
                eric.Eric(os.path.expanduser(os.environ["ERIC_PFAD"]), log) as er:
            stnr = er.steuernummer("198/113/10231", "9198")      # Teststeuernummer, Bayern
            xml = ebilanz_xbrl.datensatz(
                self.abschluss(), stamm={**STAMM, "rechtsform": "GmbH", "ust_idnr": None},
                gj=self.gjd, steuernummer=stnr, bufa="9198",
                hersteller_id=os.environ["ERIC_HERSTELLER_ID"], produkt="p", produktversion="1",
                testmerker=eric_xml.TESTMERKER, heute=datetime.date(2026, 10, 22))
            r = er.bearbeite(xml, ebilanz_xbrl.taxonomie()["datenart_version"])
        self.assertTrue(r.ok, (r.text, r.fehler[:5]))


if __name__ == "__main__":
    unittest.main()
