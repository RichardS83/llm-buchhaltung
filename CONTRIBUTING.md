# Mitarbeit

Danke für dein Interesse. Zwei Dinge vorweg, damit niemand Arbeit in etwas
steckt, das nicht passt.

## Was dieses Projekt ist — und was nicht

llm-buchhaltung ist eine kleine, nachvollziehbare doppelte Buchführung für
deutsche Verhältnisse. Der Anspruch ist nicht Vollständigkeit, sondern dass
jede Zahl bis zum Beleg zurückverfolgbar ist und der Code lesbar bleibt.

Willkommen sind:

* Fehlerkorrekturen, besonders in der Steuerlogik — mit einem Beispiel, das
  den Fehler zeigt
* weitere Bankimporte (die Schnittstelle ist `bhl/importers/`)
* Kontenrahmen für andere Mandantentypen
* Anpassungen an geänderte Formulare, Zeilennummern und Fristen

Eher nicht:

* Abhängigkeiten. Die Anwendung kommt mit der Standardbibliothek aus, und das
  soll so bleiben — eine Buchführung, die in zehn Jahren nicht mehr startet,
  weil ein Paket verschwunden ist, hat ihren Zweck verfehlt.
* eine Datenbank, die kein SQLite ist
* Mandantendaten jeder Art, auch als Testdaten

## Grundregeln im Code

1. **Beträge sind ganze Cent, niemals Gleitkomma.** Wer `float` für Geld
   benutzt, produziert Rundungsdifferenzen, die niemand mehr findet.
2. **Deutsch mit richtigen Zeichen** — `ä ö ü Ä Ö Ü ß`, keine Umschrift.
   Bezeichner und Dateinamen dürfen transliterieren, Fließtext nicht.
3. **Kommentare sagen warum, nicht was.** Der Code sagt schon, was er tut.
   Interessant ist die Vorschrift oder der Fall, der die Zeile erzwungen hat.
4. **Keine Mandantendaten in den Code.** Adressen, Kontonummern, Namen,
   Geschäftspartner gehören in `mandant.json`. Der Kontenrahmen beschreibt
   eine Gattung, keinen Menschen.
5. **Jede Änderung an der Rechenlogik braucht eine Gegenprobe** — ein Beispiel
   mit erfundenen Zahlen, das vorher fehlschlägt und nachher stimmt.

## Vor dem Pull Request

```bash
python3 -m unittest discover -s tests -q   # Selbsttest
./bh --db /tmp/probe.sqlite init beispiel 2025
./bh --db /tmp/probe.sqlite eb beispiel 2025 mandanten/beispiel/2025/eb.csv
./bh --db /tmp/probe.sqlite buchen beispiel 2025 mandanten/beispiel/2025/buchungen.csv
./bh --db /tmp/probe.sqlite pruefen beispiel 2025
```

## Contributor License Agreement

Beiträge werden nur unter der CLA in [`CLA.md`](CLA.md) angenommen. Der Kern
in einem Satz: Du behältst dein Urheberrecht, räumst dem Projektinhaber aber
das Recht ein, deinen Beitrag auch unter anderen Lizenzbedingungen
weiterzugeben.

Warum das nötig ist: Solange die Rechte an allen Teilen des Quelltexts in
einer Hand liegen, kann das Projekt später anders lizenziert werden — strenger,
freier oder im Einzelfall abweichend. Ohne CLA wäre schon der erste fremde
Beitrag ein dauerhaftes Hindernis, auch für Änderungen, die dir selbst
zugutekämen: Eine Lizenz lässt sich dann nur noch mit der Zustimmung jedes
einzelnen Beitragenden ändern, und Leute verschwinden.

Bestätige die CLA im Pull Request mit einer Zeile:

```
Ich habe die CLA in CLA.md gelesen und stimme ihr zu.
Signed-off-by: Vorname Nachname <mail@example.org>
```

## Fehler melden

Ein brauchbarer Bericht nennt: was du getan hast, was herauskam, was
herauskommen sollte, und — bei Steuerlogik — die Vorschrift oder Zeile des
Formulars, um die es geht. **Keine echten Zahlen, keine echten Belege.**
Erfinde ein Beispiel, das denselben Fehler auslöst.
