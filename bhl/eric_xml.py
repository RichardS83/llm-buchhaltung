# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""ELSTER-XML für die Umsatzsteuer-Voranmeldung und die Zusammenfassende Meldung.

Dieses Modul rechnet nichts. Die Kennzahlen kommen aus `bhl/umsatzsteuer.py`,
dieselben, die `bh ustva` als Eingabeblatt druckt — hier bekommen sie nur die
Form, die ERiC erwartet. Es braucht die ERiC-Bibliothek nicht und lässt sich
deshalb ohne sie testen.

Was der Datensatz nicht aus der Buchführung nimmt, kommt von außen:

* die **Hersteller-ID** — sie gehört dem, der sie bei ELSTER beantragt hat,
  und steht deshalb weder im Code noch in einer Datei des Repositorys;
* die **Steuernummer im ELSTER-Format** (13 Stellen) — sie bildet ERiC selbst
  aus der Steuernummer des Bescheids und der Bundesfinanzamtsnummer
  (`Eric.steuernummer`), weil die Regeln je Bundesland verschieden sind.

Die Reihenfolge der Elemente ist im Schema eine `xs:sequence`: ERiC lehnt einen
Datensatz ab, in dem Kz 83 vor Kz 81 steht. `USTVA_FELDER` folgt deshalb dem
Schema `UStVA-<jahr>.xsd`, nicht der Nummernfolge.
"""

from __future__ import annotations

import datetime
import re
import xml.etree.ElementTree as ET

NS = "http://www.elster.de/elsterxml/schema/v11"

# Ein Datensatz mit Testmerker wird vom Annahmeserver angenommen, geprüft und
# gelöscht, ohne dass ein Finanzamt ihn sieht (Entwicklerhandbuch, Kap. 6.5.5).
TESTMERKER = "700000004"

# Schema UStVA-2026.xsd, Umsatzsteuervoranmeldung_*_CType. Nur die Kennzahlen,
# die die Buchführung liefert, und die Ankreuzfelder, die ein Mensch setzt.
USTVA_FELDER = ("Kz09", "Kz10", "Kz21", "Kz22", "Kz26", "Kz29", "Kz45", "Kz46",
                "Kz47", "Kz66", "Kz67", "Kz81", "Kz83", "Kz84", "Kz85", "Kz86")

# Für die ZM gilt: nur sonstige Leistungen (Kz 21 der Voranmeldung). Lieferungen
# und Dreiecksgeschäfte kennt die Buchführung nicht — sie hat dafür keine
# Kennzahl.
ZM_UMSATZART_SONSTIGE_LEISTUNG = "S"


class DatenFehlen(ValueError):
    """Eine Angabe, ohne die kein gültiger Datensatz entsteht."""


def betrag(cent: int) -> str:
    """Steuerbetrag mit zwei Nachkommastellen, ohne Gleitkomma: 10020 -> '100.20'."""
    vorzeichen = "-" if cent < 0 else ""
    cent = abs(cent)
    return f"{vorzeichen}{cent // 100}.{cent % 100:02d}"


def euro_abgeschnitten(cent: int) -> int:
    """Volle Euro, Cent abgeschnitten — wie die Bemessungsgrundlagen der UStVA."""
    return cent // 100 if cent >= 0 else -((-cent) // 100)


def strasse_hausnummer(zeile: str) -> tuple[str, str | None, str | None]:
    """'Musterstraße 10' -> ('Musterstraße', '10', None); '10a' -> Zusatz 'a'.

    mandant.json führt die Anschrift als eine Zeile; ELSTER will Straße,
    Hausnummer und Zusatz getrennt.
    """
    m = re.match(r"^(.*?)\s+(\d+)\s*([A-Za-z][\w\-/ ]*)?$", (zeile or "").strip())
    if not m:
        return (zeile or "").strip(), None, None
    return m.group(1).strip(), m.group(2), (m.group(3) or "").strip() or None


def _sub(eltern: ET.Element, tag: str, text: str | None = None, **attrib) -> ET.Element:
    e = ET.SubElement(eltern, tag, attrib)
    if text is not None:
        e.text = str(text)
    return e


def _pflicht(stamm: dict, *felder: str) -> None:
    fehlt = [f for f in felder if not stamm.get(f)]
    if fehlt:
        raise DatenFehlen(f"mandant.json: es fehlt {', '.join(fehlt)}")


def _hersteller_id(hersteller_id: str) -> str:
    if not re.fullmatch(r"\d{5}", hersteller_id or ""):
        raise DatenFehlen("Die Hersteller-ID hat fünf Ziffern (ERIC_HERSTELLER_ID).")
    return hersteller_id


def _rahmen(verfahren: str, datenart: str, hersteller_id: str, datenlieferant: str,
            testmerker: str | None) -> tuple[ET.Element, ET.Element]:
    """Elster-Wurzel mit TransferHeader; zurück kommt auch der Nutzdatenblock."""
    wurzel = ET.Element(f"{{{NS}}}Elster")
    th = _sub(wurzel, f"{{{NS}}}TransferHeader", version="11")
    _sub(th, f"{{{NS}}}Verfahren", verfahren)
    _sub(th, f"{{{NS}}}DatenArt", datenart)
    _sub(th, f"{{{NS}}}Vorgang", "send-Auth")
    if testmerker:
        _sub(th, f"{{{NS}}}Testmerker", testmerker)
    _sub(th, f"{{{NS}}}HerstellerID", _hersteller_id(hersteller_id))
    _sub(th, f"{{{NS}}}DatenLieferant", datenlieferant[:256])
    datei = _sub(th, f"{{{NS}}}Datei")
    _sub(datei, f"{{{NS}}}Verschluesselung", "CMSEncryptedData")
    _sub(datei, f"{{{NS}}}Kompression", "GZIP")
    _sub(datei, f"{{{NS}}}TransportSchluessel")
    block = _sub(_sub(wurzel, f"{{{NS}}}DatenTeil"), f"{{{NS}}}Nutzdatenblock")
    return wurzel, block


def _text(wurzel: ET.Element) -> str:
    """Serialisieren wie die Beispiele der Finanzverwaltung: jeder Namensraum
    als Vorgabe-Namensraum dort, wo er beginnt, ohne Präfixe.

    ElementTree kann einen zweiten Vorgabe-Namensraum nicht ausdrücken und
    würde `ns0:` vergeben. Für einen XML-Parser ist das gleichwertig — aber
    der Datensatz soll sich neben das amtliche Beispiel legen lassen.
    """
    def schreibe(e: ET.Element, ns_aussen: str | None, tiefe: int) -> list[str]:
        ns, _, name = e.tag[1:].partition("}")
        attrib = "".join(f' {k}="{_esc(v)}"' for k, v in e.attrib.items())
        # ERiC liest die Nutzdaten als eigenes Dokument: ihr Wurzelelement
        # braucht die Namensraumangabe auch dann, wenn sie sich nicht ändert.
        if ns != ns_aussen or e in nutzdaten_wurzeln:
            attrib = f' xmlns="{ns}"' + attrib
        einzug = "  " * tiefe
        if len(e):
            zeilen = [f"{einzug}<{name}{attrib}>"]
            for kind in e:
                zeilen += schreibe(kind, ns, tiefe + 1)
            return zeilen + [f"{einzug}</{name}>"]
        if e.text is None:
            return [f"{einzug}<{name}{attrib}/>"]
        return [f"{einzug}<{name}{attrib}>{_esc(e.text)}</{name}>"]

    nutzdaten_wurzeln = {kind for e in wurzel.iter() if e.tag.endswith("}Nutzdaten") for kind in e}
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(schreibe(wurzel, None, 0)) + "\n"


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


# ------------------------------------------------------- Voranmeldung

def ustva_werte(k: dict) -> dict[str, str]:
    """Kennzahlen aus `umsatzsteuer.kennzahlen()` -> {'Kz81': '1500', ...}.

    Leere Kennzahlen bleiben weg; Kz 83 steht immer da, auch bei null — das
    ist die Aussage der Anmeldung.
    """
    w: dict[str, str] = {}
    for kz, euro in k["basen"].items():
        if euro:
            w[f"Kz{kz}"] = str(euro)
    for kz, cent in k["steuern"].items():
        if cent or kz == "83":
            w[f"Kz{kz}"] = betrag(cent)
    return w


def ustva(k: dict, *, stamm: dict, steuernummer: str, bufa: str, hersteller_id: str,
          produkt: str, produktversion: str, testmerker: str | None = None,
          berichtigt: bool = False, heute: datetime.date | None = None) -> str:
    """Datensatz der Datenart UStVA_<jahr>.

    `k` ist das Ergebnis von `umsatzsteuer.kennzahlen()`, `stamm` die
    mandant.json. `steuernummer` ist schon im ELSTER-Format.
    """
    _pflicht(stamm, "name", "strasse", "plz", "ort")
    if not re.fullmatch(r"\d{13}", steuernummer or ""):
        raise DatenFehlen("Steuernummer im ELSTER-Format hat 13 Ziffern.")
    if not re.fullmatch(r"\d{4}", bufa or ""):
        raise DatenFehlen("Bundesfinanzamtsnummer hat vier Ziffern (mandant.json, elster.bufa).")
    jahr = int(k["von"][:4])
    heute = heute or datetime.date.today()

    wurzel, block = _rahmen("ElsterAnmeldung", "UStVA", hersteller_id, stamm["name"], testmerker)
    ndh = _sub(block, f"{{{NS}}}NutzdatenHeader", version="11")
    _sub(ndh, f"{{{NS}}}NutzdatenTicket", "1")
    _sub(ndh, f"{{{NS}}}Empfaenger", bufa, id="F")
    hersteller = _sub(ndh, f"{{{NS}}}Hersteller")
    _sub(hersteller, f"{{{NS}}}ProduktName", produkt)
    _sub(hersteller, f"{{{NS}}}ProduktVersion", produktversion)

    ns = f"http://finkonsens.de/elster/elsteranmeldung/ustva/v{jahr}"
    nutz = _sub(_sub(block, f"{{{NS}}}Nutzdaten"), f"{{{ns}}}Anmeldungssteuern",
                version=str(jahr))
    _sub(nutz, f"{{{ns}}}Erstellungsdatum", heute.strftime("%Y%m%d"))
    dl = _sub(nutz, f"{{{ns}}}DatenLieferant")
    _sub(dl, f"{{{ns}}}Name", stamm["name"][:45])
    _sub(dl, f"{{{ns}}}Strasse", stamm["strasse"][:30])
    _sub(dl, f"{{{ns}}}PLZ", stamm["plz"])
    _sub(dl, f"{{{ns}}}Ort", stamm["ort"][:34])
    for feld, tag in (("telefon", "Telefon"), ("email", "Email")):
        if stamm.get(feld):
            _sub(dl, f"{{{ns}}}{tag}", stamm[feld])

    fall = _sub(nutz, f"{{{ns}}}Steuerfall")
    u = _sub(fall, f"{{{ns}}}Unternehmer")
    strasse, hausnr, zusatz = strasse_hausnummer(stamm["strasse"])
    _sub(u, f"{{{ns}}}Bezeichnung", stamm["name"][:45])
    _sub(u, f"{{{ns}}}Str", strasse[:25])
    if hausnr:
        _sub(u, f"{{{ns}}}Hausnummer", hausnr)
    if zusatz:
        _sub(u, f"{{{ns}}}HNrZusatz", zusatz)
    _sub(u, f"{{{ns}}}Ort", stamm["ort"][:25])
    _sub(u, f"{{{ns}}}PLZ", stamm["plz"])
    for feld, tag in (("telefon", "Telefon"), ("email", "Email")):
        if stamm.get(feld):
            _sub(u, f"{{{ns}}}{tag}", stamm[feld])

    va = _sub(fall, f"{{{ns}}}Umsatzsteuervoranmeldung")
    _sub(va, f"{{{ns}}}Jahr", str(jahr))
    _sub(va, f"{{{ns}}}Zeitraum", k["code"])
    _sub(va, f"{{{ns}}}Steuernummer", steuernummer)
    if stamm.get("widnr"):
        # Mitgeteilt wird sie mit Unterscheidungsmerkmal (DE…-00001), das
        # Schema der UStVA nimmt nur die elf Stellen davor.
        widnr = stamm["widnr"].replace(" ", "").split("-")[0]
        if not re.fullmatch(r"DE\d{9}", widnr):
            raise DatenFehlen("Die W-IdNr. ist DE und neun Ziffern (mandant.json, widnr).")
        _sub(va, f"{{{ns}}}WIdNr", widnr)
    werte = ustva_werte(k)
    werte["Kz09"] = _hersteller_id(hersteller_id)
    if berichtigt:
        werte["Kz10"] = "1"
    for kz in USTVA_FELDER:
        if kz in werte:
            _sub(va, f"{{{ns}}}{kz}", werte[kz])
    unbekannt = set(werte) - set(USTVA_FELDER)
    if unbekannt:
        raise DatenFehlen(f"Kennzahl ohne Platz im Schema: {', '.join(sorted(unbekannt))}")
    return _text(wurzel)


# ------------------------------------------ Zusammenfassende Meldung

def zm_quart(quartal: int | None = None, monat: int | None = None) -> int:
    """Meldezeitraum im Schema zm_000005: 1-4 Quartal, 21-32 Monat."""
    if quartal:
        return quartal
    if monat:
        return 20 + monat
    raise DatenFehlen("Quartal oder Monat angeben")


def zm_zeilen(meldung: dict) -> list[tuple[str, int]]:
    """(USt-IdNr., volle Euro) je Kunde. Ein Kunde ohne USt-IdNr. hält die
    Meldung an: ihn zu melden geht nicht, ihn wegzulassen verfälscht sie."""
    zeilen = []
    for kunde in meldung["kunden"]:
        idnr = kunde["ust_idnr"].replace(" ", "")
        if not re.fullmatch(r"[A-Z]{2}[A-Za-z0-9+*]{1,12}", idnr):
            raise DatenFehlen(f"Kunde ohne gültige USt-IdNr. ({kunde['ust_idnr']}, "
                              f"{betrag(kunde['betrag'])} EUR) — erst die Erlösbuchung ergänzen.")
        if idnr.startswith("DE"):
            raise DatenFehlen(f"{idnr} ist eine deutsche USt-IdNr. — gehört nicht in die ZM.")
        zeilen.append((idnr, euro_abgeschnitten(kunde["betrag"])))
    return zeilen


def zm(meldung: dict, *, jahr: int, quartal: int | None = None, monat: int | None = None,
       stamm: dict, hersteller_id: str, testmerker: str | None = None,
       berichtigt: bool = False) -> str:
    """Datensatz der Datenart ZMDO (Schema zm_000005).

    `meldung` ist das Ergebnis von `umsatzsteuer.zusammenfassende_meldung()`.
    Empfänger ist das Bundeszentralamt für Steuern (`BF`), nicht das Finanzamt.
    """
    _pflicht(stamm, "name", "strasse", "ort", "ust_idnr")
    if not re.fullmatch(r"DE\d{9}", stamm["ust_idnr"]):
        raise DatenFehlen("Die eigene USt-IdNr. ist DE und neun Ziffern (mandant.json, ust_idnr).")
    zeilen = zm_zeilen(meldung)
    if not zeilen:
        raise DatenFehlen("Keine innergemeinschaftlichen Leistungen im Zeitraum — "
                          "keine Meldung abzugeben.")

    wurzel, block = _rahmen("ElsterExtern", "ZMDO", hersteller_id, stamm["name"], testmerker)
    ndh = _sub(block, f"{{{NS}}}NutzdatenHeader", version="11")
    _sub(ndh, f"{{{NS}}}NutzdatenTicket", "1")
    _sub(ndh, f"{{{NS}}}Empfaenger", "BF", id="L")
    z = _sub(_sub(block, f"{{{NS}}}Nutzdaten"), f"{{{NS}}}zm", version="000005")
    u = _sub(z, f"{{{NS}}}unternehmer")
    _sub(u, f"{{{NS}}}knri", stamm["ust_idnr"])
    # Bei einer Übermittlung über ERiC schreibt das Schema "N" und "1111111"
    # vor; die Teilnehmernummer des BZSt braucht es dafür nicht.
    zul = _sub(u, f"{{{NS}}}zulassNr")
    _sub(zul, f"{{{NS}}}zulnr1", "N")
    _sub(zul, f"{{{NS}}}zulnr2", "1111111")
    a = _sub(u, f"{{{NS}}}anschrift")
    strasse, hausnr, zusatz = strasse_hausnummer(stamm["strasse"])
    _sub(a, f"{{{NS}}}name", stamm["name"][:100])
    _sub(a, f"{{{NS}}}strasse", strasse[:30])
    if hausnr:
        _sub(a, f"{{{NS}}}hausnr", hausnr)
    if zusatz:
        _sub(a, f"{{{NS}}}hausnrzusatz", zusatz)
    if stamm.get("plz"):
        _sub(a, f"{{{NS}}}plz", stamm["plz"])
    _sub(a, f"{{{NS}}}ort", stamm["ort"][:30])
    _sub(a, f"{{{NS}}}staat", stamm.get("staat", "DE"))
    if stamm.get("telefon"):
        _sub(a, f"{{{NS}}}telefon", stamm["telefon"])

    zz = _sub(u, f"{{{NS}}}zm-zeilen", waehrung="1", meldeart="11" if berichtigt else "10")
    _sub(zz, f"{{{NS}}}anzeige", "false")
    _sub(zz, f"{{{NS}}}widerruf", "false")
    mzr = _sub(zz, f"{{{NS}}}mzr")
    _sub(mzr, f"{{{NS}}}quart", str(zm_quart(quartal, monat)))
    _sub(mzr, f"{{{NS}}}jahr", str(jahr))
    for idnr, euro in zeilen:
        zeile = _sub(zz, f"{{{NS}}}zeile", umsatzart=ZM_UMSATZART_SONSTIGE_LEISTUNG)
        _sub(zeile, f"{{{NS}}}knre", idnr)
        _sub(zeile, f"{{{NS}}}betrag", str(euro))
    return _text(wurzel)


# ---------------------------------------------------------- Datenabholung
#
# Was das Finanzamt ins Postfach von Mein ELSTER legt — Mitteilungen,
# Bescheide —, lässt sich über ERiC abholen. Drei Datenarten im Verfahren
# ElsterDatenabholung: PostfachStatus (nur zählen), PostfachAnfrage (Metadaten
# und Verweise auf den Inhalt) und PostfachBestaetigung (Pflicht nach der
# Abholung). Empfänger ist immer `CS`.

NS_ABHOLUNG = "http://finkonsens.de/elster/elsterdatenabholung/v3"
ABHOLUNG_VERSION = "32"


def datenabholung(datenart: str, anfrage: ET.Element, *, hersteller_id: str,
                  datenlieferant: str, produkt: str, produktversion: str,
                  testmerker: str | None = None) -> str:
    """Ein Datensatz der Datenabholung. `anfrage` ist das Element unter
    `<Datenabholung>` (PostfachStatus, PostfachAnfrage, PostfachBestaetigung),
    im Namensraum NS_ABHOLUNG gebaut."""
    wurzel, block = _rahmen("ElsterDatenabholung", datenart, hersteller_id, datenlieferant,
                            testmerker)
    ndh = _sub(block, f"{{{NS}}}NutzdatenHeader", version="11")
    _sub(ndh, f"{{{NS}}}NutzdatenTicket", "1")
    _sub(ndh, f"{{{NS}}}Empfaenger", "CS", id="L")
    hersteller = _sub(ndh, f"{{{NS}}}Hersteller")
    _sub(hersteller, f"{{{NS}}}ProduktName", produkt)
    _sub(hersteller, f"{{{NS}}}ProduktVersion", produktversion)
    da = _sub(_sub(block, f"{{{NS}}}Nutzdaten"), f"{{{NS_ABHOLUNG}}}Datenabholung",
              version=ABHOLUNG_VERSION)
    da.append(anfrage)
    return _text(wurzel)


def postfach_status(einschraenkung: str = "alle", von: datetime.date | None = None,
                    **kopf) -> str:
    attrib = {"einschraenkung": einschraenkung}
    if von:
        attrib["von"] = von.strftime("%Y%m%d")
    return datenabholung("PostfachStatus",
                         ET.Element(f"{{{NS_ABHOLUNG}}}PostfachStatus", attrib), **kopf)
