# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""SQLite-Backend fuer die doppelte Buchfuehrung.

Betraege werden durchgaengig als Integer in Cent gefuehrt - niemals als Float.
"""

from __future__ import annotations

import os
import sqlite3

SCHEMA = """
PRAGMA foreign_keys = ON;

-- Mandant = Firma. Ein DB-File kann mehrere Firmen tragen.
CREATE TABLE IF NOT EXISTS mandant (
    id          INTEGER PRIMARY KEY,
    kuerzel     TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    rechtsform  TEXT,
    strasse     TEXT,
    plz         TEXT,
    ort         TEXT,
    registergericht TEXT,
    registernummer  TEXT,
    steuernummer    TEXT,
    ust_idnr        TEXT,
    finanzamt       TEXT,
    kontenrahmen    TEXT NOT NULL DEFAULT 'SKR04'
);

CREATE TABLE IF NOT EXISTS geschaeftsjahr (
    id          INTEGER PRIMARY KEY,
    mandant_id  INTEGER NOT NULL REFERENCES mandant(id),
    jahr        INTEGER NOT NULL,
    beginn      TEXT NOT NULL,
    ende        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'offen',   -- offen | abgeschlossen
    UNIQUE (mandant_id, jahr)
);

-- Kontenplan. typ steuert die Auswertung.
--   A = Aktiva, P = Passiva, E = Ertrag, X = Aufwand
CREATE TABLE IF NOT EXISTS konto (
    id          INTEGER PRIMARY KEY,
    mandant_id  INTEGER NOT NULL REFERENCES mandant(id),
    nummer      TEXT NOT NULL,
    bezeichnung TEXT NOT NULL,
    typ         TEXT NOT NULL CHECK (typ IN ('A','P','E','X')),
    -- Zuordnung zur Bilanz-/GuV-Zeile (Gliederungsposten nach HGB)
    posten      TEXT,
    -- steuerliche Behandlung, z.B. '8b_kstg_1' (Dividenden), '8b_kstg_2' (Veraeusserung)
    steuer_tag  TEXT,
    -- Kennzahl der Umsatzsteuer-Voranmeldung, auf die dieses Konto einzahlt
    -- ('81', '21', '66', '67', '47', '85' ...). NULL = umsatzsteuerlich neutral.
    ustva_kz    TEXT,
    aktiv       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (mandant_id, nummer)
);

-- Buchungssatz-Kopf
CREATE TABLE IF NOT EXISTS buchung (
    id          INTEGER PRIMARY KEY,
    gj_id       INTEGER NOT NULL REFERENCES geschaeftsjahr(id),
    nummer      INTEGER NOT NULL,          -- laufende Nummer im Journal
    datum       TEXT NOT NULL,             -- YYYY-MM-DD
    buchungstext TEXT NOT NULL,
    belegfeld   TEXT,                      -- Belegnummer / Dateiname
    art         TEXT NOT NULL DEFAULT 'lfd', -- eb | lfd | abschluss
    quelle      TEXT,                      -- z.B. banktransaktion:123
    angelegt_am TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (gj_id, nummer)
);

CREATE TABLE IF NOT EXISTS buchungszeile (
    id          INTEGER PRIMARY KEY,
    buchung_id  INTEGER NOT NULL REFERENCES buchung(id) ON DELETE CASCADE,
    konto_id    INTEGER NOT NULL REFERENCES konto(id),
    soll_cent   INTEGER NOT NULL DEFAULT 0,
    haben_cent  INTEGER NOT NULL DEFAULT 0,
    text        TEXT,
    -- Umsatzsteuer-Kennzahl dieser einzelnen Zeile, schlaegt konto.ustva_kz.
    -- Traegt vor allem die Bemessungsgrundlagen der Eingangsleistungen: Kz 46
    -- (§ 13b Abs. 1, EU) und Kz 84 (§ 13b Abs. 2, Drittland) liegen auf dem
    -- Aufwandskonto und lassen sich deshalb nicht am Konto festmachen.
    ust_schluessel TEXT,
    CHECK (soll_cent >= 0 AND haben_cent >= 0),
    CHECK (soll_cent = 0 OR haben_cent = 0)
);
CREATE INDEX IF NOT EXISTS ix_bz_buchung ON buchungszeile(buchung_id);
CREATE INDEX IF NOT EXISTS ix_bz_konto   ON buchungszeile(konto_id);

-- Bankkonten des Mandanten, verknuepft mit dem Sachkonto
CREATE TABLE IF NOT EXISTS bankkonto (
    id          INTEGER PRIMARY KEY,
    mandant_id  INTEGER NOT NULL REFERENCES mandant(id),
    iban        TEXT NOT NULL,
    bezeichnung TEXT NOT NULL,
    konto_id    INTEGER REFERENCES konto(id),
    UNIQUE (mandant_id, iban)
);

-- Importierte Kontoumsaetze (Rohdaten, unveraendert aus dem Auszug)
CREATE TABLE IF NOT EXISTS banktransaktion (
    id            INTEGER PRIMARY KEY,
    bankkonto_id  INTEGER NOT NULL REFERENCES bankkonto(id),
    auszug        TEXT,
    buchungsdatum TEXT NOT NULL,
    valuta        TEXT,
    betrag_cent   INTEGER NOT NULL,      -- + = Eingang, - = Ausgang
    text          TEXT NOT NULL,
    hash          TEXT NOT NULL,         -- Dublettenschutz
    buchung_id    INTEGER REFERENCES buchung(id) ON DELETE SET NULL,
    UNIQUE (bankkonto_id, hash)
);
CREATE INDEX IF NOT EXISTS ix_bt_datum ON banktransaktion(buchungsdatum);

-- Belege (Rechnungen, Bescheide, Kapitalabrufe, Vertraege ...)
-- Jeder Beleg wird in das Archiv kopiert und bekommt eine fortlaufende
-- Belegnummer je Jahr. sha256 verhindert doppelte Ablage.
CREATE TABLE IF NOT EXISTS beleg (
    id          INTEGER PRIMARY KEY,
    mandant_id  INTEGER NOT NULL REFERENCES mandant(id),
    jahr        INTEGER NOT NULL,
    laufnr      INTEGER NOT NULL,
    belegnr     TEXT NOT NULL,             -- z.B. 'B2025-0042'
    datum       TEXT,                      -- Belegdatum YYYY-MM-DD
    kategorie   TEXT NOT NULL DEFAULT 'sonstiges',
    aussteller  TEXT,
    bezeichnung TEXT NOT NULL,
    betrag_cent INTEGER,
    pfad        TEXT NOT NULL,             -- Pfad im Archiv (relativ zum Mandanten)
    quelle_pfad TEXT,                      -- woher der Beleg stammt
    sha256      TEXT NOT NULL,
    notiz       TEXT,
    angelegt_am TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (mandant_id, jahr, laufnr),
    UNIQUE (mandant_id, sha256)
);
CREATE INDEX IF NOT EXISTS ix_beleg_datum ON beleg(datum);
CREATE INDEX IF NOT EXISTS ix_beleg_kat   ON beleg(kategorie);

CREATE TABLE IF NOT EXISTS buchung_beleg (
    buchung_id INTEGER NOT NULL REFERENCES buchung(id) ON DELETE CASCADE,
    beleg_id   INTEGER NOT NULL REFERENCES beleg(id) ON DELETE CASCADE,
    PRIMARY KEY (buchung_id, beleg_id)
);

-- Was tatsaechlich ans Finanzamt uebermittelt wurde. Ohne diese Tabelle laesst
-- sich nicht sagen, ob ein Zeitraum erledigt ist - genau daran ist ein Mandant
-- gescheitert: fuenf Monatsanmeldungen gingen raus, wurden abgelehnt, und drei
-- Monate lang hat es niemand gemerkt.
CREATE TABLE IF NOT EXISTS voranmeldung (
    id             INTEGER PRIMARY KEY,
    mandant_id     INTEGER NOT NULL REFERENCES mandant(id),
    jahr           INTEGER NOT NULL,
    zeitraum       TEXT NOT NULL,             -- '41'..'44' Quartal, '01'..'12' Monat
    art            TEXT NOT NULL DEFAULT 'ustva',   -- ustva | zm
    status         TEXT NOT NULL DEFAULT 'offen',   -- offen | abgegeben | abgelehnt
    kennzahlen     TEXT,                      -- JSON der uebermittelten Werte
    transferticket TEXT,
    abgegeben_am   TEXT,
    zahlung_am     TEXT,
    zahlung_cent   INTEGER,
    notiz          TEXT,
    UNIQUE (mandant_id, jahr, zeitraum, art)
);

-- Vollstaendiger Journal-Blick inkl. Kontonummern
CREATE VIEW IF NOT EXISTS v_journal AS
SELECT b.gj_id, b.nummer, b.datum, b.buchungstext, b.belegfeld, b.art,
       k.nummer AS konto, k.bezeichnung AS kontobez, k.typ,
       z.soll_cent, z.haben_cent, z.text AS zeilentext
FROM buchung b
JOIN buchungszeile z ON z.buchung_id = b.id
JOIN konto k         ON k.id = z.konto_id
ORDER BY b.datum, b.nummer, z.id;
"""


# Spalten, die spaeter dazugekommen sind. CREATE TABLE IF NOT EXISTS ergaenzt
# eine bestehende Tabelle nicht - deshalb hier nachziehen.
NACHTRAEGE = [
    ("banktransaktion", "nicht_gebucht_grund", "TEXT"),
    ("konto", "ustva_kz", "TEXT"),
    ("buchungszeile", "ust_schluessel", "TEXT"),
    ("mandant", "ust_idnr", "TEXT"),
    # Rohdaten der Schnittstelle, damit sich spaeter umkontieren laesst, ohne neu
    # abzurufen. Bei Qonto das vollstaendige Transaktionsobjekt als JSON.
    ("banktransaktion", "rohdaten", "TEXT"),
    # Woher ein Beleg stammt, wenn er an einem Bankumsatz hing (Qonto-Anhang).
    # Erst darueber findet `bh link` den Weg Buchung -> Beleg.
    ("beleg", "banktransaktion_id", "INTEGER"),
    # Bei Ausgangsrechnungen das vollstaendige Stripe-Rechnungsobjekt: Kunde,
    # Land, USt-IdNr., Netto und Steuer - alles, was die Voranmeldung braucht.
    ("beleg", "rohdaten", "TEXT"),
    # Fingerabdruck des Buchungssatzes (Datum, Text, Beleg, alle Zeilen). Zusammen
    # mit `quelle` macht er den Import aus einer Datei wiederholbar: was schon so
    # in der Datenbank steht, bleibt stehen, Fehlendes kommt dazu, Verschwundenes
    # geht weg. Ohne ihn haengt jeder zweite Lauf denselben Bestand noch einmal an.
    ("buchung", "identitaet", "TEXT"),
    # Bankkonten, die nur als durchsuchbares Archiv gefuehrt werden: die Umsaetze
    # liegen vollstaendig in der Datenbank, gebucht wird aber nicht ueber sie
    # (bei der privaten Einkommensteuer wird objektweise aus der Anlage V
    # gebucht). Ohne die Marke meldet `bh pruefen` jeden solchen Bestand als
    # Abweichung.
    ("bankkonto", "nur_archiv", "INTEGER NOT NULL DEFAULT 0"),
    # Stammdaten aus der mandant.json, die der Kontenrahmen braucht: bei der
    # privaten Einkommensteuer die Mietobjekte (jedes traegt eine Anlage V),
    # dazu abweichende Kontobezeichnungen. Sie stehen hier, weil Auswertungen
    # den Rahmen aus der Datenbank aufbauen, ohne die Konfigurationsdatei zu
    # kennen.
    ("mandant", "stammdaten", "TEXT"),
]


def connect(path: str) -> sqlite3.Connection:
    neu = not os.path.exists(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    for tabelle, spalte, typ in NACHTRAEGE:
        vorhanden = {r[1] for r in con.execute(f"PRAGMA table_info({tabelle})")}
        if spalte not in vorhanden:
            con.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")
    con.commit()
    return con


# ---------------------------------------------------------------- Betraege

def cent(wert: str | int | float) -> int:
    """'1.234,56' | '1234.56' | 1234.56 -> 123456 (Cent, kaufmaennisch gerundet)."""
    if isinstance(wert, int):
        return wert
    if isinstance(wert, float):
        return int(round(wert * 100))
    s = wert.strip().replace(" ", "")
    neg = s.endswith("-") or s.startswith("-")
    s = s.strip("-")
    if "," in s:                      # deutsches Format
        s = s.replace(".", "").replace(",", ".")
    v = int(round(float(s) * 100))
    return -v if neg else v


def eur(c: int, stellen: bool = True) -> str:
    """123456 -> '1.234,56'."""
    neg = c < 0
    c = abs(c)
    s = f"{c // 100:,}".replace(",", ".")
    if stellen:
        s += "," + f"{c % 100:02d}"
    return ("-" + s) if neg else s
