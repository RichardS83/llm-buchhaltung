# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Lokale Weboberflaeche - `bh serve <mandant> <jahr>`.

Nur Standardbibliothek, kein Framework, keine Internetverbindung. Der Server
laeuft auf 127.0.0.1 und liest ausschliesslich - er schreibt nichts in die
Datenbank. Damit ist die Oberflaeche eine Lesebrille auf den Abschluss, kein
zweiter Buchungsweg.

**Jede Zahl laesst sich aufklappen.** Das ist der tragende Grundsatz der
Oberflaeche, kein Zusatz einzelner Seiten: wo eine Zahl steht, die aus Konten
stammt, fuehrt ein Klick zu den Buchungen dahinter, von dort zum ganzen
Buchungssatz und von dort zum Beleg.

    Bilanzposten -> Konto -> Buchungszeile -> Buchungssatz -> Bankumsatz -> Beleg-PDF
    Zeile der Anlage V ---^
    Zeile der Steuererklaerung ---^
    Zeile der Saldenliste ---^

Wer eine neue Seite baut, die Betraege aus Konten zeigt, haengt sie an dieselbe
Kette: `/api/buchungen/<konto>,<konto>,...` liefert die Buchungszeilen, im
Frontend erzeugen `einbauZeile()` und der zentrale Klick-Empfaenger den Rest.
Eine Zahl ohne Weg zu ihrer Herkunft ist eine Behauptung.

Belege werden unter /beleg/<id>/datei direkt aus dem Archiv ausgeliefert.
"""

from __future__ import annotations

import datetime
import http.server
import json
import mimetypes
import os
import posixpath
import socketserver
import sqlite3
import threading
import urllib.parse
import webbrowser

from . import db, fristen, offen, pruefung, reports, steuern, umsatzsteuer, verknuepfung, vorjahr
from . import zeitraum as zr
from .kontenplan import aus_mandant as rahmen_aus_mandant
from .kontenplan import lade as lade_rahmen

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

DOKUMENTE = [
    ("jahresabschluss", "Jahresabschluss", "jahresabschluss_{jahr}.md"),
    ("steuererklaerungen", "Steuererklärungen", "steuererklaerungen_{jahr}.md"),
    ("anlagenverzeichnis", "Anlagenverzeichnis", "anlagenverzeichnis_{jahr}.md"),
    ("pruefungsbericht", "Prüfungsbericht", "pruefung_{jahr}.md"),
    ("bilanzberichtigung", "Bilanzberichtigung", "bilanzberichtigung_{jahr}.md"),
    ("antrag_ao", "Antrag § 164 Abs. 2 AO", "antrag_164_abs_2_ao.md"),
    ("elster", "ELSTER-Kennzahlen", "elster_kennzahlen_{jahr}.md"),
    ("offene_punkte", "Offene Punkte (Lesefassung)", "offene_punkte.md"),
    ("zinsberechnung", "Zinsberechnung Gesellschafterdarlehen", "zinsberechnung_1309.txt"),
    # Einkommensteuer: dieselbe Mechanik, andere Dateinamen.
    ("einkommensteuer", "Einkommensteuererklärung", "einkommensteuer_{jahr}.md"),
    ("ug_zinsen", "Gesellschafterdarlehen je Objekt", "ug_zinsen_{jahr}.txt"),
    ("bankzinsen", "Schuldzinsen der Bankdarlehen", "bankzinsen_{jahr}.txt"),
    ("bauzahlungen", "Bauzahlungen nach Objekt", "bauzahlungen_{jahr}.txt"),
    ("kostenaufstellung", "Kostenaufstellung", "kostenaufstellung_{jahr}.md"),
]


class NichtGefunden(Exception):
    """Mandant oder Geschaeftsjahr gibt es nicht.

    Bewusst kein SystemExit: das erbt von BaseException und rutscht durch jedes
    `except Exception` - im Server stirbt dann der Anfrage-Thread, ohne zu
    antworten, und der Browser wartet endlos auf eine Seite.
    """


class Kontext:
    """Alles, was der Server ueber den angezeigten Abschluss wissen muss."""

    def __init__(self, db_pfad: str, wurzel: str, kuerzel: str, jahr: int):
        self.db_pfad = db_pfad
        self.wurzel = wurzel
        self.kuerzel = kuerzel
        self.jahr = jahr
        self.mandant_dir = os.path.join(wurzel, "mandanten", kuerzel)
        self.jahr_dir = os.path.join(self.mandant_dir, str(jahr))
        self._lokal = threading.local()

        con = self.con()
        m = con.execute("SELECT * FROM mandant WHERE kuerzel=?", (kuerzel,)).fetchone()
        if not m:
            raise NichtGefunden(f"Mandant '{kuerzel}' ist unbekannt.")
        self.mandant = dict(m)
        g = con.execute("SELECT id, beginn, ende FROM geschaeftsjahr"
                        " WHERE mandant_id=? AND jahr=?", (m["id"], jahr)).fetchone()
        if not g:
            jahre = [str(r[0]) for r in con.execute(
                "SELECT jahr FROM geschaeftsjahr WHERE mandant_id=? ORDER BY jahr DESC",
                (m["id"],))]
            raise NichtGefunden(
                f"{m['name']} hat kein Geschäftsjahr {jahr}."
                + (f" Vorhanden: {', '.join(jahre)}." if jahre else " Es ist keines angelegt."))
        self.mandant_id = m["id"]
        self.gj_id = g["id"]
        self.gj_beginn, self.gj_ende = g["beginn"], g["ende"]
        self.zeitraum = self.zeitraum_aufloesen(None)   # Vorgabe: ganzes Jahr

    def letzte_buchung(self) -> str | None:
        r = self.con().execute("SELECT MAX(datum) FROM buchung WHERE gj_id=?",
                               (self.gj_id,)).fetchone()
        return r[0]

    def zeitraum_aufloesen(self, code: str | None) -> zr.Zeitraum:
        return zr.aufloesen(code, self.gj_beginn, self.gj_ende, self.letzte_buchung())

    def mit_zeitraum(self, code: str | None) -> "Kontext":
        """Flache Kopie mit anderem Zeitraum - der Kontext selbst bleibt geteilt."""
        if not code or code == self.zeitraum.code:
            return self
        k = object.__new__(Kontext)
        k.__dict__ = dict(self.__dict__)
        k.zeitraum = self.zeitraum_aufloesen(code)
        return k

    def vj_vergleich(self) -> tuple[int | None, str | None, str | None]:
        """Vorjahresspalte: (gj_id, von, bis) - oder dreimal None.

        Der Ausschnitt wandert mit, sonst stuende Januar gegen ein volles Jahr.
        Liegt im Vorjahr in genau diesem Fenster keine Buchung, entfaellt die
        Spalte ganz: ein uebernommenes Vorjahr traegt nur eine Buchung zum
        31.12., und eine Null daneben liest sich wie eine leere Bilanz.
        """
        vj = self.vj_gj_id()
        if not vj:
            return None, None, None
        von, bis = zr.verschoben(self.zeitraum)
        hat = self.con().execute(
            "SELECT 1 FROM buchung WHERE gj_id=? AND datum BETWEEN ? AND ? LIMIT 1",
            (vj, von, bis)).fetchone()
        return (vj, von, bis) if hat else (None, None, None)

    def con(self) -> sqlite3.Connection:
        # sqlite3-Verbindungen sind nicht threadsicher, der Server ist mehrlaeufig
        if not getattr(self._lokal, "con", None):
            self._lokal.con = db.connect(self.db_pfad)
        return self._lokal.con

    def vj_gj_id(self) -> int | None:
        """Geschaeftsjahr des Vorjahres, falls uebernommen (`bh vorjahr`)."""
        return vorjahr.gj_id(self.con(), self.mandant_id, self.jahr - 1)

    def json_datei(self, name: str) -> dict | None:
        pfad = os.path.join(self.jahr_dir, name)
        if os.path.exists(pfad):
            with open(pfad, encoding="utf-8") as fh:
                return json.load(fh)
        return None


# Die Oberflaeche traegt mehrere Mandanten und Jahre. Welcher gemeint ist, steht
# in der Anfrage - der Kontext wandert damit aus dem Prozess in die Adresse.
_KONTEXTE: dict[tuple[str, int], Kontext] = {}
_SPERRE = threading.Lock()


def db_fuer(db_pfade: list[str], kuerzel: str) -> str:
    """In welcher Datenbank liegt dieser Mandant?

    Die Oberflaeche zeigt die Gesellschaften und die private Einkommensteuer
    nebeneinander, die Dateien bleiben aber getrennt (§ 147 Abs. 6 AO, siehe
    `bh --db`). Gesucht wird deshalb in allen mitgegebenen Datenbanken.
    """
    for pfad in db_pfade:
        if not os.path.exists(pfad):
            continue
        if db.connect(pfad).execute("SELECT 1 FROM mandant WHERE kuerzel=?",
                                    (kuerzel,)).fetchone():
            return pfad
    raise NichtGefunden(f"Mandant '{kuerzel}' ist unbekannt.")


def hole_kontext(db_pfade: list[str], wurzel: str, kuerzel: str, jahr: int) -> Kontext:
    with _SPERRE:
        schluessel = (kuerzel, jahr)
        if schluessel not in _KONTEXTE:
            _KONTEXTE[schluessel] = Kontext(db_fuer(db_pfade, kuerzel), wurzel, kuerzel, jahr)
        return _KONTEXTE[schluessel]


def mandanten(db_pfade: list[str]) -> dict:
    """Alle Mandanten mit ihren Geschaeftsjahren - fuer den Umschalter im Kopf."""
    aus = []
    for pfad in db_pfade:
        if not os.path.exists(pfad):
            continue
        con = db.connect(pfad)
        for m in con.execute("SELECT id, kuerzel, name, kontenrahmen, stammdaten FROM mandant ORDER BY name"):
            jahre = [r[0] for r in con.execute(
                "SELECT jahr FROM geschaeftsjahr WHERE mandant_id=? ORDER BY jahr DESC",
                (m["id"],))]
            aus.append({"kuerzel": m["kuerzel"], "name": m["name"], "jahre": jahre,
                        "art": ("privat" if rahmen_aus_mandant(m).ueberschussrechnung
                                else "gesellschaft")})
    return {"mandanten": aus}


# ------------------------------------------------------------------ Abfragen

def bilanz(k: Kontext) -> dict:
    """Bestandsrechnung: nur der Stichtag zaehlt, nicht der Zeitraumanfang."""
    vj, _, vj_bis = k.vj_vergleich()
    d = reports.bilanz_daten(k.con(), k.gj_id, vj,
                             bis=k.zeitraum.bis.isoformat(), vj_bis=vj_bis)
    d["zeitraum"] = k.zeitraum.als_dict()
    return d


def guv(k: Kontext) -> dict:
    """Zeitraumrechnung: Anfang und Ende wirken beide."""
    vj, vj_von, vj_bis = k.vj_vergleich()
    d = reports.guv_daten(k.con(), k.gj_id, vj,
                          von=k.zeitraum.von.isoformat(), bis=k.zeitraum.bis.isoformat(),
                          vj_von=vj_von, vj_bis=vj_bis)
    d["zeitraum"] = k.zeitraum.als_dict()
    return d


def anlagen(k: Kontext) -> dict:
    """Alle Anlagen V des Zeitraums - der Kern der privaten Erklaerung."""
    vj, vj_von, vj_bis = k.vj_vergleich()
    d = reports.anlagen_daten(k.con(), k.gj_id, vj,
                              von=k.zeitraum.von.isoformat(), bis=k.zeitraum.bis.isoformat(),
                              vj_von=vj_von, vj_bis=vj_bis)
    d["zeitraum"] = k.zeitraum.als_dict()
    return d


def anlage(k: Kontext, anlage_id: str) -> dict:
    vj, vj_von, vj_bis = k.vj_vergleich()
    try:
        d = reports.anlage_daten(k.con(), k.gj_id, anlage_id, vj,
                                 von=k.zeitraum.von.isoformat(),
                                 bis=k.zeitraum.bis.isoformat(),
                                 vj_von=vj_von, vj_bis=vj_bis)
    except KeyError as e:
        raise NichtGefunden(str(e).strip("'"))
    d["zeitraum"] = k.zeitraum.als_dict()
    return d


def pruefliste(k: Kontext) -> dict:
    """Regelverstoesse - dieselbe Pruefung wie `bh pruefen`, nur im Browser."""
    d = pruefung.alles(k.con(), k.gj_id, reports.rahmen(k.con(), k.gj_id), k.jahr_dir)
    d["zeitraum"] = k.zeitraum.als_dict()
    return d


def zeitraeume(k: Kontext) -> dict:
    """Auswahlliste, gewaehlter Zeitraum und die Zaehler der Seitenleiste.

    Bewusst ein eigener, billiger Endpunkt: die Seitenleiste soll bei jedem
    Wechsel stimmen, ohne dafuer die ganze Uebersicht zu rechnen.
    """
    con = k.con()
    zeit = k.zeitraum
    r = reports.rahmen(con, k.gj_id)
    return {
        "zeitraeume": zr.auswahl(k.gj_beginn, k.gj_ende),
        "gewaehlt": zeit.als_dict(),
        "mandant": k.mandant["name"],
        # Womit die Oberflaeche es zu tun hat: Jahresabschluss oder Erklaerung.
        # Die Menuepunkte und die Ueberschriften haengen daran.
        "art": "privat" if r.ueberschussrechnung else "gesellschaft",
        "titel": {"bilanz": r.titel_bilanz, "guv": r.titel_guv,
                  "ergebnis": r.titel_ergebnis, "steuern": r.titel_steuern},
        "anlagen": [{"id": a["id"], "titel": a["titel"], "nr": a["nr"]} for a in r.anlagen],
        "bestand": {
            "buchungen": con.execute(
                "SELECT COUNT(*) FROM buchung WHERE gj_id=? AND datum BETWEEN ? AND ?",
                (k.gj_id, zeit.von.isoformat(), zeit.bis.isoformat())).fetchone()[0],
            "belege": con.execute("SELECT COUNT(*) FROM beleg WHERE mandant_id=?",
                                  (k.mandant_id,)).fetchone()[0],
        },
    }


def uebersicht(k: Kontext) -> dict:
    con = k.con()
    zeit = k.zeitraum
    bil = bilanz(k)
    gv = guv(k)
    soll, haben = con.execute(
        "SELECT COALESCE(SUM(z.soll_cent),0), COALESCE(SUM(z.haben_cent),0)"
        " FROM buchungszeile z JOIN buchung b ON b.id=z.buchung_id"
        " WHERE b.gj_id=? AND b.datum BETWEEN ? AND ?",
        (k.gj_id, zeit.von.isoformat(), zeit.bis.isoformat())).fetchone()

    kz = k.json_datei("kennzahlen.json") or {}
    treiber = kz.get("treiber", [])
    # Der Restposten wird gerechnet, nicht gepflegt. Wer ihn in die Datei
    # schreibt, hat ihn nach der ersten Umbuchung falsch stehen - genau das
    # meldete die Pruefung "Ergebnistreiber erklaeren das Jahresergebnis
    # vollstaendig" am 02.09.2026, nachdem sich das Ergebnis um 63,45 EUR
    # verschoben hatte. Ein Treiber mit "rest": true traegt deshalb keinen
    # eigenen Betrag, sondern die Differenz zum Jahresergebnis.
    for t in treiber:
        if t.get("rest"):
            t["betrag"] = gv["ergebnis"] - sum(
                a.get("betrag", 0) for a in treiber if not a.get("rest"))
    treiber_summe = sum(t.get("betrag", 0) for t in treiber)
    # Der Verweis auf die Buchung steht als Belegfeld in der Datei, nicht als
    # Buchungsnummer: Nummern vergibt `bh buchen` neu, sobald eine Buchung
    # geaendert und wieder angelegt wird - der Link zeigte dann ins Leere oder,
    # schlimmer, auf eine fremde Buchung.
    for t in treiber:
        if t.get("beleg"):
            r = con.execute(
                "SELECT nummer FROM buchung WHERE gj_id=? AND belegfeld=?"
                " ORDER BY nummer LIMIT 1", (k.gj_id, t["beleg"])).fetchone()
            if r:
                t["buchung"] = r[0]

    voll = zeit.code == "jahr"
    st = k.json_datei("steuern.json") if voll else None
    st_kennzahlen, st_abweichungen, st_platzhalter = {}, None, None
    if st:
        st = steuern.pruefen(con, k.gj_id, st, gv["ergebnis"])
        st_kennzahlen = steuern.kennzahlen(st)
        st_abweichungen = st["abweichungen"]
        st_platzhalter = st["platzhalter"]

    o = verknuepfung.offen(con, k.mandant_id, k.gj_id, k.jahr)
    r = reports.rahmen(con, k.gj_id)
    # Die Posten heissen je nach Rahmen anders: die Kapitalgesellschaft fuehrt
    # Umlaufvermoegen und Eigenkapital, die private Rechnung Geld- und
    # Privatvermoegen. Gemeint ist dasselbe.
    liquiditaet = sum(z["wert"] for z in bil["aktiva"]
                      if z["posten"] and z["posten"][0] in ("UV_BANK", "VM_BANK"))
    # Guthaben bei Zahlungsdienstleistern stehen bilanziell unter den sonstigen
    # Vermoegensgegenstaenden - ein E-Geld-Institut ist kein Kreditinstitut.
    # Fuer die Frage, wieviel Geld abrufbar ist, zaehlen sie trotzdem mit; die
    # Kachel weist beides getrennt aus, statt die Gliederung zu verbiegen.
    zahlungsdienstleister = bil["posten"].get("UV_SONST_ZD", 0)
    eigenkapital = sum(z["wert"] for z in bil["passiva"]
                       if z["posten"] and z["posten"][0].startswith(("EK_", "VM_EIGEN")))

    pruefungen = [
        {"text": "Summe Soll gleich Summe Haben", "ok": soll == haben,
         "wert": soll - haben, "detail": f"{db.eur(soll)} / {db.eur(haben)}"},
        {"text": ("Vermögensübersicht ausgeglichen" if r.ueberschussrechnung
                  else "Bilanz ausgeglichen"), "ok": bil["differenz"] == 0,
         "wert": bil["differenz"], "detail": db.eur(bil["summe_aktiva"])},
        {"text": "Alle Bankumsätze verarbeitet", "ok": o["umsaetze_ohne_buchung"] == 0,
         "wert": o["umsaetze_ohne_buchung"],
         "detail": f"{o['umsaetze_ohne_buchung']} ohne Buchung und ohne Vermerk"},
    ]
    if r.ueberschussrechnung:
        # § 11 EStG ist die Regel, die diese Buchfuehrung von der einer
        # Kapitalgesellschaft unterscheidet - sie gehoert auf die Startseite.
        p11 = pruefung.paragraf_11(con, k.gj_id, r)
        pruefungen.append(
            {"text": "Erfolgswirksam nur bei Zahlung (§ 11 EStG)", "ok": not p11,
             "wert": len(p11),
             "detail": ("keine Buchung ohne Zahlung" if not p11
                        else f"{len(p11)} Buchung(en) ohne Geldkonto")})
    # Nur pruefen, was es fuer dieses Jahr auch gibt
    if st_abweichungen is not None:
        pruefungen.append(
            {"text": "Steuerliche Überleitung stimmt mit den Konten überein",
             "ok": st_abweichungen == 0, "wert": st_abweichungen,
             "detail": ("keine Abweichung" if st_abweichungen == 0
                        else f"{st_abweichungen} Zeile(n) weichen ab")})
    # Die Abgabesperre gehoert auf die Startseite, nicht nur auf die Steuerseite:
    # ein Platzhalter ist kein Rechenfehler, sondern eine fehlende Unterlage, und
    # den sieht man sonst erst, wenn die Erklaerung schon getippt ist.
    if st_platzhalter is not None:
        pruefungen.append(
            {"text": "Steuerrechnung ohne Platzhalter (abgabefähig)",
             "ok": not st_platzhalter, "wert": len(st_platzhalter),
             "detail": ("kein angenommener Wert" if not st_platzhalter
                        else f"{len(st_platzhalter)} angenommene(r) Wert(e) — Abgabe gesperrt")})
    if treiber and voll:
        pruefungen.append(
            {"text": "Ergebnistreiber erklären das Jahresergebnis vollständig",
             "ok": treiber_summe == gv["ergebnis"], "wert": treiber_summe - gv["ergebnis"],
             "detail": db.eur(treiber_summe)})

    # Kurzstand der Umsatzsteuer. Gehoert auf die Startseite, weil eine versaeumte
    # Voranmeldung sonst nirgends auffaellt, solange man nur den Abschluss ansieht.
    ust, alle_zeitraeume = None, []
    if any(kt["ustva_kz"] for kt in con.execute(
            "SELECT ustva_kz FROM konto WHERE mandant_id=?", (k.mandant_id,))):
        u = ust_uebersicht(k)
        offene = [z for z in u["zeitraeume"] if z["status"] == "offen" and not z.get("nur_historie")]
        unbezahlt = [z for z in u["zeitraeume"] if z.get("zahlung_offen")]
        faellig = [z["frist"] for z in offene] + [z["frist"] for z in unbezahlt]
        ust = {
            "offen": len(offene),
            "ueberfaellig": sum(1 for z in offene if z["ueberfaellig"]),
            "naechste_frist": min(faellig, default=None),
            "zu_zahlen": sum(z["kz83"] for z in offene if (z["kz83"] or 0) > 0),
            "zu_erstatten": -sum(z["kz83"] for z in offene if (z["kz83"] or 0) < 0),
            "unbezahlt": len(unbezahlt),
            "unbezahlt_cent": sum(z["kz83"] for z in unbezahlt),
            "zahlung_ueberfaellig": sum(1 for z in unbezahlt if z["zahlung_ueberfaellig"]),
            "zeitraeume": [{a: z[a] for a in ("jahr", "code", "bezeichnung", "kz83", "frist",
                                              "status", "ueberfaellig", "zahlung_offen",
                                              "zahlung_ueberfaellig")}
                           for z in u["zeitraeume"] if not z.get("nur_historie")][-4:],
        }
        pruefungen.append(
            {"text": "Keine Voranmeldung überfällig", "ok": ust["ueberfaellig"] == 0,
             "wert": ust["ueberfaellig"],
             "detail": ("alle Fristen gewahrt" if ust["ueberfaellig"] == 0
                        else f"{ust['ueberfaellig']} Zeitraum/Zeiträume über der Frist")})
        # Die offene Zahllast steht nicht hier. Sie ist angemeldet und faellig am
        # 10. - kein Mangel der Buchfuehrung, sondern ein Termin. Sie gehoert in
        # die Fristenliste, sonst faerbt ein planmaessiger Vorgang die Pruefliste
        # rot, bis niemand mehr hinsieht.
        alle_zeitraeume = u["zeitraeume"]

    # Termine: was zu tun ist, getrennt von dem, was nicht stimmt. Massgebend ist
    # der heutige Tag, nicht der Buchungsstand - eine Frist laeuft weiter, auch
    # wenn seit Wochen nichts gebucht wurde.
    termine = fristen.alle(
        k.mandant_dir,
        [(g["jahr"], g["ende"]) for g in con.execute(
            "SELECT jahr, ende FROM geschaeftsjahr WHERE mandant_id=? ORDER BY jahr",
            (k.mandant_id,))],
        alle_zeitraeume,
        heute=datetime.date.today(), kapitalgesellschaft=not r.ueberschussrechnung,
        # Kein Umsatzsteuerkonto im Kontenrahmen heisst: kein Unternehmer, keine
        # Erklaerung. Die vermoegensverwaltende UG haelt nur Beteiligungen.
        umsatzsteuerpflichtig=ust is not None)

    return {
        "fristen": termine,
        "mandant": k.mandant, "jahr": k.jahr,
        "art": "privat" if r.ueberschussrechnung else "gesellschaft",
        "titel": {"bilanz": r.titel_bilanz, "guv": r.titel_guv,
                  "ergebnis": r.titel_ergebnis, "steuern": r.titel_steuern},
        "zeitraum": zeit.als_dict(),
        "gj": {"beginn": k.gj_beginn, "ende": k.gj_ende},
        "umsatzsteuer": ust,
        "kurzfassung": kz.get("kurzfassung"),
        "anlagen": (reports.anlagen_daten(con, k.gj_id, *k.vj_vergleich()[:1],
                                          von=zeit.von.isoformat(), bis=zeit.bis.isoformat())
                    if r.anlagen else None),
        "kennzahlen": {
            "bilanzsumme": bil["summe_aktiva"],
            "bilanzsumme_vj": bil["summe_aktiva_vj"],
            "ergebnis": gv["ergebnis"],
            "eigenkapital": eigenkapital,
            "liquiditaet": liquiditaet,
            "zahlungsdienstleister": zahlungsdienstleister,
            "verfuegbar": liquiditaet + zahlungsdienstleister,
            **st_kennzahlen,
        },
        "treiber": treiber if voll else [], "treiber_summe": treiber_summe,
        "hinweise": kz.get("hinweise", []),
        "pruefungen": pruefungen,
        "bestand": {
            "buchungen": con.execute(
                "SELECT COUNT(*) FROM buchung WHERE gj_id=? AND datum BETWEEN ? AND ?",
                (k.gj_id, zeit.von.isoformat(), zeit.bis.isoformat())).fetchone()[0],
            "belege": con.execute("SELECT COUNT(*) FROM beleg WHERE mandant_id=?",
                                  (k.mandant_id,)).fetchone()[0],
            "bankumsaetze": con.execute(
                "SELECT COUNT(*) FROM banktransaktion t JOIN bankkonto b"
                " ON b.id=t.bankkonto_id WHERE b.mandant_id=?",
                (k.mandant_id,)).fetchone()[0],
            **o,
        },
    }


def konten(k: Kontext) -> dict:
    from .ledger import salden
    zeit = k.zeitraum
    zeilen = [dict(r) for r in salden(k.con(), k.gj_id,
                                      zeit.von.isoformat(), zeit.bis.isoformat())]
    return {"konten": zeilen, "zeitraum": zeit.als_dict(),
            "summe_soll": sum(z["soll"] for z in zeilen),
            "summe_haben": sum(z["haben"] for z in zeilen)}


def kontoblatt(k: Kontext, nummer: str) -> dict:
    con = k.con()
    kopf = con.execute("SELECT nummer, bezeichnung, typ, posten, steuer_tag FROM konto"
                       " WHERE mandant_id=? AND nummer=?",
                       (k.mandant_id, nummer)).fetchone()
    if not kopf:
        return {"fehler": f"Konto {nummer} unbekannt"}
    zeit = k.zeitraum
    rows = con.execute("""
        SELECT b.id, b.nummer, b.datum, b.buchungstext, b.belegfeld, b.art,
               z.soll_cent, z.haben_cent, z.text
        FROM buchungszeile z JOIN buchung b ON b.id=z.buchung_id
        JOIN konto kt ON kt.id=z.konto_id
        WHERE b.gj_id=? AND kt.nummer=? AND b.datum BETWEEN ? AND ?
        ORDER BY b.datum, b.nummer, z.id""",
        (k.gj_id, nummer, zeit.von.isoformat(), zeit.bis.isoformat())).fetchall()
    # Anfangsbestand: alles, was vor dem Zeitraum auf dem Konto gebucht wurde.
    # Ohne ihn ergibt der laufende Saldo eines Monatsauszugs keinen Sinn.
    vortrag = con.execute("""
        SELECT COALESCE(SUM(z.soll_cent - z.haben_cent), 0)
        FROM buchungszeile z JOIN buchung b ON b.id=z.buchung_id
        JOIN konto kt ON kt.id=z.konto_id
        WHERE b.gj_id=? AND kt.nummer=? AND b.datum < ?""",
        (k.gj_id, nummer, zeit.von.isoformat())).fetchone()[0]
    saldo, zeilen = vortrag, []
    for r in rows:
        saldo += r["soll_cent"] - r["haben_cent"]
        # Gegenkonten des Satzes - macht das Kontoblatt ohne Klick lesbar
        gegen = [g[0] for g in con.execute(
            "SELECT kt.nummer FROM buchungszeile z JOIN konto kt ON kt.id=z.konto_id"
            " WHERE z.buchung_id=? AND kt.nummer<>?", (r["id"], nummer))]
        zeilen.append({**dict(r), "saldo": saldo, "gegenkonten": sorted(set(gegen))})
    return {"konto": dict(kopf), "zeilen": zeilen, "saldo": saldo,
            "vortrag": vortrag, "zeitraum": zeit.als_dict(),
            "summe_soll": sum(r["soll_cent"] for r in rows),
            "summe_haben": sum(r["haben_cent"] for r in rows)}


def buchungen_zu_konten(k: Kontext, nummern: list[str]) -> dict:
    """Die einzelnen Buchungen hinter einer Zahl.

    Der Grundsatz der Oberflaeche: **jede** Zahl laesst sich aufklappen, bis
    der Buchungssatz und der Beleg dastehen. Damit das ueberall dieselbe
    Antwort gibt, holen Bilanz, GuV, Anlage V, Steuerseite und Saldenliste die
    Herkunft aus diesem einen Endpunkt - sonst zeigt dieselbe Zahl auf drei
    Seiten drei verschiedene Herkunftsrechnungen.

    Gezeigt wird die Buchungszeile, nicht der Buchungssatz: eine Sammelbuchung
    beruehrt ein Konto oft nur mit einem Teilbetrag, und aufgeklappt wird
    dieser Teilbetrag - er ist es, der in die Summe eingeht. Der ganze Satz
    steht einen Klick weiter in der Schublade.
    """
    con = k.con()
    nummern = [n for n in nummern if n and n != "GuV"]
    if not nummern:
        return {"konten": [], "zeilen": [], "summe": 0, "anzahl": 0}
    zeit = k.zeitraum
    platz = ",".join("?" * len(nummern))
    rows = con.execute(f"""
        SELECT b.id, b.nummer, b.datum, b.buchungstext, b.belegfeld, b.art,
               kt.nummer AS konto, kt.bezeichnung AS kontobez,
               z.soll_cent, z.haben_cent, z.text AS zeilentext
        FROM buchungszeile z JOIN buchung b ON b.id=z.buchung_id
        JOIN konto kt ON kt.id=z.konto_id
        WHERE b.gj_id=? AND kt.nummer IN ({platz}) AND b.datum BETWEEN ? AND ?
        ORDER BY b.datum, b.nummer, z.id""",
        (k.gj_id, *nummern, zeit.von.isoformat(), zeit.bis.isoformat())).fetchall()
    zeilen = []
    for r in rows:
        gegen = [g[0] for g in con.execute(
            "SELECT kt.nummer FROM buchungszeile z JOIN konto kt ON kt.id=z.konto_id"
            " WHERE z.buchung_id=? AND kt.nummer<>?", (r["id"], r["konto"]))]
        belege = [dict(x) for x in con.execute(
            "SELECT e.id, e.belegnr FROM beleg e JOIN buchung_beleg v ON v.beleg_id=e.id"
            " WHERE v.buchung_id=? ORDER BY e.belegnr", (r["id"],))]
        zeilen.append({**dict(r), "betrag": r["soll_cent"] - r["haben_cent"],
                       "gegenkonten": sorted(set(gegen)), "belege": belege})
    bez = {n: (con.execute("SELECT bezeichnung FROM konto WHERE mandant_id=? AND nummer=?",
                           (k.mandant_id, n)).fetchone() or [""])[0] for n in nummern}
    return {"konten": [{"nummer": n, "bezeichnung": bez[n]} for n in nummern],
            "zeilen": zeilen, "anzahl": len(zeilen),
            "summe": sum(z["betrag"] for z in zeilen),
            "summe_soll": sum(z["soll_cent"] for z in zeilen),
            "summe_haben": sum(z["haben_cent"] for z in zeilen),
            "zeitraum": zeit.als_dict()}


def buchung(k: Kontext, nummer: int) -> dict:
    con = k.con()
    kopf = con.execute("SELECT * FROM buchung WHERE gj_id=? AND nummer=?",
                       (k.gj_id, nummer)).fetchone()
    if not kopf:
        return {"fehler": f"Buchung {nummer} nicht gefunden"}
    zeilen = [dict(r) for r in con.execute(
        "SELECT kt.nummer AS konto, kt.bezeichnung AS kontobez, kt.typ,"
        " z.soll_cent, z.haben_cent, z.text"
        " FROM buchungszeile z JOIN konto kt ON kt.id=z.konto_id"
        " WHERE z.buchung_id=? ORDER BY z.id", (kopf["id"],))]
    umsatz = con.execute(
        "SELECT t.*, bk.iban, bk.bezeichnung AS bankbez FROM banktransaktion t"
        " JOIN bankkonto bk ON bk.id=t.bankkonto_id WHERE t.buchung_id=?",
        (kopf["id"],)).fetchone()
    belege = [dict(r) for r in con.execute(
        "SELECT e.id, e.belegnr, e.datum, e.kategorie, e.aussteller, e.bezeichnung, e.pfad"
        " FROM beleg e JOIN buchung_beleg v ON v.beleg_id=e.id"
        " WHERE v.buchung_id=? ORDER BY e.belegnr", (kopf["id"],))]
    return {"buchung": dict(kopf), "zeilen": zeilen,
            "bankumsatz": dict(umsatz) if umsatz else None, "belege": belege,
            "summe": sum(z["soll_cent"] for z in zeilen)}


def journal(k: Kontext) -> dict:
    con = k.con()
    rows = con.execute("""
        SELECT b.id, b.nummer, b.datum, b.buchungstext, b.belegfeld, b.art,
               COUNT(z.id) AS zeilen, COALESCE(SUM(z.soll_cent),0) AS betrag,
               (SELECT COUNT(*) FROM buchung_beleg v WHERE v.buchung_id=b.id) AS belege,
               GROUP_CONCAT(DISTINCT kt.nummer) AS konten
        FROM buchung b JOIN buchungszeile z ON z.buchung_id=b.id
        JOIN konto kt ON kt.id=z.konto_id
        WHERE b.gj_id=? AND b.datum BETWEEN ? AND ?
        GROUP BY b.id ORDER BY b.datum, b.nummer""",
        (k.gj_id, k.zeitraum.von.isoformat(), k.zeitraum.bis.isoformat())).fetchall()
    return {"buchungen": [dict(r) for r in rows], "zeitraum": k.zeitraum.als_dict()}


def bank(k: Kontext) -> dict:
    con = k.con()
    rows = con.execute("""
        SELECT t.id, t.auszug, t.buchungsdatum, t.valuta, t.betrag_cent, t.text,
               t.nicht_gebucht_grund, bk.iban, bk.bezeichnung AS bankbez,
               b.nummer AS buchung_nummer, b.buchungstext
        FROM banktransaktion t JOIN bankkonto bk ON bk.id=t.bankkonto_id
        LEFT JOIN buchung b ON b.id=t.buchung_id
        WHERE bk.mandant_id=? AND t.buchungsdatum BETWEEN ? AND ?
        ORDER BY bk.iban, t.buchungsdatum, t.id""",
        (k.mandant_id, k.zeitraum.von.isoformat(), k.zeitraum.bis.isoformat())).fetchall()
    kontenstand = [dict(r) for r in con.execute(
        "SELECT bk.iban, bk.bezeichnung, kt.nummer AS konto FROM bankkonto bk"
        " LEFT JOIN konto kt ON kt.id=bk.konto_id WHERE bk.mandant_id=?", (k.mandant_id,))]
    return {"umsaetze": [dict(r) for r in rows], "bankkonten": kontenstand,
            "zeitraum": k.zeitraum.als_dict()}


def belege(k: Kontext) -> dict:
    con = k.con()
    rows = con.execute("""
        SELECT e.id, e.jahr, e.belegnr, e.datum, e.kategorie, e.aussteller, e.bezeichnung,
               e.betrag_cent, e.pfad, e.notiz,
               (SELECT COUNT(*) FROM buchung_beleg v WHERE v.beleg_id=e.id) AS buchungen
        FROM beleg e WHERE e.mandant_id=? ORDER BY e.jahr DESC, e.laufnr""",
        (k.mandant_id,)).fetchall()
    return {"belege": [dict(r) for r in rows]}


def beleg(k: Kontext, beleg_id: int) -> dict:
    con = k.con()
    r = con.execute("SELECT * FROM beleg WHERE mandant_id=? AND id=?",
                    (k.mandant_id, beleg_id)).fetchone()
    if not r:
        return {"fehler": "Beleg nicht gefunden"}
    buchungen = [dict(x) for x in con.execute("""
        SELECT b.nummer, b.datum, b.buchungstext, b.belegfeld,
               (SELECT COALESCE(SUM(soll_cent),0) FROM buchungszeile WHERE buchung_id=b.id) AS betrag
        FROM buchung b JOIN buchung_beleg v ON v.buchung_id=b.id
        WHERE v.beleg_id=? AND b.gj_id=? ORDER BY b.nummer""", (beleg_id, k.gj_id))]
    return {"beleg": dict(r), "buchungen": buchungen}


def dokumente(k: Kontext) -> dict:
    liste = []
    for kuerzel, titel, muster in DOKUMENTE:
        name = muster.format(jahr=k.jahr)
        if os.path.exists(os.path.join(k.jahr_dir, name)):
            liste.append({"id": kuerzel, "titel": titel, "datei": name})
    return {"dokumente": liste}


def dokument(k: Kontext, kuerzel: str) -> dict:
    for kz, titel, muster in DOKUMENTE:
        if kz != kuerzel:
            continue
        name = muster.format(jahr=k.jahr)
        pfad = os.path.join(k.jahr_dir, name)
        if os.path.exists(pfad):
            with open(pfad, encoding="utf-8") as fh:
                return {"titel": titel, "datei": name, "text": fh.read(),
                        "format": "text" if name.endswith(".txt") else "markdown"}
    return {"fehler": "Dokument nicht gefunden"}


def ust_uebersicht(k: Kontext) -> dict:
    """Alle Voranmeldungszeiträume mit Stand, Frist und Zahllast.

    Bewusst über alle Jahre hinweg, nicht nur über das gewählte Geschäftsjahr:
    Ob ein Zeitraum erledigt ist, ist keine Frage des Abschlusses, und genau
    diese Übersicht hat 2026 gefehlt.
    """
    con = k.con()
    e = umsatzsteuer.einstellungen(k.mandant_dir)
    heute = __import__("datetime").date.today()
    zeilen = []
    for z in umsatzsteuer.quartale(con, k.mandant_id):
        kz = umsatzsteuer.kennzahlen(con, k.mandant_id, z, e["satz"], e["ermaessigt"],
                                     e["reverse_charge_abziehbar"])
        st = umsatzsteuer.stand(con, k.mandant_id, z)
        zm = umsatzsteuer.zusammenfassende_meldung(con, k.mandant_id, z)
        frist = z.frist(z.hat_dauerfrist(e["dauerfristverlaengerung_ab"]))
        # Abgeben und Zahlen sind zwei Vorgaenge mit derselben Frist. Ein Zeitraum,
        # der uebermittelt, aber nicht bezahlt ist, sieht sonst erledigt aus - und
        # genau das ist der Fall, in dem Saeumniszuschlaege entstehen.
        zahllast = st["status"] == "abgegeben" and (kz["steuern"]["83"] or 0) > 0
        zahlung_offen = zahllast and not st.get("zahlung_am")
        zeilen.append({
            "jahr": z.jahr, "quartal": z.quartal, "code": z.code, "label": z.label,
            "bezeichnung": z.bezeichnung, "von": z.von.isoformat(), "bis": z.bis.isoformat(),
            "kz83": kz["steuern"]["83"], "basen": kz["basen"],
            "frist": frist.isoformat(),
            "ueberfaellig": st["status"] != "abgegeben" and frist < heute,
            "zahlung_am": st.get("zahlung_am"),
            "zahlung_offen": zahlung_offen,
            "zahlung_ueberfaellig": zahlung_offen and frist < heute,
            "status": st["status"], "transferticket": st.get("transferticket"),
            "abgegeben_am": st.get("abgegeben_am"), "notiz": st.get("notiz"),
            "zm_summe": zm["summe"], "zm_frist": zm["frist"],
            "zm_stand": umsatzsteuer.stand(con, k.mandant_id, z, art="zm"),
        })
    # Abgelehnte oder abgegebene Zeitraeume, zu denen es keine Buchungen (mehr)
    # gibt - etwa die fuenf Monatsanmeldungen, die das Finanzamt verworfen hat.
    bekannt = {(r["jahr"], r["code"]) for r in zeilen}
    for r in con.execute("SELECT * FROM voranmeldung WHERE mandant_id=? AND art='ustva'"
                         " ORDER BY jahr, zeitraum", (k.mandant_id,)):
        if (r["jahr"], r["zeitraum"]) in bekannt:
            continue
        z = umsatzsteuer.zeitraum_aus_code(r["jahr"], r["zeitraum"])
        zeilen.append({
            "jahr": z.jahr, "quartal": z.quartal, "code": z.code, "label": z.label,
            "bezeichnung": z.bezeichnung, "von": z.von.isoformat(), "bis": z.bis.isoformat(),
            "kz83": None, "basen": {},
            "frist": z.frist(z.hat_dauerfrist(e["dauerfristverlaengerung_ab"])).isoformat(),
            "ueberfaellig": False, "zahlung_am": r["zahlung_am"],
            "zahlung_offen": False, "zahlung_ueberfaellig": False,
            "status": r["status"], "transferticket": r["transferticket"],
            "abgegeben_am": r["abgegeben_am"], "notiz": r["notiz"],
            "zm_summe": 0, "zm_frist": None, "zm_stand": None, "nur_historie": True,
        })
    zeilen.sort(key=lambda r: (r["von"], r["code"]))
    return {"zeitraeume": zeilen, "einstellungen": e, "mandant": k.mandant}


def ust_zeitraum(k: Kontext, jahr: int, code: str) -> dict:
    con = k.con()
    z = umsatzsteuer.zeitraum_aus_code(jahr, code)
    e = umsatzsteuer.einstellungen(k.mandant_dir)
    kz = umsatzsteuer.kennzahlen(con, k.mandant_id, z, e["satz"], e["ermaessigt"],
                                 e["reverse_charge_abziehbar"])
    kz["bezeichnungen"] = umsatzsteuer.BEZEICHNUNG
    kz["frist"] = z.frist(z.hat_dauerfrist(e["dauerfristverlaengerung_ab"])).isoformat()
    kz["dauerfrist"] = z.hat_dauerfrist(e["dauerfristverlaengerung_ab"])
    kz["einstellungen"] = e
    kz["stand"] = umsatzsteuer.stand(con, k.mandant_id, z)
    kz["zm"] = umsatzsteuer.zusammenfassende_meldung(con, k.mandant_id, z)
    kz["zm"]["stand"] = umsatzsteuer.stand(con, k.mandant_id, z, art="zm")
    kz["mandant"] = k.mandant
    return kz


def ust_herkunft(k: Kontext, jahr: int, code: str, kennzahl: str) -> dict:
    z = umsatzsteuer.zeitraum_aus_code(jahr, code)
    zeilen = umsatzsteuer.herkunft(k.con(), k.mandant_id, z, kennzahl)
    return {"kennzahl": kennzahl, "bezeichnung": umsatzsteuer.BEZEICHNUNG.get(kennzahl, ""),
            "zeilen": zeilen, "summe": sum(r["betrag"] for r in zeilen)}


def offene_punkte(k: Kontext) -> dict:
    return offen.uebersicht(k.con(), k.gj_id, k.jahr_dir,
                            heute=datetime.date.today().isoformat())


def steuerseite(k: Kontext) -> dict:
    spec = k.json_datei("steuern.json")
    if not spec:
        return {"fehler": "steuern.json fehlt"}
    erg = reports.guv_daten(k.con(), k.gj_id)["ergebnis"]
    steuern.vergleich_aufbauen(spec, k.mandant_dir)
    return steuern.pruefen(k.con(), k.gj_id, spec, erg)


# ------------------------------------------------------------------- Server

class Handler(http.server.BaseHTTPRequestHandler):
    basis: dict = None                 # wird in starten() gesetzt
    protocol_version = "HTTP/1.1"

    def _kontext(self, abfrage: str) -> Kontext:
        """Mandant und Jahr stehen in der Anfrage; ohne Angabe gilt, womit der
        Server gestartet wurde."""
        p = urllib.parse.parse_qs(abfrage)
        kuerzel = (p.get("mandant") or [self.basis["kuerzel"]])[0]
        jahr = int((p.get("jahr") or [self.basis["jahr"]])[0])
        k = hole_kontext(self.basis["dbs"], self.basis["wurzel"], kuerzel, jahr)
        return k.mit_zeitraum((p.get("zeitraum") or [None])[0])

    def log_message(self, fmt, *args):
        pass                            # kein Zugriffsprotokoll auf der Konsole

    def _senden(self, koerper: bytes, typ: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(koerper)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(koerper)

    def _json(self, obj, status: int = 200) -> None:
        self._senden(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                     "application/json; charset=utf-8", status)

    def do_GET(self):                   # noqa: N802
        zerlegt = urllib.parse.urlparse(self.path)
        pfad, abfrage = zerlegt.path, zerlegt.query
        teile = [t for t in pfad.split("/") if t]

        try:
            if pfad == "/" or pfad == "/index.html":
                return self._datei(os.path.join(STATIC, "index.html"))
            if teile and teile[0] == "static":
                return self._statisch(teile[1:])
            if teile == ["api", "mandanten"]:
                return self._json(mandanten(self.basis["dbs"]))
            # /beleg/<id>/<dateiname> - der Name ist nur Kosmetik fuer den
            # PDF-Betrachter, gefunden wird der Beleg ueber die Nummer
            if teile[:1] == ["beleg"] and len(teile) == 3:
                return self._beleg_datei(self._kontext(abfrage), int(teile[1]))
            if teile[:1] == ["api"]:
                return self._api(self._kontext(abfrage), teile[1:])
        except NichtGefunden as e:
            return self._json({"fehler": str(e)}, 404)
        except Exception as e:           # eine kaputte Seite ist besser als ein stiller Absturz
            return self._json({"fehler": f"{type(e).__name__}: {e}"}, 500)
        self._senden(b"nicht gefunden", "text/plain; charset=utf-8", 404)

    def _api(self, k: Kontext, teile: list[str]) -> None:
        if teile == ["uebersicht"]:
            return self._json(uebersicht(k))
        if teile == ["bilanz"]:
            return self._json(bilanz(k))
        if teile == ["guv"]:
            return self._json(guv(k))
        if teile == ["zeitraeume"]:
            return self._json(zeitraeume(k))
        if teile == ["anlagen"]:
            return self._json(anlagen(k))
        if teile[:1] == ["anlage"] and len(teile) == 2:
            return self._json(anlage(k, teile[1]))
        if teile == ["pruefung"]:
            return self._json(pruefliste(k))
        if teile == ["steuern"]:
            return self._json(steuerseite(k))
        if teile == ["offen"]:
            return self._json(offene_punkte(k))
        if teile == ["konten"]:
            return self._json(konten(k))
        if teile[:1] == ["konto"] and len(teile) == 2:
            return self._json(kontoblatt(k, urllib.parse.unquote(teile[1])))
        if teile[:1] == ["buchung"] and len(teile) == 2:
            return self._json(buchung(k, int(teile[1])))
        # /api/buchungen/<konto>,<konto>,... - das Aufklappen jeder Zahl
        if teile[:1] == ["buchungen"] and len(teile) == 2:
            return self._json(buchungen_zu_konten(
                k, urllib.parse.unquote(teile[1]).split(",")))
        if teile == ["journal"]:
            return self._json(journal(k))
        if teile == ["bank"]:
            return self._json(bank(k))
        if teile == ["belege"]:
            return self._json(belege(k))
        if teile[:1] == ["beleg"] and len(teile) == 2:
            return self._json(beleg(k, int(teile[1])))
        if teile == ["dokumente"]:
            return self._json(dokumente(k))
        if teile[:1] == ["dokument"] and len(teile) == 2:
            return self._json(dokument(k, teile[1]))
        if teile == ["ust"]:
            return self._json(ust_uebersicht(k))
        if teile[:1] == ["ust"] and len(teile) == 3:
            return self._json(ust_zeitraum(k, int(teile[1]), teile[2]))
        if teile[:1] == ["ust"] and len(teile) == 5 and teile[3] == "kz":
            return self._json(ust_herkunft(k, int(teile[1]), teile[2], teile[4]))
        self._json({"fehler": "unbekannter Endpunkt"}, 404)

    def _statisch(self, teile: list[str]) -> None:
        name = posixpath.normpath("/".join(teile)).lstrip("./")
        pfad = os.path.join(STATIC, name)
        if not os.path.abspath(pfad).startswith(os.path.abspath(STATIC)):
            return self._senden(b"verboten", "text/plain", 403)
        self._datei(pfad)

    def _beleg_datei(self, k: Kontext, beleg_id: int) -> None:
        r = k.con().execute("SELECT pfad FROM beleg WHERE mandant_id=? AND id=?",
                            (k.mandant_id, beleg_id)).fetchone()
        if not r:
            hinweis = (f"Beleg {beleg_id} gibt es bei {k.mandant['name']} nicht. "
                       f"Belegnummern sind nur je Mandant eindeutig - fehlt in der "
                       f"Adresse ?mandant=…, wird beim falschen gesucht.")
            return self._senden(hinweis.encode("utf-8"), "text/plain; charset=utf-8", 404)
        pfad = os.path.join(k.mandant_dir, r["pfad"])
        # Der Pfad kommt aus der eigenen Datenbank, wird aber trotzdem geprueft
        if not os.path.abspath(pfad).startswith(os.path.abspath(k.mandant_dir)):
            return self._senden(b"verboten", "text/plain", 403)
        self._datei(pfad, inline=True)

    def _datei(self, pfad: str, inline: bool = False) -> None:
        if not os.path.exists(pfad):
            return self._senden(b"nicht gefunden", "text/plain; charset=utf-8", 404)
        typ = mimetypes.guess_type(pfad)[0] or "application/octet-stream"
        if typ.startswith("text/") or typ in ("application/javascript", "application/json"):
            typ += "; charset=utf-8"
        with open(pfad, "rb") as fh:
            daten = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(daten)))
        if inline:
            name = os.path.basename(pfad).encode("ascii", "ignore").decode() or "beleg"
            self.send_header("Content-Disposition", f'inline; filename="{name}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(daten)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def starten(db_pfade: list[str] | str, wurzel: str, kuerzel: str, jahr: int,
            port: int = 8765, oeffnen: bool = True) -> None:
    if isinstance(db_pfade, str):
        db_pfade = [db_pfade]
    Handler.basis = {"dbs": db_pfade, "wurzel": wurzel, "kuerzel": kuerzel, "jahr": jahr}
    try:
        start = hole_kontext(db_pfade, wurzel, kuerzel, jahr)  # prueft Mandant und Jahr
    except NichtGefunden as e:
        raise SystemExit(str(e))
    adresse = f"http://127.0.0.1:{port}/"
    with Server(("127.0.0.1", port), Handler) as srv:
        print(f"{start.mandant['name']} · Geschäftsjahr {jahr}")
        print(f"Oberfläche: {adresse}   (Beenden mit Strg-C)")
        if oeffnen:
            threading.Timer(0.5, lambda: webbrowser.open(adresse)).start()
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nbeendet.")
