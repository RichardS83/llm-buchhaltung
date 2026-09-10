"""Die auf einer Eingangsrechnung *gedruckte* Umsatzsteuer lesen.

Warum das nötig ist: eine Vorsteuer, die als 19 % vom Bruttobetrag zurückgerechnet
wird, ist falsch, sobald eine Rechnung steuerfreie Posten mit steuerpflichtigen
mischt. Die Gründungsrechnung von Q58 ist das Beispiel — 9,00 € umsatzsteuerfreie
Auslagen (Gerichtskosten), weshalb auf der Rechnung **157,23 €** stehen und nicht
die 158,67 €, die eine Rückrechnung ergäbe. Die Vorsteuer ist der einzige
Eingangsposten, der die Zahllast wirklich mindert; sie muss stimmen.

Das Verfahren ist bewusst vorsichtig: alle Beträge der Rechnung einsammeln, am
Zahlbetrag verankern, und eine Zahl nur dann als Umsatzsteuer annehmen, wenn
`netto + Steuer = brutto` aufgeht *und* sie neben einem Stichwort wie
„Umsatzsteuer" oder „MwSt" steht. Wird nichts sicher erkannt, kommt None zurück —
lieber keine Aussage als eine erfundene.
"""

from __future__ import annotations

import re

from .belege import volltext

# Deutsche Beträge: „993,78", „1.234,56" — immer genau zwei Nachkommastellen.
RE_BETRAG = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+),(\d{2})(?![\d])")
RE_STICHWORT = re.compile(r"umsatzsteuer|mehrwertsteuer|mwst|ust\b|vat\b", re.IGNORECASE)

# Wie nah die Steuerzahl am Stichwort stehen muss, in Zeichen des ausgelesenen
# Textes. Grosszuegig, weil pdftotext -layout Spalten mit Leerraum fuellt.
MAX_ABSTAND = 180


def _cent(m: re.Match) -> int:
    return int(m.group(1).replace(".", "")) * 100 + int(m.group(2))


def aus_text(text: str, brutto_cent: int) -> tuple[int, str] | None:
    """(steuer_cent, Fundstelle) oder None, wenn nichts sicher erkennbar ist."""
    if brutto_cent <= 0 or not text:
        return None
    treffer = list(RE_BETRAG.finditer(text))
    betraege = {_cent(m) for m in treffer}
    if brutto_cent not in betraege:
        return None                     # Rechnungssumme passt nicht zur Zahlung
    stichworte = [m.start() for m in RE_STICHWORT.finditer(text)]
    if not stichworte:
        return None

    bester: tuple[int, int, str] | None = None      # (Abstand, Steuer, Fundstelle)
    for m in treffer:
        steuer = _cent(m)
        netto = brutto_cent - steuer
        if not 0 < steuer < brutto_cent:
            continue
        if netto not in betraege:
            continue                    # netto + Steuer muss den Bruttobetrag ergeben
        if steuer * 5 > brutto_cent:
            continue                    # mehr als 20 % des Brutto: keine Steuerzeile
        abstand = min(abs(m.start() - s) for s in stichworte)
        if abstand > MAX_ABSTAND:
            continue
        if bester is None or abstand < bester[0]:
            fund = " ".join(text[max(0, m.start() - 45): m.start() + 12].split())
            bester = (abstand, steuer, fund)

    return (bester[1], bester[2]) if bester else None


def aus_beleg(pfad: str, brutto_cent: int) -> tuple[int, str] | None:
    """Wie `aus_text`, aber liest den Beleg aus dem Archiv."""
    return aus_text(volltext(pfad, max_zeichen=20000), brutto_cent)
