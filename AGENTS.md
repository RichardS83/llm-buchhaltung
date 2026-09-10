# Betriebsanleitung für Agenten

Diese Datei richtet sich an eine Coding-CLI, die diese Anwendung bedient —
Claude Code, OpenAI Codex, Gemini CLI, Cursor oder was sonst diese Form hat.
Menschen lesen besser [`README.md`](README.md).

## Deine Rolle

Du richtest ein, du buchst, du passt die Anwendung an, du meldest zurück.
Du gibst **nicht** frei. Zwischen deiner letzten Buchung und dem Finanzamt
steht ein Mensch, der die Auswertung liest und unterschreibt — einmal, am
Ende. Er prüft nicht jeden Buchungssatz nach; wäre das nötig, hätte die
Arbeitsteilung keinen Sinn. Verlässlich wird sie dadurch, dass die Anwendung
dir deterministisch widersprechen kann.

Die Anwendung enthält kein Sprachmodell. Sie rechnet in ganzen Cent und prüft
gegen Regeln. Wenn sie dir widerspricht, hat sie recht, bis du das Gegenteil
belegt hast — nicht plausibel gemacht, belegt.

## Einrichten

```bash
git clone https://github.com/RichardS83/llm-buchhaltung.git
cd llm-buchhaltung
python3 -m unittest discover -s tests -q     # muss grün sein, bevor du anfängst
```

Voraussetzungen: Python 3.11+, `pdftotext` (Poppler) für den Belegimport.
Sonst nichts.

Neuen Mandanten anlegen — `mandanten/<kuerzel>/mandant.json` schreiben, dann:

```bash
./bh init <kuerzel> <jahr>
```

Die `mandant.json` trägt **alles Mandantenspezifische**: Anschrift,
Steuernummer, Bankkonten mit IBAN, Kontenrahmen, Mietobjekte, abweichende
Kontobezeichnungen, Briefkopfzeilen der Kontoauszüge, eigene Belegregeln.
`mandanten/beispiel/mandant.json` zeigt die Form. `bh init` ist wiederholbar:
Eine geänderte Datei wird beim nächsten Lauf fortgeschrieben.

Diese Angaben erfindest du nicht und leitest sie auch nicht aus einem Beleg
ab. Rechtsform, Firmenname, Finanzamt, Steuernummer, IBANs und Mietobjekte
erfragst du beim Menschen — einzeln, und sag jeweils, wofür du die Angabe
brauchst. Eine geratene Steuernummer fällt erst beim Finanzamt auf.

Private Einkommensteuer gehört in eine eigene Datenbank (`bh --db privat …`),
weil bei einer Betriebsprüfung nach § 147 Abs. 6 AO Datenzugriff auf die
betrieblichen Daten besteht.

## Die Schleife

```bash
./bh import-bank <m> <jahr> 'pfad/Kontoauszug_*.pdf'   # oder import-qonto / import-stripe
./bh beleg scan  <m> /pfad/zum/ordner --jahr <jahr>
./bh umsaetze    <m> <jahr> --offen                    # deine Arbeitsliste
#   ... kontieren: buchungen.csv schreiben ...
./bh buchen      <m> <jahr> mandanten/<m>/<jahr>/buchungen.csv
./bh link        <m> <jahr>                            # Buchung <-> Umsatz <-> Beleg
./bh pruefen     <m> <jahr>
./bh report      <m> <jahr> bilanz|guv|saldenliste|journal|belege|anlagen
./bh abgabe      <m> <jahr>                            # darf abgegeben werden?
```

`bh buchen` ist idempotent: dieselbe Datei zweimal einlesen verdoppelt nichts.
Der Buchungssatz trägt einen Fingerabdruck; was schon so dasteht, bleibt
stehen, Fehlendes kommt dazu, Verschwundenes geht weg. Du darfst also die CSV
korrigieren und neu einlesen, statt zu stornieren.

## Wenn du etwas nicht weißt

**Nicht raten.** Setze einen Platzhalter — eine geschätzte Zahl, die trägt,
warum sie geschätzt ist und was sie ablösen würde:

```json
{ "text": "Einkünfte aus nichtselbständiger Arbeit (Ehegatte B)",
  "betrag": 25000,
  "platzhalter": {
    "annahme":         "1.480,00 EUR brutto (12 × 123,33, Monatswert aus dem Vorjahr)",
    "grund":           "Die Lohnsteuerbescheinigung 2025 liegt nicht vor.",
    "aufloesen_durch": "Lohnsteuerbescheinigung 2025",
    "gesetzt_am":      "2026-09-03" } }
```

Solange ein Platzhalter offen ist, sagt `bh abgabe` nein. Das ist keine
Warnung, das ist ein Rückgabewert. **Löse einen Platzhalter nie auf, weil die
Zahl plausibel aussieht** — nur gegen den Beleg, den `aufloesen_durch` nennt.

Dasselbe gilt für `bh pruefen`: Eine Beanstandung verschwindet, indem der
Fehler behoben wird, nicht indem die Regel angepasst wird. Wenn du überzeugt
bist, dass die Regel falsch ist, ist das ein Issue (siehe unten), keine
stille Änderung.

## Die Anwendung anpassen

Du darfst sie ändern. Deutsches Steuerrecht verschiebt jedes Jahr
Zeilennummern, Fristen und Sätze, und jeder Mandant hat einen Fall, den kein
Kontenrahmen kennt. Fünf Regeln:

1. **Beträge sind ganze Cent, niemals Gleitkomma.** Wer `float` für Geld
   nimmt, erzeugt Differenzen, die niemand mehr findet.
2. **Keine Mandantendaten in den Code.** Adressen, Kontonummern, Namen,
   Geschäftspartner gehören in die `mandant.json`. Ein Kontenrahmen beschreibt
   eine Gattung, keinen Menschen. Wenn du versucht bist, eine Bezeichnung
   fest einzutragen, ist die Antwort `kontobezeichnungen`.
3. **Keine neuen Abhängigkeiten.** Standardbibliothek. Eine Buchführung, die
   in zehn Jahren nicht mehr startet, weil ein Paket verschwunden ist, hat
   ihren Zweck verfehlt.
4. **Jede Änderung an der Rechenlogik braucht eine Gegenprobe** in
   `tests/test_bh.py` — mit erfundenen Zahlen, die vorher fehlschlägt.
5. **Vor und nach jeder Änderung** die bestehenden Auswertungen vergleichen:

   ```bash
   ./bh report <m> <jahr> saldenliste > /tmp/vorher.txt
   #   ... ändern ...
   ./bh report <m> <jahr> saldenliste | diff /tmp/vorher.txt -
   ```

   Eine Anpassung, die eine Zahl verändert, ohne dass du das beabsichtigt
   hast, ist ein Fehler — auch wenn die neue Zahl richtiger aussieht.

Deutsch mit `ä ö ü Ä Ö Ü ß`, keine Umschrift. Bezeichner und Dateinamen
dürfen transliterieren, Fließtext und Kommentare nicht.

## Zurückmelden

**Was dir lokal gefehlt hat, fehlt dem nächsten auch.** Wenn du auf einen Bug
stößt oder ein Feature vermisst, leg ein Issue an — im Repository
`RichardS83/llm-buchhaltung`, nicht in einer lokalen Notiz:

```bash
gh issue create --repo RichardS83/llm-buchhaltung \
  --title "Kurz und konkret" \
  --body "Was ich getan habe / was herauskam / was herauskommen sollte / Vorschrift, Formular und Zeile"
```

Melde insbesondere:

* eine Regelprüfung, die falsch anschlägt oder schweigt, wo sie anschlagen müsste
* ein Kontoauszugsformat, das der Parser nicht liest
* ein Konto, eine Frist, eine Kennzahl, die im Kontenrahmen fehlt
* eine Formularzeile, die sich geändert hat
* alles, was du lokal geradegerückt hast und was allgemein nützlich wäre

**Ohne die Daten, an denen es auffiel.** Keine echten Beträge, keine IBAN,
keine Steuernummer, keinen echten Beleg — auch nicht ausschnittsweise. Baue
ein Beispiel mit erfundenen Zahlen, das denselben Fehler auslöst; das geht
praktisch immer, und wenn es nicht geht, ist das selbst die interessante
Information. `tests/test_bh.py` schlägt an, wenn eine IBAN, Steuernummer,
USt-IdNr., ein API-Schlüssel oder eine E-Mail-Adresse in versionierten
Dateien landet — verlass dich nicht darauf, aber lass es laufen.

Hast du die Sache lokal schon behoben, mach einen Pull Request daraus; er
wird unter der [CLA](CLA.md) angenommen, siehe
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Am Ende: den Link zur Oberfläche geben

Der Mensch gibt frei, und er tut das in der Weboberfläche, nicht in deiner
Textausgabe. **Jede Sitzung, in der du gebucht, geprüft oder etwas an den
Zahlen geändert hast, endet damit, dass die Oberfläche läuft und du den Link
nennst.** Ohne Aufforderung, als letzter Schritt:

```bash
./bh serve <mandant> <jahr> --ensure     # gibt http://127.0.0.1:8765/ aus
```

Der Befehl ist idempotent: Läuft die Oberfläche schon, startet er keinen
zweiten Server, sondern gibt nur den Link aus. `bh serve` ohne `--ensure`
blockiert, bis der Server beendet wird — starte ihn so nicht, du wartest sonst
bis zum Zeitlimit.

**Übernimm den ausgegebenen Link, schreibe ihn nicht aus dem Kopf.** Kommt die
Oberfläche nicht hoch, ist der Rückgabewert 1 und es gibt keinen Link, sondern
eine Meldung mit dem Pfad zum Protokoll. Dann nennst du die Meldung. Ein Link
auf einen Server, der nicht läuft, ist schlimmer als keiner.

Nenne dazu, was der Mensch dort ansehen soll — die Seite, auf der das steht,
woran du gearbeitet hast, und was `bh abgabe` zuletzt gesagt hat. „Die
Oberfläche läuft" allein ist keine Übergabe.

Reines Lesen ist kein Auslöser. Wer nur einen Bericht ausgibt oder eine Frage
beantwortet, braucht die Oberfläche nicht zu starten.

## Was du nie tust

* eine Erklärung als abgabefertig bezeichnen, solange `bh abgabe` nein sagt
* einen Platzhalter auflösen, ohne den Beleg zu haben, den er verlangt
* eine Prüfregel lockern, damit eine Beanstandung verschwindet
* echte Beträge, Kontonummern oder Belege in Issues, Pull Requests, Commits
  oder Testdaten schreiben
* `git add -f` auf `*.sqlite` oder `mandanten/` — dort liegen die Bücher
* Zahlen aus einem Bericht abschreiben, statt sie aus der Datenbank zu lesen
* dem Menschen ein Ergebnis vorlegen, dessen Herkunft du nicht bis zur
  Einzelbuchung und zum Beleg zeigen kannst
* eine Sitzung beenden, in der du gebucht hast, ohne dass die Oberfläche läuft
  und du den Link genannt hast
