# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Verknuepfungen herstellen: Buchung <-> Bankumsatz <-> Beleg.

Ohne diese Kanten endet jede Auswertung beim Buchungssatz. Erst sie machen den
Weg von der Bilanzzeile bis zum PDF im Archiv begehbar.

Drei Quellen, alle deterministisch - kein Raten:

1. `belegfeld` = 'KA<n>'  ->  banktransaktion.id = n
   So erzeugt `erzeuge_buchungen.py` die Buchungen aus den Bankumsaetzen.
2. banktransaktion (IBAN, auszug)  ->  Kontoauszug-PDF im Belegarchiv
   Der Dateiname der Commerzbank-Auszuege traegt beides:
   `DE80..._EUR_Kontoauszug_010_31_10_2025_DE80...`
3. `belege_zuordnung.csv` (belegfeld;belegnr) fuer alles Uebrige -
   Abschlussbuchungen, Lohn, Wertpapierabrechnungen, Vertraege.
"""

from __future__ import annotations

import csv
import os
import re
import sqlite3

RE_AUSZUG = re.compile(r"^(DE\d{20})_EUR_Kontoauszug_(\d{3})_\d{2}_\d{2}_(\d{4})", re.IGNORECASE)


def bankumsaetze(con: sqlite3.Connection, gj_id: int) -> int:
    """belegfeld 'KA<n>' -> banktransaktion.buchung_id. Rueckgabe: gesetzte Kanten."""
    n = 0
    for b in con.execute("SELECT id, belegfeld FROM buchung "
                         "WHERE gj_id=? AND belegfeld LIKE 'KA%'", (gj_id,)).fetchall():
        rest = b["belegfeld"][2:]
        if not rest.isdigit():
            continue
        cur = con.execute("UPDATE banktransaktion SET buchung_id=? WHERE id=?",
                          (b["id"], int(rest)))
        n += cur.rowcount
    return n


def kontoauszuege(con: sqlite3.Connection, mandant_id: int, gj_id: int) -> int:
    """Jede Bankbuchung an das PDF des Auszugs haengen, aus dem sie stammt."""
    # (IBAN, Auszugsnummer, Jahr) -> beleg_id
    index: dict[tuple[str, str, str], int] = {}
    for r in con.execute("SELECT id, bezeichnung FROM beleg "
                         "WHERE mandant_id=? AND kategorie='bank'", (mandant_id,)):
        if m := RE_AUSZUG.match(r["bezeichnung"]):
            index[(m.group(1).upper(), m.group(2), m.group(3))] = r["id"]

    n = 0
    for r in con.execute("""
            SELECT t.buchung_id, k.iban, t.auszug, substr(t.buchungsdatum,1,4) AS jahr
            FROM banktransaktion t JOIN bankkonto k ON k.id=t.bankkonto_id
            JOIN buchung b ON b.id=t.buchung_id
            WHERE t.buchung_id IS NOT NULL AND b.gj_id=?""", (gj_id,)):
        bid = index.get((r["iban"].upper(), (r["auszug"] or "").zfill(3), r["jahr"]))
        if bid:
            cur = con.execute("INSERT OR IGNORE INTO buchung_beleg (buchung_id, beleg_id) "
                              "VALUES (?,?)", (r["buchung_id"], bid))
            n += cur.rowcount
    return n


def anhaenge(con: sqlite3.Connection, mandant_id: int, gj_id: int) -> int:
    """Beleg, der an einem Bankumsatz hing -> Buchung dieses Umsatzes.

    So kommen die in Qonto angehaengten Eingangsrechnungen an ihren Buchungssatz,
    ohne dass jemand eine Zuordnungsliste pflegen muesste: die Kante steht schon
    in `beleg.banktransaktion_id`, gesetzt beim Import.
    """
    cur = con.execute("""
        INSERT OR IGNORE INTO buchung_beleg (buchung_id, beleg_id)
        SELECT t.buchung_id, e.id
        FROM beleg e JOIN banktransaktion t ON t.id = e.banktransaktion_id
        JOIN buchung b ON b.id = t.buchung_id
        WHERE e.mandant_id = ? AND t.buchung_id IS NOT NULL AND b.gj_id = ?""",
        (mandant_id, gj_id))
    return cur.rowcount


def belegnummern(con: sqlite3.Connection, mandant_id: int, gj_id: int) -> int:
    """`belegfeld` traegt direkt eine Belegnummer (z.B. 'B2026-0091').

    Bei den eigenen Ausgangsrechnungen ist der Beleg schon vor der Buchung da -
    die Rechnung *ist* der Anlass. Dann steht die Nummer gleich im Buchungssatz.
    """
    cur = con.execute("""
        INSERT OR IGNORE INTO buchung_beleg (buchung_id, beleg_id)
        SELECT b.id, e.id FROM buchung b
        JOIN beleg e ON e.belegnr = b.belegfeld AND e.mandant_id = ?
        WHERE b.gj_id = ?""", (mandant_id, gj_id))
    return cur.rowcount


def aus_datei(con: sqlite3.Connection, mandant_id: int, gj_id: int, pfad: str) -> tuple[int, list[str]]:
    """`belege_zuordnung.csv`: Spalten belegfeld;belegnr;notiz (Semikolon, '#' = Kommentar).

    Ein belegfeld darf mehrfach vorkommen (mehrere Belege je Buchung), ebenso
    eine Belegnummer (ein Beleg traegt mehrere Buchungen).
    """
    if not os.path.exists(pfad):
        return 0, []
    n, fehler = 0, []
    with open(pfad, encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            feld = (row.get("belegfeld") or "").strip()
            nr = (row.get("belegnr") or "").strip()
            if not feld or feld.startswith("#") or not nr:
                continue
            beleg = con.execute("SELECT id FROM beleg WHERE mandant_id=? AND belegnr=?",
                                (mandant_id, nr)).fetchone()
            if not beleg:
                fehler.append(f"Beleg {nr} unbekannt (belegfeld {feld})")
                continue
            buchungen = con.execute("SELECT id FROM buchung WHERE gj_id=? AND belegfeld=?",
                                    (gj_id, feld)).fetchall()
            if not buchungen:
                fehler.append(f"Kein Buchungssatz mit belegfeld '{feld}' (Beleg {nr})")
                continue
            for b in buchungen:
                cur = con.execute("INSERT OR IGNORE INTO buchung_beleg (buchung_id, beleg_id) "
                                  "VALUES (?,?)", (b["id"], beleg["id"]))
                n += cur.rowcount
    return n, fehler


def nicht_gebucht(con: sqlite3.Connection, pfad: str) -> int:
    """`nicht_gebucht.csv` (umsatz_id;grund) - bewusst nicht gebuchte Umsaetze.

    Reine Informationszeilen des Auszugs und die Gegenseite interner
    Umbuchungen. Ohne diesen Vermerk sehen sie in der Auswertung aus wie eine
    vergessene Buchung.
    """
    if not os.path.exists(pfad):
        return 0
    n = 0
    with open(pfad, encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            uid = (row.get("umsatz_id") or "").strip()
            if not uid.isdigit():
                continue
            n += con.execute("UPDATE banktransaktion SET nicht_gebucht_grund=?"
                             " WHERE id=? AND buchung_id IS NULL",
                             ((row.get("grund") or "").strip() or "bewusst nicht gebucht",
                              int(uid))).rowcount
    return n


def alles(con: sqlite3.Connection, mandant_id: int, gj_id: int,
          jahr_dir: str | None = None) -> dict:
    ergebnis = {"bankumsaetze": bankumsaetze(con, gj_id)}
    ergebnis["kontoauszuege"] = (kontoauszuege(con, mandant_id, gj_id)
                                 + anhaenge(con, mandant_id, gj_id)
                                 + belegnummern(con, mandant_id, gj_id))
    if jahr_dir:
        ergebnis["manuell"], ergebnis["fehler"] = aus_datei(
            con, mandant_id, gj_id, os.path.join(jahr_dir, "belege_zuordnung.csv"))
        ergebnis["ohne_buchung"] = nicht_gebucht(
            con, os.path.join(jahr_dir, "nicht_gebucht.csv"))
    else:
        ergebnis["manuell"], ergebnis["fehler"], ergebnis["ohne_buchung"] = 0, [], 0
    return ergebnis


# Kategorien, zu denen es einen Geschaeftsvorfall geben muss. Alles andere im
# Archiv sind Nachweise ohne eigene Buchung: Depotauszuege, Orderprotokolle,
# Ex-Ante-Kosteninformationen, Vertraege, Bescheide.
ERWARTET_BUCHUNG = ("eingangsrechnung", "lohn")


def offen(con: sqlite3.Connection, mandant_id: int, gj_id: int,
          jahr: int | None = None) -> dict:
    """Was noch keine Kante hat - Grundlage fuer die Pruefliste im Frontend.

    Bei den Belegen wird unterschieden. Die blosse Zahl "Belege ohne Buchung"
    klingt nach einer Luecke, ist aber zum groessten Teil keine: ein
    Depotauszug oder ein Vertrag *hat* keine Buchung. Gemeldet wird deshalb
    getrennt, wie viele **Rechnungen** des laufenden Jahres unverknuepft sind -
    nur die sind ein Hinweis auf etwas Fehlendes.
    """
    q = con.execute
    platzhalter = ",".join("?" * len(ERWARTET_BUCHUNG))
    ohne = ("SELECT COUNT(*) FROM beleg e WHERE e.mandant_id=? AND NOT EXISTS"
            " (SELECT 1 FROM buchung_beleg v WHERE v.beleg_id=e.id)")
    return {
        "umsaetze_ohne_buchung": q(
            "SELECT COUNT(*) FROM banktransaktion t JOIN bankkonto k ON k.id=t.bankkonto_id"
            " WHERE k.mandant_id=? AND t.buchung_id IS NULL"
            " AND t.nicht_gebucht_grund IS NULL", (mandant_id,)).fetchone()[0],
        "buchungen_ohne_beleg": q(
            "SELECT COUNT(*) FROM buchung b WHERE b.gj_id=? AND NOT EXISTS"
            " (SELECT 1 FROM buchung_beleg v WHERE v.buchung_id=b.id)", (gj_id,)).fetchone()[0],
        "belege_ohne_buchung": q(ohne, (mandant_id,)).fetchone()[0],
        "rechnungen_ohne_buchung": q(
            f"{ohne} AND e.kategorie IN ({platzhalter})"
            + (" AND e.jahr=?" if jahr else ""),
            (mandant_id, *ERWARTET_BUCHUNG, *( (jahr,) if jahr else () ))).fetchone()[0],
    }
