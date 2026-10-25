# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Ein ERiC-Datensatz in Klartext: Vordruck, Zeile und Wortlaut wie in Mein ELSTER.

Die Feldnamen im Datensatz (E8002001, Kz83, de-gaap-ci:bs.ass …) sagen einem
Menschen nichts. Die Finanzverwaltung liefert die Übersetzung mit dem ERiC-SDK
aus: je Datenart und Jahr eine *Jahresdokumentation* (.ods) mit Vordruck,
Abschnitt, Zeilennummer und dem Text, der im Formular neben dem Feld steht,
dazu die zulässigen Werte von Auswahlfeldern (350 = GmbH, 1 = Ja …).

Gelesen wird zur Laufzeit aus dem SDK auf dieser Maschine. Nichts davon liegt
im Repository: die Dokumentation gehört zum SDK und wird nicht weitergegeben.
Fehlt sie, bleiben nur die Feldnamen — und die Hand voll Kopfdaten, die hier
von Hand beschrieben sind.
"""

from __future__ import annotations

import functools
import glob
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile

_T = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"

# Kopf- und Hüllfelder, die in keiner Jahresdokumentation stehen
KOPF = {
    "Verfahren": "Verfahren",
    "DatenArt": "Datenart",
    "Vorgang": "Vorgang",
    "HerstellerID": "Hersteller-ID der Software",
    "DatenLieferant": "Datenlieferant",
    "Verschluesselung": "Verschlüsselung",
    "Kompression": "Kompression",
    "TransportSchluessel": "Transportschlüssel (setzt ERiC beim Senden)",
    "Testmerker": "Testmerker",
    "NutzdatenTicket": "Nutzdaten-Ticket",
    "Empfaenger": "Empfänger: Finanzamt",
    "ProduktName": "Software",
    "ProduktVersion": "Softwareversion",
    "Erstellungsdatum": "Erstellungsdatum",
    "Unterfallart": "Unterfallart",
    "StNr": "Steuernummer (ELSTER-Format)",
    "Zeitraum": "Veranlagungszeitraum",
    "AbsName": "Absender: Name",
    "AbsStr": "Absender: Straße",
    "AbsPlz": "Absender: Postleitzahl",
    "AbsOrt": "Absender: Ort",
    "Copyright": "Erstellt mit",
    "OrdNrArt": "Art der Ordnungsnummer",
    "stichtag": "Bilanzstichtag",
    "Name": "Name", "Strasse": "Straße", "PLZ": "Postleitzahl", "Ort": "Ort",
    # ZM: die Dokumentation beschreibt diese Felder nicht
    "knri": "USt-IdNr. des meldenden Unternehmers", "name": "Name", "strasse": "Straße",
    "hausnr": "Hausnummer", "plz": "Postleitzahl", "ort": "Ort", "staat": "Staat",
    "quart": "Quartal", "jahr": "Jahr", "monat": "Monat",
}
KOPF_WERTE = {
    ("Vorgang", "send-Auth"): "mit dem Zertifikat signiert senden",
    ("Vorgang", "send-NoSig"): "ohne Signatur senden",
    ("Testmerker", "700000004"): "Testfall — wird angenommen und gelöscht",
    ("OrdNrArt", "S"): "Steuernummer",
}
# Abschnitte, die in keiner Jahresdokumentation stehen
ABSCHNITTE = {"Rueckuebermittlung": "Rückübermittlung des Bescheids",
              "DatenLieferant": "Datenlieferant", "Unternehmer": "Unternehmer",
              "Umsatzsteuervoranmeldung": "Anmeldung", "Steuerfall": "",
              "zulassNr": "Teilnehmernummer", "anschrift": "Anschrift", "zm-zeilen": "Meldung",
              "mzr": "Meldezeitraum", "zeile": "Meldezeile", "unternehmer": "Unternehmer"}
VORDRUCKE = {"Vorsatz": "Vorsatz: Steuernummer und Absender",
             "Anmeldungssteuern": "Umsatzsteuer-Voranmeldung", "zm": "Zusammenfassende Meldung"}
# Elemente, die nur gliedern
HUELLE = {"Elster", "TransferHeader", "Datei", "DatenTeil", "Nutzdatenblock",
          "NutzdatenHeader", "Hersteller", "Nutzdaten", "Steuerfall"}

# Wo die Jahresdokumentation je Art liegt (relativ zu Dokumentation/Datenarten)
QUELLEN = {
    "kst": "ElsterErklaerung/KSt/Jahresdokumentation/Jahresdokumentation_E30_{jahr}.ods",
    "gewst": "ElsterErklaerung/GewSt/Jahresdokumentation/Jahresdokumentation_E20_{jahr}.ods",
    "ustva": "ElsterAnmeldung/UStVA/Jahresdokumentation/Jahresdokumentation_UStVA_{jahr}.ods",
    # die ZM ist nicht nach Jahren, sondern nach Schemaversion dokumentiert
    "zm": "ElsterExtern/ZMDO/Jahresdokumentation/Jahresdokumentation_ZMDO_{jahr}.ods",
    "ebilanz": "ElsterBilanz/Bilanz/Jahresdokumentation/"
               "Jahresdokumentation_Bilanz_Taxonomie_{taxonomie}.ods",
    "eroeffnung": "ElsterBilanz/Bilanz/Jahresdokumentation/"
                  "Jahresdokumentation_Bilanz_Taxonomie_{taxonomie}.ods",
}

# E-Bilanz: Gruppen nach dem Anfang des Elementnamens
EBILANZ_GRUPPEN = (
    ("de-gcd:", "Stammdaten (GCD-Modul)"),
    ("de-gaap-ci:bs.ass", "Bilanz — Aktiva"),
    ("de-gaap-ci:bs.eqLiab", "Bilanz — Passiva"),
    ("de-gaap-ci:is.", "Gewinn- und Verlustrechnung"),
    ("de-gaap-ci:BVV", "Betriebsvermögensvergleich"),
    ("de-gaap-ci:detailedInformation", "Kontensalden"),
    ("de-gaap-ci:", "Weitere Angaben"),
)


def _name(e: ET.Element) -> str:
    return e.tag.rsplit("}", 1)[-1]


def _praefix(e: ET.Element, nsmap: dict[str, str]) -> str:
    if e.tag.startswith("{"):
        ns = e.tag[1:].split("}", 1)[0]
        p = nsmap.get(ns)
        return f"{p}:{_name(e)}" if p else _name(e)
    return e.tag


# ------------------------------------------------------------ SDK lesen

def dokumentation(bibliothek: str | None = None) -> str | None:
    """Das Verzeichnis Dokumentation/Datenarten des SDK.

    ERIC_DOKU zeigt direkt darauf. Sonst wird neben der Bibliothek gesucht:
    das SDK und seine Dokumentation kommen als zwei Archive, entpackt meist
    nebeneinander (…/<version>/sdk/… und …/<version>/doku/…)."""
    if os.environ.get("ERIC_DOKU"):
        d = os.path.expanduser(os.environ["ERIC_DOKU"])
        return d if os.path.isdir(d) else None
    if not bibliothek:
        try:
            from . import zugang
            bibliothek = zugang.hole("ERIC_PFAD", pflicht=False)
        except Exception:                                   # noqa: BLE001
            bibliothek = None
    if not bibliothek:
        return None
    pfad = os.path.abspath(os.path.expanduser(bibliothek))
    for _ in range(6):
        pfad = os.path.dirname(pfad)
        for kandidat in (glob.glob(os.path.join(pfad, "doku", "*", "Dokumentation", "Datenarten"))
                         + glob.glob(os.path.join(pfad, "Dokumentation", "Datenarten"))):
            if os.path.isdir(kandidat):
                return kandidat
    return None


_X = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _zelltext(e: ET.Element) -> str:
    """Text einer Zelle; <text:s/> und Absätze sind Leerzeichen, nicht nichts."""
    teile = [e.text or ""]
    for k in e:
        if k.tag == f"{{{_X}}}s":
            teile.append(" " * int(k.get(f"{{{_X}}}c") or 1))
        elif k.tag in (f"{{{_X}}}line-break", f"{{{_X}}}tab"):
            teile.append(" ")
        else:
            teile.append(_zelltext(k))
            if k.tag == f"{{{_X}}}p":
                teile.append(" ")
        teile.append(k.tail or "")
    return "".join(teile)


def _tabellen(pfad: str):
    root = ET.fromstring(zipfile.ZipFile(pfad).read("content.xml"))
    for t in root.iter(f"{{{_T}}}table"):
        zeilen = []
        for r in t.iter(f"{{{_T}}}table-row"):
            zellen = []
            for c in r.findall(f"{{{_T}}}table-cell"):
                n = min(int(c.get(f"{{{_T}}}number-columns-repeated") or 1), 30)
                zellen += [re.sub(r"\s+", " ", _zelltext(c)).strip()] * n
            if any(zellen):
                zeilen.append(zellen)
        yield t.get(f"{{{_T}}}name"), zeilen


def _auswahl(format_: str, typen: dict[str, str]) -> dict[str, str] | None:
    """'JaNein (1|2)', 'Enumeration - 01 [Veranlagung] - 04 […]' -> {wert: text}."""
    m = re.match(r"Benutzerdefinierte Typdefinition: (\S+)", format_)
    if m:
        format_ = typen.get(m.group(1), "")
    m = re.match(r"JaNein \((\w+)\|(\w+)\)", format_)
    if m:
        return {m.group(1): "Ja", m.group(2): "Nein"}
    if format_.startswith("JaNein"):
        return {"1": "Ja", "2": "Nein"}
    m = re.match(r"Ja \((\w+)\)", format_)
    if m:
        return {m.group(1): "Ja"}
    if format_.startswith("Enumeration"):
        return dict(re.findall(r"- (\S+) \[([^\]]+)\]", format_)) or None
    return None


@functools.lru_cache(maxsize=16)
def _jahresdoku(pfad: str, mtime: float) -> dict:
    """Felder, Abschnitte und Vordrucke aus einer Jahresdokumentation.

    Schlüssel ist der Kontext wie in der Dokumentation (`GK/Bilanz`) plus der
    Feldname; der Vordruck ist das erste Glied des Kontexts."""
    felder: dict[tuple[str, str], dict] = {}
    abschnitte: dict[str, str] = {}
    typen: dict[str, str] = {}
    blaetter = list(_tabellen(pfad))
    for name, zeilen in blaetter:
        if name == "Benutzerdefinierte Typen":
            for z in zeilen[1:]:
                # die erste Zeile je Typ ist die neueste Version
                typen.setdefault(z[0], z[3] if len(z) > 3 else "")
    for name, zeilen in blaetter:
        if not name.endswith(" - Felder"):
            continue
        vordruck = name[: -len(" - Felder")]
        kopf = zeilen[0] if zeilen else []
        for z in zeilen[1:]:
            if len(z) < 5:
                continue
            kontext = z[0]
            # Vordruck-Blätter nennen den Kontext relativ zum Vordruck, die der
            # E-Bilanz und der UStVA absolut
            k = f"{vordruck}/{kontext}".strip("/") if vordruck not in (
                "Kontext", "Header", "Anmeldungssteuern") else kontext
            felder.setdefault((k, z[1]), {"text": z[2], "auswahl": _auswahl(z[4], typen),
                                          "format": z[4], "zeile": None})
        del kopf
    for name, zeilen in blaetter:
        if not name.endswith(" - Texte"):
            continue
        for z in zeilen:
            code_i = next((i for i, x in enumerate(z) if re.match(r"(FK|B) \S", x)), None)
            if code_i is None or code_i + 1 >= len(z):
                continue
            code, kontext = z[code_i], z[code_i + 1]
            text = next((x for x in reversed(z[1:code_i]) if x), "")
            if code.startswith("B "):
                if " EZ " not in code:                 # EZ = Untertitel des Abschnitts
                    abschnitte.setdefault(kontext, re.sub(r"\s*\(\$\w+\.Wert\$\)", "", text))
                continue
            feld = code[3:]
            # Texte nennen den Kontext absolut, Felder der UStVA relativ zur Wurzel
            schluessel = next((k for k in ((kontext, feld), (kontext.split("/", 1)[-1], feld))
                               if k in felder), (kontext, feld))
            eintrag = felder.setdefault(schluessel, {"text": text, "auswahl": None,
                                                     "format": "", "zeile": None})
            eintrag["zeile"] = eintrag["zeile"] or (z[0] or None)
            eintrag["text"] = text or eintrag["text"]
    return {"felder": felder, "abschnitte": abschnitte}


def _laden(art: str, jahr: int | None, datenart_version: str | None,
           doku: str | None) -> dict | None:
    if not doku or art not in QUELLEN:
        return None
    taxonomie = (datenart_version or "").split("_", 1)[-1].replace(".", "_")
    pfad = os.path.join(doku, QUELLEN[art].format(jahr=jahr, taxonomie=taxonomie))
    if not os.path.exists(pfad):
        # ein jüngeres Jahr ist oft noch nicht dokumentiert: das letzte vorhandene
        vorhanden = sorted(glob.glob(os.path.join(doku, QUELLEN[art].format(
            jahr="*", taxonomie="*"))))
        if not vorhanden:
            return None
        pfad = vorhanden[-1]
    d = _jahresdoku(pfad, os.path.getmtime(pfad))
    return {**d, "quelle": os.path.relpath(pfad, doku)}


@functools.lru_cache(maxsize=4)
def _finanzaemter(doku: str) -> dict[str, str]:
    """Bundesfinanzamtsnummer -> Name, aus Finanzamtsdaten.ods (ein Blatt je Land)."""
    pfad = os.path.join(os.path.dirname(doku), "Finanzamtsdaten.ods")
    if not os.path.exists(pfad):
        return {}
    aus = {}
    for land, zeilen in _tabellen(pfad):
        for z in zeilen:
            if len(z) > 1 and re.fullmatch(r"\d{4}", z[0]) and z[1]:
                aus.setdefault(z[0], f"Finanzamt {z[1]}" + ("" if land in z[1] else f" ({land})"))
    return aus


@functools.lru_cache(maxsize=1)
def _eigene_bezeichnungen() -> dict:
    pfad = os.path.join(os.path.dirname(__file__), "taxonomie", "ebilanz_klartext_de.json")
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)


def _platzhalter(text: str | None, jahr: int | None) -> str | None:
    """'$VZ$' und Verwandte im Formulartext durch das Jahr ersetzen."""
    if not text or not jahr:
        return text
    text = re.sub(r"\$VZ([+-]\d)?\$", lambda m: str(jahr + int(m.group(1) or 0)), text)
    return re.sub(r"\s*\(?\$\w+(\.\w+)?\$\)?", "", text)


# ------------------------------------------------------------ Klartext

def _wert(feld: dict | None, name: str, roh: str) -> str | None:
    """Ein Auswahlwert in Worten, sonst None (dann steht der Wert für sich)."""
    if (name, roh) in KOPF_WERTE:
        return KOPF_WERTE[(name, roh)]
    if feld and feld.get("auswahl") and roh in feld["auswahl"]:
        return feld["auswahl"][roh]
    return None


def _ist_betrag(feld: dict | None) -> bool:
    f = (feld or {}).get("format", "")
    return "Geld" in f or "Ganzzahl" in f and "Prozent" not in f


def _elster(root: ET.Element, art: str, d: dict | None, finanzaemter: dict,
            jahr: int | None) -> list[dict]:
    """KSt, GewSt, UStVA, ZM: ein Baum mit Vordrucken als oberster Ebene."""
    gruppen: list[dict] = []
    technik = {"titel": "Übertragung (technisch)", "zeilen": [], "technisch": True}
    felder = (d or {}).get("felder", {})
    abschnitte = (d or {}).get("abschnitte", {})

    def blatt(e, pfad, kontext, gruppe, abschnitt):
        name = _name(e)
        roh = (e.text or "").strip()
        feld = felder.get((kontext, name)) or felder.get((kontext.split("/", 1)[-1], name))
        if not feld and art == "zm":
            # Die ZM-Dokumentation hängt dem Feldnamen eine Erläuterung an
            # (knri_UStIdNr_Inland) und gliedert anders als der Datensatz
            feld = next((f for (_, n), f in felder.items() if n == name
                         or n.startswith(name + "_")), None)
        text = _platzhalter((feld or {}).get("text"), jahr) or KOPF.get(name)
        if art == "ustva" and re.fullmatch(r"Kz\d\d", name):
            # Die Kurztexte der Dokumentation ("zum Steuersatz von 19 Prozent")
            # stehen im Formular unter einer Überschrift; hier fehlt die.
            from .umsatzsteuer import BEZEICHNUNG
            text = BEZEICHNUNG.get(name[2:]) or text
        wert = _wert(feld, name, roh)
        if name == "Empfaenger" and roh in finanzaemter:
            wert = finanzaemter[roh]
        gruppe["zeilen"].append({
            "abschnitt": abschnitt, "zeile": (feld or {}).get("zeile"), "text": text,
            "wert": roh, "wert_text": wert, "betrag": _ist_betrag(feld),
            "feld": name, "pfad": pfad})

    def gehen(e, pfad, kontext, gruppe, abschnitt, nummern):
        kinder = list(e)
        if not kinder:
            blatt(e, pfad, kontext, gruppe, abschnitt)
            return
        # Wiederholte Abschnitte (mehrere Anteilseigner) durchzählen
        zaehler: dict[str, int] = {}
        gesamt: dict[str, int] = {}
        for k in kinder:
            gesamt[_name(k)] = gesamt.get(_name(k), 0) + 1
        for k in kinder:
            n = _name(k)
            if not list(k):
                gehen(k, f"{pfad}/{n}", kontext, gruppe, abschnitt, nummern)
                continue
            zaehler[n] = zaehler.get(n, 0) + 1
            unter = f"{kontext}/{n}" if kontext else n
            titel = _platzhalter(abschnitte.get(unter), jahr) or (
                abschnitt if n in HUELLE else ABSCHNITTE.get(n, n))
            if gesamt[n] > 1:
                titel = f"{titel} {zaehler[n]}"
            if abschnitt and titel != abschnitt and n not in HUELLE and abschnitt not in titel:
                titel = f"{abschnitt} › {titel}"
            gehen(k, f"{pfad}/{n}", unter, gruppe, titel, nummern)

    def vordruck(e):
        n = _name(e)
        titel = VORDRUCKE.get(n) or abschnitte.get(n) or n
        g = {"titel": titel, "feld": n, "zeilen": []}
        gehen(e, n, n, g, "", {})
        gruppen.append(g)

    def huelle(e, pfad):
        for k in e:
            n = _name(k)
            if n == "Nutzdaten":
                for erkl in k:                                  # E30, E20, Anmeldungssteuern
                    if _name(erkl) == "Anmeldungssteuern" or art == "zm":
                        vordruck(erkl)
                    else:
                        for v in erkl:
                            vordruck(v)
            elif list(k):
                huelle(k, f"{pfad}/{n}")
            else:
                blatt(k, f"{pfad}/{n}", n, technik, _name(e) if _name(e) not in (
                    "Elster",) else "")

    huelle(root, _name(root))
    return gruppen + ([technik] if technik["zeilen"] else [])


def _ebilanz(root: ET.Element, d: dict | None, finanzaemter: dict,
             positionen: dict[str, str]) -> list[dict]:
    """E-Bilanz: flache XBRL-Fakten, gruppiert nach Stammdaten, Bilanz, GuV."""
    felder = (d or {}).get("felder", {})
    nsmap = {}
    for e in root.iter():
        if e.tag.startswith("{"):
            ns = e.tag[1:].split("}", 1)[0]
            if "de-gcd" in ns:
                nsmap[ns] = "de-gcd"
            elif "de-gaap-ci" in ns:
                nsmap[ns] = "de-gaap-ci"
            elif "xbrl.org/2003/instance" in ns:
                nsmap[ns] = "xbrli"
    kontexte = {}
    for c in root.iter():
        if _praefix(c, nsmap) == "xbrli:context":
            p = [x for x in c.iter() if _name(x) in ("instant", "startDate", "endDate")]
            kontexte[c.get("id")] = " – ".join(
                ".".join(reversed((x.text or "").split("-"))) for x in p)

    eigene = _eigene_bezeichnungen()

    def beschriftung(el: str) -> str | None:
        kurz = el.split(":", 1)[-1]
        if kurz in eigene["positionen"]:
            return eigene["positionen"][kurz]
        f = felder.get(("", el.replace("de-gcd:", "gcd:").replace("de-gaap-ci:", "gaap:")))
        if f and f["text"]:
            return f["text"]
        return positionen.get(kurz)

    def beschriftung_mit_eltern(el: str) -> tuple[str | None, bool]:
        """Die eigene Beschriftung, sonst die der nächsten Oberposition.

        Auswahlfelder (…reportStatus.reportStatus.E mit Wert 'endgültig') tragen
        die Beschriftung der Oberposition, ihr Wert sagt den Rest. Alles andere
        wird aus den Namensgliedern übersetzt und gilt als ungefähr."""
        if (b := beschriftung(el)):
            return b, True
        teile = el.split(".")
        for i in range(len(teile) - 1, 0, -1):
            if not (b := beschriftung(".".join(teile[:i]))):
                continue
            rest = teile[i:]
            if rest[0] == teile[i - 1] or (len(rest) == 1 and re.fullmatch(r"[A-Z][A-Z0-9]*", rest[0])):
                return b, True
            return f"{b} › {' › '.join(eigene['segmente'].get(t, t) for t in rest)}", False
        return None, False

    gruppen = {t: {"titel": t, "zeilen": []} for _, t in EBILANZ_GRUPPEN}
    technik = {"titel": "Übertragung (technisch)", "zeilen": [], "technisch": True}

    def gruppe_fuer(el: str):
        return next(gruppen[t] for p, t in EBILANZ_GRUPPEN if el.startswith(p))

    def fakt(e, abschnitt):
        el = _praefix(e, nsmap)
        roh = (e.text or "").strip()
        nil = e.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true"
        text, genau = beschriftung_mit_eltern(el)
        kurz = el.replace("de-gcd:", "gcd:").replace("de-gaap-ci:", "gaap:")
        feld = felder.get(("", kurz))
        wert_text = "leer berichtet" if nil else _wert(feld, el, roh)
        if el.endswith("accountbalances.positionName") and roh:
            wert_text = beschriftung_mit_eltern(f"de-gaap-ci:{roh}")[0]
        gruppe_fuer(el)["zeilen"].append({
            "abschnitt": abschnitt, "zeile": None, "text": text, "ungefaehr": not genau,
            "leer": nil,
            "wert": "" if nil else roh, "wert_text": wert_text,
            "betrag": e.get("unitRef") == "EUR", "feld": el.split(":", 1)[-1],
            "zeitraum": kontexte.get(e.get("contextRef")), "pfad": el})

    def tupel_titel(k) -> str:
        el = _praefix(k, nsmap)
        b, _ = beschriftung_mit_eltern(el)
        if el.endswith("detailedInformation.accountBalances"):
            # Ein Kontensaldo heißt nach seinem Konto, nicht nach dem Tupel
            werte = {_name(x).rsplit(".", 1)[-1]: (x.text or "").strip() for x in k}
            return f"Konto {werte.get('accountNumber', '')} {werte.get('accountDescription', '')}".strip()
        return b or el.split(":", 1)[-1]

    def tupel(e, abschnitt):
        for k in e:
            if list(k):
                tupel(k, tupel_titel(k))
            else:
                fakt(k, abschnitt)

    for e in root.iter():
        if _praefix(e, nsmap) != "xbrli:xbrl":
            continue
        for k in e:
            el = _praefix(k, nsmap)
            if el.startswith(("de-gcd:", "de-gaap-ci:")):
                if list(k):
                    tupel(k, tupel_titel(k))
                else:
                    fakt(k, "")
    # Hülle: Transfer- und Nutzdatenkopf, Stichtag
    for e in root.iter():
        n = _name(e)
        if list(e) or n not in KOPF:
            continue
        roh = (e.text or "").strip()
        wert = finanzaemter.get(roh) if n == "Empfaenger" else _wert(None, n, roh)
        technik["zeilen"].append({"abschnitt": "", "zeile": None, "text": KOPF[n], "wert": roh,
                                  "wert_text": wert, "betrag": False, "feld": n, "pfad": n})
    return [g for g in gruppen.values() if g["zeilen"]] + [technik]


def lesbar(xml: str | None, art: str, jahr: int | None = None,
           datenart_version: str | None = None, *, doku: str | None = None,
           positionen: dict[str, str] | None = None) -> dict:
    """Der Datensatz als Gruppen von Zeilen mit Klartext.

    `positionen` (Element -> Bezeichnung) ergänzt bei der E-Bilanz, was die
    Jahresdokumentation nicht beschreibt — aus der Kontenzuordnung des Mandanten."""
    if not xml:
        return {"gruppen": [], "quelle": None}
    doku = doku if doku is not None else dokumentation()
    d = _laden(art, jahr, datenart_version, doku)
    fa = _finanzaemter(doku) if doku else {}
    root = ET.fromstring(xml.encode("utf-8"))
    if art in ("ebilanz", "eroeffnung"):
        gruppen = _ebilanz(root, d, fa, positionen or {})
    else:
        gruppen = _elster(root, art, d, fa, jahr)
    ohne = sum(1 for g in gruppen for z in g["zeilen"] if not z["text"])
    return {"gruppen": gruppen, "quelle": d and d["quelle"], "ohne_text": ohne,
            "sdk": bool(doku)}
