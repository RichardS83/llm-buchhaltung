# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Was über ERiC an ELSTER ging — zum Nachlesen in `bh serve`.

Nur Lesen. Quelle sind die Ordner, die `eric_vorgang.ausfuehren()` je Lauf
anlegt, und für Abgaben von Hand in Mein ELSTER die Tabelle `voranmeldung`.
Nichts hier lädt ERiC: angezeigt wird, was auf der Platte liegt, und
verglichen wird es mit der Buchführung von heute.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET

from . import eric_vorgang as ev
from . import ebilanz, ebilanz_xbrl, eric_ertrag, eric_klartext, eric_xml
from . import umsatzsteuer as ust

DATEIEN = ("datensatz.xml", "vorpruefung.xml", "antwort.xml", "server.xml", "protokoll.pdf",
           ev.LAUF)

# Angaben im Datensatz, die keine Beträge sind: Kz 09 trägt die Hersteller-ID,
# Kz 10 die Berichtigung, Kz 22/26/29 sind Ankreuzfelder.
KEINE_BETRAEGE = ("Kz09", "Kz10", "Kz22", "Kz26", "Kz29")

_ID = re.compile(r"\d{4}/elster/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")


class NichtGefunden(Exception):
    pass


def _name(e: ET.Element) -> str:
    return e.tag.rsplit("}", 1)[-1]


def _lesen(pfad: str) -> str | None:
    if not os.path.exists(pfad):
        return None
    with open(pfad, encoding="utf-8", newline="") as fh:
        return fh.read()


def ordner(mandant_dir: str, lauf_id: str) -> str:
    """Lauf-ID (Pfad relativ zum Mandanten) -> Ordner, mit Prüfung gegen `..`."""
    if not _ID.fullmatch(lauf_id or "") or any(t in (".", "..") for t in lauf_id.split("/")):
        raise NichtGefunden(f"Lauf {lauf_id!r} gibt es nicht.")
    pfad = os.path.abspath(os.path.join(mandant_dir, lauf_id))
    if not pfad.startswith(os.path.abspath(mandant_dir) + os.sep) \
            or not os.path.exists(os.path.join(pfad, ev.LAUF)):
        raise NichtGefunden(f"Lauf {lauf_id!r} gibt es nicht.")
    return pfad


def datei(mandant_dir: str, lauf_id: str, name: str) -> str:
    if name not in DATEIEN:
        raise NichtGefunden(f"{name!r} gehört nicht zu einem Lauf.")
    return os.path.join(ordner(mandant_dir, lauf_id), name)


# ------------------------------------------------------------ Antworten lesen

def meldungen(xml: str | None) -> list[dict]:
    """Fehler und Hinweise aus der Rückgabe von ERiC, Feld für Feld, ohne
    etwas umzuformulieren."""
    if not xml or not xml.strip():
        return []
    try:
        wurzel = ET.fromstring(xml.encode("utf-8"))
    except ET.ParseError:
        return [{"art": "fehler", "text": xml.strip()[:500], "feld": None}]
    aus = []
    for e in wurzel.iter():
        n = _name(e)
        if n not in ("Hinweis", "FehlerRegelpruefung"):
            continue
        f = {_name(k): (k.text or "").strip() for k in e}
        feld = f.get("Feldidentifikator") or None
        aus.append({
            "art": "hinweis" if n == "Hinweis" else "fehler",
            "text": f.get("Text", ""),
            "feld": feld,
            "feld_kurz": re.sub(r"\[\d+\]", "", feld.rsplit("/", 1)[-1]) if feld else None,
            "regel": f.get("RegelName") or None,
            "id": f.get("FachlicheHinweisId") or f.get("FachlicheFehlerId") or None,
            "zeile": f.get("VordruckZeilennummer") or None,
        })
    return aus


def serverantwort(xml: str | None) -> dict | None:
    """Die Antwort des Annahmeservers: jede Rückgabe mit Code und Text, dazu
    Transferticket, Eingangszeit und Testmerker aus dem Kopf."""
    if not xml or not xml.strip():
        return None
    try:
        wurzel = ET.fromstring(xml.encode("utf-8"))
    except ET.ParseError:
        return {"lesbar": False}
    kopf = {}
    for e in wurzel.iter():
        if _name(e) in ("TransferTicket", "EingangsDatum", "Testmerker") and e.text:
            kopf.setdefault(_name(e), e.text.strip())
    rueckgaben = []
    for e in wurzel.iter():
        if _name(e) == "Rueckgabe":
            f = {_name(k): (k.text or "").strip() for k in e}
            if f.get("Code") or f.get("Text"):
                rueckgaben.append({"code": f.get("Code"), "text": f.get("Text")})
    eingang = kopf.get("EingangsDatum")
    if eingang and re.fullmatch(r"\d{14}", eingang):
        eingang = (f"{eingang[:4]}-{eingang[4:6]}-{eingang[6:8]}T"
                   f"{eingang[8:10]}:{eingang[10:12]}:{eingang[12:]}")
    return {"lesbar": True, "transferticket": kopf.get("TransferTicket"), "eingang": eingang,
            "testmerker": kopf.get("Testmerker"), "rueckgaben": rueckgaben,
            "angenommen": bool(rueckgaben) and all(r["code"] in ("0", None, "")
                                                   for r in rueckgaben)}


# ------------------------------------------------------- Abgleich Buchführung

def werte_aus_xml(xml: str, art: str):
    """Die Beträge, wie sie im Datensatz stehen — als Text, nicht umgerechnet.
    UStVA: {'Kz81': '600', ...}; ZM: [(USt-IdNr., Betrag, Umsatzart), ...]."""
    if art in eric_ertrag.BETRAEGE:
        return eric_ertrag.betraege_aus_xml(xml, art)
    wurzel = ET.fromstring(xml.encode("utf-8"))
    if art in ("ebilanz", "eroeffnung"):
        # Werthaltige Positionen aus Bilanz, GuV und Betriebsvermögensvergleich
        return {_name(e): (e.text or "").strip() for e in wurzel.iter()
                if e.get("decimals") and _name(e).startswith(("bs.", "is.", "BVV."))}
    if art == "ustva":
        for e in wurzel.iter():
            if _name(e) == "Umsatzsteuervoranmeldung":
                return {_name(k): (k.text or "").strip() for k in e
                        if re.fullmatch(r"Kz\d\d", _name(k))}
        return {}
    zeilen = []
    for e in wurzel.iter():
        if _name(e) == "zeile":
            f = {_name(k): (k.text or "").strip() for k in e}
            zeilen.append((f.get("knre", ""), f.get("betrag", ""), e.get("umsatzart")))
    return zeilen


# Reihenfolge wie auf dem Vordruck und der Seite Voranmeldungen, nicht wie im Schema
VORDRUCK = ("Kz81", "Kz86", "Kz21", "Kz45", "Kz46", "Kz47", "Kz84", "Kz85", "Kz66", "Kz67",
            "Kz83")


def _ustva_zeilen(gesendet: dict, buch: dict) -> list[dict]:
    reihe = list(VORDRUCK)
    reihe += sorted(k for k in set(gesendet) | set(buch) if k not in reihe
                    and k not in KEINE_BETRAEGE)
    return [{"kz": k[2:], "bezeichnung": ust.BEZEICHNUNG.get(k[2:], ""),
             "buch": buch.get(k), "gesendet": gesendet.get(k),
             "gleich": buch.get(k) == gesendet.get(k)}
            for k in reihe if k in gesendet or k in buch]


class _OhneEric:
    """Für den Abgleich wird der Datensatz nur gebaut, nicht geprüft: die
    Steuernummern bekommen einen Platzhalter, die Beträge sind echt."""
    @staticmethod
    def steuernummer(bescheid: str, bufa: str) -> str:
        return "0" * 13


def buchfuehrung(con: sqlite3.Connection, mandant_id: int, mandant_dir: str,
                 jahr: int, code: str, art: str):
    """Was die Buchführung heute für den Zeitraum ergibt, im Format des Datensatzes."""
    if art in ("ebilanz", "eroeffnung"):
        z = ev.geschaeftsjahr(con, mandant_id, jahr)
        a = (ebilanz_xbrl.abschluss(con, z.gj_id, mandant_dir) if art == "ebilanz" else
             ebilanz_xbrl.eroeffnung(con, z.gj_id, mandant_dir, ev.stammdaten(mandant_dir)))
        return {el: ebilanz_xbrl._betrag(c) for el, c in a.werte.items() if c is not None}
    if art in eric_ertrag.BETRAEGE:
        # Den Datensatz so bauen, wie er heute entstünde, und seine Beträge lesen
        e = ev.Einstellung("", "00000")
        ds = ev.datensatz(con, mandant_id, mandant_dir, ev.zeitraum(con, mandant_id, jahr, code),
                          art, e, _OhneEric(), test=False)
        return eric_ertrag.betraege_aus_xml(ds.xml, art)
    z = ust.zeitraum_aus_code(jahr, code)
    if art == "ustva":
        e = ust.einstellungen(mandant_dir)
        return eric_xml.ustva_werte(ust.kennzahlen(con, mandant_id, z, e["satz"],
                                                   e["ermaessigt"],
                                                   e["reverse_charge_abziehbar"]))
    return {idnr: str(euro) for idnr, euro in
            eric_xml.zm_zeilen(ust.zusammenfassende_meldung(con, mandant_id, z))}


def vergleich(art: str, gesendet, buch: dict) -> list[dict]:
    """`gesendet` wie aus `werte_aus_xml()`, `buch` wie aus `buchfuehrung()`."""
    if art == "ustva":
        return _ustva_zeilen(gesendet, buch)
    if art in eric_ertrag.BETRAEGE:
        reihe = [k for k in eric_ertrag.BETRAEGE[art] if k in gesendet or k in buch]
        return [{"kz": k, "bezeichnung": "", "buch": buch.get(k), "gesendet": gesendet.get(k),
                 "gleich": buch.get(k) == gesendet.get(k)} for k in reihe]
    if art in ("ebilanz", "eroeffnung"):
        # Nur die Positionen der Bilanz und GuV; BVV steht nicht in der Buchführung
        g = {k: v for k, v in gesendet.items() if not k.startswith("BVV.")}
        reihe = sorted(set(g) | set(buch), key=lambda k: (not k.startswith("bs.ass"),
                                                          not k.startswith("bs."), k))
        return [{"kz": k, "bezeichnung": "", "buch": buch.get(k), "gesendet": g.get(k),
                 "gleich": buch.get(k) == g.get(k)} for k in reihe]
    g = {idnr: betrag for idnr, betrag, _ in gesendet}
    return [{"kz": idnr, "bezeichnung": "", "buch": buch.get(idnr), "gesendet": g.get(idnr),
             "gleich": buch.get(idnr) == g.get(idnr)} for idnr in sorted(set(buch) | set(g))]


def abgleich(con, mandant_id, mandant_dir, jahr, code, art, gesendet) -> dict:
    """Zeile für Zeile: Buchführung heute ↔ übermittelt."""
    try:
        buch = buchfuehrung(con, mandant_id, mandant_dir, jahr, code, art)
    except (eric_xml.DatenFehlen, ebilanz_xbrl.EBilanzFehler, ev.VorgangFehler) as f:
        return {"zeilen": [], "abweichend": None, "fehler": str(f)}
    zeilen = vergleich(art, gesendet, buch)
    return {"zeilen": zeilen, "abweichend": sum(not z["gleich"] for z in zeilen),
            "fehler": None}


# ------------------------------------------------------------------ Liste

def laeufe(mandant_dir: str) -> list[dict]:
    aus = []
    if not os.path.isdir(mandant_dir):
        return aus
    for jahr in sorted(os.listdir(mandant_dir)):
        basis = os.path.join(mandant_dir, jahr, "elster")
        if not (jahr.isdigit() and os.path.isdir(basis)):
            continue
        for vorgang in sorted(os.listdir(basis)):
            vdir = os.path.join(basis, vorgang)
            if not os.path.isdir(vdir):
                continue
            for name in sorted(os.listdir(vdir)):
                pfad = os.path.join(vdir, name, ev.LAUF)
                if os.path.exists(pfad):
                    with open(pfad, encoding="utf-8") as fh:
                        d = json.load(fh)
                    d["id"] = f"{jahr}/elster/{vorgang}/{name}"
                    aus.append(d)
    aus.sort(key=lambda d: d.get("zeit") or "", reverse=True)
    return aus


def _gesendet_status(d: dict) -> str:
    if d.get("abgebrochen"):
        return "abgebrochen"
    e = d.get("ergebnis") or {}
    if d["modus"] == "pruefen":
        return "plausibel" if e.get("ok") else "nicht plausibel"
    if e.get("gesendet"):
        return "angenommen"
    return "abgelehnt"


def uebersicht(con: sqlite3.Connection, mandant_id: int, mandant_dir: str) -> dict:
    zwischen = {}
    zeilen = []
    for d in laeufe(mandant_dir):
        ordner_ = os.path.join(mandant_dir, d["id"])
        xml = _lesen(os.path.join(ordner_, "datensatz.xml")) or ""
        schluessel = (d["jahr"], d["zeitraum"], d["art"])
        if schluessel not in zwischen:
            try:
                zwischen[schluessel] = buchfuehrung(con, mandant_id, mandant_dir, *schluessel)
            except (eric_xml.DatenFehlen, ebilanz_xbrl.EBilanzFehler, ev.VorgangFehler):
                zwischen[schluessel] = None
        buch = zwischen[schluessel]
        abweichend = None
        if buch is not None and xml:
            abweichend = sum(not r["gleich"]
                             for r in vergleich(d["art"], werte_aus_xml(xml, d["art"]), buch))
        vp = meldungen(_lesen(os.path.join(ordner_, "vorpruefung.xml")))
        an = meldungen(_lesen(os.path.join(ordner_, "antwort.xml")))
        alle = vp + [m for m in an if m not in vp]
        zeilen.append({
            "weg": "eric", "id": d["id"], "modus": d["modus"], "art": d["art"],
            "jahr": d["jahr"], "zeitraum": d["zeitraum"], "label": d["label"],
            "bezeichnung": d["bezeichnung"], "zeit": d["zeit"],
            "transferticket": (d.get("ergebnis") or {}).get("transferticket"),
            "status": _gesendet_status(d), "berichtigt": d.get("berichtigt"),
            "fehler": sum(m["art"] == "fehler" for m in alle),
            "hinweise": sum(m["art"] == "hinweis" for m in alle),
            "abweichend": abweichend,
            "unveraendert": ev.sha256(xml) == d["datensatz"]["sha256"] if xml else False,
        })
    # Abgaben, die nicht über ERiC liefen: alles mit Transferticket in
    # voranmeldung, das zu keinem gesendeten Lauf gehört.
    eric_tickets = {z["transferticket"] for z in zeilen
                    if z["modus"] == "senden" and z["transferticket"]}
    for r in con.execute("SELECT * FROM voranmeldung WHERE mandant_id=? AND transferticket"
                         " IS NOT NULL ORDER BY abgegeben_am DESC, jahr DESC, zeitraum DESC",
                         (mandant_id,)):
        if r["transferticket"] in eric_tickets:
            continue
        z = ust.zeitraum_aus_code(r["jahr"], r["zeitraum"])
        kz = json.loads(r["kennzahlen"]) if r["kennzahlen"] else None
        abweichend = None
        if kz and r["art"] == "ustva":
            abweichend = abgleich(con, mandant_id, mandant_dir, r["jahr"], r["zeitraum"],
                                  "ustva", kz)["abweichend"]
        zeilen.append({
            "weg": "hand", "id": None, "modus": "abgabe", "art": r["art"], "jahr": r["jahr"],
            "zeitraum": r["zeitraum"], "label": z.label, "bezeichnung": z.bezeichnung,
            "zeit": r["abgegeben_am"], "transferticket": r["transferticket"],
            "status": r["status"], "notiz": r["notiz"], "werte_erfasst": bool(kz),
            "abweichend": abweichend,
        })
    zeilen.sort(key=lambda z: (z["zeit"] or "", z["label"]), reverse=True)
    return {"zeilen": zeilen}


# --------------------------------------------------------- Zur Freigabe

def _vorschlag(mandant_dir: str, lauf_id: str) -> dict | None:
    pfad = os.path.join(mandant_dir, lauf_id, ev.VORSCHLAG)
    if not os.path.exists(pfad):
        return None
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)


# Erstellungsdaten im Datensatz: sie ändern sich mit jedem Tag, der Inhalt nicht
_DATUM = re.compile(r"<(de-gcd:genInfo\.doc\.id\.generationDate|Erstellungsdatum)\b[^>]*>[^<]*</\1>")


def inhalt_sha(xml: str) -> str:
    """Prüfsumme ohne Erstellungsdatum — gleich, solange sich nur der Tag ändert."""
    return ev.sha256(_DATUM.sub("", ohne_testmerker(xml)))


def vorschlaege(mandant_dir: str, alle: list[dict] | None = None) -> list[dict]:
    """Vorgelegte Prüfungen und wie weit sie sind.

    Der Stand ergibt sich aus den Läufen danach: eine angenommene Abgabe macht
    den Vorschlag erledigt, eine neuere Prüfung mit anderem Datensatz macht ihn
    überholt — dann hat sich die Buchführung bewegt und er gilt nicht mehr.
    Eine neuere Prüfung mit demselben Datensatz übernimmt ihn: abgegeben wird
    immer über die neueste Prüfung.
    """
    alle = laeufe(mandant_dir) if alle is None else alle
    aus = []
    for d in alle:
        if d["modus"] != "pruefen":
            continue
        v = _vorschlag(mandant_dir, d["id"])
        if not v:
            continue
        schluessel = (d["jahr"], d["zeitraum"], d["art"])

        def kern(lauf: dict) -> str:
            return inhalt_sha(_lesen(os.path.join(mandant_dir, lauf["id"], "datensatz.xml")) or "")

        sha = kern(d)
        danach = [l for l in alle if (l["jahr"], l["zeitraum"], l["art"]) == schluessel
                  and (l.get("zeit") or "") > d["zeit"]]       # neueste zuerst
        abgabe = next((l for l in danach if l["modus"] == "senden"
                       and (l.get("ergebnis") or {}).get("gesendet")), None)
        neueste = next((l for l in danach if l["modus"] == "pruefen"), None)
        spaeterer_vorschlag = any(l["modus"] == "pruefen" and _vorschlag(mandant_dir, l["id"])
                                  for l in danach)
        test = next((l for l in danach if l["modus"] == "test"
                     and (l.get("ergebnis") or {}).get("gesendet") and kern(l) == sha), None)
        if v.get("verworfen"):
            stand = "verworfen"
        elif spaeterer_vorschlag:
            stand = "ersetzt"                      # auch wenn danach abgegeben wurde
        elif abgabe:
            stand = "abgegeben"
        elif neueste and kern(neueste) != sha:
            stand = "überholt"
        else:
            stand = "offen"
        aus.append({
            "id": d["id"], "art": d["art"], "jahr": d["jahr"], "zeitraum": d["zeitraum"],
            "label": d["label"], "bezeichnung": d["bezeichnung"], "geprueft": d["zeit"],
            "betrag_cent": d.get("betrag_cent"), "vorschlag": v, "stand": stand,
            # Abgegeben wird über die neueste Prüfung desselben Datensatzes
            "freigabe_lauf": neueste["id"] if neueste and stand == "offen" else d["id"],
            "test": test and {"id": test["id"], "zeit": test["zeit"],
                              "testzertifikat": bool(test.get("testzertifikat"))},
            "abgabe": abgabe and {"id": abgabe["id"], "zeit": abgabe["zeit"],
                                  "transferticket": abgabe["ergebnis"].get("transferticket")},
            "neuere_pruefung": neueste["id"] if stand == "überholt" else None,
        })
    # Je Jahr in der Reihenfolge, in der abgegeben wird: die Erklärungen vor der
    # E-Bilanz, die ihre Zahlen bestätigt
    reihe = {a: i for i, a in enumerate(("kst", "gewst", "ebilanz", "eroeffnung", "ustva", "zm"))}
    aus.sort(key=lambda v: (-v["jahr"], reihe.get(v["art"], 9), v["zeitraum"]))
    return aus


# ---------------------------------------------------------------- Ein Lauf

def klartext(mandant_dir: str, lauf: dict, xml: str | None) -> dict:
    """Der Datensatz mit Vordruck, Zeile und Wortlaut aus der SDK-Dokumentation.
    Scheitert das Lesen, fehlt nur diese Ansicht — nicht die Seite."""
    try:
        positionen = {e["element"]: e["bezeichnung"]
                      for e in (ebilanz.laden(mandant_dir) or {}).values()
                      if e.get("element") and e.get("bezeichnung")}
        return eric_klartext.lesbar(xml, lauf["art"], lauf.get("jahr"),
                                    lauf.get("datenart_version"), positionen=positionen)
    except Exception as f:                                   # noqa: BLE001
        return {"gruppen": [], "fehler": f"{type(f).__name__}: {f}"}


def ohne_testmerker(xml: str) -> str:
    """Der Datensatz eines Tests, wie er ohne Testmerker aussähe — um zu sehen,
    ob Test und Prüfung dieselbe Meldung betreffen."""
    return re.sub(r"\n[ \t]*<Testmerker>[^<]*</Testmerker>", "", xml, count=1)


def _testmerker(xml: str | None) -> str | None:
    if not xml:
        return None
    try:
        for e in ET.fromstring(xml.encode("utf-8")).iter():
            if _name(e) == "Testmerker" and e.text:
                return e.text.strip()
    except ET.ParseError:
        pass
    return None


def detail(con: sqlite3.Connection, mandant_id: int, mandant_dir: str, kuerzel: str,
           lauf_id: str) -> dict:
    o = ordner(mandant_dir, lauf_id)
    with open(os.path.join(o, ev.LAUF), encoding="utf-8") as fh:
        d = json.load(fh)
    xml = _lesen(os.path.join(o, "datensatz.xml"))
    roh = {n: _lesen(os.path.join(o, f"{n}.xml")) for n in ("vorpruefung", "antwort", "server")}
    z = ev.zeitraum(con, mandant_id, d["jahr"], d["zeitraum"])
    periode = (f" --quartal {z.quartal}" if z.quartal else f" --monat {z.monat}" if z.monat else "")
    if d["art"] in ev.JAHRESARTEN:
        stand = ev.stand(mandant_dir, d["jahr"], d["zeitraum"], d["art"]) or {"status": "offen"}
    else:
        stand = ust.stand(con, mandant_id, z, art=d["art"])
    return {
        "id": lauf_id, "lauf": d, "status": _gesendet_status(d),
        "datensatz": xml,
        "testmerker": _testmerker(xml),
        "sha256_jetzt": ev.sha256(xml) if xml is not None else None,
        "unveraendert": xml is not None and ev.sha256(xml) == d["datensatz"]["sha256"],
        "vorpruefung": {"xml": roh["vorpruefung"], "meldungen": meldungen(roh["vorpruefung"])},
        "antwort": {"xml": roh["antwort"], "meldungen": meldungen(roh["antwort"])},
        "server": {"xml": roh["server"], **(serverantwort(roh["server"]) or {})},
        "protokoll": os.path.exists(os.path.join(o, "protokoll.pdf")),
        "abgleich": abgleich(con, mandant_id, mandant_dir, d["jahr"], d["zeitraum"], d["art"],
                             werte_aus_xml(xml, d["art"])) if xml else None,
        "stand": {k: stand.get(k) for k in ("status", "transferticket", "abgegeben_am")},
        "befehl_senden": f"./bh eric senden {kuerzel} {d['jahr']} {d['art']}{periode}",
        "klartext": klartext(mandant_dir, d, xml),
        "vorschlag": next((v for v in vorschlaege(mandant_dir)
                           if lauf_id in (v["id"], v["freigabe_lauf"])), None),
    }
