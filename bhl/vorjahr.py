# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Vorjahr als eigenes Geschaeftsjahr anlegen.

Der Vorjahresvergleich der Bilanz und der GuV soll aus der Datenbank kommen,
nicht aus einer gepflegten Zahlenliste. Fuer ein Jahr, dessen Buchungen nicht
vorliegen (hier: 2024, gebucht beim Steuerberater), werden die Salden aus dem
Jahresabschlussbericht uebernommen - als **eine** Buchung, die auf den Beleg
zeigt, aus dem sie stammt.

Zwei Quellen, damit keine Zahl doppelt gepflegt wird:

* **Bestandskonten** kommen aus der Eroeffnungsbilanz des Folgejahres. Sie sind
  dieselben Werte (Bilanzenzusammenhang, § 252 Abs. 1 Nr. 1 HGB) und stehen
  bereits in der Datenbank.
* **Erfolgskonten** kommen aus dem Kontennachweis zur GuV des Abschlusses -
  eine CSV mit `konto;soll;haben`.

Eine Korrektur ist noetig: Der Gewinnvortrag der Folgejahres-Eroeffnungsbilanz
enthaelt das Ergebnis des Vorjahres bereits. Im Vorjahr selbst steht dort noch
der Vortrag *vor* Verwendung, also abzueglich Jahresergebnis und zuzueglich der
Einstellungen in die Ruecklagen. Genau das rechnet `_gewinnvortrag_korrektur`.
"""

from __future__ import annotations

import csv
import os
import sqlite3

from .db import cent, eur
from .ledger import LedgerError, buchen, gj_anlegen


def _eb_salden(con: sqlite3.Connection, mandant_id: int, folgejahr: int) -> dict[str, int]:
    """Bestandssalden aus den Eroeffnungsbuchungen des Folgejahres (ohne 9000)."""
    rows = con.execute("""
        SELECT k.nummer, k.posten,
               SUM(z.soll_cent) - SUM(z.haben_cent) AS saldo
        FROM buchungszeile z JOIN buchung b ON b.id=z.buchung_id
        JOIN konto k ON k.id=z.konto_id JOIN geschaeftsjahr g ON g.id=b.gj_id
        WHERE g.mandant_id=? AND g.jahr=? AND b.art='eb' AND k.posten<>'EROEFFNUNG'
        GROUP BY k.id HAVING saldo <> 0""", (mandant_id, folgejahr)).fetchall()
    if not rows:
        raise LedgerError(f"Keine Eroeffnungsbilanz {folgejahr} gefunden - "
                          f"das Vorjahr laesst sich daraus nicht ableiten.")
    return {r["nummer"]: r["saldo"] for r in rows}


def _guv_salden(pfad: str) -> dict[str, int]:
    salden: dict[str, int] = {}
    with open(pfad, encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            nr = (r.get("konto") or "").strip()
            if not nr or nr.startswith("#"):
                continue
            soll = cent(r["soll"]) if (r.get("soll") or "").strip() else 0
            haben = cent(r["haben"]) if (r.get("haben") or "").strip() else 0
            if soll and haben:
                raise LedgerError(f"Konto {nr}: Soll und Haben gleichzeitig belegt")
            salden[nr] = soll - haben
    return salden


def uebernehmen(con: sqlite3.Connection, mandant_id: int, jahr: int, jahr_dir: str,
                belegfeld: str = "JA-VORJAHR") -> dict:
    """Legt das Geschaeftsjahr <jahr> an und bucht die uebernommenen Salden."""
    guv_pfad = os.path.join(jahr_dir, "guv_kontennachweis.csv")
    if not os.path.exists(guv_pfad):
        raise LedgerError(f"Fehlt: {guv_pfad}")

    bestand = _eb_salden(con, mandant_id, jahr + 1)
    guv = _guv_salden(guv_pfad)

    typen = {r["nummer"]: (r["typ"], r["posten"]) for r in con.execute(
        "SELECT nummer, typ, posten FROM konto WHERE mandant_id=?", (mandant_id,))}
    unbekannt = [n for n in list(guv) + list(bestand) if n not in typen]
    if unbekannt:
        raise LedgerError(f"Konten nicht im Kontenplan: {', '.join(sorted(unbekannt))}")

    ergebnis = -sum(s for n, s in guv.items() if typen[n][0] in ("E", "X"))
    verwendung = sum(s for n, s in guv.items() if typen[n][1] == "EK_ERGEBNISVERW")

    # Gewinnvortrag der Folge-Eroeffnungsbilanz enthaelt das Ergebnis schon
    vortrag = [n for n in bestand if typen[n][1] == "EK_BILANZGEWINN"]
    if len(vortrag) != 1:
        raise LedgerError(f"Gewinnvortragskonto nicht eindeutig: {vortrag or 'keines'}")
    salden = dict(bestand)
    salden[vortrag[0]] += ergebnis - verwendung          # Habensaldo ist negativ

    for nr, s in guv.items():
        salden[nr] = salden.get(nr, 0) + s

    zeilen = [(nr, s, 0) if s > 0 else (nr, 0, -s)
              for nr, s in sorted(salden.items()) if s]
    soll = sum(z[1] for z in zeilen)
    haben = sum(z[2] for z in zeilen)
    if soll != haben:
        raise LedgerError(f"Uebernahme {jahr} unausgeglichen: Soll {eur(soll)} "
                          f"./. Haben {eur(haben)} = {eur(soll - haben)}")

    gj_id = gj_anlegen(con, mandant_id, jahr)
    con.execute("UPDATE banktransaktion SET buchung_id=NULL WHERE buchung_id IN"
                " (SELECT id FROM buchung WHERE gj_id=?)", (gj_id,))
    con.execute("DELETE FROM buchung WHERE gj_id=?", (gj_id,))
    buchen(con, gj_id, mandant_id, f"{jahr}-12-31",
           f"Übernahme der Salden aus dem Jahresabschluss {jahr}", zeilen,
           belegfeld=belegfeld, art="vorjahr")
    con.execute("UPDATE geschaeftsjahr SET status='abgeschlossen' WHERE id=?", (gj_id,))

    return {"gj_id": gj_id, "konten": len(zeilen), "summe": soll,
            "ergebnis": ergebnis, "verwendung": verwendung}


def gj_id(con: sqlite3.Connection, mandant_id: int, jahr: int) -> int | None:
    r = con.execute("SELECT id FROM geschaeftsjahr WHERE mandant_id=? AND jahr=?",
                    (mandant_id, jahr)).fetchone()
    return r[0] if r else None
