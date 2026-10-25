# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Körperschaftsteuer- und Gewerbesteuererklärung als ERiC-Datensatz.

Datenarten `KSt` (Unterfallart 30, Schema E30) und `GewSt` (Unterfallart 20,
Schema E20) des Verfahrens ElsterErklaerung. Vorgesehen ist der einfache Fall
einer Kapitalgesellschaft, die nur Einkünfte aus Gewerbebetrieb hat:

    KSt 1, Anlagen GK, ZVE, Verluste, WA und KSt 1 F
    GewSt 1 A

Woher die Werte kommen:

* die Beträge aus `steuern.json` des Jahres — der Überleitungsrechnung, die
  `steuern.pruefen()` gegen die Konten hält. Gesperrt (Platzhalter oder
  Abweichung) heißt: kein Datensatz. Gebraucht werden die Zeilen mit diesen
  `kennzahl`-Marken: `zve`, `einkuenfte_gewerbe`, `verlustvortrag`,
  `gewinn_gewerbe` und `gewerbeverlust`; der Jahresüberschuss ist die Zeile
  mit `quelle: guv`;
* Firma, Sitz, Rechtsform und Anteilseigner aus `mandant.json`;
* was die Buchführung nicht weiß, aus `<jahr>/ertragsteuer.json`: Gegenstand
  des Unternehmens, die Betriebsstätte, die Fragen der Anlage WA und die
  Anfangsbestände der Anlage KSt 1 F.

Was der einfache Fall nicht abdeckt — Überleitungsposten zwischen
Jahresergebnis und Einkünften, Hinzurechnungen, ein Verlustvortrag aus dem
Vorjahr — hält mit `DatenFehlen` an, statt eine unvollständige Erklärung zu
bauen.

Beide Erklärungen kennen keine Kennzahl „berichtigte Erklärung“: eine zweite
Übermittlung vor dem Bescheid ersetzt die erste. Dass es eine Berichtigung ist,
sagt der Datensatz in den „Ergänzenden Angaben zur Steuererklärung“ (KSt 1
Zeile 13, GewSt 1 A Zeile 21) — aus `ergaenzende_angaben` in ertragsteuer.json.
Der Grund 3 („Sachverhalte sollen personell vertieft geprüft werden“) sorgt
dafür, dass ein Mensch im Finanzamt den Text liest, statt dass die Erklärung
maschinell durchläuft.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import xml.etree.ElementTree as ET

from .eric_xml import NS, DatenFehlen, _hersteller_id, _pflicht, _rahmen, _sub, _text

ANGABEN = "ertragsteuer.json"
COPYRIGHT = "llm-buchhaltung"

# Rechtsformschlüssel, in beiden Erklärungen gleich (RECHTSFORM_KOERPERSCHAFTEN_V3,
# RECHTSFORM_GEWERBESTEUER_V2)
RECHTSFORM = {"Gesellschaft mit beschränkter Haftung": "350",
              "Unternehmergesellschaft (haftungsbeschränkt)": "370"}

# Die Felder, die einen Betrag tragen — für den Abgleich mit der Buchführung
BETRAEGE = {
    "kst": ("E8002001", "E8109901", "E8004904", "E8005401", "E8006901", "E8007501",
            "E8008001", "E8008102", "E8009201", "E8201001", "E8201101", "E8201201", "E8201301",
            "E8240102", "E8245601"),
    "gewst": ("E4001703",),
}


def angaben_laden(jahr_dir: str) -> dict:
    pfad = os.path.join(jahr_dir, ANGABEN)
    if not os.path.exists(pfad):
        raise DatenFehlen(f"{os.path.basename(jahr_dir)}/{ANGABEN} fehlt — die Angaben, die "
                          "nicht aus der Buchführung kommen (docs/eric.md).")
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)


def _vorhanden(d: dict, wo: str, *felder: str) -> None:
    """Wie `_pflicht`, aber 0 ist eine Angabe (Einlagekonto, Bestände)."""
    fehlt = [f for f in felder if d.get(f) is None or d.get(f) == ""]
    if fehlt:
        raise DatenFehlen(f"{wo}: es fehlt {', '.join(fehlt)}")


def euro(cent: int) -> int:
    """Volle Euro, abgerundet: ein Gewinn wird kleiner, ein Verlust größer —
    wie die Formulare in Mein ELSTER (−1.234,56 → −1.235)."""
    return cent // 100


def _ergaenzend(angaben: dict, art: str) -> tuple[str, str] | None:
    """(Grund, Text) der Ergänzenden Angaben, oder None."""
    e = angaben.get("ergaenzende_angaben") or {}
    text = (e.get(art) or e.get("text") or "").strip()
    if not text:
        return None
    grund = str(e.get("grund", "3"))
    if grund not in ("1", "2", "3", "4"):
        raise DatenFehlen(f"{ANGABEN}: ergaenzende_angaben.grund muss 1 bis 4 sein.")
    if len(text) > 999:
        raise DatenFehlen(f"{ANGABEN}: Ergänzende Angaben ({art}) haben {len(text)} Zeichen, "
                          "erlaubt sind 999.")
    return grund, text


def _datum(iso: str) -> str:
    return datetime.date.fromisoformat(str(iso)[:10]).strftime("%d.%m.%Y")


def _jn(wert: bool) -> str:
    return "1" if wert else "2"


def _kennzahl(spec: dict, name: str) -> int:
    for a in spec.get("abschnitte", []):
        for z in a["zeilen"]:
            if z.get("kennzahl") == name:
                return z["betrag"]
    raise DatenFehlen(f"steuern.json: keine Zeile mit kennzahl {name!r}")


def _guv(spec: dict) -> int:
    for a in spec.get("abschnitte", []):
        for z in a["zeilen"]:
            if z.get("quelle") == "guv":
                return z["betrag"]
    raise DatenFehlen("steuern.json: keine Zeile mit quelle 'guv' (Jahresergebnis)")


def werte(spec: dict) -> dict:
    """Die Beträge beider Erklärungen in vollen Euro, aus der geprüften
    Überleitung. Hält an, wo der einfache Fall verlassen wird."""
    if spec.get("gesperrt"):
        raise DatenFehlen("Die Überleitung in steuern.json ist gesperrt (Platzhalter oder "
                          "Abweichung von den Konten) — erst `bh abgabe` freibekommen.")
    ergebnis = _guv(spec)
    einkuenfte = _kennzahl(spec, "einkuenfte_gewerbe")
    zve = _kennzahl(spec, "zve")
    gewinn = _kennzahl(spec, "gewinn_gewerbe")
    if not ergebnis == einkuenfte == zve == gewinn:
        raise DatenFehlen("Jahresergebnis, Einkünfte, Einkommen und Gewinn aus Gewerbebetrieb "
                          "weichen voneinander ab — Überleitungsposten sind im Datensatz noch "
                          "nicht vorgesehen.")
    verlustvortrag = _kennzahl(spec, "verlustvortrag")
    gewerbeverlust = _kennzahl(spec, "gewerbeverlust")
    verlust = -min(zve, 0)
    if abs(verlustvortrag) != verlust or abs(gewerbeverlust) != verlust:
        raise DatenFehlen("Der Verlustvortrag ist nicht der Verlust des Jahres — ein Vortrag "
                          "aus dem Vorjahr ist im Datensatz noch nicht vorgesehen.")
    return {"ergebnis": euro(ergebnis), "zve": euro(zve),
            "verlust": -euro(zve) if zve < 0 else 0}


def _vorsatz(eltern: ET.Element, ns: str, unterfallart: str, *, stamm: dict,
             steuernummer: str, jahr: int, bescheid: bool) -> None:
    v = _sub(eltern, f"{{{ns}}}Vorsatz")
    _sub(v, f"{{{ns}}}Unterfallart", unterfallart)
    _sub(v, f"{{{ns}}}Vorgang", "01")
    _sub(v, f"{{{ns}}}StNr", steuernummer)
    _sub(v, f"{{{ns}}}Zeitraum", str(jahr))
    _sub(v, f"{{{ns}}}AbsName", stamm["name"][:45])
    _sub(v, f"{{{ns}}}AbsStr", stamm["strasse"][:30])
    _sub(v, f"{{{ns}}}AbsPlz", stamm["plz"])
    _sub(v, f"{{{ns}}}AbsOrt", stamm["ort"][:29])
    _sub(v, f"{{{ns}}}Copyright", COPYRIGHT)
    _sub(v, f"{{{ns}}}OrdNrArt", "S")
    r = _sub(v, f"{{{ns}}}Rueckuebermittlung")
    _sub(r, f"{{{ns}}}Bescheid", _jn(bescheid))
    if bescheid:
        # Abholung der Bescheiddaten über ERiC; den Schlüssel dazu legt `bh` noch nicht an
        _sub(r, f"{{{ns}}}ArtRueckuebermittlung", "INTERNET")


def _nutzdaten(datenart: str, *, bufa: str, hersteller_id: str, produkt: str,
               produktversion: str, datenlieferant: str, testmerker: str | None):
    wurzel, block = _rahmen("ElsterErklaerung", datenart, hersteller_id, datenlieferant,
                            testmerker)
    ndh = _sub(block, f"{{{NS}}}NutzdatenHeader", version="11")
    _sub(ndh, f"{{{NS}}}NutzdatenTicket", "1")
    _sub(ndh, f"{{{NS}}}Empfaenger", bufa, id="F")
    hersteller = _sub(ndh, f"{{{NS}}}Hersteller")
    _sub(hersteller, f"{{{NS}}}ProduktName", produkt)
    _sub(hersteller, f"{{{NS}}}ProduktVersion", produktversion)
    return wurzel, _sub(block, f"{{{NS}}}Nutzdaten")


def _pruefe_kopf(stamm: dict, steuernummer: str, bufa: str) -> str:
    _pflicht(stamm, "name", "strasse", "plz", "ort", "rechtsform")
    if not re.fullmatch(r"\d{13}", steuernummer or ""):
        raise DatenFehlen("Steuernummer im ELSTER-Format hat 13 Ziffern.")
    if not re.fullmatch(r"\d{4}", bufa or ""):
        raise DatenFehlen("Bundesfinanzamtsnummer hat vier Ziffern (mandant.json, elster.bufa).")
    rf = RECHTSFORM.get(stamm["rechtsform"])
    if not rf:
        raise DatenFehlen(f"Rechtsform {stamm['rechtsform']!r} ist noch nicht vorgesehen.")
    return rf


def _widnr(stamm: dict) -> str | None:
    if not stamm.get("widnr"):
        return None
    w = stamm["widnr"].replace(" ", "").split("-")[0]
    if not re.fullmatch(r"DE\d{9}", w):
        raise DatenFehlen("Die W-IdNr. ist DE und neun Ziffern (mandant.json, widnr).")
    return w


def kst(spec: dict, angaben: dict, *, stamm: dict, gj: dict, jahr: int, steuernummer: str,
        bufa: str, elster_stnr, hersteller_id: str, produkt: str, produktversion: str,
        testmerker: str | None = None) -> str:
    """Datensatz der Datenart KSt_<jahr>. `steuernummer` im ELSTER-Format,
    `gj` mit `beginn` und `ende` des (einzigen) Wirtschaftsjahres.
    `elster_stnr(steuernummer, bufa)` bringt die Steuernummern der
    Anteilseigner ins ELSTER-Format (in der Regel `Eric.steuernummer`)."""
    rf = _pruefe_kopf(stamm, steuernummer, bufa)
    w = werte(spec)
    ae = stamm.get("anteilseigner") or []
    if not ae:
        raise DatenFehlen("mandant.json: anteilseigner fehlen (KSt 1, Zeilen 21 ff.).")
    for a in ae:
        _vorhanden(a, "mandant.json, anteilseigner", "name", "steuernummer", "bufa", "strasse",
                   "plz", "ort", "nennbetrag", "prozent", "seit")
    f = angaben.get("kst1f") or {}
    _vorhanden(f, f"{ANGABEN}, kst1f", "eigenkapital", "nennkapital", "einlagekonto",
               "einlagekonto_anfang", "einlagekonto_ende")
    if angaben.get("vertraege_mit_anteilseignern") is None:
        raise DatenFehlen(f"{ANGABEN}: vertraege_mit_anteilseignern (Anlage WA, Zeile 15) fehlt.")

    wurzel, nd = _nutzdaten("KSt", bufa=bufa, hersteller_id=hersteller_id, produkt=produkt,
                            produktversion=produktversion, datenlieferant=stamm["name"],
                            testmerker=testmerker)
    ns = f"http://finkonsens.de/elster/elstererklaerung/kst/e30/v{jahr}"
    e30 = _sub(nd, f"{{{ns}}}E30", version=str(jahr))

    def s(eltern, tag, text=None):
        return _sub(eltern, f"{{{ns}}}{tag}", text)

    k1 = s(e30, "KSt1")
    s(k1, "E8000016", "X")                         # mit Feststellung des Einlagekontos
    allg = s(k1, "Allgemein")
    s(allg, "E8000101", stamm["name"])
    if _widnr(stamm):
        s(allg, "E8000102", _widnr(stamm))
    st = s(k1, "Steuerpflicht")
    s(st, "E8000501", (angaben.get("ort_geschaeftsleitung") or stamm["ort"])[:24])
    s(st, "E8000601", (stamm.get("sitz") or stamm["ort"])[:24])
    s(s(st, "Rechtsform"), "E8001105", rf)
    if (erg := _ergaenzend(angaben, "kst")):
        q = s(k1, "Qualifiz")                      # Zeile 13
        s(q, "E8001305", erg[0])
        s(q, "E8001304", erg[1])
    wj = s(k1, "Wirtschaftsjahr")
    s(wj, "E8002606", _datum(gj["beginn"]))
    s(wj, "E8002607", _datum(gj["ende"]))
    if angaben.get("neu_gegruendet"):
        s(wj, "E8000703", "1")
    s(s(k1, "Weiteres"), "E8001007", _jn(bool(angaben.get("p8_abs9"))))
    ae_el = s(k1, "AE")
    for a in ae:
        e = s(ae_el, "Einzel")
        s(e, "E8122111", _jn(a.get("geaendert", True)))
        s(e, "E8122109", elster_stnr(a["steuernummer"], a["bufa"]))
        s(e, "E8122101", a["name"])
        s(e, "E8122102", a["strasse"])
        s(e, "E8122103", f"{a['plz']} {a['ort']}")
        b = s(e, "Besitzdauer")
        # Dezimalkomma wie auf dem Vordruck: '16250,00' und '65'
        s(b, "E8122104", a["nennbetrag"].replace(".", ""))
        s(b, "E8122105", a["prozent"].rstrip("0").rstrip(",") if "," in a["prozent"]
          else a["prozent"])
        s(b, "E8122106", _datum(a["seit"]))
    s(s(ae_el, "Gesamt"), "E8122601", _jn(angaben.get("anteilseigner_geaendert", True)))

    gk = s(e30, "GK")
    s(gk, "WJ", "WJ1")
    ga = s(gk, "Allgemein")
    s(ga, "E8000201", _datum(gj["beginn"]))
    s(ga, "E8000202", _datum(gj["ende"]))
    s(s(gk, "Bilanz"), "E8002001", str(w["ergebnis"]))
    s(s(gk, "Einkunfte"), "E8109901", str(w["zve"]))

    zve = s(e30, "ZVE")
    sde = s(zve, "SdE")
    s(s(s(sde, "Gewerbe"), "Laufend"), "E8004904", str(w["zve"]))
    s(s(sde, "SdE"), "E8005401", str(w["zve"]))
    s(s(s(zve, "GdE"), "GdE"), "E8006901", str(w["zve"]))
    s(s(s(zve, "ZVE"), "ZVE"), "E8007501", str(w["zve"]))

    if w["verlust"]:
        v = s(e30, "Verluste")
        neg = s(v, "Negativ")
        s(neg, "E8008001", str(w["verlust"]))
        s(neg, "E8008102", "0")                    # kein Rücktrag
        s(s(v, "Ende"), "E8009201", str(w["verlust"]))

    s(s(s(e30, "WA"), "AE"), "E8122801", _jn(angaben["vertraege_mit_anteilseignern"]))

    k1f = s(e30, "KSt1F")
    s(k1f, "WJ", "WJ1")
    fa = s(k1f, "Allgemein")
    if f.get("eintritt_steuerpflicht"):
        s(fa, "E8200905", _datum(f["eintritt_steuerpflicht"]))
    s(fa, "E8200101", _datum(gj["beginn"]))
    s(fa, "E8200102", _datum(gj["ende"]))
    g = s(k1f, "Gewinn")
    # § 27 Abs. 1 Satz 5 KStG: Eigenkapital laut Steuerbilanz abzüglich
    # gezeichnetem Kapital und Einlagekonto, nicht negativ. Ohne Zeile 18 lässt
    # die Plausibilitätsprüfung Zeile 20 = Zeile 17 durchgehen — ein Fehler, der
    # erst beim Bescheid auffällt.
    ausschuettbar = max(f["eigenkapital"] - f["nennkapital"] - f["einlagekonto"], 0)
    if f.get("ausschuettbarer_gewinn") not in (None, ausschuettbar):
        raise DatenFehlen(f"{ANGABEN}: kst1f.ausschuettbarer_gewinn ist {f['ausschuettbarer_gewinn']}, "
                          f"Eigenkapital − Nennkapital − Einlagekonto ergibt {ausschuettbar}.")
    s(g, "E8201001", str(f["eigenkapital"]))
    s(g, "E8201101", str(f["nennkapital"]))
    s(g, "E8201201", str(f["einlagekonto"]))
    s(g, "E8201301", str(ausschuettbar))
    tab = s(s(k1f, "ELK"), "Tabelle")
    s(s(tab, "Anfang"), "E8240102", str(f["einlagekonto_anfang"]))
    s(s(tab, "Ende"), "E8245601", str(f["einlagekonto_ende"]))

    _vorsatz(e30, ns, "30", stamm=stamm, steuernummer=steuernummer, jahr=jahr,
             bescheid=bool(angaben.get("bescheiddaten_abholen")))
    return _text(wurzel)


def gewst(spec: dict, angaben: dict, *, stamm: dict, jahr: int, steuernummer: str,
          bufa: str, hersteller_id: str, produkt: str, produktversion: str,
          testmerker: str | None = None) -> str:
    """Datensatz der Datenart GewSt_<jahr>: eine Betriebsstätte in einer Gemeinde."""
    rf = _pruefe_kopf(stamm, steuernummer, bufa)
    w = werte(spec)
    bst = angaben.get("betriebsstaette") or {}
    _vorhanden(bst, f"{ANGABEN}, betriebsstaette", "plz", "ort")
    if not angaben.get("gegenstand"):
        raise DatenFehlen(f"{ANGABEN}: gegenstand (GewSt 1 A, Zeile 4) fehlt.")

    wurzel, nd = _nutzdaten("GewSt", bufa=bufa, hersteller_id=hersteller_id, produkt=produkt,
                            produktversion=produktversion, datenlieferant=stamm["name"],
                            testmerker=testmerker)
    ns = f"http://finkonsens.de/elster/elstererklaerung/gewst/e20/v{jahr}"
    e20 = _sub(nd, f"{{{ns}}}E20", version=str(jahr))

    def s(eltern, tag, text=None):
        return _sub(eltern, f"{{{ns}}}{tag}", text)

    g1 = s(e20, "GewSt1A")
    allg = s(g1, "Allgemein")
    s(allg, "E4000101", stamm["name"])
    s(allg, "E4000201", angaben["gegenstand"][:25])
    if _widnr(stamm):
        s(allg, "E4000502", _widnr(stamm))
    s(s(g1, "Rechtsform"), "E4000501", rf)
    if (erg := _ergaenzend(angaben, "gewst")):
        q = s(g1, "Erg_Ang")                       # Zeile 21
        s(q, "E4003204", erg[0])
        s(q, "E4003205", erg[1])
    b = s(s(g1, "Ang_Gew_Betr"), "Betr_St")
    s(b, "E4001506", "2")                          # nicht in mehreren Gemeinden
    s(b, "E4001507", "2")
    s(b, "E4001102", "2")                          # nicht verlegt
    s(b, "E4001110", bst["plz"])
    s(b, "E4001111", bst["ort"])
    s(s(g1, "Gewinn"), "E4001703", str(w["zve"]))

    _vorsatz(e20, ns, "20", stamm=stamm, steuernummer=steuernummer, jahr=jahr,
             bescheid=bool(angaben.get("bescheiddaten_abholen")))
    return _text(wurzel)


def betraege_aus_xml(xml: str, art: str) -> dict[str, str]:
    """Die Betragsfelder eines Datensatzes: {'E8002001': '-1235', ...}."""
    felder = BETRAEGE[art]
    return {e.tag.rpartition("}")[2]: (e.text or "").strip()
            for e in ET.fromstring(xml.encode("utf-8")).iter()
            if e.tag.rpartition("}")[2] in felder}
