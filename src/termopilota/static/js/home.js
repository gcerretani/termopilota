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
      if (val) val.textContent = formatoEuro(r.stagione_eur || 0);
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
  if (window.generatoAlle) segnalaAggiornamento(`Aggiornato alle ${window.generatoAlle}`, false);
  caricaRisparmio();
  caricaPannello();
  ogni(5 * 60 * 1000, aggiorna);
  ogni(10 * 60 * 1000, caricaPannello);
})();
