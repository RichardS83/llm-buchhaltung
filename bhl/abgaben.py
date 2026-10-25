# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Was fuer ein Geschaeftsjahr abzugeben ist - und was davon noch fehlt.

Ein Jahresabschluss ist nicht eine Abgabe, sondern ein Buendel: aufstellen,
unterschreiben und feststellen, hinterlegen, Koerperschaftsteuererklaerung
mit ihren Anlagen und der Feststellung des Einlagekontos,
Gewerbesteuererklaerung, E-Bilanz - und wer Loehne gezahlt hat, die
Lohnsteuerbescheinigungen. Jede dieser Abgaben hat einen anderen Empfaenger,
einen anderen Weg und eine andere Frist, und jede kann aus einem anderen
Grund noch nicht fertig sein.

Dieses Modul fuehrt sie zusammen. Es rechnet nichts neu, sondern fragt die
Stellen, die es schon gibt:

* `fristen`   - welche Abgaben anfallen, bis wann, und ob sie erledigt sind;
* `steuern`   - ob die Ueberleitung aufgeht und keine Platzhalter traegt;
* `elster`    - ob jede Formularzeile einen Wert hat, der an keiner offenen
                Entscheidung haengt, und ob die Gegenproben aufgehen;
* `ebilanz`   - ob der Kontennachweis vollstaendig und geprueft ist;
* `offen`     - welche offenen Punkte eine Abgabe ausdruecklich blockieren
                (`blockiert: [art, ...]` in offene_punkte.json).

Daraus ergibt sich je Abgabe ein Zustand: `erledigt`, `laeuft` (das
Geschaeftsjahr ist noch nicht zu Ende - abzugeben ist noch nichts), `bereit`
(alles da, es fehlt nur noch das Absenden) oder `blockiert` mit der Liste der
Hindernisse.
"""

from __future__ import annotations

import datetime
import os
import sqlite3

from . import ebilanz as ebilanz_mod
from . import elster as elster_mod
from . import fristen, reports, steuern
from . import offen as offen_mod

ERLEDIGT_PUNKT = ("behoben", "erledigt", "entschieden")

# Weg und Empfaenger sind Gesetz und Verwaltungspraxis, keine Mandantendaten.
KATALOG = {
    "jahresabschluss": {
        "titel": "Jahresabschluss aufstellen",
        "empfaenger": "Geschäftsführung (intern)",
        "weg": "aus der Buchführung erzeugt",
        "unterlagen": [("dokument", "jahresabschluss", "Jahresabschluss")],
    },
    "feststellung": {
        "titel": "Jahresabschluss unterschreiben und feststellen",
        "empfaenger": "Gesellschafterversammlung",
        "weg": "Ausdruck, Unterschrift der Geschäftsführung (§ 245 HGB), "
               "Feststellungsbeschluss (§ 46 Nr. 1, § 48 Abs. 3 GmbHG)",
        "voraussetzung": ["jahresabschluss"],
        "unterlagen": [("dokument", "jahresabschluss", "Jahresabschluss"),
                       ("dokument", "feststellungsbeschluss", "Feststellungsbeschluss (Entwurf)")],
    },
    "offenlegung": {
        "titel": "Jahresabschluss hinterlegen / offenlegen",
        "empfaenger": "Unternehmensregister (Publikations-Plattform)",
        "weg": "Webformular der Publikations-Plattform des Bundesanzeiger Verlags",
        "voraussetzung": ["feststellung"],
        "unterlagen": [("dokument", "jahresabschluss", "Jahresabschluss (verkürzte Bilanz)")],
    },
    "koerperschaftsteuer": {
        "titel": "Körperschaftsteuererklärung mit Feststellung des Einlagekontos",
        "empfaenger": "Finanzamt",
        "weg": "Mein ELSTER",
        "unterlagen": [("seite", "steuern", "Steuerliche Überleitung"),
                       ("seite", "elster", "ELSTER-Ausfüllhilfe")],
    },
    "gewerbesteuer": {
        "titel": "Gewerbesteuererklärung",
        "empfaenger": "Finanzamt (Betriebsstättenfinanzamt)",
        "weg": "Mein ELSTER",
        "unterlagen": [("seite", "elster", "ELSTER-Ausfüllhilfe")],
    },
    "ebilanz": {
        "titel": "E-Bilanz (§ 5b EStG)",
        "empfaenger": "Finanzamt",
        "weg": "XBRL-Datensatz über ERiC oder Drittsoftware — Mein ELSTER hat kein Formular",
        "unterlagen": [("seite", "ebilanz", "Kontennachweis und Taxonomie")],
    },
    "lohnsteuerbescheinigung": {
        "titel": "Lohnsteuerbescheinigungen",
        "empfaenger": "Finanzamt",
        "weg": "elektronisch durch das Lohnbüro (§ 41b EStG)",
    },
    "umsatzsteuer_jahr": {"titel": "Umsatzsteuererklärung", "empfaenger": "Finanzamt",
                          "weg": "Mein ELSTER"},
    "einkommensteuer": {"titel": "Einkommensteuererklärung", "empfaenger": "Finanzamt",
                        "weg": "Mein ELSTER"},
}


def paket(con: sqlite3.Connection, gj_id: int, mandant: dict, mandant_dir: str,
          jahr_dir: str, jahr: int, gj: dict, heute: datetime.date,
          kapitalgesellschaft: bool, umsatzsteuerpflichtig: bool,
          lohnzahlung: bool) -> dict:
    termine = fristen.jahrespflichten(mandant_dir, jahr, gj["ende"], heute,
                                      kapitalgesellschaft, umsatzsteuerpflichtig,
                                      lohnzahlung=lohnzahlung)
    stand = {t["art"]: t for t in termine}

    st = steuern.laden(jahr_dir)
    if st:
        st = steuern.pruefen(con, gj_id, st, reports.ergebnis(con, gj_id))
    el = elster_mod.auswerten(con, gj_id, mandant, jahr_dir, gj)
    eb = ebilanz_mod.auswerten(con, gj_id, mandant_dir) if kapitalgesellschaft else None
    punkte = offen_mod.laden(jahr_dir).get("punkte", [])
    vorhanden = {d for d in ("jahresabschluss", "feststellungsbeschluss")
                 if os.path.exists(os.path.join(
                     jahr_dir, _DATEIEN[d].format(jahr=jahr)))}

    # Vor dem Ende des Geschaeftsjahres ist kein Jahresabschluss und keine
    # Jahreserklaerung "bereit" - auch wenn sonst kein Hindernis zu finden ist.
    ende = datetime.date.fromisoformat(str(gj["ende"])[:10])
    laeuft = heute <= ende

    abgaben = []
    for t in termine:
        art = t["art"]
        kat = KATALOG.get(art, {"titel": t["bezeichnung"]})
        hindernisse = []
        for vor in kat.get("voraussetzung", []):
            v = stand.get(vor)
            if v and v["status"] not in ("erledigt", "entfaellt"):
                hindernisse.append({"text": f"wartet auf: {KATALOG[vor]['titel']}",
                                    "art": "voraussetzung"})
        if art in ("koerperschaftsteuer", "gewerbesteuer") and st:
            for p in st.get("platzhalter", []):
                hindernisse.append({"text": f"Platzhalter in der Überleitung: {p['zeile']}",
                                    "art": "platzhalter", "verweis": "#/steuern"})
            if st.get("abweichungen"):
                hindernisse.append({"text": f"{st['abweichungen']} Zeile(n) der Überleitung "
                                            "weichen von den Konten ab",
                                    "art": "abweichung", "verweis": "#/steuern"})
        formulare = []
        if el and art in ("koerperschaftsteuer", "gewerbesteuer"):
            for f in el["formulare"]:
                if f.get("abgabe") != art:
                    continue
                formulare.append({"id": f["id"], "titel": f["titel"], "name": f["name"],
                                  "bereit": f["bereit"], "offen": f["offen"]})
            for gp in el.get("gegenproben", []):
                if not gp["ok"]:
                    hindernisse.append({"text": f"Gegenprobe geht nicht auf: {gp['text']}",
                                        "art": "gegenprobe", "verweis": "#/elster"})
        elif art in ("koerperschaftsteuer", "gewerbesteuer"):
            hindernisse.append({"text": "Keine Ausfüllhilfe (elster.json) für dieses Jahr",
                                "art": "fehlt"})
        if art == "ebilanz":
            if eb is None:
                hindernisse.append({"text": "Keine Taxonomie-Zuordnung (ebilanz.csv)",
                                    "art": "fehlt"})
            else:
                for p in eb["pruefungen"]:
                    if not p["ok"]:
                        hindernisse.append({"text": f"{p['text']}: {p['detail']}",
                                            "art": "ebilanz", "verweis": "#/ebilanz"})
        for d in ("jahresabschluss", "feststellungsbeschluss"):
            if (art == "feststellung" and d not in vorhanden):
                hindernisse.append({"text": f"Dokument fehlt: {_DATEIEN[d].format(jahr=jahr)}",
                                    "art": "fehlt"})
        for p in punkte:
            if art in (p.get("blockiert") or []) and p.get("status") not in ERLEDIGT_PUNKT:
                hindernisse.append({"text": p["titel"], "art": "punkt", "punkt": p["id"],
                                    "wer": p.get("wer"), "aktion": p.get("aktion")})
        schritt = next((p for p in punkte if art in (p.get("fuer") or [])
                        and p.get("status") not in ERLEDIGT_PUNKT), None)

        erledigt = t["status"] in ("erledigt", "entfaellt")
        if laeuft and not erledigt:
            hindernisse.insert(0, {"text": f"Geschäftsjahr läuft noch bis "
                                           f"{ende:%d.%m.%Y}", "art": "zeitraum"})
        abgaben.append({
            "art": art, "titel": f"{kat['titel']} {jahr}", "bezeichnung": t["bezeichnung"],
            "empfaenger": (mandant.get("finanzamt") and kat.get("empfaenger", "").replace(
                "Finanzamt", f"Finanzamt {mandant['finanzamt']}", 1)) or kat.get("empfaenger"),
            "weg": kat.get("weg"), "frist": t["faellig"],
            "gesetzlich_faellig": t.get("gesetzlich_faellig"), "grundlage": t["grundlage"],
            "tage": t["tage"], "frist_status": t["status"],
            "erledigt_am": t.get("erledigt_am"), "nachweis": t.get("nachweis"),
            "notiz": t.get("notiz"),
            "zustand": ("erledigt" if erledigt else "laeuft" if laeuft
                        else "bereit" if not hindernisse else "blockiert"),
            "hindernisse": [] if erledigt else hindernisse,
            "formulare": formulare,
            "unterlagen": [{"art": u[0], "ziel": u[1], "titel": u[2]}
                           for u in kat.get("unterlagen", [])],
            "schritt": ({"punkt": schritt["id"], "aktion": schritt.get("aktion")}
                        if schritt and not erledigt else None),
        })
    reihenfolge = list(KATALOG)
    abgaben.sort(key=lambda a: (a["zustand"] == "erledigt",
                                reihenfolge.index(a["art"]) if a["art"] in reihenfolge else 99))
    return {
        "jahr": jahr, "abgaben": abgaben,
        "zaehler": {z: sum(1 for a in abgaben if a["zustand"] == z)
                    for z in ("erledigt", "laeuft", "bereit", "blockiert")},
        "elster_bereit": el["bereit"] if el else None,
        "ebilanz_vollstaendig": eb["vollstaendig"] if eb else None,
    }


_DATEIEN = {
    "jahresabschluss": "jahresabschluss_{jahr}.md",
    "feststellungsbeschluss": "entwuerfe/feststellungsbeschluss_{jahr}.md",
}
