# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Ein Vorgang mit ERiC: Datensatz bauen, prüfen, senden, festhalten.

`bh eric` ist die Kommandozeile dazu, dieses Modul die Mechanik:

    pruefen   lokal plausibilisieren, Vorschau-PDF       nichts verlässt den Rechner
    test      senden mit Testmerker                       der Server prüft und löscht
    senden    die Abgabe                                  nur von einem Menschen

Was übermittelt wurde, landet in `mandanten/<kürzel>/voranmeldungen.csv` —
derselben Datei, die bisher von Hand nach einer Abgabe in Mein ELSTER gepflegt
wurde. Die Seiten *Abgaben* und *Fristen* lesen sie unverändert weiter.

Angaben je Mandant stehen in `mandant.json` unter `elster`:

    "elster": {"bufa": "9198", "zertifikat": "~/Pfad/zum/zertifikat.pfx"}

`bufa` ist die vierstellige Bundesfinanzamtsnummer (Liste im ERiC-SDK,
`Dokumentation/Finanzamtsdaten.ods`), `zertifikat` die .pfx-Datei des
ELSTER-Kontos, das übermitteln soll. Die PIN steht im Schlüsselbund.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass

from . import eric as eric_mod
from . import ebilanz_xbrl, eric_ertrag, eric_xml, fristen, reports, steuern
from . import umsatzsteuer as ust
from . import zugang

PRODUKTNAME = "llm-buchhaltung"

# Das Testzertifikat des SDK (Beispiel/ericdemo-*). Mit ihm und dem Testmerker
# lässt sich der ganze Weg bis zum Annahmeserver probieren, bevor das eigene
# ELSTER-Zertifikat überhaupt im Spiel ist. Die PIN steht in der Liesmich des
# SDK und gehört zu keinem echten Konto.
TESTZERTIFIKAT = os.path.join("Beispiel", "ericdemo-python", "test-softidnr-pse.pfx")
TESTZERTIFIKAT_PIN = "123456"

ARTEN = ("ustva", "zm", "ebilanz", "eroeffnung", "kst", "gewst")
# Jahreserklärungen: ihr Stand steht in fristen.csv, nicht in voranmeldungen.csv
JAHRESARTEN = {"ebilanz": "ebilanz", "eroeffnung": "eroeffnungsbilanz",
               "kst": "koerperschaftsteuer", "gewst": "gewerbesteuer"}
# Welcher Zeitraum zu einer Jahresart gehört
ZEITRAUM = {"ebilanz": "WJ", "eroeffnung": "EB", "kst": "VZ", "gewst": "VZ"}


class VorgangFehler(Exception):
    pass


class NichtFestgehalten(VorgangFehler):
    """Gesendet und angenommen, aber nicht festgehalten: die Abgabe liegt beim
    Finanzamt, voranmeldungen.csv, fristen.csv oder die Datenbank wissen es
    nicht. Trägt den Vorgang (`vorgang`, `lauf`) mit Transferticket — wer das
    abfängt, darf nicht melden, es sei nichts gesendet."""


@dataclass
class Datensatz:
    art: str
    zeitraum: ust.Zeitraum
    xml: str
    datenart_version: str
    betrag_cent: int                 # Kz 83 der Voranmeldung bzw. Summe der ZM
    zeilen: list[tuple[str, str]]    # (Bezeichnung, Wert) für die Anzeige


class Geschaeftsjahr:
    """Der Zeitraum der E-Bilanz: ein Wirtschaftsjahr, auch ein Rumpfjahr.
    Dieselben Felder wie `umsatzsteuer.Zeitraum`, damit Ablage, Läufe und
    Oberfläche ihn wie ein Quartal behandeln."""
    quartal = monat = None
    code = "WJ"

    def __init__(self, jahr: int, beginn: str, ende: str, gj_id: int | None = None):
        self.jahr, self.gj_id = jahr, gj_id
        self.von = datetime.date.fromisoformat(str(beginn)[:10])
        self.bis = datetime.date.fromisoformat(str(ende)[:10])
        rumpf = (self.von.month, self.von.day) != (1, 1) or (self.bis.month, self.bis.day) != (12, 31)
        self.bezeichnung = f"Wirtschaftsjahr {jahr}" + (
            f" ({self.von:%d.%m.}–{self.bis:%d.%m.%Y})" if rumpf else "")

    @property
    def label(self) -> str:
        return f"{self.jahr}-WJ"


class Veranlagungszeitraum(Geschaeftsjahr):
    """Der Zeitraum der Körperschaft- und Gewerbesteuererklärung: das
    Kalenderjahr, in dem das Wirtschaftsjahr endet. Trägt dessen Grenzen mit,
    weil Anlage GK und KSt 1 F nach ihnen fragen."""
    code = "VZ"

    def __init__(self, jahr: int, beginn: str, ende: str, gj_id: int | None = None):
        super().__init__(jahr, beginn, ende, gj_id)
        self.bezeichnung = f"Veranlagungszeitraum {jahr}" + (
            f" (Wirtschaftsjahr {self.von:%d.%m.}–{self.bis:%d.%m.%Y})"
            if self.bezeichnung != f"Wirtschaftsjahr {jahr}" else "")

    @property
    def label(self) -> str:
        return f"{self.jahr}-VZ"


class Eroeffnungsstichtag(Geschaeftsjahr):
    """Der Zeitraum der Eröffnungsbilanz: der erste Tag des Wirtschaftsjahres.
    Die Grenzen des Wirtschaftsjahres bleiben, weil die E-Bilanz sie auch für
    die Eröffnungsbilanz verlangt."""
    code = "EB"

    def __init__(self, jahr: int, beginn: str, ende: str, gj_id: int | None = None):
        super().__init__(jahr, beginn, ende, gj_id)
        self.bezeichnung = f"Eröffnungsbilanz zum {self.von:%d.%m.%Y}"

    @property
    def label(self) -> str:
        return f"{self.jahr}-EB"


def geschaeftsjahr(con: sqlite3.Connection, mandant_id: int, jahr: int,
                   klasse=Geschaeftsjahr) -> Geschaeftsjahr:
    r = con.execute("SELECT id, beginn, ende FROM geschaeftsjahr WHERE mandant_id=? AND jahr=?",
                    (mandant_id, jahr)).fetchone()
    if not r:
        raise VorgangFehler(f"Kein Geschäftsjahr {jahr}.")
    return klasse(jahr, r["beginn"], r["ende"], r["id"])


def zeitraum(con: sqlite3.Connection, mandant_id: int, jahr: int, code: str):
    """Quartal, Monat, 'WJ' (Wirtschaftsjahr der E-Bilanz) oder 'VZ'
    (Veranlagungszeitraum der Ertragsteuererklärungen)."""
    if code == Geschaeftsjahr.code:
        return geschaeftsjahr(con, mandant_id, jahr)
    if code == Veranlagungszeitraum.code:
        return geschaeftsjahr(con, mandant_id, jahr, Veranlagungszeitraum)
    if code == Eroeffnungsstichtag.code:
        return geschaeftsjahr(con, mandant_id, jahr, Eroeffnungsstichtag)
    return ust.zeitraum_aus_code(jahr, code)


@dataclass
class Einstellung:
    bibliothek: str
    hersteller_id: str
    produkt: str = PRODUKTNAME
    produktversion: str = "1.0"

    @classmethod
    def laden(cls) -> "Einstellung":
        try:
            return cls(bibliothek=os.path.expanduser(zugang.hole("ERIC_PFAD")),
                       hersteller_id=zugang.hole("ERIC_HERSTELLER_ID"),
                       produkt=os.environ.get("ERIC_PRODUKTNAME", PRODUKTNAME),
                       produktversion=os.environ.get("ERIC_PRODUKTVERSION", "1.0"))
        except zugang.ZugangFehlt as e:
            raise VorgangFehler(f"{e}\nSiehe docs/eric.md.") from None

    def testzertifikat(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(self.bibliothek)), TESTZERTIFIKAT)


def stammdaten(mandant_dir: str) -> dict:
    with open(os.path.join(mandant_dir, "mandant.json"), encoding="utf-8") as fh:
        return json.load(fh)


def elster_block(stamm: dict) -> dict:
    block = stamm.get("elster") or {}
    if not block.get("bufa"):
        raise VorgangFehler('mandant.json: "elster": {"bufa": "<vierstellige '
                            'Bundesfinanzamtsnummer>"} fehlt (siehe docs/eric.md).')
    return block


def ablage(mandant_dir: str, jahr: int) -> str:
    """Wohin Datensatz, Antwort und Protokoll-PDF gehen. Unter mandanten/ und
    damit nie im Repository — die Dateien enthalten die Steuerdaten."""
    pfad = os.path.join(mandant_dir, str(jahr), "elster")
    os.makedirs(pfad, exist_ok=True)
    return pfad


def protokoll(mandant_dir: str) -> str:
    return os.path.join(mandant_dir, "elster", "protokoll")


def datensatz(con: sqlite3.Connection, mandant_id: int, mandant_dir: str,
              z: ust.Zeitraum, art: str, einstellung: Einstellung, eric: eric_mod.Eric,
              *, test: bool, berichtigt: bool = False,
              heute: datetime.date | None = None) -> Datensatz:
    stamm = stammdaten(mandant_dir)
    testmerker = eric_xml.TESTMERKER if test else None
    try:
        if art == "ustva":
            bufa = elster_block(stamm)["bufa"]
            e = ust.einstellungen(mandant_dir)
            k = ust.kennzahlen(con, mandant_id, z, e["satz"], e["ermaessigt"],
                               e["reverse_charge_abziehbar"])
            stnr = eric.steuernummer(stamm.get("steuernummer") or "", bufa)
            xml = eric_xml.ustva(k, stamm=stamm, steuernummer=stnr, bufa=bufa,
                                 hersteller_id=einstellung.hersteller_id,
                                 produkt=einstellung.produkt,
                                 produktversion=einstellung.produktversion,
                                 testmerker=testmerker, berichtigt=berichtigt, heute=heute)
            werte = eric_xml.ustva_werte(k)
            zeilen = [(f"Kz {kz[2:]}", v.replace(".", ",")) for kz, v in werte.items()]
            return Datensatz(art, z, xml, f"UStVA_{z.jahr}", k["steuern"]["83"], zeilen)
        if art == "zm":
            meldung = ust.zusammenfassende_meldung(con, mandant_id, z)
            xml = eric_xml.zm(meldung, jahr=z.jahr, quartal=z.quartal, monat=z.monat,
                              stamm=stamm, hersteller_id=einstellung.hersteller_id,
                              testmerker=testmerker, berichtigt=berichtigt)
            zeilen = [(idnr, f"{euro} EUR (S)") for idnr, euro in eric_xml.zm_zeilen(meldung)]
            summe = sum(euro for _, euro in eric_xml.zm_zeilen(meldung)) * 100
            return Datensatz(art, z, xml, "ZMDO", summe, zeilen)
        if art == "ebilanz":
            if berichtigt:
                raise VorgangFehler("Eine berichtigte E-Bilanz ist noch nicht vorgesehen.")
            bufa = elster_block(stamm)["bufa"]
            stnr = eric.steuernummer(stamm.get("steuernummer") or "", bufa)
            tax = ebilanz_xbrl.taxonomie()
            a = ebilanz_xbrl.abschluss(con, z.gj_id, mandant_dir, tax)
            ek_vorjahr = ebilanz_xbrl.eigenkapital_vorjahr(con, z.gj_id, mandant_dir, stamm, tax)
            gj = {"beginn": z.von.isoformat(), "ende": z.bis.isoformat()}
            xml = ebilanz_xbrl.datensatz(
                a, stamm=stamm, gj=gj, steuernummer=stnr, bufa=bufa,
                hersteller_id=einstellung.hersteller_id, produkt=einstellung.produkt,
                produktversion=einstellung.produktversion, testmerker=testmerker,
                eigenkapital_vorjahr=ek_vorjahr, heute=heute, tax=tax)
            zeilen = [("Bilanzsumme", betrag_text(a.bilanzsumme)),
                      ("Jahresergebnis", betrag_text(a.ergebnis)),
                      ("Eigenkapital", betrag_text(a.eigenkapital)),
                      ("Konten im Nachweis", str(len(a.konten)))]
            return Datensatz(art, z, xml, tax["datenart_version"], a.bilanzsumme, zeilen)
        if art == "eroeffnung":
            if berichtigt:
                raise VorgangFehler("Eine berichtigte Eröffnungsbilanz ist noch nicht vorgesehen.")
            bufa = elster_block(stamm)["bufa"]
            stnr = eric.steuernummer(stamm.get("steuernummer") or "", bufa)
            tax = ebilanz_xbrl.taxonomie()
            a = ebilanz_xbrl.eroeffnung(con, z.gj_id, mandant_dir, stamm, tax)
            gj = {"beginn": z.von.isoformat(), "ende": z.bis.isoformat()}
            xml = ebilanz_xbrl.datensatz(
                a, stamm=stamm, gj=gj, steuernummer=stnr, bufa=bufa,
                hersteller_id=einstellung.hersteller_id, produkt=einstellung.produkt,
                produktversion=einstellung.produktversion, testmerker=testmerker,
                heute=heute, tax=tax, eroeffnung=True)
            zeilen = [("Stichtag", f"{z.von:%d.%m.%Y}"),
                      ("Bilanzsumme", betrag_text(a.bilanzsumme)),
                      ("Eigenkapital", betrag_text(a.eigenkapital)),
                      ("Konten im Nachweis", str(len(a.konten)))]
            return Datensatz(art, z, xml, tax["datenart_version"], a.bilanzsumme, zeilen)
        if art in ("kst", "gewst"):
            # Keine Kennzahl „berichtigt“ in diesen Erklärungen: eine zweite
            # Übermittlung vor dem Bescheid ersetzt die erste.
            bufa = elster_block(stamm)["bufa"]
            stnr = eric.steuernummer(stamm.get("steuernummer") or "", bufa)
            jahr_dir = os.path.join(mandant_dir, str(z.jahr))
            spec = steuern.laden(jahr_dir)
            if not spec:
                raise VorgangFehler(f"{z.jahr}/steuern.json fehlt — ohne Überleitung keine "
                                    "Erklärung.")
            spec = steuern.pruefen(con, z.gj_id, spec, reports.ergebnis(con, z.gj_id))
            angaben = eric_ertrag.angaben_laden(jahr_dir)
            w = eric_ertrag.werte(spec)
            gemeinsam = dict(stamm=stamm, jahr=z.jahr, steuernummer=stnr, bufa=bufa,
                             hersteller_id=einstellung.hersteller_id,
                             produkt=einstellung.produkt,
                             produktversion=einstellung.produktversion, testmerker=testmerker)
            gj = {"beginn": z.von.isoformat(), "ende": z.bis.isoformat()}
            if art == "kst":
                xml = eric_ertrag.kst(spec, angaben, gj=gj, elster_stnr=eric.steuernummer,
                                      **gemeinsam)
                zeilen = [("Jahresergebnis", f"{w['ergebnis']} EUR"),
                          ("zu versteuerndes Einkommen", f"{w['zve']} EUR"),
                          ("Verlustvortrag", f"{w['verlust']} EUR")]
            else:
                xml = eric_ertrag.gewst(spec, angaben, **gemeinsam)
                zeilen = [("Gewinn aus Gewerbebetrieb", f"{w['zve']} EUR"),
                          ("Gewerbeverlust", f"{w['verlust']} EUR")]
            # KSt trägt die Unterfallart im Namen (Datenartversionmatrix.ods)
            return Datensatz(art, z, xml, f"{'KSt_30' if art == 'kst' else 'GewSt'}_{z.jahr}",
                             w["zve"] * 100, zeilen)
    except (eric_xml.DatenFehlen, ebilanz_xbrl.EBilanzFehler) as e:
        raise VorgangFehler(str(e)) from None
    raise VorgangFehler(f"Unbekannte Art {art!r} — {', '.join(ARTEN)}")


# ------------------------------------------------- voranmeldungen.csv

KOPF = "jahr;zeitraum;art;status;transferticket;abgegeben_am;zahlung_am;notiz"


def _csv_pfad(mandant_dir: str) -> str:
    return os.path.join(mandant_dir, "voranmeldungen.csv")


def stand(mandant_dir: str, jahr: int, code: str, art: str) -> dict | None:
    """Die Zeile aus voranmeldungen.csv, falls der Zeitraum schon dort steht.
    E-Bilanz und Ertragsteuererklärungen sind Jahrespflichten und stehen in
    fristen.csv."""
    if art in JAHRESARTEN:
        e = fristen.erledigungen(mandant_dir).get((jahr, JAHRESARTEN[art]))
        if not e:
            return None
        return {"status": "abgegeben" if e["status"] == "erledigt" else e["status"],
                "transferticket": e["nachweis"] or "", "abgegeben_am": e["erledigt_am"] or ""}
    pfad = _csv_pfad(mandant_dir)
    if not os.path.exists(pfad):
        return None
    with open(pfad, encoding="utf-8") as fh:
        for zeile in fh:
            teile = zeile.rstrip("\n").split(";")
            if len(teile) >= 4 and teile[:3] == [str(jahr), code, art]:
                return dict(zip(KOPF.split(";"), teile + [""] * 8))
    return None


def _zeile(jahr: int, code: str, art: str, ticket: str, datum: str, notiz: str) -> str:
    return ";".join([str(jahr), code, art, "abgegeben", ticket, datum, "",
                     notiz.replace(";", ",").replace("\n", " ")])


def eintragen(mandant_dir: str, jahr: int, code: str, art: str, ticket: str,
              datum: str, notiz: str) -> None:
    """Abgabe in voranmeldungen.csv festhalten — eine vorhandene Zeile des
    Zeitraums wird ersetzt (berichtigte Anmeldung), sonst angehängt.
    Kommentare und Reihenfolge der übrigen Zeilen bleiben, wie sie sind."""
    pfad = _csv_pfad(mandant_dir)
    neu = _zeile(jahr, code, art, ticket, datum, notiz)
    zeilen = []
    if os.path.exists(pfad):
        with open(pfad, encoding="utf-8") as fh:
            zeilen = fh.read().splitlines()
    if not any(z.startswith("jahr;") for z in zeilen):
        zeilen.append(KOPF)
    for i, z in enumerate(zeilen):
        if z.split(";")[:3] == [str(jahr), code, art]:
            zeilen[i] = neu
            break
    else:
        zeilen.append(neu)
    with open(pfad, "w", encoding="utf-8") as fh:
        fh.write("\n".join(zeilen) + "\n")


# ------------------------------------------------- Ausführen
#
# Jeder Lauf bekommt einen eigenen Ordner, der nie überschrieben wird:
#
#     mandanten/<kürzel>/<jahr>/elster/<zeitraum>-<art>/<zeitstempel>-<modus>/
#         datensatz.xml     was an ERiC übergeben wurde, Byte für Byte
#         vorpruefung.xml   Prüfantwort mit Hinweisen (vor test und senden)
#         antwort.xml       Rückgabe von ERiC zum Lauf selbst
#         server.xml        Antwort des Annahmeservers (nur beim Senden)
#         protokoll.pdf     Übertragungsprotokoll bzw. Vorschau, von ERiC gedruckt
#         lauf.json         wann, womit, Prüfsumme, Ergebnis
#
# Eine berichtigte Anmeldung legt einen neuen Ordner an, die erste Abgabe
# bleibt daneben stehen.

MODI = ("pruefen", "test", "senden")
LAUF = "lauf.json"


@dataclass
class Lauf:
    datensatz: Datensatz
    ergebnis: eric_mod.Ergebnis | None
    ordner: str
    dateien: dict[str, str]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ordner(mandant_dir: str, ds: Datensatz, modus: str, jetzt: datetime.datetime) -> str:
    if modus not in MODI:
        raise VorgangFehler(f"Unbekannter Modus {modus!r}")
    basis = os.path.join(ablage(mandant_dir, ds.zeitraum.jahr), f"{ds.zeitraum.label}-{ds.art}")
    name = f"{jetzt:%Y%m%d-%H%M%S}-{modus}"
    pfad, n = os.path.join(basis, name), 1
    while os.path.exists(pfad):
        n += 1
        pfad = os.path.join(basis, f"{name}-{n}")
    os.makedirs(pfad)
    return pfad


def _kurz(e: eric_mod.Ergebnis | None) -> dict | None:
    if e is None:
        return None
    return {"code": e.code, "text": e.text, "ok": e.ok, "gesendet": e.gesendet,
            "transferticket": e.transferticket}


def vorpruefen(eric: eric_mod.Eric, ds: Datensatz) -> eric_mod.Ergebnis:
    """Vor jedem Senden einmal nur prüfen: Hinweise gibt ERiC zusammen mit SENDE
    nicht heraus, nur Fehler. Abgelegt wird das Ergebnis mit dem Lauf."""
    return eric.bearbeite(ds.xml, ds.datenart_version)


def ausfuehren(eric: eric_mod.Eric, ds: Datensatz, modus: str, mandant_dir: str, *,
               zertifikat: str | None = None, pin: str | None = None,
               vorpruefung: eric_mod.Ergebnis | None = None, berichtigt: bool = False,
               testzertifikat: bool = False, abbrechen: str | None = None,
               jetzt: datetime.datetime | None = None) -> Lauf:
    """Prüfen, Testsenden oder Senden — und alles Erzeugte ablegen.

    Mit `abbrechen` wird nichts mehr an ERiC gegeben, nur festgehalten, warum:
    so steht auch ein Versuch, den die Vorprüfung gestoppt hat, in der Liste.
    """
    jetzt = jetzt or datetime.datetime.now().replace(microsecond=0)
    ordner = _ordner(mandant_dir, ds, modus, jetzt)
    dateien = {"datensatz": os.path.join(ordner, "datensatz.xml")}
    with open(dateien["datensatz"], "w", encoding="utf-8", newline="") as fh:
        fh.write(ds.xml)
    erg = None
    if not abbrechen:
        pdf = os.path.join(ordner, "protokoll.pdf")
        erg = eric.bearbeite(ds.xml, ds.datenart_version, senden=modus != "pruefen",
                             pdf=pdf, zertifikat=zertifikat, pin=pin)
        if erg.pdf:
            dateien["protokoll"] = pdf
    for name, inhalt in (("vorpruefung", vorpruefung.antwort if vorpruefung else ""),
                         ("antwort", erg.antwort if erg else ""),
                         ("server", erg.server if erg else "")):
        # Eine leere Prüfantwort ist eine Aussage, ein leerer Server beim Prüfen nicht
        if (name == "vorpruefung" and vorpruefung is None) or (name != "vorpruefung"
                                                              and not inhalt.strip()):
            continue
        dateien[name] = os.path.join(ordner, f"{name}.xml")
        with open(dateien[name], "w", encoding="utf-8", newline="") as fh:
            fh.write(inhalt)
    try:
        version = eric.version()
    except eric_mod.EricFehler:
        version = None
    lauf = {
        "format": 1, "modus": modus, "art": ds.art, "jahr": ds.zeitraum.jahr,
        "zeitraum": ds.zeitraum.code, "label": ds.zeitraum.label,
        "bezeichnung": ds.zeitraum.bezeichnung, "zeit": jetzt.isoformat(),
        "eric": version, "datenart_version": ds.datenart_version, "berichtigt": berichtigt,
        "zertifikat": os.path.basename(zertifikat) if zertifikat else None,
        "testzertifikat": testzertifikat,
        "datensatz": {"datei": "datensatz.xml", "sha256": sha256(ds.xml),
                      "bytes": len(ds.xml.encode("utf-8"))},
        "betrag_cent": ds.betrag_cent, "zeilen": [list(z) for z in ds.zeilen],
        "vorpruefung": _kurz(vorpruefung), "ergebnis": _kurz(erg), "abgebrochen": abbrechen,
    }
    dateien["lauf"] = os.path.join(ordner, LAUF)
    with open(dateien["lauf"], "w", encoding="utf-8") as fh:
        json.dump(lauf, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return Lauf(ds, erg, ordner, dateien)


# ------------------------------------------------- Ein ganzer Vorgang
#
# Kommandozeile und Weboberfläche gehen denselben Weg — mit denselben
# Sperren. Unterschiedlich ist nur, wie die Freigabe zur Abgabe eingeholt wird:
# im Terminal per Eingabe, im Browser per Häkchen und eingetipptem Betrag. Das
# erledigt `freigabe`, aufgerufen nach der Vorprüfung und vor dem Senden.

TITEL = {"ustva": "Umsatzsteuer-Voranmeldung", "zm": "Zusammenfassende Meldung",
         "ebilanz": "E-Bilanz", "eroeffnung": "E-Bilanz (Eröffnungsbilanz)",
         "kst": "Körperschaftsteuererklärung",
         "gewst": "Gewerbesteuererklärung"}
# Was zur Freigabe eingetippt wird
FREIGABE = {"ebilanz": "Bilanzsumme", "eroeffnung": "Bilanzsumme",
            "kst": "zu versteuerndes Einkommen",
            "gewst": "Gewinn aus Gewerbebetrieb"}


@dataclass
class Vorgang:
    datensatz: Datensatz
    vorpruefung: eric_mod.Ergebnis | None
    lauf: Lauf
    festgehalten: bool = False
    # Gesendet, aber das Festhalten scheiterte: was von Hand nachzutragen ist
    festhalten_fehler: str | None = None


def betrag_text(cent: int) -> str:
    """Kz 83 bzw. ZM-Summe so, wie sie zur Freigabe eingetippt wird: '1500,00'."""
    return eric_xml.betrag(cent).replace(".", ",")


def letzte_pruefung(mandant_dir: str, z: ust.Zeitraum, art: str) -> dict | None:
    from . import eric_ansicht
    return next((l for l in eric_ansicht.laeufe(mandant_dir) if l["modus"] == "pruefen"
                 and (l["jahr"], l["zeitraum"], l["art"]) == (z.jahr, z.code, art)), None)


def fruehere_abgabe(mandant_dir: str, z: ust.Zeitraum, art: str) -> dict | None:
    """Der neueste angenommene Versand desselben Zeitraums und derselben Art,
    gelesen aus der Ablage der Läufe. Er zählt auch dann, wenn ihn
    voranmeldungen.csv oder fristen.csv nicht kennen — etwa weil das
    Festhalten danach gescheitert ist."""
    from . import eric_ansicht
    return next((l for l in eric_ansicht.laeufe(mandant_dir) if l["modus"] == "senden"
                 and not l.get("abgebrochen") and (l.get("ergebnis") or {}).get("gesendet")
                 and (l["jahr"], l["zeitraum"], l["art"]) == (z.jahr, z.code, art)), None)


def _nachtragen(mandant_dir: str, kuerzel: str, z: ust.Zeitraum, art: str, lauf: Lauf,
                ticket: str, heute: str, notiz: str, erledigt: list[str],
                fehler: Exception) -> str:
    """Was zu sagen ist, wenn gesendet, aber nicht festgehalten wurde."""
    if art in JAHRESARTEN:
        datei = os.path.join(mandant_dir, "fristen.csv")
        zeile = ";".join([str(z.jahr), JAHRESARTEN[art], "erledigt", heute, ticket,
                          notiz.replace(";", ",").replace("\n", " "), ""])
        danach = ""
    else:
        datei = _csv_pfad(mandant_dir)
        zeile = _zeile(z.jahr, z.code, art, ticket, heute, notiz)
        danach = f"\ndanach `bh ustva {kuerzel} {z.jahr}` — das übernimmt sie in die Datenbank."
    schon = (f"\nSchon festgehalten: {', '.join(erledigt)}." if erledigt else "")
    return (f"{TITEL[art]} {z.bezeichnung} ist ÜBERMITTELT und angenommen — "
            f"{'Transferticket ' + ticket if ticket else 'ohne Transferticket in der Antwort'}"
            f" (Lauf in {lauf.ordner}). Nicht erneut senden.\n"
            f"Festhalten ist gescheitert: {type(fehler).__name__}: {fehler}.{schon}\n"
            f"Von Hand nachtragen: in {datei} die Zeile\n    {zeile}{danach}")


def durchfuehren(con: sqlite3.Connection, mandant_id: int, kuerzel: str, mandant_dir: str,
                 z: ust.Zeitraum, art: str, modus: str, einstellung: Einstellung, *,
                 berichtigt: bool = False, testzertifikat: bool = False,
                 freigabe=None,
                 eric_klasse=eric_mod.Eric) -> Vorgang:
    """Prüfen, testen oder abgeben — von der Buchführung bis zum Festhalten.

    `freigabe(datensatz, vorpruefung, letzte_pruefung)` ist für `senden`
    Pflicht; sie wirft VorgangFehler, wenn nicht freigegeben ist. Ohne sie wird
    nicht gesendet. Wirft VorgangFehler, wenn etwas fehlt oder die Vorprüfung
    Fehler findet; der abgebrochene Lauf liegt dann trotzdem in der Ablage.

    Scheitert nach einer angenommenen Abgabe das Festhalten (voranmeldungen.csv,
    fristen.csv, Datenbank — etwa „database is locked“ durch ein zweites `bh`),
    geht die Abgabe nicht verloren: NichtFestgehalten trägt Vorgang, Lauf und
    Transferticket und sagt, was von Hand nachzutragen ist. Ein zweiter Versand
    desselben Zeitraums scheitert dann an `fruehere_abgabe`.
    """
    if modus not in MODI or art not in ARTEN:
        raise VorgangFehler(f"Unbekannt: {modus} {art}")
    if not eric_mod.hinweise_bestaetigt(einstellung.bibliothek):
        raise VorgangFehler("Zuerst einmal bh eric einrichten (Datenschutzhinweise nach der "
                            "ERiC-Lizenz).")
    if modus == "senden":
        if freigabe is None:
            raise VorgangFehler("Ohne Freigabe wird nicht abgegeben.")
        vorher = stand(mandant_dir, z.jahr, z.code, art)
        if vorher and vorher["status"] == "abgegeben" and not berichtigt:
            raise VorgangFehler(f"{TITEL[art]} {z.bezeichnung} ist schon abgegeben (Ticket "
                                f"{vorher['transferticket']}). Eine Berichtigung nur als "
                                f"berichtigte Anmeldung.")
        # Der festgehaltene Stand allein genügt nicht: ist das Festhalten nach
        # einer Abgabe gescheitert, steht sie nur in der Ablage der Läufe.
        frueher = None if berichtigt else fruehere_abgabe(mandant_dir, z, art)
        if frueher:
            ticket = (frueher.get("ergebnis") or {}).get("transferticket") or "ohne Ticket"
            raise VorgangFehler(
                f"{TITEL[art]} {z.bezeichnung} ist schon übermittelt: Lauf {frueher['id']} vom "
                f"{(frueher.get('zeit') or '')[:16].replace('T', ' ')}, Transferticket {ticket} "
                f"— aber nicht festgehalten. Erst nachtragen (voranmeldungen.csv bzw. "
                f"fristen.csv), nicht erneut senden. Eine Berichtigung nur als berichtigte "
                f"Anmeldung.")
    zert = pin = None
    if modus != "pruefen":
        if testzertifikat and modus == "test":
            zert, pin = einstellung.testzertifikat(), TESTZERTIFIKAT_PIN
        else:
            zert = (stammdaten(mandant_dir).get("elster") or {}).get("zertifikat")
            if not zert:
                raise VorgangFehler('mandant.json: "elster": {"zertifikat": "<pfad.pfx>"} fehlt.')
            try:
                pin = zugang.eric_pin(kuerzel)
            except zugang.ZugangFehlt as f:
                raise VorgangFehler(str(f)) from None
    ablegen = dict(berichtigt=berichtigt, testzertifikat=bool(testzertifikat and modus == "test"))
    with eric_klasse(einstellung.bibliothek, protokoll(mandant_dir)) as er:
        try:
            ds = datensatz(con, mandant_id, mandant_dir, z, art, einstellung, er,
                           test=modus == "test", berichtigt=berichtigt)
        except eric_mod.EricFehler as f:
            raise VorgangFehler(str(f)) from None
        vp = None
        if modus != "pruefen":
            vp = vorpruefen(er, ds)
            if not vp.ok:
                lauf = ausfuehren(er, ds, modus, mandant_dir, vorpruefung=vp,
                                  abbrechen="Fehler in der Plausibilitätsprüfung", **ablegen)
                fehler = VorgangFehler("Nichts gesendet: der Datensatz hat Fehler.")
                fehler.lauf = lauf
                raise fehler
        if modus == "senden":
            freigabe(ds, vp, letzte_pruefung(mandant_dir, z, art))
        lauf = ausfuehren(er, ds, modus, mandant_dir, zertifikat=zert, pin=pin,
                          vorpruefung=vp, **ablegen)
    v = Vorgang(ds, vp, lauf)
    r = lauf.ergebnis
    if modus == "senden" and r and r.gesendet:
        from . import eric_ansicht
        heute = datetime.date.today().isoformat()
        ticket = r.transferticket or ""
        notiz = (f"Per ERiC übermittelt ({TITEL[art]}, "
                 f"{FREIGABE.get(art, 'Betrag')} {betrag_text(ds.betrag_cent)} EUR)"
                 + (", berichtigte Anmeldung" if berichtigt and art not in JAHRESARTEN
                    else ", ersetzt die frühere Übermittlung" if berichtigt else "") + ".")
        erledigt = []
        try:
            if art in JAHRESARTEN:
                fristen.festhalten(mandant_dir, z.jahr, JAHRESARTEN[art], "erledigt", heute,
                                   ticket, notiz)
                erledigt.append("fristen.csv")
            else:
                eintragen(mandant_dir, z.jahr, z.code, art, ticket, heute, notiz)
                erledigt.append("voranmeldungen.csv")
                werte = eric_ansicht.werte_aus_xml(ds.xml, art)
                if art == "zm":
                    werte = {idnr: b for idnr, b, _ in werte}
                ust.festhalten(con, mandant_id, z, "abgegeben", transferticket=r.transferticket,
                               abgegeben_am=heute, kennzahlen_werte=werte, notiz=notiz, art=art)
                con.commit()
        except Exception as f:   # alles: eine Abgabe darf hinter keinem Fehler verschwinden
            try:
                con.rollback()   # keine halbe Eintragung, die ein späteres commit mitnimmt
            except sqlite3.Error:
                pass
            v.festhalten_fehler = _nachtragen(mandant_dir, kuerzel, z, art, lauf, ticket, heute,
                                              notiz, erledigt, f)
            fehler = NichtFestgehalten(v.festhalten_fehler)
            fehler.lauf, fehler.vorgang = lauf, v
            raise fehler from f
        v.festgehalten = True
    return v


# ------------------------------------------------- Vorschläge zur Freigabe
#
# Was jemand anderes vorbereitet hat — ein Assistent, ein Skript, die
# Steuerberaterin —, liegt als geprüfter Lauf in der Ablage und wartet auf den
# Menschen, der abgibt. `vorschlag.json` neben dem Lauf sagt, warum es diesen
# Datensatz gibt und was er gegenüber dem Stand beim Finanzamt ändert. Ohne
# diese Datei ist eine Prüfung nur eine Prüfung unter vielen im Verlauf.
#
# Ob der Vorschlag noch offen, schon abgegeben oder überholt ist, steht nicht in
# der Datei: das ergibt sich aus den Läufen danach (eric_ansicht.vorschlaege).
# Festgehalten wird nur, was sich nicht ableiten lässt — das Verwerfen.

VORSCHLAG = "vorschlag.json"


def _lauf_lesen(ordner: str) -> dict:
    pfad = os.path.join(ordner, LAUF)
    if not os.path.exists(pfad):
        raise VorgangFehler(f"Kein Lauf in {ordner}")
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)


def _schreiben(pfad: str, d: dict) -> None:
    with open(pfad, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def aenderung(text: str) -> dict:
    """'Anlage GK Zeile 11|-1.235|-1.535' -> {was, bisher, neu}."""
    teile = [t.strip() for t in text.split("|")]
    if len(teile) != 3 or not teile[0]:
        raise VorgangFehler(f"Änderung als 'was|bisher|neu' angeben, nicht {text!r}")
    return dict(zip(("was", "bisher", "neu"), teile))


def vorschlagen(ordner: str, grund: str, aenderungen: list[dict] = (), *, von: str = "Claude",
                pruefpunkte: list[str] = (),
                jetzt: datetime.datetime | None = None) -> dict:
    """Eine fehlerfreie Prüfung zur Freigabe vorlegen."""
    lauf = _lauf_lesen(ordner)
    if lauf["modus"] != "pruefen" or not (lauf.get("ergebnis") or {}).get("ok"):
        raise VorgangFehler("Vorlegen lässt sich nur eine fehlerfreie Prüfung.")
    if not grund.strip():
        raise VorgangFehler("Ohne Grund kein Vorschlag: wer freigibt, soll wissen, warum.")
    jetzt = jetzt or datetime.datetime.now().replace(microsecond=0)
    v = {"format": 1, "von": von, "zeit": jetzt.isoformat(), "grund": grund.strip(),
         "aenderungen": list(aenderungen),
         "pruefpunkte": [p.strip() for p in pruefpunkte if p.strip()], "verworfen": None}
    _schreiben(os.path.join(ordner, VORSCHLAG), v)
    return v


def verwerfen(ordner: str, grund: str = "", *, jetzt: datetime.datetime | None = None) -> dict:
    pfad = os.path.join(ordner, VORSCHLAG)
    if not os.path.exists(pfad):
        raise VorgangFehler("Zu diesem Lauf gibt es keinen Vorschlag.")
    with open(pfad, encoding="utf-8") as fh:
        v = json.load(fh)
    jetzt = jetzt or datetime.datetime.now().replace(microsecond=0)
    v["verworfen"] = {"zeit": jetzt.isoformat(), "grund": grund.strip()}
    _schreiben(pfad, v)
    return v
