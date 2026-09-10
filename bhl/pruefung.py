# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Regelpruefungen auf der Buchfuehrung.

Die Buchfuehrung traegt ihre eigenen Kontrollen: Soll gleich Haben, Aktiva
gleich Passiva. Was sie nicht von selbst traegt, ist die Regel, nach der
*gerechnet* werden soll - und die unterscheidet sich zwischen den beiden
Betriebsarten:

* Kapitalgesellschaft: Realisationsprinzip. Aufwand und Ertrag entstehen mit
  dem Geschaeftsvorfall, nicht mit der Zahlung. Rueckstellungen, Forderungen
  und Rechnungsabgrenzung sind richtig.
* private Einkommensteuer: § 11 EStG. Erfolgswirksam wird nur, was zufliesst
  oder abfliesst. Dieselben Buchungen waeren hier ein Fehler.

`paragraf_11` macht diesen Unterschied pruefbar, statt ihn der Disziplin beim
Buchen zu ueberlassen.
"""

from __future__ import annotations

import sqlite3

from .db import eur
from .kontenplan import Rahmen


def _befund(art: str, schwere: str, text: str, buchung: dict | None = None) -> dict:
    return {"art": art, "schwere": schwere, "text": text, "buchung": buchung}


def soll_gleich_haben(con: sqlite3.Connection, gj_id: int) -> list[dict]:
    """Der Grundsatz der doppelten Buchfuehrung, je Buchungssatz."""
    befunde = []
    for r in con.execute("""
            SELECT b.id, b.nummer, b.datum, b.buchungstext,
                   COALESCE(SUM(z.soll_cent),0)  AS soll,
                   COALESCE(SUM(z.haben_cent),0) AS haben
            FROM buchung b LEFT JOIN buchungszeile z ON z.buchung_id=b.id
            WHERE b.gj_id=? GROUP BY b.id HAVING soll <> haben""", (gj_id,)):
        befunde.append(_befund(
            "soll_haben", "fehler",
            f"Buchung {r['nummer']} ({r['datum']}): Soll {r['soll']/100:.2f} ≠ "
            f"Haben {r['haben']/100:.2f}",
            {"id": r["id"], "nummer": r["nummer"], "datum": r["datum"],
             "text": r["buchungstext"]}))
    return befunde


def paragraf_11(con: sqlite3.Connection, gj_id: int, rahmen: Rahmen) -> list[dict]:
    """§ 11 EStG: erfolgswirksam nur, wo Geld fliesst.

    Geprueft wird je Buchungssatz: beruehrt er ein Erfolgskonto, muss er auch
    ein Geldkonto beruehren. Ausgenommen sind die beiden Faelle, in denen das
    Gesetz selbst eine Buchung ohne Zahlung verlangt - die AfA nach § 7 EStG
    und die Verteilung von Erhaltungsaufwand nach § 82b EStDV. Deren Konten
    tragen im Kontenrahmen das Kennzeichen `ohne_zahlung`.

    Ausgenommen sind ausserdem drei Buchungsarten, die keine Zahlungsstroeme
    abbilden wollen: die Eroeffnungsbilanz (`eb`), das aus einer fertigen
    Erklaerung uebernommene Vergleichsjahr (`vorjahr`) und die Erklaerung
    selbst (`erklaerung`).

    `erklaerung` ist der Fall, in dem die Besteuerungsgrundlagen feststehen,
    die Zahlungsstroeme aber nicht vorliegen - etwa weil die Kontoauszuege des
    Jahres noch fehlen. Die Buchung sagt dann, was in die Anlage gehoert, und
    behauptet nicht, dass sie aus Zahlungen abgeleitet waere. Sobald die
    Auszuege da sind, wird das Jahr regulaer gebucht und das Kennzeichen faellt
    weg - dann greift die Regel wieder.
    """
    if not rahmen.ueberschussrechnung:
        return []
    befunde = []
    for r in con.execute("""
            SELECT b.id, b.nummer, b.datum, b.buchungstext,
                   GROUP_CONCAT(k.nummer) AS konten,
                   SUM(k.typ IN ('E','X'))  AS erfolg
            FROM buchung b JOIN buchungszeile z ON z.buchung_id=b.id
            JOIN konto k ON k.id=z.konto_id
            WHERE b.gj_id=? AND b.art NOT IN ('eb','vorjahr','erklaerung')
            GROUP BY b.id""", (gj_id,)):
        if not r["erfolg"]:
            continue
        konten = set((r["konten"] or "").split(","))
        if konten & rahmen.geldkonten:
            continue
        # Buchungen, die ausschliesslich auf zahlungsfreien Erfolgskonten
        # liegen, sind erlaubt - AfA und § 82b.
        erfolgs_konten = konten - rahmen.geldkonten
        if erfolgs_konten & rahmen.ohne_zahlung:
            continue
        befunde.append(_befund(
            "paragraf_11", "fehler",
            f"Buchung {r['nummer']} ({r['datum']}) ist erfolgswirksam, ohne dass Geld "
            f"fließt: {', '.join(sorted(konten))}. Nach § 11 EStG zählt der Zu- oder "
            f"Abfluss; ohne Zahlung sind nur AfA (§ 7 EStG) und die Verteilung nach "
            f"§ 82b EStDV zulässig.",
            {"id": r["id"], "nummer": r["nummer"], "datum": r["datum"],
             "text": r["buchungstext"]}))
    return befunde


def jahreswechsel(con: sqlite3.Connection, gj_id: int, rahmen: Rahmen) -> list[dict]:
    """§ 11 Abs. 1 S. 2 / Abs. 2 S. 2 EStG - die Zehn-Tage-Regel.

    Regelmaessig wiederkehrende Einnahmen und Ausgaben, die *kurze Zeit* (nach
    staendiger Rechtsprechung zehn Tage) vor oder nach dem Jahreswechsel
    fliessen, gehoeren wirtschaftlich in das andere Jahr - die Januarmiete, die
    am 30.12. eingeht, zaehlt zum Folgejahr.

    Automatisch entscheiden laesst sich das nicht: ob eine Zahlung
    "regelmaessig wiederkehrend" ist, steht in keiner Buchung. Gemeldet wird
    deshalb als Hinweis, nicht als Fehler - jemand muss draufschauen.

    Ausgenommen sind Buchungen auf Konten mit dem Kennzeichen `ohne_zahlung`
    (AfA nach § 7 EStG, verteilter Erhaltungsaufwand nach § 82b EStDV). Die
    Regel setzt einen Zu- oder Abfluss voraus; wo nichts fliesst, kann auch
    nichts im falschen Jahr fliessen. Die AfA steht immer zum 31.12. und
    haette sonst in jedem Jahr einen Hinweis erzeugt, der nie zutrifft.
    """
    if not rahmen.ueberschussrechnung:
        return []
    g = con.execute("SELECT beginn, ende FROM geschaeftsjahr WHERE id=?", (gj_id,)).fetchone()
    if not g:
        return []
    import datetime as dt
    beginn, ende = dt.date.fromisoformat(g["beginn"]), dt.date.fromisoformat(g["ende"])
    fenster = [(beginn.isoformat(), (beginn + dt.timedelta(days=10)).isoformat()),
               ((ende - dt.timedelta(days=10)).isoformat(), ende.isoformat())]
    befunde = []
    for von, bis in fenster:
        for r in con.execute("""
                SELECT b.id, b.nummer, b.datum, b.buchungstext
                FROM buchung b JOIN buchungszeile z ON z.buchung_id=b.id
                JOIN konto k ON k.id=z.konto_id
                WHERE b.gj_id=? AND b.art='lfd' AND b.datum BETWEEN ? AND ?
                  AND k.typ IN ('E','X') AND k.nummer NOT IN (%s)
                GROUP BY b.id ORDER BY b.datum""" % (
                    ",".join("?" * len(rahmen.ohne_zahlung)) or "''"),
                (gj_id, von, bis, *sorted(rahmen.ohne_zahlung))):
            befunde.append(_befund(
                "zehn_tage", "hinweis",
                f"Buchung {r['nummer']} ({r['datum']}): liegt im Zehn-Tage-Fenster um den "
                f"Jahreswechsel. Falls regelmäßig wiederkehrend (Miete, Beiträge), gehört "
                f"sie nach § 11 EStG in das andere Jahr.",
                {"id": r["id"], "nummer": r["nummer"], "datum": r["datum"],
                 "text": r["buchungstext"]}))
    return befunde


def ohne_gegenkonto(con: sqlite3.Connection, gj_id: int) -> list[dict]:
    """Buchungen mit nur einer Zeile - technisch moeglich, fachlich nie richtig."""
    return [_befund("einseitig", "fehler",
                    f"Buchung {r['nummer']} ({r['datum']}) hat nur eine Zeile.",
                    {"id": r["id"], "nummer": r["nummer"], "datum": r["datum"],
                     "text": r["buchungstext"]})
            for r in con.execute("""
                SELECT b.id, b.nummer, b.datum, b.buchungstext
                FROM buchung b JOIN buchungszeile z ON z.buchung_id=b.id
                WHERE b.gj_id=? GROUP BY b.id HAVING COUNT(z.id) < 2""", (gj_id,))]


def bankbestand(con: sqlite3.Connection, gj_id: int) -> list[dict]:
    """Das Bankkonto gegen die Bank halten.

    Die Summenprobe kann diesen Fehler nicht finden: eine doppelt eingebuchte
    Bewegung ist in sich ausgeglichen, Soll bleibt gleich Haben und die Bilanz
    geht auf. Sichtbar wird sie erst am Geld. Deshalb hier die Identitaet, die
    fuer jedes Bankkonto gelten muss:

        Bewegung auf dem Sachkonto im Jahr (ohne Eroeffnungsbilanz)
        = Summe *aller* importierten Bankumsaetze des Jahres

    Ohne Ausnahme, und das ist der Punkt: gezaehlt wird auch, was keine eigene
    Verknuepfung traegt oder als bewusst nicht gebucht vermerkt ist. Eine
    Umbuchung zwischen zwei eigenen Konten steht auf beiden Auszuegen und wird
    einmal gebucht - der Gegenumsatz hat deshalb keine eigene Buchung, das Geld
    hat sich auf diesem Konto aber sehr wohl bewegt. Genau dafuer steht die
    Regel: jeder Euro, der die Bank bewegt hat, steht genau einmal auf dem
    Sachkonto.

    Weicht die Zeile ab, ist entweder etwas doppelt gebucht, etwas ohne
    Bankumsatz auf dem Bankkonto gelandet oder ein Umsatz mit dem falschen
    Betrag kontiert.
    """
    befunde = []
    for k in con.execute("""
            SELECT bk.bezeichnung, bk.iban, ko.nummer,
                   (SELECT COALESCE(SUM(z.soll_cent - z.haben_cent), 0)
                      FROM buchungszeile z JOIN buchung b ON b.id = z.buchung_id
                     WHERE z.konto_id = ko.id AND b.gj_id = g.id AND b.art <> 'eb') AS gebucht,
                   (SELECT COALESCE(SUM(t.betrag_cent), 0)
                      FROM banktransaktion t
                     WHERE t.bankkonto_id = bk.id
                       AND t.buchungsdatum BETWEEN g.beginn AND g.ende) AS bank,
                   (SELECT COUNT(*) FROM banktransaktion t
                     WHERE t.bankkonto_id = bk.id
                       AND t.buchungsdatum BETWEEN g.beginn AND g.ende) AS umsaetze
            FROM geschaeftsjahr g
            JOIN bankkonto bk ON bk.mandant_id = g.mandant_id
            JOIN konto ko     ON ko.id = bk.konto_id
            WHERE g.id = ? AND COALESCE(bk.nur_archiv, 0) = 0""", (gj_id,)):
        if not k["umsaetze"]:
            continue
        diff = k["gebucht"] - k["bank"]
        if diff:
            befunde.append(_befund(
                "bankbestand", "fehler",
                f"{k['bezeichnung']}: Konto {k['nummer']} bewegt sich um "
                f"{eur(k['gebucht'])} EUR, die gebuchten Bankumsaetze summieren "
                f"sich auf {eur(k['bank'])} EUR - Abweichung {eur(diff)} EUR."))
    return befunde


def platzhalter(jahr_dir: str | None) -> list[dict]:
    """Angenommene Werte in der Steuerrechnung - die Abgabesperre.

    Ein Platzhalter ist der Fall, in dem eine Unterlage fehlt und trotzdem
    weitergerechnet werden soll: die Zahl steht in `steuern.json`, aber sie ist
    angenommen, nicht belegt. Damit sie nicht als Tatsache durchrutscht, meldet
    sie diese Pruefung als **Fehler** - `bh pruefen` endet dann mit
    Rueckgabewert 1 und `bh abgabe` verweigert die Freigabe.

    Bewusst kein Hinweis: ein Hinweis laesst sich uebersehen, und eine
    geschaetzte Besteuerungsgrundlage in einer abgegebenen Erklaerung ist keine
    Kleinigkeit (§ 150 Abs. 2 AO - die Angaben sind wahrheitsgemaess nach
    bestem Wissen und Gewissen zu machen).
    """
    if not jahr_dir:
        return []
    from . import steuern as _st
    spec = _st.laden(jahr_dir)
    if not spec:
        return []
    def satz(text: str) -> str:
        return text.rstrip().rstrip(".") + ". "

    befunde = []
    for p in _st.platzhalter(spec):
        text = satz(f"Platzhalter in der Steuerrechnung: „{p['zeile']}“ mit "
                    f"{eur(p['betrag'])} EUR ist angenommen, nicht belegt")
        for schluessel, vorspann in (("annahme", ""), ("grund", ""),
                                     ("aufloesen_durch", "Aufzulösen durch: ")):
            if p.get(schluessel):
                text += satz(vorspann + p[schluessel])
        befunde.append(_befund("platzhalter", "fehler",
                               text + "Bis dahin ist die Erklärung nicht abgabefähig."))
    return befunde


def alles(con: sqlite3.Connection, gj_id: int, rahmen: Rahmen,
          jahr_dir: str | None = None) -> dict:
    befunde = (soll_gleich_haben(con, gj_id) + ohne_gegenkonto(con, gj_id)
               + bankbestand(con, gj_id)
               + paragraf_11(con, gj_id, rahmen) + jahreswechsel(con, gj_id, rahmen)
               + platzhalter(jahr_dir))
    return {"befunde": befunde,
            "fehler": sum(1 for b in befunde if b["schwere"] == "fehler"),
            "hinweise": sum(1 for b in befunde if b["schwere"] == "hinweis")}
