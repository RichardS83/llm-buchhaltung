# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Deine Aufgaben - alles, was einen Menschen braucht, auf einer Liste.

Die Anwendung kennt viele Stellen, an denen etwas offen ist: Fristen,
Platzhalter, offene Punkte, fehlende Belege, Abgaben, die an einer Entscheidung
haengen. Jede dieser Stellen hat ihre eigene Seite, und jede ist fuer sich
richtig. Wer aber wissen will, *was er heute tun muss*, muesste sie alle
oeffnen und im Kopf zusammenfuehren.

Diese Liste tut das. Sie erfindet nichts dazu, sondern sammelt ein:

* jede Abgabe des Jahres, die nicht erledigt ist - mit dem naechsten Schritt
  und den Punkten, auf die sie wartet;
* jeden offenen Punkt mit `wer: "du"` - mit seiner `aktion`, also dem,
  was konkret zu tun ist, nicht nur dem Befund;
* jede offene Frist, die nicht schon als Abgabe des Jahres dasteht -
  Voranmeldungen, Zusammenfassende Meldungen, Abgaben anderer Jahre;
* jeden Platzhalter der Steuerrechnung;
* die Buchungen ohne Beleg.

Was ein Dritter liefern muss (`wer: "extern"`) oder die Anwendung selbst
erledigt (`wer: "app"`), steht getrennt darunter: es braucht keine Handlung,
aber Aufmerksamkeit, falls es ausbleibt.
"""

from __future__ import annotations

ERLEDIGT_PUNKT = ("behoben", "erledigt", "entschieden")
RANG_PRIO = {"hoch": 0, "mittel": 1, "niedrig": 2}


def _sortierschluessel(a: dict) -> tuple:
    return (not a.get("ueberfaellig"),
            a.get("frist") or "9999-12-31",
            RANG_PRIO.get(a.get("prioritaet"), 1),
            a.get("titel", ""))


def sammeln(paket: dict | None, offene: dict, termine: dict, steuer: dict | None,
            heute: str, jahr: int) -> dict:
    """Aufgaben aus Abgabepaket, offenen Punkten, Fristen und Steuerrechnung."""
    aufgaben, im_blick = [], []
    punkte = {p["id"]: p for p in offene.get("punkte", [])}

    # 1. Abgaben des Jahres
    if paket:
        for a in paket["abgaben"]:
            if a["zustand"] == "erledigt":
                continue
            wartet = [h["punkt"] for h in a["hindernisse"] if h.get("punkt")]
            andere = [h["text"] for h in a["hindernisse"] if not h.get("punkt")]
            # Solange das Geschaeftsjahr laeuft, ist eine Jahresabgabe keine
            # Aufgabe, sondern etwas, das man im Blick behaelt.
            (im_blick if a["zustand"] == "laeuft" else aufgaben).append({
                "art": "abgabe", "titel": a["titel"], "frist": a["frist"],
                "ueberfaellig": a["tage"] < 0, "tage": a["tage"],
                "prioritaet": "hoch", "zustand": a["zustand"],
                "aktion": (a["schritt"] or {}).get("aktion")
                          or f"{a['weg']} — an {a['empfaenger']}",
                "wartet_auf": wartet, "hindernisse": andere,
                "quelle": (a["schritt"] or {}).get("punkt"),
                "verweis": "#/abgaben",
            })

    # 2. Offene Punkte. Frist-Punkte, die schon als Abgabe dastehen, nicht doppelt.
    abgedeckt = {p for a in (paket or {}).get("abgaben", [])
                 for p in [((a.get("schritt") or {}).get("punkt"))] if p}
    fehlende_belege = [b for b in offene.get("fehlende_belege", []) if not b.get("unkritisch")]
    belege_untergebracht = False
    # Ein Punkt ohne eigene Frist, der eine Abgabe blockiert, erbt deren Frist:
    # die Entscheidung muss vor der Abgabe fallen, nicht irgendwann.
    abgabe_frist = {a["art"]: a["frist"] for a in (paket or {}).get("abgaben", [])
                    if a["zustand"] != "erledigt"}
    # Ohne Abgabepaket (private Erklaerung) kommt die Frist aus der Fristenliste.
    for t in termine.get("eintraege", []):
        if (t["status"] in ("offen", "verlaengert", "ueberfaellig")
                and t["bezeichnung"].endswith(str(jahr))):
            abgabe_frist.setdefault(t["art"], t["faellig"])
    abgabe_titel = {a["art"]: a["titel"] for a in (paket or {}).get("abgaben", [])}
    for p in offene.get("punkte", []):
        if p.get("status") in ERLEDIGT_PUNKT or p["id"] in abgedeckt:
            continue
        blockiert = [b for b in (p.get("blockiert") or [])]
        geerbt = min((abgabe_frist[b] for b in blockiert if b in abgabe_frist), default=None)
        frist = p.get("frist") or geerbt
        eintrag = {
            "art": "punkt", "titel": p["titel"], "quelle": p["id"],
            "frist": frist, "frist_geerbt": bool(geerbt and not p.get("frist")),
            "blockiert_titel": [abgabe_titel.get(b, b) for b in blockiert],
            "ueberfaellig": bool(frist and frist < heute),
            "prioritaet": p.get("prioritaet"), "status": p.get("status"),
            "aktion": p.get("aktion"), "blockiert": blockiert,
            "punkt_art": p.get("art"), "verweis": f"#/offen?punkt={p['id']}",
        }
        if p.get("belege_fehlen") and fehlende_belege:
            eintrag["belege"] = fehlende_belege
            belege_untergebracht = True
        # Ohne `wer` gilt der Punkt als deiner: was niemandem zugeordnet ist,
        # erledigt sonst niemand. Nur ausdruecklich Dritten Zugewiesenes und
        # reine Kenntnisnahme stehen unter "im Blick".
        if (p.get("wer") in ("extern", "app")
                or (p.get("aktion") or "").startswith("Nur zur Kenntnis")):
            im_blick.append(eintrag)
        else:
            aufgaben.append(eintrag)

    if fehlende_belege and not belege_untergebracht:
        aufgaben.append({
            "art": "belege", "titel": f"{len(fehlende_belege)} Buchungen ohne Beleg",
            "prioritaet": "mittel", "aktion": "Belege beschaffen; Fundort je Buchung "
            "auf der Seite „Offene Punkte“.", "belege": fehlende_belege,
            "verweis": "#/offen"})

    # 3. Platzhalter der Steuerrechnung
    for ph in (steuer or {}).get("platzhalter", []):
        aufgaben.append({
            "art": "platzhalter", "titel": f"Platzhalter: {ph['zeile']}",
            "prioritaet": "hoch", "aktion": f"Unterlage beschaffen: {ph.get('aufloesen_durch')}",
            "hindernisse": [ph.get("grund")] if ph.get("grund") else [],
            "verweis": "#/steuern"})

    # 4. Jede offene Frist, die nicht schon als Abgabe des Jahres dasteht:
    # Voranmeldungen und Zusammenfassende Meldungen, die das Abgabepaket nicht
    # kennt, und die Abgaben anderer Jahre - auch bevor sie ueberfaellig sind.
    # Sonst fehlen Voranmeldung, ZM und die Abgaben des Vorjahrs, und als
    # naechste Frist steht der Jahresabschluss des laufenden Jahres da.
    im_paket = {a["bezeichnung"] for a in (paket or {}).get("abgaben", [])}
    for t in termine.get("eintraege", []):
        if t["status"] not in ("offen", "verlaengert", "ueberfaellig"):
            continue
        if t["bezeichnung"] in im_paket:
            continue
        ueberfaellig = t["status"] == "ueberfaellig" or (t.get("tage") or 0) < 0
        # Eine Zahllast, die das Finanzamt per Lastschrift einzieht, verlangt
        # nichts - ausser Deckung. Erst wenn die Frist ohne Abbuchung verstrichen
        # ist, wird sie wieder eine Aufgabe.
        lastschrift = t.get("lastschrift") and not ueberfaellig
        # Ein Zeitraum, der noch laeuft, laesst sich nicht anmelden - wie beim
        # Jahresabschluss ist das bis Zeitraumende nur etwas fuer den Blick.
        laeuft = bool(t.get("vorlaeufig"))
        (im_blick if lastschrift or laeuft else aufgaben).append({
            "art": "frist", "titel": t["bezeichnung"], "frist": t["faellig"],
            "ueberfaellig": ueberfaellig, "tage": t["tage"],
            "prioritaet": "mittel" if lastschrift else "hoch",
            "zustand": t["status"],
            "aktion": ("Wird per SEPA-Lastschrift eingezogen — nur Deckung auf dem Konto"
                       if lastschrift else "Zeitraum läuft noch — anmelden nach Zeitraumende"
                       if laeuft else t.get("notiz") or t["grundlage"]),
            "verweis": t.get("verweis")})

    aufgaben.sort(key=_sortierschluessel)
    im_blick.sort(key=_sortierschluessel)
    return {
        "aufgaben": aufgaben, "im_blick": im_blick,
        "zaehler": {"aufgaben": len(aufgaben),
                    "ueberfaellig": sum(1 for a in aufgaben if a.get("ueberfaellig")),
                    "entscheidungen": sum(1 for a in aufgaben
                                          if a.get("punkt_art") in ("entscheidung", "klaerung")),
                    "im_blick": len(im_blick)},
        # Die naechste *kommende* Frist - ueberfaellige stehen schon im Zaehler.
        "naechste_frist": min((a["frist"] for a in aufgaben
                               if a.get("frist") and a["frist"] >= heute), default=None),
        "heute": heute,
    }
