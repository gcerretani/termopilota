// TermoPilota — toggle tema chiaro/scuro.
// Applica subito il tema salvato per evitare il flash all'avvio.
(function () {
  const KEY = 'termopilota_theme';
  const stored = (function () {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  })();
  const initial = stored === 'dark' || stored === 'light'
    ? stored
    : (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  document.documentElement.setAttribute('data-theme', initial);
})();

function applicaIconaTema() {
  const icon = document.getElementById('themeIcon');
  if (!icon) return;
  const tema = document.documentElement.getAttribute('data-theme');
  icon.className = tema === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
}

function toggleTema() {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try { localStorage.setItem('termopilota_theme', next); } catch (e) { /* noop */ }
  applicaIconaTema();
}

document.addEventListener('DOMContentLoaded', applicaIconaTema);
