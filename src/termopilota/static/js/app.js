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

const formatoEuro = (v) => v.toLocaleString('it-IT', { style: 'currency', currency: 'EUR' });

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

// Chip "Aggiornato alle" nella barra superiore
function segnalaAggiornamento(testo, errore) {
  const chip = document.getElementById('aggiornatoAlle');
  if (!chip) return;
  chip.textContent = testo;
  chip.classList.toggle('errore', !!errore);
}

(function () {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');

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
