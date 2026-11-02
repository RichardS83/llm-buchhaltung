# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Die Reihenfolge der Elemente nach dem XML-Schema einer Datenart.

ERiC prüft den Datensatz gegen das Schema, und das Schema schreibt in jeder
Ebene eine feste Folge vor (`xs:sequence`). Wer Felder aus einer Zuordnung
einsetzt — der Ausfüllhilfe statt fest verdrahteter Zeilen —, kann sie nicht in
Schemaordnung erzeugen, ohne die Ordnung abzuschreiben. Sie wird deshalb nach
dem Bau aus dem Schema hergestellt, das mit der SDK-Dokumentation kommt.

Die Schemata der Ertragsteuererklärungen bestehen nur aus benannten Typen mit
je einer Sequenz; ein Element ohne Typ oder eine Auswahl (`xs:choice`) hält
an, statt still falsch zu sortieren.
"""

from __future__ import annotations

import functools
import os
import xml.etree.ElementTree as ET

from .eric_xml import DatenFehlen

XS = "{http://www.w3.org/2001/XMLSchema}"

# Wo das Schema je Art liegt (relativ zu Dokumentation/Datenarten)
QUELLEN = {"kst": "ElsterErklaerung/KSt/Schema/{jahr}/E30-{jahr}.xsd",
           "gewst": "ElsterErklaerung/GewSt/Schema/{jahr}/E20-{jahr}.xsd",
           "est": "ElsterErklaerung/ESt/Schema/{jahr}/E10-{jahr}.xsd"}


def pfad(doku: str | None, art: str, jahr: int) -> str | None:
    if not doku or art not in QUELLEN:
        return None
    p = os.path.join(doku, QUELLEN[art].format(jahr=jahr))
    return p if os.path.exists(p) else None


def _name(e: ET.Element) -> str:
    return e.tag.rsplit("}", 1)[-1]


@functools.lru_cache(maxsize=8)
def _typen(xsd: str, mtime: float) -> tuple[dict, dict]:
    """({Typname: [(Kind, Typ), ...]}, {Wurzelelement: Typ})."""
    root = ET.parse(xsd).getroot()
    if root.find(f".//{XS}choice") is not None or root.find(f".//{XS}all") is not None:
        raise DatenFehlen(f"{os.path.basename(xsd)}: xs:choice/xs:all ist nicht vorgesehen.")
    typen = {}
    for ct in root.findall(f"{XS}complexType"):
        seq = ct.find(f"{XS}sequence")
        if seq is None:
            continue
        kinder = []
        for e in seq.findall(f"{XS}element"):
            if not e.get("type"):
                raise DatenFehlen(f"{os.path.basename(xsd)}: Element {e.get('name')} ohne Typ.")
            kinder.append((e.get("name"), e.get("type")))
        typen[ct.get("name")] = kinder
    wurzeln = {e.get("name"): e.get("type") for e in root.findall(f"{XS}element")}
    return typen, wurzeln


def sortieren(element: ET.Element, xsd: str) -> None:
    """Ordnet `element` (ein Wurzelelement des Schemas, etwa E30) und alle
    Nachfahren in Schemaordnung. Gleichnamige Geschwister (Einzel, Einzel)
    behalten ihre Folge. Ein Element, das das Schema an der Stelle nicht kennt,
    hält an."""
    typen, wurzeln = _typen(xsd, os.path.getmtime(xsd))
    typ = wurzeln.get(_name(element))
    if not typ:
        raise DatenFehlen(f"{_name(element)} ist kein Wurzelelement von "
                          f"{os.path.basename(xsd)}.")
    _sortieren(element, typ, typen, _name(element))


def _sortieren(element: ET.Element, typ: str, typen: dict, pfad_: str) -> None:
    kinder = typen.get(typ)
    if kinder is None:
        if len(element):
            raise DatenFehlen(f"{pfad_}: das Schema sieht hier keine Unterelemente vor.")
        return
    stelle = {n: (i, t) for i, (n, t) in enumerate(kinder)}
    for k in element:
        if _name(k) not in stelle:
            raise DatenFehlen(f"{pfad_}/{_name(k)} ist im Schema nicht vorgesehen.")
    element[:] = sorted(element, key=lambda k: stelle[_name(k)][0])
    for k in element:
        _sortieren(k, stelle[_name(k)][1], typen, f"{pfad_}/{_name(k)}")
