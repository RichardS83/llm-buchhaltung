# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Umsatzsteuer-Voranmeldung als Auswertung der Buchführung.

Die Kennzahlen werden nicht neben der Buchhaltung gerechnet, sondern aus ihr
abgeleitet. Jede Zahl hat damit dieselbe Herkunft wie die Bilanz, und jede lässt
sich bis zum Beleg aufklappen.

Woher eine Zeile ihre Kennzahl bekommt
--------------------------------------
Zuerst aus `buchungszeile.ust_schluessel`, sonst aus `konto.ustva_kz`. Die
Reihenfolge ist wichtig: die Bemessungsgrundlagen der Eingangsleistungen — Kz 46
(§ 13b Abs. 1, Gemeinschaftsgebiet) und Kz 84 (§ 13b Abs. 2, Drittland) — liegen
auf dem Aufwandskonto und lassen sich daher nicht am Konto festmachen. Alles
andere hängt am Konto.

Das Vorzeichen ergibt sich aus der Kontenart: Ertrags- und Passivkonten zählen im
Haben, Aktiv- und Aufwandskonten im Soll. Damit stimmt es für alle Kennzahlen,
ohne je Kennzahl eine Regel zu brauchen.

Warum die Steuer nicht einfach aus den Steuerkonten kommt
---------------------------------------------------------
Elster prüft Kz 47 gegen Kz 46 und Kz 85 gegen Kz 84 — und rechnet dabei mit der
auf volle Euro **abgeschnittenen** Bemessungsgrundlage. Gebucht wird dagegen je
Rechnung auf den Cent. Beides darf um ein paar Cent auseinanderliegen. Für die
Anmeldung gilt die Elster-Rechnung, die gebuchten Beträge werden daneben als
Probe ausgewiesen. Kz 66 dagegen kommt unverändert aus der Buchführung: das ist
der einzige Eingangsposten, der die Zahllast wirklich mindert.
"""

from __future__ import annotations

import datetime
import json
import sqlite3

# Bemessungsgrundlagen in vollen Euro, Steuerbeträge mit Cent.
BASEN = ("81", "86", "21", "45", "46", "84")
STEUERN = ("47", "85", "66", "67", "83")

BEZEICHNUNG = {
    "81": "Umsätze zum Steuersatz von 19 %",
    "86": "Umsätze zum Steuersatz von 7 %",
    "21": "Nicht steuerbare sonstige Leistungen (§ 18b Satz 1 Nr. 2 UStG)",
    "45": "Übrige nicht steuerbare Umsätze (Leistungsort nicht im Inland)",
    "46": "Leistungen eines im übrigen Gemeinschaftsgebiet ansässigen Unternehmers",
    "47": "Steuer auf die Leistungen in Kz 46",
    "84": "Andere Leistungen eines im Ausland ansässigen Unternehmers",
    "85": "Steuer auf die Leistungen in Kz 84",
    "66": "Vorsteuerbeträge aus Rechnungen von anderen Unternehmern",
    "67": "Vorsteuerbeträge aus Leistungen im Sinne des § 13b UStG",
    "83": "Verbleibende Umsatzsteuer-Vorauszahlung",
    "81s": "Umsatzsteuer 19 % auf die eigenen Erlöse (rechnet Elster selbst)",
}

QUARTAL_MONATE = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}

# Schluessel fuer eine Zeile, die auf einem Steuerkonto liegt, aber keine
# Voranmeldung beruehrt: die Umbuchung der Steuerkonten zum Bilanzstichtag.
# Ohne ihn wuerde der Abschluss die Zahlen des vierten Quartals wieder aufheben -
# das Konto 1406 wird dabei ja glattgestellt.
NEUTRAL = "-"


# ---------------------------------------------------------------- Zeitraum

class Zeitraum:
    """Ein Voranmeldungszeitraum — Quartal oder Monat."""

    def __init__(self, jahr: int, quartal: int | None = None, monat: int | None = None):
        self.jahr = jahr
        self.quartal = quartal
        self.monat = monat
        if quartal:
            von_m, bis_m = QUARTAL_MONATE[quartal]
            self.code = str(40 + quartal)
            self.bezeichnung = f"{quartal}. Kalendervierteljahr {jahr}"
        elif monat:
            von_m = bis_m = monat
            self.code = f"{monat:02d}"
            self.bezeichnung = f"{monat:02d}/{jahr}"
        else:
            raise ValueError("Quartal oder Monat angeben")
        self.von = datetime.date(jahr, von_m, 1)
        self.bis = _monatsende(jahr, bis_m)

    @property
    def label(self) -> str:
        return f"{self.jahr}-Q{self.quartal}" if self.quartal else f"{self.jahr}-{self.monat:02d}"

    def hat_dauerfrist(self, ab: int | None) -> bool:
        return ab is not None and self.jahr >= ab

    def frist(self, dauerfristverlaengerung: bool | int | None = False) -> datetime.date:
        """Der 10. des Folgemonats, mit Dauerfristverlängerung einen Monat später.

        Fällt der Tag auf ein Wochenende, verschiebt § 108 Abs. 3 AO ihn auf den
        nächsten Werktag. Gesetzliche Feiertage sind hier nicht hinterlegt — auf
        einen 10. fällt in Berlin kein fester Feiertag.
        """
        monate = 2 if dauerfristverlaengerung else 1
        m = self.bis.month + monate
        j, m = self.bis.year + (m - 1) // 12, (m - 1) % 12 + 1
        tag = datetime.date(j, m, 10)
        while tag.weekday() >= 5:
            tag += datetime.timedelta(days=1)
        return tag


def zeitraum_aus_code(jahr: int, code: str) -> Zeitraum:
    """'41'..'44' -> Quartal, '01'..'12' -> Monat."""
    code = code.strip()
    n = int(code)
    return Zeitraum(jahr, quartal=n - 40) if n > 40 else Zeitraum(jahr, monat=n)


def _monatsende(jahr: int, monat: int) -> datetime.date:
    import calendar
    return datetime.date(jahr, monat, calendar.monthrange(jahr, monat)[1])


def quartale(con: sqlite3.Connection, mandant_id: int) -> list[Zeitraum]:
    """Alle Quartale, in denen der Mandant gebucht hat."""
    r = con.execute("""
        SELECT MIN(b.datum) AS erste, MAX(b.datum) AS letzte FROM buchung b
        JOIN geschaeftsjahr g ON g.id=b.gj_id WHERE g.mandant_id=?""",
        (mandant_id,)).fetchone()
    if not r or not r["erste"]:
        return []
    erste, letzte = r["erste"], r["letzte"]
    aus = []
    jahr, q = int(erste[:4]), (int(erste[5:7]) - 1) // 3 + 1
    while (jahr, q) <= (int(letzte[:4]), (int(letzte[5:7]) - 1) // 3 + 1):
        aus.append(Zeitraum(jahr, quartal=q))
        q += 1
        if q > 4:
            jahr, q = jahr + 1, 1
    return aus


# ------------------------------------------------------------- Kennzahlen

def _summen(con: sqlite3.Connection, mandant_id: int, von: str, bis: str) -> dict[str, int]:
    """Kennzahl -> Betrag in Cent, in der natürlichen Richtung des Kontos."""
    rows = con.execute("""
        SELECT COALESCE(z.ust_schluessel, k.ustva_kz) AS kz, k.typ,
               SUM(z.soll_cent) AS soll, SUM(z.haben_cent) AS haben
        FROM buchungszeile z
        JOIN buchung b ON b.id = z.buchung_id
        JOIN konto   k ON k.id = z.konto_id
        JOIN geschaeftsjahr g ON g.id = b.gj_id
        WHERE g.mandant_id = ? AND b.datum BETWEEN ? AND ?
          AND COALESCE(z.ust_schluessel, k.ustva_kz) IS NOT NULL
          AND COALESCE(z.ust_schluessel, k.ustva_kz) <> '-'
        GROUP BY kz, k.typ""", (mandant_id, von, bis)).fetchall()
    aus: dict[str, int] = {}
    for r in rows:
        # Ertrag und Passiva stehen im Haben, Aktiva und Aufwand im Soll.
        wert = (r["haben"] - r["soll"]) if r["typ"] in ("E", "P") else (r["soll"] - r["haben"])
        aus[r["kz"]] = aus.get(r["kz"], 0) + wert
    return aus


def _abschneiden(cent: int) -> int:
    """Bemessungsgrundlagen werden in vollen Euro erklärt, Cent entfallen.
    Auch bei negativen Beträgen wird zur Null hin abgeschnitten."""
    return int(cent / 100) if cent >= 0 else -int(-cent / 100)


def _steuer_auf(euro: int, satz: int = 19) -> int:
    """Steuer auf eine Bemessungsgrundlage in vollen Euro, Ergebnis in Cent.
    Gerechnet wie Elster: auf die abgeschnittene Grundlage."""
    return round(euro * satz)


def kennzahlen(con: sqlite3.Connection, mandant_id: int, z: Zeitraum,
               satz: int = 19, ermaessigt: int = 7,
               reverse_charge_abziehbar: bool = True) -> dict:
    roh = _summen(con, mandant_id, z.von.isoformat(), z.bis.isoformat())

    basen = {kz: _abschneiden(roh.get(kz, 0)) for kz in BASEN}

    kz47 = _steuer_auf(basen["46"], satz)
    kz85 = _steuer_auf(basen["84"], satz)
    kz66 = roh.get("66", 0)
    kz67 = (kz47 + kz85) if reverse_charge_abziehbar else 0

    steuer81 = _steuer_auf(basen["81"], satz)
    steuer86 = _steuer_auf(basen["86"], ermaessigt)
    umsatzsteuer = steuer81 + steuer86 + kz47 + kz85
    vorsteuer = kz66 + kz67
    kz83 = umsatzsteuer - vorsteuer

    return {
        "zeitraum": z.label,
        "code": z.code,
        "bezeichnung": z.bezeichnung,
        "von": z.von.isoformat(),
        "bis": z.bis.isoformat(),
        "basen": basen,
        "steuern": {"47": kz47, "85": kz85, "66": kz66, "67": kz67, "83": kz83},
        "kontrolle": {
            "steuer_81": steuer81,
            "steuer_86": steuer86,
            "umsatzsteuer": umsatzsteuer,
            "vorsteuer": vorsteuer,
        },
        # Was tatsaechlich auf den Steuerkonten steht. Weicht durch das Abschneiden
        # der Bemessungsgrundlage um wenige Cent ab; groessere Differenzen sind ein
        # Zeichen fuer einen Buchungsfehler.
        "gebucht": {kz: roh.get(kz, 0) for kz in ("47", "85", "66", "67", "81s")},
        "abweichung": {
            "47": roh.get("47", 0) - kz47,
            "85": roh.get("85", 0) - kz85,
            "81s": roh.get("81s", 0) - steuer81,
        },
        "roh": roh,
    }


# --------------------------------------------------- Herkunft einer Kennzahl

def herkunft(con: sqlite3.Connection, mandant_id: int, z: Zeitraum, kz: str) -> list[dict]:
    """Die einzelnen Buchungszeilen hinter einer Kennzahl — Grundlage des Aufklappens."""
    return [dict(r) for r in con.execute("""
        SELECT b.id AS buchung_id, b.nummer, b.datum, b.buchungstext, b.belegfeld,
               k.nummer AS konto, k.bezeichnung AS kontobez, k.typ,
               z.soll_cent, z.haben_cent, z.text AS zeilentext,
               CASE WHEN k.typ IN ('E','P') THEN z.haben_cent - z.soll_cent
                    ELSE z.soll_cent - z.haben_cent END AS betrag,
               (SELECT COUNT(*) FROM buchung_beleg v WHERE v.buchung_id=b.id) AS belege
        FROM buchungszeile z
        JOIN buchung b ON b.id = z.buchung_id
        JOIN konto   k ON k.id = z.konto_id
        JOIN geschaeftsjahr g ON g.id = b.gj_id
        WHERE g.mandant_id = ? AND b.datum BETWEEN ? AND ?
          AND COALESCE(z.ust_schluessel, k.ustva_kz) = ?
        ORDER BY b.datum, b.nummer""",
        (mandant_id, z.von.isoformat(), z.bis.isoformat(), kz))]


# ------------------------------------------- Zusammenfassende Meldung

def zusammenfassende_meldung(con: sqlite3.Connection, mandant_id: int, z: Zeitraum) -> dict:
    """Kz 21 aufgeschlüsselt nach USt-IdNr. des Kunden.

    Das Bundeszentralamt gleicht die Meldung gegen Kz 21 der Voranmeldung ab —
    beide müssen denselben Zeitraum und dieselbe Summe zeigen. Die
    Dauerfristverlängerung gilt hier **nicht** (§ 18a Abs. 11 UStG): Frist ist
    stets der 25. nach Quartalsende.
    """
    zeilen = herkunft(con, mandant_id, z, "21")
    # Die USt-IdNr. steht im Zeilentext der Erlösbuchung ("CZ, USt-IdNr. CZ25534653")
    nach_id: dict[str, dict] = {}
    for r in zeilen:
        kennung = "ohne USt-IdNr."
        for stueck in (r["zeilentext"] or "").split(","):
            if "USt-IdNr." in stueck:
                kennung = stueck.split("USt-IdNr.")[-1].strip() or kennung
        eintrag = nach_id.setdefault(kennung, {"ust_idnr": kennung, "betrag": 0, "zeilen": []})
        eintrag["betrag"] += r["betrag"]
        eintrag["zeilen"].append(r)

    faellig = _naechster_werktag(_plus_tage(z.bis, 25))
    return {
        "summe": sum(e["betrag"] for e in nach_id.values()),
        "kunden": sorted(nach_id.values(), key=lambda e: -e["betrag"]),
        "frist": faellig.isoformat(),
        "hinweis": "Die Dauerfristverlängerung gilt für die Zusammenfassende Meldung "
                   "nicht (§ 18a Abs. 11 UStG).",
    }


def _plus_tage(bis: datetime.date, tag_im_folgemonat: int) -> datetime.date:
    m = bis.month + 1
    j, m = bis.year + (m - 1) // 12, (m - 1) % 12 + 1
    return datetime.date(j, m, tag_im_folgemonat)


def _naechster_werktag(tag: datetime.date) -> datetime.date:
    while tag.weekday() >= 5:
        tag += datetime.timedelta(days=1)
    return tag


# ------------------------------------------------------- Abgabestand

def stand(con: sqlite3.Connection, mandant_id: int, z: Zeitraum, art: str = "ustva") -> dict:
    r = con.execute(
        "SELECT * FROM voranmeldung WHERE mandant_id=? AND jahr=? AND zeitraum=? AND art=?",
        (mandant_id, z.jahr, z.code, art)).fetchone()
    if not r:
        return {"status": "offen", "transferticket": None, "abgegeben_am": None,
                "zahlung_am": None, "kennzahlen": None, "notiz": None}
    d = dict(r)
    d["kennzahlen"] = json.loads(d["kennzahlen"]) if d["kennzahlen"] else None
    return d


def einstellungen(mandant_dir: str) -> dict:
    """Der Block `umsatzsteuer` aus mandant.json — Besteuerungsart, Zeitraum,
    Dauerfristverlängerung. Fehlt er, gilt das Übliche: Soll, vierteljährlich,
    ohne Fristverlängerung."""
    import os
    pfad = os.path.join(mandant_dir, "mandant.json")
    if os.path.exists(pfad):
        with open(pfad, encoding="utf-8") as fh:
            block = (json.load(fh) or {}).get("umsatzsteuer") or {}
    else:
        block = {}
    # Die Dauerfristverlaengerung laeuft ab dem Jahr, in dem sie beantragt wurde,
    # bis zum Widerruf. Sie gilt deshalb nicht rueckwirkend: das vierte Quartal
    # 2025 war am 10.01.2026 faellig, nicht am 10.02.
    ab = block.get("dauerfristverlaengerung_ab")
    if ab is None and block.get("dauerfristverlaengerung"):
        ab = 0                                   # aeltere Schreibweise: gilt immer
    return {
        "besteuerung": block.get("besteuerung", "soll"),
        "zeitraum": block.get("zeitraum", "vierteljaehrlich"),
        "dauerfristverlaengerung_ab": ab,
        "satz": int(round(float(block.get("steuersatz", 0.19)) * 100)),
        "ermaessigt": int(round(float(block.get("steuersatz_ermaessigt", 0.07)) * 100)),
        "reverse_charge_abziehbar": bool(block.get("reverse_charge_voll_abziehbar", True)),
        "hinweis": block.get("hinweis", ""),
    }


def eingabeblatt(con: sqlite3.Connection, mandant_id: int, z: Zeitraum,
                 mandant_dir: str) -> str:
    """Das, was in Mein Elster einzutippen ist — als Text zum Danebenlegen."""
    e = einstellungen(mandant_dir)
    k = kennzahlen(con, mandant_id, z, e["satz"], e["ermaessigt"],
                   e["reverse_charge_abziehbar"])
    m = con.execute("SELECT * FROM mandant WHERE id=?", (mandant_id,)).fetchone()
    st = stand(con, mandant_id, z)
    zm = zusammenfassende_meldung(con, mandant_id, z)

    def euro(c: int) -> str:
        from .db import eur
        return eur(c)

    b, w = [], 78
    b.append("=" * w)
    b.append(f" UMSATZSTEUER-VORANMELDUNG — {m['name']}")
    b.append("=" * w)
    b.append(f" Zeitraum       {z.code} ({z.bezeichnung})")
    b.append(f"                {z.von.strftime('%d.%m.%Y')} – {z.bis.strftime('%d.%m.%Y')}")
    b.append(f" Steuernummer   {m['steuernummer']}")
    b.append(f" USt-IdNr.      {m['ust_idnr'] or '—'}")
    b.append(f" Besteuerung    {'Soll (vereinbarte Entgelte)' if e['besteuerung'] == 'soll' else 'Ist (vereinnahmte Entgelte)'}")
    frist = z.frist(z.hat_dauerfrist(e["dauerfristverlaengerung_ab"]))
    b.append(f" Abgabefrist    {frist.strftime('%d.%m.%Y')}"
             + ("  (mit Dauerfristverlängerung)"
                if z.hat_dauerfrist(e["dauerfristverlaengerung_ab"]) else ""))
    b.append(f" Stand          {st['status']}"
             + (f", Transferticket {st['transferticket']}" if st.get("transferticket") else ""))
    b.append("")
    b.append("-" * w)
    b.append(" IN MEIN ELSTER EINTRAGEN")
    b.append("-" * w)
    b.append("")
    b.append(f" {'Kz':<4} {'Bezeichnung':<52} {'Betrag':>15}")
    b.append(f" {'-' * 4} {'-' * 52} {'-' * 15}")
    for kz in ("81", "86", "21", "45", "46", "47", "84", "85", "66", "67", "83"):
        if kz in BASEN:
            wert = k["basen"][kz]
            if not wert:
                continue
            gezeigt = f"{wert:,}".replace(",", ".")
        else:
            wert = k["steuern"][kz]
            if not wert and kz != "83":
                continue
            gezeigt = euro(wert)
        if kz == "83":
            b.append(f" {'-' * 4} {'-' * 52} {'-' * 15}")
        b.append(f" {kz:<4} {BEZEICHNUNG[kz][:52]:<52} {gezeigt:>15}")
    b.append("")
    b.append(" Bemessungsgrundlagen in vollen Euro, Steuerbeträge mit Cent.")
    b.append(" Die Steuer auf Kz 81 und 86 rechnet Elster selbst — nicht eintragen.")
    b.append("")
    b.append("-" * w)
    b.append(" PROBE GEGEN DIE BUCHFÜHRUNG")
    b.append("-" * w)
    for kz, gebucht in sorted(k["gebucht"].items()):
        if not gebucht and not k["abweichung"].get(kz):
            continue
        ab = k["abweichung"].get(kz)
        hinweis = ""
        if ab:
            hinweis = (f"  Abweichung {euro(ab)} — Rundung des Abschneidens"
                       if abs(ab) <= 100 else f"  ABWEICHUNG {euro(ab)} — prüfen")
        b.append(f"   Kz {kz:<4} gebucht {euro(gebucht):>12}{hinweis}")
    b.append(f"   {'Umsatzsteuer gesamt':<34}{euro(k['kontrolle']['umsatzsteuer']):>14}")
    b.append(f"   {'abzüglich Vorsteuer':<34}{euro(k['kontrolle']['vorsteuer']):>14}")
    b.append(f"   {'= Kz 83':<34}{euro(k['steuern']['83']):>14}")
    b.append("")
    if zm["summe"]:
        b.append("-" * w)
        b.append(" ZUSAMMENFASSENDE MELDUNG")
        b.append("-" * w)
        faellig = datetime.date.fromisoformat(zm["frist"]).strftime("%d.%m.%Y")
        b.append(f"   Kz 21 = {euro(zm['summe'])} EUR, Frist {faellig}")
        for kunde in zm["kunden"]:
            b.append(f"   {kunde['ust_idnr']:<20}{euro(kunde['betrag']):>12} EUR")
        b.append(f"   {zm['hinweis']}")
        b.append("")
    if k["steuern"]["83"] > 0:
        b.append(f"   Zahlung {euro(k['steuern']['83'])} EUR bis {frist.strftime('%d.%m.%Y')}.")
    elif k["steuern"]["83"] < 0:
        b.append(f"   Erstattungsanspruch {euro(-k['steuern']['83'])} EUR.")
    b.append("=" * w)
    return "\n".join(b)


def festhalten(con: sqlite3.Connection, mandant_id: int, z: Zeitraum, status: str,
               transferticket: str | None = None, abgegeben_am: str | None = None,
               kennzahlen_werte: dict | None = None, zahlung_am: str | None = None,
               zahlung_cent: int | None = None, notiz: str | None = None,
               art: str = "ustva") -> None:
    """Festhalten, was übermittelt wurde. Ohne diesen Eintrag lässt sich später
    nicht sagen, ob ein Zeitraum erledigt ist."""
    con.execute("""
        INSERT INTO voranmeldung (mandant_id, jahr, zeitraum, art, status, kennzahlen,
                                  transferticket, abgegeben_am, zahlung_am, zahlung_cent, notiz)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (mandant_id, jahr, zeitraum, art) DO UPDATE SET
            status=excluded.status,
            kennzahlen=COALESCE(excluded.kennzahlen, voranmeldung.kennzahlen),
            transferticket=COALESCE(excluded.transferticket, voranmeldung.transferticket),
            abgegeben_am=COALESCE(excluded.abgegeben_am, voranmeldung.abgegeben_am),
            zahlung_am=COALESCE(excluded.zahlung_am, voranmeldung.zahlung_am),
            zahlung_cent=COALESCE(excluded.zahlung_cent, voranmeldung.zahlung_cent),
            notiz=COALESCE(excluded.notiz, voranmeldung.notiz)""",
        (mandant_id, z.jahr, z.code, art, status,
         json.dumps(kennzahlen_werte, ensure_ascii=False) if kennzahlen_werte else None,
         transferticket, abgegeben_am, zahlung_am, zahlung_cent, notiz))
