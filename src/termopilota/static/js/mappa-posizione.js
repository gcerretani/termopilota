// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — Selettore di posizione su mappa OpenStreetMap (Leaflet), legato a
// una coppia di campi latitudine/longitudine. La mappa e' un aiuto: se le tile
// non si caricano i campi numerici restano utilizzabili.

/* global L */
function selettorePosizione(contenitore, campoLat, campoLon, opzioni = {}) {
  if (typeof L === 'undefined' || !contenitore || !campoLat || !campoLon) return null;
  const precisione = opzioni.precisione ?? 4;
  const arrotonda = (v) => Number(v.toFixed(precisione));
  const leggi = () => {
    const lat = parseFloat(campoLat.value);
    const lon = parseFloat(campoLon.value);
    return Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180
      ? [lat, lon] : null;
  };

  const iniziale = leggi() || [43.77, 11.25];
  const mappa = L.map(contenitore, { scrollWheelZoom: false }).setView(iniziale, opzioni.zoom ?? 11);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
  }).addTo(mappa);
  const marker = L.marker(iniziale, { draggable: true, keyboard: true, title: 'Trascina per spostare' }).addTo(mappa);

  function imposta(lat, lon, centra = false) {
    campoLat.value = arrotonda(lat);
    campoLon.value = arrotonda(lon);
    marker.setLatLng([lat, lon]);
    if (centra) mappa.setView([lat, lon], Math.max(mappa.getZoom(), opzioni.zoom ?? 11));
    campoLat.dispatchEvent(new Event('change', { bubbles: true }));
  }

  marker.on('dragend', () => { const p = marker.getLatLng(); imposta(p.lat, p.lng); });
  mappa.on('click', (e) => imposta(e.latlng.lat, e.latlng.lng));
  const daiCampi = () => {
    const p = leggi();
    if (p) { marker.setLatLng(p); mappa.panTo(p); }
  };
  campoLat.addEventListener('input', daiCampi);
  campoLon.addEventListener('input', daiCampi);

  // La mappa nasce in un layout che puo' cambiare (tema, larghezza): ricalcola le dimensioni
  setTimeout(() => mappa.invalidateSize(), 200);
  window.addEventListener('resize', () => mappa.invalidateSize());

  return {
    imposta: (lat, lon) => imposta(lat, lon, true),
    miaPosizione: () => new Promise((ok, ko) => {
      if (!navigator.geolocation) { ko(new Error('Geolocalizzazione non disponibile')); return; }
      navigator.geolocation.getCurrentPosition(
        (pos) => { imposta(pos.coords.latitude, pos.coords.longitude, true); ok(); },
        (err) => ko(new Error(err.message || 'Posizione non disponibile')),
        { enableHighAccuracy: true, timeout: 10000 },
      );
    }),
  };
}
