# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Ausfuellhilfe fuer Mein ELSTER - Formular, Zeile, Wert, Herkunft.

Die Erklaerung wird von Hand in Mein ELSTER uebertragen. Dieses Modul sagt fuer
jede Zeile, was einzutragen ist - und rechnet den Wert bei jedem Aufruf aus der
Buchfuehrung, statt ihn abzuschreiben. Die Zuordnung steht in
`<mandant>/<jahr>/elster.json`; sie enthaelt keine Betraege, nur Verweise:

    {"text": "Berlin"}                       feste Angabe (Name, Ja/Nein, Ort)
    {"stamm": "anteilseigner.0.name"}        aus mandant.json
    {"gj": "beginn"}                         Wirtschaftsjahr von/bis
    {"guv": "ergebnis"}                      Jahresergebnis der GuV
    {"konten": ["7600", "7603"]}             Summe der Kontensalden (Soll - Haben)
    {"buchungen": {"konto": "4852", "belegfeld": ["KA94"]}, "anteil": [200, 760]}
                                             Teil der Buchungen eines Kontos, ggf.
                                             anteilig (Buchwert je Verkauf)
    {"kennzahl": "zve"}                      markierte Zeile aus steuern.json
    {"summe": ["feld-id", ...]}              Summe anderer Felder dieser Datei
    {"differenz": ["a", "b"]}                Feld a minus Feld b
    {"prozent": ["feld-id", 5]}              Prozentsatz eines anderen Feldes

`vorzeichen: -1` dreht das Vorzeichen (Ertraege stehen im Haben). `format`
ist `euro` (volle Euro, kaufmaennisch gerundet), `cent` oder `text`.

Ein Feld darf `offen: "<Punkt-ID>"` tragen: der Wert ist berechnet, haengt
aber an einer Entscheidung oder Unterlage aus offene_punkte.json. Solange der
Punkt nicht erledigt ist, gilt das Formular als nicht eingabefertig - aus
demselben Grund, aus dem ein Platzhalter die Abgabe sperrt.

Die Zeilennummern stehen in der Datei, nicht im Code: sie verschieben sich
von Jahr zu Jahr, und `zeilen_stand` sagt, gegen welchen Vordruck sie geprueft
sind.
"""

from __future__ import annotations

import json
import os
import sqlite3
from decimal import ROUND_HALF_UP, Decimal

from . import offen as offen_mod
from . import reports, steuern
from .ledger import salden

ERLEDIGT = ("behoben", "erledigt", "entschieden")


def laden(jahr_dir: str) -> dict | None:
    pfad = os.path.join(jahr_dir, "elster.json")
    if not os.path.exists(pfad):
        return None
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)


def euro(cent: int) -> int:
    """Volle Euro, kaufmaennisch gerundet - in ganzen Zahlen, ohne float."""
    return int((Decimal(cent) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _stamm(mandant: dict, pfad: str):
    wert = mandant
    for teil in pfad.split("."):
        if isinstance(wert, list):
            wert = wert[int(teil)] if teil.isdigit() and int(teil) < len(wert) else None
        elif isinstance(wert, dict):
            wert = wert.get(teil)
        else:
            return None
    return wert


class Auswerter:
    def __init__(self, con: sqlite3.Connection, gj_id: int, mandant: dict,
                 jahr_dir: str, gj: dict):
        self.con, self.gj_id, self.mandant, self.gj = con, gj_id, mandant, gj
        self.salden = {r["nummer"]: r["saldo"] for r in salden(con, gj_id)}
        spec = steuern.laden(jahr_dir)
        self.kennzahlen = steuern.kennzahlen(spec) if spec else {}
        self.werte: dict[str, int] = {}

    def buchungen(self, b: dict) -> tuple[int, list[str]]:
        bf = b.get("belegfeld") or []
        platz = ",".join("?" * len(bf))
        sql = ("SELECT COALESCE(SUM(z.soll_cent - z.haben_cent), 0) FROM buchungszeile z"
               " JOIN buchung bu ON bu.id = z.buchung_id JOIN konto k ON k.id = z.konto_id"
               " WHERE bu.gj_id = ? AND k.nummer = ?")
        args: list = [self.gj_id, b["konto"]]
        if bf:
            sql += f" AND bu.belegfeld IN ({platz})"
            args += bf
        return self.con.execute(sql, args).fetchone()[0], [b["konto"]]

    def wert(self, w: dict) -> tuple[object, list[str]]:
        """(Wert, Konten fuer das Aufklappen). Betraege in Cent."""
        if "text" in w:
            return w["text"], []
        if "stamm" in w:
            return _stamm(self.mandant, w["stamm"]), []
        if "gj" in w:
            iso = self.gj[w["gj"]]
            return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}", []
        if "guv" in w:
            return reports.ergebnis(self.con, self.gj_id), ["GuV"]
        if "konten" in w:
            return sum(self.salden.get(k, 0) for k in w["konten"]), list(w["konten"])
        if "buchungen" in w:
            betrag, konten = self.buchungen(w["buchungen"])
            if "anteil" in w:
                teil, ganz = w["anteil"]
                betrag = int((Decimal(betrag) * teil / ganz).quantize(
                    Decimal(1), rounding=ROUND_HALF_UP))
            return betrag, konten
        if "kennzahl" in w:
            return self.kennzahlen.get(w["kennzahl"]), []
        if "summe" in w:
            return sum(self.werte.get(i, 0) for i in w["summe"]), []
        if "differenz" in w:
            a, b = w["differenz"]
            return self.werte.get(a, 0) - self.werte.get(b, 0), []
        if "prozent" in w:
            basis, satz = w["prozent"]
            return int((Decimal(self.werte.get(basis, 0)) * Decimal(str(satz)) / 100)
                       .quantize(Decimal(1), rounding=ROUND_HALF_UP)), []
        raise ValueError(f"unbekannte Wertangabe {w}")


def auswerten(con: sqlite3.Connection, gj_id: int, mandant: dict, jahr_dir: str,
              gj: dict) -> dict | None:
    spec = laden(jahr_dir)
    if spec is None:
        return None
    punkte = {p["id"]: p for p in offen_mod.laden(jahr_dir).get("punkte", [])}
    aw = Auswerter(con, gj_id, mandant, jahr_dir, gj)
    # Hilfsfelder erscheinen in keinem Formular, werden aber von Summen
    # gebraucht (etwa ein Betrag mit umgekehrtem Vorzeichen).
    for h in spec.get("hilfsfelder", []):
        wert, _ = aw.wert(h["wert"])
        aw.werte[h["id"]] = -wert if h["wert"].get("vorzeichen") == -1 else wert
    for f in spec.get("formulare", []):
        offen_felder = 0
        for feld in f.get("felder", []):
            if "wert" not in feld:
                continue                       # Gruppenueberschrift
            v = feld["wert"]
            wert, konten = aw.wert(v)
            fmt = feld.get("format") or ("text" if not isinstance(wert, int) else "euro")
            if isinstance(wert, int) and v.get("vorzeichen") == -1:
                wert = -wert
            if feld.get("id") and isinstance(wert, int):
                aw.werte[feld["id"]] = wert
            feld["betrag_cent"] = wert if isinstance(wert, int) else None
            feld["eintrag"] = (euro(wert) if isinstance(wert, int) and fmt == "euro"
                               else wert)
            feld["format"] = fmt
            feld["konten"] = [k for k in konten if k != "GuV"]
            feld["fehlt"] = wert is None or wert == ""
            p = punkte.get(feld.get("offen")) if feld.get("offen") else None
            feld["offen_punkt"] = ({"id": p["id"], "titel": p["titel"], "status": p["status"]}
                                   if p and p.get("status") not in ERLEDIGT else None)
            if feld["fehlt"] or feld["offen_punkt"]:
                offen_felder += 1
        f["offen"] = offen_felder
        f["bereit"] = offen_felder == 0
    # Nachfelder fassen Formularfelder fuer die Gegenproben zusammen - etwa
    # die fuenf NVIDIA-Verkaeufe, die zusammen den Kontensaldo ergeben muessen.
    for h in spec.get("nachfelder", []):
        aw.werte[h["id"]], _ = aw.wert(h["wert"])
    # Gegenproben: eine Zeile der Ausfuellhilfe muss gleich der Zeile der
    # steuerlichen Ueberleitung sein, aus der sie stammt. Sonst sind
    # Ausfuellhilfe und Rechnung auseinandergelaufen.
    for p in spec.get("gegenproben", []):
        ist = aw.werte.get(p["feld"])
        if "kennzahl" in p:
            soll = aw.kennzahlen.get(p["kennzahl"])
        else:
            soll, _ = aw.wert({"konten": p["konten"]})
            soll = -soll if p.get("vorzeichen") == -1 else soll
        p["ist"], p["soll"] = ist, soll
        p["ok"] = ist is not None and soll is not None and ist == soll
    spec["bereit"] = (all(f["bereit"] for f in spec.get("formulare", []))
                      and all(p["ok"] for p in spec.get("gegenproben", [])))
    return spec
