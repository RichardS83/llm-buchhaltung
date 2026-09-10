"""Auswertungen: Summen- und Saldenliste, Bilanz, GuV, Kontoblatt, Journal."""

from __future__ import annotations

import sqlite3

from .db import eur
from .kontenplan import Rahmen, aus_mandant, lade
from .ledger import salden

B = 96


def rahmen(con: sqlite3.Connection, gj_id: int) -> Rahmen:
    """Der Kontenrahmen des Mandanten, zu dem dieses Geschaeftsjahr gehoert.

    Wird hier nachgeschlagen statt durchgereicht, damit jede Auswertung von
    selbst den richtigen Rahmen erwischt - Bilanz und GuV der UG sind anders
    gegliedert als die der GmbH.
    """
    r = con.execute(
        "SELECT m.kontenrahmen, m.stammdaten FROM geschaeftsjahr g"
        " JOIN mandant m ON m.id=g.mandant_id WHERE g.id=?", (gj_id,)).fetchone()
    return aus_mandant(r) if r else lade(None)


def _linie(z: str = "-") -> str:
    return z * B


def _zeile(links: str, betrag: int | None, einzug: int = 0, vj: int | None = None) -> str:
    t = " " * einzug + links
    b = eur(betrag) if betrag is not None else ""
    v = eur(vj) if vj is not None else ""
    return f"{t:<58}{b:>18}{v:>18}".rstrip()


def saldenliste(con: sqlite3.Connection, gj_id: int, von: str | None = None,
                bis: str | None = None) -> str:
    out = ["SUMMEN- UND SALDENLISTE", _linie(),
           f"{'Konto':<6}{'Bezeichnung':<44}{'Soll':>15}{'Haben':>15}{'Saldo':>15}", _linie()]
    ts = th = 0
    for r in salden(con, gj_id, von, bis):
        ts += r["soll"]; th += r["haben"]
        out.append(f"{r['nummer']:<6}{r['bezeichnung'][:43]:<44}"
                   f"{eur(r['soll']):>15}{eur(r['haben']):>15}{eur(r['saldo']):>15}")
    out += [_linie(), f"{'':<50}{eur(ts):>15}{eur(th):>15}{eur(ts - th):>15}"]
    return "\n".join(out)


def _posten_salden(con: sqlite3.Connection, gj_id: int, von: str | None = None,
                   bis: str | None = None) -> dict[str, int]:
    d: dict[str, int] = {}
    for r in salden(con, gj_id, von, bis):
        if r["posten"]:
            d[r["posten"]] = d.get(r["posten"], 0) + r["saldo"]
    return d


def _posten_konten(con: sqlite3.Connection, gj_id: int, von: str | None = None,
                   bis: str | None = None) -> dict[str, list[dict]]:
    """posten -> Konten, die darauf einzahlen. Basis fuer das Aufklappen."""
    d: dict[str, list[dict]] = {}
    for r in salden(con, gj_id, von, bis):
        if r["posten"]:
            d.setdefault(r["posten"], []).append(
                {"nummer": r["nummer"], "bezeichnung": r["bezeichnung"], "typ": r["typ"],
                 "saldo": r["saldo"], "soll": r["soll"], "haben": r["haben"],
                 "steuer_tag": r["steuer_tag"]})
    return d


def _ebene(bez: str) -> int:
    return (len(bez) - len(bez.lstrip())) // 2


def _dreh_aktiva(posten) -> int:
    return 1


def _dreh_passiva(posten) -> int:
    return -1                                   # Passiva stehen im Haben


def _dreh_guv(ertrags_posten):
    return lambda posten: -1 if any(x in ertrags_posten for x in posten) else 1


def _gliederung(gliederung, p, konten, vj, dreh_fn) -> tuple[list[dict], int, int]:
    """Eine Gliederung (Bilanzseite oder GuV) in Zeilen aufloesen.

    dreh_fn bestimmt je Zeile das Vorzeichen: Habensalden - Passiva und
    Ertraege - sollen als positive Zahl erscheinen.
    """
    zeilen, summe, summe_vj = [], 0, 0
    for bez, posten in gliederung:
        z = {"bez": bez.strip(), "ebene": _ebene(bez), "posten": posten,
             "einzug": len(bez) - len(bez.lstrip()),
             "wert": None, "vj": None, "konten": []}
        if posten is None:                      # reine Ueberschrift
            zeilen.append(z)
            continue
        dreh = dreh_fn(posten)
        z["wert"] = dreh * sum(p.get(x, 0) for x in posten)
        z["vj"] = dreh * sum(vj.get(x, 0) for x in posten) if vj is not None else None
        for x in posten:
            for k in konten.get(x, []):
                z["konten"].append({**k, "wert": dreh * k["saldo"]})
        summe += z["wert"]
        summe_vj += z["vj"] or 0
        zeilen.append(z)
    return zeilen, summe, summe_vj


def bilanz_daten(con: sqlite3.Connection, gj_id: int, vj_gj_id: int | None = None,
                 bis: str | None = None, vj_bis: str | None = None) -> dict:
    """Bilanz als Datenstruktur - Grundlage fuer Textbericht und Oberflaeche.

    Die Vorjahresspalte kommt aus dem Geschaeftsjahr `vj_gj_id`, also aus
    denselben Buchungen wie das laufende Jahr - keine gepflegte Zahlenliste.

    `bis` setzt den Stichtag. Ein Zeitraumanfang gibt es hier bewusst nicht:
    eine Bilanz zeigt immer den aufgelaufenen Bestand, nie die Bewegung eines
    Ausschnitts. `vj_bis` ist der entsprechende Stichtag im Vorjahr, damit ein
    unterjaehriger Vergleich denselben Monat trifft.
    """
    r = rahmen(con, gj_id)
    p = _posten_salden(con, gj_id, None, bis)
    konten = _posten_konten(con, gj_id, None, bis)
    vj = _posten_salden(con, vj_gj_id, None, vj_bis) if vj_gj_id else None
    erg = ergebnis(con, gj_id, None, bis)

    aktiva, s_akt, s_akt_vj = _gliederung(r.bilanz_aktiva, p, konten, vj, _dreh_aktiva)
    passiva, s_pas, s_pas_vj = _gliederung(r.bilanz_passiva, p, konten, vj, _dreh_passiva)

    # Der Bilanzgewinn traegt das Ergebnis des Jahres, das noch auf keinem
    # Bestandskonto steht - es steckt in den Erfolgskonten. Fuer das Vorjahr
    # gilt dasselbe.
    erg_vj = ergebnis(con, vj_gj_id, None, vj_bis) if vj_gj_id else 0
    for z in passiva:
        if r.ergebnis_zeile in z["bez"]:
            z["wert"] += erg
            if z["vj"] is not None:
                z["vj"] += erg_vj
            z["konten"].append({"nummer": "GuV", "bezeichnung": f"Jahresergebnis {jahr(con, gj_id)}",
                                "typ": "E", "saldo": erg, "soll": 0, "haben": 0,
                                "steuer_tag": None, "wert": erg})
            s_pas += erg
            s_pas_vj += erg_vj
            break

    return {"aktiva": aktiva, "passiva": passiva,
            "summe_aktiva": s_akt, "summe_passiva": s_pas,
            "summe_aktiva_vj": s_akt_vj if vj is not None else None,
            "summe_passiva_vj": s_pas_vj if vj is not None else None,
            "ergebnis": erg, "ergebnis_vj": erg_vj if vj_gj_id else None,
            "differenz": s_akt - s_pas,
            # Salden je Bilanzposten. Eine Gliederungszeile kann mehrere Posten
            # zusammenfassen; wer einen einzelnen davon auswerten will - etwa die
            # Guthaben bei Zahlungsdienstleistern innerhalb der sonstigen
            # Vermoegensgegenstaende -, kommt nur hier an ihn heran.
            "posten": p}


def guv_daten(con: sqlite3.Connection, gj_id: int, vj_gj_id: int | None = None,
              von: str | None = None, bis: str | None = None,
              vj_von: str | None = None, vj_bis: str | None = None) -> dict:
    """GuV als Datenstruktur. Anders als die Bilanz ist sie eine Zeitraumrechnung -
    hier wirken `von` und `bis` beide."""
    p = _posten_salden(con, gj_id, von, bis)
    konten = _posten_konten(con, gj_id, von, bis)
    vj = _posten_salden(con, vj_gj_id, vj_von, vj_bis) if vj_gj_id else None
    r = rahmen(con, gj_id)
    zeilen, _, _ = _gliederung(r.guv, p, konten, vj, _dreh_guv(r.ertrags_posten))
    return {"zeilen": zeilen, "ergebnis": ergebnis(con, gj_id, von, bis),
            "ergebnis_vj": ergebnis(con, vj_gj_id, vj_von, vj_bis) if vj_gj_id else None}


def anlage_daten(con: sqlite3.Connection, gj_id: int, anlage_id: str,
                 vj_gj_id: int | None = None, von: str | None = None, bis: str | None = None,
                 vj_von: str | None = None, vj_bis: str | None = None) -> dict:
    """Eine steuerliche Anlage (Anlage V je Objekt) als Datenstruktur.

    Technisch derselbe Vorgang wie die GuV: Konten zeigen ueber ihren `posten`
    auf eine Zeile, `_gliederung` loest auf. Der Unterschied liegt allein in
    der Gliederung - hier sind es die Zeilennummern des Formulars.

    Einnahmen und Werbungskosten werden getrennt summiert und nicht aus der
    Gliederung aufaddiert: in der Anlage V stehen beide als positive Zahl, das
    Ergebnis ist ihre Differenz (Zeile 85).
    """
    r = rahmen(con, gj_id)
    a = next((x for x in r.anlagen if x["id"] == anlage_id), None)
    if a is None:
        raise KeyError(f"Anlage '{anlage_id}' gibt es in diesem Kontenrahmen nicht.")
    p = _posten_salden(con, gj_id, von, bis)
    konten = _posten_konten(con, gj_id, von, bis)
    vj = _posten_salden(con, vj_gj_id, vj_von, vj_bis) if vj_gj_id else None
    dreh = _dreh_guv(r.ertrags_posten)
    zeilen, _, _ = _gliederung(a["gliederung"], p, konten, vj, dreh)

    def summe(quelle, posten):
        return sum(dreh([x]) * quelle.get(x, 0) for x in posten)

    ein = summe(p, a["einnahmen"])
    wk = summe(p, a["werbungskosten"])
    d = {"id": a["id"], "titel": a["titel"], "art": a["art"], "nr": a["nr"],
         "zeilen": zeilen, "einnahmen": ein, "werbungskosten": wk, "ergebnis": ein - wk,
         "objekt": {"name": a["objekt"].name, "ort": a["objekt"].ort,
                    "angeschafft": a["objekt"].angeschafft,
                    "afa_satz": a["objekt"].afa_satz,
                    "aktenzeichen": a["objekt"].aktenzeichen,
                    "darlehen": [b for _, b in a["objekt"].darlehen]}}
    if vj is not None:
        d["einnahmen_vj"] = summe(vj, a["einnahmen"])
        d["werbungskosten_vj"] = summe(vj, a["werbungskosten"])
        d["ergebnis_vj"] = d["einnahmen_vj"] - d["werbungskosten_vj"]

    # Die AfA-Bemessungsgrundlage steht in keinem Konto - sie ist der
    # Anschaffungswert des Gebaeudes und liegt im Anlagenverzeichnis des
    # Steuerberaters. Solange das fehlt, laesst sie sich aus der gebuchten AfA
    # zurueckrechnen: AfA / Satz. Das Ergebnis ist auf den Rundungsfehler der
    # Erklaerung genau (dort stehen volle Euro) und deshalb ausdruecklich als
    # abgeleitet gekennzeichnet - es ist ein Anhaltspunkt, keine Feststellung.
    satz = a["objekt"].afa_satz
    afa = p.get(a["afa_posten"], 0) or (vj or {}).get(a["afa_posten"], 0)
    if afa and satz:
        d["afa_bemessung"] = round(abs(afa) * 10000 / satz)
        d["afa_bemessung_quelle"] = ("laufendes Jahr" if p.get(a["afa_posten"])
                                     else "Vorjahr")
    return d


def anlagen_daten(con: sqlite3.Connection, gj_id: int, vj_gj_id: int | None = None,
                  von: str | None = None, bis: str | None = None,
                  vj_von: str | None = None, vj_bis: str | None = None) -> dict:
    """Alle Anlagen im Ueberblick - die Zusammenstellung, die im Bescheid als
    Summe der Einkuenfte aus Vermietung und Verpachtung erscheint."""
    r = rahmen(con, gj_id)
    liste = [anlage_daten(con, gj_id, a["id"], vj_gj_id, von, bis, vj_von, vj_bis)
             for a in r.anlagen]
    return {"anlagen": liste,
            "einnahmen": sum(a["einnahmen"] for a in liste),
            "werbungskosten": sum(a["werbungskosten"] for a in liste),
            "ergebnis": sum(a["ergebnis"] for a in liste),
            "ergebnis_vj": (sum(a.get("ergebnis_vj", 0) for a in liste)
                            if vj_gj_id else None)}


def jahr(con: sqlite3.Connection, gj_id: int) -> int:
    r = con.execute("SELECT jahr FROM geschaeftsjahr WHERE id=?", (gj_id,)).fetchone()
    return r[0] if r else 0


def ergebnis(con: sqlite3.Connection, gj_id: int, von: str | None = None,
             bis: str | None = None) -> int:
    """Ueberschuss (+) / Fehlbetrag (-) des Zeitraums in Cent."""
    zusatz = "".join([" AND b.datum >= ?" if von else "",
                      " AND b.datum <= ?" if bis else ""])
    grenzen = [d for d in (von, bis) if d]
    # E-Konten: Ertrag = Habenueberschuss (+), X-Konten: Aufwand = Sollueberschuss (-)
    ertrag = con.execute(f"""
        SELECT COALESCE(SUM(z.haben_cent - z.soll_cent),0) FROM buchungszeile z
        JOIN buchung b ON b.id=z.buchung_id JOIN konto k ON k.id=z.konto_id
        WHERE b.gj_id=? AND k.typ='E'{zusatz}""", [gj_id] + grenzen).fetchone()[0]
    aufwand = con.execute(f"""
        SELECT COALESCE(SUM(z.soll_cent - z.haben_cent),0) FROM buchungszeile z
        JOIN buchung b ON b.id=z.buchung_id JOIN konto k ON k.id=z.konto_id
        WHERE b.gj_id=? AND k.typ='X'{zusatz}""", [gj_id] + grenzen).fetchone()[0]
    return ertrag - aufwand


def _gliederungs_text(zeilen: list[dict], mit_vj: bool) -> list[str]:
    out = []
    for z in zeilen:
        bez = " " * z["einzug"] + z["bez"]
        if z["posten"] is None:
            out.append(bez)
        else:
            out.append(_zeile(bez, z["wert"], 0, z["vj"] if mit_vj else None))
    return out


def bilanz(con: sqlite3.Connection, gj_id: int, vj_gj_id: int | None = None) -> str:
    d = bilanz_daten(con, gj_id, vj_gj_id)
    mit_vj = d["summe_aktiva_vj"] is not None
    out = [rahmen(con, gj_id).titel_bilanz.upper(), _linie(),
           f"{'AKTIVA':<58}{'Geschäftsjahr':>18}{'Vorjahr':>18}", _linie()]
    out += _gliederungs_text(d["aktiva"], mit_vj)
    out += [_linie(), _zeile("Summe Aktiva", d["summe_aktiva"], 0,
                             d["summe_aktiva_vj"] if mit_vj else None), "", _linie(),
            f"{'PASSIVA':<58}{'Geschäftsjahr':>18}{'Vorjahr':>18}", _linie()]
    out += _gliederungs_text(d["passiva"], mit_vj)
    out += [_linie(), _zeile("Summe Passiva", d["summe_passiva"], 0,
                             d["summe_passiva_vj"] if mit_vj else None)]
    if d["differenz"]:
        out.append(f"\n*** BILANZ NICHT AUSGEGLICHEN: Differenz {eur(d['differenz'])} ***")
    return "\n".join(out)


def guv(con: sqlite3.Connection, gj_id: int, vj_gj_id: int | None = None) -> str:
    d = guv_daten(con, gj_id, vj_gj_id)
    r = rahmen(con, gj_id)
    mit_vj = d["ergebnis_vj"] is not None
    out = [r.titel_guv.upper(), _linie(),
           f"{'':<58}{'Geschäftsjahr':>18}{'Vorjahr':>18}" if mit_vj
           else f"{'':<58}{'Geschäftsjahr':>18}", _linie()]
    out += _gliederungs_text(d["zeilen"], mit_vj)
    out += [_linie(), _zeile(r.titel_ergebnis, d["ergebnis"], 0,
                             d["ergebnis_vj"] if mit_vj else None)]
    return "\n".join(out)


def anlagen(con: sqlite3.Connection, gj_id: int, vj_gj_id: int | None = None) -> str:
    """Alle Anlagen V als Textbericht - je Objekt eine Aufstellung, dann die Summe."""
    d = anlagen_daten(con, gj_id, vj_gj_id)
    if not d["anlagen"]:
        return "Dieser Kontenrahmen kennt keine steuerlichen Anlagen."
    mit_vj = d["ergebnis_vj"] is not None
    out = []
    for a in d["anlagen"]:
        o = a["objekt"]
        kopf = f"{a['titel']} · {o['ort']}"
        angaben = [f"AfA linear {o['afa_satz'] / 100:.2f} %".replace(".", ",")]
        if o["angeschafft"]:
            angaben.append("angeschafft " + ".".join(reversed(o["angeschafft"].split("-"))))
        if o["darlehen"]:
            angaben.append("Darlehen: " + ", ".join(o["darlehen"]))
        out += ["", kopf, _linie(), "  " + " · ".join(angaben), _linie("·")]
        out += _gliederungs_text(a["zeilen"], mit_vj)
        out += [_linie("·"),
                _zeile("Zeile 32  Summe der Einnahmen", a["einnahmen"], 0,
                       a.get("einnahmen_vj") if mit_vj else None),
                _zeile("Zeile 83  Summe der Werbungskosten", a["werbungskosten"], 0,
                       a.get("werbungskosten_vj") if mit_vj else None),
                _zeile("Zeile 85  Überschuss / Verlust", a["ergebnis"], 0,
                       a.get("ergebnis_vj") if mit_vj else None)]
    out += ["", "EINKÜNFTE AUS VERMIETUNG UND VERPACHTUNG (§ 21 EStG)", _linie()]
    for a in d["anlagen"]:
        out.append(_zeile(f"{a['nr']}. {a['objekt']['name']}", a["ergebnis"], 2,
                          a.get("ergebnis_vj") if mit_vj else None))
    out += [_linie(), _zeile("Summe", d["ergebnis"], 0,
                             d["ergebnis_vj"] if mit_vj else None)]
    return "\n".join(out)


def kontoblatt(con: sqlite3.Connection, gj_id: int, nummer: str) -> str:
    rows = con.execute("""
        SELECT b.nummer, b.datum, b.buchungstext, b.belegfeld,
               z.soll_cent, z.haben_cent, z.text
        FROM buchungszeile z JOIN buchung b ON b.id=z.buchung_id
        JOIN konto k ON k.id=z.konto_id JOIN geschaeftsjahr g ON g.id=b.gj_id
        WHERE b.gj_id=? AND k.nummer=? ORDER BY b.datum, b.nummer""", (gj_id, nummer)).fetchall()
    kb = con.execute("SELECT bezeichnung FROM konto WHERE nummer=?", (nummer,)).fetchone()
    out = [f"KONTOBLATT {nummer} {kb['bezeichnung'] if kb else ''}", _linie(),
           f"{'Nr':>5} {'Datum':<11}{'Text':<44}{'Soll':>12}{'Haben':>12}{'Saldo':>13}", _linie()]
    s = 0
    for r in rows:
        s += r["soll_cent"] - r["haben_cent"]
        t = (r["text"] or r["buchungstext"])[:43]
        out.append(f"{r['nummer']:>5} {r['datum']:<11}{t:<44}"
                   f"{eur(r['soll_cent']) if r['soll_cent'] else '':>12}"
                   f"{eur(r['haben_cent']) if r['haben_cent'] else '':>12}{eur(s):>13}")
    out += [_linie(), f"{'Saldo':<73}{eur(s):>23}"]
    return "\n".join(out)


def journal(con: sqlite3.Connection, gj_id: int) -> str:
    rows = con.execute("""
        SELECT b.nummer, b.datum, b.buchungstext, b.belegfeld, k.nummer AS konto,
               z.soll_cent, z.haben_cent
        FROM buchung b JOIN buchungszeile z ON z.buchung_id=b.id
        JOIN konto k ON k.id=z.konto_id WHERE b.gj_id=?
        ORDER BY b.datum, b.nummer, z.id""", (gj_id,)).fetchall()
    out = ["JOURNAL", _linie(),
           f"{'Nr':>5} {'Datum':<11}{'Konto':<7}{'Soll':>13}{'Haben':>13}  {'Text':<30}{'Beleg':<12}",
           _linie()]
    for r in rows:
        out.append(f"{r['nummer']:>5} {r['datum']:<11}{r['konto']:<7}"
                   f"{eur(r['soll_cent']) if r['soll_cent'] else '':>13}"
                   f"{eur(r['haben_cent']) if r['haben_cent'] else '':>13}  "
                   f"{r['buchungstext'][:29]:<30}{r['belegfeld'] or '':<12}")
    return "\n".join(out)


def belegliste(con: sqlite3.Connection, mandant_id: int, jahr: int | None = None) -> str:
    q = ("SELECT belegnr, datum, kategorie, aussteller, bezeichnung, betrag_cent, pfad "
         "FROM beleg WHERE mandant_id=?" + (" AND jahr=?" if jahr else "") +
         " ORDER BY jahr, laufnr")
    rows = con.execute(q, (mandant_id, jahr) if jahr else (mandant_id,)).fetchall()
    out = ["BELEGLISTE", _linie(),
           f"{'Beleg':<12}{'Datum':<12}{'Kategorie':<18}{'Aussteller':<28}{'Betrag':>12}  Bezeichnung",
           _linie()]
    for r in rows:
        out.append(f"{r['belegnr']:<12}{r['datum'] or '':<12}{r['kategorie']:<18}"
                   f"{(r['aussteller'] or '')[:27]:<28}"
                   f"{eur(r['betrag_cent']) if r['betrag_cent'] is not None else '':>12}  "
                   f"{r['bezeichnung'][:60]}")
    out += [_linie(), f"{len(rows)} Belege"]
    return "\n".join(out)
