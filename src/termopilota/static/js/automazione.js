// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — Automazione: interruttore, zone (stato, inclusione, pausa) e log eventi (refresh 30 s).

(function () {
  // Zone configurate (anche prima del primo ciclo o ad automazione spenta),
  // arricchite con l'ultima decisione del ciclo
  let zone = window.zoneConfigurate || [];
  let ultimo = { zone: [], pause: {} };

  function impostaStato(attiva) {
    const toggle = document.getElementById('autoToggle');
    if (toggle) toggle.checked = attiva;
    const badge = document.getElementById('autoStatusBadge');
    if (badge) {
      badge.classList.toggle('on', attiva);
      badge.textContent = attiva ? 'Attiva' : 'Non attiva';
    }
  }

  function cardZona(cfgZona) {
    const z = ultimo.zone.find(d => d.room_id === cfgZona.room_id) || {};
    const inclusa = cfgZona.automazione !== false;
    const pausaFino = ultimo.pause[cfgZona.room_id] || z.pausa_fino;
    // Zona appena reinclusa: l'ultima decisione del ciclo dice ancora "esclusa"
    const inAttesa = inclusa && z.stato === 'esclusa';
    const stato = !inclusa ? 'esclusa' : (pausaFino ? 'pausa' : (inAttesa ? null : z.stato));
    const cls = stato === 'affiancata' ? 'affiancata' : (stato && z.fonte) || 'off';
    const progresso = (z.t_stanza != null && z.target)
      ? Math.min(100, Math.max(0, (z.t_stanza / z.target) * 100)).toFixed(0) : null;
    const modalita = cfgZona.modalita === 'affiancata' ? 'affiancata' : 'esclusiva';
    const rid = escapeHtml(cfgZona.room_id || '');
    const motivo = stato === 'pausa' && pausaFino ? `In pausa fino alle ${oraDaEpoch(pausaFino)}`
      : (!inclusa ? "Zona esclusa dall'automazione" : (inAttesa ? 'Aggiornamento in corso…' : z.motivo));
    return `<div class="col-md-6 col-xl-4">
      <div class="zona-card ${cls}">
        <div class="d-flex justify-content-between align-items-start gap-2">
          <div class="min-w-0">
            <div class="fw-semibold text-truncate">
              ${cfgZona.room_id ? `<a class="text-reset text-decoration-none" href="/dispositivi/stanza/${encodeURIComponent(cfgZona.room_id)}">${escapeHtml(cfgZona.nome)}</a>` : escapeHtml(cfgZona.nome)}
            </div>
            <div class="mt-1">${badgeStatoZona(stato, z.simulazione)}</div>
            <div class="small tp-muted mt-1">modalità ${modalita}</div>
          </div>
          <div class="text-end">
            <div class="zona-temp">${gradi(z.t_stanza)}</div>
            <div class="small tp-muted mt-1">target <strong>${gradi(z.target)}</strong></div>
          </div>
        </div>
        ${progresso !== null ? `<div class="zona-progress mt-3" role="progressbar" aria-valuenow="${progresso}" aria-valuemin="0" aria-valuemax="100"><div style="width:${progresso}%"></div></div>` : ''}
        ${motivo ? `<div class="small tp-muted mt-2">${escapeHtml(motivo)}</div>` : ''}
        <div class="d-flex justify-content-between mt-2 small tp-muted">
          <span>${z.costo_gas ? `<span class="tp-gas">€${z.costo_gas.toFixed(3)}</span> · <span class="tp-ac">€${z.costo_ac.toFixed(3)}</span> /kWh<sub>th</sub>` : ''}</span>
          <span>${escapeHtml(z.aggiornato || '')}</span>
        </div>
        ${z.errore_ac ? `<div class="text-danger mt-2 small"><i class="bi bi-exclamation-triangle me-1"></i>${escapeHtml(z.errore_ac)}</div>` : ''}
        ${cfgZona.room_id ? `<div class="zona-azioni">
          <div class="form-check form-switch">
            <input class="form-check-input" type="checkbox" role="switch" id="incl-${rid}" data-includi="${rid}" ${inclusa ? 'checked' : ''}>
            <label class="form-check-label small" for="incl-${rid}">Inclusa</label>
          </div>
          ${inclusa ? `<div class="tp-segmented ms-auto" role="group" aria-label="Pausa">
            ${pausaFino
              ? `<button type="button" data-pausa="${rid}" data-ore="0"><i class="bi bi-play-fill"></i>Riprendi</button>`
              : `<button type="button" data-pausa="${rid}" data-ore="1"><i class="bi bi-pause-fill"></i>1 h</button>
                 <button type="button" data-pausa="${rid}" data-ore="3">3 h</button>`}
          </div>` : ''}
        </div>` : ''}
      </div>
    </div>`;
  }

  function renderZone() {
    const zoneDiv = document.getElementById('zoneStatus');
    if (zoneDiv && zone.length > 0) zoneDiv.innerHTML = zone.map(cardZona).join('');
  }

  async function carica() {
    try {
      const data = await apiGetJson('/api/automazione');
      impostaStato(data.attiva);
      ultimo = { zone: data.zone || [], pause: data.pause || {} };
      renderZone();

      if (data.log && data.log.length > 0) {
        document.getElementById('logEventiWrap').style.display = '';
        document.getElementById('logEventi').innerHTML = data.log.map(e => {
          const ac = e.azione.includes('AC');
          return `<div class="tp-log-item">
            <span class="tp-log-ts">${escapeHtml(e.ts)}</span>
            <span class="min-w-0">
              <strong>${escapeHtml(e.zona)}</strong>
              <span class="mx-1 fw-semibold ${ac ? 'tp-ac' : 'tp-gas'}">${escapeHtml(e.azione)}</span>
              <span class="tp-muted">${escapeHtml(e.dettaglio)}</span>
            </span>
          </div>`;
        }).join('');
      }
    } catch (e) {
      console.warn('Stato automazione non disponibile:', e);
    }
  }

  document.addEventListener('change', async (e) => {
    const rid = e.target.dataset && e.target.dataset.includi;
    if (!rid) return;
    try {
      const r = await apiPostJson(`/api/automazione/zona/${encodeURIComponent(rid)}/attiva`, { attiva: e.target.checked });
      zone = zone.map(z => z.room_id === rid ? { ...z, automazione: r.automazione } : z);
      renderZone();
      setTimeout(carica, 5000);   // il ciclo riparte subito sul server
    } catch (err) {
      e.target.checked = !e.target.checked;
      mostraMessaggio('msgZone', 'danger', escapeHtml(err.message));
    }
  });

  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-pausa]');
    if (!btn) return;
    try {
      const r = await apiPostJson(`/api/automazione/zona/${encodeURIComponent(btn.dataset.pausa)}/pausa`,
                                  { ore: Number(btn.dataset.ore) });
      ultimo.pause[btn.dataset.pausa] = r.pausa_fino;
      if (!r.pausa_fino) {
        delete ultimo.pause[btn.dataset.pausa];
        ultimo.zone = ultimo.zone.map(z => z.room_id === btn.dataset.pausa
          ? { ...z, pausa_fino: null, stato: null, fonte: null, motivo: 'Aggiornamento in corso…' } : z);
      }
      renderZone();
      setTimeout(carica, 5000);
    } catch (err) {
      mostraMessaggio('msgZone', 'danger', escapeHtml(err.message));
    }
  });

  window.toggleAutomazione = async function (checkbox) {
    try {
      const res = await fetch('/api/automazione/toggle', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      impostaStato(data.automazione_attiva);
    } catch (e) {
      checkbox.checked = !checkbox.checked;
    }
  };

  renderZone();
  carica();
  ogni(30000, carica, 10000);
  ascoltaLive(carica);
})();
