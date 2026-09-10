# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Auswertungszeitraeume.

Eine Auswertung ohne Zeitraum ist wertlos: "2026" kann der Stand vom 15. Juli
sein oder das fertige Jahr. Dieses Modul loest einen kurzen Code gegen das
Geschaeftsjahr auf und liefert immer beides - die **nominellen** Grenzen des
Zeitraums, mit denen gefiltert wird, und den **Stand**, bis zu dem tatsaechlich
gebucht ist. Angezeigt wird der Stand, gerechnet wird mit den Grenzen.

Codes:

    jahr            das ganze Geschaeftsjahr (Vorgabe)
    h1 h2           Halbjahre
    q1 q2 q3 q4     Quartale
    m01 ... m12     einzelne Monate

Vorjahresvergleich: `verschoben()` legt denselben Ausschnitt auf das Vorjahr,
damit Januar gegen Januar steht und nicht gegen ein volles Jahr.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass

MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni",
          "Juli", "August", "September", "Oktober", "November", "Dezember"]

RE_MONAT = re.compile(r"^m(0[1-9]|1[0-2])$")
RE_QUARTAL = re.compile(r"^q([1-4])$")
RE_HALBJAHR = re.compile(r"^h([12])$")


def _tag(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


@dataclass(frozen=True)
class Zeitraum:
    code: str
    von: dt.date            # Untergrenze fuer das Buchungsdatum
    bis: dt.date            # Obergrenze fuer das Buchungsdatum
    stand: dt.date          # bis hierhin ist gebucht (<= bis)
    kurz: str               # "Januar 2026", "Geschäftsjahr 2025"
    vollstaendig: bool      # ist der Zeitraum abgelaufen und bebucht?

    @property
    def label(self) -> str:
        """Was in der Oberflaeche steht: 01.01.2026 – 15.07.2026."""
        return f"{_tag(self.von)} – {_tag(self.stand)}"

    @property
    def stichtag(self) -> str:
        """Fuer Bestandsrechnungen: zum 15.07.2026."""
        return _tag(self.stand)

    def als_dict(self) -> dict:
        return {"code": self.code, "von": self.von.isoformat(), "bis": self.bis.isoformat(),
                "stand": self.stand.isoformat(), "label": self.label,
                "stichtag": self.stichtag, "kurz": self.kurz,
                "vollstaendig": self.vollstaendig}


def grenzen(code: str, jahr: int) -> tuple[dt.date, dt.date]:
    """Nominelle Grenzen eines Codes im Kalenderjahr, noch ohne Geschaeftsjahr."""
    code = (code or "jahr").lower()
    if m := RE_MONAT.match(code):
        monat = int(m.group(1))
        return dt.date(jahr, monat, 1), dt.date(jahr, monat, calendar.monthrange(jahr, monat)[1])
    if m := RE_QUARTAL.match(code):
        q = int(m.group(1))
        return dt.date(jahr, 3 * q - 2, 1), dt.date(jahr, 3 * q, calendar.monthrange(jahr, 3 * q)[1])
    if m := RE_HALBJAHR.match(code):
        h = int(m.group(1))
        return (dt.date(jahr, 1, 1), dt.date(jahr, 6, 30)) if h == 1 else \
               (dt.date(jahr, 7, 1), dt.date(jahr, 12, 31))
    return dt.date(jahr, 1, 1), dt.date(jahr, 12, 31)


def _kurz(code: str, jahr: int) -> str:
    code = (code or "jahr").lower()
    if m := RE_MONAT.match(code):
        return f"{MONATE[int(m.group(1)) - 1]} {jahr}"
    if m := RE_QUARTAL.match(code):
        return f"{m.group(1)}. Quartal {jahr}"
    if m := RE_HALBJAHR.match(code):
        return f"{m.group(1)}. Halbjahr {jahr}"
    return f"Geschäftsjahr {jahr}"


def aufloesen(code: str, gj_beginn: str, gj_ende: str,
              letzte_buchung: str | None) -> Zeitraum:
    """Code + Geschaeftsjahr + letztes Buchungsdatum -> Zeitraum.

    Die Grenzen werden am Geschaeftsjahr abgeschnitten (ein Rumpfjahr faengt
    nicht am 1. Januar an). Der Stand ist die letzte Buchung innerhalb der
    Grenzen - deshalb steht in der Kopfzeile "bis 15.07.2026" und nicht
    "bis 31.12.2026", solange das Jahr laeuft.
    """
    beginn = dt.date.fromisoformat(gj_beginn)
    ende = dt.date.fromisoformat(gj_ende)
    von, bis = grenzen(code, beginn.year)
    von, bis = max(von, beginn), min(bis, ende)

    stand = bis
    if letzte_buchung:
        letzte = dt.date.fromisoformat(letzte_buchung)
        if letzte < bis:
            stand = max(letzte, von)          # nie vor den Anfang zurueckfallen
    return Zeitraum(code=(code or "jahr").lower(), von=von, bis=bis, stand=stand,
                    kurz=_kurz(code, beginn.year), vollstaendig=stand >= bis)


def verschoben(z: Zeitraum, jahre: int = -1) -> tuple[str, str]:
    """Derselbe Ausschnitt im Vor- oder Folgejahr, als ISO-Grenzen.

    Der 29. Februar wird auf den 28. gezogen, damit kein ungueltiges Datum
    entsteht.
    """
    def schieben(d: dt.date) -> dt.date:
        j = d.year + jahre
        tag = min(d.day, calendar.monthrange(j, d.month)[1])
        return dt.date(j, d.month, tag)

    return schieben(z.von).isoformat(), schieben(z.bis).isoformat()


def auswahl(gj_beginn: str, gj_ende: str) -> list[dict]:
    """Die Liste fuer den Umschalter - nur Zeitraeume, die im Jahr liegen."""
    beginn = dt.date.fromisoformat(gj_beginn)
    ende = dt.date.fromisoformat(gj_ende)
    aus = [{"code": "jahr", "text": "ganzes Geschäftsjahr"}]
    for gruppe in (["h1", "h2"], ["q1", "q2", "q3", "q4"],
                   [f"m{m:02d}" for m in range(1, 13)]):
        for code in gruppe:
            von, bis = grenzen(code, beginn.year)
            if bis >= beginn and von <= ende:
                aus.append({"code": code, "text": _kurz(code, beginn.year)})
    return aus
