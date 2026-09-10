# llm-buchhaltung

Doppelte Buchführung für deutsche Verhältnisse, gebaut für eine bestimmte
Arbeitsteilung:

| | wer | was |
|---|---|---|
| **Einrichten** | der Agent | klont, legt Mandant und Kontenrahmen an, richtet Bankkonten und Belegarchiv ein |
| **Buchen** | der Agent | liest Kontoauszüge und Belege, kontiert, bucht, holt Bankumsätze über die Schnittstellen |
| **Anpassen** | der Agent | fehlt ein Konto, eine Frist, ein Auszugsformat — er ändert die Anwendung lokal und schreibt den Test dazu |
| **Prüfen** | die Anwendung | Soll gleich Haben, Bankbestand, § 11 EStG, Zehn-Tage-Regel, Abgabesperre |
| **Zurückmelden** | der Agent | was lokal fehlte oder falsch rechnete, wird hier als Issue gemeldet — ohne die Daten, an denen es auffiel |
| **Freigeben** | der Mensch | einmal am Ende: liest die Auswertung, klappt Zahlen bis zum Beleg auf, unterschreibt |

Mit „der Agent" ist eine Coding-CLI gemeint, die auf dem Rechner läuft und
Dateien und Shell bedienen darf — **Claude Code**, **OpenAI Codex**, **Gemini
CLI**, Cursor und was sonst diese Form hat. Die Anwendung ist ein
Kommandozeilenwerkzeug mit Textausgabe und einer SQLite-Datei; das ist genau
die Oberfläche, mit der solche Agenten gut umgehen. Ihre Betriebsanleitung
steht in [`AGENTS.md`](AGENTS.md) (Claude Code findet sie über
[`CLAUDE.md`](CLAUDE.md)).

Der Mensch kommt einmal vor, am Ende, und das ist Absicht: Wer jeden
Buchungssatz selbst prüft, hat nichts gewonnen. Verlässlich ist die
Arbeitsteilung nicht, weil ein Mensch mitliest, sondern weil die Anwendung
deterministisch widerspricht.

Die Anwendung ist kein fertiges Produkt, sondern die Unterlage, auf der ein
Agent arbeitet. Deutsches Steuerrecht ändert Zeilennummern, Fristen und Sätze
jedes Jahr, und jeder Mandant hat einen Fall, den der Kontenrahmen nicht kennt.
Der Agent darf beides lokal geraderücken — und soll melden, was er geradegerückt
hat, damit es beim nächsten nicht wieder fehlt.

Der Agent arbeitet, die Anwendung widerspricht, der Mensch entscheidet. Die
Prüfzeile ist der Grund, warum die anderen zusammen funktionieren:
**in dieser Anwendung steckt kein Sprachmodell.** Kein API-Schlüssel, kein
Netzabruf, keine Inferenz – gerechnet wird in ganzen Cent, geprüft wird gegen
Regeln, und dasselbe Eingangsmaterial ergibt zweimal dasselbe Ergebnis. Eine
Buchführung, deren Zahlen von einem Modell abhängen, ist keine Buchführung.

Kontieren dagegen ist Urteilsarbeit – wozu gehört diese Zahlung, welcher
Paragraf greift, ist dieser Beleg vollständig. Das kann ein Modell, und es ist
schnell darin. Es darf nur nicht dieselbe Instanz sein, die das Ergebnis
bestätigt.

Technisch: Python-Standardbibliothek, SQLite, keine Abhängigkeiten außer
`pdftotext` (Poppler) für den Belegimport.

Zwei Betriebsarten, dieselbe Mechanik:

| | Kapitalgesellschaft | private Einkommensteuer |
|---|---|---|
| Periodisierung | Realisationsprinzip (HGB) | Zufluss/Abfluss (§ 11 EStG) |
| Rechenwerke | Bilanz, GuV | Vermögensübersicht, Ermittlung des zu versteuernden Einkommens |
| Formulare | Jahresabschluss, KSt/GewSt, UStVA | Anlage V je Objekt, Anlage N, KAP, S |
| Datenbank | `buchhaltung.sqlite` | `privat.sqlite` (`bh --db privat`) |

Buchen, Belegarchiv, Bankimport, Auswertungsmaschine und Oberfläche sind
identisch – der Unterschied steckt im Kontenrahmen und in einer Prüfung.

## Woher das kommt

Das hier ist kein Produkt, sondern Werkzeug aus dem eigenen Gebrauch. Gebaut
für drei Mandanten, die es tatsächlich gibt: eine operative GmbH mit
Umsatzsteuer und Reverse Charge, eine Beteiligungs-UG ohne Umsatzsteuer, und
eine private Zusammenveranlagung mit mehreren Mietobjekten — je Objekt eine
Anlage V, dazu AfA, Erhaltungsaufwand nach § 82b EStDV, Schuldzinsen aus
mehreren Darlehen und die Trennung von abziehbar und privat.

Fast jede Eigenheit hat einen Anlass. Die Tabelle, die festhält, was
tatsächlich ans Finanzamt übermittelt wurde, gibt es, weil fünf
Voranmeldungen abgelehnt wurden und es drei Monate niemand merkte. Die
Abgabesperre gibt es, weil eine geschätzte Zahl beinahe in einer Erklärung
gelandet wäre. Und der Kontoauszug-Parser kennt drei Vordruckgenerationen,
weil die Bank ihr Layout zweimal geändert hat und die Auszüge von 2017 anders
aussehen als die von heute.

**Geteilt, damit sich das lohnt.** Wer dieselben Formulare ausfüllt, stößt auf
dieselben Lücken: ein Kontenrahmen, der einen Fall nicht kennt; eine
Formularzeile, die sich verschoben hat; ein Auszugsformat, das der Parser
nicht liest. Zu mehreren findet man das schneller als allein, und jede
Korrektur nützt auch allen anderen. Deshalb steht in
[`AGENTS.md`](AGENTS.md) ausdrücklich, dass ein Agent melden soll, was er
lokal geradegerückt hat.

Was das Projekt **nicht** ist: eine Kanzleisoftware, ein DATEV-Ersatz oder
etwas, das jeden Fall des deutschen Steuerrechts kennt. Es kennt die Fälle,
die vorkamen.

> ## ⚠️ Nutzung auf eigenes Risiko
>
> **Diese Software rechnet, sie berät nicht.** Sie ist keine Hilfeleistung in
> Steuersachen im Sinne des § 5 StBerG und ersetzt weder Steuerberater noch
> Wirtschaftsprüfer.
>
> **Für Richtigkeit und Vollständigkeit haftet allein, wer einreicht.** Wer
> mit diesem Werkzeug Bücher führt, einen Abschluss aufstellt, eine
> Voranmeldung oder eine Steuererklärung abgibt, trägt dafür die volle
> Verantwortung — steuerlich, handelsrechtlich und strafrechtlich. Eine
> unrichtige Erklärung bleibt deine unrichtige Erklärung, gleich welches
> Programm sie gerechnet hat.
>
> **Keine Gewährleistung.** Die Software wird ohne jede Zusicherung
> bereitgestellt, wie in §§ 15 bis 17 der AGPL-3.0 ausgeführt: kein Anspruch
> auf Fehlerfreiheit, Eignung für einen bestimmten Zweck oder auf ein
> steuerlich zutreffendes Ergebnis. Eine Haftung des Urhebers für Schäden —
> Steuernachzahlungen, Zinsen, Säumnis- oder Verspätungszuschläge, Bußgelder,
> Datenverlust — ist ausgeschlossen, soweit das Gesetz es zulässt.
>
> **Der Rechtsstand veraltet.** Zeilennummern der Formulare, Fristen,
> Steuersätze, Hebesätze und Freibeträge bilden einen bestimmten Stand ab und
> ändern sich jedes Jahr. Prüfe sie gegen die geltenden Vordrucke, bevor du
> etwas abgibst.
>
> **Lass es prüfen.** Vor jeder Abgabe von einem Steuerberater durchsehen
> lassen. `bh pruefen` findet Rechen- und Regelfehler, keine falsche
> rechtliche Würdigung.

## Was herauskommt

Textausgaben, zeilen- und kennzahlengenau nach den amtlichen Vordrucken —
zum Danebenlegen beim Ausfüllen, nicht zum Übermitteln. Eine ERiC-Anbindung
gibt es nicht; abgegeben wird in Mein ELSTER von Hand.

**Anlage V, je Objekt eine** (`bh report <m> <jahr> anlagen`) — die
Zeilennummern sind die des Formulars, mit Vorjahresspalte:

```
1. Anlage V – Beispielstraße 1 · 10115 Berlin
  AfA linear 2,00 % · angeschafft 01.03.2019 · Darlehen: Musterbank
Einnahmen
  Zeile 15  Mieteinnahmen für Wohnungen (ohne Umlagen)         0,00     6.376,00
  Zeile 20  Umlagen: laufende Neben- und Betriebskosten        0,00     1.420,00
Werbungskosten
  Zeile 33  Absetzung für Abnutzung, linear                1.435,00     1.435,00
  Zeile 46  Schuldzinsen (ohne Tilgung)                    1.705,65     2.708,00
  Zeile 56  Erhaltungsaufwand, verteilt nach § 82b EStDV       0,00         0,00
Zeile 85  Überschuss / Verlust                            -3.140,65     1.314,00
```

**Umsatzsteuer-Voranmeldung** (`bh ustva <m> <jahr>`) — die amtlichen
Kennzahlen in der Reihenfolge des Formulars, dazu Frist, Abgabestand und
Transferticket. Darunter eine **Probe gegen die Buchführung**: was die
Anmeldung sagt gegen das, was auf den Steuerkonten gebucht ist, mit
Begründung jeder Abweichung.

```
 Kz   Bezeichnung                                              Betrag
 46   Leistungen eines im übrigen Gemeinschaftsgebiets…            31
 47   Steuer auf die Leistungen in Kz 46                        5,89
 66   Vorsteuerbeträge aus Rechnungen von anderen Unter…        5,12
 83   Verbleibende Umsatzsteuer-Vorauszahlung                  -5,12

 PROBE GEGEN DIE BUCHFÜHRUNG
   Kz 47   gebucht  6,04   Abweichung 0,15 — Rundung des Abschneidens
```

Dazu **Zusammenfassende Meldung**, **Bilanz und GuV** nach HGB-Gliederung mit
Vorjahresvergleich, **Vermögensübersicht und Ermittlung des zu versteuernden
Einkommens** bei der Einkommensteuer, **Saldenliste, Kontoblatt, Journal**
und eine **Belegliste**. Jede Auswertung gibt es als Text auf der
Kommandozeile und in der Weboberfläche, dort mit Drilldown bis zum Beleg.

Die **steuerliche Überleitungsrechnung** steht als `steuern.json` im
Mandantenordner — Zeile für Zeile im Klartext, jede mit Paragraf und
Begründung, und jede gegen die Kontensalden geprüft. Was dort nicht gegen die
Buchführung aufgeht, fällt vor der Abgabe auf statt danach.

## Leitplanken

Damit die Arbeitsteilung trägt, muss die Anwendung dem Agenten widersprechen
können. Vier Mechanismen tun das:

* **Regelprüfungen** (`bh pruefen`): Soll gleich Haben, Bankbestand gegen
  Kontoauszug, Zehn-Tage-Regel, § 11 EStG bei der Überschussrechnung.
* **Platzhalter mit Auflösungsbedingung**: eine geschätzte Zahl trägt, warum
  sie geschätzt ist, worauf sie beruht und welcher Beleg sie ablösen würde.
* **Abgabesperre** (`bh abgabe`): solange ein Platzhalter offen ist, sagt die
  Anwendung nein. Nicht als Warnung – als Rückgabewert.
* **Drilldown**: jede Zahl jeder Auswertung klappt bis zur Einzelbuchung und
  zum Beleg auf. Was sich nicht bis zum Papier zurückverfolgen lässt, ist
  keine Grundlage für eine Unterschrift.

## Loslegen

Voraussetzung ist Python 3.11 oder neuer. Für den Belegimport zusätzlich
`pdftotext` (in Poppler enthalten: `brew install poppler`, `apt install
poppler-utils`). Sonst nichts – keine Pakete, keine Datenbank, kein Dienst.

```bash
git clone https://github.com/RichardS83/llm-buchhaltung.git
cd llm-buchhaltung

./bh init    beispiel 2025
./bh eb      beispiel 2025 mandanten/beispiel/2025/eb.csv
./bh buchen  beispiel 2025 mandanten/beispiel/2025/buchungen.csv
./bh report  beispiel 2025 bilanz
./bh pruefen beispiel 2025
./bh abgabe  beispiel 2025          # "FREI — die Erklärung kann abgegeben werden"
./bh serve   beispiel 2025          # Oberfläche auf http://127.0.0.1:8765

python3 -m unittest discover -s tests -q    # Selbsttest
```

Zwei Beispielmandanten liegen bei, beide mit erfundenen Zahlen:

| Kürzel | was | zeigt |
|---|---|---|
| `beispiel` | Beteiligungsholding, SKR04-HOLDING | Bilanz, GuV, steuerliche Überleitung — geht sauber durch |
| `beispiel-privat` | Zusammenveranlagung, PRIVAT-ESt | Anlage V, Überschussrechnung nach § 11 EStG, **und die Abgabesperre** |

Der private trägt absichtlich einen offenen Platzhalter. So sieht man, was
passiert, wenn eine Zahl angenommen und nicht belegt ist:

```bash
./bh --db privat init    beispiel-privat 2025
./bh --db privat eb      beispiel-privat 2025 mandanten/beispiel-privat/2025/eb.csv
./bh --db privat buchen  beispiel-privat 2025 mandanten/beispiel-privat/2025/buchungen.csv
./bh --db privat report  beispiel-privat 2025 anlagen
./bh --db privat abgabe  beispiel-privat 2025
```

```
  PLATZHALTER  + Einkünfte aus nichtselbständiger Arbeit (Person A)  48.000,00 EUR
               angenommen:      48.000,00 EUR brutto, fortgeschrieben aus dem Vorjahr
               Grund:           Die Lohnsteuerbescheinigung 2025 liegt noch nicht vor.
               aufzulösen durch: Lohnsteuerbescheinigung 2025

GESPERRT — 1 Platzhalter. Die Erklärung ist nicht abgabefähig.
```

Rückgabewert 1 — ein Skript oder ein Agent kann daran hängen.

## Lizenz und Haftung

**AGPL-3.0.** Wer die Software ändert und über ein Netzwerk anbietet, muss den
geänderten Quelltext den Nutzern zugänglich machen (§ 13 AGPL). Der volle Text
steht in [`LICENSE`](LICENSE), die Randbedingungen in [`NOTICE`](NOTICE) – dort
steht auch, was gilt, wenn jemand die Software außerhalb der AGPL einsetzen
möchte: dafür braucht es die ausdrückliche Zustimmung des Urhebers.

Beiträge sind willkommen, siehe [`CONTRIBUTING.md`](CONTRIBUTING.md); sie
werden unter der [CLA](CLA.md) angenommen.

Zur Haftung siehe den Kasten unter
[Nutzung auf eigenes Risiko](#%EF%B8%8F-nutzung-auf-eigenes-risiko): Die
Software rechnet, sie berät nicht, und für das, was eingereicht wird, haftet
allein, wer es einreicht.

Eine Datenbank trägt **alle Mandanten** einer Art. Jeder führt seinen eigenen
Kontenrahmen, seine eigene Gliederung und sein eigenes Belegarchiv; getrennt
wird über `mandant_id`. Die privaten Buchungen liegen in einer **eigenen
Datei**, weil bei einer Betriebsprüfung nach § 147 Abs. 6 AO Datenzugriff auf
die betrieblichen Daten besteht – die Oberfläche zeigt trotzdem beide
nebeneinander.

```
buchhaltung/
├── bh                        CLI
├── bhl/
│   ├── db.py                 Schema, Cent-Arithmetik (nie float)
│   ├── kontenplan/           Kontenrahmen je Mandant
│   │   ├── holding.py          SKR04-HOLDING – Beteiligungsholding ohne USt
│   │   ├── operativ.py         SKR04-OPERATIV – operativ, mit USt und Reverse Charge
│   │   └── privat.py           PRIVAT-ESt – Überschussrechnung, Anlage V je Objekt
│   ├── ledger.py             Buchen, Salden, Summenprobe
│   ├── belege.py             Belegarchiv, Klassifizierung, Hash-Dublettenschutz
│   ├── verknuepfung.py       Buchung <-> Bankumsatz <-> Beleg
│   ├── vorjahr.py            Vorjahressalden aus dem Abschluss übernehmen
│   ├── steuern.py            steuerliche Überleitung gegen die Konten prüfen
│   ├── pruefung.py           Regelprüfungen (Soll=Haben, § 11 EStG, Zehn-Tage-Regel)
│   ├── umsatzsteuer.py       Voranmeldung und Zusammenfassende Meldung
│   ├── rechnungsust.py       gedruckte Umsatzsteuer vom Rechnungs-PDF ablesen
│   ├── zugang.py             Zugangsdaten aus dem Schlüsselbund
│   ├── reports.py            Saldenliste, Bilanz, GuV, Kontoblatt, Journal
│   ├── web.py                lokale Weboberfläche (nur lesend)
│   ├── static/               index.html, app.css, app.js
│   └── importers/
│       ├── commerzbank.py    Kontoauszug-PDF (drei Vordruckgenerationen) und CSV-Export
│       ├── qonto.py          Bankumsätze und angehängte Rechnungen
│       └── stripe.py         Ausgangsrechnungen und Guthabenbewegungen
├── tests/                    Selbsttest (Standardbibliothek, kein pytest)
├── buchhaltung.sqlite        Datenbank (alle Mandanten und Jahre, nicht im Repo)
└── mandanten/<kürzel>/
    ├── mandant.json          Stammdaten, Bankkonten, Umsatzsteuer-Einstellungen
    ├── voranmeldungen.csv    was tatsächlich ans Finanzamt übermittelt wurde
    ├── belege/<jahr>/<kategorie>/   Belegarchiv
    └── <jahr>/               eb.csv, buchungen.csv, steuern.json, Berichte
```

## Grundsätze

* **Beträge sind Integer in Cent.** Keine Fließkommazahlen im Rechenweg.
* **Jede Buchung muss ausgeglichen sein** – sonst wird sie abgelehnt.
* **Jeder Bankumsatz wird geprüft**: Anfangssaldo + Summe der Umsätze muss den
  Endsaldo des Auszugs ergeben, sonst meldet der Import eine Differenz.
* **Jeder Bankumsatz wird erklärt**: entweder gebucht oder mit Grund als
  bewusst nicht gebucht vermerkt (`nicht_gebucht.csv`). Keine stille Lücke.
* **Dublettenschutz** über einen Hash aus Datum, Betrag und Text.
* **Die Buchungsdatei ist die Wahrheit, die Datenbank ihr Abbild.** `bh buchen`
  und `bh eb` lassen sich beliebig oft auf dieselbe Datei anwenden: was
  unverändert dasteht, bleibt unangetastet – samt Journalnummer, verknüpftem
  Bankumsatz und Beleg –, Neues kommt dazu, was aus der Datei verschwunden ist,
  wird gelöscht. Der übliche Weg nach neuen Umsätzen ist deshalb
  `erzeuge_buchungen.py` und dann `bh buchen`, ohne `bh reset` davor.
* **Das Bankkonto wird gegen die Bank gehalten**: die Bewegung des Sachkontos im
  Jahr muss der Summe aller importierten Bankumsätze entsprechen. Das ist die
  einzige Kontrolle, die eine doppelt eingebuchte Bewegung findet – die
  Summenprobe kann es nicht, weil eine Dublette in sich ausgeglichen ist und
  auch die Bilanz weiter aufgeht.
* **Belege** liegen als Kopie im Archiv, referenziert über eine fortlaufende
  Belegnummer (`B2025-0042`), die im Buchungssatz steht.
* **Vorjahreszahlen kommen aus der Datenbank**, nicht aus einer gepflegten
  Zahlenliste – auch für Jahre, deren Buchungen nicht vorliegen (`bh vorjahr`).
* **Die Steuererklärung wird gegen die Buchhaltung gehalten**: jede Zeile der
  Überleitungsrechnung, die auf Konten verweist, muss deren Saldo treffen.
* **Privat gilt § 11 EStG**, und das wird geprüft, nicht der Disziplin
  überlassen: jede erfolgswirksame Buchung muss ein Geldkonto berühren. Die
  einzigen Ausnahmen sind die beiden, die das Gesetz selbst verlangt – AfA
  (§ 7 EStG) und die Verteilung von Erhaltungsaufwand (§ 82b EStDV).

## Ablauf für ein Geschäftsjahr

```bash
./bh init beispiel 2025                    # Mandant, Jahr, Kontenplan
./bh eb   beispiel 2025 mandanten/beispiel/2025/eb.csv
./bh import-bank beispiel 2025 'pfad/Kontoauszug_*.pdf'
./bh umsaetze beispiel 2025 --offen        # Arbeitsliste zum Kontieren
./bh --db privat umsaetze privat 0 --suche Musterstr --ab-betrag 5000   # Archivsuche
./bh buchen beispiel 2025 mandanten/beispiel/2025/buchungen.csv
./bh beleg scan beispiel /pfad/zum/ordner --jahr 2025
./bh link beispiel 2025                    # Buchung <-> Umsatz <-> Beleg
./bh vorjahr beispiel 2024                 # Vorjahresvergleich (einmalig)
./bh report beispiel 2025 bilanz
./bh serve  beispiel 2025                  # Oberfläche im Browser
```

`import-bank` liest drei Generationen des Commerzbank-Vordrucks: den heutigen,
den **gesperrt gesetzten** (jeder Buchstabe einzeln — `pdftotext -layout` macht
daraus Wörter aus einem Zeichen, deshalb werden dort die Glyphenkoordinaten
gelesen) und den alten ohne IBAN, bei dem die Kontoverbindung aus Kontonummer
und Bankleitzahl gebildet wird. Erkannt wird das automatisch. Jeder Auszug wird
über `alter Kontostand + Summe der Umsätze = neuer Kontostand` geprüft; die
Kontrollrechnung steht in der Ausgabe.

Ein Bankkonto kann in `mandant.json` als `"nur_archiv": true` geführt werden.
Dann liegen seine Umsätze vollständig und durchsuchbar in der Datenbank, ohne
dass `bh pruefen` einen Abgleich mit dem Sachkonto verlangt — so sind die
privaten Commerzbank-Konten abgelegt, bei denen objektweise aus der Anlage V
gebucht wird und nicht über den Kontoauszug. `bh umsaetze <mandant> 0` sucht
über alle Jahre, `--suche` filtert im Buchungstext (mehrfach = und).

Statt der Commerzbank-PDFs kann die Bank auch über eine Schnittstelle kommen:
`bh import-qonto` und `bh import-stripe` holen Umsätze und Belege direkt. Die
Zugangsdaten liegen im Schlüsselbund von macOS (siehe `bhl/zugang.py`), nie in
einer Datei dieses Verzeichnisses.

## Oberfläche

`./bh serve <mandant> <jahr>` startet einen lokalen Server auf 127.0.0.1 (Port
8765) und öffnet den Browser. Der Server **liest nur** – gebucht wird
ausschließlich über die CLI.

**Jede Zahl lässt sich aufklappen.** Das ist der tragende Grundsatz der
Oberfläche und gilt für jede Seite, nicht nur für einzelne:

```
Bilanz-/GuV-Posten ┐
Zeile der Anlage V ├→ Konto → Buchungszeile → Buchungssatz → Bankumsatz → Beleg-PDF
Zeile der Steuererklärung ┤
Zeile der Saldenliste ─────┘
```

Ein Klick auf eine Zeile zeigt die Konten dahinter, ein Klick auf ein Konto
seine Buchungen, ein Klick auf eine Buchung den vollständigen Satz mit dem
Rohtext des Kontoauszugs, ein Klick auf den Beleg das PDF – alles ohne die
Seite zu verlassen. Daneben führt jeweils ein Verweis *Kontoblatt ›* auf das
Konto mit Anfangsbestand und laufendem Saldo. Der Zustand steht im
Adressfragment (`#/konto/1309?buchung=180&beleg=42`), Zurück-Taste und
Lesezeichen funktionieren also bis auf Belegebene.

Wer eine Seite ergänzt, die Beträge aus Konten zeigt, hängt sie an dieselbe
Kette: `/api/buchungen/<konto>,<konto>,…` liefert die Buchungszeilen, im
Frontend erzeugen `einbauZeile()` und der zentrale Klick-Empfänger den Rest.
Eine Zahl ohne Weg zu ihrer Herkunft ist eine Behauptung.

Die Übersicht zeigt zusätzlich eine Ergebnisbrücke – welcher Sachverhalt das
Jahresergebnis in welcher Höhe geprägt hat –, eine Fristenliste und eine
Prüfliste (Summenprobe, Bilanzausgleich, unerklärte Bankumsätze, Abweichungen
der Steuererklärung).

**Fristen und Prüfungen sind zweierlei und stehen deshalb getrennt.** Ein
Befund der Prüfliste heißt, dass in der Buchführung etwas nicht stimmt. Eine
angemeldete, noch nicht gezahlte Zahllast ist dagegen kein Mangel, sondern ein
Termin – sie wird am 10. fällig und ist bis dahin in Ordnung. Stünde sie in
derselben Liste, wäre die Prüfliste dauerhaft rot, und irgendwann sieht niemand
mehr hin.

## Offene Punkte und fehlende Belege

Ein fertiger Abschluss ist selten fertig: Fristen laufen, Feststellungen fehlen,
Zuordnungen brauchen eine Bestätigung. `<jahr>/offene_punkte.json` hält das
fest — je Punkt Art (`feststellung`, `frist`, `entscheidung`, `unterlage`,
`klaerung`, `hinweis`), Status, Priorität, Adressat, Frist und die Wirkung in
Euro. Die Oberfläche zeigt sie unter *Offene Punkte*, gruppiert und mit der Zahl
der offenen Punkte am Menüeintrag.

**Welche Buchungen keinen Beleg haben, steht nicht in der Datei.** Das zieht
`bhl/offen.py` bei jedem Aufruf aus der Buchführung; eine gepflegte Liste würde
sonst nach der ersten Verknüpfung etwas Falsches behaupten. Aus der Datei kommt
nur die Anmerkung dazu: `beschaffung` sagt, wo der Beleg zu holen ist,
`unkritisch` sagt, warum keiner nötig ist — Abschlussbuchungen haben keinen
Fremdbeleg und sollen die Liste nicht dauerhaft rot färben.

### Prüfung eines fertigen Abschlusses

Wird ein fertiger Abschluss nachgeprüft, kommen die Feststellungen als Punkte
mit `"art": "feststellung"` in dieselbe Datei — daneben ein Block `pruefung`
mit dem Rahmen der Prüfung, den Grundlagen und der Liste dessen, was geprüft
wurde und stimmt. Aus beidem erzeugt `erzeuge_texte.py` den Bericht
`pruefung_<jahr>.md`; die Oberfläche zeigt die Feststellungen als erste Gruppe
unter *Offene Punkte* und den Bericht unter *Unterlagen*.

Feststellungen bleiben nach der Umsetzung sichtbar: der Status `behoben`
(„korrigiert") zeigt, was aus den Belegen eindeutig folgte und deshalb bereits
gebucht oder im Text geändert wurde. Anders als `erledigt` verschwindet er nicht
in die Erledigt-Gruppe, sondern bleibt in der Prüfungsliste stehen — ein
Prüfbericht, aus dem die behobenen Punkte verschwinden, lässt nicht mehr
erkennen, was geprüft wurde.

Die Feststellungen stehen damit an derselben Stelle wie die übrigen offenen
Punkte und nicht in einem Dokument daneben. Ein Prüfungsbericht, den man
gesondert öffnen muss, wird beim nächsten Arbeitsschritt nicht gelesen.

`<jahr>/erzeuge_texte.py` schreibt daraus die Lesefassung `offene_punkte.md`,
damit derselbe Stand auch gedruckt vorliegt.

**Warum:** Am 25.07.2026 stand die größte Lücke des Abschlusses 2025 — die
vollständig fehlenden Kartenumsätze des Moss-Kontos — nur in einer
Markdown-Datei, während die Oberfläche einen abgeschlossenen Abschluss zeigte.

## Fristen

Welche Jahrespflichten anfallen, hängt am Kontenrahmen: eine Kapitalgesellschaft
schuldet Körperschaft- und Gewerbesteuererklärung, Aufstellung und Offenlegung,
die private Rechnung die Einkommensteuererklärung.

Eine bewilligte Fristverlängerung nach § 109 AO steht in `fristen.csv` als
`status = verlaengert` mit dem neuen Datum in `erledigt_am`. Sie tritt an die
Stelle der gesetzlichen Frist, die Pflicht bleibt aber offen — ohne diesen
Zustand meldet die Liste eine längst verlängerte Frist als überfällig, und die
gesetzliche Frist bliebe unsichtbar. Die Zeile nennt beide.

`bhl/fristen.py` stellt zusammen, was ansteht: Voranmeldungen und ihre
Zahlungen, Zusammenfassende Meldungen, Steuererklärungen, Aufstellung und
Offenlegung des Jahresabschlusses.

Die laufenden Termine kommen aus der Buchführung. Die jährlichen rechnet die
App aus Gesetz und Stichtag, statt sie zu pflegen – jede Zeile nennt ihre
Grundlage, damit die Annahme sichtbar bleibt:

| Pflicht | Frist | Grundlage |
|---|---|---|
| Steuererklärungen | 31.07. des Folgejahres | § 149 Abs. 2 AO |
| … wenn steuerlich beraten | Ende Februar des zweiten Folgejahres | § 149 Abs. 3 AO |
| Jahresabschluss aufstellen | 3 Monate, kleine 6 Monate | § 264 Abs. 1 S. 2 / S. 3 HGB |
| Offenlegung | 12 Monate nach dem Stichtag | § 325 Abs. 1a HGB |
| … Kleinstkapitalgesellschaft | Hinterlegung statt Offenlegung | § 326 Abs. 2 HGB |

Zwei Angaben steuern das, beide in `mandant.json` unter `"fristen"`:
`steuerlich_beraten` und `groessenklasse` (`kleinst` | `klein` | `mittel`).
Fällt ein Termin auf Samstag oder Sonntag, rückt er auf den nächsten Werktag
(§ 108 Abs. 3 AO); Feiertage kennt die Rechnung nicht.

**Ob eine Pflicht erfüllt ist, weiß die Buchführung nicht.** Das steht in
`mandanten/<kürzel>/fristen.csv` – dieselbe Rolle wie `voranmeldungen.csv` bei
der Umsatzsteuer. Ohne Eintrag gilt eine Pflicht als offen, und das ist die
richtige Vorgabe: eine vergessene Erklärung fällt so auf, eine erledigte kostet
eine Zeile.

```
jahr;art;status;erledigt_am;nachweis;notiz
2025;koerperschaftsteuer;erledigt;2026-04-03;;Bescheid steht aus
```

## Belegablage

```bash
./bh beleg scan beispiel /pfad/zum/ordner --jahr 2025
./bh beleg add  beispiel rechnung.pdf --datum 2025-03-04 \
                --aussteller "Bundesanzeiger Verlag GmbH" --betrag 72,95
./bh beleg list beispiel --jahr 2025
```

`bh link` stellt die Verbindungen her, und zwar nur deterministisch:

1. `belegfeld` `KA<n>` → Bankumsatz Nr. `n` (so erzeugt `erzeuge_buchungen.py`
   die laufenden Buchungen),
2. Bankumsatz → das PDF des Kontoauszugs, aus dem er stammt (IBAN und
   Auszugsnummer stehen im Dateinamen),
3. `belege_zuordnung.csv` (`belegfeld;belegnr;notiz`) für alles Übrige –
   Abschlussbuchungen, Lohn, Wertpapierabrechnungen, Verträge.

## Format der Buchungsdatei

Semikolon-getrennt, deutsche Beträge. Eine Zeile je Buchungszeile;
Folgezeilen ohne Datum gehören zur vorhergehenden Buchung (Splitbuchung).

```
datum;text;soll;haben;betrag;beleg;art;ztext
2025-01-13;IHK Berlin Beitrag 2025;6420;1800;195,58;KA4;;
2025-12-31;Gehaltsabrechnung 12/2025;6020;3500;8.333,33;LOHN12;;Bruttogehalt
;;6110;3500;179,39;;;AG-Zuschuss KV/PV
;;3500;3720;2.517,02;;;Lohnsteuer und SolZ
```

Zeilen, die mit `#` beginnen, sind Kommentare.

## Vorjahr ohne eigene Buchungen

Für ein Jahr, das beim Steuerberater gebucht wurde, übernimmt
`bh vorjahr <mandant> <jahr>` die Salden als **eine** Buchung. Es zieht

* die **Bestandskonten** aus der Eröffnungsbilanz des Folgejahres (dieselben
  Werte, § 252 Abs. 1 Nr. 1 HGB – sie stehen schon in der Datenbank),
* die **Erfolgskonten** aus `<jahr>/guv_kontennachweis.csv`, abgeschrieben aus
  dem Kontennachweis des Jahresabschlussberichts.

Den Gewinnvortrag rechnet es dabei auf den Stand *vor* Verwendung zurück. Geht
die Übernahme nicht auf, bricht sie mit der Differenz ab.

## Private Einkommensteuer

```
bh --db privat init privat 2025
bh --db privat import-est privat 2024 <ESt-Erklaerung_2024.pdf>
bh --db privat report privat 2024 anlagen
bh --db privat pruefen privat 2025
bh --db privat abgabe privat 2025
```

**Doppelte Buchführung, aber keine Bilanzierung.** Gebucht wird wie bei einer
Kapitalgesellschaft; erfolgswirksam wird nur, was zufließt oder abfließt.
`bh pruefen` weist jede Buchung zurück, die das verletzt.

**Die Kontonummer sagt, wo der Betrag im Formular landet.** Erfolgskonten
folgen dem Muster `<4|6><Objekt><Zeile der Anlage V>`:

```
4115   Objekt 1, Zeile 15   Mieteinnahmen für Wohnungen
6646   Objekt 6, Zeile 46   Schuldzinsen
6633   Objekt 6, Zeile 33   AfA           (Buchung ohne Zahlung erlaubt)
```

Damit ist die Anlage V keine Nebenrechnung, sondern eine Auswertung derselben
Gliederungsmaschine, die sonst Bilanz und GuV baut.

**Das Vorjahr kommt aus der Erklärung selbst.** `bh import-est` liest den
Textlayer der ausgefertigten Erklärung, bucht jede Anlage V und den
Mantelbogen – und prüft sich dabei gegen die Summenzeilen des Formulars
(Zeile 32, 83, 85 sowie Gesamtbetrag der Einkünfte und zu versteuerndes
Einkommen). Trifft eine Summe nicht, wird nichts gebucht.

Warum das mehr bringt als eine Einnahmen-Ausgaben-Liste: die Annuität teilt
sich von selbst in Tilgung (Bestand) und Zins (Werbungskosten), die Aufteilung
eines Darlehens auf mehrere Objekte fällt als Saldo an statt geschätzt zu
werden, und der Bankbestand kontrolliert die Vollständigkeit.

### Kontext in der Adresse

Mandant, Jahr und Zeitraum stehen im Adressfragment und wandern an jeden Abruf
und in jeden Link. Zwei Regeln halten das zusammen: fehlt der Mandant in der
Adresse, **bleibt der angezeigte stehen** — ein Link ohne Kontext ist kein
Wechselwunsch —, und nach jedem Seitenaufbau ergänzt `inhaltLinksErgaenzen()`
den Kontext in allen Links des Inhalts.

**Warum:** Der Router fiel bei fehlendem Mandanten auf `MANDANTEN[0]` zurück.
Damit sprang jeder Klick auf ein Kontenzeichen oder eine Journalnummer aus dem
angezeigten Mandanten heraus zum erstbesten der Liste – und zeigte dort ein
Konto, das es gar nicht gibt.

### Ergebnistreiber

`<jahr>/kennzahlen.json` benennt unter `treiber` die Posten, die das
Jahresergebnis erklären. Genau ein Eintrag trägt `"rest": true` und **keinen
Betrag** — seine Zahl ist die Differenz zwischen den benannten Treibern und dem
Jahresergebnis und wird bei jedem Aufruf gerechnet. Die Übersicht prüft, dass
die Summe aufgeht.

**Warum:** Am 02.09.2026 verschob eine Korrektur das Ergebnis um 63,45 EUR. Der
Restposten stand als feste Zahl in der Datei und stimmte sofort nicht mehr; die
Prüfung „Ergebnistreiber erklären das Jahresergebnis vollständig" schlug an.
Eine gerechnete Zahl kann nicht veralten.

Der Verweis auf die zugehörige Buchung steht als `beleg` (das Belegfeld) in der
Datei, nicht als Buchungsnummer. `bh buchen` legt eine geänderte Buchung neu an
und vergibt dabei eine neue Nummer; ein gepflegter Zahlenverweis zeigte danach
ins Leere oder, schlimmer, auf eine fremde Buchung. Das Belegfeld überlebt die
Neuanlage.

## Größenklasse und Offenlegung

`mandant.json` trägt unter `fristen.groessenklasse` `kleinst`, `klein` oder
`mittel`. Der Eintrag steuert zwei Dinge: die Aufstellungsfrist (§ 264 Abs. 1
Satz 2 oder 3 HGB) und ob die Fristenliste **Offenlegung** oder
**Hinterlegung** verlangt. Nur die Kleinstkapitalgesellschaft darf hinterlegen
(§ 326 Abs. 2 HGB); die hinterlegte Bilanz steht nicht frei im
Unternehmensregister, sondern wird nur auf Antrag und gegen Gebühr
herausgegeben.

Der Eintrag ist keine Formalie. Er entscheidet, ob die Zahlen einer Gesellschaft
frei im Netz stehen. `erzeuge_abschluss.py` baut den Abschluss entsprechend:
verkürzte Bilanz nach § 266 Abs. 1 Satz 4 HGB, verkürzte GuV nach § 275 Abs. 5
HGB und statt eines Anhangs die Angaben unter der Bilanz nach § 264 Abs. 1
Satz 5 HGB. Alles Weitere — Bewertungsmethoden, Anlagenspiegel, Berichtigungen —
steht in einem **Erläuterungsteil**, der ausdrücklich nicht zum Abschluss gehört
und nicht offengelegt wird. Die Trennlinie steht im Dokument, damit beim
Einreichen nicht versehentlich mehr hochgeladen wird als nötig.

## Steuerliche Überleitung

`<jahr>/steuern.json` beschreibt die Überleitung vom Handelsbilanzergebnis zum
zu versteuernden Einkommen Zeile für Zeile, mit Paragraf und Begründung. Zeilen
mit `konten` werden gegen die Kontensalden geprüft, `pruefgruppe` fasst mehrere
Zeilen zusammen, die sich dieselben Konten teilen, `verweis_konten` verlinkt
ohne zu prüfen. Abweichungen erscheinen in der Oberfläche und in der Prüfliste.

### Platzhalter und die Abgabesperre

Manchmal fehlt eine Unterlage, und trotzdem soll die Rechnung durchlaufen —
sonst sieht man nicht, was sie bewirkt. Dafür trägt eine Zeile einen
**Platzhalter**:

```json
{ "text": "Einkünfte aus nichtselbständiger Arbeit (Ehegatte B)",
  "betrag": 25000,
  "platzhalter": {
    "annahme":         "1.480,00 EUR brutto (12 × 123,33, Monatswert aus dem Vorjahr) …",
    "grund":           "Die Lohnsteuerbescheinigung 2025 liegt nicht vor.",
    "aufloesen_durch": "Lohnsteuerbescheinigung 2025 — oder die Bestätigung, …",
    "gesetzt_am":      "2026-09-03" } }
```

Das ist keine Anmerkung, sondern eine **Sperre**. Solange ein Platzhalter in der
Datei steht, gilt die Erklärung als nicht abgabefähig, und zwar mechanisch an
drei Stellen:

| wo | was passiert |
|---|---|
| `bh abgabe <mandant> <jahr>` | nennt jeden Platzhalter mit Annahme, Grund und Auflösung und endet mit **Rückgabewert 1** |
| `bh pruefen <mandant> <jahr>` | meldet jeden als **Fehler**, nicht als Hinweis — also ebenfalls Rückgabewert 1 |
| Oberfläche | rote Karte „Abgabe gesperrt“ auf der Steuerseite, Chip *Platzhalter* an der Zeile, Prüfung „Steuerrechnung ohne Platzhalter (abgabefähig)“ auf der Startseite |

Ein Hinweis wäre zu wenig: eine geschätzte Besteuerungsgrundlage in einer
abgegebenen Erklärung ist keine Kleinigkeit — nach § 150 Abs. 2 AO sind die
Angaben wahrheitsgemäß nach bestem Wissen und Gewissen zu machen. Aufgelöst
wird ein Platzhalter, indem der Block aus der Zeile entfernt und der belegte
Betrag eingesetzt wird; danach sagt `bh abgabe` **FREI**.

Der Vergleich über die Jahre wird **nicht hinterlegt**, sondern aus den
`steuern.json` der genannten Jahre gezogen:
`"vergleich": {"aus_jahren": [2024, 2025]}`. Jede Zahl steht damit genau einmal
in der Ablage, nämlich in dem Jahr, zu dem sie gehört; ein Jahr ohne Datei
bekommt eine leere Spalte statt einer abgeschriebenen Zahl.

Die Dokumente eines Jahres entstehen aus denselben Daten:
`<jahr>/erzeuge_abschluss.py` schreibt den Jahresabschluss aus der Datenbank,
`<jahr>/erzeuge_texte.py` die Steuererklärung aus `steuern.json` und die offenen
Punkte aus `offene_punkte.json`. Von Hand gepflegt wird keine Zahl.

## Umsatzsteuer

Die Voranmeldung wird **aus der Buchführung abgeleitet**, nicht daneben
gerechnet. Jede Zahl hat damit dieselbe Herkunft wie die Bilanz, und die
Oberfläche klappt jede Kennzahl bis zum Beleg auf:

```
Kennzahl → Buchungszeilen → Buchungssatz → Bankumsatz → Rechnungs-PDF
```

Woher eine Zeile ihre Kennzahl bekommt: zuerst aus `buchungszeile.ust_schluessel`,
sonst aus `konto.ustva_kz`. Die Reihenfolge ist wichtig, weil die
Bemessungsgrundlagen der Eingangsleistungen — Kz 46 (§ 13b Abs. 1) und Kz 84
(§ 13b Abs. 2) — auf dem Aufwandskonto liegen und sich nicht am Konto festmachen
lassen. Der Schlüssel `-` macht eine Zeile ausdrücklich neutral; das braucht die
Umbuchung der Steuerkonten zum Bilanzstichtag, die sonst das vierte Quartal
wieder aufheben würde.

```bash
./bh import-qonto  <m> <jahr>        # Bankumsätze + angehängte Rechnungen
./bh import-stripe <m> <jahr>        # Ausgangsrechnungen + Guthabenbewegungen
python3 mandanten/<m>/erzeuge_buchungen.py <jahr>
./bh buchen <m> <jahr> mandanten/<m>/<jahr>/buchungen.csv
./bh ustva  <m> <jahr> --quartal 2   # Eingabeblatt für Mein Elster
```

Drei Dinge, die dabei leicht falsch laufen und deshalb ausdrücklich behandelt
sind:

* **Die Vorsteuer wird vom Beleg abgelesen, nicht zurückgerechnet.** Mischt eine
  Rechnung steuerfreie Posten mit steuerpflichtigen — eine Notarrechnung mit
  Gerichtsauslagen etwa —, ist jede Rückrechnung falsch. `rechnungsust.py` liest
  den gedruckten Betrag und schweigt, wenn er nicht sicher erkennbar ist.
* **Elster rechnet die Steuer auf Kz 47 und 85 aus der auf volle Euro
  abgeschnittenen Bemessungsgrundlage**, gebucht wird je Rechnung auf den Cent.
  Für die Anmeldung gilt die Elster-Rechnung; die gebuchten Beträge stehen als
  Probe daneben. Differenzen von wenigen Cent sind richtig, größere ein Fehler.
* **Bei Stripe bewegt `net` das Guthaben, nicht `amount`.** Die Gebühr behält
  Stripe gleich ein; der Bruttobetrag der Rechnung wird nie gutgeschrieben. Als
  Bankumsatz steht deshalb der Nettobetrag, während die Forderung in voller Höhe
  ausgeglichen wird und die Gebühr als eigene Zeile vom Guthaben abgeht. Stünde
  brutto im Umsatz, wiche das Sachkonto dauerhaft um die Summe der Gebühren ab.
* **`vat_amount_cents` aus Qonto ist nur bei den Kontogebühren ein Beleg.** Dort
  steht die tatsächlich berechnete französische Steuer, und der Reverse Charge
  bemisst sich am Nettobetrag (Schlüssel `46f`). Bei Kartenumsätzen rät Qonto
  dagegen 19 % hinein — auch bei Anbietern, die gar keine Steuer berechnen.
  Ein pauschaler Abzug dieses Feldes würde die Bemessungsgrundlage verkürzen.

`voranmeldungen.csv` hält fest, was tatsächlich übermittelt wurde — Zeitraum,
Stand, Transferticket, Abgabedatum. Ohne diesen Nachweis lässt sich nicht sagen,
ob ein Zeitraum erledigt ist.

## Mandanten

Jeder Mandant ist ein Ordner unter `mandanten/` mit einer `mandant.json`.
Mitgeliefert werden zwei mit erfundenen Zahlen, `beispiel` und
`beispiel-privat` — siehe [Loslegen](#loslegen).

Drei Kontenrahmen stehen zur Wahl (`kontenrahmen` in der `mandant.json`):

| Rahmen | wofür | Umsatzsteuer |
|---|---|---|
| `SKR04-HOLDING` | Beteiligungsholding, Bilanz nach HGB | nein |
| `SKR04-OPERATIV` | operative Kapitalgesellschaft | ja, mit Reverse Charge |
| `PRIVAT-ESt` | private Einkommensteuer, Anlage V je Objekt | nein (§ 4 Nr. 12a UStG) |

Beide Gesellschaftsrahmen folgen demselben Nummernkreis, damit
Vorjahresvergleich und Übergabe an eine Kanzlei ohne Umschlüsselung
funktionieren; der operative ergänzt die Umsatzsteuer- und Erlöskonten.

**Kontenrahmen beschreiben eine Gattung, keinen Menschen.** Was einen einzelnen
Mandanten ausmacht — Anschrift, Steuernummer, Bankverbindungen, Mietobjekte,
abweichende Kontobezeichnungen, eigene Geschäftspartner — steht in seiner
`mandant.json` und wird beim `bh init` übernommen:

```json
{
  "kontenrahmen": "PRIVAT-ESt",
  "objekte": [
    { "nr": 1, "name": "Beispielstraße 1", "ort": "10115 Berlin",
      "angeschafft": "2019-03-01", "afa_satz": 200,
      "aktenzeichen": "000000000000",
      "darlehen": [ { "suffix": "10", "bezeichnung": "Musterbank" } ] }
  ],
  "kontobezeichnungen": { "1800": "Musterbank Geschäftskonto 1234567" },
  "kontoauszug_kopfzeilen": ["Musterstraße", "10115 Berlin"],
  "belegregeln": [["Musterlieferant", "eingangsrechnung", "Musterlieferant GmbH"]]
}
```

Jedes Objekt erzeugt seinen eigenen Satz Konten (`0<nr>10` Grund und Boden,
`6<nr>46` Schuldzinsen …) und eine eigene Anlage V. `suffix` sind die letzten
beiden Ziffern der Darlehenskontonummer; fehlen sie, wird der Reihe nach 10,
20, 30 vergeben.

Private Einkommensteuer gehört in eine **eigene Datei** (`bh --db privat …`),
weil bei einer Betriebsprüfung nach § 147 Abs. 6 AO Datenzugriff auf die
betrieblichen Daten besteht. `bh serve` zeigt beide nebeneinander.

Die Oberfläche wechselt oben links zwischen Mandant und Geschäftsjahr; beides
steht im Adressfragment, ein Lesezeichen ist also eindeutig.
