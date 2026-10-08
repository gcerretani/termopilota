// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — tema chiaro/scuro/automatico.
// Caricato nel <head>: applica subito il tema salvato per evitare il flash all'avvio.
// Al cambio emette l'evento 'tema-cambiato' su document (i grafici si ricolorano).

const TEMA_KEY = 'termopilota_theme';
const TEMA_SCURO_MQ = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

function preferenzaTema() {
  try {
    const v = localStorage.getItem(TEMA_KEY);
    return v === 'dark' || v === 'light' ? v : 'auto';
  } catch (e) { return 'auto'; }
}

function temaEffettivo(pref) {
  if (pref === 'dark' || pref === 'light') return pref;
  return TEMA_SCURO_MQ && TEMA_SCURO_MQ.matches ? 'dark' : 'light';
}

function applicaTema() {
  const tema = temaEffettivo(preferenzaTema());
  const html = document.documentElement;
  const cambiato = html.getAttribute('data-theme') !== tema;
  html.setAttribute('data-theme', tema);
  html.setAttribute('data-bs-theme', tema);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', tema === 'dark' ? '#0f1117' : '#f4f6f9');
  applicaIconaTema();
  if (cambiato) document.dispatchEvent(new CustomEvent('tema-cambiato', { detail: { tema } }));
}

function applicaIconaTema() {
  const icon = document.getElementById('themeIcon');
  if (icon) {
    const tema = document.documentElement.getAttribute('data-theme');
    icon.className = tema === 'dark' ? 'bi bi-sun' : 'bi bi-moon-stars';
  }
  const pref = preferenzaTema();
  document.querySelectorAll('[data-tema-scelta]').forEach(b => {
    b.classList.toggle('active', b.dataset.temaScelta === pref);
    b.setAttribute('aria-pressed', b.dataset.temaScelta === pref ? 'true' : 'false');
  });
}

function impostaTema(pref) {
  try {
    if (pref === 'auto') localStorage.removeItem(TEMA_KEY);
    else localStorage.setItem(TEMA_KEY, pref);
  } catch (e) { /* noop */ }
  applicaTema();
}

function toggleTema() {
  impostaTema(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
}

applicaTema();
if (TEMA_SCURO_MQ && TEMA_SCURO_MQ.addEventListener) {
  TEMA_SCURO_MQ.addEventListener('change', () => { if (preferenzaTema() === 'auto') applicaTema(); });
}
document.addEventListener('DOMContentLoaded', applicaIconaTema);
