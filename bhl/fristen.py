"""Fristen und Faelligkeiten - was zu tun ist, nicht was falsch ist.

Bewusst getrennt von `pruefung.py`. Dort stehen Kontrollen auf den Daten: geht
die Bilanz auf, ist jeder Bankumsatz erklaert. Ein Befund dort heisst, dass in
der Buchfuehrung etwas nicht stimmt.

Eine offene Zahllast ist dagegen kein Fehler. Sie ist angemeldet, faellig am
10., und bis dahin ist alles in Ordnung. Sie in dieselbe Liste zu schreiben,
macht aus einem Termin einen Mangel - und laesst die Liste dauerhaft rot
aussehen, bis niemand mehr hinsieht.

Zwei Quellen:

* **Laufend** - Voranmeldungen und Zusammenfassende Meldungen. Sie kommen aus
  der Buchfuehrung und aus `voranmeldung`, sind also schon da.
* **Jaehrlich** - Steuererklaerungen, Aufstellung und Offenlegung des
  Jahresabschlusses. Sie ergeben sich aus Gesetz und Stichtag. Ob sie erledigt
  sind, weiss die Buchfuehrung nicht; das steht in `fristen.csv`.

Welche Jahrespflichten anfallen, haengt am Kontenrahmen: eine
Kapitalgesellschaft schuldet Koerperschaft- und Gewerbesteuererklaerung,
Aufstellung und Offenlegung, die private Rechnung nur die
Einkommensteuererklaerung.

Die gesetzlichen Fristen haengen an zwei Angaben, die in `mandant.json` unter
`"fristen"` stehen und beide voreingestellt sind:

    "steuerlich_beraten": false   § 149 Abs. 2 AO statt Abs. 3
    "groessenklasse": "klein"     § 264 Abs. 1 S. 3 HGB statt S. 2

Stimmen sie nicht, stimmen die Termine nicht - deshalb nennt jede Zeile ihre
Grundlage, damit die Annahme sichtbar bleibt und nachgerechnet werden kann.
Feiertage kennt die Berechnung nicht, nur Samstag und Sonntag (§ 108 Abs. 3
AO); faellt ein Termin auf einen Feiertag, verschiebt er sich zusaetzlich.
"""

from __future__ import annotations

import calendar
import csv
import datetime
import json
import os

# Der Vorlauf, ab dem ein Termin als "steht an" gilt. Alles Weitere steht
# darunter, damit die Liste nicht zur Jahresvorschau wird.
BALD_TAGE = 45

# Was noch zu tun ist. `verlaengert` gehoert dazu: die Pflicht besteht, nur der
# Termin ist ein anderer.
OFFEN_STATUS = ("offen", "ueberfaellig", "verlaengert")


def _kurz(iso: str) -> str:
    j, m, t = iso.split("-")
    return f"{t}.{m}.{j}"


def werktag(d: datetime.date) -> datetime.date:
    """§ 108 Abs. 3 AO: faellt das Fristende auf Samstag oder Sonntag, endet die
    Frist mit Ablauf des naechsten Werktags."""
    return d + datetime.timedelta(days=(7 - d.weekday()) if d.weekday() >= 5 else 0)


def _monate(d: datetime.date, n: int) -> datetime.date:
    """d plus n Monate, auf den Monatsletzten begrenzt."""
    m = d.month - 1 + n
    jahr, monat = d.year + m // 12, m % 12 + 1
    naechster = datetime.date(jahr + monat // 12, monat % 12 + 1, 1)
    return min(d.replace(year=jahr, month=monat, day=1)
               + datetime.timedelta(days=d.day - 1), naechster - datetime.timedelta(days=1))


def erledigungen(mandant_dir: str) -> dict[tuple[int, str], dict]:
    """`fristen.csv` - was tatsaechlich erledigt wurde.

    Dieselbe Rolle wie `voranmeldungen.csv` bei der Umsatzsteuer: ohne Nachweis
    laesst sich nicht sagen, ob eine Pflicht erfuellt ist. Die Datei ist
    freiwillig; fehlt sie, gilt alles als offen.

    Spalten: jahr;art;status;erledigt_am;nachweis;notiz

    `status` = `verlaengert` traegt eine bewilligte oder beantragte
    Fristverlaengerung nach § 109 AO: das Datum steht dann in `erledigt_am` und
    tritt an die Stelle der gesetzlichen Frist. Die Pflicht bleibt offen, nur
    der Termin verschiebt sich - ohne diesen Zustand meldet die Liste eine
    laengst verlaengerte Frist als ueberfaellig.

    Die optionale Spalte `grundlage` ersetzt in diesem Fall die Begruendung.
    Nicht jeder abweichende Termin ist eine Verlaengerung nach § 109 AO: fordert
    das Finanzamt Unterlagen nach § 149 Abs. 1 S. 2 AO an und setzt dafuer ein
    Datum, gilt dieses - und § 109 AO waere die falsche Fundstelle. Fehlt die
    Spalte, bleibt es bei § 109 AO.
    """
    pfad = os.path.join(mandant_dir, "fristen.csv")
    if not os.path.exists(pfad):
        return {}
    d = {}
    with open(pfad, encoding="utf-8") as fh:
        for r in csv.DictReader((z for z in fh if not z.startswith("#")), delimiter=";"):
            if not (r.get("jahr") or "").strip():
                continue
            d[(int(r["jahr"]), (r["art"] or "").strip())] = {
                "status": (r.get("status") or "erledigt").strip(),
                "erledigt_am": (r.get("erledigt_am") or "").strip() or None,
                "nachweis": (r.get("nachweis") or "").strip() or None,
                "notiz": (r.get("notiz") or "").strip() or None,
                "grundlage": (r.get("grundlage") or "").strip() or None,
            }
    return d


def _eintrag(art, bezeichnung, faellig, grundlage, heute, erledigt=None,
             betrag_cent=None, verweis=None, hinweis=None, vorlaeufig=False) -> dict:
    erl = erledigt or {}
    offen = erl.get("status") not in ("erledigt", "entfaellt")
    gesetzlich = None
    # Eine bewilligte Fristverlaengerung tritt an die Stelle der gesetzlichen
    # Frist. Die gesetzliche bleibt sichtbar, damit nachvollziehbar ist, wovon
    # verlaengert wurde.
    if erl.get("status") == "verlaengert" and erl.get("erledigt_am"):
        gesetzlich = faellig.isoformat()
        faellig = datetime.date.fromisoformat(erl["erledigt_am"])
        grundlage = (erl.get("grundlage")
                     or f"§ 109 AO (verlängert, gesetzlich {_kurz(gesetzlich)})")
    tage = (faellig - heute).days
    return {
        "art": art, "bezeichnung": bezeichnung, "faellig": faellig.isoformat(),
        "gesetzlich_faellig": gesetzlich,
        "grundlage": grundlage, "betrag_cent": betrag_cent, "verweis": verweis,
        "hinweis": hinweis, "tage": tage,
        # Der Betrag eines noch laufenden Zeitraums ist der Stand von heute, nicht
        # die Summe, die gemeldet wird. Ohne diesen Vermerk liest er sich neben
        # einer Frist in drei Monaten wie ein Endbetrag.
        "vorlaeufig": vorlaeufig, "stand": heute.isoformat() if vorlaeufig else None,
        "status": (erl.get("status") if not offen
                   else "ueberfaellig" if tage < 0
                   else "verlaengert" if gesetzlich else "offen"),
        "erledigt_am": erl.get("erledigt_am"), "nachweis": erl.get("nachweis"),
        "notiz": erl.get("notiz"),
    }


def jahrespflichten(mandant_dir: str, jahr: int, gj_ende: str, heute: datetime.date,
                    kapitalgesellschaft: bool = True,
                    umsatzsteuerpflichtig: bool = True) -> list[dict]:
    """Erklaerungs-, Aufstellungs- und Offenlegungsfristen fuer ein Geschaeftsjahr."""
    cfg = {}
    pfad = os.path.join(mandant_dir, "mandant.json")
    if os.path.exists(pfad):
        cfg = (json.load(open(pfad, encoding="utf-8")).get("fristen") or {})
    beraten = bool(cfg.get("steuerlich_beraten", False))
    klein = str(cfg.get("groessenklasse", "klein")) in ("kleinst", "klein")
    kleinst_gj = str(cfg.get("groessenklasse", "klein")) == "kleinst"
    erl = erledigungen(mandant_dir)
    stichtag = datetime.date.fromisoformat(gj_ende)

    # § 149 Abs. 2 AO: sieben Monate nach Ablauf des Kalenderjahres.
    # § 149 Abs. 3 AO (beraten): letzter Tag des Februars des zweiten Folgejahres.
    if beraten:
        # "letzter Tag des Monats Februar des zweiten auf den Besteuerungszeitraum
        # folgenden Kalenderjahres"
        erklaerung = werktag(datetime.date(
            jahr + 2, 2, calendar.monthrange(jahr + 2, 2)[1]))
        grund_erkl = "§ 149 Abs. 3 AO (steuerlich beraten)"
    else:
        erklaerung = werktag(datetime.date(jahr + 1, 7, 31))
        grund_erkl = "§ 149 Abs. 2 AO (nicht steuerlich beraten)"

    eintraege = []
    steuern = []
    # Eine vermoegensverwaltende Gesellschaft, die nur Beteiligungen haelt, ist
    # kein Unternehmer im Sinne des § 2 UStG - sie schuldet keine Umsatzsteuer
    # und hat nichts zu erklaeren. Die Koerperschaft- und Gewerbesteuerpflicht
    # bleibt: eine Kapitalgesellschaft ist kraft Rechtsform Gewerbebetrieb
    # (§ 2 Abs. 2 GewStG), auch wenn sie nur verwaltet.
    if umsatzsteuerpflichtig:
        steuern.append(("umsatzsteuer_jahr", "Umsatzsteuererklärung"))
    if kapitalgesellschaft:
        steuern += [("koerperschaftsteuer", "Körperschaftsteuererklärung"),
                    ("gewerbesteuer", "Gewerbesteuererklärung")]
    else:
        # Die private Erklaerung hat dieselbe Frist wie jede andere
        # Steuererklaerung (§ 149 AO). Sie fehlte hier, weil der Block
        # urspruenglich nur fuer Kapitalgesellschaften geschrieben war - die
        # Fristenliste des privaten Mandanten war deshalb dauerhaft leer.
        steuern.append(("einkommensteuer", "Einkommensteuererklärung"))
    for art, name in steuern:
        eintraege.append(_eintrag(
            art, f"{name} {jahr}", erklaerung, grund_erkl, heute, erl.get((jahr, art)),
            verweis="#/steuern"))

    if kapitalgesellschaft:
        # § 5b EStG: Bilanz und Gewinn- und Verlustrechnung sind als Datensatz
        # zu uebermitteln - eine eigene Uebermittlung neben der Erklaerung, nicht
        # ein Teil von ihr, mit derselben Frist (§ 149 AO).
        #
        # Sie faellt leicht durchs Raster: Mein ELSTER hat kein Formular dafuer,
        # die E-Bilanz laeuft nur ueber Drittsoftware. Wer seine Erklaerungen
        # dort abgibt, hakt die Erklaerungen ab und uebersieht die Bilanz. Genau
        # das kommt im Gruendungsjahr vor - KSt, GewSt und USt waren am
        # 03.04.2026 uebermittelt, die Bilanz nie, und das Finanzamt hat sie am
        # 20.08.2026 angefordert. Deshalb steht sie hier als eigene Zeile.
        eintraege.append(_eintrag(
            "ebilanz", f"E-Bilanz {jahr} übermitteln", erklaerung,
            f"§ 5b EStG, Frist nach {grund_erkl}", heute,
            erl.get((jahr, "ebilanz")), verweis="#/steuern"))
        # § 264 Abs. 1 S. 2 HGB: drei Monate. S. 3 laesst kleinen Gesellschaften
        # sechs, "wenn dies einem ordnungsgemaessen Geschaeftsgang entspricht".
        eintraege.append(_eintrag(
            "jahresabschluss", f"Jahresabschluss {jahr} aufstellen",
            werktag(_monate(stichtag, 6 if klein else 3)),
            f"§ 264 Abs. 1 S. {3 if klein else 2} HGB "
            f"({'Kleinstkapitalgesellschaft' if kleinst_gj else 'kleine' if klein else 'mittelgroße oder große'}"
            f"{'' if kleinst_gj else ' Kapitalgesellschaft'})",
            heute, erl.get((jahr, "jahresabschluss"))))
        # § 325 Abs. 1a HGB: spaetestens ein Jahr nach dem Abschlussstichtag.
        # Die Kleinstkapitalgesellschaft (§ 267a HGB) darf statt offenzulegen
        # nur die Bilanz hinterlegen (§ 326 Abs. 2 HGB) - dieselbe Frist, aber
        # ein anderer Vorgang, und die Bilanz bleibt nicht frei abrufbar.
        kleinst = str(cfg.get("groessenklasse", "klein")) == "kleinst"
        eintraege.append(_eintrag(
            "offenlegung",
            f"Jahresabschluss {jahr} {'hinterlegen' if kleinst else 'offenlegen'}",
            werktag(_monate(stichtag, 12)),
            ("§ 326 Abs. 2 HGB (Hinterlegung, Kleinstkapitalgesellschaft)" if kleinst
             else "§ 325 Abs. 1a HGB (Offenlegung im Bundesanzeiger)"),
            heute, erl.get((jahr, "offenlegung"))))
    return eintraege


def laufend(zeitraeume: list[dict], heute: datetime.date) -> list[dict]:
    """Voranmeldungen und Zusammenfassende Meldungen aus der Umsatzsteuer-Übersicht.

    Abgeben und zahlen sind zwei Termine mit derselben Frist. Ein Zeitraum, der
    uebermittelt, aber nicht bezahlt ist, sieht sonst erledigt aus - und genau
    das ist der Fall, in dem Saeumniszuschlaege entstehen (§ 240 AO).
    """
    aus = []
    for z in zeitraeume:
        if z.get("nur_historie"):
            continue
        frist = datetime.date.fromisoformat(z["frist"])
        verweis = f"#/ustva/{z['jahr']}/{z['code']}"
        # Solange der Zeitraum laeuft, ist jeder Betrag ein Zwischenstand: was
        # bis heute gebucht ist, nicht was gemeldet wird.
        laeuft = datetime.date.fromisoformat(z["bis"]) >= heute
        if z["status"] != "abgegeben":
            aus.append(_eintrag(
                "ustva_abgabe", f"Umsatzsteuer-Voranmeldung {z['bezeichnung']}", frist,
                "§ 18 Abs. 1 UStG" + (", § 46 UStDV (Dauerfristverlängerung)"
                                      if z.get("dauerfrist") else ""),
                heute, betrag_cent=z.get("kz83"), verweis=verweis, vorlaeufig=laeuft))
        if z.get("zahlung_offen"):
            aus.append(_eintrag(
                "ustva_zahlung", f"Umsatzsteuer-Zahllast {z['bezeichnung']}", frist,
                "§ 18 Abs. 1 S. 4 UStG — angemeldet, noch nicht gezahlt",
                heute, betrag_cent=z.get("kz83"), verweis=verweis))
        if z.get("zm_summe") and z.get("zm_frist"):
            zm_erledigt = (z.get("zm_stand") or {}).get("status") == "abgegeben"
            aus.append(_eintrag(
                "zm", f"Zusammenfassende Meldung {z['bezeichnung']}",
                datetime.date.fromisoformat(z["zm_frist"]),
                "§ 18a UStG — die Dauerfristverlängerung gilt hier nicht "
                "(§ 18a Abs. 11 UStG)",
                heute, {"status": "erledigt",
                        "erledigt_am": (z.get("zm_stand") or {}).get("abgegeben_am"),
                        "nachweis": (z.get("zm_stand") or {}).get("transferticket")}
                if zm_erledigt else None,
                betrag_cent=z.get("zm_summe"), verweis=verweis,
                vorlaeufig=laeuft and not zm_erledigt))
    return aus


def alle(mandant_dir: str, jahre: list[tuple[int, str]], zeitraeume: list[dict],
         heute: datetime.date | None = None, kapitalgesellschaft: bool = True,
         umsatzsteuerpflichtig: bool = True) -> dict:
    """Alle Termine, faelligste zuerst. Erledigte bleiben drin, aber hinten.

    `jahre` sind die angelegten Geschaeftsjahre als (Jahr, Stichtag) - nicht ein
    Zeitfenster um das laufende Jahr herum. Sonst entstuenden Pflichten fuer
    Jahre, in denen es die Gesellschaft noch gar nicht gab: wer im November
    2025 gegruendet wurde, schuldet fuer 2024 keine Erklaerung.

    Die Jahrespflichten des laufenden Jahres sind selten schon faellig - die des
    Vorjahres dagegen sehr wohl. Beide gehoeren auf dieselbe Liste.
    """
    heute = heute or datetime.date.today()
    eintraege = laufend(zeitraeume, heute)
    for j, ende in jahre:
        eintraege += jahrespflichten(mandant_dir, j, ende, heute, kapitalgesellschaft,
                                     umsatzsteuerpflichtig)

    offen = [e for e in eintraege if e["status"] in OFFEN_STATUS]
    offen.sort(key=lambda e: e["faellig"])
    erledigt = sorted((e for e in eintraege if e["status"] not in OFFEN_STATUS),
                      key=lambda e: e["faellig"], reverse=True)
    return {
        "eintraege": offen + erledigt,
        "offen": len(offen),
        "ueberfaellig": sum(1 for e in offen if e["status"] == "ueberfaellig"),
        "bald": sum(1 for e in offen if 0 <= e["tage"] <= BALD_TAGE),
        "naechste": offen[0]["faellig"] if offen else None,
        "zu_zahlen": sum(e["betrag_cent"] or 0 for e in offen
                         if e["art"] == "ustva_zahlung"),
    }
