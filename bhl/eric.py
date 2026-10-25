# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""ERiC — die C-Bibliothek der Finanzverwaltung, über ctypes angebunden.

Was dieses Repository **nicht** enthält und nie enthalten darf
----------------------------------------------------------------
Die Bibliothek selbst, ihre Plugins und ihre Dokumentation. Die
ERiC-Lizenzvereinbarung erlaubt, ERiC in die *eigene* Software einzubinden
und mit ihr zusammen weiterzugeben; eine Unterlizenzierung erlaubt sie nicht.
Wer `bh` mit ERiC benutzen will, registriert sich deshalb selbst als
ELSTER-Entwickler, beantragt eine eigene Hersteller-ID, akzeptiert die Lizenz
und lädt das SDK selbst herunter. `docs/eric.md` beschreibt den Weg.

Ebenso wenig stehen hier Hersteller-ID, Zertifikat oder PIN. Sie kommen aus
der Umgebung oder dem Schlüsselbund (`bhl/zugang.py`).

Drei Betriebsarten, eine Funktion
---------------------------------
Alles läuft über `EricBearbeiteVorgang`; die Flags entscheiden:

* **prüfen** — nur ERIC_VALIDIERE (+ DRUCKE für das Vorschau-PDF). Lokal,
  nichts verlässt den Rechner.
* **test** — zusätzlich ERIC_SENDE, der Datensatz trägt aber den Testmerker:
  der Annahmeserver prüft ihn, antwortet und löscht ihn.
* **senden** — derselbe Aufruf ohne Testmerker. Das ist die Abgabe.

ERiC ist nicht threadfest (Singlethread-API) und darf je Prozess nur einmal
initialisiert sein. `Eric` ist deshalb ein Kontextmanager, der beim Verlassen
`EricBeende` ruft.
"""

from __future__ import annotations

import ctypes as ct
import datetime
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# Bearbeitungsflags (ericapi.h / Entwicklerhandbuch, Kap. 5.1)
VALIDIERE = 1 << 1
SENDE = 1 << 2
DRUCKE = 1 << 5
PRUEFE_HINWEISE = 1 << 7

# Rückgabewerte (eric_fehlercodes.h), soweit dieses Modul sie unterscheidet
OK = 0
PRUEF_FEHLER = 610001002
HINWEISE = 610001003
MEHRFACHE_INITIALISIERUNG = 610001082
FEHLER_THEADER = 610101210
FEHLER_NHEADER = 610101292
PIN_FALSCH = 610201106

# Was `bh` von den rund 220 Plugins braucht. ERiC lädt beim Start jedes Plugin
# im Verzeichnis einmal an — mit allen dauert das zwei Minuten, mit diesen
# keinen Augenblick. `einrichten()` verlinkt nur sie.
PLUGINS = (r"libcommonData\.", r"libcheckUStVA_\d{4}\.", r"libcheckZMDO\.",
           r"libcheckBilanz_\d+_\d+\.", r"libcheckElsterDatenabholung\.",
           r"libcheckKSt_\d{4}\.", r"libcheckGewSt_\d{4}\.")
PLUGIN_VERZEICHNIS = "bh-plugins"


def bibliotheksname() -> str:
    if sys.platform == "darwin":
        return "libericapi.dylib"
    if os.name == "nt":
        return "ericapi.dll"
    return "libericapi.so"


class EricFehler(Exception):
    def __init__(self, code: int, text: str, antwort: str = "", server: str = ""):
        super().__init__(f"ERiC {code}: {text}")
        self.code, self.text, self.antwort, self.server = code, text, antwort, server


# ------------------------------------------------------------ ctypes-Typen

if os.name == "nt":
    _FUNKTION = ct.WINFUNCTYPE
else:
    _FUNKTION = ct.CFUNCTYPE
PdfCallback = _FUNKTION(ct.c_int, ct.c_char_p, ct.c_void_p, ct.c_uint32, ct.c_void_p)


class DruckParameter(ct.Structure):
    _fields_ = [("version", ct.c_uint32), ("vorschau", ct.c_uint32),
                ("duplexDruck", ct.c_uint32), ("pdfName", ct.c_char_p),
                ("fussText", ct.c_char_p), ("pdfCallback", PdfCallback),
                ("pdfCallbackBenutzerdaten", ct.c_void_p)]


class VerschluesselungsParameter(ct.Structure):
    _fields_ = [("version", ct.c_uint32), ("zertifikatHandle", ct.c_uint32),
                ("pin", ct.c_char_p)]


def _signaturen(lib) -> None:
    """Argument- und Rückgabetypen setzen. Ohne sie schneidet ctypes Zeiger
    auf 32 Bit ab — auf 64-Bit-Systemen ein Absturz, kein Fehlercode."""
    P, S, U = ct.c_void_p, ct.c_char_p, ct.c_uint32
    for name, args, res in (
        ("EricInitialisiere", [S, S], ct.c_int),
        ("EricBeende", [], ct.c_int),
        ("EricRueckgabepufferErzeugen", [], P),
        ("EricRueckgabepufferInhalt", [P], S),
        ("EricRueckgabepufferFreigeben", [P], ct.c_int),
        ("EricBearbeiteVorgang", [S, S, U, P, P, P, P], ct.c_int),
        ("EricHoleFehlerText", [ct.c_int, P], ct.c_int),
        ("EricVersion", [P], ct.c_int),
        ("EricMakeElsterStnr", [S, S, S, P], ct.c_int),
        ("EricGetHandleToCertificate", [ct.POINTER(U), ct.POINTER(U), S], ct.c_int),
        ("EricCloseHandleToCertificate", [U], ct.c_int),
        ("EricHoleZertifikatEigenschaften", [U, S, P], ct.c_int),
    ):
        f = getattr(lib, name)
        f.argtypes, f.restype = args, res


# ------------------------------------------------------------ Ergebnis

def _texte(xml: str, *namen: str) -> list[str]:
    """Alle <Text> unter Elementen mit einem der Namen — ohne Namensräume."""
    if not xml.strip():
        return []
    try:
        wurzel = ET.fromstring(xml.encode("utf-8"))
    except ET.ParseError:
        return [xml.strip()[:500]]
    aus = []
    for e in wurzel.iter():
        if e.tag.rsplit("}", 1)[-1] in namen:
            text = " ".join(t.text.strip() for t in e.iter() if t.text and t.text.strip()
                            and t.tag.rsplit("}", 1)[-1] == "Text")
            feld = next((t.text for t in e.iter()
                         if t.tag.rsplit("}", 1)[-1] == "Feldidentifikator"), None)
            aus.append(f"{text} (Feld {feld})" if feld else text)
    return [a for a in aus if a]


def _utc_datum(wert: str | None) -> str | None:
    """Zeitangabe aus den Zertifikateigenschaften -> '2023-08-17'. ERiC liefert
    UTCTime ('230817152116Z', so in der Doku) oder GeneralizedTime
    ('20290725175300Z', so bei einem echten ELSTER-Zertifikat)."""
    m = re.fullmatch(r"(\d\d|\d{4})(\d\d)(\d\d)\d{6}Z", wert or "")
    if not m:
        return wert
    return f"{m[1] if len(m[1]) == 4 else '20' + m[1]}-{m[2]}-{m[3]}"


def _element(xml: str, name: str) -> str | None:
    if not xml.strip():
        return None
    try:
        for e in ET.fromstring(xml.encode("utf-8")).iter():
            if e.tag.rsplit("}", 1)[-1] == name and e.text and e.text.strip():
                return e.text.strip()
    except ET.ParseError:
        pass
    return None


@dataclass
class Ergebnis:
    code: int
    text: str
    antwort: str = ""                 # Rückgabepuffer von ERiC (Prüfergebnis)
    server: str = ""                  # Antwort des Annahmeservers (nur beim Senden)
    pdf: str | None = None
    gesendet: bool = False
    hinweise: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.code in (OK, HINWEISE)

    @property
    def fehler(self) -> list[str]:
        return (_texte(self.antwort, "FehlerRegelpruefung")
                + _texte(self.server, "Fehler", "FehlerRegelpruefung"))

    @property
    def transferticket(self) -> str | None:
        return _element(self.server, "TransferTicket") or _element(self.antwort, "TransferTicket")


# ------------------------------------------------------------ Sitzung

class Eric:
    """Eine ERiC-Sitzung. `lib` lässt sich für Tests durch eine Attrappe ersetzen."""

    def __init__(self, bibliothek: str, protokoll: str, lib=None):
        """`bibliothek` ist das Verzeichnis mit libericapi (im SDK `.../lib`)."""
        self.verzeichnis = os.path.abspath(os.path.expanduser(bibliothek))
        self.protokoll = os.path.abspath(protokoll)
        self._lib = lib
        self._offen = False

    # Plugins aus `bh-plugins/` neben der Bibliothek, falls eingerichtet.
    @property
    def plugin_pfad(self) -> str:
        schlank = os.path.join(os.path.dirname(self.verzeichnis), PLUGIN_VERZEICHNIS)
        return schlank if os.path.isdir(os.path.join(schlank, "plugins")) else self.verzeichnis

    def __enter__(self) -> "Eric":
        if self._lib is None:
            pfad = os.path.join(self.verzeichnis, bibliotheksname())
            if not os.path.exists(pfad):
                raise EricFehler(-1, f"{pfad} nicht gefunden. ERIC_PFAD zeigt auf das "
                                     f"lib-Verzeichnis des ERiC-SDK (siehe docs/eric.md).")
            self._lib = ct.CDLL(pfad)
            _signaturen(self._lib)
        os.makedirs(self.protokoll, exist_ok=True)
        rc = self._lib.EricInitialisiere(self.plugin_pfad.encode(), self.protokoll.encode())
        if rc not in (OK, MEHRFACHE_INITIALISIERUNG):
            raise EricFehler(rc, self.fehlertext(rc))
        self._offen = True
        return self

    def __exit__(self, *_):
        if self._offen:
            self._lib.EricBeende()
            self._offen = False

    # -- Puffer ----------------------------------------------------------
    def _puffer(self):
        return self._lib.EricRueckgabepufferErzeugen()

    def _inhalt(self, puffer) -> str:
        roh = self._lib.EricRueckgabepufferInhalt(puffer) or b""
        self._lib.EricRueckgabepufferFreigeben(puffer)
        return roh.decode("utf-8", errors="replace")

    def fehlertext(self, code: int) -> str:
        p = self._puffer()
        self._lib.EricHoleFehlerText(code, p)
        return self._inhalt(p).strip()

    def version(self) -> str:
        """Produktversion von libericapi, etwa '44.3.6.0'."""
        p = self._puffer()
        rc = self._lib.EricVersion(p)
        xml = self._inhalt(p)
        if rc != OK:
            raise EricFehler(rc, self.fehlertext(rc))
        try:
            for b in ET.fromstring(xml.encode()).iter():
                if b.tag.endswith("Bibliothek"):
                    name = next((c.text for c in b if c.tag.endswith("Name")), "")
                    if name and "ericapi" in name:
                        pv = next(c.text for c in b if c.tag.endswith("Produktversion"))
                        return ".".join(t.strip() for t in pv.split(","))
        except (ET.ParseError, StopIteration):
            pass
        return "unbekannt"

    def steuernummer(self, bescheid: str, bufa: str) -> str:
        """Steuernummer des Bescheids -> 13-stelliges ELSTER-Format, von ERiC
        selbst gebildet und dabei geprüft."""
        p = self._puffer()
        rc = self._lib.EricMakeElsterStnr(bescheid.encode(), None, bufa.encode(), p)
        wert = self._inhalt(p).strip()
        if rc != OK:
            raise EricFehler(rc, self.fehlertext(rc))
        return wert

    def zertifikat(self, pfad: str, pin: str) -> dict:
        """Eigenschaften eines Zertifikats — zugleich die Probe, ob die PIN stimmt."""
        h = self._oeffne(pfad)
        try:
            p = self._puffer()
            rc = self._lib.EricHoleZertifikatEigenschaften(h, pin.encode(), p)
            xml = self._inhalt(p)
            if rc != OK:
                raise EricFehler(rc, self.fehlertext(rc))
            return {"gueltig_bis": _utc_datum(_element(xml, "GueltigBis")),
                    "ausgestellt_am": _utc_datum(_element(xml, "AusgestelltAm"))}
        finally:
            self._lib.EricCloseHandleToCertificate(h)

    def _oeffne(self, pfad: str) -> int:
        h, pins = ct.c_uint32(), ct.c_uint32()
        rc = self._lib.EricGetHandleToCertificate(ct.byref(h), ct.byref(pins),
                                                  os.path.expanduser(pfad).encode())
        if rc != OK:
            raise EricFehler(rc, self.fehlertext(rc))
        return h.value

    # -- der eine Aufruf -------------------------------------------------
    def bearbeite(self, xml: str, datenart_version: str, *, senden: bool = False,
                  pdf: str | None = None, zertifikat: str | None = None,
                  pin: str | None = None) -> Ergebnis:
        # Hinweise (keine Fehler, nur "Bitte prüfen Sie ...") gibt es nur beim
        # Prüfen; zusammen mit SENDE lehnt ERiC die Flag-Kombination ab.
        flags = VALIDIERE | (SENDE if senden else PRUEFE_HINWEISE)
        druck = None
        if pdf:
            flags |= DRUCKE
            druck = DruckParameter(version=4, vorschau=0 if senden else 1, duplexDruck=0,
                                   pdfName=os.path.abspath(pdf).encode(), fussText=None,
                                   pdfCallback=PdfCallback(), pdfCallbackBenutzerdaten=None)
        krypto, handle = None, None
        if senden:
            if not zertifikat or pin is None:
                raise EricFehler(-1, "Zum Senden braucht es Zertifikat und PIN.")
            handle = self._oeffne(zertifikat)
            krypto = VerschluesselungsParameter(version=3, zertifikatHandle=handle,
                                                pin=pin.encode())
        antwort, server = self._puffer(), self._puffer()
        try:
            rc = self._lib.EricBearbeiteVorgang(
                xml.encode("utf-8"), datenart_version.encode(), flags,
                ct.byref(druck) if druck else None, ct.byref(krypto) if krypto else None,
                antwort, server)
        finally:
            if handle is not None:
                self._lib.EricCloseHandleToCertificate(handle)
        e = Ergebnis(code=rc, text=self.fehlertext(rc), antwort=self._inhalt(antwort),
                     server=self._inhalt(server), gesendet=bool(senden and rc == OK))
        e.hinweise = _texte(e.antwort, "Hinweis")
        if pdf and os.path.exists(pdf):
            e.pdf = pdf
        return e


# ------------------------------------------------------------ Einrichtung

def einrichten(bibliothek: str) -> list[str]:
    """`bh-plugins/plugins/` neben dem lib-Verzeichnis anlegen und die
    gebrauchten Plugins hineinverlinken. Gibt die verlinkten Namen zurück."""
    bibliothek = os.path.abspath(os.path.expanduser(bibliothek))
    quelle = os.path.join(bibliothek, "plugins")
    if not os.path.isdir(quelle):
        raise EricFehler(-1, f"{quelle} fehlt — ist ERIC_PFAD das lib-Verzeichnis des SDK?")
    ziel = os.path.join(os.path.dirname(bibliothek), PLUGIN_VERZEICHNIS, "plugins")
    os.makedirs(ziel, exist_ok=True)
    for alt in os.listdir(ziel):
        os.unlink(os.path.join(ziel, alt))
    gewaehlt = sorted(n for n in os.listdir(quelle)
                      if any(re.match(m, n) for m in PLUGINS))
    for n in gewaehlt:
        os.symlink(os.path.join(quelle, n), os.path.join(ziel, n))
    return gewaehlt


def mindestversion(heute: datetime.date | None = None) -> tuple[int, int]:
    """(gültige Mindestversion, die ab dem nächsten April) — als Faustregel.

    ELSTER hebt die Mindestversion jedes Jahr Ende April auf das
    November-Release des Vorjahres an; ältere Releases übermitteln dann
    nichts mehr. November-Releases tragen ungerade Nummern (43 = 11/2025),
    Mai-Releases gerade. Verbindlich ist allein die Mindestversionsliste im
    Entwicklerbereich — diese Rechnung soll nur rechtzeitig warnen.
    """
    heute = heute or datetime.date.today()
    jahr = heute.year if (heute.month, heute.day) >= (4, 27) else heute.year - 1
    jetzt = 2 * (jahr - 1) - 4007
    return jetzt, jetzt + 2


# Pflichttexte nach § 5 und § 15 der ERiC-Lizenzvereinbarung. Der Wortlaut des
# Datenschutzhinweises ist dort vorgegeben; das Informationsschreiben nach
# Art. 12-14 DSGVO steht als Anhang 2 in `lizenz.pdf` des SDK.
DATENSCHUTZHINWEIS = (
    "Datenschutzhinweis durch die Finanzverwaltung:\n"
    "„Mit dieser Software werden personenbezogene Daten im Sinne des Art. 4 Nr. 1 "
    "Datenschutzgrundverordnung (DSGVO) und Art. 9 Abs. 1 DSGVO zum Zwecke der "
    "Verarbeitung erhoben. Neben den reinen Daten, die zur Steuerveranlagung benötigt "
    "werden, erhebt die Software Daten über die Art des Betriebssystems des Nutzers und "
    "übermittelt diese an die Finanzverwaltung. Diese Daten werden benötigt, um die "
    "ordnungsgemäße Verarbeitung der Daten sicherzustellen und Fehlern im "
    "Verarbeitungsprozess vorzubeugen. Die Nutzung der Daten erfolgt im Rahmen des "
    "Art. 6 Abs. 1 UAbs. 1 Buchst. e i.V.m. Abs. 3 UAbs. 1 Buchst. b DSGVO i.V.m. "
    "bundes- bzw. landesgesetzlicher Steuergesetze durch die Finanzverwaltung und nur "
    "für den genannten Zweck.“")
PROTOKOLLHINWEIS = (
    "ERiC schreibt Protokolldateien und legt sie lokal ab — bei `bh` unter "
    "mandanten/<kürzel>/elster/protokoll/. Sie enthalten die übermittelten Daten, "
    "verbleiben auf diesem Rechner und werden nur im Supportfall und nur mit "
    "ausdrücklicher Erlaubnis weitergegeben.")


def hinweise_bestaetigt(bibliothek: str) -> str | None:
    pfad = os.path.join(os.path.dirname(os.path.abspath(os.path.expanduser(bibliothek))),
                        PLUGIN_VERZEICHNIS, "hinweise-bestaetigt")
    if os.path.exists(pfad):
        with open(pfad, encoding="utf-8") as fh:
            return fh.read().strip() or "ja"
    return None


def hinweise_bestaetigen(bibliothek: str) -> None:
    pfad = os.path.join(os.path.dirname(os.path.abspath(os.path.expanduser(bibliothek))),
                        PLUGIN_VERZEICHNIS, "hinweise-bestaetigt")
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as fh:
        fh.write(datetime.datetime.now().isoformat(timespec="seconds") + "\n")
