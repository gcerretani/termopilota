// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — service worker minimale.
// Cache-first SOLO per gli asset statici (/static/*): pagine HTML, /api/* e
// login passano sempre dalla rete, così i dati non risultano mai stantii e i
// redirect di autenticazione funzionano normalmente.
// Aggiornare la versione quando cambia un asset statico.
const CACHE = 'termopilota-static-v1';

const PRECACHE = [
  '/static/vendor/bootstrap/bootstrap.min.css',
  '/static/vendor/bootstrap/bootstrap.bundle.min.js',
  '/static/vendor/bootstrap-icons/bootstrap-icons.min.css',
  '/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2',
  '/static/vendor/chartjs/chart.umd.min.js',
  '/static/css/theme.css',
  '/static/js/theme.js',
  '/static/js/admin.js',
  '/static/js/dashboard.js',
  '/static/js/storico.js',
  '/static/icons/icon-192.png',
  '/static/icons/favicon.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((chiavi) => Promise.all(chiavi.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || !url.pathname.startsWith('/static/')) {
    return; // rete diretta, nessuna cache
  }
  event.respondWith(
    caches.match(event.request).then((hit) => {
      if (hit) return hit;
      return fetch(event.request).then((res) => {
        if (res.ok) {
          const copia = res.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copia));
        }
        return res;
      });
    })
  );
});
