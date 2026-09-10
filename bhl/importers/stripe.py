# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Stripe-Importer: Ausgangsrechnungen und Bewegungen auf dem Stripe-Guthaben.

Nur Standardbibliothek, Zugriff über die REST-Schnittstelle.

Zwei Dinge kommen herein, und sie sind bewusst getrennt:

* **Ausgangsrechnungen** (und Gutschriften) landen als Belege im Archiv, mit dem
  vollständigen Rechnungsobjekt in `rohdaten`. Sie tragen den Steuerzeitpunkt:
  bei Soll-Versteuerung ist das der Tag der Rechnungsstellung, nicht der
  Zahlungseingang.

* **Guthabenbewegungen** (Zahlung, Gebühr, Auszahlung) werden als Bankumsätze
  auf dem Stripe-Konto geführt. Stripe ist wirtschaftlich ein zweites Konto: das
  Geld liegt dort, bis es nach Qonto ausgezahlt wird, und die Gebühren werden nie
  überwiesen, sondern gleich abgezogen. Genau deshalb tauchen sie in Qonto nicht
  auf — und würden ohne diesen Weg in der Buchführung fehlen.

Die Gebühren stammen von Stripe Payments Europe Ltd (Irland) und sind damit eine
Eingangsleistung nach § 13b Abs. 1 UStG — Kennzahl 46 mit der Steuer in 47, die
in 67 sofort wieder abgezogen wird.
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

BASIS = "https://api.stripe.com/v1"
ZEITLIMIT = 60

# Bewegungsarten, deren negativer Betrag selbst die Stripe-Gebuehr ist. Bei
# 'charge' steckt die Gebuehr dagegen im Feld `fee` derselben Bewegung.
GEBUEHRENARTEN = {"stripe_fee", "tax_fee"}


class ImportFehler(Exception):
    """Eine Rechnung konnte nicht abgelegt werden. Wird gesammelt und gemeldet -
    ein stiller Fehlschlag saehe aus wie 'es gab nichts zu holen'."""


def _hole(pfad: str, params: dict | None = None) -> dict:
    url = BASIS + pfad
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={**zugang.stripe_kopf(),
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=ZEITLIMIT) as r:
        return json.loads(r.read().decode("utf-8"))


def _liste(pfad: str, params: dict) -> list[dict]:
    """Alle Seiten einer Stripe-Liste, über `starting_after` durchgeblättert."""
    aus, nach = [], None
    while True:
        p = {**params, "limit": 100}
        if nach:
            p["starting_after"] = nach
        antwort = _hole(pfad, p)
        daten = antwort.get("data") or []
        aus += daten
        if not antwort.get("has_more") or not daten:
            return aus
        nach = daten[-1]["id"]


def _jahr_grenzen(jahr: int) -> tuple[int, int]:
    import calendar
    import datetime
    anfang = int(datetime.datetime(jahr, 1, 1, tzinfo=datetime.timezone.utc).timestamp())
    ende = int(datetime.datetime(jahr, 12, 31, 23, 59, 59,
                                 tzinfo=datetime.timezone.utc).timestamp())
    del calendar
    return anfang, ende


def _tag(stempel: int | None) -> str | None:
    if not stempel:
        return None
    import datetime
    from zoneinfo import ZoneInfo
    # Stripe stempelt in UTC, der Voranmeldungszeitraum ist ein Berliner Kalender.
    return datetime.datetime.fromtimestamp(stempel, ZoneInfo("Europe/Berlin")).date().isoformat()


# ------------------------------------------------------------- Rechnungen

def rechnungen(jahr: int) -> list[dict]:
    """Festgeschriebene Ausgangsrechnungen mit Steuerzeitpunkt im Jahr."""
    von, bis = _jahr_grenzen(jahr)
    # Der Anlagezeitpunkt kann vor der Festschreibung liegen, deshalb grosszuegig
    # suchen und danach ueber das Festschreibedatum filtern.
    roh = _liste("/invoices", {"created[gte]": von - 45 * 86400, "created[lte]": bis + 5 * 86400})
    aus = []
    for inv in roh:
        fest = (inv.get("status_transitions") or {}).get("finalized_at")
        tag = _tag(fest)
        if not tag or not tag.startswith(str(jahr)):
            continue
        if inv.get("status") in ("draft",):
            continue
        aus.append(inv)
    return aus


def gutschriften(jahr: int) -> list[dict]:
    von, bis = _jahr_grenzen(jahr)
    return [cn for cn in _liste("/credit_notes", {"created[gte]": von, "created[lte]": bis})
            if (_tag(cn.get("created")) or "").startswith(str(jahr))]


def _ust_id(inv: dict) -> str | None:
    for t in inv.get("customer_tax_ids") or []:
        if t.get("value"):
            return t["value"]
    return None


def _mandant_name(con: sqlite3.Connection, mandant_id: int) -> str:
    """Aussteller einer eigenen Rechnung ist der Mandant."""
    r = con.execute("SELECT name FROM mandant WHERE id=?", (mandant_id,)).fetchone()
    return r[0] if r else ""


def _rechnung_ablegen(con: sqlite3.Connection, mandant_id: int, mandant_dir: str,
                      inv: dict, jahr: int, gutschrift: bool = False) -> bool:
    nummer = inv.get("number") or inv["id"]
    # Stripe erzeugt das PDF bei jedem Abruf neu, die Bytes unterscheiden sich also
    # von Lauf zu Lauf. Der Hash-Dublettenschutz des Belegarchivs greift hier
    # deshalb nicht - erkannt wird die Wiederholung an der Objektkennung.
    if con.execute("SELECT 1 FROM beleg WHERE mandant_id=? AND"
                   " json_extract(rohdaten,'$.id')=?", (mandant_id, inv["id"])).fetchone():
        return False
    url = inv.get("invoice_pdf") or inv.get("pdf")
    kunde = inv.get("customer_name") or inv.get("customer_email") or ""
    stempel = ((inv.get("status_transitions") or {}).get("finalized_at")
               or inv.get("created"))
    if not url:
        raise ImportFehler(f"{nummer}: Stripe liefert keine PDF-Adresse")
    try:
        # Ohne Authorization-Kopf: die Adresse traegt ihr eigenes Merkmal, und mit
        # dem Bearer-Schluessel antwortet Stripe mit 400.
        with urllib.request.urlopen(url, timeout=ZEITLIMIT) as r:
            daten = r.read()
    except (urllib.error.URLError, OSError) as e:
        raise ImportFehler(f"{nummer}: PDF nicht abrufbar ({e})") from e

    with tempfile.TemporaryDirectory() as tmp:
        pfad = os.path.join(tmp, f"{nummer}.pdf")
        with open(pfad, "wb") as fh:
            fh.write(daten)
        beleg_id, _, neu = blg.erfassen(
            con, mandant_id, mandant_dir, pfad,
            jahr=jahr,
            datum=_tag(stempel),
            kategorie="gutschrift" if gutschrift else "ausgangsrechnung",
            aussteller=_mandant_name(con, mandant_id),
            bezeichnung=f"{'Gutschrift' if gutschrift else 'Rechnung'} {nummer} an {kunde}".strip(),
            betrag_cent=int(inv.get("total") or 0),
            notiz=f"Stripe {inv['id']}, USt-IdNr. des Kunden: {_ust_id(inv) or 'keine'}",
        )
    con.execute("UPDATE beleg SET rohdaten=? WHERE id=?",
                (json.dumps(inv, ensure_ascii=False, sort_keys=True), beleg_id))
    return neu


# --------------------------------------------------- Guthabenbewegungen

def bewegungen(jahr: int) -> list[dict]:
    von, bis = _jahr_grenzen(jahr)
    return _liste("/balance_transactions", {"created[gte]": von, "created[lte]": bis})


def _bewegung_text(bt: dict) -> str:
    teile = [bt.get("type"), bt.get("description")]
    return " | ".join(t for t in teile if t) or bt["id"]


def importieren(con: sqlite3.Connection, mandant_id: int, mandant_dir: str, jahr: int,
                bankkonto_iban: str = "STRIPE") -> dict:
    """Rechnungen als Belege, Guthabenbewegungen als Bankumsätze."""
    z = {"rechnungen": 0, "gutschriften": 0, "bewegungen": 0, "dubletten": 0,
         "gebuehren_cent": 0, "fehler": []}

    for inv in rechnungen(jahr):
        try:
            z["rechnungen"] += _rechnung_ablegen(con, mandant_id, mandant_dir, inv, jahr)
        except ImportFehler as e:
            z["fehler"].append(str(e))
    for cn in gutschriften(jahr):
        try:
            z["gutschriften"] += _rechnung_ablegen(con, mandant_id, mandant_dir, cn, jahr,
                                                   gutschrift=True)
        except ImportFehler as e:
            z["fehler"].append(str(e))

    konto = con.execute("SELECT id FROM bankkonto WHERE mandant_id=? AND iban=?",
                        (mandant_id, bankkonto_iban)).fetchone()
    if not konto:
        return z

    for bt in bewegungen(jahr):
        # `created` ist der Tag der Bewegung, `available_on` erst der Tag, an dem
        # Stripe das Geld freigibt. Fuer den Voranmeldungszeitraum zaehlt `created`.
        tag = _tag(bt.get("created"))
        gebuehr = int(bt.get("fee") or 0)
        if bt.get("type") in GEBUEHRENARTEN:
            gebuehr = abs(int(bt["amount"]))
        z["gebuehren_cent"] += gebuehr
        try:
            con.execute(
                "INSERT INTO banktransaktion (bankkonto_id, auszug, buchungsdatum, valuta,"
                " betrag_cent, text, hash, rohdaten) VALUES (?,?,?,?,?,?,?,?)",
                # `net`, nicht `amount`: um diesen Betrag bewegt sich das
                # Guthaben. Bei einer Zahlung behaelt Stripe die Gebuehr gleich
                # ein, `amount` ist der Bruttobetrag der Rechnung und wird nie
                # gutgeschrieben. Stuende er hier, wiche das Sachkonto dauerhaft
                # um die Summe der Gebuehren ab. Brutto und Gebuehr stehen
                # weiterhin in den Rohdaten, wo die Kontierung sie liest.
                (konto["id"], (tag or "")[:7], tag, _tag(bt.get("available_on")),
                 int(bt["net"]), _bewegung_text(bt), bt["id"],
                 json.dumps(bt, ensure_ascii=False, sort_keys=True)))
            z["bewegungen"] += 1
        except sqlite3.IntegrityError:
            z["dubletten"] += 1
    return z
