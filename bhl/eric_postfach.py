# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Das Postfach von Mein ELSTER über ERiC abholen — `bh eric postfach`.

Was das Finanzamt oder das BZSt einem ELSTER-Konto bereitstellt (Mitteilungen,
Bescheide, Rückmeldungen), holt das Verfahren ElsterDatenabholung ab:

    PostfachStatus        zählt, je Bereitstellungsdatenart          ändert nichts
    PostfachAnfrage       Metadaten und Verweise auf den Inhalt
    OttoDatenAbholen      Inhalt und Anhänge aus dem ELSTER-Objektspeicher
    PostfachBestaetigung  „erhalten“ — Pflicht binnen 24 Stunden nach der Anfrage

Bestätigt wird erst, wenn alles auf der Platte liegt. Abgelegt wird je
Bereitstellung unter `mandanten/<kürzel>/elster/postfach/<datenart>/<id>/`:
`meta.json`, `datenpaket` (so wie geliefert) und die Anhänge unter ihrem
Dateinamen. Die Antworten des Servers liegen unter `postfach/protokoll/`.

Welches Postfach: das des ELSTER-Kontos, dessen Zertifikat in mandant.json
steht. Zwei Mandanten mit demselben Zertifikat teilen sich ein Postfach.
"""

from __future__ import annotations

import ctypes as ct
import datetime
import gzip
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import eric as eric_mod
from . import eric_vorgang as ev
from . import eric_xml
from . import zugang

OTTO_OK = 0
ABHOLUNG = ("PostfachStatus", "PostfachAnfrage", "PostfachBestaetigung")


class PostfachFehler(Exception):
    pass


def _name(e: ET.Element) -> str:
    return e.tag.rsplit("}", 1)[-1]


# ------------------------------------------------------------------ Otto

class Otto:
    """libotto aus dem ERiC-SDK: lädt Inhalte aus dem ELSTER-Objektspeicher."""

    def __init__(self, bibliothek: str, protokoll: str, lib=None):
        self.protokoll = os.path.abspath(protokoll)
        self._lib = lib
        self._pfad = os.path.join(os.path.abspath(os.path.expanduser(bibliothek)),
                                  eric_mod.bibliotheksname().replace("ericapi", "otto"))
        self._instanz = None

    def __enter__(self) -> "Otto":
        if self._lib is None:
            self._lib = ct.CDLL(self._pfad)
            L = self._lib
            P, S = ct.c_void_p, ct.c_char_p
            L.OttoInstanzErzeugen.argtypes = [S, P, P, ct.POINTER(P)]
            L.OttoInstanzFreigeben.argtypes = [P]
            L.OttoRueckgabepufferErzeugen.argtypes = [P, ct.POINTER(P)]
            L.OttoRueckgabepufferGroesse.argtypes = [P]
            L.OttoRueckgabepufferGroesse.restype = ct.c_uint64
            L.OttoRueckgabepufferInhalt.argtypes = [P]
            L.OttoRueckgabepufferInhalt.restype = P
            L.OttoRueckgabepufferFreigeben.argtypes = [P]
            L.OttoDatenAbholen.argtypes = [P, S, ct.c_uint32, S, S, S, S, P]
            L.OttoHoleFehlertext.argtypes = [ct.c_int]
            L.OttoHoleFehlertext.restype = S
        os.makedirs(self.protokoll, exist_ok=True)
        h = ct.c_void_p()
        self._pruefe(self._lib.OttoInstanzErzeugen(self.protokoll.encode(), None, None,
                                                   ct.byref(h)))
        self._instanz = h
        return self

    def __exit__(self, *_):
        if self._instanz is not None:
            self._lib.OttoInstanzFreigeben(self._instanz)
            self._instanz = None

    def _pruefe(self, rc: int) -> None:
        if rc != OTTO_OK:
            text = self._lib.OttoHoleFehlertext(rc)
            raise PostfachFehler(f"Otto {rc}: {text.decode('utf-8', 'replace') if text else ''}")

    def abholen(self, objekt_id: str, groesse: int, zertifikat: str, pin: str,
                hersteller_id: str) -> bytes:
        puffer = ct.c_void_p()
        self._pruefe(self._lib.OttoRueckgabepufferErzeugen(self._instanz, ct.byref(puffer)))
        try:
            self._pruefe(self._lib.OttoDatenAbholen(
                self._instanz, objekt_id.encode(), min(int(groesse or 0), 2**32 - 1),
                os.path.expanduser(zertifikat).encode(), pin.encode(), hersteller_id.encode(),
                None, puffer))
            n = self._lib.OttoRueckgabepufferGroesse(puffer)
            zeiger = self._lib.OttoRueckgabepufferInhalt(puffer)
            return ct.string_at(zeiger, n) if n else b""
        finally:
            self._lib.OttoRueckgabepufferFreigeben(puffer)


# ------------------------------------------------------------------ XML

def _anfrage_xml(datenart: str, element: ET.Element, e: ev.Einstellung, stamm: dict) -> str:
    return eric_xml.datenabholung(datenart, element, hersteller_id=e.hersteller_id,
                                  datenlieferant=stamm.get("name") or "",
                                  produkt=e.produkt, produktversion=e.produktversion)


def status_xml(e, stamm) -> str:
    return _anfrage_xml("PostfachStatus", ET.Element(
        f"{{{eric_xml.NS_ABHOLUNG}}}PostfachStatus", einschraenkung="alle"), e, stamm)


def anfrage_xml(datenarten: list[str], e, stamm, einschraenkung: str = "alle") -> str:
    a = ET.Element(f"{{{eric_xml.NS_ABHOLUNG}}}PostfachAnfrage",
                   einschraenkung=einschraenkung, max="1000")
    for d in datenarten:
        ET.SubElement(a, f"{{{eric_xml.NS_ABHOLUNG}}}DatenartBereitstellung", name=d)
    return _anfrage_xml("PostfachAnfrage", a, e, stamm)


def bestaetigung_xml(ids: list[str], e, stamm) -> str:
    b = ET.Element(f"{{{eric_xml.NS_ABHOLUNG}}}PostfachBestaetigung")
    liste = ET.SubElement(b, f"{{{eric_xml.NS_ABHOLUNG}}}Bereitstellungen")
    for i in ids:
        ET.SubElement(liste, f"{{{eric_xml.NS_ABHOLUNG}}}Bereitstellung", id=i)
    return _anfrage_xml("PostfachBestaetigung", b, e, stamm)


def status_lesen(xml: str) -> list[dict]:
    aus = []
    for e in ET.fromstring(xml.encode("utf-8")).iter():
        if _name(e) == "PostfachStatusAntwort":
            for d in e:
                if _name(d) == "DatenartBereitstellung":
                    aus.append({"datenart": d.get("name"), "anzahl": int(d.get("anzahltreffer", 0)),
                                "rolle": e.get("rolle")})
    return aus


def bereitstellungen_lesen(xml: str) -> list[dict]:
    """Jede Bereitstellung mit Metadaten, Datenpaket und Anhängen."""
    aus = []
    for d in ET.fromstring(xml.encode("utf-8")).iter():
        if _name(d) != "DatenartBereitstellung":
            continue
        for b in d.iter():
            if _name(b) != "Bereitstellung":
                continue
            eintrag = {"id": b.get("id"), "datenart": d.get("name"),
                       "bereitstellungsticket": b.get("bereitstellungsticket"),
                       "groesse": b.get("groesse"), "meta": {}, "metainfomaps": [],
                       "datenpaket": None, "anhaenge": []}
            for k in b:
                n = _name(k)
                if n == "MetaInformationen":
                    for m in k:
                        if _name(m) == "Meta":
                            eintrag["meta"][m.get("name")] = (m.text or "").strip()
                        elif _name(m) == "MetaInfoMaps":
                            for mp in m:
                                eintrag["metainfomaps"].append({
                                    "typ": mp.get("typ"),
                                    "meta": {x.get("name"): (x.text or "").strip()
                                             for x in mp if _name(x) == "Meta"},
                                    "listen": {x.get("typ"): [(y.text or "").strip() for y in x]
                                               for x in mp if _name(x) == "MetaList"}})
                elif n == "Datenpaket":
                    f = {_name(x): (x.text or "").strip() for x in k}
                    eintrag["datenpaket"] = {"referenz": f.get("ReferenzId"),
                                             "encoding": f.get("Encoding") or None,
                                             "groesse": int(f.get("Groesse") or 0)}
                elif n == "Anhaenge":
                    for a in k:
                        f = {_name(x): (x.text or "").strip() for x in a}
                        eintrag["anhaenge"].append({
                            "dateiname": f.get("DateinameMitErweiterung"),
                            "bezeichnung": f.get("Dateibezeichnung"),
                            "typ": f.get("Dateityp"), "referenz": f.get("DateiReferenzId"),
                            "groesse": int(f.get("DateiGroesse") or 0)})
            aus.append(eintrag)
    return aus


def bestaetigung_lesen(xml: str) -> dict[str, dict]:
    aus = {}
    for e in ET.fromstring(xml.encode("utf-8")).iter():
        if _name(e) == "PostfachBestaetigungAntwort":
            for b in e.iter():
                if _name(b) == "Bereitstellung":
                    r = {_name(x): (x.text or "").strip() for x in b.iter()}
                    aus[b.get("id")] = {"code": r.get("Code"), "text": r.get("Text")}
    return aus


# --------------------------------------------------------------- Ablage

def ablage(mandant_dir: str) -> str:
    return os.path.join(mandant_dir, "elster", "postfach")


def _sicher(name: str) -> str:
    """Dateiname aus fremder Quelle: nur der letzte Bestandteil, ohne Sonderzeichen."""
    name = os.path.basename(name or "").strip() or "anhang"
    name = re.sub(r"[^\w.\-]+", "_", name)
    return name.lstrip(".") or "anhang"


def _entpacken(daten: bytes) -> bytes:
    return gzip.decompress(daten) if daten[:2] == b"\x1f\x8b" else daten


@dataclass
class Abruf:
    status: list[dict]
    neu: list[str] = field(default_factory=list)
    vorhanden: list[str] = field(default_factory=list)
    bestaetigt: dict = field(default_factory=dict)


def abrufen(kuerzel: str, mandant_dir: str, einstellung: ev.Einstellung, *,
            eric_klasse=eric_mod.Eric, otto_klasse=Otto,
            jetzt: datetime.datetime | None = None) -> Abruf:
    """Status, Anfrage, Inhalte holen, ablegen, bestätigen."""
    jetzt = jetzt or datetime.datetime.now().replace(microsecond=0)
    if not eric_mod.hinweise_bestaetigt(einstellung.bibliothek):
        raise PostfachFehler("Zuerst einmal bh eric einrichten (Datenschutzhinweise).")
    stamm = ev.stammdaten(mandant_dir)
    zert = (stamm.get("elster") or {}).get("zertifikat")
    if not zert:
        raise PostfachFehler('mandant.json: "elster": {"zertifikat": "<pfad.pfx>"} fehlt.')
    try:
        pin = zugang.eric_pin(kuerzel)
    except zugang.ZugangFehlt as f:
        raise PostfachFehler(str(f)) from None
    zert = os.path.expanduser(zert)
    ziel = ablage(mandant_dir)
    protokoll = os.path.join(ziel, "protokoll")
    os.makedirs(protokoll, exist_ok=True)
    stempel = f"{jetzt:%Y%m%d-%H%M%S}"

    def senden(er, datenart: str, xml: str) -> str:
        r = er.bearbeite(xml, f"{datenart}_{eric_xml.ABHOLUNG_VERSION}", senden=True,
                         zertifikat=zert, pin=pin)
        with open(os.path.join(protokoll, f"{stempel}-{datenart}.xml"), "w",
                  encoding="utf-8") as fh:
            fh.write(r.server or r.antwort)
        if not r.ok:
            raise PostfachFehler(f"{datenart}: {r.text} {' '.join(r.fehler)}".strip())
        return r.server

    with eric_klasse(einstellung.bibliothek, ev.protokoll(mandant_dir)) as er:
        status = status_lesen(senden(er, "PostfachStatus", status_xml(einstellung, stamm)))
        abruf = Abruf(status)
        arten = sorted({s["datenart"] for s in status if s["anzahl"]})
        if not arten:
            return abruf
        liste = bereitstellungen_lesen(senden(er, "PostfachAnfrage",
                                              anfrage_xml(arten, einstellung, stamm)))
        abgelegt = []
        with otto_klasse(einstellung.bibliothek, ev.protokoll(mandant_dir)) as otto:
            for b in liste:
                ordner = os.path.join(ziel, _sicher(b["datenart"]), _sicher(b["id"]))
                meta_pfad = os.path.join(ordner, "meta.json")
                if os.path.exists(meta_pfad):
                    with open(meta_pfad, encoding="utf-8") as fh:
                        alt = json.load(fh)
                    if (alt.get("bestaetigung") or {}).get("code") == "0":
                        abruf.vorhanden.append(b["id"])
                        continue
                os.makedirs(ordner, exist_ok=True)
                if b["datenpaket"] and b["datenpaket"]["referenz"]:
                    daten = otto.abholen(b["datenpaket"]["referenz"], b["datenpaket"]["groesse"],
                                         zert, pin, einstellung.hersteller_id)
                    with open(os.path.join(ordner, "datenpaket"), "wb") as fh:
                        fh.write(_entpacken(daten))
                for a in b["anhaenge"]:
                    if not a["referenz"]:
                        continue
                    daten = otto.abholen(a["referenz"], a["groesse"], zert, pin,
                                         einstellung.hersteller_id)
                    a["datei"] = _sicher(a["dateiname"])
                    with open(os.path.join(ordner, a["datei"]), "wb") as fh:
                        fh.write(_entpacken(daten))
                b["abgeholt_am"] = jetzt.isoformat()
                with open(meta_pfad, "w", encoding="utf-8") as fh:
                    json.dump(b, fh, ensure_ascii=False, indent=2)
                abgelegt.append((b, meta_pfad))
                abruf.neu.append(b["id"])
        if abgelegt:
            antwort = senden(er, "PostfachBestaetigung",
                             bestaetigung_xml([b["id"] for b, _ in abgelegt], einstellung, stamm))
            abruf.bestaetigt = bestaetigung_lesen(antwort)
            for b, pfad in abgelegt:
                b["bestaetigung"] = abruf.bestaetigt.get(b["id"])
                with open(pfad, "w", encoding="utf-8") as fh:
                    json.dump(b, fh, ensure_ascii=False, indent=2)
    return abruf


# --------------------------------------------------------------- Ansicht

_ID = re.compile(r"[\w.\-]+/[\w.\-]+")


def nachrichten(mandant_dir: str) -> list[dict]:
    ziel = ablage(mandant_dir)
    aus = []
    if not os.path.isdir(ziel):
        return aus
    for art in sorted(os.listdir(ziel)):
        if art == "protokoll" or not os.path.isdir(os.path.join(ziel, art)):
            continue
        for i in sorted(os.listdir(os.path.join(ziel, art))):
            pfad = os.path.join(ziel, art, i, "meta.json")
            if os.path.exists(pfad):
                with open(pfad, encoding="utf-8") as fh:
                    d = json.load(fh)
                d["pfad"] = f"{art}/{i}"
                aus.append(d)
    def datum(d):
        m = d["meta"].get("bescheiddatum") or d["meta"].get("erstelldatum") or ""
        return f"{m[:4]}-{m[4:6]}-{m[6:]}" if re.fullmatch(r"\d{8}", m) else d.get("abgeholt_am") or ""
    aus.sort(key=datum, reverse=True)
    return aus


def ordner(mandant_dir: str, nachricht_id: str) -> str:
    if not _ID.fullmatch(nachricht_id or "") or ".." in nachricht_id.split("/"):
        raise PostfachFehler(f"Nachricht {nachricht_id!r} gibt es nicht.")
    pfad = os.path.abspath(os.path.join(ablage(mandant_dir), nachricht_id))
    if not pfad.startswith(os.path.abspath(ablage(mandant_dir)) + os.sep) \
            or not os.path.exists(os.path.join(pfad, "meta.json")):
        raise PostfachFehler(f"Nachricht {nachricht_id!r} gibt es nicht.")
    return pfad


def nachricht(mandant_dir: str, nachricht_id: str) -> dict:
    o = ordner(mandant_dir, nachricht_id)
    with open(os.path.join(o, "meta.json"), encoding="utf-8") as fh:
        d = json.load(fh)
    d["pfad"] = nachricht_id
    inhalt = None
    p = os.path.join(o, "datenpaket")
    if os.path.exists(p):
        with open(p, "rb") as fh:
            roh = fh.read()
        kodierung = (d.get("datenpaket") or {}).get("encoding") or "utf-8"
        try:
            inhalt = roh.decode(kodierung)
        except (LookupError, UnicodeDecodeError):
            inhalt = None
        d["datenpaket_bytes"] = len(roh)
    d["inhalt"] = inhalt
    return d


def datei(mandant_dir: str, nachricht_id: str, name: str) -> str:
    o = ordner(mandant_dir, nachricht_id)
    erlaubt = {"datenpaket"} | {a.get("datei") for a in nachricht(mandant_dir, nachricht_id)
                                ["anhaenge"] if a.get("datei")}
    if name not in erlaubt:
        raise PostfachFehler(f"{name!r} gehört nicht zu dieser Nachricht.")
    return os.path.join(o, name)
