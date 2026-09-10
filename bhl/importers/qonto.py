"""Qonto-Importer: Bankumsätze und die daran hängenden Rechnungen.

Nur Standardbibliothek. Der Importer schreibt ausschließlich **Rohdaten** — den
Bankumsatz so, wie Qonto ihn liefert, samt vollständigem JSON, und die in Qonto
angehängten Rechnungen als Belege ins Archiv. Kontiert wird hier nichts; das ist
Sache von `erzeuge_buchungen.py`.

Das ist dieselbe Arbeitsteilung wie beim Commerzbank-Importer, nur dass die
Quelle eine Schnittstelle statt eines PDF ist. Was Qonto an einen Umsatz gehängt
hat, landet über `beleg.banktransaktion_id` wieder bei genau diesem Umsatz — und
darüber später bei der Buchung.

Zum Datum: gebucht wird auf `settled_at`. Für die Vorsteuer ist eigentlich das
Rechnungsdatum maßgebend (§ 15 UStG), nicht der Zahlungstag. Bei monatlich
abgerechneten Diensten fällt beides in denselben Zeitraum; wo es auseinanderfällt,
gehört die Buchung von Hand verschoben.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from .. import belege as blg
from .. import zugang

BASIS = "https://thirdparty.qonto.com/v2"
SEITE = 100
ZEITLIMIT = 60

# Qonto weist Anfragen ohne eigenen User-Agent mit 403 ab. Die Standardkennung
# von urllib genuegt nicht.
KENNUNG = "bh-buchhaltung/1.0"


def _hole(pfad: str, params: dict | None = None) -> dict:
    url = BASIS + pfad
    if params:
        # Qonto erwartet Listen als wiederholte Parameter mit [] im Namen
        paare = []
        for k, v in params.items():
            for w in (v if isinstance(v, (list, tuple)) else [v]):
                paare.append((k, str(w)))
        url += "?" + urllib.parse.urlencode(paare)
    req = urllib.request.Request(url, headers={
        **zugang.qonto_kopf(), "Accept": "application/json", "User-Agent": KENNUNG})
    with urllib.request.urlopen(req, timeout=ZEITLIMIT) as r:
        return json.loads(r.read().decode("utf-8"))


def bankkonten() -> list[dict]:
    return _hole("/organization")["organization"]["bank_accounts"]


def umsaetze(bankkonto_id: str, von: str, bis: str) -> list[dict]:
    """Abgeschlossene Umsätze im Zeitraum [von, bis] (beides YYYY-MM-DD)."""
    alle, seite = [], 1
    while True:
        antwort = _hole("/transactions", {
            "bank_account_id": bankkonto_id,
            "status[]": "completed",
            "settled_at_from": f"{von}T00:00:00.000Z",
            "settled_at_to": f"{bis}T23:59:59.999Z",
            "includes[]": ["vat_details", "labels", "attachments"],
            "page": seite, "per_page": SEITE,
        })
        alle += antwort["transactions"]
        meta = antwort.get("meta") or {}
        if seite >= int(meta.get("total_pages") or 1):
            return alle
        seite += 1


def _text(tx: dict) -> str:
    """Ein lesbarer Verwendungszweck aus den Feldern, die Qonto getrennt führt."""
    teile = [tx.get("clean_counterparty_name") or tx.get("counterparty_name"),
             tx.get("label"), tx.get("reference"), tx.get("note")]
    gesehen, aus = set(), []
    for t in teile:
        t = (t or "").strip()
        if t and t.lower() not in gesehen:
            gesehen.add(t.lower())
            aus.append(t)
    return " | ".join(aus) or "(ohne Bezeichnung)"


def _datum(wert: str | None) -> str | None:
    return wert[:10] if wert else None


def _anhaenge_ablegen(con: sqlite3.Connection, mandant_id: int, mandant_dir: str,
                      tx: dict, bt_id: int, jahr: int) -> int:
    """Angehängte Rechnungen herunterladen und ins Belegarchiv legen."""
    neu = 0
    aussteller = tx.get("clean_counterparty_name") or tx.get("counterparty_name")
    for att in tx.get("attachments") or []:
        url = att.get("url")
        if not url:
            continue
        name = att.get("file_name") or f"{att.get('id', 'anhang')}.pdf"
        try:
            with urllib.request.urlopen(url, timeout=ZEITLIMIT) as r:
                daten = r.read()
        except (urllib.error.URLError, OSError):
            continue                      # abgelaufener Link: beim nächsten Lauf erneut
        with tempfile.TemporaryDirectory() as tmp:
            pfad = os.path.join(tmp, os.path.basename(name))
            with open(pfad, "wb") as fh:
                fh.write(daten)
            beleg_id, _, ist_neu = blg.erfassen(
                con, mandant_id, mandant_dir, pfad,
                jahr=jahr,
                datum=_datum(tx.get("settled_at")),
                kategorie="eingangsrechnung",
                aussteller=aussteller,
                bezeichnung=f"{aussteller or 'Beleg'} {name}",
                betrag_cent=abs(int(tx["amount_cents"])),
                notiz=f"Qonto-Anhang zu Umsatz {tx.get('transaction_id') or tx['id']}",
            )
        con.execute("UPDATE beleg SET banktransaktion_id=? WHERE id=? AND banktransaktion_id IS NULL",
                    (bt_id, beleg_id))
        neu += ist_neu
    return neu


def importieren(con: sqlite3.Connection, mandant_id: int, mandant_dir: str, jahr: int,
                mit_anhaengen: bool = True) -> dict:
    """Alle Umsätze eines Kalenderjahres holen. Rückgabe: Zählwerk für die Ausgabe."""
    konten = {k["iban"]: k["id"] for k in bankkonten()}
    zaehler = {"neu": 0, "dubletten": 0, "belege": 0, "unbekannte_iban": []}

    for r in con.execute("SELECT id, iban FROM bankkonto WHERE mandant_id=?", (mandant_id,)):
        qonto_id = konten.get(r["iban"])
        if not qonto_id:
            zaehler["unbekannte_iban"].append(r["iban"])
            continue
        for tx in umsaetze(qonto_id, f"{jahr}-01-01", f"{jahr}-12-31"):
            gezeichnet = int(tx["amount_cents"]) * (-1 if tx.get("side") == "debit" else 1)
            kennung = tx.get("transaction_id") or tx["id"]
            gebucht = _datum(tx.get("settled_at")) or _datum(tx.get("emitted_at"))
            try:
                cur = con.execute(
                    "INSERT INTO banktransaktion (bankkonto_id, auszug, buchungsdatum, valuta,"
                    " betrag_cent, text, hash, rohdaten) VALUES (?,?,?,?,?,?,?,?)",
                    (r["id"], (gebucht or "")[:7], gebucht, _datum(tx.get("emitted_at")),
                     gezeichnet, _text(tx), kennung,
                     json.dumps(tx, ensure_ascii=False, sort_keys=True)))
                bt_id = cur.lastrowid
                zaehler["neu"] += 1
            except sqlite3.IntegrityError:
                vorhanden = con.execute(
                    "SELECT id FROM banktransaktion WHERE bankkonto_id=? AND hash=?",
                    (r["id"], kennung)).fetchone()
                bt_id = vorhanden["id"] if vorhanden else None
                zaehler["dubletten"] += 1
            if mit_anhaengen and bt_id:
                zaehler["belege"] += _anhaenge_ablegen(
                    con, mandant_id, mandant_dir, tx, bt_id, jahr)
    return zaehler
