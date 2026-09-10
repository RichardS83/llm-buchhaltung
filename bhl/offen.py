"""Offene Punkte, Fristen und fehlende Belege.

Zwei Quellen, die sich ergaenzen:

* **`<mandant>/<jahr>/offene_punkte.json`** haelt fest, was jemand entscheiden,
  anfordern oder fristgerecht einreichen muss - im Klartext, mit Adressat,
  Frist und der Wirkung in Euro. Das laesst sich nicht ableiten, das weiss nur
  der Mensch, der den Abschluss gemacht hat.

* **Die Buchfuehrung selbst** weiss, welche Buchung keinen Beleg hat. Diese
  Liste wird *nicht* gepflegt, sondern bei jedem Aufruf frisch gezogen -
  sonst behauptet sie irgendwann etwas, das nicht mehr stimmt. Aus der
  JSON-Datei kommt dazu nur die Anmerkung: wo der Beleg zu holen ist
  (`beschaffung`) oder warum keiner noetig ist (`unkritisch`).

Die Trennung ist der ganze Punkt. Am 25.07.2026 stand die groesste Luecke des
Abschlusses - die fehlenden Moss-Kartenumsaetze - nur in einer Markdown-Datei,
die niemand oeffnet, waehrend die Oberflaeche einen abgeschlossenen Abschluss
zeigte.
"""

from __future__ import annotations

import json
import os
import sqlite3

RANG_STATUS = {"offen": 0, "teilweise": 1, "entschieden": 2, "behoben": 3, "erledigt": 4}
RANG_PRIO = {"hoch": 0, "mittel": 1, "niedrig": 2}


def laden(jahr_dir: str) -> dict:
    pfad = os.path.join(jahr_dir, "offene_punkte.json")
    if not os.path.exists(pfad):
        return {"punkte": [], "beleg_notizen": {}}
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)


def _buchungen_ohne_beleg(con: sqlite3.Connection, gj_id: int) -> list[dict]:
    rows = con.execute("""
        SELECT b.id, b.nummer, b.datum, b.belegfeld, b.buchungstext, b.art,
               (SELECT COALESCE(SUM(soll_cent), 0) FROM buchungszeile z
                 WHERE z.buchung_id = b.id) AS betrag_cent
        FROM buchung b
        WHERE b.gj_id = ?
          AND NOT EXISTS (SELECT 1 FROM buchung_beleg v WHERE v.buchung_id = b.id)
        ORDER BY b.datum, b.nummer""", (gj_id,)).fetchall()
    return [dict(r) for r in rows]


def _notiz(notizen: dict, belegfeld: str | None) -> dict:
    """Anmerkung zu einem Belegfeld - exakt, sonst ueber den Praefix.

    Mehrere Buchungen teilen sich ein Belegfeld (`ST-2025` steht an zwei
    Abschlussbuchungen). Ein Eintrag deckt dann beide ab.
    """
    if not belegfeld:
        return {}
    if belegfeld in notizen:
        return notizen[belegfeld]
    for schluessel, wert in notizen.items():
        if belegfeld.startswith(schluessel):
            return wert
    return {}


def uebersicht(con: sqlite3.Connection, gj_id: int, jahr_dir: str,
               heute: str | None = None) -> dict:
    spec = laden(jahr_dir)
    punkte = list(spec.get("punkte", []))
    punkte.sort(key=lambda p: (RANG_STATUS.get(p.get("status"), 9),
                               RANG_PRIO.get(p.get("prioritaet"), 9),
                               p.get("frist") or "9999",
                               p.get("id", "")))
    if heute:
        for p in punkte:
            if p.get("frist"):
                p["ueberfaellig"] = p["frist"] < heute

    notizen = spec.get("beleg_notizen", {})
    fehlend = []
    for b in _buchungen_ohne_beleg(con, gj_id):
        n = _notiz(notizen, b.get("belegfeld"))
        b["beschaffung"] = n.get("beschaffung")
        b["unkritisch"] = n.get("unkritisch")
        fehlend.append(b)

    offen_zahl = sum(1 for p in punkte if p.get("status") in ("offen", "teilweise"))
    return {
        "jahr": spec.get("jahr"),
        "stand": spec.get("stand"),
        "einleitung": spec.get("einleitung"),
        "punkte": punkte,
        "fehlende_belege": fehlend,
        "zaehler": {
            "offen": offen_zahl,
            "gesamt": len(punkte),
            "belege_offen": sum(1 for b in fehlend if not b["unkritisch"]),
            "belege_gesamt": len(fehlend),
        },
    }
