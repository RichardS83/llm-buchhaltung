# Übermitteln mit ERiC

`bh eric` übermittelt die **Umsatzsteuer-Voranmeldung** und die
**Zusammenfassende Meldung** direkt aus der Buchführung an ELSTER — über ERiC,
die C-Bibliothek der Finanzverwaltung. Die Kennzahlen sind dieselben, die
`bh ustva` als Eingabeblatt druckt; abgetippt wird nichts mehr.

```
bh eric pruefen beispiel 2026 ustva --quartal 3     lokal prüfen, Vorschau-PDF
bh eric test    beispiel 2026 ustva --quartal 3     an ELSTER, mit Testmerker
bh eric senden  beispiel 2026 ustva --quartal 3     die Abgabe
```

Dasselbe mit `zm` statt `ustva`. Monatszahler nehmen `--monat 8`.

## Was du selbst besorgen musst

ERiC liegt **nicht** in diesem Repository und darf dort nicht liegen. Die
Lizenz der Finanzverwaltung erlaubt, ERiC in die eigene Software einzubinden,
aber nicht, es unterzulizenzieren. Jeder, der `bh eric` benutzt, holt sich
deshalb Zugang, Hersteller-ID und SDK selbst. Das kostet nichts, dauert aber
ein paar Tage.

1. **Im ELSTER-Entwicklerbereich registrieren** —
   <https://www.elster.de/elsterweb/registrierung-entwickler/form>.
   Anzugeben ist, wofür ERiC gebraucht wird: eine eigene
   Finanzbuchhaltungssoftware ist ein anerkannter Zweck. Die Zugangsdaten kamen
   bei uns nach drei Tagen per E-Mail.
2. **Hersteller-ID beantragen** — im Entwicklerbereich unter *Anträge und
   Formulare* (`/elsterweb/entwickler/antrag-hersteller-id`). Seit ERiC 43
   gibt es keine Test-Hersteller-ID mehr: ohne eigene geht nicht einmal ein
   Testfall. Bei uns lag die ID nach drei Tagen vor.
3. **Lizenz akzeptieren und das SDK laden** — Entwicklerbereich → *ERiC* →
   aktuelles Release → *Lizenzvertrag anzeigen* → *Akzeptieren*. Drei Dateien:
   - `ERiC-<version>-<plattform>.jar` (macOS: `Darwin-universal`, Linux:
     `Linux-x86_64`) — die Bibliothek; ein JAR ist ein ZIP, `unzip` genügt;
   - `ERiC-<version>-Dokumentation.zip` — Handbuch, Prüfregeln, Beispiele;
   - `ERiC-<version>-Schemadokumentation.zip` — die XML-Schemata.
4. **Ein ELSTER-Zertifikat** des Kontos, das übermitteln soll (die `.pfx`
   aus der Registrierung bei Mein ELSTER), und seine PIN.

## Einrichten

Hersteller-ID, Pfad und PIN stehen nie in einer Datei des Repositorys. `bh`
liest sie aus der Umgebung oder dem macOS-Schlüsselbund:

| Umgebung | Schlüsselbund-Dienst | Inhalt |
|---|---|---|
| `ERIC_PFAD` | `bh-eric-pfad` | das `lib`-Verzeichnis im entpackten SDK |
| `ERIC_HERSTELLER_ID` | `bh-eric-hersteller-id` | deine fünfstellige Hersteller-ID |
| `ERIC_PIN_<KÜRZEL>` | `bh-eric-pin-<kürzel>` | PIN des Zertifikats eines Mandanten |
| `ERIC_PIN` | `bh-eric-pin` | PIN, falls alle Mandanten ein Zertifikat teilen |

```sh
security add-generic-password -s bh-eric-pfad -a "$USER" -w   # fragt nach dem Wert
security add-generic-password -s bh-eric-hersteller-id -a "$USER" -w
security add-generic-password -s bh-eric-pin-beispiel -a "$USER" -w
```

Je Mandant in `mandanten/<kürzel>/mandant.json`:

```json
"elster": {"bufa": "9198", "zertifikat": "~/ELSTER/firma.pfx"}
```

`bufa` ist die vierstellige Bundesfinanzamtsnummer deines Finanzamts — zu
finden in `Dokumentation/Finanzamtsdaten.ods` des SDK. Aus ihr und der
Steuernummer des Bescheids bildet ERiC selbst die 13-stellige Steuernummer
im ELSTER-Format. Für die ZM braucht es zusätzlich `ust_idnr`.

Dann, **in einem Terminal**:

```sh
bh eric einrichten beispiel
```

Das tut dreierlei:

- Es legt neben `lib/` ein Verzeichnis `bh-plugins/` an und verlinkt dort nur
  die Plugins, die `bh` braucht (UStVA, ZM, Bilanz). ERiC lädt beim Start jedes
  Plugin einmal an — mit allen rund 220 dauert der Start zwei Minuten, so keinen
  Augenblick.
- Es zeigt die Hinweise, die die ERiC-Lizenz dem Nutzer vor der ersten
  Verwendung vorschreibt (Datenschutzhinweis, Protokolldateien, das
  DSGVO-Informationsschreiben in Anhang 2 von `lizenz.pdf`), und lässt sie
  bestätigen. Ohne diese Bestätigung verweigert `bh eric` jeden Vorgang.
- Mit Mandant: es öffnet das Zertifikat mit der PIN und meldet, bis wann es
  gilt.

## Der Ablauf

1. **`pruefen`** — ERiC prüft den Datensatz gegen Schema und Plausiregeln der
   Finanzverwaltung und erzeugt ein Vorschau-PDF. Lokal; nichts verlässt den
   Rechner. Fehler sperren das Senden; Hinweise wie „Bitte geben Sie Ihre
   W-IdNr. an" sind keine Fehler und sperren nicht.
2. **`test --testzertifikat`** — sendet mit dem Testzertifikat aus dem SDK
   und dem Testmerker `700000004` an den Annahmeserver. Der prüft, antwortet
   mit einem Transferticket und löscht den Datensatz; kein Finanzamt sieht ihn.
   So lässt sich der ganze Weg probieren, bevor das eigene Zertifikat im
   Spiel ist.
3. **`test`** — dasselbe mit dem eigenen Zertifikat: die Probe, dass
   Zertifikat und PIN stimmen.
4. **`senden`** — die Abgabe, ohne Testmerker. Nur in einem Terminal, und erst
   nachdem der angezeigte Betrag (Kz 83 bzw. die Summe der ZM) eingetippt ist.
   Ist der Zeitraum in `voranmeldungen.csv` schon als abgegeben vermerkt —
   oder liegt in der Ablage ein angenommener Versand, der nie festgehalten
   wurde —, verweigert `bh` den zweiten Versand; eine Berichtigung geht mit
   `--berichtigt` (Kz 10 bzw. Meldeart 11).

**Vor jedem `test` und `senden` prüft `bh` erst nur.** ERiC gibt Hinweise
(„Bitte prüfen Sie …") nur bei einer reinen Prüfung heraus, zusammen mit dem
Senden allein Fehler. Deshalb läuft vorher eine Prüfung mit Hinweisen; Fehler
und Hinweise stehen mit Feld im Terminal, bevor die Freigabe abgefragt wird.
Bei einem Fehler bricht `bh` ab, ohne etwas zu senden.

Nach dem Senden steht die Abgabe mit Transferticket in
`mandanten/<kürzel>/voranmeldungen.csv` — dieselbe Datei, die die Seiten
*Abgaben* und *Fristen* lesen — und mit den übermittelten Werten in der
Datenbank. Jeder Lauf bekommt einen eigenen Ordner, der nie überschrieben wird:

    mandanten/<kürzel>/<jahr>/elster/<zeitraum>-<art>/<zeitstempel>-<modus>/
        datensatz.xml     was an ERiC übergeben wurde, Byte für Byte
        vorpruefung.xml   Prüfantwort mit Hinweisen (vor test und senden)
        antwort.xml       Rückgabe von ERiC zum Lauf
        server.xml        Antwort des Annahmeservers
        protokoll.pdf     Übertragungsprotokoll bzw. Vorschau, von ERiC gedruckt
        lauf.json         Zeitpunkt, ERiC-Version, Zertifikat, SHA-256, Ergebnis

Die ERiC-Protokolle liegen unter `mandanten/<kürzel>/elster/protokoll/`. Alles
davon enthält Steuerdaten und ist deshalb von Git ausgenommen.

## Nachlesen: Seite *ELSTER-Übermittlungen*

`bh serve` zeigt unter *Umsatzsteuer → ELSTER-Übermittlungen* jeden Lauf und
daneben die Abgaben von Hand aus Mein ELSTER. Ein Lauf zeigt:

- die Plausibilitätsprüfung mit jedem Fehler und Hinweis, Feld und Regel im
  Wortlaut von ERiC;
- den Abgleich Buchführung heute ↔ Datensatz, Kennzahl für Kennzahl;
- den Datensatz roh und gelesen — die gelesene Ansicht baut der Browser aus
  genau dem Rohtext, ohne Ergänzung und ohne Umrechnung;
- die Antwort des Annahmeservers, formatiert und roh;
- die Prüfsumme beim Lauf und jetzt — stimmen sie, ist die Datei unverändert;
- das von ERiC gedruckte Protokoll-PDF.

### Abgeben aus der Oberfläche

Unter *Abzugeben* steht jede offene Voranmeldung und ZM mit Frist, letzter
Prüfung und letztem Test. Der Ablauf hat drei Schritte, alle per Knopf:

1. **prüfen** — baut die Meldung aus der Buchführung, ERiC prüft lokal. Es
   öffnet sich die Seite dieser Prüfung mit Abgleich und Datensatz.
2. **testweise senden** (empfohlen) — mit dem eigenen Zertifikat und
   Testmerker. Ein Test zählt, wenn er bis auf den Testmerker dieselbe Meldung
   war.
3. **Abgeben** — Häkchen „gelesen", Betrag eintippen, dann „Verbindlich an
   ELSTER übermitteln". Der Server baut die Meldung neu und sendet nur, wenn sie
   Byte für Byte der angezeigten Prüfung entspricht, es die neueste Prüfung ist
   und die Vorprüfung keine Fehler findet. Danach ist sie festgehalten wie beim
   Senden im Terminal.

Die Knöpfe lösen eine Anfrage aus, die ein Sitzungstoken braucht. Es steht nur
in der von `bh serve` ausgelieferten Seite; Anfragen ohne Token, von einer
anderen Herkunft oder mit fremdem Host-Kopf lehnt der Server ab. So kann keine
andere Webseite im selben Browser eine Abgabe auslösen. Die PIN liest der
Server beim Senden aus dem Schlüsselbund; einrichten (Datenschutzhinweise) und
berichtigte Meldungen bleiben im Terminal.

## Pflege

- **Mindestversion.** ELSTER hebt jedes Jahr Ende April die Mindestversion an;
  ältere ERiC-Releases übermitteln danach nichts mehr. `bh eric` warnt vorher.
  Neues Release laden, entpacken, `ERIC_PFAD` umstellen,
  `bh eric einrichten` wiederholen.
- **Neues Jahr.** Für jedes Anmeldungsjahr gibt es ein eigenes Plugin
  (`libcheckUStVA_<jahr>`) und einen eigenen Namensraum. Das November-Release
  bringt das kommende Jahr mit; `bh` wählt beides nach dem Zeitraum.
- **Die Lizenz verpflichtet dich**, ERiC nach seiner Dokumentation zu benutzen,
  Updates zu verfolgen und deinen eigenen Nutzern Support zu leisten — nicht
  ELSTER. Die Supportadresse von ELSTER darfst du nicht weitergeben.

## Eröffnungsbilanz

Bei Eröffnung des Betriebs ist auch die Eröffnungsbilanz als E-Bilanz zu
übermitteln (§ 5b Abs. 1 Satz 4 EStG). `bh eric pruefen <mandant> <jahr>
eroeffnung` baut sie aus den Eröffnungsbuchungen (`art = eb`) am ersten Tag des
Wirtschaftsjahres, ohne GuV, und folgt dem Beispiel des SDK: Bilanzart
„Eröffnungsbilanz“, Bestandteile Bilanz und „Eröffnungsbilanz ohne GuV“, Stichtag
und Bilanzkontext am Eröffnungstag. Die Kontensalden gehen mit.

Bei Gründung einer GmbH hält das Konto der ausstehenden Einlagen das ganze
Stammkapital. Was der Gesellschaftsvertrag sofort fällig stellt, ist eingefordert
und steht als Forderung auf der Aktivseite (§ 272 Abs. 1 Satz 3 HGB); den Betrag
nennt `mandant.json`:

    "einlagen": {"eingefordert_bei_gruendung": "12.500,00", "quelle": "§ 4 des Gesellschaftsvertrags …"}

Die Seite *Abzugeben* und die Fristenliste führen die Eröffnungsbilanz nur im
Gründungsjahr, erkannt am Datum `gesellschaftsvertrag` in `mandant.json`.

## Klartext: was im Datensatz steht

Auf der Seite eines Laufs hat der Datensatz drei Ansichten: *Gelesen* (der
XML-Baum), *Roh* und **Klartext**. Klartext ordnet jedes Feld seinem Vordruck,
Abschnitt und seiner Zeile zu, mit dem Wortlaut aus Mein ELSTER — `E8002001`
wird „Anlage GK, Zeile 11: Jahresüberschuss/-fehlbetrag …“, `350` wird „GmbH“.

Quelle ist die *Jahresdokumentation* in der Dokumentation des ERiC-SDK
(`ERiC-<version>-Dokumentation.zip`). Gesucht wird sie neben der Bibliothek
(`…/<version>/doku/ERiC-<version>/Dokumentation/Datenarten`), sonst über
`ERIC_DOKU`. Sie wird zur Laufzeit gelesen und nicht ins Repository kopiert.
Ohne sie zeigt die Ansicht nur die Feldnamen.

Bei der E-Bilanz beschreibt die Dokumentation nur einen Teil der Positionen.
Den Rest benennt `bhl/taxonomie/ebilanz_klartext_de.json` (eigene Bezeichnungen
nach § 266 und § 275 HGB), dann die Kontenzuordnung des Mandanten. Was nur aus
den Namensgliedern übersetzt ist, trägt ein ≈. Leer übermittelte Mussfelder
stehen eingeklappt unter jeder Gruppe.

## Zur Freigabe vorlegen

Wer einen Datensatz vorbereitet, aber nicht selbst abgibt — ein Assistent, ein
Skript, die Steuerberaterin —, legt ihn vor:

    ./bh eric pruefen beispiel 2025 kst \
        --vorlegen "Berichtigt die Erklärung vom 15.05.2026: …" \
        --aenderung "Anlage GK Z. 11|−1.235|−1.535"     # was|beim Finanzamt|neu, mehrfach

Das prüft wie sonst auch und legt neben den Lauf eine `vorschlag.json` mit Grund
und Änderungen. Vorlegen lässt sich nur eine fehlerfreie Prüfung.

Auf der Seite *ELSTER-Übermittlungen* steht oben **Zur Freigabe**: je Vorschlag
eine Karte mit Grund, Änderungstabelle und den drei Schritten lokal geprüft →
Test an ELSTER → Abgabe. Die Zahl neben „Übermittlungen“ in der Seitenleiste
zählt die offenen. Abgegeben wird wie immer auf der Seite der Prüfung.

Der Stand steht nicht in der Datei, er ergibt sich aus den Läufen danach:

| Stand | wann |
|---|---|
| offen | keine Abgabe danach; eine neuere Prüfung mit demselben Datensatz übernimmt den Vorschlag |
| überholt | eine neuere Prüfung ergab einen anderen Datensatz — die Buchführung hat sich bewegt |
| abgegeben | eine angenommene Abgabe desselben Zeitraums und derselben Art danach |
| verworfen | auf der Karte verworfen (der Lauf bleibt in der Ablage) |

## Was (noch) nicht geht

- Die Voranmeldung trägt nur die Kennzahlen, die die Buchführung kennt (81, 86,
  21, 45, 46/47, 84/85, 66, 67, 83). Innergemeinschaftliche Lieferungen,
  Kleinunternehmer und Sondervorauszahlung fehlen.
- Die ZM meldet nur sonstige Leistungen (Umsatzart `S`).
- Körperschaft- und Gewerbesteuererklärung nur für den einfachen Fall (siehe
  unten); die Abholung der Bescheiddaten über ERiC fehlt.

## ELSTER-Postfach

`bh eric postfach <mandant>` — oder in `bh serve` die Seite *ELSTER-Postfach*,
Knopf „Postfach abrufen" — holt ab, was Finanzamt und BZSt dem ELSTER-Konto
des Zertifikats bereitstellen (Verfahren ElsterDatenabholung):

1. `PostfachStatus` zählt je Bereitstellungsdatenart, ohne etwas zu ändern.
2. `PostfachAnfrage` liefert Metadaten und Verweise; Inhalt und Anhänge lädt
   `libotto` aus dem SDK (`OttoDatenAbholen`) aus dem ELSTER-Objektspeicher.
3. Erst wenn alles auf der Platte liegt, bestätigt `PostfachBestaetigung` den
   Empfang — ELSTER verlangt das binnen 24 Stunden.

Abgelegt wird unter `mandanten/<kürzel>/elster/postfach/<datenart>/<id>/`
(`meta.json`, `datenpaket` wie geliefert, Anhänge), die Serverantworten unter
`postfach/protokoll/`. Schon bestätigte Nachrichten werden nicht erneut
geladen. Die Seite zeigt PDF als PDF und XML gelesen und roh.

## E-Bilanz

`bh eric pruefen|test|senden <mandant> <jahr> ebilanz` — oder in `bh serve`
unter *Abzugeben* — übermittelt die E-Bilanz nach § 5b EStG für das ganze
Wirtschaftsjahr, auch ein Rumpfjahr. Datenart `Bilanz_6.9`, HGB-Taxonomie 6.9.

* Übermittelt wird eine **Steuerbilanz** (Rechnungslegungsstandard AO). Wer
  keine Abweichungen zwischen Handels- und Steuerbilanz hat, braucht damit keine
  Überleitungsrechnung.
* Die Zuordnung Konto → Taxonomieposition steht je Mandant in
  `mandanten/<kürzel>/ebilanz.csv`; aus ihr entsteht auch der seit 2025
  verpflichtende **Kontennachweis**.
* Bilanz und GuV gehen mit allen Oberpositionen hinein, dazu der
  Betriebsvermögensvergleich (Ergebnis = Eigenkapital Ende − Anfang +
  Entnahmen − Einlagen). Aktiva = Passiva und GuV-Ergebnis = Jahresergebnis
  prüft `bh` vor ERiC.
* **Mussfelder**, die die Buchführung nicht kennt, werden leer berichtet. Welche
  das sind und welche Oberposition eine Position hat, steht in
  `bhl/taxonomie/ebilanz_6_9.json` und ist gegen das Plugin ermittelt. Für eine
  neue Taxonomie: Datei kopieren, Version und Namensräume anpassen,
  `tools/ebilanz_mussfelder.py <version>` laufen lassen, bis das Plugin nichts
  mehr vermisst.
* Die Freigabe verlangt die Bilanzsumme. Nach dem Senden steht die E-Bilanz mit
  Transferticket in `fristen.csv`.

## Körperschaft- und Gewerbesteuererklärung

`bh eric pruefen|test|senden <mandant> <jahr> kst` bzw. `… gewst` — oder in
`bh serve` auf der Seite eines Laufs. Datenarten `KSt_30_<jahr>` (KSt 1 mit
den Anlagen GK, ZVE, Verluste, WA und KSt 1 F) und `GewSt_<jahr>` (GewSt 1 A),
Zeitraum ist der Veranlagungszeitraum (`VZ`), Ablage unter
`<jahr>/elster/<jahr>-VZ-kst/`.

* Die **Beträge** kommen aus `<jahr>/steuern.json`, nachdem `steuern.pruefen()`
  sie gegen die Konten gehalten hat — gesperrt heißt kein Datensatz. Gebraucht
  werden die Zeilen mit `kennzahl` `zve`, `einkuenfte_gewerbe`,
  `verlustvortrag`, `gewinn_gewerbe`, `gewerbeverlust` und die Zeile mit
  `quelle: guv`. Volle Euro, abgerundet (−1.234,56 → −1.235), wie Mein ELSTER.
* **Stammdaten** aus `mandant.json`: Firma, Anschrift, Rechtsform, W-IdNr. und
  `anteilseigner` mit `steuernummer`, `bufa` (ihres Finanzamts), `strasse`,
  `plz`, `ort`, `nennbetrag`, `prozent` und `seit`.
* **Was die Buchführung nicht weiß**, steht in `<jahr>/ertragsteuer.json`:
  `neu_gegruendet`, `ort_geschaeftsleitung`, `p8_abs9`,
  `anteilseigner_geaendert`, `vertraege_mit_anteilseignern` (Anlage WA,
  Zeile 15), `gegenstand` und `betriebsstaette` (GewSt 1 A), `kst1f` mit den
  Beständen der Anlage KSt 1 F und `bescheiddaten_abholen`.
* Vorgesehen ist der **einfache Fall**: nur Einkünfte aus Gewerbebetrieb, keine
  Überleitungsposten, Hinzurechnungen oder Kürzungen, kein Verlustvortrag aus
  dem Vorjahr, eine Betriebsstätte. Alles andere hält mit einer Meldung an.
* Eine **Berichtigung** hat in diesen Erklärungen keine Kennzahl: eine zweite
  Übermittlung vor dem Bescheid ersetzt die erste.
* Zur Freigabe wird das zu versteuernde Einkommen bzw. der Gewinn aus
  Gewerbebetrieb eingetippt. Nach dem Senden steht die Erklärung mit
  Transferticket in `fristen.csv` (`koerperschaftsteuer`, `gewerbesteuer`).
