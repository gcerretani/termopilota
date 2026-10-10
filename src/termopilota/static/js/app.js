// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — script comune a tutte le pagine: service worker, installazione
// come app, refresh periodico e piccoli helper di formattazione.

// Esegue fn ogni `ms` millisecondi e, al ritorno sulla scheda dopo almeno
// `minimoAlRitorno` ms dall'ultima esecuzione, subito.
function ogni(ms, fn, minimoAlRitorno = 60000) {
  let ultimo = Date.now();
  const esegui = () => { ultimo = Date.now(); fn(); };
  setInterval(esegui, ms);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - ultimo > minimoAlRitorno) esegui();
  });
  return esegui;
}

// Aggiornamenti live: il server incrementa una versione a ogni evento dei
// dispositivi (webhook Netatmo/SmartThings); se cambia si ricaricano i dati.
// Solo a pagina visibile, una richiesta minima ogni 10 s.
function ascoltaLive(callback, ms = 10000) {
  let versione = null;
  const controlla = async () => {
    if (document.visibilityState !== 'visible') return;
    try {
      const res = await fetch('/api/live');
      if (!res.ok) return;
      const v = (await res.json()).versione;
      if (versione !== null && v !== versione) callback();
      versione = v;
    } catch (e) { /* rete assente: riprova al prossimo giro */ }
  };
  controlla();
  setInterval(controlla, ms);
}

const formatoEuro =(v) => v.toLocaleString('it-IT', { style: 'currency', currency: 'EUR' });

function escapeHtml(testo) {
  return String(testo ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// Data/ora locale nel formato delle raccomandazioni ("YYYY-MM-DDTHH:00").
// toISOString() darebbe l'ora UTC, sfasata rispetto all'ora del server.
function oraLocale(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:00`;
}
const giornoLocale = (d = new Date()) => oraLocale(d).slice(0, 10);

// Valori con unita' ("—" se mancano)
const gradi = (v, decimali = 1) => (v === null || v === undefined) ? '—' : `${Number(v).toFixed(decimali)}°`;
const percento = (v) => (v === null || v === undefined) ? '—' : `${Math.round(v)}%`;
const oraDaEpoch = (s) => new Date(s * 1000).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });

// ── Sensori (stazione meteo Netatmo) ─────────────────────────────────────
const TIPI_SENSORE = { NAMain: 'Stazione meteo, modulo base', NAModule1: 'Modulo esterno', NAModule2: 'Anemometro',
                       NAModule3: 'Pluviometro', NAModule4: 'Modulo interno' };
const tipoSensore = (tipo) => TIPI_SENSORE[tipo] || tipo || 'Sensore';
// Netatmo: rf_status e wifi_strength piu' bassi = segnale migliore
const qualitaSegnale = (chiave, v) => (v === null || v === undefined) ? '—'
  : chiave === 'segnale_wifi' ? (v <= 60 ? 'buono' : v <= 75 ? 'medio' : 'debole')
  : (v <= 70 ? 'buono' : v <= 85 ? 'medio' : 'debole');

// Valore di una grandezza {chiave, unita, valore} con la sua unita'
function valoreGrandezza(g) {
  if (g.valore === null || g.valore === undefined) return '—';
  if (g.chiave.startsWith('segnale_')) return qualitaSegnale(g.chiave, g.valore);
  if (g.unita === '°C') return gradi(g.valore);
  if (g.unita === '%') return percento(g.valore);
  const decimali = g.unita === 'mm' || Math.abs(g.valore) < 10 ? 1 : 0;
  return `${Number(g.valore).toFixed(decimali)}${g.unita ? ' ' + g.unita : ''}`;
}

// Riquadro "Sensori nella stanza": un elemento per sensore, con tutte le misure (non diagnostiche)
function htmlSensori(sensori) {
  if (!sensori || !sensori.length) return '';
  return `<div class="tp-list">${sensori.map(m => {
    const misure = (m.grandezze || []).filter(g => !g.diagnostica && !['minima', 'massima'].includes(g.chiave));
    return `<a class="tp-list-item" href="/dispositivi/meteo/${encodeURIComponent(m.id)}">
      <span class="tp-list-icon"><i class="bi bi-cloud-sun"></i></span>
      <div class="tp-list-body">
        <div class="tp-list-title">${escapeHtml(m.nome)} <span class="tp-muted small">· ${escapeHtml(tipoSensore(m.tipo))}</span></div>
        <div class="tp-list-sub">${misure.length ? misure.map(g => `${escapeHtml(g.etichetta)} <strong>${escapeHtml(valoreGrandezza(g))}</strong>`).join(' · ')
          : escapeHtml(m.errore || 'Nessuna misura')}</div>
      </div>
      <i class="bi bi-chevron-right tp-muted"></i>
    </a>`;
  }).join('')}</div>`;
}

// Stato dell'automazione di una zona: [etichetta, icona, classe colore]
const STATI_ZONA = {
  ac: ['Pompa di calore', 'snow', 'ac'],
  affiancata: ['AC + caldaia', 'snow', 'ac'],
  gas: ['Caldaia', 'fire', 'gas'],
  errore: ['Caldaia (dati mancanti)', 'exclamation-triangle', 'gas'],
  finestra: ['Finestra aperta', 'wind', 'danger'],
  pausa: ['In pausa', 'pause-circle', 'neutro'],
  manuale: ['Manuale', 'hand-index', 'neutro'],
  esclusa: ['Esclusa', 'slash-circle', 'neutro'],
  raffrescamento: ['Raffrescamento', 'snow2', 'neutro'],
};

function badgeStatoZona(stato, simulazione = false) {
  const [etichetta, icona, classe] = STATI_ZONA[stato] || ['In attesa', 'hourglass', 'neutro'];
  return `<span class="tp-badge-stato ${classe}"><i class="bi bi-${icona}"></i>${etichetta}${simulazione ? ' · simulazione' : ''}</span>`;
}

// Chip "Aggiornato alle" nella barra superiore
function segnalaAggiornamento(testo, errore) {
  const chip = document.getElementById('aggiornatoAlle');
  if (!chip) return;
  chip.textContent = testo;
  chip.classList.toggle('errore', !!errore);
}

// Ora dell'ultima lettura di un dispositivo ("YYYY-MM-DDTHH:MM:SS" locale del
// server), con "x min fa" aggiornato ogni 30 s; oltre LETTURA_VECCHIA_MIN in rosso.
const LETTURA_VECCHIA_MIN = 10;

const letturaVecchia = (iso) => !iso || Date.now() - new Date(iso) > LETTURA_VECCHIA_MIN * 60000;

function testoLettura(iso) {
  if (!iso) return '<i class="bi bi-exclamation-circle"></i> Non ancora letto';
  const minuti = Math.max(0, Math.floor((Date.now() - new Date(iso)) / 60000));
  const fa = minuti < 1 ? 'adesso' : minuti < 60 ? `${minuti} min fa` : `${Math.floor(minuti / 60)} h fa`;
  return `<i class="bi bi-clock-history"></i> Letto alle ${iso.slice(11, 16)} · ${fa}`;
}

function etichettaLettura(iso) {
  return `<span class="tp-letto${letturaVecchia(iso) ? ' vecchio' : ''}" data-letto="${escapeHtml(iso || '')}">${testoLettura(iso)}</span>`;
}

function aggiornaLetture() {
  document.querySelectorAll('[data-letto]').forEach(el => {
    el.innerHTML = testoLettura(el.dataset.letto);
    el.classList.toggle('vecchio', letturaVecchia(el.dataset.letto));
  });
}

(function () {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');
  setInterval(aggiornaLetture, 30000);

  // Pulsante "Installa app" (Chrome/Edge/Android): compare solo se il browser lo propone
  let promptInstallazione = null;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    promptInstallazione = e;
    document.querySelectorAll('[data-installa-app]').forEach(el => el.classList.remove('d-none'));
  });
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-installa-app]');
    if (!btn || !promptInstallazione) return;
    promptInstallazione.prompt();
    await promptInstallazione.userChoice;
    promptInstallazione = null;
    btn.classList.add('d-none');
  });
})();
