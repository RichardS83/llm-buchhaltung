"""Druckfassung der Dokumente: Markdown -> HTML -> PDF.

Die Oberflaeche zeigt Jahresabschluss, Beschluesse und Erklaerungen als
Markdown. Unterschrieben, hinterlegt und abgeheftet wird aber ein PDF. Damit
beide nicht auseinanderlaufen, entsteht das PDF aus genau demselben Text: der
Umsetzer hier ist eine Abschrift von `markdown()` in static/app.js und kennt
denselben Ausschnitt (Ueberschriften, Tabellen, Listen, Zitate, Trennlinien,
fett/kursiv/Code). Zwei Trennlinien direkt hintereinander (`---` / `---`)
werden im Druck zum Seitenumbruch.

Gedruckt wird mit einem Chrome oder Chromium im Headless-Modus - einer neuen
Abhaengigkeit in Python bedarf es dafuer nicht. Gefunden wird der Browser ueber
die Umgebungsvariable BH_CHROME, sonst an den ueblichen Orten.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import shutil
import subprocess
import tempfile

CHROME_ORTE = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
]

SEITENUMBRUCH = '<div class="umbruch"></div>'


class DruckFehler(Exception):
    """Das PDF liess sich nicht erzeugen."""


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(^|[\s(])\*([^*\n]+)\*", r"\1<em>\2</em>", s)
    # Verweise bleiben Text: im PDF fuehrt ein relativer Link ins Leere.
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", s)
    return s


def _zeilen(teile: list[str]) -> str:
    """Zeilen eines Absatzes: endet eine auf `\\` oder zwei Leerzeichen, bricht sie um.

    Erst zusammenfuegen, dann formatieren - sonst endet Fettdruck, der ueber
    zwei Quellzeilen laeuft, am Zeilenende.
    """
    text = ""
    for n, t in enumerate(teile):
        umbruch = t.endswith("\\") or t.endswith("  ")
        text += t.rstrip("\\").rstrip()
        if n < len(teile) - 1:
            text += "\x00" if umbruch else " "
    return _inline(text).replace("\x00", "<br>")


def _zellen(zeile: str) -> list[str]:
    zeile = re.sub(r"^\s*\|", "", zeile)
    zeile = re.sub(r"\|\s*$", "", zeile)
    return [c.strip() for c in zeile.split("|")]


def markdown_html(text: str) -> str:
    """Derselbe Ausschnitt wie markdown() in app.js - siehe Modulkopf."""
    zeilen = text.replace("\r", "").split("\n")
    aus: list[str] = []
    liste = None
    i = 0

    def liste_zu():
        nonlocal liste
        if liste:
            aus.append(f"</{liste}>")
            liste = None

    while i < len(zeilen):
        z = zeilen[i]
        if not z.strip():
            liste_zu()
            i += 1
            continue
        if re.match(r"^(---+|===+)\s*$", z):
            liste_zu()
            if aus and aus[-1] == "<hr>":
                aus[-1] = SEITENUMBRUCH
            elif aus and aus[-1] != SEITENUMBRUCH:
                aus.append("<hr>")
            i += 1
            continue
        h = re.match(r"^(#{1,6})\s+(.*)$", z)
        if h:
            liste_zu()
            n = len(h.group(1))
            aus.append(f"<h{n}>{_inline(h.group(2))}</h{n}>")
            i += 1
            continue
        folge = zeilen[i + 1] if i + 1 < len(zeilen) else ""
        if "|" in z and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", folge):
            liste_zu()
            kopf = _zellen(z)
            bund = [' class="z"' if c.endswith(":") and not c.startswith(":") else ""
                    for c in _zellen(folge)]
            i += 2
            rumpf = []
            while i < len(zeilen) and "|" in zeilen[i]:
                rumpf.append(_zellen(zeilen[i]))
                i += 1
            b = lambda n: bund[n] if n < len(bund) else ""          # noqa: E731
            leer = all(not c for c in kopf)
            th = "" if leer else "<thead><tr>" + "".join(
                f"<th{b(n)}>{_inline(c)}</th>" for n, c in enumerate(kopf)) + "</tr></thead>"
            td = "".join("<tr>" + "".join(f"<td{b(n)}>{_inline(c)}</td>"
                                          for n, c in enumerate(r)) + "</tr>" for r in rumpf)
            aus.append(f"<table>{th}<tbody>{td}</tbody></table>")
            continue
        li = re.match(r"^\s*([-*+]|\d+\.)\s+(.*)$", z)
        if li:
            art = "ol" if li.group(1)[0].isdigit() else "ul"
            if liste != art:
                liste_zu()
                aus.append(f"<{art}>")
                liste = art
            teile = [li.group(2)]
            i += 1
            # eingerueckte Folgezeilen gehoeren noch zum Listenpunkt
            while (i < len(zeilen) and re.match(r"^\s+\S", zeilen[i])
                   and not re.match(r"^\s*([-*+]|\d+\.)\s+", zeilen[i])):
                teile.append(zeilen[i].strip())
                i += 1
            aus.append(f"<li>{_zeilen(teile)}</li>")
            continue
        if re.match(r"^>\s?", z):
            liste_zu()
            teile = []
            while i < len(zeilen) and re.match(r"^>\s?", zeilen[i]):
                teile.append(re.sub(r"^>\s?", "", zeilen[i]))
                i += 1
            aus.append(f"<blockquote>{_inline(' '.join(teile))}</blockquote>")
            continue
        liste_zu()
        absatz = [z]
        i += 1
        while (i < len(zeilen) and zeilen[i].strip()
               and not re.match(r"^(#{1,6}\s|>|\s*[-*+]\s|\s*\d+\.\s|---)", zeilen[i])
               and "|" not in zeilen[i]):
            absatz.append(zeilen[i])
            i += 1
        text_absatz = " ".join(absatz)
        # Eine Zeile nur aus &nbsp; ist Luft fuer die Unterschrift, kein Text.
        if text_absatz.strip() == "&nbsp;":
            aus.append('<p class="luft">&nbsp;</p>')
        else:
            aus.append(f"<p>{_zeilen(absatz)}</p>")
    liste_zu()
    return "\n".join(aus)


STIL = """
@page { size: A4; margin: 20mm 20mm 18mm 22mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; color: #111; font: 10pt/1.45 "Helvetica Neue", Helvetica, Arial, sans-serif;
       hyphens: auto; }
h1 { font-size: 16pt; margin: 0 0 10pt; }
h2 { font-size: 12.5pt; margin: 18pt 0 6pt; padding-bottom: 3pt; border-bottom: 0.6pt solid #888; }
h3 { font-size: 10.5pt; margin: 12pt 0 4pt; }
h4 { font-size: 10pt; margin: 10pt 0 4pt; }
h1, h2, h3, h4 { break-after: avoid; page-break-after: avoid; }
p { margin: 0 0 7pt; orphans: 3; widows: 3; }
p.luft { margin: 0 0 14pt; }
ul, ol { margin: 0 0 8pt; padding-left: 16pt; }
li { margin-bottom: 2pt; }
hr { border: 0; border-top: 0.6pt solid #999; margin: 14pt 0; }
.umbruch { break-after: page; page-break-after: always; height: 0; }
table { width: 100%; border-collapse: collapse; margin: 4pt 0 10pt;
        font-variant-numeric: tabular-nums; break-inside: avoid; page-break-inside: avoid; }
th, td { text-align: left; vertical-align: top; padding: 3pt 6pt; border-bottom: 0.4pt solid #ccc; }
th { font-weight: 600; border-bottom: 0.8pt solid #555; }
th.z, td.z { text-align: right; white-space: nowrap; }
blockquote { margin: 8pt 0 10pt; padding: 5pt 9pt; border-left: 2pt solid #999; background: #f4f4f2; }
code { font: 8.5pt ui-monospace, Menlo, monospace; }
"""


def seite(text: str, titel: str) -> str:
    return (f'<!doctype html><html lang="de"><head><meta charset="utf-8">'
            f"<title>{html.escape(titel)}</title><style>{STIL}</style></head>"
            f"<body>{markdown_html(text)}</body></html>")


def chrome() -> str | None:
    eigen = os.environ.get("BH_CHROME")
    for ort in ([eigen] if eigen else []) + CHROME_ORTE:
        pfad = ort if os.path.isabs(ort) else shutil.which(ort)
        if pfad and os.path.exists(pfad):
            return pfad
    return None


def html_pdf(seite_html: str, ziel: str) -> None:
    """Rendert eine HTML-Seite mit Headless-Chrome nach `ziel`."""
    browser = chrome()
    if not browser:
        raise DruckFehler("Für das PDF wird Chrome oder Chromium gebraucht "
                          "(Pfad über BH_CHROME angeben).")
    with tempfile.TemporaryDirectory(prefix="bh-druck-") as tmp:
        quelle = os.path.join(tmp, "seite.html")
        with open(quelle, "w", encoding="utf-8") as fh:
            fh.write(seite_html)
        # Eigenes Profil: sonst haengt sich der Aufruf an ein laufendes Chrome.
        befehl = [browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                  "--no-first-run", "--no-default-browser-check",
                  f"--user-data-dir={os.path.join(tmp, 'profil')}",
                  f"--print-to-pdf={os.path.join(tmp, 'aus.pdf')}",
                  "file://" + quelle]
        try:
            subprocess.run(befehl, capture_output=True, timeout=90, check=False)
        except subprocess.TimeoutExpired:
            raise DruckFehler("Chrome hat das PDF nicht in 90 Sekunden erzeugt.") from None
        erzeugt = os.path.join(tmp, "aus.pdf")
        if not os.path.exists(erzeugt) or os.path.getsize(erzeugt) == 0:
            raise DruckFehler("Chrome hat kein PDF erzeugt.")
        os.makedirs(os.path.dirname(ziel), exist_ok=True)
        shutil.move(erzeugt, ziel)


def pdf(text: str, titel: str, ablage: str) -> str:
    """PDF zum Markdown-Text, zwischengespeichert nach Inhalt.

    `ablage` ist ein Verzeichnis; der Dateiname traegt den Hash von Text und
    Stil, ein geaendertes Dokument bekommt also ein neues PDF und ein altes
    wird nie ausgeliefert.
    """
    s = seite(text, titel)
    name = hashlib.sha256(s.encode("utf-8")).hexdigest()[:16] + ".pdf"
    ziel = os.path.join(ablage, name)
    if not os.path.exists(ziel):
        html_pdf(s, ziel)
    return ziel
