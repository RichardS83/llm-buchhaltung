# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Körperschaftsteuer-, Gewerbesteuer- und Einkommensteuererklärung als
ERiC-Datensatz.

Die Einkommensteuer (Datenart `ESt`, Unterfallart 10, Schema E10) kommt ganz
aus der Ausfüllhilfe, siehe `est()`. Für KSt und GewSt gilt:

Datenarten `KSt` (Unterfallart 30, Schema E30) und `GewSt` (Unterfallart 20,
Schema E20) des Verfahrens ElsterErklaerung. Vorgesehen ist der einfache Fall
einer Kapitalgesellschaft, die nur Einkünfte aus Gewerbebetrieb hat:

    KSt 1, Anlagen GK, ZVE, Verluste, WA und KSt 1 F
    GewSt 1 A

Woher die Werte kommen:

* die Beträge aus `steuern.json` des Jahres — der Überleitungsrechnung, die
  `steuern.pruefen()` gegen die Konten hält. Gesperrt (Platzhalter oder
  Abweichung) heißt: kein Datensatz. Gebraucht werden die Zeilen mit diesen
  `kennzahl`-Marken: `zve`, `einkuenfte_gewerbe`, `verlustvortrag`,
  `gewinn_gewerbe` und `gewerbeverlust`; der Jahresüberschuss ist die Zeile
  mit `quelle: guv`;
* Firma, Sitz, Rechtsform und Anteilseigner aus `mandant.json`;
* was die Buchführung nicht weiß, aus `<jahr>/ertragsteuer.json`: Gegenstand
  des Unternehmens, die Betriebsstätte, die Fragen der Anlage WA und die
  Anfangsbestände der Anlage KSt 1 F.

Was der einfache Fall nicht abdeckt — Überleitungsposten zwischen
Jahresergebnis und Einkünften, Hinzurechnungen, ein Verlustvortrag aus dem
Vorjahr — hält mit `DatenFehlen` an, statt eine unvollständige Erklärung zu
bauen. Es sei denn, die Ausfüllhilfe (`elster.json`) trägt die Zeilen: dann
kommen alle Beträge der Anlagen aus ihr. Jedes Feld mit `xml` wird mit
Kontext und Format aus der Jahresdokumentation des SDK eingesetzt, der
Datensatz danach nach dem Schema geordnet. Die Ausfüllhilfe muss dafür
eingabefertig sein und ihre Gegenproben gegen die Überleitung bestehen — was
übermittelt wird, ist dann genau das, was sie zeigt.

Beide Erklärungen kennen keine Kennzahl „berichtigte Erklärung“: eine zweite
Übermittlung vor dem Bescheid ersetzt die erste. Dass es eine Berichtigung ist,
sagt der Datensatz in den „Ergänzenden Angaben zur Steuererklärung“ (KSt 1
Zeile 13, GewSt 1 A Zeile 21) — aus `ergaenzende_angaben` in ertragsteuer.json.
Der Grund 3 („Sachverhalte sollen personell vertieft geprüft werden“) sorgt
dafür, dass ein Mensch im Finanzamt den Text liest, statt dass die Erklärung
maschinell durchläuft.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import xml.etree.ElementTree as ET

from . import eric_klartext, eric_schema
from .eric_xml import NS, DatenFehlen, _hersteller_id, _pflicht, _rahmen, _sub, _text

ANGABEN = "ertragsteuer.json"
COPYRIGHT = "llm-buchhaltung"

# Rechtsformschlüssel, in beiden Erklärungen gleich (RECHTSFORM_KOERPERSCHAFTEN_V3,
# RECHTSFORM_GEWERBESTEUER_V2)
RECHTSFORM = {"Gesellschaft mit beschränkter Haftung": "350",
              "Unternehmergesellschaft (haftungsbeschränkt)": "370"}

# Die Felder, die einen Betrag tragen — für den Abgleich mit der Buchführung
BETRAEGE = {
    "kst": ("E8002001", "E8100301", "E8100501", "E8100601", "E8100702", "E8101001",
            "E8101101", "E8561212", "E8561410", "E8561301", "E8561411", "E8562101",
            "E8109901", "E8004904", "E8005401", "E8006901", "E8007301", "E8007501",
            "E8007504", "E8680303", "E8680404", "E8120401", "E8120601",
            "E8008001", "E8008102", "E8009201", "E8201001", "E8201101", "E8201201", "E8201301",
            "E8240102", "E8240103", "E8245601"),
    "gewst": ("E4001703", "E4003202", "E4003703"),
    # Einkommensteuer: die Summenzeilen der Vordrucke (mehrfach: je Ausfertigung)
    "est": ("E0104801", "E0200201", "E0200301", "E0200401", "E0201401", "E0204812", "E0204803",
            "E0803202", "E0800502", "E1900701", "E0701401", "E0705701", "E0701601",
            "E2000401", "E2001203", "E2001505", "E2003104", "E2003202", "E0506105",
            "E0161304", "E0161305", "E0104109", "E0107208", "E0111215"),
}

# Die Vordrucke je Erklärung, wie sie im Schema heißen (Formular-`vordruck`
# in elster.json)
VORDRUCKE = {"kst": ("KSt1", "GK", "ZVE", "AESt", "WA", "Verluste", "KSt1F"),
             "gewst": ("GewSt1A",),
             # Einkommensteuer: Hauptvordruck, Anlagen und der Vorsatz (IdNr der
             # Personen A und B), alle allein aus der Ausfüllhilfe
             "est": ("ESt1A", "ESt1A_U", "AgB", "AUS", "AV", "Corona", "EM_35c", "FW", "G",
                     "HA_35a", "KAP", "KAP_BET", "KAP_I", "Kind", "L", "Mob", "N", "N_AUS",
                     "N_DHH", "N_GRE", "R", "RAV_bAV", "R_AUS", "S", "SA", "SO", "Sonst",
                     "Anl_32c", "Anl_34b", "V", "V_FeWo", "V_Sonstige", "VOR", "WA_ESt",
                     "Zins", "Vorsatz")}


def angaben_laden(jahr_dir: str) -> dict:
    pfad = os.path.join(jahr_dir, ANGABEN)
    if not os.path.exists(pfad):
        raise DatenFehlen(f"{os.path.basename(jahr_dir)}/{ANGABEN} fehlt — die Angaben, die "
                          "nicht aus der Buchführung kommen (docs/eric.md).")
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)


def _vorhanden(d: dict, wo: str, *felder: str) -> None:
    """Wie `_pflicht`, aber 0 ist eine Angabe (Einlagekonto, Bestände)."""
    fehlt = [f for f in felder if d.get(f) is None or d.get(f) == ""]
    if fehlt:
        raise DatenFehlen(f"{wo}: es fehlt {', '.join(fehlt)}")


def euro(cent: int) -> int:
    """Volle Euro, abgerundet: ein Gewinn wird kleiner, ein Verlust größer —
    wie die Formulare in Mein ELSTER (−1.234,56 → −1.235)."""
    return cent // 100


def _ergaenzend(angaben: dict, art: str) -> tuple[str, str] | None:
    """(Grund, Text) der Ergänzenden Angaben, oder None."""
    e = angaben.get("ergaenzende_angaben") or {}
    text = (e.get(art) or e.get("text") or "").strip()
    if not text:
        return None
    grund = str(e.get("grund", "3"))
    if grund not in ("1", "2", "3", "4"):
        raise DatenFehlen(f"{ANGABEN}: ergaenzende_angaben.grund muss 1 bis 4 sein.")
    if len(text) > 999:
        raise DatenFehlen(f"{ANGABEN}: Ergänzende Angaben ({art}) haben {len(text)} Zeichen, "
                          "erlaubt sind 999.")
    return grund, text


def _datum(iso: str) -> str:
    return datetime.date.fromisoformat(str(iso)[:10]).strftime("%d.%m.%Y")


def _jn(wert: bool) -> str:
    return "1" if wert else "2"


def _kennzahl(spec: dict, name: str) -> int:
    for a in spec.get("abschnitte", []):
        for z in a["zeilen"]:
            if z.get("kennzahl") == name:
                return z["betrag"]
    raise DatenFehlen(f"steuern.json: keine Zeile mit kennzahl {name!r}")


def _guv(spec: dict) -> int:
    for a in spec.get("abschnitte", []):
        for z in a["zeilen"]:
            if z.get("quelle") == "guv":
                return z["betrag"]
    raise DatenFehlen("steuern.json: keine Zeile mit quelle 'guv' (Jahresergebnis)")


def zuordnung(ausfuellhilfe: dict | None, art: str) -> list[dict]:
    """Die Felder der ausgewerteten Ausfüllhilfe (`elster.auswerten`), die einen
    XML-Namen tragen: [{vordruck, formular_einzel, name, einzel, kontext, feld}].
    Leer heißt einfacher Fall.

    `formular_einzel` ist das `einzel` des Formulars: ein Vordruck, der mehrfach
    abgegeben wird (mehrere Anlagen V, eine Anlage N je Person), bekommt je
    Schlüssel ein eigenes Element. `kontext` wählt unter mehreren Abschnitten
    desselben Vordrucks, die ein gleichnamiges Feld tragen (`Person` in
    `VOR/AVor` und `VOR/Beitr_g_KV_PV_Inl`)."""
    aus = []
    for f in (ausfuellhilfe or {}).get("formulare", []):
        if f.get("vordruck") not in VORDRUCKE[art]:
            continue
        for feld in f.get("felder", []):
            if feld.get("xml"):
                aus.append({"vordruck": f["vordruck"], "formular_einzel": f.get("einzel"),
                            "name": feld["xml"], "einzel": feld.get("einzel"),
                            "kontext": feld.get("kontext"), "feld": feld})
    if aus:
        offen = [f"{x['vordruck']} Zeile {x['feld'].get('zeile')}" for x in aus
                 if x["feld"].get("fehlt") or x["feld"].get("offen_punkt")]
        if offen:
            raise DatenFehlen("Die Ausfüllhilfe ist nicht eingabefertig: " + ", ".join(offen))
        falsch = [p["text"] for p in ausfuellhilfe.get("gegenproben", []) if not p.get("ok")]
        if falsch:
            raise DatenFehlen("Gegenprobe der Ausfüllhilfe verfehlt: " + "; ".join(falsch))
    return aus


def _felddoku(art: str, jahr: int, doku: str | None) -> dict:
    """{(Vordruck, Feldname): [(Kontext ohne Vordruck, Feldbeschreibung)]}."""
    return _doku(art, jahr, doku)[0]


def _doku(art: str, jahr: int, doku: str | None) -> tuple[dict, frozenset]:
    """Der Feldindex wie `_felddoku` und die Kontexte, die sich laut
    Jahresdokumentation wiederholen dürfen (`V`, `V/Wk/Schuldzins/Direkt`)."""
    d = eric_klartext._laden(art, jahr, None, doku)
    if not d:
        raise DatenFehlen("Die Jahresdokumentation des ERiC-SDK fehlt — ohne sie lassen sich "
                          "die Felder der Ausfüllhilfe nicht zuordnen (ERIC_DOKU).")
    if str(jahr) not in d["quelle"]:
        raise DatenFehlen(f"Für {jahr} liegt keine Jahresdokumentation vor ({d['quelle']}).")
    index: dict = {}
    for (kontext, name), info in d["felder"].items():
        vordruck, _, rest = kontext.partition("/")
        index.setdefault((vordruck, name), []).append((rest, info))
    return index, frozenset(d.get("wiederholt") or ())


def _feldtext(info: dict, feld: dict, wo: str) -> str:
    """Der Wert eines Ausfüllhilfe-Feldes im Format, das das Schema verlangt."""
    fmt = info.get("format", "")
    if fmt.startswith(("GeldBetragOhneCent", "GeldBetragMitCent")):
        if feld.get("betrag_cent") is None:
            raise DatenFehlen(f"{wo}: kein Betrag ({feld.get('eintrag')!r}).")
        if fmt.startswith("GeldBetragOhneCent"):
            betrag, text = feld["euro"], str(feld["euro"])
        else:
            betrag = feld["betrag_cent"]
            text = f"{'-' if betrag < 0 else ''}{abs(betrag) // 100},{abs(betrag) % 100:02d}"
        if betrag < 0 and "ohne Vorzeichen" in fmt:
            raise DatenFehlen(f"{wo}: das Feld erlaubt kein Minus ({text}).")
        return text
    text = str(feld.get("eintrag") or "").strip()
    if not text:
        raise DatenFehlen(f"{wo}: leer.")
    return text


def _einsetzen(wurzel: ET.Element, ns: str, eintraege: list[dict], index: dict,
               wiederholt: frozenset = frozenset()) -> None:
    """Setzt die Felder der Ausfüllhilfe unter ihrem Kontext ein.

    Wiederholt wird, was die Jahresdokumentation wiederholbar nennt
    (`wiederholt`), und jede Ebene namens Einzel…:

    * der Vordruck selbst je `formular_einzel` (ohne: ein gemeinsames Element);
    * die Ebenen darunter je `einzel` des Feldes — ein Text gilt der tiefsten
      Ebene namens Einzel…, ohne eine solche der tiefsten wiederholten Ebene;
      eine Liste gilt den tiefsten wiederholten Ebenen von außen nach innen
      (`["a", "elterngeld"]`: Person a, darin der Eintrag elterngeld). Eine
      Ebene ohne Schlüssel ist ein gemeinsames Element; nur die tiefste Ebene
      namens Einzel… verlangt einen Schlüssel."""
    gruppen: dict = {}

    def kind(eltern, tag):
        k = eltern.find(f"{{{ns}}}{tag}")
        return k if k is not None else _sub(eltern, f"{{{ns}}}{tag}")

    def gruppe(eltern, tag, schluessel):
        if schluessel is None:
            return kind(eltern, tag)
        s = (id(eltern), tag, schluessel)
        if s not in gruppen:
            gruppen[s] = _sub(eltern, f"{{{ns}}}{tag}")
        return gruppen[s]

    for x in eintraege:
        wo = f"{x['vordruck']} Zeile {x['feld'].get('zeile')} ({x['name']})"
        stellen = index.get((x["vordruck"], x["name"]))
        if not stellen:
            raise DatenFehlen(f"{wo}: das Feld steht nicht in der Jahresdokumentation.")
        if x.get("kontext") is not None:
            k = x["kontext"].strip("/")
            k = k[len(x["vordruck"]):].lstrip("/") if (k + "/").startswith(
                x["vordruck"] + "/") else k
            stellen = [s for s in stellen if s[0] == k]
            if not stellen:
                raise DatenFehlen(f"{wo}: das Feld steht nicht im Abschnitt {x['kontext']!r}.")
        if len(stellen) > 1:
            raise DatenFehlen(f"{wo}: das Feld steht in mehreren Abschnitten "
                              f"({', '.join(k for k, _ in stellen)}) — `kontext` wählt.")
        kontext, info = stellen[0]
        teile = [t for t in kontext.split("/") if t]
        wdh = [i for i, t in enumerate(teile)
               if t.startswith("Einzel") or "/".join([x["vordruck"], *teile[:i + 1]]) in wiederholt]
        tiefstes = max((i for i in wdh if teile[i].startswith("Einzel")), default=None)
        einzel = x.get("einzel")
        if isinstance(einzel, list):
            einzel = [str(e) for e in einzel]
            if len(einzel) > len(wdh):
                raise DatenFehlen(f"{wo}: `einzel` hat {len(einzel)} Schlüssel, der Abschnitt "
                                  f"{kontext or x['vordruck']} nur {len(wdh)} wiederholte "
                                  "Ebenen.")
            schluessel = dict(zip(wdh[len(wdh) - len(einzel):], einzel))
        elif einzel not in (None, ""):
            if not wdh:
                raise DatenFehlen(f"{wo}: `einzel`, aber der Abschnitt "
                                  f"{kontext or x['vordruck']} wiederholt sich nicht.")
            schluessel = {tiefstes if tiefstes is not None else wdh[-1]: str(einzel)}
        else:
            schluessel = {}
        if tiefstes is not None and tiefstes not in schluessel:
            raise DatenFehlen(f"{wo}: das Feld steht in einer wiederholten Gruppe ({kontext}) "
                              "und braucht `einzel`.")
        knoten = gruppe(wurzel, x["vordruck"], x.get("formular_einzel"))
        for i, t in enumerate(teile):
            knoten = gruppe(knoten, t, schluessel.get(i))
        text = _feldtext(info, x["feld"], wo)
        blatt = knoten.find(f"{{{ns}}}{x['name']}")
        if blatt is not None:
            if (blatt.text or "") != text:
                raise DatenFehlen(f"{wo}: steht schon mit {blatt.text!r} im Datensatz, die "
                                  f"Ausfüllhilfe sagt {text!r}.")
            continue
        _sub(knoten, f"{{{ns}}}{x['name']}", text)


def _ordnen(wurzel: ET.Element, art: str, jahr: int, doku: str | None) -> None:
    xsd = eric_schema.pfad(doku, art, jahr)
    if not xsd:
        raise DatenFehlen(f"Das Schema {eric_schema.QUELLEN[art].format(jahr=jahr)} fehlt in "
                          "der SDK-Dokumentation — ohne es lässt sich der Datensatz nicht "
                          "ordnen.")
    eric_schema.sortieren(wurzel, xsd)


def _zahl(eintraege: list[dict], name: str) -> int | None:
    for x in eintraege:
        if x["name"] == name:
            return x["feld"]["euro"]
    return None


def werte(spec: dict) -> dict:
    """Die Beträge beider Erklärungen in vollen Euro, aus der geprüften
    Überleitung. Hält an, wo der einfache Fall verlassen wird."""
    if spec.get("gesperrt"):
        raise DatenFehlen("Die Überleitung in steuern.json ist gesperrt (Platzhalter oder "
                          "Abweichung von den Konten) — erst `bh abgabe` freibekommen.")
    ergebnis = _guv(spec)
    einkuenfte = _kennzahl(spec, "einkuenfte_gewerbe")
    zve = _kennzahl(spec, "zve")
    gewinn = _kennzahl(spec, "gewinn_gewerbe")
    if not ergebnis == einkuenfte == zve == gewinn:
        raise DatenFehlen("Jahresergebnis, Einkünfte, Einkommen und Gewinn aus Gewerbebetrieb "
                          "weichen voneinander ab — Überleitungsposten sind im Datensatz noch "
                          "nicht vorgesehen.")
    verlustvortrag = _kennzahl(spec, "verlustvortrag")
    gewerbeverlust = _kennzahl(spec, "gewerbeverlust")
    verlust = -min(zve, 0)
    if abs(verlustvortrag) != verlust or abs(gewerbeverlust) != verlust:
        raise DatenFehlen("Der Verlustvortrag ist nicht der Verlust des Jahres — ein Vortrag "
                          "aus dem Vorjahr ist im Datensatz noch nicht vorgesehen.")
    return {"ergebnis": euro(ergebnis), "zve": euro(zve),
            "verlust": -euro(zve) if zve < 0 else 0}


def _vorsatz(eltern: ET.Element, ns: str, unterfallart: str, *, stamm: dict,
             steuernummer: str, jahr: int, bescheid: bool) -> None:
    v = _sub(eltern, f"{{{ns}}}Vorsatz")
    _sub(v, f"{{{ns}}}Unterfallart", unterfallart)
    _sub(v, f"{{{ns}}}Vorgang", "01")
    _sub(v, f"{{{ns}}}StNr", steuernummer)
    _sub(v, f"{{{ns}}}Zeitraum", str(jahr))
    _sub(v, f"{{{ns}}}AbsName", stamm["name"][:45])
    _sub(v, f"{{{ns}}}AbsStr", stamm["strasse"][:30])
    _sub(v, f"{{{ns}}}AbsPlz", stamm["plz"])
    _sub(v, f"{{{ns}}}AbsOrt", stamm["ort"][:29])
    _sub(v, f"{{{ns}}}Copyright", COPYRIGHT)
    _sub(v, f"{{{ns}}}OrdNrArt", "S")
    r = _sub(v, f"{{{ns}}}Rueckuebermittlung")
    _sub(r, f"{{{ns}}}Bescheid", _jn(bescheid))
    if bescheid:
        # Abholung der Bescheiddaten über ERiC; den Schlüssel dazu legt `bh` noch nicht an
        _sub(r, f"{{{ns}}}ArtRueckuebermittlung", "INTERNET")


def _nutzdaten(datenart: str, *, bufa: str, hersteller_id: str, produkt: str,
               produktversion: str, datenlieferant: str, testmerker: str | None):
    wurzel, block = _rahmen("ElsterErklaerung", datenart, hersteller_id, datenlieferant,
                            testmerker)
    ndh = _sub(block, f"{{{NS}}}NutzdatenHeader", version="11")
    _sub(ndh, f"{{{NS}}}NutzdatenTicket", "1")
    _sub(ndh, f"{{{NS}}}Empfaenger", bufa, id="F")
    hersteller = _sub(ndh, f"{{{NS}}}Hersteller")
    _sub(hersteller, f"{{{NS}}}ProduktName", produkt)
    _sub(hersteller, f"{{{NS}}}ProduktVersion", produktversion)
    return wurzel, _sub(block, f"{{{NS}}}Nutzdaten")


def _pruefe_kopf(stamm: dict, steuernummer: str, bufa: str) -> str:
    _pflicht(stamm, "name", "strasse", "plz", "ort", "rechtsform")
    if not re.fullmatch(r"\d{13}", steuernummer or ""):
        raise DatenFehlen("Steuernummer im ELSTER-Format hat 13 Ziffern.")
    if not re.fullmatch(r"\d{4}", bufa or ""):
        raise DatenFehlen("Bundesfinanzamtsnummer hat vier Ziffern (mandant.json, elster.bufa).")
    rf = RECHTSFORM.get(stamm["rechtsform"])
    if not rf:
        raise DatenFehlen(f"Rechtsform {stamm['rechtsform']!r} ist noch nicht vorgesehen.")
    return rf


def _widnr(stamm: dict) -> str | None:
    if not stamm.get("widnr"):
        return None
    w = stamm["widnr"].replace(" ", "").split("-")[0]
    if not re.fullmatch(r"DE\d{9}", w):
        raise DatenFehlen("Die W-IdNr. ist DE und neun Ziffern (mandant.json, widnr).")
    return w


def kst(spec: dict, angaben: dict, *, stamm: dict, gj: dict, jahr: int, steuernummer: str,
        bufa: str, elster_stnr, hersteller_id: str, produkt: str, produktversion: str,
        testmerker: str | None = None, ausfuellhilfe: dict | None = None,
        doku: str | None = None) -> str:
    """Datensatz der Datenart KSt_<jahr>. `steuernummer` im ELSTER-Format,
    `gj` mit `beginn` und `ende` des (einzigen) Wirtschaftsjahres.
    `elster_stnr(steuernummer, bufa)` bringt die Steuernummern der
    Anteilseigner ins ELSTER-Format (in der Regel `Eric.steuernummer`).
    `ausfuellhilfe` ist das Ergebnis von `elster.auswerten`; trägt sie
    XML-Namen, stammen alle Beträge der Anlagen aus ihr."""
    rf = _pruefe_kopf(stamm, steuernummer, bufa)
    eintraege = zuordnung(ausfuellhilfe, "kst")
    if eintraege:
        if spec.get("gesperrt"):
            raise DatenFehlen("Die Überleitung in steuern.json ist gesperrt — erst `bh abgabe` "
                              "freibekommen.")
        doku = doku or eric_klartext.dokumentation()
        index, wiederholt = _doku("kst", jahr, doku)
        zve_aus = _zahl(eintraege, "E8007501")
        if zve_aus is None:
            raise DatenFehlen("Die Ausfüllhilfe trägt kein zu versteuerndes Einkommen "
                              "(ZVE Zeile 70, E8007501).")
        w = {"verlust": -zve_aus if zve_aus < 0 else 0}
    else:
        w = werte(spec)
    ae = stamm.get("anteilseigner") or []
    if not ae:
        raise DatenFehlen("mandant.json: anteilseigner fehlen (KSt 1, Zeilen 21 ff.).")
    for a in ae:
        _vorhanden(a, "mandant.json, anteilseigner", "name", "steuernummer", "bufa", "strasse",
                   "plz", "ort", "nennbetrag", "prozent", "seit")
    f = angaben.get("kst1f") or {}
    _vorhanden(f, f"{ANGABEN}, kst1f", "eigenkapital", "nennkapital", "einlagekonto",
               "einlagekonto_anfang", "einlagekonto_ende")
    if angaben.get("vertraege_mit_anteilseignern") is None:
        raise DatenFehlen(f"{ANGABEN}: vertraege_mit_anteilseignern (Anlage WA, Zeile 15) fehlt.")

    wurzel, nd = _nutzdaten("KSt", bufa=bufa, hersteller_id=hersteller_id, produkt=produkt,
                            produktversion=produktversion, datenlieferant=stamm["name"],
                            testmerker=testmerker)
    ns = f"http://finkonsens.de/elster/elstererklaerung/kst/e30/v{jahr}"
    e30 = _sub(nd, f"{{{ns}}}E30", version=str(jahr))

    def s(eltern, tag, text=None):
        return _sub(eltern, f"{{{ns}}}{tag}", text)

    k1 = s(e30, "KSt1")
    s(k1, "E8000016", "X")                         # mit Feststellung des Einlagekontos
    allg = s(k1, "Allgemein")
    s(allg, "E8000101", stamm["name"])
    if _widnr(stamm):
        s(allg, "E8000102", _widnr(stamm))
    st = s(k1, "Steuerpflicht")
    s(st, "E8000501", (angaben.get("ort_geschaeftsleitung") or stamm["ort"])[:24])
    s(st, "E8000601", (stamm.get("sitz") or stamm["ort"])[:24])
    s(s(st, "Rechtsform"), "E8001105", rf)
    if (erg := _ergaenzend(angaben, "kst")):
        q = s(k1, "Qualifiz")                      # Zeile 13
        s(q, "E8001305", erg[0])
        s(q, "E8001304", erg[1])
    wj = s(k1, "Wirtschaftsjahr")
    s(wj, "E8002606", _datum(gj["beginn"]))
    s(wj, "E8002607", _datum(gj["ende"]))
    if angaben.get("neu_gegruendet"):
        s(wj, "E8000703", "1")
    s(s(k1, "Weiteres"), "E8001007", _jn(bool(angaben.get("p8_abs9"))))
    ae_el = s(k1, "AE")
    for a in ae:
        e = s(ae_el, "Einzel")
        s(e, "E8122111", _jn(a.get("geaendert", True)))
        s(e, "E8122109", elster_stnr(a["steuernummer"], a["bufa"]))
        s(e, "E8122101", a["name"])
        s(e, "E8122102", a["strasse"])
        s(e, "E8122103", f"{a['plz']} {a['ort']}")
        b = s(e, "Besitzdauer")
        # Dezimalkomma wie auf dem Vordruck: '25000,00' und '100'
        s(b, "E8122104", a["nennbetrag"].replace(".", ""))
        s(b, "E8122105", a["prozent"].rstrip("0").rstrip(",") if "," in a["prozent"]
          else a["prozent"])
        s(b, "E8122106", _datum(a["seit"]))
        # bis zum Ende des Wirtschaftsjahres, wenn kein Abgang eingetragen ist
        s(b, "E8122107", _datum(a.get("bis") or gj["ende"]))
    s(s(ae_el, "Gesamt"), "E8122601", _jn(angaben.get("anteilseigner_geaendert", True)))

    gk = s(e30, "GK")
    s(gk, "WJ", "WJ1")
    ga = s(gk, "Allgemein")
    s(ga, "E8000201", _datum(gj["beginn"]))
    s(ga, "E8000202", _datum(gj["ende"]))
    if not eintraege:
        s(s(gk, "Bilanz"), "E8002001", str(w["ergebnis"]))
        s(s(gk, "Einkunfte"), "E8109901", str(w["zve"]))

        zve = s(e30, "ZVE")
        sde = s(zve, "SdE")
        s(s(s(sde, "Gewerbe"), "Laufend"), "E8004904", str(w["zve"]))
        s(s(sde, "SdE"), "E8005401", str(w["zve"]))
        s(s(s(zve, "GdE"), "GdE"), "E8006901", str(w["zve"]))
        s(s(s(zve, "ZVE"), "ZVE"), "E8007501", str(w["zve"]))

    if w["verlust"]:
        v = s(e30, "Verluste")
        neg = s(v, "Negativ")
        s(neg, "E8008001", str(w["verlust"]))
        s(neg, "E8008102", "0")                    # kein Rücktrag
        s(s(v, "Ende"), "E8009201", str(w["verlust"]))

    s(s(s(e30, "WA"), "AE"), "E8122801", _jn(angaben["vertraege_mit_anteilseignern"]))

    k1f = s(e30, "KSt1F")
    s(k1f, "WJ", "WJ1")
    fa = s(k1f, "Allgemein")
    if f.get("eintritt_steuerpflicht"):
        s(fa, "E8200905", _datum(f["eintritt_steuerpflicht"]))
    s(fa, "E8200101", _datum(gj["beginn"]))
    s(fa, "E8200102", _datum(gj["ende"]))
    g = s(k1f, "Gewinn")
    # § 27 Abs. 1 Satz 5 KStG: Eigenkapital laut Steuerbilanz abzüglich
    # gezeichnetem Kapital und Einlagekonto, nicht negativ. Ohne Zeile 18 lässt
    # die Plausibilitätsprüfung Zeile 20 = Zeile 17 durchgehen — ein Fehler, der
    # erst beim Bescheid auffällt.
    ausschuettbar = max(f["eigenkapital"] - f["nennkapital"] - f["einlagekonto"], 0)
    if f.get("ausschuettbarer_gewinn") not in (None, ausschuettbar):
        raise DatenFehlen(f"{ANGABEN}: kst1f.ausschuettbarer_gewinn ist {f['ausschuettbarer_gewinn']}, "
                          f"Eigenkapital − Nennkapital − Einlagekonto ergibt {ausschuettbar}.")
    s(g, "E8201001", str(f["eigenkapital"]))
    s(g, "E8201101", str(f["nennkapital"]))
    s(g, "E8201201", str(f["einlagekonto"]))
    s(g, "E8201301", str(ausschuettbar))
    tab = s(s(k1f, "ELK"), "Tabelle")
    # Zeile 40 gilt nur beim Eintritt in die Steuerpflicht; sonst ist der
    # Anfangsbestand der Bestand zum Schluss des Vorjahres (Zeile 42)
    s(s(tab, "Anfang"), "E8240102" if f.get("eintritt_steuerpflicht") else "E8240103",
      str(f["einlagekonto_anfang"]))
    s(s(tab, "Ende"), "E8245601", str(f["einlagekonto_ende"]))

    _vorsatz(e30, ns, "30", stamm=stamm, steuernummer=steuernummer, jahr=jahr,
             bescheid=bool(angaben.get("bescheiddaten_abholen")))
    if eintraege:
        _einsetzen(e30, ns, eintraege, index, wiederholt)
        _ordnen(e30, "kst", jahr, doku)
    return _text(wurzel)


def gewst(spec: dict, angaben: dict, *, stamm: dict, jahr: int, steuernummer: str,
          bufa: str, hersteller_id: str, produkt: str, produktversion: str,
          testmerker: str | None = None, ausfuellhilfe: dict | None = None,
          doku: str | None = None) -> str:
    """Datensatz der Datenart GewSt_<jahr>: eine Betriebsstätte in einer Gemeinde."""
    rf = _pruefe_kopf(stamm, steuernummer, bufa)
    eintraege = zuordnung(ausfuellhilfe, "gewst")
    if eintraege:
        if spec.get("gesperrt"):
            raise DatenFehlen("Die Überleitung in steuern.json ist gesperrt — erst `bh abgabe` "
                              "freibekommen.")
        doku = doku or eric_klartext.dokumentation()
        index, wiederholt = _doku("gewst", jahr, doku)
        if _zahl(eintraege, "E4001703") is None:
            raise DatenFehlen("Die Ausfüllhilfe trägt keinen Gewinn aus Gewerbebetrieb "
                              "(GewSt 1 A Zeile 39, E4001703).")
    else:
        w = werte(spec)
    bst = angaben.get("betriebsstaette") or {}
    _vorhanden(bst, f"{ANGABEN}, betriebsstaette", "plz", "ort")
    if not angaben.get("gegenstand"):
        raise DatenFehlen(f"{ANGABEN}: gegenstand (GewSt 1 A, Zeile 4) fehlt.")

    wurzel, nd = _nutzdaten("GewSt", bufa=bufa, hersteller_id=hersteller_id, produkt=produkt,
                            produktversion=produktversion, datenlieferant=stamm["name"],
                            testmerker=testmerker)
    ns = f"http://finkonsens.de/elster/elstererklaerung/gewst/e20/v{jahr}"
    e20 = _sub(nd, f"{{{ns}}}E20", version=str(jahr))

    def s(eltern, tag, text=None):
        return _sub(eltern, f"{{{ns}}}{tag}", text)

    g1 = s(e20, "GewSt1A")
    allg = s(g1, "Allgemein")
    s(allg, "E4000101", stamm["name"])
    s(allg, "E4000201", angaben["gegenstand"][:25])
    if _widnr(stamm):
        s(allg, "E4000502", _widnr(stamm))
    s(s(g1, "Rechtsform"), "E4000501", rf)
    if (erg := _ergaenzend(angaben, "gewst")):
        q = s(g1, "Erg_Ang")                       # Zeile 21
        s(q, "E4003204", erg[0])
        s(q, "E4003205", erg[1])
    b = s(s(g1, "Ang_Gew_Betr"), "Betr_St")
    s(b, "E4001506", "2")                          # nicht in mehreren Gemeinden
    s(b, "E4001507", "2")
    s(b, "E4001102", "2")                          # nicht verlegt
    s(b, "E4001110", bst["plz"])
    s(b, "E4001111", bst["ort"])
    if not eintraege:
        s(s(g1, "Gewinn"), "E4001703", str(w["zve"]))

    _vorsatz(e20, ns, "20", stamm=stamm, steuernummer=steuernummer, jahr=jahr,
             bescheid=bool(angaben.get("bescheiddaten_abholen")))
    if eintraege:
        _einsetzen(e20, ns, eintraege, index, wiederholt)
        _ordnen(e20, "gewst", jahr, doku)
    return _text(wurzel)


def est(spec: dict, *, stamm: dict, jahr: int, steuernummer: str, bufa: str,
        hersteller_id: str, produkt: str, produktversion: str,
        testmerker: str | None = None, ausfuellhilfe: dict | None = None,
        doku: str | None = None) -> str:
    """Datensatz der Datenart ESt_<jahr> (Schema E10, Unterfallart 10).

    Anders als KSt und GewSt gibt es keinen einfachen Fall aus steuern.json:
    jedes Feld — Hauptvordruck, Anlagen, auch die Identifikationsnummern im
    Vorsatz — kommt aus der Ausfüllhilfe. Sie muss eingabefertig sein und ihre
    Gegenproben gegen steuern.json bestehen; `bh` fügt nur den Rahmen hinzu
    (Steuernummer, Zeitraum, Absender aus mandant.json). Mehrfach abgegebene
    Vordrucke (Anlage N je Person, Anlage V je Objekt, Anlage Kind je Kind) trennt
    das `einzel` ihres Formulars."""
    _pflicht(stamm, "name", "strasse", "plz", "ort")
    if not re.fullmatch(r"\d{13}", steuernummer or ""):
        raise DatenFehlen("Steuernummer im ELSTER-Format hat 13 Ziffern.")
    if not re.fullmatch(r"\d{4}", bufa or ""):
        raise DatenFehlen("Bundesfinanzamtsnummer hat vier Ziffern (mandant.json, elster.bufa).")
    if spec.get("gesperrt"):
        raise DatenFehlen("Die Überleitung in steuern.json ist gesperrt (Platzhalter oder "
                          "Abweichung von den Konten) — erst `bh abgabe` freibekommen.")
    eintraege = zuordnung(ausfuellhilfe, "est")
    if not any(x["vordruck"] == "ESt1A" for x in eintraege):
        raise DatenFehlen("Die Ausfüllhilfe (elster.json) trägt keine Felder des "
                          "Hauptvordrucks ESt 1 A mit `xml` — die Einkommensteuererklärung "
                          "entsteht allein aus ihr.")
    doku = doku or eric_klartext.dokumentation()
    index, wiederholt = _doku("est", jahr, doku)

    wurzel, nd = _nutzdaten("ESt", bufa=bufa, hersteller_id=hersteller_id, produkt=produkt,
                            produktversion=produktversion, datenlieferant=stamm["name"],
                            testmerker=testmerker)
    ns = f"http://finkonsens.de/elster/elstererklaerung/est/e10/v{jahr}"
    e10 = _sub(nd, f"{{{ns}}}E10", version=str(jahr))
    # Der Absender hat 45 Zeichen; zwei Ehegatten passen oft nicht hinein, dann
    # steht die erste Person da, nicht ein abgeschnittener Name
    absender = stamm["name"]
    if len(absender) > 45:
        absender = absender.split(" und ")[0] if len(absender.split(" und ")[0]) <= 45 else \
            absender[:46].rsplit(" ", 1)[0]
    _vorsatz(e10, ns, "10", stamm={**stamm, "name": absender}, steuernummer=steuernummer, jahr=jahr,
             bescheid=bool((ausfuellhilfe or {}).get("bescheiddaten_abholen")))
    _einsetzen(e10, ns, eintraege, index, wiederholt)
    _ordnen(e10, "est", jahr, doku)
    return _text(wurzel)


def betraege_aus_xml(xml: str, art: str) -> dict[str, str]:
    """Die Betragsfelder eines Datensatzes: {'E8002001': '-1235', ...}. Steht ein
    Feld mehrfach darin (mehrere Anlagen V), zählt es mit: 'E0701601 (2)'."""
    felder = BETRAEGE[art]
    aus: dict[str, str] = {}
    zahl: dict[str, int] = {}
    for e in ET.fromstring(xml.encode("utf-8")).iter():
        n = e.tag.rpartition("}")[2]
        if n in felder:
            zahl[n] = zahl.get(n, 0) + 1
            aus[n if zahl[n] == 1 else f"{n} ({zahl[n]})"] = (e.text or "").strip()
    return aus
