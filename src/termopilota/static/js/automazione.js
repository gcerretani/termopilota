// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — Automazione: interruttore, stato delle zone e log eventi (refresh 30 s).

(function () {
  function impostaStato(attiva) {
    const toggle = document.getElementById('autoToggle');
    if (toggle) toggle.checked = attiva;
    const badge = document.getElementById('autoStatusBadge');
    if (badge) {
      badge.classList.toggle('on', attiva);
      badge.textContent = attiva ? 'Attiva' : 'Non attiva';
    }
  }

  function cardZona(z) {
    const cls = z.fonte || 'off';
    const icona = z.fonte === 'ac' ? 'snow' : z.fonte === 'gas' ? 'fire' : 'pause-circle';
    const etichetta = z.fonte === 'ac' ? 'Condizionatore' : z.fonte === 'gas' ? 'Caldaia' : 'In attesa';
    const tStanza = z.t_stanza != null ? z.t_stanza.toFixed(1) + '°' : '—';
    const setpoint = z.setpoint != null ? z.setpoint.toFixed(1) + '°C' : '—';
    const progresso = (z.t_stanza != null && z.setpoint != null)
      ? Math.min(100, Math.max(0, (z.t_stanza / z.setpoint) * 100)).toFixed(0) : null;
    return `<div class="col-md-6 col-xl-4">
      <div class="zona-card ${cls}">
        <div class="d-flex justify-content-between align-items-start gap-2">
          <div class="min-w-0">
            <div class="fw-semibold text-truncate">${escapeHtml(z.nome)}</div>
            <div class="mt-1">${z.fonte ? `<span class="tp-badge-fonte ${cls}"><i class="bi bi-${icona}"></i>${etichetta}</span>`
                                        : `<span class="tp-pill-status"><i class="bi bi-${icona}"></i>${etichetta}</span>`}</div>
          </div>
          <div class="text-end">
            <div class="zona-temp">${tStanza}</div>
            <div class="small tp-muted mt-1">setpoint <strong>${setpoint}</strong></div>
          </div>
        </div>
        ${progresso !== null ? `<div class="zona-progress mt-3" role="progressbar" aria-valuenow="${progresso}" aria-valuemin="0" aria-valuemax="100"><div style="width:${progresso}%"></div></div>` : ''}
        <div class="d-flex justify-content-between mt-2 small tp-muted">
          <span>${z.costo_gas ? `<span class="tp-gas">€${z.costo_gas.toFixed(3)}</span> · <span class="tp-ac">€${z.costo_ac.toFixed(3)}</span> /kWh<sub>th</sub>` : ''}</span>
          <span>${escapeHtml(z.aggiornato || '')}</span>
        </div>
        ${z.errore_ac ? `<div class="text-danger mt-2 small"><i class="bi bi-exclamation-triangle me-1"></i>${escapeHtml(z.errore_ac)}</div>` : ''}
      </div>
    </div>`;
  }

  async function carica() {
    try {
      const res = await fetch('/api/automazione');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      impostaStato(data.attiva);

      const zoneDiv = document.getElementById('zoneStatus');
      if (zoneDiv && data.zone && data.zone.length > 0) {
        zoneDiv.innerHTML = data.zone.map(cardZona).join('');
      }

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

  carica();
  ogni(30000, carica, 10000);
})();
