"""Buchen: Stammdaten, Journal, Salden."""

from __future__ import annotations

import csv
import hashlib
import sqlite3

from .db import cent


class LedgerError(Exception):
    pass


# ------------------------------------------------------------ Stammdaten

def mandant_anlegen(con: sqlite3.Connection, **f) -> int:
    cols = ",".join(f)
    setz = ", ".join(f"{c}=excluded.{c}" for c in f if c != "kuerzel")
    con.execute(
        f"INSERT INTO mandant ({cols}) VALUES ({','.join('?' * len(f))}) "
        f"ON CONFLICT(kuerzel) DO UPDATE SET {setz}",
        tuple(f.values()),
    )
    return con.execute("SELECT id FROM mandant WHERE kuerzel=?", (f["kuerzel"],)).fetchone()[0]


def gj_anlegen(con: sqlite3.Connection, mandant_id: int, jahr: int) -> int:
    con.execute(
        "INSERT OR IGNORE INTO geschaeftsjahr (mandant_id, jahr, beginn, ende) VALUES (?,?,?,?)",
        (mandant_id, jahr, f"{jahr}-01-01", f"{jahr}-12-31"),
    )
    return con.execute(
        "SELECT id FROM geschaeftsjahr WHERE mandant_id=? AND jahr=?", (mandant_id, jahr)
    ).fetchone()[0]


def konten_anlegen(con: sqlite3.Connection, mandant_id: int, konten) -> None:
    """konten: Tupel aus dem Kontenrahmen, jeweils sechs Spalten
    (nummer, bezeichnung, typ, posten, steuer_tag, ustva_kz)."""
    con.executemany(
        "INSERT INTO konto (mandant_id, nummer, bezeichnung, typ, posten, steuer_tag, ustva_kz) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(mandant_id, nummer) DO UPDATE SET "
        "bezeichnung=excluded.bezeichnung, typ=excluded.typ, posten=excluded.posten, "
        "steuer_tag=excluded.steuer_tag, ustva_kz=excluded.ustva_kz",
        [(mandant_id, *(tuple(k) + (None,) * (6 - len(k)))) for k in konten],
    )


def konto_id(con: sqlite3.Connection, mandant_id: int, nummer: str) -> int:
    r = con.execute(
        "SELECT id FROM konto WHERE mandant_id=? AND nummer=?", (mandant_id, nummer)
    ).fetchone()
    if not r:
        raise LedgerError(f"Konto {nummer} existiert nicht im Kontenplan")
    return r[0]


# ------------------------------------------------------------ Buchen

def buchen(con: sqlite3.Connection, gj_id: int, mandant_id: int, datum: str,
           text: str, zeilen: list[tuple[str, int, int]], belegfeld: str | None = None,
           art: str = "lfd", quelle: str | None = None,
           zeilentexte: list[str | None] | None = None,
           identitaet: str | None = None) -> int:
    """zeilen: [(kontonummer, soll_cent, haben_cent[, ust_schluessel]), ...]

    Der vierte Eintrag ist die Umsatzsteuer-Kennzahl dieser Zeile. Gesetzt wird
    sie nur dort, wo sie sich nicht am Konto ablesen laesst - bei den
    Bemessungsgrundlagen der Eingangsleistungen (Kz 46 und 84).
    """
    soll = sum(z[1] for z in zeilen)
    haben = sum(z[2] for z in zeilen)
    if soll != haben:
        raise LedgerError(f"Buchung '{text}' ({datum}) unausgeglichen: "
                          f"Soll {soll} != Haben {haben}")
    if soll == 0:
        raise LedgerError(f"Buchung '{text}' ({datum}) ohne Betrag")

    nr = (con.execute("SELECT COALESCE(MAX(nummer),0)+1 FROM buchung WHERE gj_id=?",
                      (gj_id,)).fetchone()[0])
    cur = con.execute(
        "INSERT INTO buchung (gj_id, nummer, datum, buchungstext, belegfeld, art, quelle,"
        " identitaet) VALUES (?,?,?,?,?,?,?,?)",
        (gj_id, nr, datum, text, belegfeld, art, quelle, identitaet))
    bid = cur.lastrowid
    for i, z in enumerate(zeilen):
        knr, s, h = z[0], z[1], z[2]
        schluessel = z[3] if len(z) > 3 else None
        zt = zeilentexte[i] if zeilentexte and i < len(zeilentexte) else None
        con.execute(
            "INSERT INTO buchungszeile (buchung_id, konto_id, soll_cent, haben_cent, text,"
            " ust_schluessel) VALUES (?,?,?,?,?,?)",
            (bid, konto_id(con, mandant_id, knr), s, h, zt, schluessel))
    return bid


def saetze_aus_csv(pfad: str) -> list[dict]:
    """CSV-Format (Semikolon):
    datum;text;soll;haben;betrag;beleg;art;ztext;ust_soll;ust_haben
    Betrag im deutschen Format. Split-Buchungen: Folgezeilen mit leerem datum
    werden an die vorhergehende Buchung angehaengt.

    ust_soll / ust_haben tragen die Umsatzsteuer-Kennzahl der jeweiligen Seite.
    Zwei Spalten, weil die Bemessungsgrundlage einer Eingangsleistung beim
    Aufwand im Soll steht, bei einer Gutschrift darauf aber im Haben - eine
    einzelne Spalte muesste raten, welche Seite gemeint ist.

    Liest nur - geschrieben wird in `buchungen_aus_csv`.
    """
    with open(pfad, encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh, delimiter=";")
                if r.get("datum") is not None and any((v or "").strip() for v in r.values())]

    saetze: list[dict] = []
    for r in rows:
        if (r["datum"] or "").strip().startswith("#"):
            continue
        betrag = cent(r["betrag"])
        if betrag < 0:
            raise LedgerError(f"Negativer Betrag in Zeile {r}")
        zeilen, ztexte = [], []
        if (r.get("soll") or "").strip():
            zeilen.append((r["soll"].strip(), betrag, 0, (r.get("ust_soll") or "").strip() or None))
            ztexte.append(r.get("ztext") or None)
        if (r.get("haben") or "").strip():
            zeilen.append((r["haben"].strip(), 0, betrag, (r.get("ust_haben") or "").strip() or None))
            ztexte.append(r.get("ztext") or None)
        if (r["datum"] or "").strip():
            saetze.append({"datum": r["datum"].strip(), "text": r["text"].strip(),
                           "beleg": (r.get("beleg") or "").strip() or None,
                           "art": (r.get("art") or "").strip() or "lfd",
                           "zeilen": zeilen, "ztexte": ztexte})
        else:
            if not saetze:
                raise LedgerError(f"Fortsetzungszeile ohne Buchung: {r}")
            saetze[-1]["zeilen"] += zeilen
            saetze[-1]["ztexte"] += ztexte
    return saetze


def identitaet(satz: dict, lauf: int = 0) -> str:
    """Fingerabdruck eines Buchungssatzes: Datum, Text, Belegfeld, Art und jede
    Zeile mit Konto, Betrag, Zeilentext und Umsatzsteuer-Kennzahl.

    Er entscheidet beim Wiedereinlesen, ob ein Satz derselbe ist. Deshalb geht
    alles ein, was den Satz fachlich ausmacht - aendert sich eine Kontierung
    oder ein Cent, ist es ein anderer Satz und der alte verschwindet.

    `lauf` unterscheidet Saetze, die einander bis aufs Zeichen gleichen. In
    einer erzeugten Datei kommt das kaum vor, weil das Belegfeld den Bankumsatz
    nennt; von Hand geschrieben ist es moeglich, und dann muessen beide
    ueberleben statt sich gegenseitig zu verdraengen.
    """
    teile = [satz["datum"], satz["text"], satz["beleg"] or "", satz["art"]]
    for i, z in enumerate(satz["zeilen"]):
        zt = satz["ztexte"][i] if i < len(satz["ztexte"]) else None
        teile.append(f"{z[0]}|{z[1]}|{z[2]}|{z[3] or ''}|{zt or ''}")
    teile.append(str(lauf))
    return hashlib.sha256("\x1f".join(teile).encode("utf-8")).hexdigest()[:32]


def _identitaet_aus_db(con: sqlite3.Connection, buchung_id: int) -> str:
    """Denselben Fingerabdruck fuer eine Buchung, die schon in der Datenbank
    steht. Die Zeilen kommen nach `id` zurueck, also in der Reihenfolge, in der
    sie eingelesen wurden - sonst traefe der Abdruck einer Splitbuchung nicht."""
    b = con.execute("SELECT datum, buchungstext, belegfeld, art FROM buchung WHERE id=?",
                    (buchung_id,)).fetchone()
    zeilen, ztexte = [], []
    for z in con.execute(
            "SELECT k.nummer, z.soll_cent, z.haben_cent, z.ust_schluessel, z.text"
            " FROM buchungszeile z JOIN konto k ON k.id=z.konto_id"
            " WHERE z.buchung_id=? ORDER BY z.id", (buchung_id,)):
        zeilen.append((z["nummer"], z["soll_cent"], z["haben_cent"], z["ust_schluessel"]))
        ztexte.append(z["text"])
    return identitaet({"datum": b["datum"], "text": b["buchungstext"],
                       "beleg": b["belegfeld"], "art": b["art"],
                       "zeilen": zeilen, "ztexte": ztexte})


def _altbestand_uebernehmen(con: sqlite3.Connection, gj_id: int, quelle: str,
                            gesucht: set[str]) -> int:
    """Buchungen aus einer Version ohne Quellenangabe der Datei zuschlagen.

    Ohne das haenge der erste Lauf nach der Umstellung den ganzen Bestand ein
    zweites Mal an - ausgeglichen, und deshalb von der Summenprobe ungeruegt.
    Uebernommen wird nur, was der Fingerabdruck als denselben Satz ausweist;
    Buchungen aus anderen Dateien oder von Hand bleiben unberuehrt.
    """
    n = 0
    for r in con.execute("SELECT id FROM buchung WHERE gj_id=? AND quelle IS NULL"
                         " AND art<>'vorjahr'", (gj_id,)).fetchall():
        kennung = _identitaet_aus_db(con, r["id"])
        if kennung in gesucht:
            con.execute("UPDATE buchung SET quelle=?, identitaet=? WHERE id=?",
                        (quelle, kennung, r["id"]))
            gesucht.discard(kennung)
            n += 1
    return n


def buchungen_aus_csv(con: sqlite3.Connection, gj_id: int, mandant_id: int, pfad: str,
                      quelle: str | None = None) -> dict:
    """Buchungen einer Datei einlesen - wiederholbar.

    Mit `quelle` ist die Datei die Wahrheit und die Datenbank ihr Abbild: was
    unveraendert dasteht, bleibt unangetastet (samt Nummer, Verknuepfung zum
    Bankumsatz und zum Beleg), Neues kommt dazu, was aus der Datei verschwunden
    ist, wird geloescht. Derselbe Lauf zweimal aendert nichts.

    Ohne `quelle` wird angehaengt wie bisher. Das ist die alte Betriebsart und
    nur noch fuer Aufrufer da, die keine Datei als Bezug haben.
    """
    saetze = saetze_aus_csv(pfad)

    gesehen: dict[str, int] = {}
    for satz in saetze:
        kennung = identitaet(satz)
        lauf = gesehen.get(kennung, 0)
        gesehen[kennung] = lauf + 1
        satz["identitaet"] = kennung if lauf == 0 else identitaet(satz, lauf)

    vorhanden, uebernommen = {}, 0
    if quelle:
        uebernommen = _altbestand_uebernehmen(
            con, gj_id, quelle, {s["identitaet"] for s in saetze})
        vorhanden = {r["identitaet"]: r["id"] for r in con.execute(
            "SELECT id, identitaet FROM buchung WHERE gj_id=? AND quelle=?"
            " AND identitaet IS NOT NULL", (gj_id, quelle))}

    neu = 0
    for satz in saetze:
        if satz["identitaet"] in vorhanden:
            continue
        buchen(con, gj_id, mandant_id, satz["datum"], satz["text"], satz["zeilen"],
               satz["beleg"], satz["art"], quelle=quelle, zeilentexte=satz["ztexte"],
               identitaet=satz["identitaet"])
        neu += 1

    entfernt = 0
    if quelle:
        aktuell = {s["identitaet"] for s in saetze}
        veraltet = [bid for kennung, bid in vorhanden.items() if kennung not in aktuell]
        for bid in veraltet:
            con.execute("DELETE FROM buchung WHERE id=?", (bid,))
        entfernt = len(veraltet)

    return {"gesamt": len(saetze), "neu": neu, "uebernommen": uebernommen,
            "unveraendert": len(saetze) - neu, "entfernt": entfernt}


# ------------------------------------------------------------ Auswertung

def salden(con: sqlite3.Connection, gj_id: int, von: str | None = None,
           bis: str | None = None) -> list[sqlite3.Row]:
    """Kontensalden, wahlweise auf einen Zeitraum eingegrenzt.

    `von` und `bis` sind ISO-Daten und wirken auf das Buchungsdatum. Fuer die
    Bilanz wird nur `bis` gesetzt - ein Bestand ist immer der aufgelaufene Stand
    zum Stichtag, kein Zeitraumwert. Fuer die GuV zaehlt der ganze Zeitraum.
    """
    bedingung = "".join([" AND b.datum >= ?" if von else "",
                         " AND b.datum <= ?" if bis else ""])
    werte = [gj_id] + [d for d in (von, bis) if d]
    return con.execute(f"""
      SELECT k.nummer, k.bezeichnung, k.typ, k.posten, k.steuer_tag, k.ustva_kz,
             SUM(z.soll_cent)  AS soll,
             SUM(z.haben_cent) AS haben,
             SUM(z.soll_cent) - SUM(z.haben_cent) AS saldo
      FROM buchungszeile z
      JOIN buchung b ON b.id = z.buchung_id
      JOIN konto   k ON k.id = z.konto_id
      WHERE b.gj_id = ?{bedingung}
      GROUP BY k.id ORDER BY k.nummer
    """, werte).fetchall()


def probe(con: sqlite3.Connection, gj_id: int) -> tuple[int, int]:
    r = con.execute(
        "SELECT SUM(z.soll_cent), SUM(z.haben_cent) FROM buchungszeile z "
        "JOIN buchung b ON b.id=z.buchung_id WHERE b.gj_id=?", (gj_id,)).fetchone()
    return (r[0] or 0), (r[1] or 0)
