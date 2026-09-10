# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Parser fuer eine ausgefertigte Einkommensteuererklaerung (DATEV, PDF).

Zweck: das Vorjahr als Vergleichsjahr in die Buchfuehrung holen, ohne eine
einzige Zahl abzuschreiben. Die Erklaerung ist das Dokument, aus dem gebucht
wird - dieselbe Rolle, die bei den Gesellschaften der Jahresabschlussbericht
hat (siehe `bhl/vorjahr.py`).

Gelesen wird der Textlayer (`pdftotext -layout`). Die Erklaerung ist ein
Formular, deshalb ist jede Zahl an ihrer Zeilennummer erkennbar:

        15   Summe                                          6.376
        20   auf die Zeilen 15 und 18 entfallende ...       1.420

Zwei Fallen stecken in diesem Layout, beide hier behandelt:

* Die Beschriftung selbst enthaelt Zahlen ("... und im Jahr 2024", "auf die
  Zeilen 15 und 18"). Ohne `_saeubern` wird die Jahreszahl als Betrag gelesen.
* Der Betrag steht oft nicht in der Zeile mit der Nummer, sondern eine bis
  drei Zeilen darunter - Umbrueche im Formular.

Deshalb prueft der Parser sich selbst: die aufaddierten Einzelzeilen muessen
die Summenzeilen des Formulars treffen (Zeile 32, 83 und 85 der Anlage V).
Weicht etwas ab, wird nichts gebucht, sondern ein Fehler gemeldet.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

# Zeilen der Anlage V, die einzeln gebucht werden.
EINNAHMEN = (15, 18, 20, 21, 29)
# Die Werbungskosten werden aus den *Summenzeilen* der jeweiligen Rubrik
# gelesen (35 = AfA, 48 = Schuldzinsen ...), nicht aus den Einzelangaben:
# die Erklaerung listet dort beliebig viele Positionen auf.
WERBUNGSKOSTEN = {35: 33, 48: 46, 55: 55, 56: 56, 75: 73, 78: 76}
SUMMEN = {"einnahmen": 32, "werbungskosten": 83, "ergebnis": 85}

_MUSTER = {
    15: r"^15\s+Summe", 18: r"^18\s", 20: r"^20\s", 21: r"^21\s", 29: r"^29\s",
    35: r"^35\s+Abzugsfähige", 48: r"^48\s+Abzugsfähige", 55: r"^55\s+Abzugsfähige",
    56: r"^56\s", 75: r"^75\s+Abzugsfähige", 78: r"^78\s+Abzugsfähige",
    32: r"^32\s+Summe der Einnahmen", 83: r"^83\s+Summe der Werbungskosten \(",
    85: r"^85 Überschuss",
}

RE_ZAHL = re.compile(r"(-?[\d.]+)\s*$")
RE_BESCHRIFTUNG = re.compile(r"(im Jahr\s+\d{4}|Zeilen?\s+[\d, und]+)\s*$")
RE_ANLAGE_V = re.compile(r"(\d)\. Anlage V")
RE_ENDE = re.compile(r"^\s*Anlage Vorsorgeaufwand\s*$")


class ParseFehler(Exception):
    pass


@dataclass
class AnlageV:
    nr: int
    zeilen: dict[int, int]              # Zeilennummer -> Betrag in vollen Euro
    einnahmen: int
    werbungskosten: int
    ergebnis: int


def _text(pfad: str) -> list[str]:
    r = subprocess.run(["pdftotext", "-layout", pfad, "-"],
                       capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        raise ParseFehler(f"{pfad}: kein Textlayer lesbar. Ein Scan ohne Text "
                          f"laesst sich so nicht auswerten.")
    return r.stdout.splitlines()


def _saeubern(s: str) -> str:
    return RE_BESCHRIFTUNG.sub("", s.rstrip())


def _wert(block: list[str], i: int, tiefe: int = 4) -> int | None:
    """Betrag zu einer Formularzeile - notfalls einige Zeilen weiter unten."""
    for j in range(i, min(i + tiefe, len(block))):
        m = RE_ZAHL.search(_saeubern(block[j]))
        if m and any(c.isdigit() for c in m.group(1)):
            return int(m.group(1).replace(".", ""))
    return None


def anlagen_v(pfad: str) -> list[AnlageV]:
    """Alle Anlagen V der Erklaerung, jede gegen ihre eigenen Summen geprueft."""
    t = _text(pfad)
    anfaenge = [i for i, l in enumerate(t) if RE_ANLAGE_V.search(l)]
    if not anfaenge:
        raise ParseFehler(f"{pfad}: keine Anlage V gefunden.")
    # Die letzte Anlage V endet dort, wo die naechste Anlage beginnt - die
    # erste Erwaehnung im Dokument taugt dafuer nicht, sie steht als Querverweis
    # schon im Mantelbogen.
    ende = next((i for i, l in enumerate(t)
                 if i > anfaenge[-1] and RE_ENDE.match(l)), len(t))
    grenzen = anfaenge + [ende]

    aus = []
    for nr, (a, b) in enumerate(zip(grenzen, grenzen[1:]), 1):
        block = t[a:b]
        werte: dict[int, int] = {}
        for i, l in enumerate(block):
            s = l.strip()
            for zeile, muster in _MUSTER.items():
                if re.match(muster, s):
                    v = _wert(block, i)
                    if v is not None:
                        werte[zeile] = v       # spaeterer Treffer = Summenzeile
        ein = sum(werte.get(z, 0) for z in EINNAHMEN)
        wk = sum(werte.get(z, 0) for z in WERBUNGSKOSTEN)
        for name, zeile in SUMMEN.items():
            soll = {"einnahmen": ein, "werbungskosten": wk, "ergebnis": ein - wk}[name]
            if werte.get(zeile) != soll:
                raise ParseFehler(
                    f"Anlage V Nr. {nr}: gelesene {name} {soll} treffen Zeile {zeile} "
                    f"({werte.get(zeile)}) nicht. Der Parser hat sich verlesen - "
                    f"es wird nichts gebucht.")
        aus.append(AnlageV(nr, werte, ein, wk, ein - wk))
    return aus


# Mantelbogen: Zeile der Steuerberechnung -> (Konto, Spalte, im Haben?)
# Spalte 0 = Ehemann, 1 = Ehefrau, -1 = Gesamtspalte.
MANTEL = {
    "lohn_em":    (r"^Bruttoarbeitslohn ohne", "4010", 0, True),
    "lohn_ef":    (r"^Bruttoarbeitslohn ohne", "4020", 1, True),
    "wk_em":      (r"^- Werbungskosten ggf", "6010", 0, False),
    "wk_ef":      (r"^- Werbungskosten ggf", "6020", 1, False),
    "vorsorge":   (r"^- Höchstbetrag der Vorsorgeaufwendungen", "6300", -1, False),
    "sonderausg": (r"^- sonstige abzugsfähige Sonderausgaben", "6390", -1, False),
    "agb":        (r"^- außergewöhnliche Belastungen", "6360", -1, False),
}
# Zeilen, gegen die geprueft wird - sie werden nicht gebucht.
MANTEL_PROBE = {
    "gesamtbetrag": r"^Summe/Gesamtbetrag der Einkünfte",
    "zve": r"^Einkommen/zu versteuerndes Einkommen",
    "vuv": r"^Einkünfte aus Vermietung und Verpachtung",
}


def _zahlen(zeile: str) -> list[int]:
    return [int(x.replace(".", "")) for x in re.findall(r"-?[\d.]+(?=\s|$)", zeile)
            if any(c.isdigit() for c in x)]


def mantelbogen(pfad: str) -> dict[str, int]:
    """Die Steuerberechnung des Mantelbogens, gegen ihre eigenen Summen geprueft.

    Anders als bei der Anlage V steht hier keine Zeilennummer, sondern eine
    Beschriftung mit drei Spalten (Ehemann, Ehefrau, Gesamt). Die Probe ist
    dieselbe: was gelesen wurde, muss die ausgewiesenen Summen treffen.
    """
    t = _text(pfad)
    treffer: dict[str, list[int]] = {}
    for l in t:
        s = l.strip()
        for name, muster in ([(k, v[0]) for k, v in MANTEL.items()]
                             + list(MANTEL_PROBE.items())):
            if re.match(muster, s) and name not in treffer:
                treffer[name] = _zahlen(s)

    def hol(name: str, spalte: int) -> int:
        z = treffer.get(name) or []
        if spalte == -1:
            return z[-1] if z else 0
        return z[spalte] if len(z) > spalte else 0

    d = {name: hol(name, spalte) for name, (_, _, spalte, _) in MANTEL.items()}
    for name in MANTEL_PROBE:
        d[name] = hol(name, -1)

    einkuenfte = (d["lohn_em"] - d["wk_em"]) + (d["lohn_ef"] - d["wk_ef"]) + d["vuv"]
    if einkuenfte != d["gesamtbetrag"]:
        raise ParseFehler(
            f"Mantelbogen: gelesene Einkünfte {einkuenfte} treffen den "
            f"Gesamtbetrag {d['gesamtbetrag']} nicht.")
    zve = d["gesamtbetrag"] - d["vorsorge"] - d["sonderausg"] - d["agb"]
    if zve != d["zve"]:
        raise ParseFehler(
            f"Mantelbogen: gerechnetes zu versteuerndes Einkommen {zve} trifft "
            f"den Ausweis {d['zve']} nicht.")
    return d


def mantel_buchung(d: dict[str, int], jahr: int, gegenkonto: str = "2100"
                   ) -> tuple[str, str, list[tuple[str, int, int]]]:
    """Arbeitslohn, Sonderausgaben und außergewöhnliche Belastungen als eine Buchung."""
    zeilen: list[tuple[str, int, int]] = []
    for name, (_, konto, _, im_haben) in MANTEL.items():
        cent = d.get(name, 0) * 100
        if not cent:
            continue
        zeilen.append((konto, 0, cent) if im_haben else (konto, cent, 0))
    soll = sum(z[1] for z in zeilen)
    haben = sum(z[2] for z in zeilen)
    if soll != haben:
        zeilen.append((gegenkonto, 0, soll - haben) if soll > haben
                      else (gegenkonto, haben - soll, 0))
    return (f"{jahr}-12-31",
            f"Arbeitslohn, Sonderausgaben und agB aus der Erklärung {jahr}", zeilen)


def buchungszeilen(anlagen: list[AnlageV], jahr: int, gegenkonto: str = "2100"
                   ) -> list[tuple[str, str, list[tuple[str, int, int]]]]:
    """(datum, text, [(konto, soll_cent, haben_cent), ...]) je Objekt.

    Gegenbuchung auf den Ergebnisvortrag: das Vergleichsjahr traegt keine
    Zahlungsstroeme, es traegt nur das steuerliche Ergebnis. Die
    Vermoegensuebersicht dieses Jahres ist deshalb ohne Aussage - sie ist auch
    nicht ihr Zweck.
    """
    from ..kontenplan.privat import konto_nr

    def zeile(konto: str, cent: int, im_haben: bool) -> tuple[str, int, int]:
        # Ein negativer Betrag steht auf der anderen Seite - die Erklaerung
        # kennt negative Einnahmen (Zeile 21: geleistete Erstattungen).
        if cent < 0:
            im_haben = not im_haben
            cent = -cent
        return (konto, 0, cent) if im_haben else (konto, cent, 0)

    aus = []
    for a in anlagen:
        zeilen: list[tuple[str, int, int]] = []
        for z in EINNAHMEN:
            if betrag := a.zeilen.get(z, 0):
                zeilen.append(zeile(konto_nr(a.nr, z, True), betrag * 100, True))
        for summenzeile, kontozeile in WERBUNGSKOSTEN.items():
            if betrag := a.zeilen.get(summenzeile, 0):
                zeilen.append(zeile(konto_nr(a.nr, kontozeile, False), betrag * 100, False))
        soll = sum(z[1] for z in zeilen)
        haben = sum(z[2] for z in zeilen)
        if soll > haben:
            zeilen.append((gegenkonto, 0, soll - haben))
        elif haben > soll:
            zeilen.append((gegenkonto, haben - soll, 0))
        aus.append((f"{jahr}-12-31", f"Anlage V Nr. {a.nr} aus der Erklärung {jahr}",
                    zeilen))
    return aus
