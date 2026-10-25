"""Druckfassung: Markdown -> HTML -> PDF (bhl/druck.py).

Der Umsetzer muss dasselbe liefern wie markdown() in static/app.js, sonst
unterschreibt man etwas anderes, als man am Bildschirm gelesen hat.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WURZEL)

from bhl import druck   # noqa: E402

PROBE = """# Titel

**Firma**\\
Straße 1{zwei}
12345 Ort

Ein Absatz mit **Fettdruck über
zwei Zeilen** und `code`.

- erster Punkt
  mit Fortsetzung
- zweiter

| | |
|---|---:|
| Posten | 1,00 |

> Zitat

&nbsp;

---
---

## Nach dem Umbruch
""".format(zwei="  ")      # zwei Leerzeichen am Zeilenende: im Quelltext unsichtbar


class Umsetzer(unittest.TestCase):

    def test_bausteine(self):
        h = druck.markdown_html(PROBE)
        self.assertIn("<h1>Titel</h1>", h)
        self.assertIn("<strong>Firma</strong><br>Straße 1<br>12345 Ort", h)
        self.assertIn("<strong>Fettdruck über zwei Zeilen</strong>", h)
        self.assertIn("<li>erster Punkt mit Fortsetzung</li>", h)
        self.assertIn('<td class="z">1,00</td>', h)
        self.assertNotIn("<thead>", h)              # leere Kopfzeile faellt weg
        self.assertIn("<blockquote>Zitat</blockquote>", h)
        self.assertIn('<p class="luft">', h)
        self.assertIn(druck.SEITENUMBRUCH, h)
        self.assertNotIn("<hr>", h)

    def test_html_wird_maskiert(self):
        self.assertIn("&lt;script&gt;", druck.markdown_html("<script>x</script>"))

    @unittest.skipUnless(shutil.which("node"), "kein node für den Vergleich mit app.js")
    def test_gleich_wie_app_js(self):
        """Beide Umsetzer auf denselben Text - bis auf die Druckzusaetze gleich."""
        skript = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const m = src.match(/function markdown\(text\) \{[\s\S]*?\n\}\n/)[0];
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
eval(m);
process.stdout.write(markdown(fs.readFileSync(0, 'utf8')));
"""
        js = subprocess.run(["node", "-e", skript, os.path.join(WURZEL, "bhl", "static", "app.js")],
                            input=PROBE.split("---\n---")[0], capture_output=True, text=True,
                            check=True).stdout
        py = druck.markdown_html(PROBE.split("---\n---")[0]).replace(' class="luft"', "")
        self.assertEqual(js.strip(), py.strip())


@unittest.skipUnless(druck.chrome(), "kein Chrome/Chromium für das PDF")
class Pdf(unittest.TestCase):

    def test_pdf_und_zwischenspeicher(self):
        with tempfile.TemporaryDirectory() as d:
            a = druck.pdf(PROBE, "Probe", d)
            with open(a, "rb") as fh:
                self.assertEqual(fh.read(5), b"%PDF-")
            self.assertEqual(druck.pdf(PROBE, "Probe", d), a)        # gleicher Text, gleiche Datei
            self.assertNotEqual(druck.pdf(PROBE + "x", "Probe", d), a)


if __name__ == "__main__":
    unittest.main()
