/* Oberflaeche der Buchhaltung.
 *
 * Kein Framework, kein Build. Der Zustand steht im Adressfragment:
 *
 *     #/konto/1309?buchung=180&beleg=42
 *      \___ Seite       \___ Schublade  \___ Belegfenster
 *
 * Dadurch funktionieren Zurueck-Taste und Lesezeichen bis auf Belegebene.
 * Alle Betraege kommen als Integer in Cent vom Server und werden erst hier
 * formatiert - gerechnet wird nirgends im Browser.
 */

'use strict';

const $ = (s) => document.querySelector(s);
const inhalt = $('#inhalt');

/* ------------------------------------------------------------- Formatierung */

const NF = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function eur(cent, opt = {}) {
  if (cent === null || cent === undefined) return '';
  if (cent === 0 && opt.nullLeer) return '';
  return NF.format(cent / 100);
}

/** Betrag mit Vorzeichenfarbe. */
function betrag(cent, opt = {}) {
  if (cent === null || cent === undefined) return '';
  const k = cent < 0 ? 'minus' : (cent > 0 && opt.gruen ? 'plus' : '');
  return `<span class="zahl ${k}">${eur(cent, opt)}</span>`;
}

function datum(iso) {
  if (!iso) return '';
  const t = String(iso).slice(0, 10).split('-');
  return t.length === 3 ? `${t[2]}.${t[1]}.${t[0]}` : iso;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* -------------------------------------------------------------------- Daten */

const zwischenspeicher = new Map();

/* Welcher Mandant und welches Jahr gezeigt werden, steht im Adressfragment und
 * wandert an jeden Abruf mit. Dadurch ist auch ein Lesezeichen eindeutig. */
const KONTEXT = { mandant: null, jahr: null, zeitraum: 'jahr' };

function mitKontext(pfad) {
  if (!KONTEXT.mandant) return pfad;
  const trenner = pfad.includes('?') ? '&' : '?';
  return `${pfad}${trenner}${kontextAbfrage()}`;
}

/** Mandant, Jahr und Zeitraum als Abfrageteil - für Links wie für Abrufe. */
function kontextAbfrage() {
  return `mandant=${encodeURIComponent(KONTEXT.mandant)}&jahr=${KONTEXT.jahr}`
       + (KONTEXT.zeitraum && KONTEXT.zeitraum !== 'jahr' ? `&zeitraum=${KONTEXT.zeitraum}` : '');
}

async function holen(pfad, frisch = false) {
  const voll = mitKontext(pfad);
  if (!frisch && zwischenspeicher.has(voll)) return zwischenspeicher.get(voll);
  const antwort = await fetch(voll);
  let daten;
  try { daten = await antwort.json(); }
  catch { throw new Error(`${antwort.status} ${antwort.statusText} bei ${pfad}`); }
  // Fehler nicht zwischenspeichern und nicht stillschweigend weiterreichen -
  // sonst bleibt die Seite auf "lädt …" stehen.
  if (!antwort.ok) throw new Error(daten.fehler || `${antwort.status} bei ${pfad}`);
  zwischenspeicher.set(voll, daten);
  return daten;
}

/** Eine Aktion auslösen (nur ELSTER-Übermittlungen). Das Token steht in der
 * ausgelieferten Seite; ohne es lehnt der Server ab. */
async function posten(pfad, daten) {
  const token = document.querySelector('meta[name="bh-token"]')?.content || '';
  const antwort = await fetch(mitKontext(pfad), {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-BH-Token': token },
    body: JSON.stringify(daten) });
  let d;
  try { d = await antwort.json(); } catch { d = { fehler: `${antwort.status} ${antwort.statusText}` }; }
  zwischenspeicher.clear();
  return { ok: antwort.ok, ...d };
}

/** Fehlerseite mit Rückweg - eine Sackgasse hilft niemandem. */
function fehlerSeite(text) {
  const wege = (MANDANTEN || []).flatMap((m) => m.jahre.map((j) =>
    `<li><a href="#/uebersicht?mandant=${encodeURIComponent(m.kuerzel)}&jahr=${j}"
      >${esc(m.name)} · ${j}</a></li>`)).join('');
  inhalt.innerHTML = `
    <h1>Das gibt es nicht</h1>
    <p class="unter">${esc(text)}</p>
    ${wege ? `<div class="karte"><strong>Vorhandene Geschäftsjahre</strong>
      <ul style="margin:8px 0 0;padding-left:20px">${wege}</ul></div>` : ''}`;
}

/* ------------------------------------------------------------------- Router */

function zustand() {
  const roh = location.hash.replace(/^#\/?/, '');
  const [weg, abfrage] = roh.split('?');
  const teile = weg.split('/').filter(Boolean);
  const p = new URLSearchParams(abfrage || '');
  return { seite: teile[0] || 'uebersicht', param: teile.slice(1).map(decodeURIComponent),
           buchung: p.get('buchung'), beleg: p.get('beleg'),
           mandant: p.get('mandant'), jahr: p.get('jahr'),
           zeitraum: p.get('zeitraum') || 'jahr' };
}

/** Den Kontext in jeden Menüpunkt schreiben, damit er beim Blättern erhalten bleibt. */
function navAktualisieren() {
  document.querySelectorAll('.menue a').forEach((a) => {
    const weg = a.getAttribute('href').split('?')[0];
    a.setAttribute('href', `${weg}?${kontextAbfrage()}`);
  });
}

/** Mandant oder Jahr wechseln: Kontext neu setzen, Zwischenspeicher leeren. */
function kontextSetzen(mandant, jahr, zeitraum) {
  KONTEXT.mandant = mandant;
  KONTEXT.jahr = String(jahr);
  KONTEXT.zeitraum = zeitraum || 'jahr';
  zwischenspeicher.clear();
  letzterWeg = null;
  navAktualisieren();
}

/** Adressfragment aendern, ohne die Seite neu zu bauen. */
function setzeAbfrage(name, wert) {
  const roh = location.hash.replace(/^#\/?/, '');
  const [weg, abfrage] = roh.split('?');
  const p = new URLSearchParams(abfrage || '');
  if (wert === null) p.delete(name); else p.set(name, wert);
  const s = p.toString();
  location.hash = '#/' + weg + (s ? '?' + s : '');
}

const SEITEN = {};
let letzterWeg = null;

/** Mandant und Jahr aus der Adresse auf etwas Vorhandenes abbilden.
 *
 * Ein Lesezeichen - oder der Mandantenwechsel selbst - kann auf ein Jahr
 * zeigen, das es dort nicht gibt (ein Mandant hat 2026, ein anderer nicht). Dann
 * still auf das juengste vorhandene Jahr zurueckfallen. Rueckgabe `null`,
 * solange die Mandantenliste noch nicht geladen ist.
 */
function kontextPruefen(z) {
  if (!MANDANTEN.length) return null;
  // Fehlt der Mandant in der Adresse, ist das kein Wechselwunsch, sondern ein
  // Link ohne Kontext. Frueher fiel der Router dann auf MANDANTEN[0] zurueck -
  // ein Klick auf ein Kontenzeichen sprang deshalb vom angezeigten Mandanten
  // zum erstbesten Mandanten, weil der in der Liste vorn steht.
  const wunschM = z.mandant || KONTEXT.mandant;
  const wunschJ = z.jahr || KONTEXT.jahr;
  const m = MANDANTEN.find((x) => x.kuerzel === wunschM) || MANDANTEN[0];
  const jahre = m.jahre.map(String);
  return { mandant: m.kuerzel,
           jahr: jahre.includes(String(wunschJ)) ? String(wunschJ) : jahre[0],
           zeitraum: z.zeitraum || KONTEXT.zeitraum || 'jahr' };
}

/** Jeden Link im Inhalt mit dem Kontext versehen.
 *
 * Die Menuepunkte erledigt `navAktualisieren`, die Links in den Seiten selbst
 * standen bisher ohne Mandant und Jahr da - jeder von ihnen war ein
 * Lesezeichen auf den falschen Mandanten. Hier einmal zentral nachziehen ist
 * verlaesslicher, als es in jeder Seitenvorlage einzeln zu schreiben.
 */
function inhaltLinksErgaenzen() {
  if (!KONTEXT.mandant) return;
  document.querySelectorAll('#inhalt a[href^="#/"], #schublade-inhalt a[href^="#/"]')
    .forEach((a) => {
      const href = a.getAttribute('href');
      if (href.includes('mandant=')) return;
      const [weg, abfrage] = href.split('?');
      a.setAttribute('href', `${weg}?${abfrage ? abfrage + '&' : ''}${kontextAbfrage()}`);
    });
}

/** Die drei Auswahlfelder auf den Kontext bringen.
 *
 * Die Jahresliste gehoert zum Mandanten und muss beim Wechsel neu aufgebaut
 * werden - sonst stehen dort weiter die Jahre des vorigen Mandanten.
 */
function wahlAbgleichen() {
  const w = $('#mandantwahl');
  if (w) w.value = KONTEXT.mandant;
  jahreFuellen();
}

async function zeichnen() {
  const z = zustand();
  const k = kontextPruefen(z) || (z.mandant ? { ...z } : null);
  if (k && (k.mandant !== KONTEXT.mandant || k.jahr !== KONTEXT.jahr
            || k.zeitraum !== KONTEXT.zeitraum)) {
    kontextSetzen(k.mandant, k.jahr, k.zeitraum);
    wahlAbgleichen();
    try {
      await zeitraeumeFuellen();
      await dokumenteFuellen();
      offenzahlSetzen();               // die Zahl gehoert zum Mandanten, nicht zum Start
      aufgabenzahlSetzen();
      freigabezahlSetzen();
    } catch (e) {
      return fehlerSeite(String(e && e.message || e));
    }
    const t = $('#zeitraumwahl');
    if (t) t.value = KONTEXT.zeitraum;
    letzterWeg = null;                 // Zahlen ändern sich, Seite neu aufbauen
  }
  // Die Adresse mitkorrigieren, wenn zurueckgefallen wurde: sonst liest der
  // Router beim naechsten Durchlauf wieder das ungueltige Jahr.
  if (k && ((z.jahr && String(z.jahr) !== k.jahr) || (z.mandant && z.mandant !== k.mandant))) {
    const ziel = (location.hash.split('?')[0] || '#/uebersicht');
    location.replace(`${location.pathname}${ziel}?${kontextAbfrage()}`);
  }
  const weg = location.hash.split('?')[0];

  if (weg !== letzterWeg) {
    letzterWeg = weg;
    document.querySelectorAll('.menue a').forEach((a) => {
      a.classList.toggle('aktiv', a.getAttribute('href').split('?')[0] === weg);
    });
    const bauen = SEITEN[z.seite];
    inhalt.innerHTML = '<div class="laedt">lädt …</div>';
    try {
      await (bauen ? bauen(z) : seiteFehlt(z));
    } catch (e) {
      const t = String(e && e.message || e);
      if (/gibt es|unbekannt|Geschäftsjahr/.test(t)) fehlerSeite(t);
      else inhalt.innerHTML = `<div class="karte"><strong>Fehler beim Aufbau der Seite</strong>
        <div class="rohtext" style="margin-top:10px">${esc(e && e.stack || e)}</div></div>`;
    }
    inhaltLinksErgaenzen();
    inhalt.scrollTop = 0;
    window.scrollTo(0, 0);
  }

  z.buchung ? schubladeOeffnen(z.buchung) : schubladeSchliessen();
  z.beleg ? viewerOeffnen(z.beleg) : viewerSchliessen();
}

function seiteFehlt(z) {
  inhalt.innerHTML = `<h1>Nicht gefunden</h1><p class="unter">Die Seite „${esc(z.seite)}“ gibt es nicht.</p>`;
}

window.addEventListener('hashchange', zeichnen);

/* --------------------------------------------------------------- Bausteine */

function kopf(titel, unter, brot) {
  return `${brot ? `<div class="brot">${brot}</div>` : ''}
    <h1>${esc(titel)}</h1>${unter ? `<p class="unter">${unter}</p>` : ''}`;
}

/** Firma, Zeitraum und Bestand in der Seitenleiste - eine Quelle für alle Seiten. */
function kopfzeile(d) {
  const z = d.zeitraum;
  $('#firma').textContent = d.mandant.name;
  periodeSetzen(z);
  $('#bestand').textContent =
    `${d.bestand.buchungen} Buchungen · ${d.bestand.belege} Belege`;
  document.title = `${d.mandant.name} · ${z.label}`;
}

/** Zeitraumzeile unter einer Seitenüberschrift. */
function zeitraumZeile(z, art) {
  if (!z) return '';
  const text = art === 'stichtag'
    ? `Stand zum <strong>${esc(z.stichtag)}</strong>`
    : `Zeitraum <strong>${esc(z.label)}</strong>`;
  return `<span class="marke-chip">${text}</span>`
       + (z.vollstaendig ? '' : ` <span class="marke-chip warn">Zeitraum läuft noch</span>`);
}

function kz(titel, wert, zusatz) {
  return `<div class="kz"><div class="titel">${esc(titel)}</div>
    <div class="wert">${wert}</div>
    ${zusatz ? `<div class="zusatz">${zusatz}</div>` : ''}</div>`;
}

/** Summenzeile im selben Spaltenraster wie der Tabellenkoerper.
 *
 * Sie gehoert in dieselbe Tabelle wie die Zeilen, ueber die sie summiert:
 * eine eigene <table> darunter rechnet ihre Spaltenbreiten unabhaengig aus,
 * dann steht die Summe nicht unter ihrer Spalte. Und jede Wertspalte bekommt
 * ihre Summe - Vorjahr und Veraenderung sind sonst die einzigen Zahlen der
 * Tabelle, die man selbst zusammenrechnen muesste.
 */
function fusszeile(f, mitVorjahr) {
  const wert = (v) => (v === null || v === undefined ? ''
    : (f.gruen ? betrag(v, { gruen: true }) : eur(v)));
  const delta = mitVorjahr && f.vj !== null && f.vj !== undefined ? f.wert - f.vj : null;
  return `<tr class="${f.klasse || 'summe'}">
    <td>${esc(f.bez)}</td>
    <td class="z">${wert(f.wert)}</td>
    ${mitVorjahr ? `<td class="z leise">${wert(f.vj)}</td>
    <td class="z klein still">${delta === null ? '' : (delta > 0 ? '+' : '') + eur(delta)}</td>` : ''}
  </tr>`;
}

/** Platzhalter fuer die Buchungen hinter einer Zahl - gefuellt beim ersten Klick.
 *
 * Nachgeladen statt mitgeliefert: die Bilanz haette sonst jede Buchung des
 * Jahres im Gepaeck, nur damit vielleicht eine einzige Zeile aufgeklappt wird.
 */
function einbauZeile(schluessel, konten, spalten) {
  return `<tr class="unterzeile" data-unter="${schluessel}"
      data-konten="${esc(konten.join(','))}" hidden>
    <td colspan="${spalten}" class="einbau"><div class="laedt klein">lädt …</div></td></tr>`;
}

/** Die Buchungen hinter einer Zahl, wie sie ueberall aufgeklappt aussehen.
 *
 * Gezeigt wird die Buchungs*zeile*: eine Sammelbuchung beruehrt das Konto oft
 * nur mit einem Teilbetrag, und dieser Teilbetrag ist es, der in die Summe
 * daruber eingeht. Der ganze Satz steht einen Klick weiter in der Schublade,
 * der Beleg noch einen weiter im Belegfenster.
 */
function buchungsTabelle(d) {
  if (!d.zeilen.length) {
    return `<p class="klein still" style="padding:8px 0">Im Zeitraum ist auf
      ${d.konten.length > 1 ? 'diesen Konten' : 'diesem Konto'} nichts gebucht.</p>`;
  }
  const mehrere = d.konten.length > 1;
  return `<table class="t"><thead><tr>
      <th class="z">Nr</th><th>Datum</th><th>Buchungstext</th>
      ${mehrere ? '<th>Konto</th>' : ''}<th>Gegenkonto</th>
      <th class="z">Betrag</th><th>Beleg</th></tr></thead>
    <tbody>${d.zeilen.map((r) => `
      <tr class="klick" data-buchung="${r.nummer}">
        <td class="z still mono">${r.nummer}</td>
        <td class="nw">${datum(r.datum)}</td>
        <td>${esc(r.buchungstext)}${r.zeilentext && r.zeilentext !== r.buchungstext
          ? `<div class="klein still">${esc(r.zeilentext)}</div>` : ''}</td>
        ${mehrere ? `<td class="mono klein leise">${esc(r.konto)}</td>` : ''}
        <td class="mono klein leise">${r.gegenkonten.join(' ')}</td>
        <td class="z">${betrag(r.betrag)}</td>
        <td class="klein">${r.belege.length
          ? r.belege.map((e) => `<button type="button" class="belegchip"
              data-beleg="${e.id}" title="Beleg öffnen">${e.belegnr}</button>`).join(' ')
          : `<span class="marke-chip" title="Belegfeld der Buchung, kein Beleg im Archiv verknüpft"
              >${esc(r.belegfeld || '–')}</span>`}</td></tr>`).join('')}
      <tr class="summe"><td colspan="${mehrere ? 5 : 4}">${d.anzahl}
        Buchung${d.anzahl > 1 ? 'en' : ''}</td>
        <td class="z">${betrag(d.summe)}</td><td></td></tr>
    </tbody></table>`;
}

async function buchungenLaden(tr) {
  tr.dataset.geladen = '1';
  const ziel = tr.querySelector('.einbau');
  const konten = (tr.dataset.konten || '').split(',').filter(Boolean);
  const d = await holen(`/api/buchungen/${encodeURIComponent(konten.join(','))}`);
  ziel.innerHTML = d.fehler
    ? `<p class="klein minus" style="padding:8px 0">${esc(d.fehler)}</p>`
    : buchungsTabelle(d);
  inhaltLinksErgaenzen();
}

/** Aufklappbare Gliederungstabelle fuer Bilanz und GuV. */
function gliederungsTabelle(zeilen, spalten, mitVorjahr, fuss = []) {
  const zs = [];
  zeilen.forEach((z, i) => {
    if (z.posten === null) {
      zs.push(`<tr class="ueberschrift"><td colspan="${mitVorjahr ? 4 : 2}"
        class="e${z.ebene}">${esc(z.bez)}</td></tr>`);
      return;
    }
    const hatKonten = z.konten.length > 0;
    const delta = mitVorjahr && z.vj !== null ? z.wert - z.vj : null;
    zs.push(`<tr data-zeile="${i}">
      <td class="e${z.ebene} ${hatKonten ? 'aufklapp' : ''}" ${hatKonten ? `data-auf="${i}"` : ''}>
        ${hatKonten ? '<span class="pfeil">›</span> ' : ''}${esc(z.bez)}
        ${hatKonten ? `<span class="still klein"> · ${z.konten.length} ${z.konten.length > 1 ? 'Konten' : 'Konto'}</span>` : ''}
      </td>
      <td class="z">${betrag(z.wert)}</td>
      ${mitVorjahr ? `<td class="z leise">${eur(z.vj)}</td>
      <td class="z klein still">${delta === null ? '' : (delta > 0 ? '+' : '') + eur(delta)}</td>` : ''}
    </tr>`);
    // Zweite Ebene: das Konto klappt zu seinen Buchungen auf. Der Weg zum
    // Kontoblatt bleibt daneben stehen - aufklappen heisst hier bleiben,
    // das Kontoblatt heisst die Seite wechseln.
    z.konten.forEach((k) => {
      const guv = k.nummer === 'GuV';
      const s = `${i}k${k.nummer}`;
      zs.push(`<tr class="unterzeile" data-unter="${i}" hidden>
        <td class="${guv ? '' : 'aufklapp'}" ${guv ? '' : `data-auf="${s}"`}>
          ${guv ? '' : '<span class="pfeil">›</span> '}<span class="mono">${esc(k.nummer)}</span>
          ${esc(k.bezeichnung)}
          <a class="hinweg" href="${guv ? '#/guv' : `#/konto/${k.nummer}`}"
             >${guv ? 'Rechnung' : 'Kontoblatt'} ›</a></td>
        <td class="z">${betrag(k.wert)}</td>${mitVorjahr ? '<td></td><td></td>' : ''}</tr>`);
      if (!guv) zs.push(einbauZeile(s, [k.nummer], mitVorjahr ? 4 : 2));
    });
  });
  return `<table class="t"><thead><tr>
      <th>${spalten[0]}</th><th class="z">${spalten[1]}</th>
      ${mitVorjahr ? `<th class="z">${spalten[2]}</th><th class="z">Δ</th>` : ''}
    </tr></thead><tbody>${zs.join('')}</tbody>
    ${fuss.length ? `<tfoot>${fuss.map((f) => fusszeile(f, mitVorjahr)).join('')}</tfoot>` : ''}
    </table>`;
}

/* Aufklappen wird zentral behandelt, damit neu gebaute Tabellen nichts binden
   muessen - und rekursiv, damit eine geschlossene Ebene ihre offenen
   Unterebenen mitnimmt, statt sie verwaist stehen zu lassen. */
function zweigOeffnen(tabelle, schluessel) {
  tabelle.querySelectorAll(`tr[data-unter="${schluessel}"]`).forEach((tr) => {
    tr.hidden = false;
    if (tr.dataset.konten && !tr.dataset.geladen) buchungenLaden(tr);
  });
}

function zweigSchliessen(tabelle, schluessel) {
  tabelle.querySelectorAll(`tr[data-unter="${schluessel}"]`).forEach((tr) => {
    tr.hidden = true;
    tr.querySelectorAll('[data-auf]').forEach((el) => {
      const p = el.querySelector('.pfeil');
      if (!p || !p.classList.contains('offen')) return;
      p.classList.remove('offen');
      zweigSchliessen(tabelle, el.dataset.auf);
    });
  });
}

document.addEventListener('click', (e) => {
  // Der Beleg zuerst: sein Knopf sitzt in einer Zeile, die sonst die Buchung
  // oeffnen wuerde - und wer auf den Beleg klickt, will den Beleg sehen.
  const chip = e.target.closest('[data-beleg]');
  if (chip) {
    setzeAbfrage('beleg', chip.dataset.beleg);
    return;
  }
  const auf = e.target.closest('[data-auf]');
  if (auf && !e.target.closest('a')) {
    const tabelle = auf.closest('table');
    const pfeil = auf.querySelector('.pfeil');
    if (pfeil.classList.toggle('offen')) zweigOeffnen(tabelle, auf.dataset.auf);
    else zweigSchliessen(tabelle, auf.dataset.auf);
    return;
  }
  const zeile = e.target.closest('tr[data-buchung]');
  if (zeile && !e.target.closest('a')) {
    setzeAbfrage('buchung', zeile.dataset.buchung);
  }
});

/** Fristen und Fälligkeiten - Termine, keine Prüfungen.
 *
 * Bewusst eine eigene Liste: eine angemeldete, noch nicht gezahlte Zahllast ist
 * kein Mangel der Buchführung, sondern etwas, das am 10. fällig wird. In der
 * Prüfliste stünde sie dauerhaft als Fehler.
 */
function fristenHtml(f) {
  if (!f || !f.eintraege.length) return '';
  const heute = new Date().toISOString().slice(0, 10);
  const zeile = (e) => {
    const erledigt = e.status === 'erledigt' || e.status === 'entfaellt';
    const wann = erledigt
      ? `✓ ${e.status === 'entfaellt' ? 'entfällt' : 'erledigt'}${
          e.erledigt_am ? ` ${datum(e.erledigt_am)}` : ''}`
      : (e.tage < 0 ? `${-e.tage} Tage über der Frist`
        : e.tage === 0 ? 'heute fällig'
        : e.tage === 1 ? 'morgen fällig' : `in ${e.tage} Tagen`);
    const marke = erledigt ? 'gut' : e.status === 'ueberfaellig' ? 'schlecht'
      : e.tage <= 14 ? 'warn' : '';
    const verlaengert = e.status === 'verlaengert'
      ? ' <span class="marke-chip">Frist verlängert</span>' : '';
    return `<tr class="${e.verweis ? 'klick' : ''}" ${e.verweis
        ? `onclick="location.hash='${e.verweis}?${kontextAbfrage()}'"` : ''}>
      <td class="nw">${datum(e.faellig)}</td>
      <td>${esc(e.bezeichnung)}
        ${verlaengert}
        <div class="klein still">${esc(e.grundlage)}${e.notiz ? ` · ${esc(e.notiz)}` : ''}</div></td>
      <td class="z">${!e.betrag_cent ? '' : e.vorlaeufig
        ? `<span class="leise">${eur(e.betrag_cent)}</span>
           <div class="klein still">Stand ${datum(e.stand)} · Zeitraum läuft noch</div>`
        : eur(e.betrag_cent)}</td>
      <td class="klein"><span class="marke-chip ${marke}">${wann}</span></td></tr>`;
  };
  const istOffen = (e) => ['offen', 'ueberfaellig', 'verlaengert'].includes(e.status);
  const offen = f.eintraege.filter(istOffen);
  // Erledigtes in derselben Form wie das Offene, neueste Frist zuerst
  const erledigt = f.eintraege.filter((e) => !istOffen(e))
    .sort((a, b) => (b.faellig || '').localeCompare(a.faellig || ''));
  return `<h2>Fristen und Fälligkeiten</h2>
    <div class="kennzahlen">
      ${kz('Offene Termine', String(f.offen),
           f.ueberfaellig ? `<span class="minus">${f.ueberfaellig} über der Frist</span>`
             : (f.bald ? `${f.bald} in den nächsten Wochen` : 'nichts Dringendes'))}
      ${kz('Nächster Termin', f.naechste ? datum(f.naechste) : '–',
           f.naechste ? (f.naechste < heute ? '<span class="minus">überfällig</span>'
             : 'noch offen') : 'nichts offen')}
      ${kz('Zu zahlen', eur(f.zu_zahlen), 'angemeldet, noch nicht gezahlt')}
    </div>
    <div class="karte eng"><table class="t">
      <thead><tr><th style="width:110px">fällig</th><th>Vorgang</th>
        <th class="z">Betrag</th><th>Stand</th></tr></thead>
      <tbody>${offen.map(zeile).join('') || `<tr><td colspan="4" class="still">
        Nichts offen.</td></tr>`}</tbody>
      ${erledigt.length ? `<tbody class="erledigt">
        <tr class="ueberschrift"><td colspan="4">Erledigt (${erledigt.length})</td></tr>
        ${erledigt.map(zeile).join('')}</tbody>` : ''}
    </table></div>
    <p class="klein still">Die gesetzlichen Fristen sind aus Stichtag und Paragraf gerechnet,
      nicht hinterlegt. Sie hängen an zwei Annahmen in <span class="mono">mandant.json</span>
      – steuerlich beraten und Größenklasse; erledigt ist, was in
      <span class="mono">fristen.csv</span> steht. Feiertage kennt die Rechnung nicht,
      nur Samstag und Sonntag (§ 108 Abs. 3 AO).</p>`;
}

/* ----------------------------------------------------------------- Übersicht */

SEITEN.uebersicht = async () => {
  const d = await holen('/api/uebersicht');
  const k = d.kennzahlen;
  const m = d.mandant;

  kopfzeile(d);

  const dBilanz = k.bilanzsumme_vj ? k.bilanzsumme - k.bilanzsumme_vj : null;

  const bruecke = d.treiber.length ? brueckeHtml(d.treiber, d.kennzahlen.ergebnis) : '';

  inhalt.innerHTML = `
    ${kopf(m.name, `${esc(m.rechtsform || '')} · ${esc(m.strasse || '')}, ${esc(m.plz || '')} ${esc(m.ort || '')}
       · ${esc(m.registergericht || '')} ${esc(m.registernummer || '')} · Steuernummer ${esc(m.steuernummer || '')}
       <br>${zeitraumZeile(d.zeitraum)}`)}

    ${d.kurzfassung ? `<div class="karte"><p style="margin:0">${esc(d.kurzfassung)}</p></div>` : ''}

    ${d.aufgaben && d.aufgaben.zaehler.aufgaben ? `<div class="karte" style="border-left:3px solid var(--minus)">
      <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
        <strong>Braucht dich: ${d.aufgaben.zaehler.aufgaben} Aufgabe${d.aufgaben.zaehler.aufgaben > 1 ? 'n' : ''}</strong>
        ${d.aufgaben.zaehler.ueberfaellig ? `<span class="marke-chip schlecht">${d.aufgaben.zaehler.ueberfaellig} überfällig</span>` : ''}
        ${d.aufgaben.naechste_frist ? `<span class="marke-chip warn">nächste Frist ${datum(d.aufgaben.naechste_frist)}</span>` : ''}
        <a href="#/aufgaben" style="margin-left:auto">Alle Aufgaben →</a></div>
      <ul style="margin:8px 0 0;padding-left:18px">${d.aufgaben.oben.map((a) => `<li>
        ${a.frist ? `<span class="mono klein ${a.ueberfaellig ? 'minus' : 'leise'}">${datum(a.frist)}</span> ` : ''}
        ${a.quelle ? `<span class="mono klein still">${esc(a.quelle)}</span> ` : ''}${esc(a.titel)}</li>`).join('')}</ul>
    </div>` : ''}

    <h2>Handelsbilanz</h2>
    <div class="kennzahlen">
      ${kz('Bilanzsumme', eur(k.bilanzsumme),
           `zum ${esc(d.zeitraum.stichtag)}` + (dBilanz === null ? ''
             : ` · Vorjahr ${eur(k.bilanzsumme_vj)}, ${dBilanz > 0 ? '+' : ''}${eur(dBilanz)}`))}
      ${kz(d.zeitraum.code === 'jahr'
             ? (k.ergebnis < 0 ? 'Jahresfehlbetrag' : 'Jahresüberschuss')
             : (k.ergebnis < 0 ? 'Fehlbetrag im Zeitraum' : 'Überschuss im Zeitraum'),
           betrag(k.ergebnis, { gruen: true }), `nach Steuern · ${esc(d.zeitraum.label)}`)}
      ${kz('Eigenkapital', eur(k.eigenkapital),
           `${Math.round(k.eigenkapital / k.bilanzsumme * 100)} % der Bilanzsumme`)}
      ${k.zahlungsdienstleister
          ? kz('Verfügbare Mittel', eur(k.verfuegbar),
               `${eur(k.liquiditaet)} bei Kreditinstituten, ${eur(k.zahlungsdienstleister)} `
               + 'bei Zahlungsdienstleistern')
          : kz('Liquide Mittel', eur(k.liquiditaet), 'Guthaben bei Kreditinstituten')}
    </div>

    ${!d.umsatzsteuer ? '' : `<h2>Umsatzsteuer</h2>
    <div class="kennzahlen">
      ${kz('Offene Zeiträume', String(d.umsatzsteuer.offen),
           d.umsatzsteuer.ueberfaellig
             ? `<span class="minus">${d.umsatzsteuer.ueberfaellig} über der Frist</span>`
             : (d.umsatzsteuer.naechste_frist ? `nächste Frist ${datum(d.umsatzsteuer.naechste_frist)}` : 'nichts fällig'))}
      ${kz('Noch zu zahlen', eur(d.umsatzsteuer.zu_zahlen), 'aus offenen Zeiträumen')}
      ${kz('Angemeldet, nicht bezahlt', eur(d.umsatzsteuer.unbezahlt_cent),
           d.umsatzsteuer.zahlung_ueberfaellig
             ? `<span class="minus">${d.umsatzsteuer.zahlung_ueberfaellig} über der Frist</span>`
             : (d.umsatzsteuer.unbezahlt ? `${d.umsatzsteuer.unbezahlt} Zeitraum/Zeiträume` : 'nichts offen'))}
      ${kz('Noch zu erstatten', eur(d.umsatzsteuer.zu_erstatten), 'aus offenen Zeiträumen')}
    </div>
    <div class="karte eng"><table class="t">
      <thead><tr><th>Zeitraum</th><th>Stand</th><th>Frist</th><th class="z">Kz 83</th></tr></thead>
      <tbody>${d.umsatzsteuer.zeitraeume.map((z) => `
        <tr class="klick" onclick="location.hash='#/ustva/${z.jahr}/${z.code}?mandant=${encodeURIComponent(KONTEXT.mandant)}&jahr=${z.jahr}'">
          <td>${esc(z.bezeichnung)}</td>
          <td><span class="marke-chip ${UST_STATUS[z.status]?.[0] || ''}">${esc(z.status)}</span>${
            z.zahlung_offen ? '<span class="marke-chip warn">unbezahlt</span>' : ''}</td>
          <td class="nw klein ${z.ueberfaellig || z.zahlung_ueberfaellig ? 'minus' : 'leise'}">${datum(z.frist)}</td>
          <td class="z">${betrag(z.kz83)}</td></tr>`).join('')}</tbody></table></div>
    <p class="klein still"><a href="#/ust">Alle Voranmeldungen mit Aufklappkette →</a></p>`}

    ${k.zve === undefined ? '' : `<h2>Steuern</h2>
    <div class="kennzahlen">
      ${kz('Zu versteuerndes Einkommen', eur(k.zve),
           k.ergebnis < 0 ? 'trotz handelsrechtlichem Verlust' : '')}
      ${kz('Körperschaftsteuer', eur(k.kst), `Erstattung ${eur(Math.abs(k.erstattung_kst || 0))}`)}
      ${kz('Gewerbesteuer', eur(k.gewst), `Erstattung ${eur(Math.abs(k.erstattung_gewst || 0))}`)}
      ${kz('Erstattung insgesamt',
           eur(Math.abs((k.erstattung_kst || 0) + (k.erstattung_solz || 0) + (k.erstattung_gewst || 0))),
           'Körperschaft-, Gewerbesteuer und SolZ')}
    </div>`}

    ${bruecke ? `<h2>Was das Ergebnis geprägt hat</h2>
    <div class="karte">${bruecke}</div>` : ''}

    ${fristenHtml(d.fristen)}

    <h2>Prüfungen</h2>
    <div class="karte"><ul class="pruef">${d.pruefungen.map((p) => `
      <li><span class="zeichen ${p.ok ? 'ok' : 'nok'}">${p.ok ? '✓' : '✕'}</span>
        <span>${esc(p.text)}</span>
        <span class="detail">${esc(p.detail)}</span></li>`).join('')}</ul>
      <p class="klein still" style="margin:12px 0 0">
        ${d.bestand.bankumsaetze} Bankumsätze · ${d.bestand.buchungen} Buchungen ·
        ${d.bestand.belege} Belege im Archiv ·
        ${d.bestand.buchungen_ohne_beleg} Buchungen ohne verknüpften Beleg ·
        <span title="Nur Rechnungen und Lohnunterlagen des Jahres. Depotauszüge, Orderprotokolle, Verträge und Bescheide haben naturgemäß keine Buchung und zählen hier nicht mit."
          >${d.bestand.rechnungen_ohne_buchung} Rechnungen ohne verknüpfte Buchung</span>
        <span class="still">(von ${d.bestand.belege_ohne_buchung} unverknüpften Belegen insgesamt –
        der Rest sind Nachweise ohne eigenen Geschäftsvorfall)</span></p>
    </div>

    ${d.hinweise.length ? `<h2>Was noch offen ist</h2>
      <div class="karte">${d.hinweise.map((h) => `
        <div style="display:flex;gap:10px;align-items:baseline;padding:7px 0;border-bottom:1px solid var(--linie)">
          <span class="marke-chip ${h.gewicht === 'hoch' ? 'warn' : ''}">${esc(h.gewicht)}</span>
          <span>${esc(h.text)}</span></div>`).join('')}
        <p class="klein" style="margin:12px 0 0"><a href="#/dokument/offene_punkte">Alle offenen Punkte und Annahmen →</a></p>
      </div>` : ''}`;
};

function brueckeHtml(treiber, ergebnis) {
  const gross = Math.max(...treiber.map((t) => Math.abs(t.betrag)), 1);
  const zeilen = treiber.map((t) => {
    const anteil = Math.abs(t.betrag) / gross * 48;
    const links = t.betrag < 0 ? 50 - anteil : 50;
    const ziel = t.buchung ? `?buchung=${t.buchung}`
               : (t.konten && t.konten.length ? `#/konto/${t.konten[0]}` : null);
    const titel = t.buchung
      ? `<a href="#" data-treiber="${t.buchung}">${esc(t.titel)}</a>`
      : (ziel ? `<a href="${ziel}">${esc(t.titel)}</a>` : esc(t.titel));
    return `<div class="br-zeile">
      <div><div class="br-titel">${titel}</div>
        <div class="br-text">${esc(t.text)}${t.steuerlich ? ` <span class="still">— steuerlich: ${esc(t.steuerlich)}</span>` : ''}</div></div>
      <div class="br-balken"><span class="br-null" style="left:50%"></span>
        <i class="${t.betrag < 0 ? 'm' : 'p'}" style="left:${links}%;width:${anteil}%"></i></div>
      <div class="br-betrag">${betrag(t.betrag, { gruen: true })}</div>
    </div>`;
  });
  zeilen.push(`<div class="br-zeile" style="border-top:2px solid var(--linie-2);border-bottom:0">
      <div class="br-titel"><strong>Jahresergebnis</strong></div><div></div>
      <div class="br-betrag"><strong>${betrag(ergebnis, { gruen: true })}</strong></div></div>`);
  return `<div class="bruecke">${zeilen.join('')}</div>`;
}

document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-treiber]');
  if (t) { e.preventDefault(); setzeAbfrage('buchung', t.dataset.treiber); }
});

/* -------------------------------------------------------------------- Bilanz */

SEITEN.bilanz = async () => {
  const d = await holen('/api/bilanz');
  const mitVj = d.summe_aktiva_vj !== null;
  const stichtag = d.zeitraum.stichtag;
  const vjStichtag = stichtag.slice(0, 6) + (Number(stichtag.slice(6)) - 1);
  const privat = KONTEXT.art === 'privat';
  const name = KONTEXT.titel.bilanz || 'Bilanz';
  inhalt.innerHTML = `
    ${kopf(`${name} zum ${d.zeitraum.stichtag}`,
           `${zeitraumZeile(d.zeitraum, 'stichtag')} ${privat
             ? `Steuerlich ohne Bedeutung – niemand verlangt sie. Sie macht die Buchführung aber
                kontrollierbar: solange sie aufgeht, fehlt keine Buchung.`
             : 'Gliederung nach § 266 HGB.'} Eine Bestandsrechnung – sie zeigt immer den
            aufgelaufenen Stand, auch wenn ein Monat gewählt ist.
            Eine Zeile anklicken zeigt die Konten dahinter, ein Konto seine Buchungen,
            eine Buchung den Satz mit Beleg.`)}
    ${d.differenz ? `<div class="karte" style="border-color:var(--minus)">
       <strong class="minus">${esc(name)} nicht ausgeglichen:</strong> Differenz ${eur(d.differenz)}</div>` : ''}
    <h2>${privat ? 'Vermögen' : 'Aktiva'}</h2>
    <div class="karte eng">${gliederungsTabelle(d.aktiva, ['Posten', stichtag, vjStichtag], mitVj,
        [{ bez: privat ? 'Summe Vermögen' : 'Summe Aktiva',
           wert: d.summe_aktiva, vj: d.summe_aktiva_vj }])}</div>
    <h2>${privat ? 'Verbindlichkeiten und Privatvermögen' : 'Passiva'}</h2>
    <div class="karte eng">${gliederungsTabelle(d.passiva, ['Posten', stichtag, vjStichtag], mitVj,
        [{ bez: privat ? 'Summe' : 'Summe Passiva',
           wert: d.summe_passiva, vj: d.summe_passiva_vj }])}</div>
    <p class="klein still">${privat ? 'Das Privatvermögen' : 'Der Bilanzgewinn'} enthält das
      Ergebnis von ${eur(d.ergebnis)}, das noch auf keinem Bestandskonto steht.
      Die Vorjahresspalte zeigt denselben Stichtag ein Jahr früher.</p>`;
};

/* ----------------------------------------------------------------------- GuV */

SEITEN.guv = async () => {
  const d = await holen('/api/guv');
  const mitVj = d.zeilen.some((z) => z.vj !== null);
  const jahr = Number(d.zeitraum.von.slice(0, 4));
  const privat = KONTEXT.art === 'privat';
  const ergebnisName = privat
    ? (d.zeitraum.code === 'jahr' ? KONTEXT.titel.ergebnis : 'Zwischenstand im Zeitraum')
    : (d.zeitraum.code === 'jahr'
        ? (d.ergebnis < 0 ? 'Jahresfehlbetrag' : 'Jahresüberschuss')
        : (d.ergebnis < 0 ? 'Fehlbetrag im Zeitraum' : 'Überschuss im Zeitraum'));
  inhalt.innerHTML = `
    ${kopf(`${KONTEXT.titel.guv || 'Gewinn- und Verlustrechnung'} ${d.zeitraum.kurz}`,
           `${zeitraumZeile(d.zeitraum)} ${privat
             ? `Gliederung nach § 2 EStG. Zufluss und Abfluss entscheiden (§ 11 EStG),
                nicht der Zeitpunkt der Leistung.`
             : 'Gesamtkostenverfahren nach § 275 Abs. 2 HGB.'}
            Eine Zeile anklicken zeigt die Konten dahinter, ein Konto seine Buchungen,
            eine Buchung den Satz mit Beleg.`)}
    <div class="karte eng">${gliederungsTabelle(
        d.zeilen, ['Posten', d.zeitraum.code === 'jahr' ? jahr : d.zeitraum.kurz,
                   d.zeitraum.code === 'jahr' ? jahr - 1 : `${d.zeitraum.kurz} (Vorjahr)`],
        mitVj,
        [{ bez: ergebnisName, wert: d.ergebnis, vj: d.ergebnis_vj,
           klasse: 'ergebnis', gruen: true }])}</div>
    <p class="klein still">${privat
      ? `Werbungskosten, Sonderausgaben und außergewöhnliche Belastungen stehen ohne Vorzeichen
         und werden abgezogen.`
      : `Aufwendungen stehen ohne Vorzeichen; das Ergebnis ergibt sich aus
         Erträgen abzüglich Aufwendungen.`}</p>`;
};

/* ---------------------------------------------------- Vermietung, Anlage V */

SEITEN.anlagen = async () => {
  const d = await holen('/api/anlagen');
  const mitVj = d.ergebnis_vj !== null && d.ergebnis_vj !== undefined;
  const zeile = (a) => `
    <tr class="klick" onclick="location.hash='#/anlage/${a.id}?${kontextAbfrage()}'">
      <td>${a.nr}. ${esc(a.objekt.name)}<div class="klein still">${esc(a.objekt.ort)}</div></td>
      <td class="z">${eur(a.einnahmen)}</td>
      <td class="z">${eur(a.werbungskosten)}</td>
      <td class="z">${betrag(a.ergebnis, { gruen: true })}</td>
      ${mitVj ? `<td class="z leise">${eur(a.ergebnis_vj)}</td>` : ''}</tr>`;
  inhalt.innerHTML = `
    ${kopf(`Vermietung und Verpachtung ${d.zeitraum.kurz}`,
           `${zeitraumZeile(d.zeitraum)} ${d.anlagen.length} Objekte, je eine Anlage V
            (§ 21 EStG). Ein Objekt anklicken zeigt die Zeilen des Formulars und die
            Konten dahinter.`)}
    <div class="karte eng"><table class="t">
      <thead><tr><th>Objekt</th><th class="z">Einnahmen</th>
        <th class="z">Werbungskosten</th><th class="z">Ergebnis</th>
        ${mitVj ? '<th class="z">Vorjahr</th>' : ''}</tr></thead>
      <tbody>${d.anlagen.map(zeile).join('')}
        <tr class="summe"><td>Einkünfte aus Vermietung und Verpachtung</td>
          <td class="z">${eur(d.einnahmen)}</td><td class="z">${eur(d.werbungskosten)}</td>
          <td class="z">${betrag(d.ergebnis, { gruen: true })}</td>
          ${mitVj ? `<td class="z leise">${eur(d.ergebnis_vj)}</td>` : ''}</tr>
      </tbody></table></div>`;
};

SEITEN.anlage = async (z) => {
  const d = await holen(`/api/anlage/${encodeURIComponent(z.param[0])}`);
  const mitVj = d.zeilen.some((x) => x.vj !== null);
  const o = d.objekt;
  const angaben = [`AfA linear ${(o.afa_satz / 100).toFixed(2).replace('.', ',')} %`];
  if (o.angeschafft) angaben.push(`angeschafft ${o.angeschafft.split('-').reverse().join('.')}`);
  if (o.aktenzeichen) angaben.push(`Aktenzeichen ${o.aktenzeichen}`);
  inhalt.innerHTML = `
    ${kopf(esc(d.titel),
           `${zeitraumZeile(d.zeitraum)} ${esc(o.ort)} · ${angaben.map(esc).join(' · ')}
            ${o.darlehen.length ? `· Darlehen: ${o.darlehen.map(esc).join(', ')}` : ''}`,
           `<a href="#/anlagen?${kontextAbfrage()}">Vermietung und Verpachtung</a>`)}
    <div class="karte eng">${gliederungsTabelle(
        d.zeilen, ['Zeile der Anlage V', d.zeitraum.kurz, `${d.zeitraum.kurz} (Vorjahr)`],
        mitVj,
        [{ bez: 'Zeile 32 · Summe der Einnahmen', wert: d.einnahmen, vj: d.einnahmen_vj },
         { bez: 'Zeile 83 · Summe der Werbungskosten',
           wert: d.werbungskosten, vj: d.werbungskosten_vj },
         { bez: 'Zeile 85 · Überschuss oder Verlust', wert: d.ergebnis, vj: d.ergebnis_vj,
           klasse: 'ergebnis', gruen: true }])}</div>
    ${d.afa_bemessung ? `<p class="klein still">Aus der AfA (${d.afa_bemessung_quelle})
      zurückgerechnete Bemessungsgrundlage: <strong>${eur(d.afa_bemessung)}</strong>
      bei ${(o.afa_satz / 100).toFixed(2).replace('.', ',')} %. Abgeleitet, nicht
      festgestellt – verbindlich ist das Anlagenverzeichnis des Steuerberaters.</p>` : ''}
    <p class="klein still">Die Zeilennummern sind die des amtlichen Formulars. Eine Zeile
      anklicken zeigt die Konten, aus denen sie sich zusammensetzt – von dort geht es
      weiter bis zum Buchungssatz und zum Beleg.</p>`;
};

/* ------------------------------------------------------------------ Prüfung */

SEITEN.pruefung = async () => {
  const d = await holen('/api/pruefung');
  const gruppe = (schwere, titel, erklaerung) => {
    const b = d.befunde.filter((x) => x.schwere === schwere);
    if (!b.length) return '';
    return `<h2>${titel} <span class="still">(${b.length})</span></h2>
      <p class="unter">${erklaerung}</p>
      <div class="karte eng"><table class="t"><tbody>${b.map((x) => `
        <tr ${x.buchung ? `class="klick" onclick="setzeAbfrage('buchung', ${x.buchung.id})"` : ''}>
          <td>${esc(x.text)}${x.buchung
            ? `<div class="klein still">${esc(x.buchung.text)}</div>` : ''}</td></tr>`).join('')}
      </tbody></table></div>`;
  };
  inhalt.innerHTML = `
    ${kopf(`Prüfungen ${d.zeitraum.kurz}`,
           `${zeitraumZeile(d.zeitraum)} Was die Buchführung selbst prüfen kann.
            Eine Zeile anklicken öffnet den Buchungssatz.`)}
    ${d.befunde.length ? '' : `<div class="karte"><strong class="plus">Keine Beanstandungen.</strong>
       Soll gleich Haben, jede Buchung hat ein Gegenkonto, und erfolgswirksam wurde nur,
       wo Geld geflossen ist.</div>`}
    ${gruppe('fehler', 'Fehler',
             `Diese Buchungen verstoßen gegen eine Regel, die nicht im Ermessen steht.`)}
    ${gruppe('hinweis', 'Hinweise',
             `Kein Fehler, sondern etwas, das jemand ansehen muss – die Zehn-Tage-Regel des
              § 11 EStG lässt sich nicht automatisch entscheiden.`)}`;
};

/* ------------------------------------------------------------------- Steuern */

SEITEN.steuern = async () => {
  const d = await holen('/api/steuern');
  const jahr = d.jahr;
  if (d.fehler) { inhalt.innerHTML = kopf('Steuern', esc(d.fehler)); return; }
  if (d.fehlt) {
    // Laufendes Jahr: noch keine Überleitung - sagen, was ansteht und wo es weitergeht
    const link = (j) => `#/steuern?mandant=${encodeURIComponent(KONTEXT.mandant)}&jahr=${j}`;
    const frist = d.pflichten.filter((p) => p.status !== 'erledigt').map((p) => p.faellig).sort()[0];
    inhalt.innerHTML = `
      ${kopf(`Steuern ${d.jahr}`, d.laeuft_noch
        ? `Das Geschäftsjahr ${d.jahr} läuft noch bis ${datum(d.gj_ende)}. Die Überleitung vom Ergebnis
           zum zu versteuernden Einkommen entsteht mit dem Jahresabschluss — bis dahin gibt es hier nichts zu erklären.`
        : `Für ${d.jahr} ist die Steuerberechnung noch nicht angelegt. Sie entsteht mit dem Jahresabschluss
           (<span class="mono">${d.jahr}/steuern.json</span>).`)}
      <div class="kennzahlen">
        ${kz(d.laeuft_noch ? 'Vorläufiges Ergebnis bis heute' : 'Jahresergebnis',
             `<a href="#/guv?${kontextAbfrage()}">${eur(d.ergebnis)}</a>`, 'laut Buchführung, vor Steuern')}
        ${frist ? kz('Erklärungen fällig', datum(frist), d.laeuft_noch ? 'nach Ende des Geschäftsjahres' : '') : ''}
      </div>
      ${d.pflichten.length ? `<div class="karte eng"><table class="t">
        <thead><tr><th>Erklärung</th><th>Frist</th><th>Stand</th></tr></thead>
        <tbody>${d.pflichten.map((p) => `<tr><td>${esc(p.bezeichnung)}</td>
          <td class="nw klein">${datum(p.faellig)}</td>
          <td><span class="marke-chip ${p.status === 'erledigt' ? 'gut' : p.status === 'ueberfaellig' ? 'schlecht' : ''}">${
            esc({ ueberfaellig: 'überfällig', verlaengert: 'verlängert' }[p.status] || p.status)}</span></td></tr>`).join('')}
        </tbody></table></div>` : ''}
      ${d.andere_jahre.length ? `<p>Steuerberechnung vorhanden für ${d.andere_jahre.map((j) =>
        `<a href="${link(j)}">${j}</a>`).join(', ')}.</p>` : ''}`;
    return;
  }

  const zeile = (z, schluessel) => {
    if (z.art === 'gruppe') {
      return `<tr class="ueberschrift"><td colspan="2">${esc(z.text)}
        ${z.paragraf ? `<span class="still klein"> ${esc(z.paragraf)}</span>` : ''}</td></tr>`;
    }
    const klasse = z.art === 'summe' ? 'summe' : (z.art === 'ergebnis' ? 'ergebnis' : '');
    const kn = (z.konten_info || []).map((k) => k.nummer);
    const konten = (z.konten_info || []).map((k) =>
      `<a href="#/konto/${k.nummer}" class="marke-chip" title="${esc(k.bezeichnung)}">${k.nummer}</a>`).join(' ');
    const geprueft = z.abweichung !== undefined
      ? (z.abweichung === 0
          ? `<span class="marke-chip gut" title="stimmt mit den Konten überein">✓ gebucht</span>`
          : `<span class="marke-chip schlecht" title="Abweichung zur Buchhaltung">Abweichung ${eur(z.abweichung)}</span>`)
      : '';
    const ph = z.platzhalter
      ? `<span class="marke-chip schlecht" title="angenommener Wert, nicht belegt">Platzhalter</span>`
      : '';
    return `<tr class="${klasse}">
      <td class="${kn.length ? 'aufklapp' : ''}" ${kn.length ? `data-auf="${schluessel}"` : ''}>
        ${kn.length ? '<span class="pfeil">›</span> ' : ''}${esc(z.text)}
        ${z.paragraf ? `<span class="still klein"> ${esc(z.paragraf)}</span>` : ''}
        ${konten || geprueft || ph ? `<div style="margin-top:4px">${konten} ${geprueft} ${ph}</div>` : ''}
        ${z.platzhalter ? `<div class="klein" style="margin-top:3px;max-width:64ch">
          <strong class="minus">Angenommen, nicht belegt.</strong>
          ${esc(z.platzhalter.annahme || '')}
          ${z.platzhalter.grund ? ` ${esc(z.platzhalter.grund)}` : ''}
          ${z.platzhalter.aufloesen_durch ? ` <em>Aufzulösen durch: ${esc(z.platzhalter.aufloesen_durch)}</em>` : ''}
          </div>` : ''}
        ${z.hinweis ? `<div class="klein leise" style="margin-top:3px;max-width:64ch">${esc(z.hinweis)}</div>` : ''}
        ${z.rechnung ? `<div class="klein still" style="margin-top:2px">${esc(z.rechnung)}</div>` : ''}
      </td>
      <td class="z">${betrag(z.betrag)}</td></tr>
      ${kn.length ? einbauZeile(schluessel, kn, 2) : ''}`;
  };

  const abschnitte = d.abschnitte.map((a, ai) => `
    <h2>${esc(a.titel)}${a.hinweis ? ` <span class="still klein" style="font-weight:400">${esc(a.hinweis)}</span>` : ''}</h2>
    <div class="karte eng"><table class="t"><tbody>${
      a.zeilen.map((z, zi) => zeile(z, `s${ai}z${zi}`)).join('')}</tbody></table></div>`).join('');

  inhalt.innerHTML = `
    ${kopf(`${esc(KONTEXT.titel.steuern || 'Steuererklärung')} ${jahr}`,
           `${d.hebesatz_prozent ? `Hebesatz ${esc(d.gemeinde)} ${d.hebesatz_prozent} %. ` : ''}Jede Zeile, die auf Konten verweist,
            wird gegen die Buchhaltung geprüft — <span class="marke-chip gut">✓ gebucht</span> heißt: Betrag und Kontensaldo stimmen überein.
            Eine solche Zeile anklicken zeigt die Buchungen, aus denen sie besteht.`)}
    ${!d.gesperrt ? '' : `<div class="karte" style="border-color:var(--minus)">
        <strong class="minus">Abgabe gesperrt — ${d.platzhalter.length} Platzhalter.</strong>
        <p class="klein" style="max-width:72ch">Die Rechnung ist vollständig durchgerechnet, aber
        ${d.platzhalter.length === 1 ? 'eine Zeile trägt' : `${d.platzhalter.length} Zeilen tragen`}
        einen angenommenen Wert. Solange er dasteht, ist die Erklärung nicht abgabefähig
        (<span class="mono">bh abgabe</span> endet mit Rückgabewert 1).</p>
        <table class="t"><tbody>${d.platzhalter.map((p) => `<tr>
          <td>${esc(p.zeile)}
            <div class="klein leise" style="max-width:64ch">${esc(p.annahme || '')}
              ${p.grund ? ` ${esc(p.grund)}` : ''}
              ${p.aufloesen_durch ? `<br><em>Aufzulösen durch: ${esc(p.aufloesen_durch)}</em>` : ''}</div></td>
          <td class="z">${eur(p.betrag)}</td></tr>`).join('')}</tbody></table></div>`}
    ${d.abweichungen ? `<div class="karte" style="border-color:var(--minus)">
        <strong class="minus">${d.abweichungen} Zeile(n) weichen von der Buchhaltung ab.</strong></div>`
      : `<div class="karte"><span class="marke-chip gut">✓</span>
         Alle prüfbaren Zeilen stimmen mit den Kontensalden überein.</div>`}
    ${abschnitte}

    ${!d.positionen || !d.positionen.length ? '' : `
    <h2>Steuerpositionen in der Bilanz zum 31.12.${jahr}</h2>
    <div class="karte eng"><table class="t">
      <thead><tr><th>Konto</th><th>Position</th><th class="z">Betrag</th><th class="z">Kontensaldo</th></tr></thead>
      <tbody>${d.positionen.map((p) => `<tr>
        <td><a href="#/konto/${p.konto}" class="mono">${p.konto}</a></td>
        <td>${esc(p.text)}</td>
        <td class="z">${eur(p.betrag)}</td>
        <td class="z ${p.abweichung ? 'minus' : 'leise'}">${eur(p.konten_saldo)}${
          p.abweichung ? ` <span class="klein">(Abweichung ${eur(p.abweichung)})</span>` : ''}</td>
      </tr>`).join('')}</tbody></table></div>`}

    ${d.vergleich && d.vergleich.zeilen ? `<h2>Vergleich über die Jahre</h2>
    <div class="karte eng"><table class="t">
      <thead><tr><th></th>${d.vergleich.spalten.map((s) => `<th class="z">${s}</th>`).join('')}</tr></thead>
      <tbody>${d.vergleich.zeilen.map((z) => `<tr><td>${esc(z.text)}</td>
        ${z.werte.map((w) => `<td class="z">${w === null ? '<span class="still">–</span>' : eur(w)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
    ${d.vergleich.hinweis ? `<p class="klein leise" style="max-width:70ch">${esc(d.vergleich.hinweis)}</p>` : ''}` : ''}
    <p class="klein still">Ausführliche Begründungen im Dokument
      ${KONTEXT.art === 'privat'
        ? `<a href="#/dokument/einkommensteuer">Einkommensteuererklärung ${jahr}</a>`
        : `<a href="#/dokument/steuererklaerungen">Steuererklärungen ${jahr}</a>`}.</p>`;
};

/* -------------------------------------------------------------------- Konten */

const TYPNAME = { A: 'Aktiva', P: 'Passiva', E: 'Ertrag', X: 'Aufwand' };

SEITEN.konten = async () => {
  const d = await holen('/api/konten');
  inhalt.innerHTML = `
    ${kopf(`Konten · ${d.zeitraum.kurz}`,
           `${zeitraumZeile(d.zeitraum)} Summen- und Saldenliste. Eine Zeile anklicken
            klappt die Buchungen des Kontos auf; <em>Kontoblatt</em> öffnet es mit
            Anfangsbestand und laufendem Saldo.`)}
    <div class="werkzeug">
      <input type="search" id="suche" placeholder="Konto oder Bezeichnung suchen …">
      <button class="knopf an" data-typ="">alle</button>
      ${Object.entries(TYPNAME).map(([t, n]) => `<button class="knopf" data-typ="${t}">${n}</button>`).join('')}
      <span class="anzahl" id="anzahl"></span>
    </div>
    <div class="karte eng"><table class="t">
      <thead><tr><th>Konto</th><th>Bezeichnung</th><th>Art</th>
        <th class="z">Soll</th><th class="z">Haben</th><th class="z">Saldo</th></tr></thead>
      <tbody id="rumpf"></tbody><tfoot id="fuss"></tfoot></table></div>`;

  let typ = '', text = '';
  const male = () => {
    const zeilen = d.konten.filter((k) =>
      (!typ || k.typ === typ) &&
      (!text || (k.nummer + ' ' + k.bezeichnung).toLowerCase().includes(text)));
    // Die Summe zaehlt, was zu sehen ist. Ueber alle Konten muss Soll gleich
    // Haben und der Saldo damit null sein - das ist die Summenprobe. Sobald
    // gefiltert wird, ist sie die Summe des Ausschnitts und nicht mehr null.
    const s = zeilen.reduce((a, k) => a + k.soll, 0);
    const h = zeilen.reduce((a, k) => a + k.haben, 0);
    $('#fuss').innerHTML = `<tr class="summe">
      <td colspan="3">Summe${zeilen.length < d.konten.length ? ' der gezeigten Konten' : ''}</td>
      <td class="z">${eur(s)}</td><td class="z">${eur(h)}</td>
      <td class="z">${betrag(s - h)}</td></tr>`;
    $('#rumpf').innerHTML = zeilen.map((k) => `
      <tr class="klick" data-auf="k${k.nummer}">
        <td class="mono nw"><span class="pfeil">›</span> ${k.nummer}</td>
        <td>${esc(k.bezeichnung)}
          <a class="hinweg" href="#/konto/${k.nummer}">Kontoblatt ›</a></td>
        <td class="klein still">${TYPNAME[k.typ]}${k.steuer_tag ? ` · ${esc(k.steuer_tag)}` : ''}</td>
        <td class="z leise">${eur(k.soll, { nullLeer: true })}</td>
        <td class="z leise">${eur(k.haben, { nullLeer: true })}</td>
        <td class="z">${betrag(k.saldo)}</td></tr>
      ${einbauZeile('k' + k.nummer, [k.nummer], 6)}`).join('');
    $('#anzahl').textContent = `${zeilen.length} von ${d.konten.length} Konten`;
  };
  $('#suche').oninput = (e) => { text = e.target.value.toLowerCase(); male(); };
  document.querySelectorAll('[data-typ]').forEach((b) => {
    b.onclick = () => {
      typ = b.dataset.typ;
      document.querySelectorAll('[data-typ]').forEach((x) => x.classList.toggle('an', x === b));
      male();
    };
  });
  male();
};

/* ---------------------------------------------------------------- Kontoblatt */

SEITEN.konto = async (z) => {
  const nummer = z.param[0];
  const d = await holen(`/api/konto/${encodeURIComponent(nummer)}`);
  if (d.fehler) { inhalt.innerHTML = kopf('Konto', esc(d.fehler)); return; }
  const k = d.konto;

  inhalt.innerHTML = `
    ${kopf(`${k.nummer} ${k.bezeichnung}`,
           `${TYPNAME[k.typ]}${k.posten ? ` · Bilanz-/GuV-Posten ${esc(k.posten)}` : ''}
            ${k.steuer_tag ? ` · steuerliche Kennzeichnung ${esc(k.steuer_tag)}` : ''}
            <br>${zeitraumZeile(d.zeitraum)} Eine Zeile anklicken zeigt den vollständigen Buchungssatz mit Beleg.`,
           `<a href="#/konten">Konten</a> › ${esc(k.nummer)}`)}
    <div class="kennzahlen">
      ${kz('Saldo', betrag(d.saldo), `zum ${esc(d.zeitraum.stichtag)}`)}
      ${kz('Anfangsbestand', betrag(d.vortrag), `vor dem ${esc(d.zeitraum.label.split(' – ')[0])}`)}
      ${kz('Summe Soll', eur(d.summe_soll), 'im Zeitraum')}
      ${kz('Summe Haben', eur(d.summe_haben), 'im Zeitraum')}
      ${kz('Buchungen', String(d.zeilen.length), 'im Zeitraum')}
    </div>
    <div class="karte eng"><table class="t">
      <thead><tr><th class="z">Nr</th><th>Datum</th><th>Buchungstext</th><th>Gegenkonto</th>
        <th class="z">Soll</th><th class="z">Haben</th><th class="z">Saldo</th></tr></thead>
      <tbody>${d.zeilen.map((r) => `
        <tr class="klick" data-buchung="${r.nummer}">
          <td class="z still mono">${r.nummer}</td>
          <td class="nw">${datum(r.datum)}</td>
          <td>${esc(r.buchungstext)}${r.text && r.text !== r.buchungstext
            ? `<div class="klein still">${esc(r.text)}</div>` : ''}</td>
          <td class="mono klein leise">${r.gegenkonten.join(' ')}</td>
          <td class="z">${eur(r.soll_cent, { nullLeer: true })}</td>
          <td class="z">${eur(r.haben_cent, { nullLeer: true })}</td>
          <td class="z leise">${eur(r.saldo)}</td></tr>`).join('')}</tbody>
      <tfoot><tr class="summe">
        <td colspan="4">Summe · Saldo</td>
        <td class="z">${eur(d.zeilen.reduce((a, r) => a + r.soll_cent, 0))}</td>
        <td class="z">${eur(d.zeilen.reduce((a, r) => a + r.haben_cent, 0))}</td>
        <td class="z">${betrag(d.zeilen.length ? d.zeilen[d.zeilen.length - 1].saldo : 0)}</td>
      </tr></tfoot></table></div>`;
};

/* ------------------------------------------------------------------- Journal */

SEITEN.journal = async () => {
  const d = await holen('/api/journal');
  inhalt.innerHTML = `
    ${kopf(`Journal · ${d.zeitraum.kurz}`,
           `${zeitraumZeile(d.zeitraum)} Alle Buchungssätze in zeitlicher Folge.
            Eine Zeile anklicken öffnet den Satz mit Beleg.`)}
    <div class="werkzeug">
      <input type="search" id="suche" placeholder="Text, Konto oder Belegfeld suchen …">
      <button class="knopf an" data-art="">alle</button>
      <button class="knopf" data-art="eb">Eröffnung</button>
      <button class="knopf" data-art="lfd">laufend</button>
      <button class="knopf" data-art="abschluss">Abschluss</button>
      <button class="knopf" data-ohnebeleg="1">ohne Beleg</button>
      <span class="anzahl" id="anzahl"></span>
    </div>
    <div class="karte eng"><table class="t">
      <thead><tr><th class="z">Nr</th><th>Datum</th><th>Buchungstext</th><th>Konten</th>
        <th class="z">Betrag</th><th>Beleg</th></tr></thead>
      <tbody id="rumpf"></tbody></table></div>`;

  let art = '', text = '', ohneBeleg = false;
  const male = () => {
    const zeilen = d.buchungen.filter((b) =>
      (!art || b.art === art) && (!ohneBeleg || b.belege === 0) &&
      (!text || (b.buchungstext + ' ' + b.konten + ' ' + (b.belegfeld || '')).toLowerCase().includes(text)));
    $('#rumpf').innerHTML = zeilen.map((b) => `
      <tr class="klick" data-buchung="${b.nummer}">
        <td class="z still mono">${b.nummer}</td>
        <td class="nw">${datum(b.datum)}</td>
        <td>${esc(b.buchungstext)}</td>
        <td class="mono klein leise">${esc((b.konten || '').split(',').join(' '))}</td>
        <td class="z">${eur(b.betrag)}</td>
        <td class="klein">${b.belege
          ? `<span class="marke-chip gut">${b.belege} Beleg${b.belege > 1 ? 'e' : ''}</span>`
          : `<span class="marke-chip">${esc(b.belegfeld || '–')}</span>`}</td></tr>`).join('');
    $('#anzahl').textContent = `${zeilen.length} von ${d.buchungen.length} Buchungen`;
  };
  $('#suche').oninput = (e) => { text = e.target.value.toLowerCase(); male(); };
  document.querySelectorAll('[data-art]').forEach((b) => {
    b.onclick = () => {
      art = b.dataset.art; ohneBeleg = false;
      document.querySelectorAll('.werkzeug .knopf').forEach((x) => x.classList.toggle('an', x === b));
      male();
    };
  });
  document.querySelector('[data-ohnebeleg]').onclick = (e) => {
    ohneBeleg = !ohneBeleg; art = '';
    document.querySelectorAll('.werkzeug .knopf').forEach((x) => x.classList.remove('an'));
    e.target.classList.toggle('an', ohneBeleg);
    male();
  };
  male();
};

/* ---------------------------------------------------------------------- Bank */

SEITEN.bank = async () => {
  const d = await holen('/api/bank');
  const proKonto = {};
  d.umsaetze.forEach((u) => (proKonto[u.iban] = proKonto[u.iban] || []).push(u));

  const block = (iban, liste) => {
    const bank = d.bankkonten.find((b) => b.iban === iban) || {};
    const ein = liste.filter((u) => u.betrag_cent > 0).reduce((s, u) => s + u.betrag_cent, 0);
    const aus = liste.filter((u) => u.betrag_cent < 0).reduce((s, u) => s + u.betrag_cent, 0);
    return `<h2>${esc(bank.bezeichnung || iban)}
        <span class="still klein" style="font-weight:400">${esc(iban)}
        ${bank.konto ? `· Sachkonto <a href="#/konto/${bank.konto}">${bank.konto}</a>` : ''}</span></h2>
      <div class="kennzahlen">
        ${kz('Umsätze', String(liste.length))}
        ${kz('Eingänge', eur(ein))}
        ${kz('Ausgänge', eur(aus))}
      </div>
      <div class="karte eng"><table class="t">
        <thead><tr><th>Datum</th><th class="z">Auszug</th><th>Verwendungszweck</th>
          <th class="z">Betrag</th><th>Buchung</th></tr></thead>
        <tbody>${liste.map((u) => `
          <tr class="${u.buchung_nummer ? 'klick' : ''}" ${u.buchung_nummer ? `data-buchung="${u.buchung_nummer}"` : ''}>
            <td class="nw">${datum(u.buchungsdatum)}</td>
            <td class="z still mono">${esc(u.auszug || '')}</td>
            <td class="klein">${esc((u.text || '').replace(/\s+/g, ' ').slice(0, 130))}</td>
            <td class="z">${betrag(u.betrag_cent, { gruen: true })}</td>
            <td class="klein">${u.buchung_nummer
              ? `<span class="marke-chip gut">Nr ${u.buchung_nummer}</span>`
              : (u.nicht_gebucht_grund
                  ? `<span class="marke-chip" title="bewusst nicht gebucht">${esc(u.nicht_gebucht_grund)}</span>`
                  : '<span class="marke-chip schlecht">nicht gebucht</span>')}</td>
          </tr>`).join('')}</tbody>
        <tfoot><tr class="summe">
          <td colspan="3">Summe · ${liste.length} Umsätze</td>
          <td class="z">${betrag(liste.reduce((a, u) => a + u.betrag_cent, 0), { gruen: true })}</td>
          <td></td></tr></tfoot></table></div>`;
  };

  inhalt.innerHTML = `
    ${kopf(`Bankumsätze · ${d.zeitraum.kurz}`, `${zeitraumZeile(d.zeitraum)}
       Rohdaten aus den Kontoauszügen, unverändert wie eingelesen.
       Gebuchte Zeilen anklicken zeigt den Buchungssatz. Umsätze mit einem Vermerk sind
       bewusst nicht gebucht — Informationszeilen oder die Gegenseite interner Umbuchungen.`)}
    ${Object.entries(proKonto).map(([iban, liste]) => block(iban, liste)).join('')}`;
};

/* -------------------------------------------------------------------- Belege */

SEITEN.belege = async () => {
  const d = await holen('/api/belege');
  const kategorien = [...new Set(d.belege.map((b) => b.kategorie))].sort();
  const jahre = [...new Set(d.belege.map((b) => b.jahr))].sort((a, b) => b - a);

  inhalt.innerHTML = `
    ${kopf('Belegarchiv', `Jeder Beleg liegt unter seiner Nummer im Archiv und ist über den
       SHA-256-Hash gegen doppelte Ablage gesichert. Eine Zeile anklicken öffnet das Dokument.`)}
    <div class="werkzeug">
      <input type="search" id="suche" placeholder="Bezeichnung, Aussteller oder Belegnummer suchen …">
      <select id="jahr"><option value="">alle Jahre</option>
        ${jahre.map((j) => `<option ${j === 2025 ? 'selected' : ''}>${j}</option>`).join('')}</select>
      <select id="kat"><option value="">alle Kategorien</option>
        ${kategorien.map((k) => `<option>${k}</option>`).join('')}</select>
      <span class="anzahl" id="anzahl"></span>
    </div>
    <div class="karte eng"><table class="t">
      <thead><tr><th>Beleg</th><th>Datum</th><th>Kategorie</th><th>Aussteller</th>
        <th>Bezeichnung</th><th class="z">Buchungen</th></tr></thead>
      <tbody id="rumpf"></tbody></table></div>`;

  let text = '', jahr = String(jahre.includes(2025) ? 2025 : ''), kat = '';
  const male = () => {
    const zeilen = d.belege.filter((b) =>
      (!jahr || String(b.jahr) === jahr) && (!kat || b.kategorie === kat) &&
      (!text || (b.belegnr + ' ' + b.bezeichnung + ' ' + (b.aussteller || '')).toLowerCase().includes(text)));
    $('#rumpf').innerHTML = zeilen.map((b) => `
      <tr class="klick" data-beleg="${b.id}">
        <td class="mono">${b.belegnr}</td>
        <td class="nw">${datum(b.datum) || '<span class="still">ohne Datum</span>'}</td>
        <td class="klein leise">${esc(b.kategorie)}</td>
        <td class="klein">${esc(b.aussteller || '')}</td>
        <td class="klein">${esc(b.bezeichnung.slice(0, 90))}</td>
        <td class="z klein">${b.buchungen
          ? `<span class="marke-chip gut">${b.buchungen}</span>`
          : '<span class="still">–</span>'}</td></tr>`).join('');
    $('#anzahl').textContent = `${zeilen.length} von ${d.belege.length} Belegen`;
  };
  $('#suche').oninput = (e) => { text = e.target.value.toLowerCase(); male(); };
  $('#jahr').onchange = (e) => { jahr = e.target.value; male(); };
  $('#kat').onchange = (e) => { kat = e.target.value; male(); };
  male();
};

/* ------------------------------------------------------------ Offene Punkte */

const PUNKT_ART = {
  feststellung: ['Feststellung', 'warn'],
  frist:        ['Frist', 'schlecht'],
  entscheidung: ['Entscheidung', ''],
  unterlage:    ['Unterlage fehlt', 'warn'],
  klaerung:     ['Klärung', ''],
  hinweis:      ['Hinweis', ''],
};

const PUNKT_STATUS = {
  offen:        ['offen', 'warn'],
  behoben:      ['korrigiert', 'gut'],
  teilweise:    ['teilweise erledigt', 'warn'],
  entschieden:  ['entschieden', 'gut'],
  erledigt:     ['erledigt', 'gut'],
};

const WER = {
  du: ['du', 'warn'],
  extern:  ['Dritte', ''],
  app:     ['Anwendung', ''],
};

SEITEN.offen = async (z0) => {
  const d = await holen('/api/offen');
  const z = d.zaehler;

  const marke = (paar, extra = '') =>
    paar ? `<span class="marke-chip ${paar[1]} ${extra}">${esc(paar[0])}</span>` : '';

  const punkt = (p) => {
    const erledigt = p.status === 'erledigt' || p.status === 'entschieden';
    const klasse = erledigt ? p.status : (p.prioritaet || '');
    return `<div class="punkt ${klasse}" id="punkt-${esc(p.id)}">
      <div class="zeile1">
        <span class="kennung">${esc(p.id)}</span>
        <span class="titel">${esc(p.titel)}</span>
        <span class="marken">
          ${marke(PUNKT_ART[p.art])}
          ${marke(PUNKT_STATUS[p.status])}
          ${p.ueberfaellig ? '<span class="marke-chip schlecht">überfällig</span>' : ''}
        </span>
      </div>
      ${p.aktion && !erledigt && p.status !== 'behoben' ? `<div class="aktion">
        <span class="marke-chip ${WER[p.wer]?.[1] || 'warn'}">${esc(WER[p.wer]?.[0] || 'du')}</span>
        <span>${esc(p.aktion)}</span>
        ${(p.blockiert || []).length ? `<span class="marke-chip schlecht"
          title="Diese Abgaben warten auf den Punkt">blockiert ${p.blockiert.map(esc).join(', ')}</span>` : ''}
      </div>` : ''}
      <div class="text dok">${markdown(p.text)}</div>
      <div class="fuss">
        ${p.frist ? `<span><strong>Frist</strong> ${datum(p.frist)}</span>` : ''}
        ${p.adressat ? `<span><strong>bei</strong> ${esc(p.adressat)}</span>` : ''}
        ${p.wirkung_cent !== undefined
          ? `<span class="wirkung"><strong>Wirkung</strong> ${betrag(p.wirkung_cent)} EUR</span>` : ''}
        ${p.wirkung_text ? `<span class="wirkung">${esc(p.wirkung_text)}</span>` : ''}
        ${(p.konten || []).map((k) =>
            `<a href="#/konto/${k}" class="marke-chip">${k}</a>`).join(' ')}
        ${(p.belege || []).map((b) =>
            `<span class="marke-chip">${esc(b)}</span>`).join(' ')}
        ${p.verweis ? `<a href="#/dokument/${esc(p.verweis)}?mandant=${
            encodeURIComponent(KONTEXT.mandant)}&jahr=${p.verweis_jahr || KONTEXT.jahr}"
            class="marke-chip">Dokument${p.verweis_jahr ? ' ' + p.verweis_jahr : ''}</a>` : ''}
      </div></div>`;
  };

  const gruppe = (titel, erklaerung, filter) => {
    const p = d.punkte.filter(filter);
    if (!p.length) return '';
    return `<h2>${esc(titel)} <span class="still">(${p.length})</span></h2>
      <p class="unter">${esc(erklaerung)}</p>${p.map(punkt).join('')}`;
  };

  const belegzeile = (b) => `<tr class="${b.unkritisch ? 'leise' : ''}">
    <td class="nw">${datum(b.datum)}</td>
    <td class="mono klein">${esc(b.belegfeld || '')}</td>
    <td class="klein">${esc(b.buchungstext)}</td>
    <td class="z">${eur(b.betrag_cent)}</td>
    <td class="klein leise">${b.unkritisch
      ? `<span class="marke-chip gut">kein Fremdbeleg nötig</span> ${esc(b.unkritisch)}`
      : (b.beschaffung ? esc(b.beschaffung) : '<span class="marke-chip warn">Quelle unklar</span>')}</td>
    <td class="z"><a href="#/journal?buchung=${b.id}" class="marke-chip">Nr. ${b.nummer}</a></td></tr>`;

  inhalt.innerHTML = `
    ${kopf(`Offene Punkte ${d.jahr || ''}`,
           `${esc(d.einleitung || '')} <span class="marke-chip">Stand ${datum(d.stand)}</span>`)}
    <div class="offen-kopf">
      ${kz('offen', `<span class="zahl ${z.offen ? 'minus' : 'plus'}">${z.offen}</span>`,
           `von ${z.gesamt} Punkten insgesamt`)}
      ${kz('Belege fehlen', `<span class="zahl ${z.belege_offen ? 'minus' : 'plus'}">${z.belege_offen}</span>`,
           `von ${z.belege_gesamt} Buchungen ohne Beleg`)}
      ${kz('Feststellungen',
           (() => { const f = d.punkte.filter((p) => p.art === 'feststellung');
                    const b = f.filter((p) => p.status === 'behoben').length;
                    return `<span class="zahl ${b === f.length ? 'plus' : ''}">${b}/${f.length}</span>`; })(),
           'aus der Prüfung korrigiert')}
      ${kz('nächste Frist', d.punkte.filter((p) => p.frist && p.status === 'offen').length
            ? datum(d.punkte.filter((p) => p.frist && p.status === 'offen')
                     .map((p) => p.frist).sort()[0])
            : '<span class="still">keine</span>', 'Steuererklärungen und Offenlegung')}
    </div>

    ${gruppe('Feststellungen aus der Prüfung',
             `Alle Feststellungen der Nachprüfung, auch die schon erledigten. `
             + `Korrigiert sind die Punkte, die sich aus den Belegen eindeutig ergaben; `
             + `offen bleibt, was eine Entscheidung oder einen fehlenden Nachweis braucht.`,
             (p) => p.art === 'feststellung')}
    ${gruppe('Fristen und Entscheidungen',
             'Was terminiert ist oder entschieden werden muss, bevor etwas anderes weitergeht.',
             (p) => ['frist', 'entscheidung'].includes(p.art) && p.status !== 'erledigt')}
    ${gruppe('Fehlende Unterlagen',
             'Nachweise, die angefordert sind oder angefordert werden müssen.',
             (p) => p.art === 'unterlage' && p.status !== 'erledigt')}
    ${gruppe('Offene Fragen',
             'Zuordnungen und Annahmen, die eine Bestätigung brauchen. Bis dahin gilt, was im Abschluss steht.',
             (p) => ['klaerung', 'hinweis'].includes(p.art) && !['erledigt'].includes(p.status))}
    ${gruppe('Erledigt',
             'Bleibt stehen, damit nachvollziehbar ist, wie der Punkt ausgegangen ist.',
             (p) => p.status === 'erledigt' && p.art !== 'feststellung')}

    <h2>Buchungen ohne Beleg <span class="still">(${d.fehlende_belege.length})</span></h2>
    <p class="unter">Diese Liste wird nicht gepflegt, sondern bei jedem Aufruf aus der
      Buchführung gezogen — sie kann deshalb nicht veralten. Aus der Ablage kommt nur
      die Anmerkung, wo der Beleg zu holen ist oder warum keiner nötig ist.</p>
    <div class="karte eng"><table class="t">
      <thead><tr><th>Datum</th><th>Beleg&shy;feld</th><th>Buchung</th><th class="z">Betrag</th>
        <th>Woher</th><th class="z">Journal</th></tr></thead>
      <tbody>${d.fehlende_belege.map(belegzeile).join('')}</tbody></table></div>`;
  // Sprung auf einen einzelnen Punkt (#/offen?punkt=P4) - von der Aufgabenliste aus.
  const ziel = new URLSearchParams(location.hash.split('?')[1] || '').get('punkt');
  const el = ziel && document.getElementById(`punkt-${ziel}`);
  if (el) { el.classList.add('markiert'); setTimeout(() => el.scrollIntoView({ block: 'start' }), 0); }
};

/* ----------------------------------------------------------------- Dokumente */

SEITEN.dokument = async (z) => {
  const schluessel = z.param[0];
  const d = await holen(`/api/dokument/${encodeURIComponent(schluessel)}`);
  if (d.fehler) {
    // Der haeufige Fall ist kein Tippfehler in der Adresse, sondern ein
    // Mandanten- oder Jahreswechsel auf einer Dokumentseite: die Adresse
    // bleibt stehen, das Dokument gibt es dort aber nicht. Statt einer
    // Sackgasse zeigen wir, wo man ist und was es hier tatsaechlich gibt.
    const vorhanden = await holen('/api/dokumente').catch(() => ({ dokumente: [] }));
    const wer = `${$('#firma').textContent || KONTEXT.mandant} ${KONTEXT.jahr}`;
    const liste = vorhanden.dokumente.length
      ? `<p class="unter">Für ${esc(wer)} liegen diese Unterlagen vor:</p>
         <ul class="dokwahl">${vorhanden.dokumente.map((x) =>
            `<li><a href="#/dokument/${x.id}?${kontextAbfrage()}">${esc(x.titel)}</a>
               <span class="mono still">${esc(x.datei)}</span></li>`).join('')}</ul>`
      : `<p class="unter">Für ${esc(wer)} ist noch keine Unterlage abgelegt.</p>`;
    inhalt.innerHTML = `${kopf('Dokument nicht gefunden',
        `<span class="mono">${esc(schluessel)}</span> gibt es in diesem Geschäftsjahr nicht.`)}
      <div class="karte">${liste}
        <p><a href="#/uebersicht?${kontextAbfrage()}">Zur Übersicht</a></p></div>`;
    return;
  }
  // Die PDF-Fassung entsteht auf dem Server aus demselben Markdown - zum
  // Unterschreiben, Hinterlegen und Abheften.
  const pdf = `/dokument/${encodeURIComponent(schluessel)}.pdf?${kontextAbfrage()}`;
  const ansicht = d.pdf && /(^|&)ansicht=pdf(&|$)/.test(location.hash.split('?')[1] || '') ? 'pdf' : 'text';
  inhalt.innerHTML = `${kopf(d.titel, `<span class="mono">${esc(d.datei)}</span>`)}
    ${d.pdf ? `<div class="dokleiste"><span class="seg" role="group" aria-label="Ansicht">
        <button type="button" data-zeige="text">Text</button
        ><button type="button" data-zeige="pdf">PDF</button></span>
        <a class="knopf" href="${pdf}" target="_blank" rel="noopener">PDF in neuem Tab</a></div>` : ''}
    <div class="dokansicht" data-ansicht="${ansicht}">
      ${d.format === 'text'
        ? `<div class="vorformatiert">${esc(d.text)}</div>`
        : `<div class="dok nurtext">${markdown(d.text)}</div>`}
      ${d.pdf ? '<div class="karte eng nurpdf"><iframe class="dokpdf" title="PDF"></iframe></div>' : ''}
    </div>`;
  const rahmen = inhalt.querySelector('.dokansicht');
  const zeige = (art) => {
    rahmen.dataset.ansicht = art;
    inhalt.querySelectorAll('.dokleiste [data-zeige]').forEach((b) =>
      b.setAttribute('aria-pressed', String(b.dataset.zeige === art)));
    const f = inhalt.querySelector('iframe.dokpdf');
    if (art === 'pdf' && f && !f.getAttribute('src')) f.src = `${pdf}#view=FitH`;
  };
  inhalt.querySelectorAll('.dokleiste [data-zeige]').forEach((b) => {
    b.onclick = () => { zeige(b.dataset.zeige); setzeAbfrage('ansicht', b.dataset.zeige === 'pdf' ? 'pdf' : null); };
  });
  zeige(ansicht);
};

/* ------------------------------------------------------------- Umsatzsteuer */

const UST_STATUS = {
  offen: ['', 'noch nicht übermittelt'],
  abgegeben: ['gut', 'übermittelt'],
  abgelehnt: ['schlecht', 'vom Finanzamt abgelehnt'],
};

SEITEN.ust = async () => {
  const d = await holen('/api/ust');
  const e = d.einstellungen;
  const heute = new Date().toISOString().slice(0, 10);

  const offen = d.zeitraeume.filter((z) => z.status === 'offen' && !z.nur_historie);
  const naechste = offen.map((z) => z.frist).sort()[0];
  const zuZahlen = offen.filter((z) => z.kz83 > 0).reduce((s, z) => s + z.kz83, 0);
  const zuErstatten = offen.filter((z) => z.kz83 < 0).reduce((s, z) => s + z.kz83, 0);

  inhalt.innerHTML = `
    ${kopf('Umsatzsteuer-Voranmeldungen',
           `${e.besteuerung === 'soll' ? 'Soll-Versteuerung' : 'Ist-Versteuerung'},
            ${e.zeitraum === 'vierteljaehrlich' ? 'vierteljährlich' : 'monatlich'}${
              e.dauerfristverlaengerung_ab ? `, Dauerfristverlängerung ab ${e.dauerfristverlaengerung_ab}` : ''}.
            Jede Kennzahl lässt sich bis zum Beleg aufklappen.`)}

    <div class="kennzahlen">
      ${kz('Offene Zeiträume', String(offen.length),
           naechste ? `nächste Frist ${datum(naechste)}` : 'nichts fällig')}
      ${kz('Noch zu zahlen', eur(zuZahlen), 'aus offenen Zeiträumen')}
      ${kz('Noch zu erstatten', eur(Math.abs(zuErstatten)), 'aus offenen Zeiträumen')}
      ${kz('Übermittelt', String(d.zeitraeume.filter((z) => z.status === 'abgegeben').length),
           `von ${d.zeitraeume.length} erfassten`)}
    </div>

    ${e.hinweis ? `<div class="karte"><p class="klein" style="margin:0;max-width:78ch">${esc(e.hinweis)}</p></div>` : ''}

    <h2>Zeiträume</h2>
    <div class="karte eng"><table class="t">
      <thead><tr><th>Zeitraum</th><th>Stand</th><th class="z">Kz 83</th><th>Frist</th>
        <th>Transferticket</th><th class="z">ZM</th></tr></thead>
      <tbody>${d.zeitraeume.map((z) => `
        <tr class="${z.nur_historie ? '' : 'klick'}" ${z.nur_historie ? ''
            : `onclick="location.hash='#/ustva/${z.jahr}/${z.code}?mandant=${encodeURIComponent(KONTEXT.mandant)}&jahr=${z.jahr}'"`}>
          <td><strong>${esc(z.bezeichnung)}</strong>
            <div class="klein still">${datum(z.von)} – ${datum(z.bis)}
              ${z.nur_historie ? ' · nur Historie, keine Buchungen' : ''}</div></td>
          <td><span class="marke-chip ${UST_STATUS[z.status]?.[0] || ''}">${esc(z.status)}</span>
            ${z.zahlung_offen ? '<span class="marke-chip warn">unbezahlt</span>' : ''}
            ${z.zahlung_am ? `<div class="klein still">bezahlt ${datum(z.zahlung_am)}</div>` : ''}
            ${z.notiz ? `<div class="klein leise" style="max-width:46ch;margin-top:3px">${esc(z.notiz)}</div>` : ''}</td>
          <td class="z">${z.kz83 === null ? '<span class="still">–</span>' : betrag(z.kz83)}</td>
          <td class="nw klein ${z.ueberfaellig || z.zahlung_ueberfaellig ? 'minus' : ''}">${datum(z.frist)}
            ${z.ueberfaellig ? '<div class="klein">überfällig</div>'
              : z.zahlung_ueberfaellig ? '<div class="klein">Zahlung überfällig</div>'
              : z.zahlung_offen ? '<div class="klein">Zahlung fällig</div>' : ''}</td>
          <td class="klein mono leise">${esc(z.transferticket || '–')}
            ${z.abgegeben_am ? `<div class="klein still">${datum(z.abgegeben_am)}</div>` : ''}</td>
          <td class="z klein">${z.zm_summe
            ? `${eur(z.zm_summe)}<div class="klein ${z.zm_stand?.status === 'abgegeben' ? 'still' : 'minus'}">${
                z.zm_stand?.status === 'abgegeben' ? 'gemeldet' : 'offen bis ' + datum(z.zm_frist)}</div>`
            : '<span class="still">–</span>'}</td>
        </tr>`).join('')}</tbody></table></div>

    <p class="klein still" style="max-width:74ch">Kz 83 ist die verbleibende Vorauszahlung:
      positiv heißt Zahllast, negativ Erstattungsanspruch. Die Zahlen kommen aus denselben
      Buchungen wie Bilanz und Gewinn- und Verlustrechnung — es gibt keine zweite Rechnung
      daneben.</p>`;
};

/* Zusammenfassende Meldungen: je Quartal, was an das BZSt zu melden ist (aus
 * den Erlösbuchungen auf Kz 21) und was gemeldet wurde. Gemeldet werden volle
 * Euro je Kunde; das BZSt gleicht die Summe gegen Kz 21 der Voranmeldung ab. */
SEITEN.zm = async () => {
  const d = await holen('/api/zm', true);
  const offen = d.zeitraeume.filter((z) => z.status !== 'abgegeben' && !z.nur_historie);
  const naechste = offen.map((z) => z.frist).filter(Boolean).sort()[0];
  const zielLauf = (z) => (z.lauf ? `#/lauf/${encodeURIComponent(z.lauf)}?${kontextAbfrage()}` : null);

  inhalt.innerHTML = `
    ${kopf('Zusammenfassende Meldungen',
      `Innergemeinschaftliche sonstige Leistungen an Unternehmer in anderen EU-Staaten, je
       Quartal an das Bundeszentralamt für Steuern. ${esc(d.hinweis)}
       Eine Zeile anklicken zeigt Kunden und Buchungen.`)}
    <div class="kennzahlen">
      ${kz('Offene Meldungen', String(offen.length), naechste ? `nächste Frist ${datum(naechste)}` : 'nichts fällig')}
      ${kz('Gemeldet', String(d.zeitraeume.filter((z) => z.status === 'abgegeben').length),
           `von ${d.zeitraeume.length} Quartalen`)}
      ${kz('Weicht ab', String(d.zeitraeume.filter((z) => z.abweichend).length),
           'übermittelt ≠ Buchführung heute')}
    </div>
    <div class="karte eng"><table class="t">
      <thead><tr><th>Zeitraum</th><th>Stand</th><th class="z">Meldebetrag</th><th class="z">Kz 21</th>
        <th>Kunden</th><th>Frist</th><th>Transferticket</th><th></th></tr></thead>
      <tbody>${d.zeitraeume.map((z, i) => `
        <tr class="${z.kunden.length ? 'klick' : ''}" data-zm="${i}">
          <td class="nw"><span class="pfeil">${z.kunden.length ? '›' : ''}</span>
            <strong>${esc(z.bezeichnung)}</strong>
            <div class="klein still">${datum(z.von)} – ${datum(z.bis)}${z.nur_historie ? ' · nur Historie' : ''}</div></td>
          <td><span class="marke-chip ${UST_STATUS[z.status]?.[0] || ''}">${esc(z.status)}</span>
            ${z.abweichend ? `<span class="marke-chip warn">⚠ ${z.abweichend} weicht ab</span>`
              : z.werte_erfasst ? '<span class="marke-chip gut">✓ stimmt</span>' : ''}
            ${z.fehler ? `<div class="klein minus" style="max-width:40ch">${esc(z.fehler)}</div>` : ''}</td>
          <td class="z">${z.meldebetrag_euro ? NF.format(z.meldebetrag_euro).replace(',00', '') + ' €' : '<span class="still">–</span>'}</td>
          <td class="z ${z.kz21 === z.meldebetrag_euro ? '' : 'minus'}" title="Kz 21 der Voranmeldung desselben Quartals">${
            z.nur_historie ? '<span class="still">–</span>' : NF.format(z.kz21).replace(',00', '') + ' €'}</td>
          <td class="klein">${z.kunden.map((c) => `<span class="mono">${esc(c.ust_idnr)}</span>`).join(', ')
            || '<span class="still">–</span>'}</td>
          <td class="nw klein ${z.ueberfaellig ? 'minus' : ''}">${datum(z.frist)}${z.ueberfaellig ? '<div>überfällig</div>' : ''}</td>
          <td class="mono klein leise">${esc(z.transferticket || '–')}
            ${z.abgegeben_am ? `<div class="still">${datum(z.abgegeben_am)}</div>` : ''}</td>
          <td class="z klein">${zielLauf(z) ? `<a href="${zielLauf(z)}" onclick="event.stopPropagation()">${
              z.lauf_modus === 'senden' ? 'Übermittlung' : 'letzte Prüfung'} →</a>`
            : z.status !== 'abgegeben' && !z.laeuft_noch && !z.nur_historie
              ? `<button type="button" class="knopf" data-eric="pruefen" data-art="zm"
                  data-jahr="${z.jahr}" data-zeitraum="${z.code}">prüfen</button>`
              : z.laeuft_noch ? '<span class="still">läuft noch</span>' : ''}</td>
        </tr>
        <tr class="unterzeile" data-zm-unter="${i}" hidden><td colspan="8" class="einbau">
          <table class="t">
            <thead><tr><th>USt-IdNr.</th><th class="z">gebucht</th><th class="z">Meldebetrag</th>
              <th class="z">übermittelt</th><th>Buchungen</th></tr></thead>
            <tbody>${z.kunden.map((c) => `<tr>
              <td class="mono">${esc(c.ust_idnr)}</td>
              <td class="z">${eur(c.betrag)}</td>
              <td class="z">${c.euro === null || c.euro === undefined ? '–' : c.euro + ' €'}</td>
              <td class="z ${c.uebermittelt !== null && c.uebermittelt !== undefined
                && String(c.uebermittelt) !== String(c.euro) ? 'minus' : ''}">${
                c.uebermittelt === null || c.uebermittelt === undefined
                  ? '<span class="still">nicht erfasst</span>' : esc(c.uebermittelt) + ' €'}</td>
              <td><table class="t">${c.zeilen.map((r) => `
                <tr class="klick" data-buchung="${r.nummer}">
                  <td class="z still mono">${r.nummer}</td><td class="nw">${datum(r.datum)}</td>
                  <td>${esc(r.buchungstext)}</td><td class="z">${eur(r.betrag)}</td></tr>`).join('')}
              </table></td></tr>`).join('')}</tbody></table>
          ${z.notiz ? `<p class="klein leise" style="margin:8px 0 0;max-width:80ch">${esc(z.notiz)}</p>` : ''}
        </td></tr>`).join('') || '<tr><td colspan="8" class="still">Keine innergemeinschaftlichen Leistungen.</td></tr>'}
      </tbody></table></div>
    <p class="klein still" style="max-width:78ch">Meldebetrag: volle Euro je Kunde, abgeschnitten.
      „übermittelt“ steht da, wo die gemeldeten Werte festgehalten sind — bei Abgaben über ERiC
      immer, bei Abgaben von Hand in Mein ELSTER nur, wenn sie nachgetragen wurden.
      Abgegeben wird unter ELSTER → Übermittlungen.</p>`;

  inhalt.querySelectorAll('tr[data-zm]').forEach((tr) => {
    tr.onclick = (ev) => {
      if (ev.target.closest('button, a')) return;
      const unten = inhalt.querySelector(`tr[data-zm-unter="${tr.dataset.zm}"]`);
      const pfeil = tr.querySelector('.pfeil');
      if (!unten || !pfeil.textContent) return;
      unten.hidden = !pfeil.classList.toggle('offen');
    };
  });
  inhalt.querySelectorAll('button[data-eric="pruefen"]').forEach((b) => {
    b.onclick = () => ericAktion(b, 'pruefen',
      { art: b.dataset.art, jahr: b.dataset.jahr, zeitraum: b.dataset.zeitraum });
  });
  inhalt.querySelectorAll('button[data-verwerfen]').forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      const grund = b.parentElement.querySelector('[data-grund]').value;
      const r = await posten('/api/eric/verwerfen', { lauf: b.dataset.verwerfen, grund });
      if (r.ok) { freigabezahlSetzen(); SEITEN.eric(); return; }
      b.disabled = false;
      b.insertAdjacentHTML('afterend', `<div class="aktionsfehler">${esc(r.fehler || 'Fehler')}</div>`);
    };
  });
};

SEITEN.ustva = async (z) => {
  const [jahr, code] = z.param;
  const d = await holen(`/api/ust/${jahr}/${code}`);
  if (d.fehler) { inhalt.innerHTML = kopf('Voranmeldung', esc(d.fehler)); return; }

  const REIHE = ['81', '86', '21', '45', '46', '47', '84', '85', '66', '67'];
  const istBasis = (k) => ['81', '86', '21', '45', '46', '84'].includes(k);
  const wert = (k) => (istBasis(k) ? d.basen[k] * 100 : d.steuern[k]);

  const zeilen = REIHE.filter((k) => wert(k) !== 0).map((k) => `
    <tr class="klick" data-kz="${k}">
      <td class="mono"><span class="pfeil">›</span> ${k}</td>
      <td>${esc(d.bezeichnungen[k])}</td>
      <td class="z">${eur(wert(k))}${istBasis(k) ? '<span class="still klein"> €</span>' : ''}</td>
    </tr>
    <tr class="unterzeile" data-unter="${k}" hidden><td colspan="3" class="einbau">
      <div class="laedt klein">lädt …</div></td></tr>`).join('');

  const abweichungen = Object.entries(d.abweichung).filter(([, v]) => v);

  inhalt.innerHTML = `
    ${kopf(`Umsatzsteuer-Voranmeldung · ${d.bezeichnung}`,
           `${datum(d.von)} – ${datum(d.bis)} · Abgabefrist ${datum(d.frist)}${
             d.dauerfrist ? ' (mit Dauerfristverlängerung)' : ''} ·
            Steuernummer ${esc(d.mandant.steuernummer)} · Zeitraum-Schlüssel ${d.code}.
            Eine Kennzahl anklicken zeigt die Buchungen dahinter.`,
           `<a href="#/ust">Voranmeldungen</a> › ${esc(d.bezeichnung)}`)}

    <div class="karte">
      <span class="marke-chip ${UST_STATUS[d.stand.status]?.[0] || ''}">${esc(d.stand.status)}</span>
      ${d.stand.transferticket
        ? `übermittelt am ${datum(d.stand.abgegeben_am)}, Transferticket
           <span class="mono">${esc(d.stand.transferticket)}</span>`
        : 'noch nicht übermittelt'}
      ${d.stand.notiz ? `<div class="klein leise" style="margin-top:6px;max-width:74ch">${esc(d.stand.notiz)}</div>` : ''}
    </div>

    <h2>In Mein Elster eintragen</h2>
    <div class="karte eng"><table class="t">
      <thead><tr><th style="width:70px">Kz</th><th>Bezeichnung</th><th class="z">Betrag</th></tr></thead>
      <tbody>${zeilen}</tbody>
      <tfoot><tr class="ergebnis">
        <td class="mono">83</td>
        <td>${esc(d.bezeichnungen['83'])}${d.steuern['83'] < 0 ? ' (Erstattung)' : ''}</td>
        <td class="z">${betrag(d.steuern['83'])}</td></tr></tfoot></table></div>
    <p class="klein still">Bemessungsgrundlagen in vollen Euro, Steuerbeträge mit Cent.
      Die Steuer auf Kz 81 und 86 rechnet Elster selbst.</p>

    <h2>Kontrollrechnung</h2>
    <div class="karte eng"><table class="t"><tbody>
      <tr><td>Umsatzsteuer 19 % auf Kz 81</td><td class="z">${eur(d.kontrolle.steuer_81)}</td></tr>
      ${d.kontrolle.steuer_86 ? `<tr><td>Umsatzsteuer 7 % auf Kz 86</td><td class="z">${eur(d.kontrolle.steuer_86)}</td></tr>` : ''}
      ${d.steuern['47'] ? `<tr><td>Steuer § 13b Abs. 1 (Kz 47)</td><td class="z">${eur(d.steuern['47'])}</td></tr>` : ''}
      ${d.steuern['85'] ? `<tr><td>Steuer § 13b Abs. 2 (Kz 85)</td><td class="z">${eur(d.steuern['85'])}</td></tr>` : ''}
      <tr class="summe"><td>Umsatzsteuer gesamt</td><td class="z">${eur(d.kontrolle.umsatzsteuer)}</td></tr>
      <tr><td>abzüglich Vorsteuer (Kz 66 und 67)</td><td class="z">${eur(d.kontrolle.vorsteuer)}</td></tr>
      <tr class="ergebnis"><td>= Kz 83</td><td class="z">${betrag(d.steuern['83'])}</td></tr>
    </tbody></table></div>

    <h2>Probe gegen die Buchführung</h2>
    <div class="karte eng"><table class="t">
      <thead><tr><th>Kennzahl</th><th class="z">angemeldet</th><th class="z">gebucht</th>
        <th class="z">Differenz</th></tr></thead>
      <tbody>${['47', '85', '66', '67'].filter((k) => d.gebucht[k] || d.steuern[k]).map((k) => `
        <tr><td><span class="mono">${k}</span> ${esc(d.bezeichnungen[k])}</td>
          <td class="z">${eur(d.steuern[k])}</td>
          <td class="z leise">${eur(d.gebucht[k])}</td>
          <td class="z klein ${Math.abs(d.gebucht[k] - d.steuern[k]) > 100 ? 'minus' : 'still'}">${
            d.gebucht[k] - d.steuern[k] ? eur(d.gebucht[k] - d.steuern[k]) : '–'}</td></tr>`).join('')}
      </tbody>
      <tfoot>${(() => {
        const ks = ['47', '85', '66', '67'].filter((k) => d.gebucht[k] || d.steuern[k]);
        const a = ks.reduce((s, k) => s + d.steuern[k], 0);
        const g = ks.reduce((s, k) => s + d.gebucht[k], 0);
        return `<tr class="summe"><td>Summe</td><td class="z">${eur(a)}</td>
          <td class="z leise">${eur(g)}</td>
          <td class="z klein ${Math.abs(g - a) > 100 ? 'minus' : 'still'}">${
            g - a ? eur(g - a) : '–'}</td></tr>`;
      })()}</tfoot></table>
      <p class="klein leise" style="margin:10px 0 0;max-width:74ch">Elster rechnet die Steuer auf
        Kz 47 und 85 aus der auf volle Euro <em>abgeschnittenen</em> Bemessungsgrundlage, gebucht
        wird dagegen je Rechnung auf den Cent. Differenzen von wenigen Cent sind deshalb
        richtig${abweichungen.length ? '' : ' — hier tritt keine auf'}. Größere Beträge wären ein Buchungsfehler.</p>
    </div>

    ${d.zm.summe ? `<h2>Zusammenfassende Meldung</h2>
    <div class="karte">
      <div class="kennzahlen" style="margin:0 0 12px">
        ${kz('Meldebetrag', eur(d.zm.summe), 'muss Kz 21 entsprechen')}
        ${kz('Frist', datum(d.zm.frist), 'der 25. nach Quartalsende')}
        ${kz('Stand', d.zm.stand?.status || 'offen',
             d.zm.stand?.transferticket ? 'Transferticket vorhanden' : 'noch nicht gemeldet')}
      </div>
      <table class="t"><thead><tr><th>USt-IdNr.</th><th class="z">Betrag</th></tr></thead>
        <tbody>${d.zm.kunden.map((c) => `<tr>
          <td class="mono">${esc(c.ust_idnr)}</td><td class="z">${eur(c.betrag)}</td></tr>`).join('')}
        </tbody>
        <tfoot><tr class="summe"><td>Summe · Kz 21</td>
          <td class="z">${eur(d.zm.summe)}</td></tr></tfoot></table>
      <p class="klein leise" style="margin:10px 0 0;max-width:74ch">${esc(d.zm.hinweis)}
        Das Bundeszentralamt gleicht die Meldung gegen Kz 21 der Voranmeldung ab.</p>
      ${d.zm.stand?.transferticket ? `<p class="klein still" style="margin:6px 0 0">Übermittelt am
        ${datum(d.zm.stand.abgegeben_am)}, Transferticket
        <span class="mono">${esc(d.zm.stand.transferticket)}</span></p>` : ''}
    </div>` : ''}`;

  // Aufklappen: die tragenden Buchungen werden erst beim Klick geholt
  inhalt.querySelectorAll('tr[data-kz]').forEach((tr) => {
    tr.onclick = async () => {
      const k = tr.dataset.kz;
      const unten = inhalt.querySelector(`tr[data-unter="${k}"]`);
      const auf = tr.querySelector('.pfeil').classList.toggle('offen');
      unten.hidden = !auf;
      if (!auf || unten.dataset.geladen) return;
      unten.dataset.geladen = '1';
      const h = await holen(`/api/ust/${jahr}/${code}/kz/${k}`);
      unten.querySelector('.einbau').innerHTML = `
        <table class="t"><thead><tr><th class="z">Nr</th><th>Datum</th><th>Buchungstext</th>
          <th>Konto</th><th class="z">Betrag</th><th>Beleg</th></tr></thead>
          <tbody>${h.zeilen.map((r) => `
            <tr class="klick" data-buchung="${r.nummer}">
              <td class="z still mono">${r.nummer}</td>
              <td class="nw">${datum(r.datum)}</td>
              <td>${esc(r.buchungstext)}${r.zeilentext
                ? `<div class="klein still">${esc(r.zeilentext)}</div>` : ''}</td>
              <td class="mono klein leise">${esc(r.konto)}</td>
              <td class="z">${eur(r.betrag)}</td>
              <td class="klein">${r.belege
                ? `<span class="marke-chip gut">${r.belege}</span>`
                : `<span class="marke-chip">${esc(r.belegfeld || '–')}</span>`}</td></tr>`).join('')}
            <tr class="summe"><td colspan="4">Summe</td><td class="z">${eur(h.summe)}</td><td></td></tr>
          </tbody></table>`;
    };
  });
};

/* ------------------------------------------------- ELSTER-Übermittlungen */

/* Was über ERiC an ELSTER ging, Lauf für Lauf, und daneben die Abgaben von
 * Hand. Angezeigt wird, was auf der Platte liegt: der Datensatz Byte für Byte,
 * die Antworten von ERiC und vom Annahmeserver. Die gelesene Ansicht baut der
 * Browser mit seinem eigenen XML-Parser aus genau diesem Text - sie kann vom
 * Rohtext nicht abweichen, weil es keinen zweiten Weg zu ihr gibt. */

const ERIC_ART = { ustva: ['UStVA', 'Umsatzsteuer-Voranmeldung'], zm: ['ZM', 'Zusammenfassende Meldung'],
  kst: ['KSt', 'Körperschaftsteuererklärung'], gewst: ['GewSt', 'Gewerbesteuererklärung'],
  ebilanz: ['E-Bilanz', 'E-Bilanz'], eroeffnung: ['Eröffnungsbilanz', 'E-Bilanz (Eröffnungsbilanz)'] };
const ERIC_MODUS = {
  pruefen: ['', 'lokal geprüft'], test: ['test', 'Test'], senden: ['gut', 'Abgabe'],
  abgabe: ['', 'Abgabe'],
};
const ERIC_STATUS = {
  angenommen: 'gut', plausibel: 'gut', abgegeben: 'gut',
  'nicht plausibel': 'schlecht', abgelehnt: 'schlecht', abgebrochen: 'schlecht',
};

function zeitpunkt(iso) {
  if (!iso) return '';
  const s = String(iso);
  return s.length > 10 ? `${datum(s)} ${s.slice(11, 16)}` : datum(s);
}

/** Betrag im Format des Datensatzes ("169.29", "1500") deutsch schreiben -
 * nur umgesetzt, nicht gerechnet. */
function xmlZahl(s) {
  if (s === null || s === undefined) return '<span class="still">–</span>';
  const m = String(s).match(/^(-?)(\d+)(?:\.(\d+))?$/);
  if (!m) return esc(s);
  const ganz = m[2].replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `${m[1]}${ganz}${m[3] ? ',' + m[3] : ''}`;
}

function abgleichChip(n, ohneWerte) {
  if (ohneWerte) return '<span class="marke-chip">Werte nicht erfasst</span>';
  if (n === null || n === undefined) return '<span class="still">–</span>';
  return n === 0 ? '<span class="marke-chip gut">✓ stimmt</span>'
    : `<span class="marke-chip warn">⚠ ${n} weicht ab</span>`;
}

function laufLink(id) {
  return `#/lauf/${encodeURIComponent(id)}?${kontextAbfrage()}`;
}

/** Eine ERiC-Aktion aus einem Knopf heraus: Knopf sperren, Ergebnis-Lauf öffnen,
 * Fehler neben dem Knopf zeigen. */
async function ericAktion(knopf, aktion, daten, meldung) {
  const alle = inhalt.querySelectorAll('button[data-eric]');
  alle.forEach((b) => { b.disabled = true; });
  const text = knopf.textContent;
  knopf.textContent = { pruefen: 'prüft …', test: 'sendet Test …', senden: 'übermittelt …' }[aktion];
  if (meldung) meldung.innerHTML = '';
  const r = await posten(`/api/eric/${aktion}`, daten);
  if (r.ok && r.lauf) { location.hash = laufLink(r.lauf); return; }
  alle.forEach((b) => { b.disabled = false; });
  knopf.textContent = text;
  const ziel = meldung || knopf.parentElement;
  ziel.innerHTML = `<div class="aktionsfehler">${esc(r.fehler || 'Unbekannter Fehler')}${
    r.lauf ? ` · <a href="${laufLink(r.lauf)}">Lauf ansehen</a>` : ''}${
    r.neu_laden ? ' <button type="button" class="knopf" onclick="location.reload()">Seite neu laden</button>' : ''}</div>`;
}

/** Was vor dem Vorlegen geprüft wurde — aufklappbar, damit die Karte kurz bleibt. */
function pruefpunkteListe(punkte) {
  if (!punkte || !punkte.length) return '';
  return `<details class="pruefpunkte"><summary class="klein">✓ ${punkte.length} Prüfpunkt${punkte.length > 1 ? 'e' : ''}
    vor dem Vorlegen</summary><ul>${punkte.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></details>`;
}

/** Eine vorgelegte Übermittlung: warum, was sich ändert, wie weit sie ist. */
function vorschlagKarte(v, abweichend) {
  const [, lang] = ERIC_ART[v.art] || [v.art, v.art];
  const p = v.vorschlag;
  const schritt = (fertig, text, wann) => `<li class="${fertig ? 'fertig' : ''}">
    <span class="haken">${fertig ? '✓' : '○'}</span> ${text}${wann ? ` <span class="still">${wann}</span>` : ''}</li>`;
  const offen = v.stand === 'offen';
  return `<div class="karte vorschlag" data-vorschlag="${esc(v.id)}">
    <div class="vkopf">
      <div><strong>${esc(lang)} ${esc(v.bezeichnung)}</strong>
        <div class="klein still">vorgelegt von ${esc(p.von)} am ${zeitpunkt(p.zeit)}</div></div>
      <div class="z">${v.betrag_cent !== null && v.betrag_cent !== undefined
        ? `<div class="zahl">${eur(v.betrag_cent)}</div>
           <div class="klein still">${esc({ ebilanz: 'Bilanzsumme', eroeffnung: 'Bilanzsumme', kst: 'zu versteuerndes Einkommen',
                                             gewst: 'Gewinn aus Gewerbebetrieb' }[v.art] || 'Betrag')}</div>` : ''}</div>
    </div>
    <p style="margin:10px 0;max-width:82ch">${esc(p.grund)}</p>
    ${p.aenderungen && p.aenderungen.length ? `<table class="t" style="margin-bottom:10px">
      <thead><tr><th>Was</th><th class="z">beim Finanzamt</th><th class="z">neu</th></tr></thead>
      <tbody>${p.aenderungen.map((a) => `<tr><td>${esc(a.was)}</td>
        <td class="z mono still">${esc(a.bisher)}</td><td class="z mono"><strong>${esc(a.neu)}</strong></td></tr>`).join('')}
      </tbody></table>` : ''}
    ${pruefpunkteListe(p.pruefpunkte)}
    <ol class="schritte">
      ${schritt(true, `<a href="${laufLink(v.id)}">lokal geprüft</a>, fehlerfrei`, zeitpunkt(v.geprueft))}
      ${schritt(!!v.test, v.test ? `<a href="${laufLink(v.test.id)}">Test an ELSTER</a> angenommen${
        v.test.testzertifikat ? ' (SDK-Zertifikat)' : ''}` : 'Test an ELSTER', v.test ? zeitpunkt(v.test.zeit) : '')}
      ${schritt(!!v.abgabe, v.abgabe ? `<a href="${laufLink(v.abgabe.id)}">abgegeben</a>, Ticket
        <span class="mono">${esc(v.abgabe.transferticket || '')}</span>` : 'Abgabe — von Ihnen freigegeben',
        v.abgabe ? zeitpunkt(v.abgabe.zeit) : '')}
    </ol>
    ${v.stand === 'überholt' ? `<p class="minus klein">Überholt: eine neuere Prüfung ergab einen anderen
      Datensatz — die Buchführung hat sich bewegt. <a href="${laufLink(v.neuere_pruefung)}">Neueste Prüfung</a>
      ansehen; vorgelegt ist sie nicht.</p>` : ''}
    ${abweichend ? `<p class="minus klein">⚠ ${abweichend} Wert(e) weichen inzwischen von der Buchführung ab.</p>` : ''}
    ${v.stand === 'verworfen' ? `<p class="klein still">Verworfen am ${zeitpunkt(p.verworfen.zeit)}${
      p.verworfen.grund ? `: ${esc(p.verworfen.grund)}` : ''}.</p>` : ''}
    ${offen ? `<div class="werkzeug" style="gap:8px;margin:10px 0 0">
      <a class="knopf an" href="${laufLink(v.freigabe_lauf)}">${v.test ? 'Ansehen und abgeben →' : 'Ansehen, testen, abgeben →'}</a>
      <details class="verwerfen"><summary class="knopf">verwerfen</summary>
        <span><input type="text" placeholder="warum (optional)" data-grund>
        <button type="button" class="knopf" data-verwerfen="${esc(v.id)}">verwerfen</button></span></details>
    </div>` : ''}
  </div>`;
}

SEITEN.eric = async () => {
  const [d, o] = await Promise.all([holen('/api/eric', true), holen('/api/eric/offen', true)]);
  const z = d.zeilen;
  const vorschlaege = o.vorschlaege || [];
  const freigabe = vorschlaege.filter((v) => v.stand === 'offen' || v.stand === 'überholt');
  const erledigteVorschlaege = vorschlaege.filter((v) => !freigabe.includes(v));
  const abweichungVon = (id) => (z.find((r) => r.id === id) || {}).abweichend || 0;
  const abgaben = z.filter((r) => (r.modus === 'senden' && r.status === 'angenommen')
                               || (r.weg === 'hand' && r.status === 'abgegeben'));
  const tests = z.filter((r) => r.modus === 'test');
  // Eine Abweichung zählt nur, wo sie etwas bedeutet: bei dem, was ans Finanzamt
  // ging, und bei der neuesten Prüfung je Zeitraum. Ältere Prüfungen sind
  // Entwürfe — dass sie von der heutigen Buchführung abweichen, ist ihr Wesen.
  const neueste = new Set();
  const gesehen = new Set();
  z.forEach((r) => {                                   // neueste zuerst
    const k = `${r.jahr}|${r.zeitraum}|${r.art}`;
    if (r.weg === 'eric' && r.modus === 'pruefen' && !gesehen.has(k)) { gesehen.add(k); neueste.add(r.id); }
  });
  const zaehlt = (r) => (r.modus === 'senden' && r.status === 'angenommen')
    || (r.weg === 'hand' && r.status === 'abgegeben') || neueste.has(r.id);
  const abweichend = z.filter((r) => r.abweichend > 0 && zaehlt(r));
  const alteEntwuerfe = z.filter((r) => r.abweichend > 0 && !zaehlt(r)).length;
  // Der Filter bleibt in der Seite: ein eigenes Adressfragment je Filter
  // würde der Router als dieselbe Seite erkennen und nicht neu zeichnen.
  const gruppe = { senden: 'abgaben', abgabe: 'abgaben', test: 'tests', pruefen: 'pruefungen' };
  // Standard sind die echten Abgaben; Tests und lokale Prüfungen auf Knopfdruck
  const knopf = (k, t) => `<button type="button" class="knopf ${k === 'abgaben' ? 'an' : ''}"
      data-nur="${k}">${t}</button>`;
  const ziel = (r) => (r.weg === 'eric' ? `#/lauf/${encodeURIComponent(r.id)}?${kontextAbfrage()}`
    : `#/ustva/${r.jahr}/${r.zeitraum}?mandant=${encodeURIComponent(KONTEXT.mandant)}&jahr=${r.jahr}`);

  inhalt.innerHTML = `
    ${kopf('ELSTER-Übermittlungen',
      `Alles, was an ELSTER ging — über ERiC oder von Hand in Mein ELSTER. Ein Lauf öffnet
       den Datensatz, wie er an ERiC ging, die Prüfung davor, die Antwort von ELSTER und den
       Abgleich mit der Buchführung von heute. Abgeben: oben „prüfen“, dann auf der Seite der
       Prüfung testen und freigeben — oder im Terminal mit <span class="mono">bh eric</span>.`)}
    <div class="kennzahlen">
      ${kz('Abgegeben', String(abgaben.length),
           `davon ${abgaben.filter((r) => r.weg === 'eric').length} über ERiC`)}
      ${kz('Testsendungen', String(tests.length),
           tests.length ? `${tests.filter((r) => r.status === 'angenommen').length} angenommen` : '')}
      ${kz('Weicht von der Buchführung ab', String(abweichend.length),
           (abweichend.map((r) => `${r.label} ${ERIC_ART[r.art][0]}`).join(', ') || 'nichts')
           + (alteEntwuerfe ? ` · Abgaben und neueste Prüfungen; ${alteEntwuerfe} ältere Prüfung${
             alteEntwuerfe > 1 ? 'en' : ''} nicht gezählt` : ''))}
    </div>
    ${freigabe.length || erledigteVorschlaege.length ? `
    <h2 id="zur-freigabe">Zur Freigabe <span class="still klein">${freigabe.length
      ? `${freigabe.length} vorgelegt` : 'nichts offen'}</span></h2>
    <p class="klein still" style="max-width:78ch;margin-top:-4px">Vorbereitet und lokal geprüft, aber nicht
      gesendet. Jede Karte sagt, warum es den Datensatz gibt und was er beim Finanzamt ändert.
      Testen und abgeben tun Sie auf der Seite der Prüfung — dort steht der Datensatz, die Vorschau
      und der Abgleich mit der Buchführung.</p>
    ${freigabe.map((v) => vorschlagKarte(v, abweichungVon(v.id))).join('')}
    ${erledigteVorschlaege.length ? `<details class="karte"><summary class="klein">Erledigt oder verworfen
      (${erledigteVorschlaege.length})</summary>${erledigteVorschlaege.map((v) => vorschlagKarte(v, 0)).join('')}</details>` : ''}
    ` : ''}

    <h2>Abzugeben</h2>
    <div class="karte eng">${o.zeilen.length ? `<table class="t">
      <thead><tr><th>Zeitraum</th><th>Art</th><th class="z">Betrag</th><th>Frist</th>
        <th title="ERiC prüft lokal, nichts verlässt den Rechner">Lokal geprüft</th>
        <th title="An ELSTER gesendet mit Testmerker - angenommen und wieder gelöscht">Test an ELSTER</th><th></th></tr></thead>
      <tbody>${o.zeilen.map((r) => `<tr>
        <td class="nw"><strong>${esc(r.label)}</strong></td>
        <td>${ERIC_ART[r.art][1]}</td>
        <td class="z">${eur(r.betrag_cent)}</td>
        <td class="nw klein ${!r.laeuft_noch && r.frist && r.frist < new Date().toISOString().slice(0, 10) ? 'minus' : ''}">${r.berichtigung
          ? '<span class="marke-chip warn" title="Schon abgegeben; eine zweite Übermittlung vor dem Bescheid ersetzt die erste">Berichtigung</span>'
          : datum(r.frist)}</td>
        <td class="klein">${r.pruefung ? `<a href="${laufLink(r.pruefung.id)}">${zeitpunkt(r.pruefung.zeit)}</a>
          <span class="marke-chip ${r.pruefung.ok ? 'gut' : 'schlecht'}">${r.pruefung.ok ? 'plausibel' : 'Fehler'}</span>`
          : '<span class="still">–</span>'}</td>
        <td class="klein">${r.test ? `<a href="${laufLink(r.test.id)}">${zeitpunkt(r.test.zeit)}</a>
          <span class="marke-chip ${r.test.angenommen ? 'gut' : 'schlecht'}">${r.test.angenommen ? 'angenommen' : 'abgelehnt'}</span>${
            r.test.testzertifikat ? ' <span class="marke-chip" title="Mit dem Testzertifikat des ERiC-SDK, nicht mit Ihrem - zählt nicht als Test vor der Abgabe">SDK-Zertifikat</span>' : ''}`
          : '<span class="still">–</span>'}</td>
        <td class="z">${r.laeuft_noch ? '<span class="klein still">Zeitraum läuft noch</span>'
          : `<button type="button" class="knopf" data-eric="pruefen" data-art="${r.art}"
              data-jahr="${r.jahr}" data-zeitraum="${r.zeitraum}">${r.pruefung ? 'neu prüfen' : 'prüfen'}</button>`}</td>
      </tr>`).join('')}</tbody></table>` : '<p class="klein" style="margin:12px 14px">Nichts offen.</p>'}</div>
    <p class="klein still" style="max-width:78ch">„Lokal geprüft“: ERiC prüft die Meldung auf diesem
      Rechner mit denselben Regeln wie der ELSTER-Server — nichts wird gesendet. „Test an ELSTER“:
      die Meldung geht mit Ihrem Zertifikat und Testmerker an ELSTER, wird angenommen und gelöscht —
      das prüft Zertifikat, PIN und Übertragung. Getestet und abgegeben wird auf der Seite der Prüfung.</p>

    <h2>Verlauf</h2>
    <div class="werkzeug">${knopf('alle', 'Alle')}${knopf('abgaben', 'Abgaben')}
      ${knopf('tests', 'Tests')}${knopf('pruefungen', 'Lokale Prüfungen')}</div>
    <div class="karte eng"><table class="t">
      <thead><tr><th>Zeitraum</th><th>Art</th><th>Weg</th><th>Modus</th><th>Zeitpunkt</th>
        <th>Transferticket</th><th>Ergebnis</th><th>Abgleich</th></tr></thead>
      <tbody>${z.map((r) => `
        <tr class="klick" data-gruppe="${gruppe[r.modus] || ''}" onclick="location.hash='${ziel(r)}'">
          <td class="nw"><strong>${esc(r.label)}</strong>${r.berichtigt ? ' <span class="marke-chip">berichtigt</span>' : ''}</td>
          <td><a href="${ziel(r)}">${ERIC_ART[r.art]?.[0] || esc(r.art)}</a></td>
          <td class="klein">${r.weg === 'eric' ? 'ERiC' : 'Mein ELSTER'}</td>
          <td><span class="marke-chip ${ERIC_MODUS[r.modus]?.[0] || ''}">${ERIC_MODUS[r.modus]?.[1] || esc(r.modus)}</span></td>
          <td class="nw klein">${zeitpunkt(r.zeit)}</td>
          <td class="mono klein leise">${r.transferticket
            ? `${esc(r.transferticket.slice(0, 6))}…${esc(r.transferticket.slice(-4))}` : '–'}</td>
          <td><span class="marke-chip ${ERIC_STATUS[r.status] || ''}">${esc(r.status)}</span>
            ${r.fehler ? `<span class="marke-chip schlecht">${r.fehler} Fehler</span>` : ''}
            ${r.hinweise ? `<span class="marke-chip warn">${r.hinweise} Hinweis${r.hinweise > 1 ? 'e' : ''}</span>` : ''}</td>
          <td>${r.abweichend > 0 && !zaehlt(r)
            ? `<span class="marke-chip" title="Ältere Prüfung — seitdem neu geprüft">überholt</span>`
            : abgleichChip(r.abweichend, r.weg === 'hand' && !r.werte_erfasst)}
            ${r.weg === 'eric' && !r.unveraendert ? '<span class="marke-chip schlecht">Datei verändert</span>' : ''}</td>
        </tr>`).join('') || '<tr><td colspan="8" class="still">Noch nichts übermittelt.</td></tr>'}</tbody>
    </table></div>
    <p class="klein still" style="max-width:78ch">Bei Abgaben von Hand liegt kein Datensatz vor.
      Abgeglichen wird gegen die übermittelten Werte, sofern sie in der Datenbank festgehalten
      sind; sonst stehen nur Transferticket und Notiz in der Voranmeldung. Über ERiC
      gesendete Abgaben halten ihre Werte selbst fest.</p>`;

  inhalt.querySelectorAll('button[data-eric="pruefen"]').forEach((b) => {
    b.onclick = () => ericAktion(b, 'pruefen',
      { art: b.dataset.art, jahr: b.dataset.jahr, zeitraum: b.dataset.zeitraum });
  });
  const filtern = (nur) => {
    inhalt.querySelectorAll('[data-nur]').forEach((x) => x.classList.toggle('an', x.dataset.nur === nur));
    inhalt.querySelectorAll('tr[data-gruppe]').forEach((tr) => {
      tr.hidden = nur !== 'alle' && tr.dataset.gruppe !== nur;
    });
  };
  inhalt.querySelectorAll('[data-nur]').forEach((b) => {
    b.onclick = () => filtern(b.dataset.nur);
  });
  filtern('abgaben');
};

/** XML zweimal: gelesen (aufklappbarer Baum) und roh. `markiert(name)` färbt
 * die Elemente, deren Werte aus der Buchführung stammen - in beiden Ansichten. */
/** Ein Wert im Klartext: Beträge in Euro, ISO-Daten deutsch, Auswahlwerte in Worten. */
function klartextWert(z) {
  if (z.leer) return '<span class="still">leer übermittelt</span>';
  const roh = z.wert || '';
  let haupt = esc(roh);
  if (z.betrag && /^-?\d+([.,]\d+)?$/.test(roh)) {
    const zahl = Number(roh.replace(',', '.'));
    const nachkomma = /[.,]\d+$/.test(roh) ? 2 : 0;
    haupt = `${zahl.toLocaleString('de-DE', { minimumFractionDigits: nachkomma, maximumFractionDigits: nachkomma })} €`;
  } else if (/^\d{4}-\d{2}-\d{2}$/.test(roh)) {
    haupt = roh.split('-').reverse().join('.');
  } else if (/^\d{8}$/.test(roh) && /datum|stichtag/i.test(z.text || '')) {
    haupt = `${roh.slice(6)}.${roh.slice(4, 6)}.${roh.slice(0, 4)}`;
  } else if (roh === 'true' || roh === 'false') {
    haupt = roh === 'true' ? 'ja' : 'nein';
  }
  if (!roh && !z.wert_text) return '<span class="still">–</span>';
  return z.wert_text ? `${esc(z.wert_text)} <span class="mono still klein">${haupt}</span>` : haupt;
}

/** Der Datensatz, wie ihn ein Mensch liest: Vordruck, Zeile, Wortlaut, Wert. */
function klartextAnsicht(k, markiert) {
  if (!k) return '';
  if (k.fehler) return `<p class="minus klein" style="margin:12px 14px">Klartext nicht lesbar: ${esc(k.fehler)}</p>`;
  const zeilen = (liste) => {
    let vorher = null;
    return liste.map((z) => {
      const kopf = z.abschnitt && z.abschnitt !== vorher
        ? `<tr class="kt-abschnitt"><td colspan="4">${esc(z.abschnitt)}</td></tr>` : '';
      vorher = z.abschnitt;
      const mark = markiert(z.pfad && z.pfad.includes(':') ? z.pfad : z.feld, !z.leer && !!z.wert);
      return `${kopf}<tr class="${mark ? 'mark' : ''}">
        <td class="z mono klein still">${esc(z.zeile || '')}</td>
        <td>${z.text ? esc(z.text) : `<span class="still">${esc(z.feld)}</span>`}${z.ungefaehr
          ? ' <span class="still" title="Bezeichnung aus dem Elementnamen übersetzt, nicht amtlich">≈</span>' : ''}</td>
        <td class="z kt-wert${z.betrag ? ' nw' : ''}${(z.wert || '').length > 40 ? ' kt-lang' : ''}">${klartextWert(z)}</td>
        <td class="mono klein still kt-feld" title="${esc(z.pfad || '')}">${esc(z.feld)}</td></tr>`;
    }).join('');
  };
  const gruppe = (g) => {
    const voll = g.zeilen.filter((z) => !z.leer);
    const leer = g.zeilen.filter((z) => z.leer);
    const inhalt = `<table class="t kt">
      <thead><tr><th class="z">Zeile</th><th>Angabe</th><th class="z">Wert</th><th>Feld</th></tr></thead>
      <tbody>${zeilen(voll)}</tbody></table>
      ${leer.length ? `<details class="kt-leer"><summary class="klein still">${leer.length} Position${leer.length > 1 ? 'en' : ''}
        ohne Wert übermittelt (Mussfelder der Taxonomie)</summary><table class="t kt"><tbody>${zeilen(leer)}</tbody></table></details>` : ''}`;
    return g.technisch
      ? `<details class="kt-gruppe"><summary><strong>${esc(g.titel)}</strong></summary>${inhalt}</details>`
      : `<div class="kt-gruppe"><h4>${esc(g.titel)}</h4>${inhalt}</div>`;
  };
  return `${k.gruppen.map(gruppe).join('')}
    <p class="klein still" style="margin:10px 14px">${k.quelle
      ? `Wortlaut, Zeilennummern und Auswahlwerte aus der Jahresdokumentation des ERiC-SDK
         (<span class="mono">${esc(k.quelle)}</span>).`
      : k.sdk === false ? 'Die Dokumentation des ERiC-SDK wurde nicht gefunden — ohne sie bleiben nur die Feldnamen. ERIC_DOKU setzen (docs/eric.md).'
        : ''} ≈ = Bezeichnung aus dem Elementnamen übersetzt, nicht amtlich.</p>`;
}

function xmlAnsicht(text, markiert = () => false, klartext = null) {
  if (!text || !text.trim()) return '<div class="still klein" style="padding:12px 14px">leer</div>';
  const doc = new DOMParser().parseFromString(text, 'application/xml');
  const kaputt = doc.querySelector('parsererror');
  const knoten = (el) => {
    const attr = [...el.attributes].map((a) =>
      `<span class="attr">${esc(a.name)} = ${esc(a.value)}</span>`).join('');
    const kinder = [...el.children];
    if (!kinder.length) {
      const t = el.textContent;
      return `<div class="blatt${markiert(el.tagName, el.getAttribute('xsi:nil') !== 'true') ? ' mark' : ''}">
        <span class="n">${esc(el.tagName)}${attr}</span>${t.trim()
          ? `<span class="w">${esc(t)}</span>` : '<span class="w leer">leer</span>'}</div>`;
    }
    return `<details open><summary>${esc(el.tagName)}${attr}<span class="anz">${kinder.length}</span></summary>
      <div>${kinder.map(knoten).join('')}</div></details>`;
  };
  const muster = /<\s*([A-Za-z0-9_.:-]+)[\s>/]/;
  const roh = text.split('\n').map((z) => {
    const m = z.match(muster);
    return m && markiert(m[1], !/nil="true"/.test(z)) ? `<mark>${esc(z)}</mark>` : esc(z);
  }).join('\n');
  const mitKlartext = klartext && (klartext.gruppen?.length || klartext.fehler);
  return `<div class="xmlansicht" data-ansicht="${mitKlartext ? 'klartext' : kaputt ? 'roh' : 'gelesen'}">
    <div class="xmlleiste">
      <span class="seg" role="group" aria-label="Ansicht">
        <button type="button" data-zeige="gelesen" ${kaputt ? 'disabled' : ''}>Gelesen</button
        ><button type="button" data-zeige="roh">Roh</button
        >${mitKlartext ? '<button type="button" data-zeige="klartext">Klartext</button>' : ''}</span>
      <span class="nurgelesen"><button type="button" class="knopf" data-alle="auf">alle auf</button>
        <button type="button" class="knopf" data-alle="zu">alle zu</button></span>
      ${kaputt ? '<span class="marke-chip schlecht">kein gültiges XML — nur roh</span>' : ''}
    </div>
    <div class="baum">${kaputt ? '' : knoten(doc.documentElement)}</div>
    ${mitKlartext ? `<div class="klartext">${klartextAnsicht(klartext, markiert)}</div>` : ''}
    <pre class="xmlroh">${roh}</pre></div>`;
}

function xmlAnsichtenVerdrahten() {
  inhalt.querySelectorAll('.xmlansicht').forEach((x) => {
    x.querySelectorAll('[data-zeige]').forEach((b) => {
      b.onclick = () => { x.dataset.ansicht = b.dataset.zeige; };
    });
    x.querySelectorAll('[data-alle]').forEach((b) => {
      b.onclick = () => x.querySelectorAll('.baum details').forEach((d) => {
        d.open = b.dataset.alle === 'auf';
      });
    });
  });
}

function meldungsTabelle(liste) {
  if (!liste.length) return '<p class="klein" style="margin:0">Keine Fehler, keine Hinweise.</p>';
  return `<table class="t">
    <thead><tr><th>Art</th><th>Feld</th><th>Meldung</th><th>Regel</th><th>Wirkung</th></tr></thead>
    <tbody>${liste.map((m) => `<tr>
      <td><span class="marke-chip ${m.art === 'fehler' ? 'schlecht' : 'warn'}">${m.art === 'fehler' ? 'Fehler' : 'Hinweis'}</span></td>
      <td>${m.feld_kurz ? `<span class="mono">${esc(m.feld_kurz)}</span>` : '<span class="still">–</span>'}
        ${m.feld ? `<div class="klein still mono" style="white-space:normal;word-break:break-all">${esc(m.feld)}</div>` : ''}</td>
      <td>${esc(m.text)}</td>
      <td class="mono klein">${esc(m.id || '–')}${m.regel
        ? `<div class="still" style="white-space:normal;word-break:break-all">${esc(m.regel.split('/').pop())}</div>` : ''}</td>
      <td class="klein">${m.art === 'fehler' ? 'sperrt das Senden' : 'sperrt nicht — lesen'}</td>
    </tr>`).join('')}</tbody></table>`;
}

SEITEN.lauf = async (z) => {
  const id = z.param[0];
  const d = await holen(`/api/eric/lauf?lauf=${encodeURIComponent(id)}`, true);
  const l = d.lauf;
  const [kurz, lang] = ERIC_ART[l.art] || [l.art, l.art];
  const datei = (n) => mitKontext(`/eric/datei?lauf=${encodeURIComponent(id)}&datei=${n}`);
  const vp = d.vorpruefung.meldungen;
  const an = d.antwort.meldungen;
  const fehlerZahl = (vp.length ? vp : an).filter((m) => m.art === 'fehler').length;
  const hinweisZahl = (vp.length ? vp : an).filter((m) => m.art === 'hinweis').length;
  // Kz 09 trägt die Hersteller-ID, Kz 10/22/26/29 sind Ankreuzfelder - keine Werte aus Buchungen
  const markiert = (n, mitWert) => (/^Kz\d\d$/.test(n) && !['Kz09', 'Kz10', 'Kz22', 'Kz26', 'Kz29'].includes(n))
    || n === 'Testmerker' || n === 'knre' || n === 'betrag'
    // E-Bilanz: nur Positionen mit Wert, nicht die leer berichteten Mussfelder
    || (mitWert && /^de-gaap-ci:(bs|is|BVV)\./.test(n))
    // KSt/GewSt: Felder mit Wert (E8002001 …)
    || (mitWert && /^E\d{7}$/.test(n))
    || n === 'de-gaap-ci:detailedInformation.accountbalances.amount';
  const ab = d.abgleich;
  const s = d.server;

  const zertifikat = l.testzertifikat ? 'dem Testzertifikat des ERiC-SDK'
    : l.zertifikat ? `<span class="mono">${esc(l.zertifikat)}</span>` : null;
  const was = {
    pruefen: 'Nur geprüft: ERiC hat den Datensatz lokal plausibilisiert, nichts hat den Rechner verlassen.',
    test: 'Ein Testfall: ELSTER hat ihn geprüft, angenommen und gelöscht — kein Finanzamt sieht ihn.',
    senden: 'Die Abgabe an die Finanzverwaltung, ohne Testmerker.',
  }[l.modus];

  const vorSenden = l.modus === 'pruefen' ? `
    <h2>Vor dem Senden: Plausibilitätsprüfung</h2>
    <div class="kennzahlen">
      ${kz('Fehler', `<span class="${fehlerZahl ? 'minus' : 'plus'}">${fehlerZahl}</span>`, 'sperren das Senden')}
      ${kz('Hinweise', `<span style="color:var(--warn)">${hinweisZahl}</span>`, 'sperren nicht — bitte lesen')}
      ${kz('Geprüft', zeitpunkt(l.zeit), `ERiC ${esc(l.eric || '?')}`)}
    </div>
    <div class="karte ${an.length ? 'eng' : ''}">${meldungsTabelle(an)}</div>
    ${abgabeAblauf(d, id, fehlerZahl, hinweisZahl, lang)}
` : `
    <h2>Vorher: Plausibilitätsprüfung</h2>
    <div class="karte ${vp.length ? 'eng' : ''}">${l.vorpruefung
      ? meldungsTabelle(vp)
      : '<p class="klein still" style="margin:0">Für diesen Lauf gibt es keine gesonderte Vorprüfung — er stammt aus der Zeit, bevor sie vor jedem Senden lief.</p>'}</div>`;

  inhalt.innerHTML = `
    ${kopf(`${lang} ${l.bezeichnung}`, '',
      `<a href="#/eric">ELSTER-Übermittlungen</a> › ${esc(l.label)} › ${esc(kurz)} ›
       ${ERIC_MODUS[l.modus]?.[1] || esc(l.modus)} vom ${zeitpunkt(l.zeit)}`)}
    <div class="werkzeug" style="gap:6px">
      <span class="marke-chip ${ERIC_MODUS[l.modus]?.[0] || ''}">${ERIC_MODUS[l.modus]?.[1]}${
        d.testmerker ? ` · Testmerker ${esc(d.testmerker)}` : ' · ohne Testmerker'}</span>
      <span class="marke-chip ${ERIC_STATUS[d.status] || ''}">${esc(d.status)}</span>
      ${l.berichtigt ? '<span class="marke-chip">berichtigte Anmeldung</span>' : ''}
      ${ab && ab.abweichend !== null ? (ab.abweichend
        ? `<span class="marke-chip warn">⚠ ${ab.abweichend} weicht von der Buchführung ab</span>`
        : '<span class="marke-chip gut">✓ stimmt mit der Buchführung</span>') : ''}
      <span class="marke-chip ${d.unveraendert ? 'gut' : 'schlecht'}">${d.unveraendert
        ? '✓ Datei unverändert' : '✕ Datei verändert'}</span>
    </div>
    <p class="unter" style="max-width:82ch">${was}
      ${zertifikat ? `Gesendet mit ${zertifikat}, ` : ''}ERiC ${esc(l.eric || '?')}, über
      <span class="mono">bh eric ${esc(l.modus)}</span>.
      ${l.abgebrochen ? `<strong class="minus">Abgebrochen: ${esc(l.abgebrochen)}.</strong>` : ''}
      ${l.nachgetragen ? `<span class="still">${esc(l.nachgetragen)}</span>` : ''}</p>
    ${d.vorschlag ? `<div class="karte vorschlag-hinweis"><strong>${{
        offen: 'Zur Freigabe vorgelegt', 'überholt': 'Vorgelegt, aber überholt', ersetzt: 'Durch einen neueren Vorschlag ersetzt',
        abgegeben: 'Vorgelegt und abgegeben', verworfen: 'Vorgelegt und verworfen' }[d.vorschlag.stand]}</strong>
      <span class="klein still">von ${esc(d.vorschlag.vorschlag.von)} am ${zeitpunkt(d.vorschlag.vorschlag.zeit)}</span>
      <p style="margin:6px 0 0;max-width:82ch">${esc(d.vorschlag.vorschlag.grund)}</p>
      ${(d.vorschlag.vorschlag.aenderungen || []).length ? `<table class="t" style="margin-top:8px">
        <thead><tr><th>Was</th><th class="z">beim Finanzamt</th><th class="z">neu</th></tr></thead>
        <tbody>${d.vorschlag.vorschlag.aenderungen.map((a) => `<tr><td>${esc(a.was)}</td>
          <td class="z mono still">${esc(a.bisher)}</td><td class="z mono"><strong>${esc(a.neu)}</strong></td></tr>`).join('')}
        </tbody></table>` : ''}
      ${pruefpunkteListe(d.vorschlag.vorschlag.pruefpunkte)}
      ${d.vorschlag.stand === 'offen' && d.vorschlag.freigabe_lauf !== id
        ? `<p class="klein" style="margin:8px 0 0"><a href="${laufLink(d.vorschlag.freigabe_lauf)}">Zur neuesten Prüfung desselben Datensatzes →</a> dort wird abgegeben.</p>` : ''}
    </div>` : ''}
    ${l.modus === 'test' && d.neueste_pruefung && d.stand.status !== 'abgegeben'
      ? `<div class="karte"><a href="${laufLink(d.neueste_pruefung)}">Weiter zur Abgabe →</a>
          <span class="klein still">auf der Seite der neuesten Prüfung</span></div>` : ''}

    ${vorSenden}

    <h2>Abgleich: Buchführung heute ↔ Datensatz</h2>
    <div class="karte eng">${ab && ab.fehler ? `<p class="minus" style="margin:12px 14px">${esc(ab.fehler)}</p>` : `
      <table class="t">
        <thead><tr><th>${{ zm: 'USt-IdNr.', ebilanz: 'Position', eroeffnung: 'Position', kst: 'Feld', gewst: 'Feld' }[l.art] || 'Kz'}</th>${l.art === 'ustva' ? '<th>Bezeichnung</th>' : ''}
          <th class="z">Buchführung heute</th><th class="z">im Datensatz</th><th class="z">Abgleich</th></tr></thead>
        <tbody>${(ab ? ab.zeilen : []).map((r) => `
          <tr class="${r.gleich ? '' : 'abweichung'}">
            <td class="mono">${l.art === 'ustva'
              ? `<a href="#/ustva/${l.jahr}/${l.zeitraum}">${esc(r.kz)}</a>` : esc(r.kz)}</td>
            ${l.art === 'ustva' ? `<td>${esc(r.bezeichnung)}</td>` : ''}
            <td class="z">${xmlZahl(r.buch)}</td>
            <td class="z mono">${r.gesendet === null || r.gesendet === undefined
              ? '<span class="still">–</span>' : esc(r.gesendet)}</td>
            <td class="z ${r.gleich ? 'plus' : 'minus'}">${r.gleich ? '✓' : '≠'}</td></tr>`).join('')}
        </tbody></table>`}</div>
    <p class="klein still" style="max-width:78ch">„Im Datensatz“ steht so, wie es in der XML-Datei
      steht; „Buchführung heute“ ist neu aus den Buchungen gerechnet.${l.art === 'ustva'
        ? ' Ein Klick auf die Kennzahl führt zu den Buchungen dahinter.' : ''}</p>

    <h2>Datensatz, wie er an ERiC übergeben wurde</h2>
    <div class="karte eng">
      ${xmlAnsicht(d.datensatz, markiert, d.klartext)}
      <div class="klein still" style="padding:8px 14px;border-top:1px solid var(--linie)">
        Gelb: Werte aus der Buchführung und der Testmerker. Die gelesene Ansicht ist der Rohtext,
        vom Browser zerlegt — ohne Ergänzung, ohne Umrechnung. ERiC verschlüsselt den Datensatz
        anschließend und füllt Transferticket und Transportschlüssel im Kopf.
        <a href="${datei('datensatz.xml')}" target="_blank" rel="noopener">Datei öffnen</a></div>
    </div>

    ${l.modus !== 'pruefen' ? `
    <h2>Antwort von ELSTER</h2>
    <div class="karte">${s && s.lesbar ? `
      <dl class="feld">
        <dt>Ergebnis</dt><dd>${s.rueckgaben.map((r) => `<span class="mono">${esc(r.code)}</span> ${esc(r.text)}`).join('<br>')
          || '<span class="still">keine Rückgabe</span>'}</dd>
        <dt>Transferticket</dt><dd class="mono">${esc(s.transferticket || '–')}</dd>
        <dt>Eingang</dt><dd>${zeitpunkt(s.eingang) || '–'}</dd>
        <dt>Testmerker</dt><dd class="mono">${esc(s.testmerker || 'keiner — echte Abgabe')}</dd>
      </dl>` : `<p class="klein still" style="margin:0">${l.abgebrochen
        ? 'Nichts gesendet, deshalb keine Antwort.' : 'Keine lesbare Antwort des Annahmeservers.'}</p>`}</div>
    ${s && s.xml ? `<div class="karte eng">${xmlAnsicht(s.xml)}</div>` : ''}` : ''}

    <h2>Rückgabe von ERiC zum Lauf</h2>
    <div class="karte ${an.length ? 'eng' : ''}">${l.ergebnis
      ? `${an.length ? meldungsTabelle(an) : `<p class="klein" style="margin:0"><span class="mono">${esc(l.ergebnis.code)}</span>
          ${esc(l.ergebnis.text || (l.ergebnis.ok ? 'ohne Beanstandung' : ''))}</p>`}`
      : '<p class="klein still" style="margin:0">ERiC wurde nicht aufgerufen.</p>'}</div>
    ${d.antwort.xml ? `<div class="karte eng">${xmlAnsicht(d.antwort.xml)}</div>` : ''}
    ${d.vorpruefung.xml ? `<h3>Prüfantwort der Vorprüfung</h3><div class="karte eng">${xmlAnsicht(d.vorpruefung.xml)}</div>` : ''}

    <h2>Unversehrtheit</h2>
    <div class="karte"><dl class="feld" style="grid-template-columns:150px 1fr">
      <dt>SHA-256 beim Lauf</dt><dd class="mono" style="word-break:break-all">${esc(l.datensatz.sha256)}</dd>
      <dt>SHA-256 jetzt</dt><dd class="mono ${d.unveraendert ? 'plus' : 'minus'}" style="word-break:break-all">${esc(d.sha256_jetzt || 'Datei fehlt')}</dd>
      <dt>Größe</dt><dd>${l.datensatz.bytes.toLocaleString('de-DE')} Byte, UTF-8</dd>
      <dt>Ablage</dt><dd class="mono klein">mandanten/${esc(KONTEXT.mandant)}/${esc(id)}/</dd>
    </dl>
    <p class="klein still" style="margin:10px 0 0;max-width:78ch">Die Prüfsumme wird beim Lauf über
      genau den Text gebildet, der an ERiC ging, und beim Öffnen dieser Seite neu über die Datei.
      Stimmen beide überein, ist die Datei seitdem unverändert.</p></div>

    ${d.protokoll ? `
    <h2>${l.modus === 'pruefen' ? 'Vorschau' : 'Übertragungsprotokoll'} (von ERiC gedruckt)</h2>
    <div class="karte eng"><iframe class="protokoll" src="${datei('protokoll.pdf')}#view=FitH"
      title="Protokoll"></iframe>
      <div class="klein" style="padding:8px 14px;border-top:1px solid var(--linie)">
        <a href="${datei('protokoll.pdf')}" target="_blank" rel="noopener">in neuem Tab öffnen</a>
        <span class="still"> · ERiC setzt das PDF aus dem Datensatz selbst — eine zweite,
        unabhängige Lesart derselben Zahlen.</span></div></div>` : ''}`;

  xmlAnsichtenVerdrahten();
  abgabeVerdrahten(d, id);
};

/** Die drei Schritte zur Abgabe, auf der Seite der Prüfung. Jeder Schritt baut
 * die Meldung neu aus der Buchführung; abgegeben wird nur, wenn sie Byte für
 * Byte der hier angezeigten Fassung entspricht. */
function abgabeAblauf(d, id, fehlerZahl, hinweisZahl, lang) {
  const l = d.lauf;
  const daten = `data-art="${l.art}" data-jahr="${l.jahr}" data-zeitraum="${l.zeitraum}"`;
  const neu = `<button type="button" class="knopf" data-eric="pruefen" ${daten}>neu prüfen</button>`;
  if (d.stand.status === 'abgegeben') {
    return `<div class="karte"><p style="margin:0">${esc(lang)} ${esc(l.bezeichnung)} ist abgegeben
      (Transferticket <span class="mono">${esc(d.stand.transferticket || '')}</span>,
      ${datum(d.stand.abgegeben_am)}). Eine Berichtigung geht als berichtigte Meldung über
      <span class="mono">bh eric senden … --berichtigt</span>.</p></div>`;
  }
  if (d.neueste_pruefung !== id) {
    return `<div class="karte"><p style="margin:0">Es gibt eine neuere Prüfung dieses Zeitraums.
      Abgegeben wird nur die neueste: <a href="${laufLink(d.neueste_pruefung)}">zur neuesten Prüfung</a>.</p></div>`;
  }
  if (fehlerZahl) {
    return `<div class="karte"><p style="margin:0 0 10px">Erst die Fehler in der Buchführung beheben,
      dann neu prüfen. Mit Fehlern lässt ERiC nichts senden.</p>${neu}</div>`;
  }
  const t = d.letzter_test;
  // Ein Test zählt, wenn er mit dem eigenen Zertifikat angenommen wurde und - bis auf
  // den Testmerker - genau diese Meldung war, egal ob vor oder nach der Prüfung.
  const testOk = t && t.angenommen && !t.testzertifikat && t.gleicher_datensatz;
  return `<div class="karte abgabe">
    <ol class="schritte">
      <li class="erledigt"><strong>Geprüft</strong> — diese Seite: ${fehlerZahl} Fehler, ${hinweisZahl}
        Hinweis${hinweisZahl === 1 ? '' : 'e'}. Bitte Abgleich und Datensatz unten ansehen.
        <div style="margin-top:6px">${neu}</div></li>
      <li class="${testOk ? 'erledigt' : ''}"><strong>Testweise senden</strong> <span class="still">(empfohlen)</span> —
        mit Ihrem Zertifikat und Testmerker. ELSTER prüft, nimmt an und löscht; das Finanzamt sieht nichts.
        <div style="margin-top:6px">${testOk
          ? `<span class="marke-chip gut">angenommen</span> <a href="${laufLink(t.id)}">Test vom ${zeitpunkt(t.zeit)}</a>`
          : `<button type="button" class="knopf" data-eric="test" ${daten}>testweise senden</button>`}</div>
        <div class="aktionsmeldung" data-meldung="test"></div></li>
      <li><strong>Abgeben</strong> — ohne Testmerker an die Finanzverwaltung.
        <label class="bestaetigung"><input type="checkbox" data-gelesen>
          Ich habe Prüfergebnis, Abgleich und Datensatz auf dieser Seite gelesen und gebe
          ${esc(lang)} ${esc(l.bezeichnung)} so frei.</label>
        <label class="bestaetigung">${{ ebilanz: 'Bilanzsumme', eroeffnung: 'Bilanzsumme', kst: 'Zu versteuerndes Einkommen',
          gewst: 'Gewinn aus Gewerbebetrieb' }[l.art] || 'Betrag'} zur Bestätigung eintippen
          (<span class="mono">${esc(d.betrag_text)}</span>):
          <input type="text" inputmode="decimal" autocomplete="off" data-betrag
            placeholder="${esc(d.betrag_text)}" style="width:9em"></label>
        <div style="margin-top:8px"><button type="button" class="knopf scharf" data-eric="senden"
          ${daten} disabled>Verbindlich an ELSTER übermitteln</button></div>
        <div class="aktionsmeldung" data-meldung="senden"></div>
        <p class="klein still" style="margin:8px 0 0;max-width:78ch">Vor dem Senden baut der Server die
          Meldung neu aus der Buchführung und sendet nur, wenn sie mit dieser Prüfung übereinstimmt
          (SHA-256 <span class="mono">${esc(l.datensatz.sha256.slice(0, 12))}…</span>) und die
          Vorprüfung keine Fehler findet. Eine Abgabe lässt sich nicht zurücknehmen, nur berichtigen.</p></li>
    </ol></div>`;
}

function abgabeVerdrahten(d, id) {
  inhalt.querySelectorAll('button[data-eric="pruefen"]').forEach((b) => {
    b.onclick = () => ericAktion(b, 'pruefen', { art: b.dataset.art, jahr: b.dataset.jahr,
                                                 zeitraum: b.dataset.zeitraum });
  });
  const test = inhalt.querySelector('button[data-eric="test"]');
  if (test) {
    test.onclick = () => ericAktion(test, 'test', { art: test.dataset.art, jahr: test.dataset.jahr,
      zeitraum: test.dataset.zeitraum }, inhalt.querySelector('[data-meldung="test"]'));
  }
  const senden = inhalt.querySelector('button[data-eric="senden"]');
  if (!senden) return;
  const haken = inhalt.querySelector('[data-gelesen]');
  const betrag = inhalt.querySelector('[data-betrag]');
  const normal = (v) => v.replace(/[.\s€]/g, '');
  const pruefen = () => {
    senden.disabled = !(haken.checked && normal(betrag.value) === d.betrag_text);
  };
  haken.onchange = pruefen;
  betrag.oninput = pruefen;
  senden.onclick = () => {
    if (senden.disabled) return;
    ericAktion(senden, 'senden', { art: senden.dataset.art, jahr: senden.dataset.jahr,
      zeitraum: senden.dataset.zeitraum, pruefung: id, gelesen: haken.checked,
      betrag: betrag.value }, inhalt.querySelector('[data-meldung="senden"]'));
  };
}

/* ------------------------------------------------------------ ELSTER-Postfach */

/* Was Finanzamt und BZSt ins Postfach von Mein ELSTER legen, abgeholt über
 * ERiC (bh eric postfach). Angezeigt wird der Inhalt so, wie er kam: PDF als
 * PDF, XML gelesen und roh. */

const POSTFACH_ART = {
  WIdNrVA: 'Mitteilung der Wirtschafts-Identifikationsnummer',
  DivaEinwilligungRM: 'Rückmeldung zur elektronischen Bekanntgabe',
  EPMitteilung: 'Mitteilung', EPKurzmitteilung: 'Kurzmitteilung', EPBescheid: 'Bescheid',
  EBilanzRueckmeldung: 'Rückmeldung zur E-Bilanz', AnhangRueckmeldung: 'Rückmeldung zu Anhängen',
};
const POSTFACH_META = {
  steuernummer: 'Steuernummer', bescheiddatum: 'Bescheiddatum', erstelldatum: 'Erstellt',
  geschaeftszeichen: 'Geschäftszeichen', finanzamt: 'Finanzamt', sendername: 'Absender',
  bezeichnungnachricht: 'Bezeichnung', veranlagungsjahr: 'Veranlagungsjahr',
  verfuegbarbis: 'Abholbar bis', lieferticket: 'Lieferticket', bundesland: 'Land',
};

function metaDatum(v) {
  return /^\d{8}$/.test(v || '') ? `${v.slice(6)}.${v.slice(4, 6)}.${v.slice(0, 4)}` : v;
}

function postfachDatum(m) {
  return metaDatum(m.bescheiddatum || m.erstelldatum || '');
}

function postfachLink(pfad) {
  return `#/nachricht/${encodeURIComponent(pfad)}?${kontextAbfrage()}`;
}

SEITEN.postfach = async () => {
  const d = await holen('/api/postfach', true);
  inhalt.innerHTML = `
    ${kopf('ELSTER-Postfach',
      `Was Finanzamt und Bundeszentralamt dem ELSTER-Konto bereitstellen — Mitteilungen,
       Bescheide, Rückmeldungen —, abgeholt über ERiC mit dem Zertifikat aus mandant.json.`)}
    <div class="werkzeug">
      <button type="button" class="knopf" data-eric="postfach">Postfach abrufen</button>
      <span class="klein still" data-meldung="postfach"></span>
    </div>
    <div class="karte eng"><table class="t">
      <thead><tr><th>Datum</th><th>Art</th><th>Inhalt</th><th>Steuernummer</th><th>Abgeholt</th></tr></thead>
      <tbody>${d.zeilen.map((r) => `
        <tr class="klick" onclick="location.hash='${postfachLink(r.pfad)}'">
          <td class="nw">${postfachDatum(r.meta) || '<span class="still">–</span>'}</td>
          <td><a href="${postfachLink(r.pfad)}">${esc(POSTFACH_ART[r.datenart] || r.datenart)}</a>
            <div class="klein still mono">${esc(r.datenart)}</div></td>
          <td class="klein" style="max-width:52ch">${esc(r.kurztext || r.meta.geschaeftszeichen
            || r.meta.bezeichnungnachricht || '')}${r.anhaenge && r.anhaenge.length
            ? ` <span class="marke-chip">${r.anhaenge.length} Anhang</span>` : ''}</td>
          <td class="mono klein">${esc(r.meta.steuernummer || '')}</td>
          <td class="nw klein">${zeitpunkt(r.abgeholt_am)}
            ${r.bestaetigung && r.bestaetigung.code === '0' ? '<span class="marke-chip gut">bestätigt</span>'
              : '<span class="marke-chip warn">nicht bestätigt</span>'}</td>
        </tr>`).join('') || '<tr><td colspan="5" class="still">Noch nichts abgeholt.</td></tr>'}</tbody>
    </table></div>
    <p class="klein still" style="max-width:78ch">„Postfach abrufen“ fragt ELSTER, was bereitliegt,
      holt Neues mit allen Anhängen ab, legt es unter
      <span class="mono">mandanten/${esc(KONTEXT.mandant)}/elster/postfach/</span> ab und bestätigt
      danach den Empfang — so verlangt es ELSTER. Schon Abgeholtes wird nicht erneut geladen.</p>`;

  const b = inhalt.querySelector('button[data-eric="postfach"]');
  b.onclick = async () => {
    const m = inhalt.querySelector('[data-meldung="postfach"]');
    b.disabled = true;
    b.textContent = 'ruft ab …';
    const r = await posten('/api/eric/postfach', {});
    if (!r.ok) {
      b.disabled = false;
      b.textContent = 'Postfach abrufen';
      m.innerHTML = `<span class="minus">${esc(r.fehler || 'Fehler')}</span>`;
      return;
    }
    await SEITEN.postfach();
    inhalt.querySelector('[data-meldung="postfach"]').textContent =
      `${r.neu.length} neu, ${r.vorhanden.length} schon vorhanden.`;
  };
};

SEITEN.nachricht = async (z) => {
  const id = z.param[0];
  const d = await holen(`/api/postfach/nachricht?id=${encodeURIComponent(id)}`, true);
  const datei = (n) => mitKontext(`/postfach/datei?id=${encodeURIComponent(id)}&datei=${encodeURIComponent(n)}`);
  const titel = POSTFACH_ART[d.datenart] || d.datenart;
  const meta = Object.entries(d.meta).map(([k, v]) => `<dt>${esc(POSTFACH_META[k] || k)}</dt>
    <dd class="${/ticket|id$|Id$/.test(k) ? 'mono klein' : ''}">${esc(metaDatum(v))}</dd>`).join('');
  inhalt.innerHTML = `
    ${kopf(titel, '', `<a href="#/postfach">ELSTER-Postfach</a> › ${esc(postfachDatum(d.meta) || d.id)}`)}
    <div class="werkzeug" style="gap:6px">
      <span class="marke-chip mono">${esc(d.datenart)}</span>
      ${d.bestaetigung && d.bestaetigung.code === '0' ? '<span class="marke-chip gut">Empfang bestätigt</span>'
        : '<span class="marke-chip warn">Empfang nicht bestätigt</span>'}
      <span class="marke-chip">abgeholt ${zeitpunkt(d.abgeholt_am)}</span>
    </div>
    ${d.kurztext ? `<div class="karte"><p style="margin:0;max-width:80ch;font-size:14px">${esc(d.kurztext)}</p></div>` : ''}

    <h2>Inhalt</h2>
    ${d.inhaltsart === 'pdf' ? `<div class="karte eng">
        <iframe class="protokoll" src="${datei('datenpaket')}#view=FitH" title="Inhalt"></iframe>
        <div class="klein" style="padding:8px 14px;border-top:1px solid var(--linie)">
          <a href="${datei('datenpaket')}" target="_blank" rel="noopener">in neuem Tab öffnen</a></div></div>`
      : d.inhaltsart === 'xml' ? `<div class="karte eng">${xmlAnsicht(d.inhalt.trim())}</div>`
      : d.inhalt ? `<div class="rohtext">${esc(d.inhalt)}</div>`
      : '<p class="still">Kein Inhalt geliefert.</p>'}

    ${d.anhaenge.length ? `<h2>Anhänge</h2><div class="karte eng"><table class="t">
      <thead><tr><th>Datei</th><th>Bezeichnung</th><th>Typ</th><th class="z">Größe</th></tr></thead>
      <tbody>${d.anhaenge.map((a) => `<tr>
        <td>${a.datei ? `<a href="${datei(a.datei)}" target="_blank" rel="noopener">${esc(a.dateiname)}</a>`
          : esc(a.dateiname)}</td>
        <td>${esc(a.bezeichnung || '')}</td><td class="klein">${esc(a.typ || '')}</td>
        <td class="z klein">${(a.groesse || 0).toLocaleString('de-DE')} Byte</td></tr>`).join('')}
      </tbody></table></div>` : ''}

    <h2>Angaben von ELSTER</h2>
    <div class="karte"><dl class="feld" style="grid-template-columns:170px 1fr">${meta}
      <dt>Bereitstellung</dt><dd class="mono klein">${esc(d.id)}</dd>
      ${d.bereitstellungsticket ? `<dt>Bereitstellungsticket</dt><dd class="mono klein">${esc(d.bereitstellungsticket)}</dd>` : ''}
      <dt>Empfangsbestätigung</dt><dd>${d.bestaetigung ? `<span class="mono">${esc(d.bestaetigung.code)}</span>
        ${esc(d.bestaetigung.text || '')}` : '–'}</dd>
      <dt>Ablage</dt><dd class="mono klein">mandanten/${esc(KONTEXT.mandant)}/elster/postfach/${esc(id)}/</dd>
    </dl></div>`;
  xmlAnsichtenVerdrahten();
};

/* ---------------------------------------------------------- Schublade Buchung */

async function schubladeOeffnen(nummer) {
  const d = await holen(`/api/buchung/${nummer}`);
  const s = $('#schublade');
  if (d.fehler) {
    $('#schublade-titel').textContent = 'Buchung';
    $('#schublade-inhalt').innerHTML = esc(d.fehler);
    s.hidden = false;
    return;
  }
  const b = d.buchung;
  const ART = { eb: 'Eröffnungsbilanz', lfd: 'laufende Buchung', abschluss: 'Abschlussbuchung',
                vorjahr: 'Vorjahreszahl zum Vergleich', erklaerung: 'Zahl aus der Steuererklärung' };

  $('#schublade-titel').textContent = `Buchung ${b.nummer} · ${datum(b.datum)}`;
  $('#schublade-inhalt').innerHTML = `
    <p style="margin:0 0 14px;font-size:15px">${esc(b.buchungstext)}</p>
    <dl class="feld">
      <dt>Art</dt><dd>${ART[b.art] || esc(b.art)}</dd>
      <dt>Belegfeld</dt><dd class="mono">${esc(b.belegfeld || '–')}</dd>
      <dt>Betrag</dt><dd class="zahl">${eur(d.summe)}</dd>
    </dl>

    <h3>Buchungssatz</h3>
    <table class="t"><thead><tr><th>Konto</th><th class="z">Soll</th><th class="z">Haben</th></tr></thead>
      <tbody>${d.zeilen.map((r) => `<tr>
        <td><a href="#/konto/${r.konto}"><span class="mono">${r.konto}</span> ${esc(r.kontobez)}</a>
          ${r.text ? `<div class="klein still">${esc(r.text)}</div>` : ''}</td>
        <td class="z">${eur(r.soll_cent, { nullLeer: true })}</td>
        <td class="z">${eur(r.haben_cent, { nullLeer: true })}</td></tr>`).join('')}
      <tr class="summe"><td>Summe</td><td class="z">${eur(d.summe)}</td><td class="z">${eur(d.summe)}</td></tr>
      </tbody></table>

    ${d.bankumsatz ? `<h3>Bankumsatz</h3>
      <dl class="feld">
        <dt>Konto</dt><dd>${esc(d.bankumsatz.bankbez)} <span class="still klein">${esc(d.bankumsatz.iban)}</span></dd>
        <dt>Auszug</dt><dd>Nr. ${esc(d.bankumsatz.auszug || '')}</dd>
        <dt>Buchung / Valuta</dt><dd>${datum(d.bankumsatz.buchungsdatum)} / ${datum(d.bankumsatz.valuta)}</dd>
        <dt>Betrag</dt><dd>${betrag(d.bankumsatz.betrag_cent, { gruen: true })}</dd>
      </dl>
      <div class="rohtext" style="margin-top:8px">${esc(d.bankumsatz.text)}</div>` : ''}

    <h3>Belege</h3>
    ${d.belege.length ? `<div class="belegchips">${d.belege.map((e) => `
        <button type="button" class="belegchip" data-beleg="${e.id}">
          <b>${e.belegnr}</b> ${esc((e.aussteller ? e.aussteller + ' · ' : '') + e.bezeichnung.slice(0, 52))}
        </button>`).join('')}</div>`
      : '<p class="klein still" style="margin:0">Kein Beleg verknüpft.</p>'}`;
  inhaltLinksErgaenzen();
  s.hidden = false;
}

function schubladeSchliessen() { $('#schublade').hidden = true; }
$('#schublade-zu').onclick = () => setzeAbfrage('buchung', null);

/* ------------------------------------------------------------ Belegfenster */

async function viewerOeffnen(id) {
  const d = await holen(`/api/beleg/${id}`);
  const v = $('#viewer');
  if (d.fehler) { $('#viewer-inhalt').innerHTML = `<div class="hinweis">${esc(d.fehler)}</div>`; v.hidden = false; return; }
  const b = d.beleg;
  const dateiname = b.pfad.split('/').pop();
  // Der Mandant muss mit in die Adresse: die Belegnummer allein ist nur je
  // Mandant eindeutig, sonst sucht der Server beim falschen und findet nichts.
  const url = mitKontext(`/beleg/${b.id}/${encodeURIComponent(dateiname)}`);
  const endung = (b.pfad.split('.').pop() || '').toLowerCase();

  $('#viewer-titel').innerHTML = `${b.belegnr} · ${esc(b.bezeichnung.slice(0, 70))}`;
  $('#viewer-neu').href = url;

  let ansicht;
  if (endung === 'pdf') ansicht = `<iframe src="${url}#view=FitH" title="Beleg"></iframe>`;
  else if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(endung)) ansicht = `<img src="${url}" alt="Beleg">`;
  else ansicht = `<div class="hinweis">Für <span class="mono">.${esc(endung)}</span> gibt es keine Vorschau.
      <br><br><a href="${url}" download>Datei herunterladen</a></div>`;

  $('#viewer-inhalt').innerHTML = `
    <div class="beleg-kopf">
      <dl class="feld" style="grid-template-columns:104px 1fr">
        <dt>Datum</dt><dd>${datum(b.datum) || 'ohne Datum'}</dd>
        <dt>Kategorie</dt><dd>${esc(b.kategorie)}${b.aussteller ? ` · ${esc(b.aussteller)}` : ''}</dd>
        <dt>Archivpfad</dt><dd class="mono klein">${esc(b.pfad)}</dd>
        ${d.buchungen.length ? `<dt>Buchungen</dt><dd>${d.buchungen.map((x) =>
          `<a href="#" data-zubuchung="${x.nummer}">Nr ${x.nummer} · ${esc(x.buchungstext.slice(0, 46))}</a>`).join(' · ')}</dd>` : ''}
      </dl>
    </div>
    <div class="beleg-dok">${ansicht}</div>`;
  v.hidden = false;
}

function viewerSchliessen() { $('#viewer').hidden = true; }
$('#viewer-zu').onclick = () => setzeAbfrage('beleg', null);

document.addEventListener('click', (e) => {
  const a = e.target.closest('[data-zubuchung]');
  if (a) {
    e.preventDefault();
    setzeAbfrage('beleg', null);
    setTimeout(() => setzeAbfrage('buchung', a.dataset.zubuchung), 0);
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!$('#viewer').hidden) setzeAbfrage('beleg', null);
  else if (!$('#schublade').hidden) setzeAbfrage('buchung', null);
});

/* -------------------------------------------------------------- Markdown */

/* Reicht fuer die erzeugten Dokumente: Ueberschriften, Tabellen, Listen,
   Zitate, Trennlinien, fett/kursiv/Code. Kein allgemeiner Parser. */
function markdown(text) {
  const zeilen = text.replace(/\r/g, '').split('\n');
  const aus = [];
  let i = 0, liste = null;

  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');

  // Endet eine Zeile auf \ oder zwei Leerzeichen, bricht sie um - wie in bhl/druck.py.
  // Erst zusammenfuegen, dann formatieren - Fettdruck darf ueber zwei Zeilen laufen.
  const zeilenText = (teile) => inline(teile.map((t, n) => t.replace(/\\$/, '').trimEnd()
    + (n < teile.length - 1 ? (/(\\|  )$/.test(t) ? '\u0000' : ' ') : '')).join('')).replace(/\u0000/g, '<br>');

  const listeZu = () => { if (liste) { aus.push(`</${liste}>`); liste = null; } };

  while (i < zeilen.length) {
    const z = zeilen[i];

    if (/^\s*$/.test(z)) { listeZu(); i++; continue; }

    if (/^(---+|===+)\s*$/.test(z)) { listeZu(); aus.push('<hr>'); i++; continue; }

    const h = z.match(/^(#{1,6})\s+(.*)$/);
    if (h) { listeZu(); aus.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }

    // Tabelle: Kopfzeile, Trennzeile, dann Datenzeilen
    if (z.includes('|') && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(zeilen[i + 1] || '')) {
      listeZu();
      const zellen = (r) => r.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());
      const kopfZellen = zellen(z);
      const bund = zellen(zeilen[i + 1]).map((c) => c.endsWith(':') && !c.startsWith(':') ? ' class="z"' : '');
      const rumpf = [];
      i += 2;
      while (i < zeilen.length && zeilen[i].includes('|')) { rumpf.push(zellen(zeilen[i])); i++; }
      // Eine leere Kopfzeile (`| | |`) ist nur Markdown-Pflicht, keine Ueberschrift.
      const kopfHtml = kopfZellen.every((c) => !c) ? '' : `<thead><tr>${kopfZellen.map((c, n) =>
        `<th${bund[n] || ''}>${inline(c)}</th>`).join('')}</tr></thead>`;
      aus.push(`<table>${kopfHtml}<tbody>${rumpf.map((r) =>
        `<tr>${r.map((c, n) => `<td${bund[n] || ''}>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
      continue;
    }

    const li = z.match(/^\s*([-*+]|\d+\.)\s+(.*)$/);
    if (li) {
      const art = /\d/.test(li[1]) ? 'ol' : 'ul';
      if (liste !== art) { listeZu(); aus.push(`<${art}>`); liste = art; }
      const teile = [li[2]];
      i++;
      // eingerueckte Folgezeilen gehoeren noch zum Listenpunkt
      while (i < zeilen.length && /^\s+\S/.test(zeilen[i]) && !/^\s*([-*+]|\d+\.)\s+/.test(zeilen[i])) {
        teile.push(zeilen[i].trim()); i++;
      }
      aus.push(`<li>${zeilenText(teile)}</li>`);
      continue;
    }

    if (/^>\s?/.test(z)) {
      listeZu();
      const teile = [];
      while (i < zeilen.length && /^>\s?/.test(zeilen[i])) { teile.push(zeilen[i].replace(/^>\s?/, '')); i++; }
      aus.push(`<blockquote>${inline(teile.join(' '))}</blockquote>`);
      continue;
    }

    // Absatz: Folgezeilen anhaengen, bis eine Leerzeile oder ein Blockanfang kommt
    listeZu();
    const absatz = [z];
    i++;
    while (i < zeilen.length && !/^\s*$/.test(zeilen[i]) && !/^(#{1,6}\s|>|\s*[-*+]\s|\s*\d+\.\s|---)/.test(zeilen[i])
           && !zeilen[i].includes('|')) {
      absatz.push(zeilen[i]); i++;
    }
    aus.push(absatz.join(' ').trim() === '&nbsp;' ? '<p>&nbsp;</p>' : `<p>${zeilenText(absatz)}</p>`);
  }
  listeZu();
  return aus.join('\n');
}

/* ----------------------------------------------------------- Deine Aufgaben */

/** Wie weit eine Frist entfernt ist - in Worten, nicht als Datum allein. */
function abstand(frist, heute) {
  if (!frist) return '';
  const t = Math.round((new Date(frist) - new Date(heute)) / 86400000);
  return t < 0 ? `${-t} Tage über der Frist` : t === 0 ? 'heute' : t === 1 ? 'morgen' : `in ${t} Tagen`;
}

const AUFGABE_ART = {
  abgabe: ['Abgabe', 'schlecht'], punkt: ['', ''], frist: ['Frist', 'schlecht'],
  belege: ['Belege', 'warn'], platzhalter: ['Platzhalter', 'schlecht'],
};

function aufgabeHtml(a, heute) {
  const art = a.art === 'punkt' ? (PUNKT_ART[a.punkt_art] || ['Punkt', '']) : AUFGABE_ART[a.art];
  const zustand = a.zustand === 'bereit'
    ? '<span class="marke-chip gut">bereit zum Absenden</span>'
    : a.zustand === 'blockiert' ? '<span class="marke-chip schlecht">blockiert</span>'
    : a.zustand === 'laeuft' ? '<span class="marke-chip">Geschäftsjahr läuft noch</span>' : '';
  const wartet = (a.wartet_auf || []).map((id) =>
    `<a class="marke-chip" href="#/offen?punkt=${esc(id)}">wartet auf ${esc(id)}</a>`).join(' ');
  const blockiert = (a.blockiert_titel || []).length
    ? `<span class="marke-chip schlecht">blockiert ${a.blockiert_titel.map(esc).join(' · ')}</span>` : '';
  const belege = (a.belege || []).length ? `<ul class="klein">${a.belege.map((b) =>
    `<li>${datum(b.datum)} · ${esc(b.buchungstext)} · ${eur(b.betrag_cent)} EUR
      ${b.beschaffung ? `<span class="still">— ${esc(b.beschaffung)}</span>` : ''}</li>`).join('')}</ul>` : '';
  return `<div class="aufgabe ${a.ueberfaellig ? 'ueberfaellig' : ''} ${a.prioritaet || ''}">
    <div class="wann">${a.frist ? `<strong>${datum(a.frist)}</strong>${abstand(a.frist, heute)}
        ${a.frist_geerbt ? '<div class="klein still" title="Frist der Abgabe, die auf diesen Punkt wartet">Frist der Abgabe</div>' : ''}`
      : '<span class="still">ohne Frist</span>'}</div>
    <div>
      <div class="titel">${a.quelle ? `<span class="mono still klein">${esc(a.quelle)}</span> ` : ''}
        <a href="${a.verweis || '#/offen'}">${esc(a.titel)}</a></div>
      <div class="tun">${a.aktion ? esc(a.aktion)
        : '<span class="still">Was zu tun ist, steht noch nicht fest — Befund lesen.</span>'}</div>
      ${(a.hindernisse || []).length ? `<ul class="klein">${a.hindernisse.map((h) =>
          `<li class="leise">${esc(h)}</li>`).join('')}</ul>` : ''}
      ${belege}
      <div class="neben">${art && art[0] ? `<span class="marke-chip ${art[1]}">${esc(art[0])}</span>` : ''}
        ${zustand} ${wartet} ${blockiert}
        ${a.status === 'teilweise' ? '<span class="marke-chip warn">teilweise erledigt</span>' : ''}</div>
    </div></div>`;
}

SEITEN.aufgaben = async () => {
  const d = await holen('/api/aufgaben');
  const z = d.zaehler;
  const ueberfaellig = d.aufgaben.filter((a) => a.ueberfaellig);
  const bald = d.aufgaben.filter((a) => !a.ueberfaellig && a.frist
    && (new Date(a.frist) - new Date(d.heute)) / 86400000 <= 45);
  const spaeter = d.aufgaben.filter((a) => !a.ueberfaellig && !bald.includes(a));
  const gruppe = (titel, unter, liste) => liste.length ? `<h2>${esc(titel)}
      <span class="still">(${liste.length})</span></h2>${unter ? `<p class="unter">${unter}</p>` : ''}
      ${liste.map((a) => aufgabeHtml(a, d.heute)).join('')}` : '';
  inhalt.innerHTML = `
    ${kopf(`Deine Aufgaben · ${esc(d.mandant.name)}`,
      `Alles, was eine Handlung oder Entscheidung von dir braucht — eingesammelt aus Abgaben,
       Fristen, offenen Punkten, Platzhaltern und fehlenden Belegen des Geschäftsjahres ${d.jahr}.
       Ein Punkt ohne eigene Frist trägt die Frist der Abgabe, die auf ihn wartet.`)}
    <div class="offen-kopf">
      ${kz('Aufgaben', `<span class="zahl ${z.aufgaben ? 'minus' : 'plus'}">${z.aufgaben}</span>`,
           `${z.entscheidungen} davon Entscheidungen oder Klärungen`)}
      ${kz('Überfällig', `<span class="zahl ${z.ueberfaellig ? 'minus' : 'plus'}">${z.ueberfaellig}</span>`,
           z.ueberfaellig ? 'Frist abgelaufen' : 'keine Frist abgelaufen')}
      ${kz('Nächste Frist', d.naechste_frist ? datum(d.naechste_frist) : '–',
           d.naechste_frist ? abstand(d.naechste_frist, d.heute) : '')}
      ${kz('Im Blick', String(z.im_blick), 'liefern Dritte oder die Anwendung')}
    </div>
    ${gruppe('Überfällig', '', ueberfaellig)}
    ${gruppe('In den nächsten sechs Wochen', '', bald)}
    ${gruppe('Danach oder ohne Frist', '', spaeter)}
    ${!d.aufgaben.length ? '<div class="karte">Nichts zu tun.</div>' : ''}
    ${d.im_blick.length ? `<h2>Im Blick behalten <span class="still">(${d.im_blick.length})</span></h2>
      <p class="unter">Hier muss jemand anderes liefern — ein Dritter oder die Anwendung. Nachfassen, wenn es ausbleibt.</p>
      ${d.im_blick.map((a) => aufgabeHtml(a, d.heute)).join('')}` : ''}`;
};

/* ------------------------------------------------------------------ Abgaben */

const ZUSTAND = {
  erledigt: ['erledigt', 'gut'], bereit: ['bereit zum Absenden', 'gut'],
  blockiert: ['blockiert', 'schlecht'], laeuft: ['Geschäftsjahr läuft noch', ''],
};

SEITEN.abgaben = async () => {
  const d = await holen('/api/abgaben');
  const unterlage = (u) => u.art === 'dokument'
    ? `<a class="marke-chip" href="#/dokument/${u.ziel}">${esc(u.titel)}</a>`
    : `<a class="marke-chip" href="#/${u.ziel}">${esc(u.titel)}</a>`;
  const karte = (a) => `<div class="abgabe ${a.zustand}">
    <div class="kopfzeile">
      <span class="titel">${esc(a.titel)}</span>
      <span class="rechts">
        <span class="marke-chip ${ZUSTAND[a.zustand][1]}">${ZUSTAND[a.zustand][0]}</span>
        ${a.zustand === 'erledigt'
          ? (a.erledigt_am ? `<span class="marke-chip">${datum(a.erledigt_am)}</span>` : '')
          : `<span class="marke-chip ${a.tage < 0 ? 'schlecht' : a.tage <= 30 ? 'warn' : ''}">bis ${datum(a.frist)}</span>`}
      </span></div>
    <div class="meta">${esc(a.empfaenger || '')} · ${esc(a.weg || '')}</div>
    <div class="meta still">${esc(a.grundlage)}${a.gesetzlich_faellig
      ? ` · gesetzlich ${datum(a.gesetzlich_faellig)}` : ''}${a.notiz ? ` · ${esc(a.notiz)}` : ''}</div>
    ${a.formulare.length ? `<div style="margin-top:8px">${a.formulare.map((f) =>
      `<a class="marke-chip ${f.bereit ? 'gut' : 'warn'}" href="#/elster?formular=${f.id}"
         title="${esc(f.name)}">${f.bereit ? '✓' : f.offen + ' offen'} · ${esc(f.titel)}</a>`).join(' ')}</div>` : ''}
    ${a.hindernisse.length ? `<ul class="liste">${a.hindernisse.map((h) => `<li>${h.punkt
        ? `<a href="#/offen?punkt=${esc(h.punkt)}" class="mono">${esc(h.punkt)}</a> ` : ''}${
        h.verweis ? `<a href="${h.verweis}">${esc(h.text)}</a>` : esc(h.text)}${
        h.aktion ? `<div class="klein leise">→ ${esc(h.aktion)}</div>` : ''}</li>`).join('')}</ul>` : ''}
    ${a.schritt && a.schritt.aktion ? `<div class="schritt"><strong>Nächster Schritt
        (${esc(a.schritt.punkt)}):</strong> ${esc(a.schritt.aktion)}</div>` : ''}
    ${a.unterlagen.length ? `<div style="margin-top:8px">${a.unterlagen.map(unterlage).join(' ')}</div>` : ''}
  </div>`;
  inhalt.innerHTML = `
    ${kopf(`Abgaben ${d.jahr}`,
      `Was für das Geschäftsjahr abzugeben ist, an wen, auf welchem Weg und bis wann — und woran
       jede Abgabe noch hängt. „Bereit“ heißt: alles da, es fehlt nur das Absenden.
       Erledigt ist, was in <span class="mono">fristen.csv</span> mit Nachweis steht.`)}
    <div class="offen-kopf">
      ${kz('Blockiert', `<span class="zahl ${d.zaehler.blockiert ? 'minus' : 'plus'}">${d.zaehler.blockiert}</span>`, 'warten auf etwas')}
      ${kz('Bereit', String(d.zaehler.bereit), 'nur noch absenden')}
      ${d.zaehler.laeuft ? kz('Noch nicht dran', String(d.zaehler.laeuft), 'Geschäftsjahr läuft') : ''}
      ${kz('Erledigt', `<span class="zahl plus">${d.zaehler.erledigt}</span>`, 'mit Nachweis')}
    </div>
    ${d.abgaben.map(karte).join('')}`;
};

/* --------------------------------------------------------- ELSTER-Ausfüllhilfe */

SEITEN.elster = async () => {
  const d = await holen('/api/elster');
  if (d.fehler) { inhalt.innerHTML = kopf('ELSTER-Ausfüllhilfe', esc(d.fehler)); return; }
  const fokus = new URLSearchParams(location.hash.split('?')[1] || '').get('formular');
  const feld = (x, fi, xi) => {
    if (!('wert' in x)) {
      return `<tr class="ueberschrift"><td colspan="4">${esc(x.gruppe)}</td></tr>`;
    }
    const kn = x.konten || [];
    const s = `e${fi}f${xi}`;
    const eintrag = x.format === 'text' ? esc(x.eintrag ?? '')
      : x.format === 'cent' ? eur(x.eintrag) : (x.eintrag === null ? '' : NF.format(x.eintrag).replace(',00', ''));
    return `<tr>
      <td class="zeile">${esc(x.zeile || '')}</td>
      <td class="${kn.length ? 'aufklapp' : ''}" ${kn.length ? `data-auf="${s}"` : ''}>
        ${kn.length ? '<span class="pfeil">›</span> ' : ''}${esc(x.feld)}
        ${kn.length ? `<span class="still klein"> · ${kn.join(' ')}</span>` : ''}
        ${x.offen_punkt ? `<div><a class="marke-chip schlecht" href="#/offen?punkt=${esc(x.offen_punkt.id)}"
            >hängt an ${esc(x.offen_punkt.id)}: ${esc(x.offen_punkt.titel)}</a></div>` : ''}
        ${x.hinweis ? `<div class="klein leise" style="max-width:70ch">${esc(x.hinweis)}</div>` : ''}</td>
      <td class="eintrag ${x.format === 'text' ? 'text' : ''}">${eintrag}</td>
      <td class="z klein still">${x.format === 'euro' && x.betrag_cent !== null ? eur(x.betrag_cent) : ''}</td></tr>
      ${kn.length ? einbauZeile(s, kn, 4) : ''}`;
  };
  const formular = (f, fi) => `
    <h2 id="formular-${f.id}">${esc(f.titel)} <span class="still klein" style="font-weight:400">${esc(f.name)}</span>
      <span class="marke-chip ${f.bereit ? 'gut' : 'warn'}">${f.bereit ? 'eingabefertig' : `${f.offen} Zeile(n) offen`}</span></h2>
    <div class="karte eng"><table class="t">
      <thead><tr><th>Zeile</th><th>Feld</th><th class="z">Eintrag</th><th class="z">in Cent</th></tr></thead>
      <tbody>${f.felder.map((x, xi) => feld(x, fi, xi)).join('')}</tbody></table></div>`;
  inhalt.innerHTML = `
    ${kopf(`ELSTER-Ausfüllhilfe ${d.jahr}`,
      `Was in Mein ELSTER in welche Zeile gehört. Jeder Betrag wird beim Öffnen aus den Konten
       gerechnet; eine Zeile mit Konten anklicken zeigt die Buchungen dahinter.
       ${esc(d.hinweis || '')}`)}
    <div class="karte"><strong>Zeilennummern:</strong> ${esc(d.zeilen_stand || '')}</div>
    <h2>Gegenproben</h2>
    <div class="karte"><ul class="pruef">${d.gegenproben.map((p) => `
      <li><span class="zeichen ${p.ok ? 'ok' : 'nok'}">${p.ok ? '✓' : '✕'}</span>
        <span>${esc(p.text)}</span>
        <span class="detail">${eur(p.ist)}${p.ok ? '' : ` ≠ ${eur(p.soll)}`}</span></li>`).join('')}</ul></div>
    ${d.formulare.map(formular).join('')}`;
  if (fokus) {
    const el = document.getElementById(`formular-${fokus}`);
    if (el) setTimeout(() => el.scrollIntoView({ block: 'start' }), 0);
  }
};

/* ------------------------------------------------------------------ E-Bilanz */

SEITEN.ebilanz = async () => {
  const d = await holen('/api/ebilanz');
  if (d.fehler) { inhalt.innerHTML = kopf('E-Bilanz', esc(d.fehler)); return; }
  const s = d.stammdaten;
  const zeige = (p) => (p.element.startsWith('bs.eqLiab') || p.seite === 'guv' ? -p.summe : p.summe);
  const quelleChip = (q) => q === 'neu' ? '<span class="marke-chip warn">ungeprüft</span>'
    : q === 'auffang' ? '<span class="marke-chip">Auffangposition</span>' : '';
  const tabelle = (liste, schl) => `<table class="t"><thead><tr><th>Taxonomieposition</th>
      <th class="z">Betrag</th></tr></thead><tbody>${liste.map((p, i) => {
        const kn = p.konten.filter((k) => k !== 'GuV');
        const konten = d.konten.filter((k) => kn.includes(k.nummer));
        return `<tr><td class="${kn.length ? 'aufklapp' : ''}" ${kn.length ? `data-auf="${schl}${i}"` : ''}>
          ${kn.length ? '<span class="pfeil">›</span> ' : ''}${esc(p.bezeichnung)}
          <div class="mono klein still">${esc(p.element)}</div>
          ${p.ungeprueft ? '<span class="marke-chip warn">Elementname ungeprüft</span>' : ''}
          ${p.errechnet ? '<span class="marke-chip">aus der GuV errechnet</span>' : ''}</td>
          <td class="z">${betrag(zeige(p))}</td></tr>
          ${konten.map((k) => `<tr class="unterzeile" data-unter="${schl}${i}" hidden>
            <td><a href="#/konto/${k.nummer}" class="mono">${k.nummer}</a> ${esc(k.bezeichnung)}
              ${quelleChip(k.quelle)}${k.hinweis ? `<div class="klein leise">${esc(k.hinweis)}</div>` : ''}</td>
            <td class="z">${eur(k.seite === 'guv' || p.element.startsWith('bs.eqLiab') ? -k.saldo : k.saldo)}</td></tr>`).join('')}`;
      }).join('')}</tbody></table>`;
  inhalt.innerHTML = `
    ${kopf(`E-Bilanz ${KONTEXT.jahr}`,
      `Kontennachweis nach § 5b EStG: jedes Sachkonto mit Saldo und der Taxonomieposition, in die es
       eingeht — aus der Buchführung gezogen, nicht abgeschrieben. Mein ELSTER hat hierfür kein Formular;
       übermittelt wird ein XBRL-Datensatz über ERiC oder Drittsoftware.`)}
    <h2>Prüfungen</h2>
    <div class="karte"><ul class="pruef">${d.pruefungen.map((p) => `
      <li><span class="zeichen ${p.ok ? 'ok' : 'nok'}">${p.ok ? '✓' : '✕'}</span>
        <span>${esc(p.text)}</span><span class="detail">${esc(p.detail)}</span></li>`).join('')}</ul>
      ${d.ungeprueft.length ? `<p class="klein leise" style="margin:10px 0 0">Ungeprüft:
        <span class="mono">${d.ungeprueft.map(esc).join(', ')}</span></p>` : ''}</div>
    <h2>Stammdaten (GCD-Modul)</h2>
    <div class="karte eng"><table class="t"><tbody>
      ${[['Name', s.name], ['Rechtsform', s.rechtsform], ['Sitz', s.sitz], ['Register', s.register],
         ['Steuernummer', s.steuernummer], ['Finanzamt', s.finanzamt],
         ['Wirtschaftsjahr', s.wirtschaftsjahr], ['Größenklasse', s.groessenklasse],
         ['Bilanzart', s.bilanzart]]
        .map(([a, b]) => `<tr><td class="leise" style="width:180px">${esc(a)}</td><td>${esc(b || '')}</td></tr>`).join('')}
    </tbody></table></div>
    <h2>Bilanz <span class="still klein" style="font-weight:400">Aktiva ${eur(d.aktiva)} · Passiva ${eur(d.passiva)}</span></h2>
    <div class="karte eng">${tabelle(d.bilanz, 'b')}</div>
    <h2>Gewinn- und Verlustrechnung <span class="still klein" style="font-weight:400">Ergebnis ${eur(d.ergebnis)}</span></h2>
    <div class="karte eng">${tabelle(d.guv, 'g')}</div>
    ${d.fehlend.length ? `<h2>Ohne Zuordnung</h2><div class="karte eng"><table class="t"><tbody>
      ${d.fehlend.map((k) => `<tr><td class="mono">${k.nummer}</td><td>${esc(k.bezeichnung)}</td>
        <td class="z">${eur(k.saldo)}</td></tr>`).join('')}</tbody></table></div>` : ''}
    <p class="klein still">Zuordnung in <span class="mono">mandanten/${esc(KONTEXT.mandant)}/ebilanz.csv</span>.
      Positionen aus der DATEV-Übermittlung 2024 hat das Finanzamt bereits angenommen; „ungeprüft“ heißt
      neu vergeben und vor der Übermittlung gegen die Taxonomie zu halten.</p>`;
};

/* ---------------------------------------------------------------- Startlauf */

/* ------------------------------------------------------ Mandantenumschalter */

let MANDANTEN = [];

function jahreFuellen() {
  const m = MANDANTEN.find((x) => x.kuerzel === KONTEXT.mandant);
  if (!m) return;
  $('#jahrwahl').innerHTML = m.jahre.map((j) =>
    `<option value="${j}" ${String(j) === KONTEXT.jahr ? 'selected' : ''}>${j}</option>`).join('');
}

/** Die Zeitraumliste kommt vom Server - sie kennt Beginn und Ende des Jahres. */
async function zeitraeumeFuellen() {
  const w = $('#zeitraumwahl');
  if (!w) return;
  const d = await holen('/api/zeitraeume');
  w.innerHTML = d.zeitraeume.map((z) =>
    `<option value="${z.code}" ${z.code === KONTEXT.zeitraum ? 'selected' : ''}
     >${esc(z.text)}</option>`).join('');
  periodeSetzen(d.gewaehlt);
  $('#firma').textContent = d.mandant;
  $('#bestand').textContent = `${d.bestand.buchungen} Buchungen · ${d.bestand.belege} Belege`;
  // Auch der Fenstertitel traegt Firma und Zeitraum - nicht nur auf der Übersicht.
  if (d.gewaehlt) document.title = `${d.mandant} · ${d.gewaehlt.label}`;
  artSetzen(d);
}

/* Kapitalgesellschaft oder private Erklärung: das entscheidet über die
 * Menüpunkte und über die Namen der beiden Rechenwerke. Beides kommt vom
 * Server, damit die Oberfläche nichts über Kontenrahmen wissen muss. */
function artSetzen(d) {
  KONTEXT.art = d.art || 'gesellschaft';
  KONTEXT.titel = d.titel || {};
  KONTEXT.anlagen = d.anlagen || [];
  document.querySelectorAll('[data-art]').forEach((el) => {
    el.hidden = el.dataset.art !== KONTEXT.art;
  });
  const b = document.querySelector('[data-seite="bilanz"]');
  const g = document.querySelector('[data-seite="guv"]');
  const st = document.querySelector('[data-seite="steuern"]');
  if (b && KONTEXT.titel.bilanz) b.textContent = KONTEXT.titel.bilanz;
  if (g && KONTEXT.titel.guv) g.textContent = KONTEXT.titel.guv;
  if (st && KONTEXT.titel.steuern) st.textContent = KONTEXT.titel.steuern;
}

/** Die Unterlagen der Seitenleiste - je Mandant und Jahr andere Dateien.
 *
 * Muss beim Wechsel neu geholt werden: gebaut wurde die Liste frueher nur
 * einmal beim Start, danach standen unter der UG die Dokumente der privaten
 * Erklaerung - vier Verweise, die dort ins Leere zeigen.
 */
async function dokumenteFuellen() {
  const ziel = $('#dokumentliste');
  if (!ziel) return;
  try {
    const dok = await holen('/api/dokumente');
    ziel.innerHTML = dok.dokumente.map((x) =>
      `<a href="#/dokument/${x.id}" data-seite="dokument">${esc(x.titel)}</a>`).join('');
    const da = new Set(dok.dokumente.map((x) => x.id));
    document.querySelectorAll('[data-dok]').forEach((li) => { li.hidden = !da.has(li.dataset.dok); });
  } catch (e) {
    ziel.innerHTML = '';
    document.querySelectorAll('[data-dok]').forEach((li) => { li.hidden = true; });
  }
  navAktualisieren();
}

/** Zahl der offenen Punkte neben den Menuepunkt.
 *
 * Sie steht dort, damit ein offener Punkt nicht erst auffaellt, wenn jemand
 * die Seite aufschlaegt. Schlaegt der Abruf fehl, bleibt die Marke weg statt
 * eine Null zu behaupten. */
async function offenzahlSetzen() {
  const marke = $('#offenzahl');
  if (!marke) return;
  try {
    const d = await holen('/api/offen');
    const n = d.zaehler.offen + d.zaehler.belege_offen;
    marke.textContent = n;
    marke.hidden = !n;
  } catch { marke.hidden = true; }
}

/** Zahl der Aufgaben neben "Deine Aufgaben" - ueberfaellige faerben die Marke. */
async function aufgabenzahlSetzen() {
  const marke = $('#aufgabenzahl');
  if (!marke) return;
  try {
    const d = await holen('/api/aufgaben');
    marke.textContent = d.zaehler.aufgaben;
    marke.hidden = !d.zaehler.aufgaben;
    marke.classList.toggle('dringend', d.zaehler.ueberfaellig > 0);
  } catch { marke.hidden = true; }
}

/** Zahl der vorgelegten ELSTER-Übermittlungen neben "Übermittlungen". */
async function freigabezahlSetzen() {
  const marke = $('#freigabezahl');
  if (!marke) return;
  try {
    const d = await holen('/api/eric/offen');
    const n = (d.vorschlaege || []).filter((v) => v.stand === 'offen' || v.stand === 'überholt').length;
    marke.textContent = n;
    marke.hidden = !n;
  } catch { marke.hidden = true; }
}

/** Der Zeitraum in der Seitenleiste - er gilt für jede Seite, nicht nur die Übersicht. */
function periodeSetzen(z) {
  if (!z) return;
  $('#periode').innerHTML = esc(z.label)
    + (z.vollstaendig ? '' : ' <span class="still">· laufend</span>');
}

function wechsler() {
  $('#mandantwahl').innerHTML = MANDANTEN.map((m) =>
    `<option value="${esc(m.kuerzel)}" ${m.kuerzel === KONTEXT.mandant ? 'selected' : ''}
     >${esc(m.name)}</option>`).join('');
  jahreFuellen();

  $('#mandantwahl').onchange = (e) => {
    const m = MANDANTEN.find((x) => x.kuerzel === e.target.value);
    // Nach dem Wechsel auf die Übersicht: eine Kontonummer des einen Mandanten
    // gibt es beim anderen nicht zwingend.
    location.hash = `#/uebersicht?mandant=${encodeURIComponent(m.kuerzel)}&jahr=${m.jahre[0]}`;
  };
  $('#jahrwahl').onchange = (e) => {
    const weg = location.hash.split('?')[0] || '#/uebersicht';
    // Beim Jahreswechsel zurück auf das ganze Jahr: "März" des einen Jahres
    // stillschweigend auf ein anderes zu übertragen wäre irreführend.
    location.hash = `${weg}?mandant=${encodeURIComponent(KONTEXT.mandant)}&jahr=${e.target.value}`;
  };
  $('#zeitraumwahl').onchange = (e) => {
    const weg = location.hash.split('?')[0] || '#/uebersicht';
    const z = e.target.value;
    location.hash = `${weg}?mandant=${encodeURIComponent(KONTEXT.mandant)}&jahr=${KONTEXT.jahr}`
                  + (z && z !== 'jahr' ? `&zeitraum=${z}` : '');
  };
}

/* ---------------------------------------------------------------- Startlauf */

(async function start() {
  MANDANTEN = (await (await fetch('/api/mandanten')).json()).mandanten;

  const z = zustand();
  const k = kontextPruefen(z) || { mandant: '', jahr: '', zeitraum: 'jahr' };
  kontextSetzen(k.mandant, k.jahr, k.zeitraum);
  if (z.jahr && String(z.jahr) !== k.jahr) {
    const weg = location.hash.split('?')[0] || '#/uebersicht';
    location.replace(`${location.pathname}${weg}?${kontextAbfrage()}`);
  }
  wechsler();

  try {
    await zeitraeumeFuellen();          // setzt Firma, Zeitraum und Fußzeile
    await dokumenteFuellen();
  } catch (e) {
    fehlerSeite(String(e && e.message || e));
    return;
  }
  offenzahlSetzen();
  aufgabenzahlSetzen();
  freigabezahlSetzen();

  if (!location.hash) location.hash = `#/uebersicht?${kontextAbfrage()}`;
  zeichnen();
})();
