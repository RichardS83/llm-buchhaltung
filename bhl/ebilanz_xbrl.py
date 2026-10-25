# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""E-Bilanz als XBRL-Datensatz für ERiC (Verfahren ElsterBilanz).

`ebilanz.py` ordnet die Konten den Taxonomiepositionen zu und prüft die
Zuordnung; dieses Modul baut daraus den Datensatz, den ERiC an die
Finanzverwaltung übermittelt:

* **GCD** (Stammdaten): Unternehmen, Wirtschaftsjahr, Berichtsbestandteile.
  Übermittelt wird eine *Steuerbilanz* (accountingStandard AO) — für eine
  Gesellschaft ohne Abweichungen zwischen Handels- und Steuerbilanz entfällt
  damit die Überleitungsrechnung.
* **GAAP**: Bilanz und GuV mit allen Oberpositionen, der Betriebsvermögens-
  vergleich und der seit Wirtschaftsjahren ab 2025 verpflichtende
  Kontennachweis (`detailedInformation.accountBalances`).
* **Mussfelder**, die die Buchführung nicht kennt, werden leer (NIL)
  berichtet — so verlangt es die Taxonomie.

Welche Positionen Mussfelder sind und welche Oberposition eine Position hat,
steht nicht im Code, sondern in `taxonomie/ebilanz_<version>.json`. Die Datei
ist gegen das ERiC-Plugin der Taxonomie ermittelt (tools/ebilanz_mussfelder.py),
nicht von Hand geschrieben.

Beträge sind ganze Cent; das Vorzeichen eines Kontos ergibt sich aus der
Rechenrichtung seiner Position: Aufwand steht positiv und wird abgezogen,
Ertrag steht positiv und wird addiert.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from . import db, eric_xml, fristen, reports
from . import ebilanz as ebilanz_mod
from .ledger import salden

TAXONOMIE = "6.9"
JAHRESERGEBNIS = ebilanz_mod.JAHRESERGEBNIS
REIHE_IDNO = ("HRN", "UID", "ST13", "STID", "STWID", "beWiNr", "kleinU-ID", "BF4", "BKN", "BUN",
              "IN", "EN", "SN", "LEI", "customerID", "S")


class EBilanzFehler(ValueError):
    pass


def taxonomie(version: str = TAXONOMIE) -> dict:
    pfad = os.path.join(os.path.dirname(__file__), "taxonomie",
                        f"ebilanz_{version.replace('.', '_')}.json")
    with open(pfad, encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class Abschluss:
    """Die werthaltigen Positionen eines Jahres, so wie sie berichtet werden."""
    werte: dict[str, int | None]                  # Position -> Cent, None = leer
    konten: list[tuple[str, str, str, int]]       # (Konto, Bezeichnung, Position, Cent)
    ergebnis: int                                 # Jahresergebnis, Gewinn positiv
    eigenkapital: int
    bilanzsumme: int
    fehlend: list[str] = field(default_factory=list)


def _elter(el: str, tax: dict, bekannt: set[str]) -> str | None:
    if el in tax["eltern"]:
        return tax["eltern"][el]
    teile = el.split(".")
    for i in range(len(teile) - 1, 1, -1):
        p = ".".join(teile[:i])
        if p in bekannt:
            return p
    return None


def _richtung(el: str, tax: dict, bekannt: set[str]) -> int:
    """+1, wenn die Position in ihre Summe (Aktiva, Passiva, Jahresergebnis)
    addiert wird, -1, wenn sie abgezogen wird."""
    r = 1
    while el:
        r *= tax["gewicht"].get(el, 1)
        el = _elter(el, tax, bekannt)
    return r


def abschluss(con: sqlite3.Connection, gj_id: int, mandant_dir: str,
              tax: dict | None = None) -> Abschluss:
    """Positionen und Kontennachweis aus den Konten und `ebilanz.csv`."""
    tax = tax or taxonomie()
    zuordnung = ebilanz_mod.laden(mandant_dir)
    if zuordnung is None:
        raise EBilanzFehler("Keine Taxonomie-Zuordnung: mandanten/<kürzel>/ebilanz.csv fehlt.")
    return _auswerten(salden(con, gj_id), zuordnung, reports.ergebnis(con, gj_id), tax)


def _auswerten(zeilen, zuordnung: dict, ergebnis: int, tax: dict, *,
               umbuchen: dict[str, tuple[str, int]] | None = None,
               nur_bilanz: bool = False) -> Abschluss:
    """Kontensalden -> Positionen, Oberpositionen und Kontennachweis.

    `umbuchen` {Konto: (Position, Cent)} legt einen Teil eines Kontos auf eine
    andere Position — so beim eingeforderten Teil der ausstehenden Einlagen in
    der Eröffnungsbilanz. `nur_bilanz` lässt GuV-Mussfelder weg."""
    bekannt = set(tax["positionen"])
    umbuchen = umbuchen or {}
    summen: dict[str, int] = {}
    konten, fehlend = [], []
    for r in zeilen:
        if not r["saldo"]:
            continue
        z = zuordnung.get(r["nummer"])
        if not z:
            fehlend.append(r["nummer"])
            continue
        el = z["element"]
        saldo = r["saldo"]
        teile = []
        if r["nummer"] in umbuchen:
            ziel, anteil = umbuchen[r["nummer"]]
            # Der umgebuchte Teil wird mit dem Vorzeichen des Kontos abgezogen
            anteil = anteil if saldo > 0 else -anteil
            if abs(anteil) > abs(saldo):
                raise EBilanzFehler(f"Konto {r['nummer']}: {_betrag(abs(anteil))} umzubuchen, "
                                    f"aber nur {_betrag(abs(saldo))} vorhanden.")
            teile.append((ziel, anteil, TEILE.get(ziel, "")))
            saldo -= anteil
        if saldo:
            teile.append((el, saldo, TEILE.get(el, "") if len(teile) else ""))
        for ziel, betrag_soll, zusatz in teile:
            # Soll-Saldo positiv. Aktiva und Aufwand behalten ihn, Passiva und
            # Ertrag drehen ihn; ein Gegenposten (Richtung -1) dreht ein zweites Mal.
            haben_seite = ziel.startswith("bs.eqLiab") or ziel.startswith("is.")
            betrag = betrag_soll * _richtung(ziel, tax, bekannt) * (-1 if haben_seite else 1)
            summen[ziel] = summen.get(ziel, 0) + betrag
            konten.append((r["nummer"], r["bezeichnung"] + zusatz, ziel, betrag))
    if fehlend:
        raise EBilanzFehler(f"Konten ohne Taxonomieposition in ebilanz.csv: {', '.join(fehlend)}")
    if not nur_bilanz:
        summen[JAHRESERGEBNIS] = summen.get(JAHRESERGEBNIS, 0) + ergebnis

    werte: dict[str, int | None] = dict(summen)
    for el in tax["mussfelder"]:
        if not el.startswith("genInfo") and not (nur_bilanz and not el.startswith("bs.")):
            werte.setdefault(el, None)
    offen = list(werte)
    while offen:
        p = _elter(offen.pop(), tax, bekannt)
        if p and p not in werte:
            werte[p] = None
            offen.append(p)
    kinder: dict[str, list[str]] = {}
    for el in werte:
        p = _elter(el, tax, bekannt)
        if p:
            kinder.setdefault(p, []).append(el)

    def wert(el: str) -> int | None:
        if el in summen:
            return summen[el]
        ws = [(k, wert(k)) for k in kinder.get(el, [])]
        ws = [(k, w) for k, w in ws if w is not None]
        return sum(w * tax["gewicht"].get(k, 1) for k, w in ws) if ws else None

    for el in list(werte):
        werte[el] = wert(el)
    aktiva, passiva = werte.get("bs.ass") or 0, werte.get("bs.eqLiab") or 0
    if aktiva != passiva:
        raise EBilanzFehler(f"Aktiva {_betrag(aktiva)} und Passiva {_betrag(passiva)} weichen ab.")
    if not nur_bilanz and (werte.get("is.netIncome") or 0) != ergebnis:
        raise EBilanzFehler(f"GuV-Ergebnis {_betrag(werte.get('is.netIncome') or 0)} und "
                            f"Jahresergebnis {_betrag(ergebnis)} weichen ab.")
    eigenkapital = (werte.get("bs.eqLiab.equity") or 0) - (werte.get("bs.ass.deficitNotCoveredByCapital") or 0)
    return Abschluss(werte, konten, ergebnis, eigenkapital, aktiva)


# Eingeforderte, noch nicht eingezahlte Einlagen (§ 272 Abs. 1 Satz 3 HGB)
EINGEFORDERT = "bs.ass.currAss.receiv.other.unpaidCapital"
NICHT_EINGEFORDERT = "bs.eqLiab.equity.subscribed.unpaidCap"
# Wird ein Konto auf zwei Positionen verteilt, sagt der Kontonachweis, welcher Teil wohin geht
TEILE = {EINGEFORDERT: ", eingefordert", NICHT_EINGEFORDERT: ", nicht eingefordert"}


def _eb_zeilen(con: sqlite3.Connection, gj_id: int, datum: str | None = None) -> list:
    """Kontensalden der Eröffnungsbuchungen (`art = eb`), wahlweise nur eines Tages."""
    return con.execute(f"""
      SELECT k.nummer, k.bezeichnung, SUM(z.soll_cent) - SUM(z.haben_cent) AS saldo
      FROM buchungszeile z JOIN buchung b ON b.id = z.buchung_id
      JOIN konto k ON k.id = z.konto_id
      WHERE b.gj_id = ? AND b.art = 'eb'{" AND b.datum = ?" if datum else ""}
      GROUP BY k.id ORDER BY k.nummer""", (gj_id, datum) if datum else (gj_id,)).fetchall()


def eigenkapital_vorjahr(con: sqlite3.Connection, gj_id: int, mandant_dir: str, stamm: dict,
                         tax: dict | None = None) -> int:
    """Eigenkapital am Ende des vorangegangenen Wirtschaftsjahres — der Anfang
    des Betriebsvermögensvergleichs.

    * Steht das Vorjahr in der Datenbank, kommt es aus dessen Abschluss.
    * Im Gründungsjahr (Jahr des Gesellschaftsvertrags, wie bei der
      Eröffnungsbilanz) gibt es kein Vorjahr: 0. Die Eröffnungsbuchung ist hier
      die Gründung selbst, und was an Kapital eingezahlt ist, ist Einlage.
    * Sonst beginnt nur die Buchführung in `bh` später als die Gesellschaft.
      Dann tragen die Eröffnungsbuchungen das Eigenkapital des Vorjahres
      (Bilanzenzusammenhang, § 252 Abs. 1 Nr. 1 HGB, § 4 Abs. 1 EStG) — ohne
      sie stünde das ganze Anfangskapital als Einlage im Vergleich.
    """
    tax = tax or taxonomie()
    gj = con.execute("SELECT mandant_id, jahr FROM geschaeftsjahr WHERE id=?", (gj_id,)).fetchone()
    vorjahr = con.execute("SELECT id FROM geschaeftsjahr WHERE mandant_id=? AND jahr=?",
                          (gj["mandant_id"], gj["jahr"] - 1)).fetchone()
    if vorjahr:
        return abschluss(con, vorjahr["id"], mandant_dir, tax).eigenkapital
    if fristen.eroeffnungsbilanz_im_jahr(stamm, gj["jahr"]):
        return 0
    zeilen = _eb_zeilen(con, gj_id)
    if not zeilen:
        return 0
    zuordnung = ebilanz_mod.laden(mandant_dir)
    if zuordnung is None:
        raise EBilanzFehler("Keine Taxonomie-Zuordnung: mandanten/<kürzel>/ebilanz.csv fehlt.")
    try:
        return _auswerten(zeilen, zuordnung, 0, tax, nur_bilanz=True).eigenkapital
    except EBilanzFehler as e:
        raise EBilanzFehler(f"Eigenkapital zu Beginn {gj['jahr']} aus den Eröffnungsbuchungen "
                            f"(kein Vorjahr in der Datenbank, nicht das Gründungsjahr): {e}"
                            ) from None


def eroeffnung(con: sqlite3.Connection, gj_id: int, mandant_dir: str, stamm: dict,
               tax: dict | None = None) -> Abschluss:
    """Die Eröffnungsbilanz (§ 5b Abs. 1 Satz 4 EStG): die Eröffnungsbuchungen
    am ersten Tag des Wirtschaftsjahres, ohne GuV.

    Das Konto der ausstehenden Einlagen hält bei Gründung das ganze
    Stammkapital. Was der Gesellschaftsvertrag sofort fällig stellt, ist
    eingefordert und gehört als Forderung auf die Aktivseite; nur der Rest wird
    offen vom gezeichneten Kapital abgesetzt. Den eingeforderten Betrag kennt
    die Buchführung nicht — er steht in mandant.json unter `einlagen`."""
    tax = tax or taxonomie()
    zuordnung = ebilanz_mod.laden(mandant_dir)
    if zuordnung is None:
        raise EBilanzFehler("Keine Taxonomie-Zuordnung: mandanten/<kürzel>/ebilanz.csv fehlt.")
    beginn = con.execute("SELECT beginn FROM geschaeftsjahr WHERE id=?", (gj_id,)).fetchone()[0]
    zeilen = _eb_zeilen(con, gj_id, beginn)
    if not zeilen:
        raise EBilanzFehler(f"Keine Eröffnungsbuchungen am {beginn}.")
    umbuchen = {}
    e = stamm.get("einlagen") or {}
    if e.get("eingefordert_bei_gruendung"):
        konto = next((nr for nr, z in zuordnung.items() if z["element"] == NICHT_EINGEFORDERT),
                     None)
        if not konto:
            raise EBilanzFehler(f"Kein Konto mit {NICHT_EINGEFORDERT} in ebilanz.csv.")
        umbuchen[konto] = (EINGEFORDERT, db.cent(e["eingefordert_bei_gruendung"]))
    return _auswerten(zeilen, zuordnung, 0, tax, umbuchen=umbuchen, nur_bilanz=True)


def _gcd(stamm: dict, gj: dict, steuernummer: str, bufa: str, heute: datetime.date,
         tax: dict, eroeffnung: bool = False) -> str:
    D = 'contextRef="D-AKTJAHR"'

    def w(el, inhalt=None, nil=False):
        if nil:
            return f'<de-gcd:{el} xsi:nil="true" {D}/>'
        if inhalt is None:
            return f'<de-gcd:{el} {D}/>'
        return f'<de-gcd:{el} {D}>{escape(str(inhalt))}</de-gcd:{el}>'

    def tupel(el, *innen):
        return f'<de-gcd:{el}>' + "".join(innen) + f'</de-gcd:{el}>'

    rechtsform = stamm.get("rechtsform") or ""
    # Die UG ist eine GmbH (§ 5a GmbHG) und wird als solche berichtet.
    rf = (("GMBH", "GmbH") if fristen.ist_gmbh(rechtsform)
          else ("AG", "AG") if rechtsform in ("AG", "Aktiengesellschaft") else None)
    if not rf:
        raise EBilanzFehler(f"Rechtsform {stamm.get('rechtsform')!r} ist für die E-Bilanz "
                            "noch nicht hinterlegt.")
    strasse, hausnr, zusatz = eric_xml.strasse_hausnummer(stamm.get("strasse") or "")
    widnr = (stamm.get("widnr") or "").replace(" ", "")
    ids = {"ST13": steuernummer, "BF4": bufa}
    if stamm.get("registernummer"):
        ids["HRN"] = stamm["registernummer"]
    if stamm.get("ust_idnr"):
        ids["UID"] = stamm["ust_idnr"]
    if widnr:
        ids["STWID"] = widnr
    gcd_nil = [m for m in tax["mussfelder"] if m.startswith("genInfo")]
    leer_ids = {m.rsplit(".", 1)[1] for m in gcd_nil
                if m.startswith("genInfo.company.id.idNo.type.companyId.")}
    idno = tupel("genInfo.company.id.idNo", *(
        w(f"genInfo.company.id.idNo.type.companyId.{k}", ids[k]) if k in ids
        else w(f"genInfo.company.id.idNo.type.companyId.{k}", nil=True) if k in leer_ids else ""
        for k in REIHE_IDNO))
    eigene = ("genInfo.company.id.idNo", "genInfo.company.id.shareholder",
              "genInfo.report.id.reportElement", "genInfo.report.id.accordingTo",
              "genInfo.report.id.specialAccountingStandard")
    weitere = []
    for m in gcd_nil:
        if m.startswith(eigene):
            continue
        kind = tax["gcd_tupel"].get(m)
        weitere.append(tupel(m, w(kind, nil=True)) if kind else w(m, nil=True))
    teile = [
        w("genInfo.doc.id.generationDate", heute.isoformat()),
        tupel("genInfo.report.id.reportStatus", w("genInfo.report.id.reportStatus.reportStatus.E", "endgültig")),
        tupel("genInfo.report.id.revisionStatus", w("genInfo.report.id.revisionStatus.revisionStatus.E", "erstmalig")),
        # Eröffnungsbilanz wie im Beispiel des SDK: Bilanz und „Eröffnungsbilanz
        # ohne GuV“, GuV und Betriebsvermögensvergleich leer
        tupel("genInfo.report.id.reportElement",
              w("genInfo.report.id.reportElement.reportElements.B", "Bilanz"),
              w("genInfo.report.id.reportElement.reportElements.EB", "Eröffnungsbilanz")
              if eroeffnung else
              w("genInfo.report.id.reportElement.reportElements.GuV", "GuV"),
              w("genInfo.report.id.reportElement.reportElements.KS"),
              w("genInfo.report.id.reportElement.reportElements.BVV", "Betriebsvermögensvergleich")
              if not eroeffnung else "",
              *(w(f"genInfo.report.id.reportElement.reportElements.{k}", nil=True)
                for k in (("GuV", "BVV", "BAL", "EV", "STU") if eroeffnung
                          else ("EB", "BAL", "EV", "STU")))),
        w("genInfo.report.id.reportElement.reportElements.transmissionNotYetPossible", nil=True),
        tupel("genInfo.report.id.specialAccountingStandard",
              w("genInfo.report.id.specialAccountingStandard.K")),
        tupel("genInfo.report.id.accordingTo",
              w("genInfo.report.id.accordingTo.name", nil=True),
              tupel("genInfo.report.id.accordingTo.idNo",
                    w("genInfo.company.id.idNo.type.companyId.ST13", nil=True),
                    w("genInfo.company.id.idNo.type.companyId.BF4", nil=True)),
              w("genInfo.report.id.accordingTo.yearEnd", nil=True)),
        tupel("genInfo.report.id.statementType",
              w("genInfo.report.id.statementType.statementType.EB", "Eröffnungsbilanz")
              if eroeffnung else
              w("genInfo.report.id.statementType.statementType.E", "Jahresabschluss")),
        w("genInfo.report.id.incomeStatementendswithBalProfit", "false"),
        tupel("genInfo.report.id.accountingStandard",
              w("genInfo.report.id.accountingStandard.accountingStandard.AO")),
        tupel("genInfo.report.id.incomeStatementFormat",
              w("genInfo.report.id.incomeStatementFormat.incomeStatementFormat.GKV", "Gesamtkostenverfahren")),
        tupel("genInfo.report.id.consolidationRange",
              w("genInfo.report.id.consolidationRange.consolidationRange.EA", "Einzelabschluss")),
        w("genInfo.report.period.balSheetClosingDate", gj["beginn"] if eroeffnung else gj["ende"]),
        w("genInfo.report.period.fiscalYearBegin", gj["beginn"]),
        w("genInfo.report.period.fiscalYearEnd", gj["ende"]),
        w("genInfo.company.id.name", stamm["name"]),
        tupel("genInfo.company.id.legalStatus",
              w(f"genInfo.company.id.legalStatus.legalStatus.{rf[0]}", rf[1])),
        w("genInfo.company.id.location", stamm.get("ort")),
        w("genInfo.company.id.location.street", strasse),
        w("genInfo.company.id.location.houseNo", (hausnr or "") + (zusatz or "")),
        w("genInfo.company.id.location.zipCode", stamm.get("plz")),
        w("genInfo.company.id.location.city", stamm.get("ort")),
        w("genInfo.company.id.location.country", "Deutschland"),
        idno,
    ]
    if any(m.startswith("genInfo.company.id.shareholder") for m in gcd_nil):
        teile.append(tupel(
            "genInfo.company.id.shareholder",
            *(w(f"genInfo.company.id.shareholder.{k}", nil=True)
              for k in ("name", "id", "taxnumber", "taxid", "WID")),
            tupel("genInfo.company.id.shareholder.legalStatus",
                  w("genInfo.company.id.shareholder.legalStatus.legalStatus.NPP", nil=True)),
            tupel("genInfo.company.id.shareholder.ShareDivideKey",
                  w("genInfo.company.id.shareholder.ShareDivideKey.numerator", nil=True),
                  w("genInfo.company.id.shareholder.ShareDivideKey.denominator", nil=True))))
    teile.append(tupel("genInfo.company.id.incomeClassification",
                       w("genInfo.company.id.incomeClassification.trade")))
    return "\n".join(teile + weitere)


def _betrag(c: int) -> str:
    vz = "-" if c < 0 else ""
    return f"{vz}{abs(c) // 100}.{abs(c) % 100:02d}"


def xbrl(a: Abschluss, *, stamm: dict, gj: dict, steuernummer: str, bufa: str,
         eigenkapital_vorjahr: int = 0, heute: datetime.date | None = None,
         tax: dict | None = None, eroeffnung: bool = False) -> str:
    """Die XBRL-Instanz (Inhalt von <ebilanz:EBilanz>)."""
    tax = tax or taxonomie()
    heute = heute or datetime.date.today()

    def fakt(el, c, ctx):
        if c is None:
            return f'<de-gaap-ci:{el} xsi:nil="true" contextRef="{ctx}" unitRef="EUR"/>'
        return (f'<de-gaap-ci:{el} decimals="2" contextRef="{ctx}" unitRef="EUR">'
                f'{_betrag(c)}</de-gaap-ci:{el}>')

    fakten = [fakt(el, c, "I-AKTJAHR" if el.startswith("bs.") else "D-AKTJAHR")
              for el, c in sorted(a.werte.items())]
    # Betriebsvermögensvergleich: Gewinn = EK Ende - EK Anfang + Entnahmen - Einlagen.
    # Einlagen und Entnahmen sind, was die Eigenkapitaländerung nicht aus dem
    # Ergebnis erklärt. EK Anfang liefert `eigenkapital_vorjahr()` — auch dann,
    # wenn das Vorjahr nicht in der Datenbank steht.
    rest = a.eigenkapital - eigenkapital_vorjahr - a.ergebnis
    einlagen, entnahmen = (rest, None) if rest > 0 else (None, -rest if rest else None)
    for el, c in () if eroeffnung else (("BVV.profitLoss", a.ergebnis),
                  ("BVV.profitLoss.assetsCurrentYear", a.eigenkapital),
                  ("BVV.profitLoss.assetsPreviousYear", eigenkapital_vorjahr),
                  ("BVV.profitLoss.assetsPreviousYear.assets", eigenkapital_vorjahr),
                  ("BVV.profitLoss.assetsPreviousYear.capAdjustments", None),
                  ("BVV.profitLoss.contribution", einlagen),
                  ("BVV.profitLoss.withdrawalDistrib", entnahmen)):
        fakten.append(fakt(el, c, "D-AKTJAHR"))
    D = 'contextRef="D-AKTJAHR"'
    nachweis = [
        "<de-gaap-ci:detailedInformation.accountBalances>"
        f"<de-gaap-ci:detailedInformation.accountbalances.positionName {D}>{el}</de-gaap-ci:detailedInformation.accountbalances.positionName>"
        f"<de-gaap-ci:detailedInformation.accountbalances.accountNumber {D}>{escape(nr)}</de-gaap-ci:detailedInformation.accountbalances.accountNumber>"
        f"<de-gaap-ci:detailedInformation.accountbalances.accountDescription {D}>{escape(bez)}</de-gaap-ci:detailedInformation.accountbalances.accountDescription>"
        f'<de-gaap-ci:detailedInformation.accountbalances.amount decimals="2" {D} unitRef="EUR">{_betrag(c)}</de-gaap-ci:detailedInformation.accountbalances.amount>'
        "</de-gaap-ci:detailedInformation.accountBalances>"
        for nr, bez, el, c in a.konten]
    gaap, gcd = tax["namensraum_gaap"], tax["namensraum_gcd"]
    stand = gaap.rsplit("-", 3)[-3:]
    gaap_shell = f"{gaap}/de-gaap-ci-{'-'.join(stand)}-shell-fiscal.xsd"
    gcd_shell = f"{gcd}/de-gcd-{'-'.join(stand)}-shell.xsd"
    ident = (f'<xbrli:entity><xbrli:identifier scheme="http://www.rzf-nrw.de/Steuernummer">'
             f'{steuernummer}</xbrli:identifier></xbrli:entity>')
    return "\n".join([
        f'<xbrli:xbrl xmlns:de-gcd="{gcd}" xmlns:de-gaap-ci="{gaap}" '
        'xmlns:link="http://www.xbrl.org/2003/linkbase" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
        'xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:xlink="http://www.w3.org/1999/xlink">',
        f'<link:schemaRef xlink:type="simple" xlink:href="{gcd_shell}"/>',
        f'<link:schemaRef xlink:type="simple" xlink:href="{gaap_shell}"/>',
        f'<xbrli:context id="I-AKTJAHR">{ident}<xbrli:period><xbrli:instant>'
        f'{gj["beginn"] if eroeffnung else gj["ende"]}</xbrli:instant></xbrli:period></xbrli:context>',
        f'<xbrli:context id="D-AKTJAHR">{ident}<xbrli:period><xbrli:startDate>{gj["beginn"]}'
        f'</xbrli:startDate><xbrli:endDate>{gj["ende"]}</xbrli:endDate></xbrli:period></xbrli:context>',
        '<xbrli:unit id="EUR"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>',
        _gcd(stamm, gj, steuernummer, bufa, heute, tax, eroeffnung),
        *fakten,
        *nachweis,
        "</xbrli:xbrl>",
    ])


def datensatz(a: Abschluss, *, stamm: dict, gj: dict, steuernummer: str, bufa: str,
              hersteller_id: str, produkt: str, produktversion: str,
              testmerker: str | None = None, eigenkapital_vorjahr: int = 0,
              heute: datetime.date | None = None, tax: dict | None = None,
              eroeffnung: bool = False) -> str:
    """Der vollständige ElsterXML-Datensatz der Datenart Bilanz."""
    tax = tax or taxonomie()
    inhalt = xbrl(a, stamm=stamm, gj=gj, steuernummer=steuernummer, bufa=bufa,
                  eigenkapital_vorjahr=eigenkapital_vorjahr, heute=heute, tax=tax,
                  eroeffnung=eroeffnung)
    stichtag = gj["beginn"] if eroeffnung else gj["ende"]
    test = f"<Testmerker>{testmerker}</Testmerker>" if testmerker else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Elster xmlns="http://www.elster.de/elsterxml/schema/v11"><TransferHeader version="11">'
        "<Verfahren>ElsterBilanz</Verfahren><DatenArt>Bilanz</DatenArt><Vorgang>send-Auth</Vorgang>"
        f"{test}<HerstellerID>{escape(hersteller_id)}</HerstellerID>"
        f"<DatenLieferant>{escape(stamm['name'][:256])}</DatenLieferant>"
        "<Datei><Verschluesselung>CMSEncryptedData</Verschluesselung><Kompression>GZIP"
        "</Kompression><TransportSchluessel/></Datei></TransferHeader>\n"
        '<DatenTeil><Nutzdatenblock><NutzdatenHeader version="11"><NutzdatenTicket>1</NutzdatenTicket>'
        f'<Empfaenger id="F">{bufa}</Empfaenger><Hersteller><ProduktName>{escape(produkt)}'
        f"</ProduktName><ProduktVersion>{escape(produktversion)}</ProduktVersion></Hersteller>"
        "</NutzdatenHeader>\n<Nutzdaten>"
        '<ebilanz:EBilanz version="000002" xmlns:ebilanz="http://rzf.fin-nrw.de/RMS/EBilanz/2016/XMLSchema">'
        f'<ebilanz:stichtag>{stichtag.replace("-", "")}</ebilanz:stichtag>\n'
        f"{inhalt}\n</ebilanz:EBilanz></Nutzdaten></Nutzdatenblock></DatenTeil></Elster>\n")
