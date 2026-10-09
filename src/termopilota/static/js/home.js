// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — Home: consiglio per adesso, KPI, prossime ore, prezzi, risparmio.
// Primo paint dal server (window.datiOrari); poi refresh in place da /api/dashboard.

(function () {
  let datiOrari = window.datiOrari || [];
  const ORE_STRISCIA = 12;

  // ── Striscia prossime ore ──────────────────────────────────────────────
  function renderStriscia() {
    const el = document.getElementById('striscaOre');
    if (!el) return;
    const adesso = oraLocale();
    let inizio = datiOrari.findIndex(d => d.ora === adesso);
    if (inizio < 0) inizio = datiOrari.findIndex(d => d.ora > adesso);
    if (inizio < 0) inizio = 0;
    const ore = datiOrari.slice(inizio, inizio + ORE_STRISCIA);
    if (ore.length === 0) {
      el.innerHTML = '<div class="tp-muted small">Previsioni non disponibili.</div>';
      return;
    }
    el.innerHTML = ore.map((d, i) => {
      const gas = d.raccomandazione === 'gas';
      const etichetta = gas ? 'Caldaia' : 'Pompa di calore';
      return `<div class="tp-ora-pill${i === 0 && d.ora === adesso ? ' adesso' : ''}" role="listitem"
                   title="${d.ora.slice(11, 16)} · ${escapeHtml(d.meteo_desc)} · consiglio: ${etichetta}">
        <span class="tp-ora">${i === 0 && d.ora === adesso ? 'Ora' : d.ora.slice(11, 16)}</span>
        <span class="tp-meteo" aria-hidden="true">${d.meteo_icon}</span>
        <span class="tp-temp">${Math.round(d.temp_esterna)}°</span>
        <span class="tp-fonte-dot ${d.raccomandazione}" aria-label="${etichetta}"><i class="bi bi-${gas ? 'fire' : 'snow'}"></i></span>
      </div>`;
    }).join('');
  }

  // ── Hero + KPI ─────────────────────────────────────────────────────────
  function renderHero(attuale, cfrInfo) {
    const card = document.getElementById('heroCard');
    if (!card || !attuale) return;
    const gas = attuale.raccomandazione === 'gas';
    card.classList.remove('gas', 'ac');
    card.classList.add(attuale.raccomandazione);

    document.getElementById('heroDevice').innerHTML = gas
      ? '<i class="bi bi-fire me-1"></i>Usa la Caldaia'
      : '<i class="bi bi-snow me-1"></i>Usa il Condizionatore';
    document.getElementById('heroReason').textContent = attuale.motivo;
    document.getElementById('heroTemp').textContent = `${attuale.temp_esterna}°`;

    let extra = `<div>${attuale.meteo_icon} ${escapeHtml(attuale.meteo_desc)}</div>`;
    if (attuale.pioggia_prob > 20) extra += `<div>🌧 pioggia ${attuale.pioggia_prob}%</div>`;
    if (cfrInfo) extra += `<span class="tp-hero-tag"><i class="bi bi-broadcast"></i>CFR ${escapeHtml(cfrInfo.ora)}</span>`;
    document.getElementById('heroExtra').innerHTML = extra;

    document.getElementById('heroCosti').innerHTML =
      `<span class="tp-hero-pill${gas ? ' vincente' : ''}"><i class="bi bi-fire"></i>Caldaia <strong>${attuale.costo_gas_kwh.toFixed(3)} €/kWh<sub>th</sub></strong></span>` +
      `<span class="tp-hero-pill${gas ? '' : ' vincente'}"><i class="bi bi-snow"></i>AC <strong>${attuale.costo_ac_kwh.toFixed(3)} €/kWh<sub>th</sub></strong></span>` +
      `<span class="tp-hero-pill"><i class="bi bi-thermometer-half"></i>COP <strong>${attuale.cop}</strong></span>`;
  }

  function renderKpi(dati) {
    const set = (id, testo) => {
      const el = document.getElementById(id);
      if (el) el.textContent = testo;
    };
    const a = dati.attuale;
    if (a) {
      set('statGasVal', a.costo_gas_kwh.toFixed(3));
      set('statAcVal', a.costo_ac_kwh.toFixed(3));
    }
    set('statOreVal', `${dati.ore_gas_oggi}h / ${dati.ore_ac_oggi}h`);
    set('prezzoGasTot', dati.prezzi.gas_totale_smc);
    set('prezzoLuceTot', dati.prezzi.luce_totale_kwh);
  }

  // ── Stanze: termostato, condizionatore e decisione dell'automazione ────
  function rigaStanza(z) {
    // Zona appena reinclusa: l'ultima decisione del ciclo dice ancora "esclusa"
    const s = z.inclusa && z.stato_automazione === 'esclusa' ? null : z.stato_automazione;
    const icona = z.finestra_aperta ? ['danger', 'wind']
      : (s === 'ac' || s === 'affiancata') ? ['ac', 'snow']
      : z.sta_riscaldando ? ['gas', 'fire'] : ['', 'door-open'];
    const sub = [];
    if (z.target !== null && z.target !== undefined) sub.push(`target ${gradi(z.target)}`);
    if (z.setpoint !== null && z.setpoint !== undefined && z.setpoint !== z.target) sub.push(`termostato ${gradi(z.setpoint)}`);
    if (z.umidita !== null && z.umidita !== undefined) sub.push(`<i class="bi bi-droplet"></i> ${percento(z.umidita)}`);
    if (z.richiesta_calore_pct) sub.push(`<i class="bi bi-fire"></i> richiesta ${percento(z.richiesta_calore_pct)}`);
    if (z.ac) sub.push(z.ac.acceso
      ? `<i class="bi bi-snow"></i> ${escapeHtml(z.ac.nome)} ${gradi(z.ac.setpoint, 0)}`
      : `<i class="bi bi-snow"></i> AC spento`);
    const tag = [];
    if (s) tag.push(badgeStatoZona(s));
    else if (!z.inclusa) tag.push(badgeStatoZona('esclusa'));
    if (z.pausa_fino && s !== 'pausa') tag.push(`<span class="tp-badge-stato"><i class="bi bi-pause-circle"></i>pausa fino alle ${oraDaEpoch(z.pausa_fino)}</span>`);
    if (z.raggiungibile === false) tag.push(`<span class="tp-badge-stato danger" title="${escapeHtml(z.errore_termostato || '')}"><i class="bi bi-wifi-off"></i>termostato non raggiungibile</span>`);
    if (z.ac && z.ac.filtro_stato && z.ac.filtro_stato !== 'normal') {
      tag.push('<span class="tp-badge-stato danger"><i class="bi bi-funnel"></i>filtro AC da pulire</span>');
    }
    const link = z.room_id ? `/stanze/${encodeURIComponent(z.room_id)}` : null;
    const tagApertura = link ? `a class="tp-list-item" href="${link}"` : 'div class="tp-list-item"';
    return `<${tagApertura} title="${escapeHtml(z.motivo || '')}">
      <span class="tp-list-icon ${icona[0]}"><i class="bi bi-${icona[1]}"></i></span>
      <div class="tp-list-body">
        <div class="tp-list-title text-truncate">${escapeHtml(z.nome)}</div>
        <div class="tp-list-sub">${sub.join(' · ') || '—'}</div>
        ${tag.length ? `<div class="tp-list-tags">${tag.join('')}</div>` : ''}
      </div>
      <div class="tp-list-end fs-5 fw-light">${gradi(z.t_stanza)}</div>
    </${link ? 'a' : 'div'}>`;
  }

  function renderStanze(stanze) {
    const lista = document.getElementById('stanzeLista');
    if (!lista || !stanze) return;
    const errore = document.getElementById('stanzeErrore');
    if (errore) {
      errore.innerHTML = stanze.errore
        ? `<div class="alert alert-warning py-2 small mb-2">${escapeHtml(stanze.errore)}</div>` : '';
    }
    if (stanze.zone && stanze.zone.length) lista.innerHTML = stanze.zone.map(rigaStanza).join('');
  }

  async function aggiorna() {
    try {
      const res = await fetch('/api/dashboard');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const dati = await res.json();
      if (dati.raccomandazioni && dati.raccomandazioni.length > 0) {
        datiOrari = dati.raccomandazioni;
        renderStriscia();
      }
      renderHero(dati.attuale, dati.cfr_info);
      renderKpi(dati);
      renderStanze(dati.stanze);
      const consumo = document.getElementById('statConsumoAc');
      if (consumo && dati.consumo_ac_oggi_kwh !== null && dati.consumo_ac_oggi_kwh !== undefined) {
        consumo.textContent = ` · AC ${dati.consumo_ac_oggi_kwh.toFixed(1)} kWh`;
      }
      segnalaAggiornamento(`Aggiornato alle ${dati.generato_alle}`, false);
    } catch (e) {
      console.warn('Refresh dashboard fallito:', e);
      segnalaAggiornamento('Aggiornamento fallito', true);
    }
  }

  // ── Risparmio stagione ─────────────────────────────────────────────────
  async function caricaRisparmio() {
    try {
      const res = await fetch('/api/risparmi');
      if (!res.ok) return;
      const r = await res.json();
      const val = document.getElementById('chipRisparmioVal');
      if (val) val.textContent = formatoEuro(r.stagione_principale_eur || 0);
    } catch (e) { /* silenzioso: lo storico può essere vuoto */ }
  }

  // ── Pannello adottato (stima da irraggiamento) ─────────────────────────
  async function caricaPannello() {
    try {
      const res = await fetch('/api/pannello');
      if (!res.ok) return;
      const p = await res.json();
      document.getElementById('chipPannelloKw').textContent = `${p.adesso_kw.toFixed(2)} kW`;
      document.getElementById('chipPannelloKwh').textContent = `${p.oggi_kwh.toFixed(1)} kWh`;
      document.getElementById('chipPannello').style.display = '';
    } catch (e) { /* silenzioso: dato accessorio */ }
  }

  renderStriscia();
  renderStanze(window.stanze);
  if (window.generatoAlle) segnalaAggiornamento(`Aggiornato alle ${window.generatoAlle}`, false);
  caricaRisparmio();
  caricaPannello();
  ogni(5 * 60 * 1000, aggiorna);
  ascoltaLive(aggiorna);
  ogni(10 * 60 * 1000, caricaPannello);
})();
