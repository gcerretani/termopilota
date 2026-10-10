// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — Storico → "Esplora i dati": qualsiasi misura di qualsiasi dispositivo
// (sensori, termostati, condizionatori, stazione CFR, valori calcolati) su un grafico,
// fino a MAX serie insieme, un asse y per unita' di misura. La scelta resta nell'URL (?s=).

(function () {
  const contenitore = document.getElementById('esplora');
  if (!contenitore || typeof TPGrafici === 'undefined') return;

  const SORGENTI = { sensore: 'Sensore', stanza: 'Termostato', ac: 'Condizionatore', cfr: 'Stazione CFR',
                     sistema: 'Calcolati' };
  const ORDINE = ['sensore', 'stanza', 'ac', 'cfr', 'sistema'];
  let catalogo = [];
  let max = 8;
  let scelte = new URLSearchParams(location.search).getAll('s');
  let intervallo = '7g';
  let grafico = null;

  const enc = encodeURIComponent;
  const el = (id) => document.getElementById(id);

  function aggiornaUrl() {
    const url = new URL(location.href);
    url.searchParams.delete('s');
    scelte.forEach(s => url.searchParams.append('s', s));
    history.replaceState(null, '', url);
  }

  function renderScelta() {
    const filtro = el('esploraCerca').value.trim().toLowerCase();
    const gruppi = [...catalogo].sort((a, b) => ORDINE.indexOf(a.sorgente) - ORDINE.indexOf(b.sorgente)
      || a.nome.localeCompare(b.nome));
    const html = gruppi.map(g => {
      const titolo = `${g.nome} · ${SORGENTI[g.sorgente] || g.sorgente}`;
      const serie = g.serie.filter(s => !filtro || `${titolo} ${s.etichetta}`.toLowerCase().includes(filtro));
      if (!serie.length) return '';
      return `<div class="tp-esplora-gruppo">
        <div class="tp-esplora-titolo">${escapeHtml(titolo)}</div>
        <div class="tp-legend">${serie.map(s => {
          const attiva = scelte.includes(s.chiave);
          return `<button type="button" class="tp-serie-chip" aria-pressed="${attiva}" data-serie="${escapeHtml(s.chiave)}"
            ${!attiva && scelte.length >= max ? 'disabled' : ''}>${escapeHtml(s.etichetta)}${s.unita ? ` <span class="tp-muted">${escapeHtml(s.unita)}</span>` : ''}</button>`;
        }).join('')}</div>
      </div>`;
    }).join('');
    el('esploraSerie').innerHTML = html || '<div class="tp-muted small">Nessuna misura trovata.</div>';
    el('esploraConteggio').textContent = `${scelte.length} di ${max}`;
  }

  async function carica() {
    const vuoto = el('esploraVuoto');
    if (grafico) { grafico.destroy(); grafico = null; }
    el('legendaEsplora').innerHTML = '';
    if (!scelte.length) {
      vuoto.textContent = 'Scegli una o più misure qui sopra.';
      vuoto.style.display = '';
      return;
    }
    const { da, a, risoluzione } = TPGrafici.periodo(intervallo);
    try {
      const dati = await apiGetJson(`/api/serie/dati?${scelte.map(s => `s=${enc(s)}`).join('&')}&da=${da}&a=${a}&risoluzione=${risoluzione}`);
      const serie = dati.serie.filter(s => s.punti.length).map(s => ({ ...s, label: `${s.nome} · ${s.etichetta}` }));
      vuoto.textContent = 'Nessuna misura in questo intervallo: TermoPilota le registra ogni 15 minuti.';
      vuoto.style.display = serie.length ? 'none' : '';
      if (serie.length) grafico = TPGrafici.graficoSerie(el('graficoEsplora'), serie, risoluzione, { legenda: 'legendaEsplora' });
    } catch (e) {
      vuoto.textContent = `Dati non disponibili: ${e.message}`;
      vuoto.style.display = '';
    }
  }

  async function avvia() {
    try {
      const d = await apiGetJson('/api/serie');
      catalogo = d.dispositivi;
      max = d.max_serie;
    } catch (e) {
      el('esploraSerie').innerHTML = `<div class="text-danger small">${escapeHtml(e.message)}</div>`;
      return;
    }
    const note = new Set(catalogo.flatMap(g => g.serie.map(s => s.chiave)));
    scelte = scelte.filter(s => note.has(s)).slice(0, max);
    if (!scelte.length) {
      // Di partenza: la temperatura esterna usata dal motore, se c'e'
      const base = 'sistema|termopilota|temp_esterna';
      if (note.has(base)) scelte = [base];
    }
    renderScelta();
    carica();
    if (location.hash === '#esplora') contenitore.scrollIntoView({ block: 'start' });
  }

  el('esploraSerie').addEventListener('click', (e) => {
    const b = e.target.closest('[data-serie]');
    if (!b) return;
    const chiave = b.dataset.serie;
    scelte = scelte.includes(chiave) ? scelte.filter(s => s !== chiave) : [...scelte, chiave].slice(0, max);
    aggiornaUrl();
    renderScelta();
    carica();
  });
  el('esploraCerca').addEventListener('input', renderScelta);
  el('esploraAzzera').addEventListener('click', () => {
    scelte = [];
    aggiornaUrl();
    renderScelta();
    carica();
  });
  contenitore.querySelectorAll('[data-esplora-intervallo]').forEach(btn => btn.addEventListener('click', () => {
    contenitore.querySelectorAll('[data-esplora-intervallo]').forEach(b => {
      b.classList.toggle('active', b === btn);
      b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
    });
    intervallo = btn.dataset.esploraIntervallo;
    carica();
  }));

  avvia();
})();
