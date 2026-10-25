# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Lokale Weboberflaeche - `bh serve <mandant> <jahr>`.

Nur Standardbibliothek, kein Framework, keine Internetverbindung. Der Server
laeuft auf 127.0.0.1 und liest - er bucht nichts. Damit ist die Oberflaeche
eine Lesebrille auf den Abschluss, kein zweiter Buchungsweg.

Einzige Ausnahme sind die ELSTER-Uebermittlungen (POST /api/eric/...): pruefen,
testen und abgeben ueber ERiC, und nach einer Abgabe das Festhalten von
Transferticket und Werten. Diese Anfragen brauchen das Sitzungstoken, das nur
in der hier ausgelieferten Seite steht, und kommen nur von 127.0.0.1/localhost
- sonst koennte jede Webseite im selben Browser eine Abgabe ausloesen.

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
import secrets
import socketserver
import sqlite3
import threading
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET

from . import abgaben, aufgaben, db, druck, ebilanz, elster, eric_ansicht, fristen, offen, pruefung
from . import eric_postfach, eric_vorgang, eric_xml
from . import reports, steuern
from . import umsatzsteuer, verknuepfung, vorjahr
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
    # Entwuerfe zum Unterschreiben - sie liegen getrennt, weil sie erst mit der
    # Unterschrift gelten und bis dahin geaendert werden duerfen.
    ("feststellungsbeschluss", "Feststellungsbeschluss (Entwurf)",
     "entwuerfe/feststellungsbeschluss_{jahr}.md"),
    ("gf_anstellungsvertrag", "Geschäftsführer-Anstellungsvertrag (Entwurf)",
     "entwuerfe/gf_anstellungsvertrag.md"),
    ("gf_beschluss", "Gesellschafterbeschluss GF-Vergütung (Entwurf)",
     "entwuerfe/gesellschafterbeschluss_gf_verguetung.md"),
    # Einkommensteuer: dieselbe Mechanik, andere Dateinamen.
    ("einkommensteuer", "Einkommensteuererklärung", "einkommensteuer_{jahr}.md"),
    ("ug_zinsen", "Gesellschafterdarlehen je Objekt", "ug_zinsen_{jahr}.txt"),
    ("bankzinsen", "Schuldzinsen der Bankdarlehen", "bankzinsen_{jahr}.txt"),
    ("bauzahlungen", "Bauzahlungen nach Objekt", "bauzahlungen_{jahr}.txt"),
    ("kostenaufstellung", "Kostenaufstellung", "kostenaufstellung_{jahr}.md"),
    ("kreditkarte", "Kreditkarte — steuerliche Auswertung", "kreditkarte_{jahr}.md"),
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

    def stamm(self) -> dict:
        """Datenbankzeile des Mandanten, ergaenzt um alles aus mandant.json.

        Die Ausfuellhilfe braucht Angaben, die nur in der Datei stehen
        (Anteilseigner, Gegenstand des Unternehmens) - die Datei ist dafuer die
        Quelle, nicht eine Kopie in der Datenbank.
        """
        d = dict(self.mandant)
        pfad = os.path.join(self.mandant_dir, "mandant.json")
        if os.path.exists(pfad):
            with open(pfad, encoding="utf-8") as fh:
                d.update(json.load(fh))
        return d

    def lohnjahre(self) -> set[int]:
        """Jahre, in denen Loehne gebucht sind - sie loesen § 41b EStG aus."""
        return {r[0] for r in self.con().execute("""
            SELECT g.jahr FROM buchungszeile z JOIN buchung b ON b.id = z.buchung_id
            JOIN konto k ON k.id = z.konto_id JOIN geschaeftsjahr g ON g.id = b.gj_id
            WHERE g.mandant_id = ? AND k.posten = 'PERSONAL_LOEHNE'
            GROUP BY g.jahr HAVING SUM(z.soll_cent - z.haben_cent) <> 0""",
            (self.mandant_id,))}

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
    # vollstaendig", nachdem eine Korrektur das Ergebnis um ein paar Euro
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
    termine = _termine(k, alle_zeitraeume)
    auf = aufgaben_liste(k, termine) if voll else None

    return {
        "fristen": termine,
        "aufgaben": ({"zaehler": auf["zaehler"], "naechste_frist": auf["naechste_frist"],
                      "oben": auf["aufgaben"][:5]} if auf else None),
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


def _umsatzsteuerpflichtig(k: Kontext) -> bool:
    # Kein Umsatzsteuerkonto im Kontenrahmen heisst: kein Unternehmer, keine
    # Erklaerung. Die vermoegensverwaltende UG haelt nur Beteiligungen.
    return any(kt["ustva_kz"] for kt in k.con().execute(
        "SELECT ustva_kz FROM konto WHERE mandant_id=?", (k.mandant_id,)))


def _termine(k: Kontext, alle_zeitraeume: list[dict] | None = None) -> dict:
    """Alle Fristen des Mandanten - eine Rechnung fuer Uebersicht und Aufgaben."""
    con = k.con()
    r = reports.rahmen(con, k.gj_id)
    ust = _umsatzsteuerpflichtig(k)
    if alle_zeitraeume is None:
        alle_zeitraeume = ust_uebersicht(k)["zeitraeume"] if ust else []
    return fristen.alle(
        k.mandant_dir,
        [(g["jahr"], g["ende"]) for g in con.execute(
            "SELECT jahr, ende FROM geschaeftsjahr WHERE mandant_id=? ORDER BY jahr",
            (k.mandant_id,))],
        alle_zeitraeume,
        heute=datetime.date.today(), kapitalgesellschaft=not r.ueberschussrechnung,
        umsatzsteuerpflichtig=ust, lohn_jahre=k.lohnjahre())


def abgabepaket(k: Kontext) -> dict:
    """Was fuer das Geschaeftsjahr abzugeben ist und woran es noch haengt."""
    r = reports.rahmen(k.con(), k.gj_id)
    d = abgaben.paket(k.con(), k.gj_id, k.stamm(), k.mandant_dir, k.jahr_dir, k.jahr,
                      {"beginn": k.gj_beginn, "ende": k.gj_ende}, datetime.date.today(),
                      kapitalgesellschaft=not r.ueberschussrechnung,
                      umsatzsteuerpflichtig=_umsatzsteuerpflichtig(k),
                      lohnzahlung=k.jahr in k.lohnjahre())
    d["mandant"] = k.mandant
    return d


def ausfuellhilfe(k: Kontext) -> dict:
    d = elster.auswerten(k.con(), k.gj_id, k.stamm(), k.jahr_dir,
                         {"beginn": k.gj_beginn, "ende": k.gj_ende})
    if d is None:
        return {"fehler": f"Für {k.jahr} gibt es keine Ausfüllhilfe (elster.json fehlt)."}
    d["mandant"] = k.mandant
    return d


def ebilanz_seite(k: Kontext) -> dict:
    d = ebilanz.auswerten(k.con(), k.gj_id, k.mandant_dir)
    if d is None:
        return {"fehler": "Keine Taxonomie-Zuordnung (ebilanz.csv) für diesen Mandanten."}
    s = k.stamm()
    d["stammdaten"] = {
        "name": s.get("name"), "rechtsform": s.get("rechtsform"), "sitz": s.get("ort"),
        "register": " ".join(x for x in (s.get("registergericht"), s.get("registernummer")) if x),
        "steuernummer": s.get("steuernummer"), "finanzamt": s.get("finanzamt"),
        "wirtschaftsjahr": f"{k.gj_beginn} – {k.gj_ende}",
        "groessenklasse": (s.get("fristen") or {}).get("groessenklasse"),
        "bilanzart": _bilanzart(k),
    }
    d["mandant"] = k.mandant
    return d


def _bilanzart(k: Kontext) -> str:
    # Weicht die Steuerbilanz ab, beginnt die Ueberleitung in steuern.json mit
    # einer eigenen Zeile "steuerbilanzergebnis" - dann braucht die E-Bilanz
    # eine Ueberleitungsrechnung.
    spec = steuern.laden(k.jahr_dir) or {}
    abweichend = any(z.get("kennzahl") == "steuerbilanzergebnis"
                     for a in spec.get("abschnitte", []) for z in a.get("zeilen", []))
    return ("Handelsbilanz mit Überleitungsrechnung" if abweichend
            else "Steuerbilanz = Handelsbilanz")


def aufgaben_liste(k: Kontext, termine: dict | None = None) -> dict:
    r = reports.rahmen(k.con(), k.gj_id)
    paket = abgabepaket(k) if not r.ueberschussrechnung else None
    st = k.json_datei("steuern.json")
    if st:
        st = steuern.pruefen(k.con(), k.gj_id, st, reports.ergebnis(k.con(), k.gj_id))
    d = aufgaben.sammeln(paket, offene_punkte(k), termine or _termine(k), st,
                         datetime.date.today().isoformat(), k.jahr)
    d["mandant"] = k.mandant
    d["jahr"] = k.jahr
    return d


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
                markdown = not name.endswith(".txt")
                return {"titel": titel, "datei": name, "text": fh.read(),
                        "format": "markdown" if markdown else "text",
                        "pdf": markdown and druck.chrome() is not None}
    return {"fehler": "Dokument nicht gefunden"}


def dokument_pdf(k: Kontext, kuerzel: str) -> str:
    """Pfad des PDFs zum Dokument - erzeugt aus demselben Markdown wie die Ansicht.

    Abgelegt unter out/ (nicht versioniert), benannt nach dem Inhalt: ein
    geaendertes Dokument bekommt ein neues PDF, ein veraltetes gibt es nicht.
    """
    d = dokument(k, kuerzel)
    if "fehler" in d or d["format"] != "markdown":
        raise NichtGefunden(f"Zu '{kuerzel}' gibt es kein druckbares Dokument.")
    return druck.pdf(d["text"], f"{k.mandant['name']} – {d['titel']} {k.jahr}",
                     os.path.join(k.wurzel, "out", "pdf", k.kuerzel, str(k.jahr)))


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
        bank = (umsatzsteuer.zahlung_aus_bank(con, k.mandant_id, z,
                                              zahllast_buch=kz["steuern"]["83"])
                if zahllast and not st.get("zahlung_am") else None)
        zahlung_am = st.get("zahlung_am") or (bank and bank["buchungsdatum"])
        zahlung_offen = zahllast and not zahlung_am
        zeilen.append({
            "jahr": z.jahr, "quartal": z.quartal, "code": z.code, "label": z.label,
            "bezeichnung": z.bezeichnung, "von": z.von.isoformat(), "bis": z.bis.isoformat(),
            "kz83": kz["steuern"]["83"], "basen": kz["basen"],
            "frist": frist.isoformat(),
            "ueberfaellig": st["status"] != "abgegeben" and frist < heute,
            "zahlung_am": zahlung_am, "zahlung_aus_bank": bool(bank),
            "zahlung_offen": zahlung_offen, "lastschrift": e["lastschrift"],
            # nach einer Teilzahlung ist nur noch der Rest offen
            "zahlung_offen_cent": (bank["offen_cent"] if bank else kz["steuern"]["83"])
            if zahlung_offen else 0,
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


def _idnr(kennung: str) -> str:
    """USt-IdNr. ohne Leerraum und in Großbuchstaben - als Schlüssel für den
    Abgleich zwischen Buchung, Datensatz und übermitteltem Wert."""
    return "".join(str(kennung).split()).upper()


def zm_uebersicht(k: Kontext) -> dict:
    """Alle Zusammenfassenden Meldungen: aus den Buchungen, was zu melden ist,
    aus `voranmeldung`, was gemeldet wurde - über alle Jahre wie die Voranmeldungen."""
    con = k.con()
    e = umsatzsteuer.einstellungen(k.mandant_dir)
    heute = datetime.date.today()
    laeufe = [l for l in eric_ansicht.laeufe(k.mandant_dir) if l["art"] == "zm"]
    zeilen, bekannt = [], set()
    for z in umsatzsteuer.quartale(con, k.mandant_id):
        zm = umsatzsteuer.zusammenfassende_meldung(con, k.mandant_id, z)
        st = umsatzsteuer.stand(con, k.mandant_id, z, art="zm")
        if not zm["summe"] and st["status"] == "offen":
            continue
        bekannt.add((z.jahr, z.code))
        kz = umsatzsteuer.kennzahlen(con, k.mandant_id, z, e["satz"], e["ermaessigt"],
                                     e["reverse_charge_abziehbar"])
        try:
            gemeldet_euro = {_idnr(i): b for i, b in eric_xml.zm_zeilen(zm)}
            fehler = None
        except eric_xml.DatenFehlen as f:
            gemeldet_euro, fehler = {}, str(f)
        # Beide Seiten in derselben Schreibweise: eine im Buchungstext mit
        # Leerzeichen erfasste USt-IdNr. fände sonst ihren Wert nie wieder
        uebermittelt = st.get("kennzahlen")
        if isinstance(uebermittelt, dict):
            uebermittelt = {_idnr(i): v for i, v in uebermittelt.items()}
        eigene = [l for l in laeufe if (l["jahr"], l["zeitraum"]) == (z.jahr, z.code)]
        zeilen.append({
            "jahr": z.jahr, "code": z.code, "label": z.label, "bezeichnung": z.bezeichnung,
            "von": z.von.isoformat(), "bis": z.bis.isoformat(), "frist": zm["frist"],
            "laeuft_noch": z.bis >= heute,
            "ueberfaellig": st["status"] != "abgegeben" and zm["frist"] < heute.isoformat(),
            "summe": zm["summe"], "kz21": kz["basen"]["21"],
            "meldebetrag_euro": sum(gemeldet_euro.values()), "fehler": fehler,
            "kunden": [{"ust_idnr": c["ust_idnr"], "betrag": c["betrag"],
                        "euro": gemeldet_euro.get(_idnr(c["ust_idnr"])),
                        "uebermittelt": (uebermittelt or {}).get(_idnr(c["ust_idnr"])),
                        "zeilen": [{x: r[x] for x in ("nummer", "datum", "buchungstext", "betrag",
                                                      "belege", "belegfeld")}
                                   for r in c["zeilen"]]} for c in zm["kunden"]],
            "status": st["status"], "transferticket": st.get("transferticket"),
            "abgegeben_am": st.get("abgegeben_am"), "notiz": st.get("notiz"),
            "werte_erfasst": bool(uebermittelt),
            "abweichend": (None if not uebermittelt else
                           sum(str(gemeldet_euro.get(i)) != str(v) for i, v in uebermittelt.items())
                           + len(set(gemeldet_euro) - set(uebermittelt))),
            "lauf": eigene[0]["id"] if eigene else None,
            "lauf_modus": eigene[0]["modus"] if eigene else None,
        })
    for r in con.execute("SELECT * FROM voranmeldung WHERE mandant_id=? AND art='zm'",
                         (k.mandant_id,)):
        if (r["jahr"], r["zeitraum"]) in bekannt:
            continue
        z = umsatzsteuer.zeitraum_aus_code(r["jahr"], r["zeitraum"])
        zeilen.append({"jahr": z.jahr, "code": z.code, "label": z.label,
                       "bezeichnung": z.bezeichnung, "von": z.von.isoformat(),
                       "bis": z.bis.isoformat(), "frist": None, "laeuft_noch": False,
                       "ueberfaellig": False, "summe": 0, "kz21": 0, "meldebetrag_euro": 0,
                       "fehler": None, "kunden": [], "status": r["status"],
                       "transferticket": r["transferticket"], "abgegeben_am": r["abgegeben_am"],
                       "notiz": r["notiz"], "werte_erfasst": bool(r["kennzahlen"]),
                       "abweichend": None, "lauf": None, "lauf_modus": None,
                       "nur_historie": True})
    zeilen.sort(key=lambda r: r["von"], reverse=True)
    return {"zeitraeume": zeilen, "mandant": k.mandant,
            "hinweis": "Die Dauerfristverlängerung gilt für die Zusammenfassende Meldung nicht "
                       "(§ 18a Abs. 11 UStG): Frist ist der 25. nach Quartalsende."}


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


def eric_liste(k: Kontext) -> dict:
    """Alles, was an ELSTER ging - über ERiC und von Hand -, über alle Jahre."""
    d = eric_ansicht.uebersicht(k.con(), k.mandant_id, k.mandant_dir)
    d["mandant"] = k.mandant
    return d


def eric_lauf(k: Kontext, lauf_id: str) -> dict:
    try:
        d = eric_ansicht.detail(k.con(), k.mandant_id, k.mandant_dir, k.kuerzel, lauf_id)
    except eric_ansicht.NichtGefunden as e:
        raise NichtGefunden(str(e)) from None
    l = d["lauf"]
    eigene = [x for x in eric_ansicht.laeufe(k.mandant_dir)
              if (x["jahr"], x["zeitraum"], x["art"]) == (l["jahr"], l["zeitraum"], l["art"])]
    neueste = next((x for x in eigene if x["modus"] == "pruefen"), None)
    test = next((x for x in eigene if x["modus"] == "test"), None)
    d["neueste_pruefung"] = neueste and neueste["id"]
    passt = False
    if test:
        pfad = os.path.join(k.mandant_dir, test["id"], "datensatz.xml")
        if os.path.exists(pfad):
            with open(pfad, encoding="utf-8", newline="") as fh:
                passt = (eric_vorgang.sha256(eric_ansicht.ohne_testmerker(fh.read()))
                         == l["datensatz"]["sha256"])
    d["letzter_test"] = test and {"id": test["id"], "zeit": test["zeit"],
                                  "angenommen": (test.get("ergebnis") or {}).get("gesendet"),
                                  "testzertifikat": test.get("testzertifikat"),
                                  "gleicher_datensatz": passt}
    d["betrag_text"] = eric_vorgang.betrag_text(l["betrag_cent"])
    d["mandant"] = k.mandant
    return d


# Eine ERiC-Sitzung zur Zeit: ERiC ist je Prozess einmal initialisiert, und
# zwei Abgaben gleichzeitig will niemand.
ERIC_SPERRE = threading.Lock()


def eric_offen(k: Kontext) -> dict:
    """Welche Meldungen abzugeben sind, mit der letzten Prüfung und dem letzten Test."""
    con = k.con()
    heute = datetime.date.today()
    e = umsatzsteuer.einstellungen(k.mandant_dir)
    laeufe = eric_ansicht.laeufe(k.mandant_dir)
    zeilen = []
    for z in umsatzsteuer.quartale(con, k.mandant_id):
        for art in ("ustva", "zm"):
            st = umsatzsteuer.stand(con, k.mandant_id, z, art=art)
            if st["status"] == "abgegeben":
                continue
            if art == "zm":
                zm = umsatzsteuer.zusammenfassende_meldung(con, k.mandant_id, z)
                if not zm["summe"]:
                    continue
                betrag, frist = zm["summe"], zm["frist"]
            else:
                kz = umsatzsteuer.kennzahlen(con, k.mandant_id, z, e["satz"], e["ermaessigt"],
                                             e["reverse_charge_abziehbar"])
                betrag = kz["steuern"]["83"]
                frist = z.frist(z.hat_dauerfrist(e["dauerfristverlaengerung_ab"])).isoformat()
            eigene = [l for l in laeufe
                      if (l["jahr"], l["zeitraum"], l["art"]) == (z.jahr, z.code, art)]
            letzte = lambda modus: next((l for l in eigene if l["modus"] == modus), None)
            p, t = letzte("pruefen"), letzte("test")
            zeilen.append({
                "jahr": z.jahr, "zeitraum": z.code, "label": z.label, "art": art,
                "bezeichnung": z.bezeichnung, "betrag_cent": betrag, "frist": frist,
                "status": st["status"], "laeuft_noch": z.bis >= heute,
                "pruefung": p and {"id": p["id"], "zeit": p["zeit"],
                                   "ok": (p.get("ergebnis") or {}).get("ok")},
                "test": t and {"id": t["id"], "zeit": t["zeit"],
                               "angenommen": (t.get("ergebnis") or {}).get("gesendet"),
                               "testzertifikat": bool(t.get("testzertifikat"))},
            })
    # E-Bilanz, KSt und GewSt: Jahrespflichten der Kapitalgesellschaft, offen bis
    # zum Eintrag in fristen.csv. Erst nach Ende des Wirtschaftsjahres können sie
    # entstehen. Steht dort schon ein Transferticket bei offenem Status, war die
    # Erklärung abgegeben und ist zu berichtigen — dann gilt keine Frist mehr.
    r_ = reports.rahmen(con, k.gj_id)
    erledigt = fristen.erledigungen(k.mandant_dir)
    if not r_.ueberschussrechnung:
        for g in con.execute("SELECT id, jahr, beginn, ende FROM geschaeftsjahr WHERE mandant_id=?"
                             " ORDER BY jahr", (k.mandant_id,)):
            termine = {t["art"]: t for t in fristen.jahrespflichten(
                k.mandant_dir, g["jahr"], g["ende"], heute, kapitalgesellschaft=True,
                umsatzsteuerpflichtig=False)}
            stamm = eric_vorgang.stammdaten(k.mandant_dir)
            for art, pflicht in eric_vorgang.JAHRESARTEN.items():
                code = eric_vorgang.ZEITRAUM[art]
                # Die Eröffnungsbilanz gibt es nur im Gründungsjahr (§ 5b Abs. 1 Satz 4 EStG) -
                # dieselbe Regel wie in der Fristenliste
                if art == "eroeffnung" and not fristen.eroeffnungsbilanz_im_jahr(stamm, g["jahr"]):
                    continue
                st = eric_vorgang.stand(k.mandant_dir, g["jahr"], code, art)
                if st and st["status"] == "abgegeben":
                    continue
                eigene = [l for l in laeufe
                          if (l["jahr"], l["zeitraum"], l["art"]) == (g["jahr"], code, art)]
                p = next((l for l in eigene if l["modus"] == "pruefen"), None)
                t = next((l for l in eigene if l["modus"] == "test"), None)
                if art in ("ebilanz", "eroeffnung"):
                    try:
                        betrag = (eric_vorgang.ebilanz_xbrl.abschluss(con, g["id"], k.mandant_dir)
                                  if art == "ebilanz" else eric_vorgang.ebilanz_xbrl.eroeffnung(
                                      con, g["id"], k.mandant_dir, stamm)).bilanzsumme
                    except eric_vorgang.ebilanz_xbrl.EBilanzFehler:
                        betrag = None
                else:
                    # Das zu versteuernde Einkommen steht erst im geprüften Datensatz fest
                    betrag = p.get("betrag_cent") if p else None
                termin = termine.get(pflicht)
                berichtigung = bool(st and st["transferticket"]
                                    and art not in ("ebilanz", "eroeffnung"))
                klasse = {"WJ": eric_vorgang.Geschaeftsjahr, "EB": eric_vorgang.Eroeffnungsstichtag,
                          "VZ": eric_vorgang.Veranlagungszeitraum}[code]
                zeilen.append({
                    "jahr": g["jahr"], "zeitraum": code, "label": str(g["jahr"]), "art": art,
                    "bezeichnung": klasse(g["jahr"], g["beginn"], g["ende"]).bezeichnung,
                    "betrag_cent": betrag,
                    "frist": None if berichtigung else termin["faellig"] if termin else None,
                    "berichtigung": berichtigung,
                    "status": "offen",
                    # Die Eröffnungsbilanz steht ab dem ersten Tag fest
                    "laeuft_noch": art != "eroeffnung"
                    and datetime.date.fromisoformat(g["ende"]) >= heute,
                    "pruefung": p and {"id": p["id"], "zeit": p["zeit"],
                                       "ok": (p.get("ergebnis") or {}).get("ok")},
                    "test": t and {"id": t["id"], "zeit": t["zeit"],
                                   "angenommen": (t.get("ergebnis") or {}).get("gesendet"),
                                   "testzertifikat": bool(t.get("testzertifikat"))},
                })
    zeilen.sort(key=lambda r: (r["frist"] or "", r["art"]))
    return {"zeilen": zeilen, "vorschlaege": eric_ansicht.vorschlaege(k.mandant_dir, laeufe)}


def eric_verwerfen(k: Kontext, daten: dict) -> tuple[dict, int]:
    """Einen Vorschlag zur Freigabe ablehnen. Der Lauf bleibt in der Ablage."""
    try:
        ordner = eric_ansicht.ordner(k.mandant_dir, str(daten["lauf"]))
        v = eric_vorgang.verwerfen(ordner, str(daten.get("grund") or ""))
    except KeyError:
        return {"fehler": "lauf fehlt"}, 400
    except eric_vorgang.VorgangFehler as f:
        return {"fehler": str(f)}, 400
    return {"ok": True, "vorschlag": v}, 200


def eric_aktion(k: Kontext, aktion: str, daten: dict) -> tuple[dict, int]:
    """Prüfen, testen oder abgeben, ausgelöst aus dem Browser."""
    try:
        art = daten["art"]
        z = eric_vorgang.zeitraum(k.con(), k.mandant_id, int(daten["jahr"]), str(daten["zeitraum"]))
    except (KeyError, ValueError, TypeError, eric_vorgang.VorgangFehler):
        return {"fehler": "art, jahr und zeitraum fehlen"}, 400
    if aktion not in eric_vorgang.MODI or art not in eric_vorgang.ARTEN:
        return {"fehler": "unbekannte Aktion"}, 400

    def freigabe(ds, vp, pruefung):
        # Abgegeben wird nur, was angesehen wurde: die zuletzt geprüfte Fassung,
        # Byte für Byte, mit Häkchen und eingetipptem Betrag.
        if not pruefung or daten.get("pruefung") != pruefung["id"]:
            raise eric_vorgang.VorgangFehler(
                "Freigegeben ist nur die zuletzt geprüfte Fassung — bitte die neueste "
                "Prüfung öffnen.")
        if pruefung["datensatz"]["sha256"] != eric_vorgang.sha256(ds.xml):
            raise eric_vorgang.VorgangFehler(
                "Der Datensatz weicht von der geprüften Fassung ab (Buchführung oder Datum "
                "haben sich geändert). Nichts gesendet — bitte neu prüfen und ansehen.")
        if daten.get("gelesen") is not True:
            raise eric_vorgang.VorgangFehler("Bestätigung, dass alles gelesen ist, fehlt.")
        eingabe = str(daten.get("betrag", "")).replace(".", "").replace("€", "").strip()
        if eingabe != eric_vorgang.betrag_text(ds.betrag_cent):
            raise eric_vorgang.VorgangFehler("Der eingetippte Betrag stimmt nicht. Nichts gesendet.")

    if not ERIC_SPERRE.acquire(blocking=False):
        return {"fehler": "Es läuft schon eine Übermittlung."}, 409
    try:
        e = eric_vorgang.Einstellung.laden()
        v = eric_vorgang.durchfuehren(
            k.con(), k.mandant_id, k.kuerzel, k.mandant_dir, z, art, aktion, e,
            freigabe=freigabe if aktion == "senden" else None)
    except eric_vorgang.NichtFestgehalten as f:
        lauf = getattr(f, "lauf", None)
        r = getattr(lauf, "ergebnis", None)
        return {"fehler": str(f), "lauf": _lauf_id(k, lauf.ordner) if lauf else None,
                "gesendet": True, "transferticket": getattr(r, "transferticket", None),
                "festgehalten": False}, 409
    except eric_vorgang.VorgangFehler as f:
        lauf = getattr(f, "lauf", None)
        return {"fehler": str(f), "lauf": _lauf_id(k, lauf.ordner) if lauf else None}, 409
    finally:
        ERIC_SPERRE.release()
    r = v.lauf.ergebnis
    return {"lauf": _lauf_id(k, v.lauf.ordner), "ok": r.ok, "gesendet": r.gesendet,
            "transferticket": r.transferticket, "text": r.text,
            "festgehalten": v.festgehalten}, 200


def _kurztext(d: dict) -> str | None:
    """Erste Zeile des Inhalts für die Liste: der <Text> einer XML-Rückmeldung."""
    inhalt = d.get("inhalt")
    if not inhalt or not inhalt.lstrip().startswith("<"):
        return None
    try:
        for e in ET.fromstring(inhalt.strip().encode("utf-8")).iter():
            if e.tag.rsplit("}", 1)[-1] == "Text" and (e.text or "").strip():
                return e.text.strip()
    except ET.ParseError:
        pass
    return None


def _inhaltsart(roh: bytes | None) -> str | None:
    if roh is None:
        return None
    if roh.startswith(b"%PDF"):
        return "pdf"
    return "xml" if roh.lstrip()[:1] == b"<" else "text"


def postfach_liste(k: Kontext) -> dict:
    zeilen = []
    for d in eric_postfach.nachrichten(k.mandant_dir):
        voll = eric_postfach.nachricht(k.mandant_dir, d["pfad"])
        zeilen.append({**{x: d.get(x) for x in ("pfad", "id", "datenart", "meta",
                                                "abgeholt_am", "bestaetigung", "anhaenge")},
                       "kurztext": _kurztext(voll)})
    return {"zeilen": zeilen}


def postfach_nachricht(k: Kontext, nachricht_id: str) -> dict:
    try:
        d = eric_postfach.nachricht(k.mandant_dir, nachricht_id)
        roh_pfad = os.path.join(eric_postfach.ordner(k.mandant_dir, nachricht_id), "datenpaket")
    except eric_postfach.PostfachFehler as e:
        raise NichtGefunden(str(e)) from None
    roh = None
    if os.path.exists(roh_pfad):
        with open(roh_pfad, "rb") as fh:
            roh = fh.read()
    d["inhaltsart"] = _inhaltsart(roh)
    if d["inhaltsart"] == "pdf":
        d["inhalt"] = None
    d["kurztext"] = _kurztext(d)
    return d


def postfach_abrufen(k: Kontext) -> tuple[dict, int]:
    if not ERIC_SPERRE.acquire(blocking=False):
        return {"fehler": "Es läuft schon eine Verbindung zu ELSTER."}, 409
    try:
        r = eric_postfach.abrufen(k.kuerzel, k.mandant_dir, eric_vorgang.Einstellung.laden())
    except (eric_postfach.PostfachFehler, eric_vorgang.VorgangFehler) as f:
        return {"fehler": str(f)}, 409
    finally:
        ERIC_SPERRE.release()
    return {"status": r.status, "neu": r.neu, "vorhanden": r.vorhanden,
            "bestaetigt": r.bestaetigt}, 200


def _lauf_id(k: Kontext, ordner: str) -> str:
    return os.path.relpath(ordner, k.mandant_dir).replace(os.sep, "/")


def offene_punkte(k: Kontext) -> dict:
    return offen.uebersicht(k.con(), k.gj_id, k.jahr_dir,
                            heute=datetime.date.today().isoformat())


STEUERERKLAERUNGEN = ("koerperschaftsteuer", "gewerbesteuer", "einkommensteuer",
                      "umsatzsteuer_jahr", "ebilanz")


def steuerseite(k: Kontext) -> dict:
    spec = k.json_datei("steuern.json")
    if not spec:
        # Kein Fehler, sondern der Normalfall eines laufenden Jahres: die
        # Überleitung zum zu versteuernden Einkommen entsteht mit dem Abschluss.
        # Statt der nackten Meldung sagen, was ansteht und wo es weitergeht.
        con = k.con()
        r = reports.rahmen(con, k.gj_id)
        heute = datetime.date.today()
        pflichten = [
            {f: e.get(f) for f in ("art", "bezeichnung", "faellig", "status", "erledigt_am")}
            for e in fristen.jahrespflichten(
                k.mandant_dir, k.jahr, k.gj_ende, heute,
                kapitalgesellschaft=not r.ueberschussrechnung,
                umsatzsteuerpflichtig=_umsatzsteuerpflichtig(k),
                lohnzahlung=k.jahr in k.lohnjahre())
            if e["art"] in STEUERERKLAERUNGEN]
        andere = [int(j) for j in sorted(os.listdir(k.mandant_dir))
                  if j.isdigit() and int(j) != k.jahr
                  and os.path.exists(os.path.join(k.mandant_dir, j, "steuern.json"))]
        return {"fehlt": True, "jahr": k.jahr, "gj_ende": k.gj_ende,
                "laeuft_noch": k.gj_ende >= heute.isoformat(),
                "ergebnis": reports.guv_daten(con, k.gj_id)["ergebnis"],
                "pflichten": pflichten, "andere_jahre": andere}
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
        # DNS-Rebinding: eine fremde Seite, deren Name auf 127.0.0.1 umgebogen
        # wurde, gilt dem Browser als gleiche Herkunft und duerfte jede Antwort
        # lesen - Steuerdaten, Postfach, Protokolle. Sie schickt aber ihren
        # eigenen Namen im Host-Kopf mit. Das Token hilft hier nicht: die Seite
        # selbst wird per GET geladen und bringt es erst mit.
        if not self._eigener_host():
            return self._senden("Nur über 127.0.0.1 oder localhost erreichbar.".encode("utf-8"),
                                "text/plain; charset=utf-8", 403)
        zerlegt = urllib.parse.urlparse(self.path)
        pfad, abfrage = zerlegt.path, zerlegt.query
        teile = [t for t in pfad.split("/") if t]

        try:
            if pfad == "/" or pfad == "/index.html":
                return self._startseite()
            if teile and teile[0] == "static":
                return self._statisch(teile[1:])
            if teile == ["api", "mandanten"]:
                return self._json(mandanten(self.basis["dbs"]))
            # /beleg/<id>/<dateiname> - der Name ist nur Kosmetik fuer den
            # PDF-Betrachter, gefunden wird der Beleg ueber die Nummer
            if teile[:1] == ["beleg"] and len(teile) == 3:
                return self._beleg_datei(self._kontext(abfrage), int(teile[1]))
            # /eric/datei?lauf=<jahr/elster/vorgang/lauf>&datei=protokoll.pdf
            if teile == ["eric", "datei"]:
                return self._eric_datei(self._kontext(abfrage), abfrage)
            # /postfach/datei?id=<datenart/bereitstellung>&datei=datenpaket|<anhang>
            if teile == ["postfach", "datei"]:
                return self._postfach_datei(self._kontext(abfrage))
            # /dokument/<id>.pdf - Druckfassung eines Dokuments
            if teile[:1] == ["dokument"] and len(teile) == 2 and teile[1].endswith(".pdf"):
                return self._dokument_pdf(self._kontext(abfrage), teile[1][:-4])
            if teile[:1] == ["api"]:
                return self._api(self._kontext(abfrage), teile[1:])
        except NichtGefunden as e:
            return self._json({"fehler": str(e)}, 404)
        except Exception as e:           # eine kaputte Seite ist besser als ein stiller Absturz
            return self._json({"fehler": f"{type(e).__name__}: {e}"}, 500)
        self._senden(b"nicht gefunden", "text/plain; charset=utf-8", 404)

    def _startseite(self) -> None:
        # Das Sitzungstoken steht nur in dieser Seite. Eine fremde Webseite kann
        # sie wegen der Same-Origin-Policy nicht lesen und das Token deshalb
        # nicht mitschicken.
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
            seite = fh.read().replace('<meta name="bh-token" content="">',
                                      f'<meta name="bh-token" content="{self.basis["token"]}">')
        self._senden(seite.encode("utf-8"), "text/html; charset=utf-8")

    def _erlaubte_hosts(self) -> set[str]:
        port = self.basis["port"]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _eigener_host(self) -> bool:
        return self.headers.get("Host") in self._erlaubte_hosts()

    def _gleiche_herkunft(self) -> bool:
        if not self._eigener_host():
            return False                 # DNS-Rebinding
        herkunft = self.headers.get("Origin")
        return not herkunft or herkunft in {f"http://{h}" for h in self._erlaubte_hosts()}

    def _eigener_aufruf(self) -> bool:
        return self._gleiche_herkunft() and secrets.compare_digest(
            self.headers.get("X-BH-Token") or "", self.basis["token"])

    def do_POST(self):                  # noqa: N802
        zerlegt = urllib.parse.urlparse(self.path)
        teile = [t for t in zerlegt.path.split("/") if t]
        if not self._eigener_aufruf():
            if self.headers.get("X-BH-Token") and self._gleiche_herkunft():
                # Richtige Herkunft, alter Schlüssel: der Server wurde neu
                # gestartet, während die Seite offen war
                return self._json({"fehler": "Der Server wurde neu gestartet, seit diese Seite "
                                   "geladen wurde. Nichts wurde gesendet — bitte die Seite neu "
                                   "laden.", "neu_laden": True}, 403)
            return self._json({"fehler": "Nur aus der Oberfläche selbst."}, 403)
        # Fehlt die Länge oder ist sie negativ, liest rfile.read(-1) bis zum
        # Verbindungsende - der Faden hinge, bis der Client aufgibt.
        angabe = (self.headers.get("Content-Length") or "").strip()
        laenge = int(angabe) if angabe.isascii() and angabe.isdigit() else -1
        if not 0 <= laenge <= 10_000 or "json" not in (self.headers.get("Content-Type") or ""):
            return self._json({"fehler": "ungültige Anfrage"}, 400)
        try:
            daten = json.loads(self.rfile.read(laenge) or b"{}")
            k = self._kontext(zerlegt.query)
            if teile == ["api", "eric", "postfach"]:
                antwort, status = postfach_abrufen(k)
                return self._json(antwort, status)
            if teile == ["api", "eric", "verwerfen"]:
                antwort, status = eric_verwerfen(k, daten)
                return self._json(antwort, status)
            if len(teile) == 3 and teile[:2] == ["api", "eric"]:
                antwort, status = eric_aktion(k, teile[2], daten)
                return self._json(antwort, status)
        except NichtGefunden as e:
            return self._json({"fehler": str(e)}, 404)
        except Exception as e:
            return self._json({"fehler": f"{type(e).__name__}: {e}"}, 500)
        self._json({"fehler": "unbekannter Endpunkt"}, 404)

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
        if teile == ["abgaben"]:
            return self._json(abgabepaket(k))
        if teile == ["elster"]:
            return self._json(ausfuellhilfe(k))
        if teile == ["ebilanz"]:
            return self._json(ebilanz_seite(k))
        if teile == ["aufgaben"]:
            return self._json(aufgaben_liste(k))
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
        if teile == ["eric"]:
            return self._json(eric_liste(k))
        if teile == ["eric", "offen"]:
            return self._json(eric_offen(k))
        if teile == ["postfach"]:
            return self._json(postfach_liste(k))
        if teile == ["postfach", "nachricht"]:
            return self._json(postfach_nachricht(k, self._abfrage("id")))
        # Die Lauf-ID enthält Schrägstriche und kommt deshalb als Abfrage
        if teile == ["eric", "lauf"]:
            return self._json(eric_lauf(k, self._abfrage("lauf")))
        if teile == ["ust"]:
            return self._json(ust_uebersicht(k))
        if teile == ["zm"]:
            return self._json(zm_uebersicht(k))
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

    def _abfrage(self, name: str) -> str:
        p = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (p.get(name) or [""])[0]

    def _eric_datei(self, k: Kontext, abfrage: str) -> None:
        try:
            pfad = eric_ansicht.datei(k.mandant_dir, self._abfrage("lauf"), self._abfrage("datei"))
        except eric_ansicht.NichtGefunden as e:
            raise NichtGefunden(str(e)) from None
        self._datei(pfad, inline=True)

    def _dokument_pdf(self, k: Kontext, kuerzel: str) -> None:
        try:
            pfad = dokument_pdf(k, kuerzel)
        except druck.DruckFehler as e:
            return self._senden(str(e).encode("utf-8"), "text/plain; charset=utf-8", 503)
        with open(pfad, "rb") as fh:
            daten = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(daten)))
        self.send_header("Content-Disposition",
                         f'inline; filename="{kuerzel}_{k.kuerzel}_{k.jahr}.pdf"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(daten)

    def _postfach_datei(self, k: Kontext) -> None:
        try:
            pfad = eric_postfach.datei(k.mandant_dir, self._abfrage("id"), self._abfrage("datei"))
        except eric_postfach.PostfachFehler as e:
            raise NichtGefunden(str(e)) from None
        if not os.path.exists(pfad):
            return self._senden(b"nicht gefunden", "text/plain; charset=utf-8", 404)
        with open(pfad, "rb") as fh:
            daten = fh.read()
        # Das Datenpaket hat keine Endung: die Art steht in den ersten Bytes
        typ = {"pdf": "application/pdf", "xml": "application/xml",
               "text": "text/plain; charset=utf-8"}[_inhaltsart(daten)] \
            if os.path.basename(pfad) == "datenpaket" \
            else (mimetypes.guess_type(pfad)[0] or "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(daten)))
        self.send_header("Content-Disposition", "inline")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(daten)

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
    Handler.basis = {"dbs": db_pfade, "wurzel": wurzel, "kuerzel": kuerzel, "jahr": jahr,
                     "port": port, "token": secrets.token_urlsafe(32)}
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
