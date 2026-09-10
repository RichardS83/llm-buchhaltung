# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Kontenrahmen je Mandant.

Welchen Rahmen ein Mandant führt, steht in `mandant.kontenrahmen`. Ein Rahmen
bringt die Konten *und* die Gliederung von Bilanz und GuV mit — beides gehört
zusammen, weil jedes Konto über seinen `posten` auf eine Gliederungszeile zeigt.
Zwei Rahmen sind hinterlegt:

    SKR04-HOLDING   Beteiligungsholding ohne Umsatzsteuer
    SKR04-OPERATIV  operative Kapitalgesellschaft, mit Umsatzsteuer
                    und Reverse Charge
    PRIVAT-ESt   private Einkommensteuer — doppelte Buchführung, aber
                 Überschussrechnung nach § 11 EStG statt Bilanzierung

Ein Konto ist ein Tupel:

    (nummer, bezeichnung, typ, posten, steuer_tag, ustva_kz)

    typ         A Aktiva · P Passiva · E Ertrag · X Aufwand
    posten      Gliederungszeile in Bilanz oder GuV
    steuer_tag  Kennzeichen für die körperschaftsteuerliche Überleitung
    ustva_kz    Kennzahl der Umsatzsteuer-Voranmeldung, auf die das Konto zahlt

Die letzte Spalte darf fehlen; Rahmen ohne Umsatzsteuer schreiben sie nicht.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import holding, operativ, privat


@dataclass(frozen=True)
class Rahmen:
    name: str
    konten: list[tuple]
    bilanz_aktiva: list[tuple]
    bilanz_passiva: list[tuple]
    guv: list[tuple]
    ertrags_posten: frozenset[str]
    # Zeile der Passivseite, die das Ergebnis des Jahres aufnimmt. Es steht
    # noch auf keinem Bestandskonto, sondern in den Erfolgskonten.
    ergebnis_zeile: str = "Bilanzgewinn"
    # Wie die beiden Rechenwerke heißen. Bei der Kapitalgesellschaft sind es
    # Bilanz und GuV, bei der Einkommensteuer Vermögensübersicht und
    # Überschussrechnung - dasselbe Verfahren, anderer Rechtsgrund.
    titel_bilanz: str = "Bilanz"
    titel_guv: str = "Gewinn- und Verlustrechnung"
    titel_ergebnis: str = "Jahresüberschuss / Jahresfehlbetrag"
    # Wie die Steuerseite heißt. Die Kapitalgesellschaft erklärt Körperschaft-
    # und Gewerbesteuer, die Zusammenveranlagung Einkommensteuer.
    titel_steuern: str = "Körperschaft- und Gewerbesteuererklärung"
    # Steuerliche Anlagen (Anlage V je Objekt). Leer bei Kapitalgesellschaften -
    # die haben keine Anlagen, sondern einen Jahresabschluss.
    anlagen: tuple = ()
    # Konten, die ohne Geldkonto bebucht werden duerfen, und die Geldkonten
    # selbst. Nur gefuellt, wo die Ueberschussrechnung nach § 11 EStG gilt.
    ohne_zahlung: frozenset[str] = frozenset()
    geldkonten: frozenset[str] = frozenset()

    @property
    def ueberschussrechnung(self) -> bool:
        """Rechnet dieser Rahmen nach § 11 EStG statt nach HGB?"""
        return bool(self.geldkonten)


def _aus_modul(name: str, modul, stammdaten: dict | None = None) -> Rahmen:
    # Ein Rahmen kann objektabhaengige Teile haben - die Anlagen V der privaten
    # Einkommensteuer haengen an den Mietobjekten des Mandanten. Solche Module
    # bringen `baue` mit und bekommen die Stammdaten aus der mandant.json.
    sd = stammdaten or {}
    dyn = modul.baue(sd) if hasattr(modul, "baue") else {}
    roh = dyn.get("konten", getattr(modul, "KONTEN", None))
    # Auf sechs Spalten auffüllen, damit ein Rahmen ohne Umsatzsteuer dieselbe
    # Form hat wie einer mit.
    konten = [tuple(k) + (None,) * (6 - len(k)) for k in roh]
    # Kontobezeichnungen sind Mandantendaten: welches Geldinstitut, welcher
    # Vertrag, welches Objekt. Der Rahmen gibt eine sachliche Vorgabe, die
    # mandant.json darf sie je Kontonummer ueberschreiben.
    eigene = sd.get("kontobezeichnungen") or {}
    if eigene:
        konten = [(k[0], eigene.get(k[0], k[1]), *k[2:]) for k in konten]
    return Rahmen(
        name=name,
        konten=konten,
        bilanz_aktiva=modul.BILANZ_AKTIVA,
        bilanz_passiva=modul.BILANZ_PASSIVA,
        guv=dyn.get("guv", getattr(modul, "GUV", None)),
        ertrags_posten=frozenset(dyn.get("ertrags_posten",
                                         getattr(modul, "ERTRAGS_POSTEN", ()))),
        ergebnis_zeile=getattr(modul, "ERGEBNIS_ZEILE", "Bilanzgewinn"),
        titel_bilanz=getattr(modul, "TITEL_BILANZ", "Bilanz"),
        titel_guv=getattr(modul, "TITEL_GUV", "Gewinn- und Verlustrechnung"),
        titel_ergebnis=getattr(modul, "TITEL_ERGEBNIS",
                               "Jahresüberschuss / Jahresfehlbetrag"),
        titel_steuern=getattr(modul, "TITEL_STEUERN",
                              "Körperschaft- und Gewerbesteuererklärung"),
        anlagen=tuple(dyn.get("anlagen", getattr(modul, "ANLAGEN", ()))),
        ohne_zahlung=frozenset(dyn.get("ohne_zahlung",
                                       getattr(modul, "OHNE_ZAHLUNG", ()))),
        geldkonten=frozenset(getattr(modul, "GELDKONTEN", ())),
    )


MODULE = {
    "SKR04-HOLDING": holding,
    "SKR04-OPERATIV": operativ,
    "PRIVAT-ESt": privat,
}


def lade(name: str | None, stammdaten: dict | None = None) -> Rahmen:
    """Den Rahmen `name` bauen, angereichert um die Stammdaten des Mandanten.

    Ohne Stammdaten kommt ein vollstaendiger, aber unpersoenlicher Rahmen
    zurueck - brauchbar zum Rechnen, nicht zum Buchen eines echten Bestands.
    """
    if name not in MODULE:
        bekannt = ", ".join(sorted(MODULE))
        raise KeyError(f"Kontenrahmen '{name}' unbekannt. Vorhanden: {bekannt}")
    return _aus_modul(name, MODULE[name], stammdaten)


def aus_mandant(m) -> Rahmen:
    """Den Rahmen zu einer Mandantenzeile der Datenbank bauen.

    `m` ist ein sqlite3.Row mit mindestens `kontenrahmen`; traegt es
    `stammdaten`, fliessen sie in den Rahmen ein.
    """
    roh = m["stammdaten"] if "stammdaten" in m.keys() else None
    return lade(m["kontenrahmen"], json.loads(roh) if roh else None)
