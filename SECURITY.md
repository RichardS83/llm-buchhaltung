# Sicherheit

## Eine Lücke melden

Melde Schwachstellen **nicht** über ein öffentliches Issue, sondern über
[GitHub Security Advisories](https://github.com/RichardS83/llm-buchhaltung/security/advisories/new).
Antwort innerhalb von 14 Tagen.

Bitte im Bericht: was betroffen ist, wie sich der Fehler auslösen lässt, was
ein Angreifer damit erreichen kann. **Keine echten Daten** — ein Beispiel mit
erfundenen Zahlen reicht immer aus, um eine Lücke zu zeigen.

## Was diese Anwendung schützt — und was nicht

llm-buchhaltung ist ein lokales Werkzeug. Es gibt keinen Server, keinen
Mandantenzugriff über das Netz, keine Benutzerverwaltung. Das
Sicherheitsmodell ist das des Rechners, auf dem es läuft.

Konkret:

* **Die Weboberfläche (`bh serve`) bindet auf `127.0.0.1` und ist rein
  lesend.** Sie hat keine Anmeldung. Wer sie an eine öffentliche Adresse
  bindet, veröffentlicht seine Buchführung — tu das nicht.
* **Die Datenbank ist unverschlüsselt.** Eine SQLite-Datei mit Bankumsätzen,
  Belegen und Steuerdaten. Verschlüsselung ist Sache des Dateisystems
  (FileVault, LUKS), nicht der Anwendung.
* **Zugangsdaten stehen nie im Quelltext.** `bhl/zugang.py` liest Schlüssel
  für Stripe und Qonto aus dem Schlüsselbund des Betriebssystems, ersatzweise
  aus der Umgebung. Ein Schlüssel in einer Datei im Projektverzeichnis ist
  ein Fehler, kein Vorgehen.
* **Belegimport ruft `pdftotext` auf.** Ein bösartiges PDF greift damit
  Poppler an, nicht diese Anwendung. Halte Poppler aktuell.

## Was hier niemals hineingehört

Keine echten Buchführungs-, Bank- oder Steuerdaten — nicht in Issues, nicht
in Pull Requests, nicht in Testdaten, nicht in Fehlerberichte. Die
`.gitignore` sperrt Datenbanken und Mandantenordner aus, und der Selbsttest
`tests/test_bh.py` schlägt an, wenn eine IBAN, eine Steuernummer, ein
API-Schlüssel oder eine E-Mail-Adresse in den versionierten Dateien landet.
Beides sind Netze, keine Garantien. Das Netz, auf das es ankommt, bist du.
