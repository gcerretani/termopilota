// TermoPilota — script dashboard.
// Primo paint renderizzato dal server (window.datiOrari); poi la dashboard si
// aggiorna in place da /api/dashboard ogni 5 minuti (e al ritorno sul tab).

(function () {
  let datiOrari = window.datiOrari || [];
  let grafico = null;
  let giornoAttivo = 'oggi';
  let ultimoFetch = Date.now();

  const oraCorrente = () => new Date().toISOString().slice(0, 13) + ':00';

  // ── Grafico comparazione ───────────────────────────────────────────────
  const pluginSfondo = {
    id: 'sfondoZone',
    beforeDraw(chart) {
      const { ctx, chartArea, scales } = chart;
      if (!chartArea) return;
      const { top, bottom } = chartArea;
      datiOrari.forEach((d, i) => {
        if (d.raccomandazione === 'ac') {
          const x0 = scales.x.getPixelForValue(i);
          const x1 = scales.x.getPixelForValue(i + 1);
          ctx.fillStyle = 'rgba(47,128,237,0.08)';
          ctx.fillRect(x0, top, (x1 - x0), bottom - top);
        }
      });
    },
  };

  function datasetGrafico() {
    return {
      labels: datiOrari.map(d => d.ora.slice(11, 16)),
      gas: datiOrari.map(d => d.costo_gas_kwh),
      ac: datiOrari.map(d => d.costo_ac_kwh),
      temp: datiOrari.map(d => d.temp_esterna),
    };
  }

  function creaGrafico() {
    const canvas = document.getElementById('graficoComparazione');
    if (!canvas || datiOrari.length === 0 || typeof Chart === 'undefined') return;
    const isMobile = window.matchMedia('(max-width: 576px)').matches;
    const d = datasetGrafico();

    grafico = new Chart(canvas, {
      type: 'line',
      data: {
        labels: d.labels,
        datasets: [
          { label: 'Caldaia (€/kWh_th)', data: d.gas, borderColor: '#e07b39', backgroundColor: 'rgba(224,123,57,.1)', borderWidth: 2.5, pointRadius: 0, tension: 0.3, yAxisID: 'y' },
          { label: 'AC (€/kWh_th)',      data: d.ac,  borderColor: '#2f80ed', backgroundColor: 'rgba(47,128,237,.1)', borderWidth: 2.5, pointRadius: 0, tension: 0.3, yAxisID: 'y' },
          { label: 'Temp. est. (°C)',    data: d.temp, borderColor: '#bbb', borderDash: [4, 3], borderWidth: 1.5, pointRadius: 0, tension: 0.4, yAxisID: 'y2' },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label(ctx) {
                if (ctx.dataset.yAxisID === 'y2') return ` Temp: ${ctx.parsed.y.toFixed(1)}°C`;
                return ` ${ctx.dataset.label}: ${ctx.parsed.y.toFixed(4)} €`;
              },
              afterBody(items) {
                const i = items[0].dataIndex;
                const dd = datiOrari[i];
                return [`Consiglio: ${dd.raccomandazione === 'gas' ? '🔥 Caldaia' : '❄️ Condizionatore'}`, `COP AC: ${dd.cop}`];
              },
            },
          },
        },
        scales: {
          x:  { ticks: { maxTicksLimit: isMobile ? 8 : 12, font: { size: 11 } }, grid: { color: 'rgba(127,127,127,.15)' } },
          y:  { title: { display: !isMobile, text: '€/kWh termico', font: { size: 11 } }, ticks: { callback: v => '€' + v.toFixed(3), font: { size: 11 } }, grid: { color: 'rgba(127,127,127,.15)' } },
          y2: { position: 'right', title: { display: !isMobile, text: '°C', font: { size: 11 } }, ticks: { callback: v => v.toFixed(0) + '°', font: { size: 11 } }, grid: { display: false } },
        },
      },
      plugins: [pluginSfondo],
    });
  }

  function aggiornaGrafico() {
    if (!grafico) { creaGrafico(); return; }
    const d = datasetGrafico();
    grafico.data.labels = d.labels;
    grafico.data.datasets[0].data = d.gas;
    grafico.data.datasets[1].data = d.ac;
    grafico.data.datasets[2].data = d.temp;
    grafico.update('none');
  }

  // ── Tabella oraria ─────────────────────────────────────────────────────
  function renderTabella(giorno) {
    giornoAttivo = giorno;
    const oggi   = new Date().toISOString().slice(0, 10);
    const domani = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
    const prefisso = giorno === 'oggi' ? oggi : domani;
    const adessoStr = oraCorrente();
    const righe = datiOrari.filter(d => d.ora.startsWith(prefisso));
    const tbody = document.getElementById('tabellaOraria');
    if (!tbody) return;

    tbody.innerHTML = righe.map(d => {
      const ora = d.ora.slice(11, 16);
      const isAdesso = d.ora === adessoStr;
      const badgeClass = d.raccomandazione === 'gas' ? 'badge-gas' : 'badge-ac';
      const badgeLabel = d.raccomandazione === 'gas' ? '🔥 Caldaia' : '❄️ Condizionatore';
      const risparmio = d.risparmio_pct != null ? `<strong>${d.risparmio_pct.toFixed(0)}%</strong>` : '—';
      const adesso = isAdesso ? '<span class="badge bg-warning text-dark ms-1" style="font-size:.7rem;">ADESSO</span>' : '';
      const cfr = (isAdesso && d.fonte_temp === 'cfr') ? '<span class="badge ms-1" style="font-size:.65rem;background:#1a7a4a;color:#fff;">📡 CFR</span>' : '';
      return `<tr class="${isAdesso ? 'evidenziata' : ''}">
        <td class="ps-3 fw-${isAdesso ? 'bold' : 'normal'}">${ora}${adesso}${cfr}</td>
        <td>${d.meteo_icon} <span class="d-none d-md-inline text-muted" style="font-size:.8rem;">${d.meteo_desc}</span></td>
        <td>${d.temp_esterna.toFixed(1)}°C</td>
        <td><span style="color:var(--ac-color);">${d.cop.toFixed(2)}</span></td>
        <td><span style="color:var(--gas-color);">€${d.costo_gas_kwh.toFixed(4)}</span></td>
        <td><span style="color:var(--ac-color);">€${d.costo_ac_kwh.toFixed(4)}</span></td>
        <td><span class="badge ${badgeClass} text-white">${badgeLabel}</span></td>
        <td class="pe-3">${risparmio}</td>
      </tr>`;
    }).join('');
  }

  // ── Refresh live (hero + stat card + grafico + tabella) ────────────────
  function renderHero(attuale, cfrInfo) {
    const card = document.getElementById('heroCard');
    if (!card || !attuale) return;
    card.classList.remove('gas', 'ac');
    card.classList.add(attuale.raccomandazione);

    const device = document.getElementById('heroDevice');
    if (device) {
      device.innerHTML = attuale.raccomandazione === 'gas'
        ? '<i class="bi bi-fire me-1"></i>Usa la Caldaia'
        : '<i class="bi bi-snow me-1"></i>Usa il Condizionatore';
    }
    const reason = document.getElementById('heroReason');
    if (reason) reason.textContent = attuale.motivo;
    const temp = document.getElementById('heroTemp');
    if (temp) temp.textContent = `${attuale.temp_esterna}°C`;

    const extra = document.getElementById('heroExtra');
    if (extra) {
      let html = '';
      if (cfrInfo) {
        html += `<div style="font-size:.78rem;opacity:.85;background:rgba(255,255,255,.2);border-radius:6px;padding:2px 7px;display:inline-block;margin-top:2px;">📡 CFR ${cfrInfo.ora}</div>`;
      }
      html += `<div style="font-size:.85rem;opacity:.8;margin-top:4px;">${attuale.meteo_icon} ${attuale.meteo_desc}</div>`;
      if (attuale.pioggia_prob > 20) {
        html += `<div style="font-size:.8rem;opacity:.7;">🌧 pioggia ${attuale.pioggia_prob}%</div>`;
      }
      extra.innerHTML = html;
    }

    const costi = document.getElementById('heroCosti');
    if (costi) {
      const gas = `<span><i class="bi bi-fire me-1"></i>Caldaia: <strong>${attuale.costo_gas_kwh.toFixed(3)} €/kWh<sub>th</sub></strong></span>`;
      const ac  = `<span><i class="bi bi-snow me-1"></i>AC: <strong>${attuale.costo_ac_kwh.toFixed(3)} €/kWh<sub>th</sub></strong></span>`;
      const cop = `<span><i class="bi bi-thermometer me-1"></i>COP AC: <strong>${attuale.cop}</strong></span>`;
      costi.innerHTML = (attuale.raccomandazione === 'gas' ? gas + ac : ac + gas) + cop;
    }
  }

  function renderStatCards(dati) {
    const a = dati.attuale;
    if (!a) return;
    const set = (id, html) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = html;
    };
    set('statGasVal', a.costo_gas_kwh.toFixed(3));
    set('statAcVal', a.costo_ac_kwh.toFixed(3));
    set('statOreVal', `${dati.ore_gas_oggi}h / ${dati.ore_ac_oggi}h`);
    set('statTotaliVal', `⛽ ${dati.prezzi.gas_totale_smc}€/Smc<br>⚡ ${dati.prezzi.luce_totale_kwh}€/kWh`);
  }

  function segnalaAggiornamento(testo, errore) {
    const chip = document.getElementById('aggiornatoAlle');
    if (!chip) return;
    chip.textContent = testo;
    chip.classList.toggle('errore', !!errore);
  }

  async function aggiornaDashboard() {
    ultimoFetch = Date.now();
    try {
      const res = await fetch('/api/dashboard');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const dati = await res.json();
      if (dati.raccomandazioni && dati.raccomandazioni.length > 0) {
        datiOrari = dati.raccomandazioni;
        aggiornaGrafico();
        renderTabella(giornoAttivo);
      }
      renderHero(dati.attuale, dati.cfr_info);
      renderStatCards(dati);
      segnalaAggiornamento(`Aggiornato alle ${dati.generato_alle}`, false);
    } catch (e) {
      console.warn('Refresh dashboard fallito:', e);
      segnalaAggiornamento('aggiornamento fallito — riprovo', true);
    }
  }

  renderTabella('oggi');
  creaGrafico();
  document.querySelectorAll('[data-giorno]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault();
      document.querySelectorAll('[data-giorno]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderTabella(btn.dataset.giorno);
    });
  });

  setInterval(aggiornaDashboard, 5 * 60 * 1000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - ultimoFetch > 60000) {
      aggiornaDashboard();
    }
  });

  // ── Chip risparmio (storico) ───────────────────────────────────────────
  async function caricaChipRisparmio() {
    try {
      const res = await fetch('/api/risparmi');
      if (!res.ok) return;
      const r = await res.json();
      if (r.stagione_eur > 0) {
        const chip = document.getElementById('chipRisparmio');
        const val = document.getElementById('chipRisparmioVal');
        if (chip && val) {
          val.textContent = r.stagione_eur.toLocaleString('it-IT', { style: 'currency', currency: 'EUR' });
          chip.style.display = '';
        }
      }
    } catch (e) { /* silenzioso: lo storico può essere vuoto */ }
  }
  caricaChipRisparmio();

  // ── Chip pannello adottato (stima da irraggiamento) ────────────────────
  async function caricaChipPannello() {
    try {
      const res = await fetch('/api/pannello');
      if (!res.ok) return;
      const p = await res.json();
      document.getElementById('chipPannelloKw').textContent = `${p.adesso_kw.toFixed(2)} kW`;
      document.getElementById('chipPannelloKwh').textContent = `${p.oggi_kwh.toFixed(1)} kWh`;
      document.getElementById('chipPannello').style.display = '';
    } catch (e) { /* silenzioso: dato accessorio */ }
  }
  caricaChipPannello();
  setInterval(caricaChipPannello, 10 * 60 * 1000);

  // ── Automazione ────────────────────────────────────────────────────────
  async function caricaStatoAutomazione() {
    try {
      const res = await fetch('/api/automazione');
      const data = await res.json();
      const toggle = document.getElementById('autoToggle');
      if (toggle) toggle.checked = data.attiva;
      const badge = document.getElementById('autoStatusBadge');
      if (badge) {
        if (data.attiva) { badge.className = 'badge bg-success'; badge.textContent = 'Attiva'; }
        else             { badge.className = 'badge bg-secondary'; badge.textContent = 'Non attiva'; }
      }

      const zoneDiv = document.getElementById('zoneStatus');
      if (zoneDiv && data.zone && data.zone.length > 0) {
        zoneDiv.innerHTML = data.zone.map(z => {
          const cls = z.fonte || 'off';
          const icona = z.fonte === 'ac' ? '❄️' : z.fonte === 'gas' ? '🔥' : '⏸';
          const etichetta = z.fonte === 'ac' ? 'Condizionatore' : z.fonte === 'gas' ? 'Caldaia' : 'In attesa';
          const tStanza = z.t_stanza != null ? z.t_stanza.toFixed(1) + '°C' : '—';
          const setpoint = z.setpoint != null ? z.setpoint.toFixed(1) + '°C' : '—';
          const progressPct = (z.t_stanza != null && z.setpoint != null)
            ? Math.min(100, Math.max(0, (z.t_stanza / z.setpoint) * 100)).toFixed(0) : null;
          return `<div class="col-md-6">
            <div class="zona-card ${cls}">
              <div class="d-flex justify-content-between align-items-center">
                <div>
                  <div class="fw-semibold">${z.nome}</div>
                  <div style="font-size:.82rem;color:var(--text-soft);">${icona} ${etichetta}</div>
                </div>
                <div class="text-end">
                  <div style="font-size:1.3rem;font-weight:700;">${tStanza}</div>
                  <div style="font-size:.78rem;color:var(--text-muted);">→ setpoint <strong>${setpoint}</strong></div>
                  <div style="font-size:.72rem;color:var(--text-faint);">${z.aggiornato || ''}</div>
                </div>
              </div>
              ${progressPct !== null ? `<div class="mt-2"><div class="progress" style="height:4px;border-radius:2px;"><div class="progress-bar ${z.fonte === 'ac' ? 'bg-primary' : 'bg-warning'}" style="width:${progressPct}%"></div></div></div>` : ''}
              ${z.costo_gas ? `<div class="mt-1" style="font-size:.75rem;color:var(--text-muted);">🔥 €${z.costo_gas.toFixed(3)} · ❄️ €${z.costo_ac.toFixed(3)} /kWh_th</div>` : ''}
              ${z.errore_ac ? `<div class="text-danger mt-1" style="font-size:.75rem;"><i class="bi bi-exclamation-triangle me-1"></i>${z.errore_ac}</div>` : ''}
            </div>
          </div>`;
        }).join('');
      }

      if (data.log && data.log.length > 0) {
        const wrap = document.getElementById('logEventiWrap');
        const log  = document.getElementById('logEventi');
        if (wrap) wrap.style.display = '';
        if (log) {
          log.innerHTML = data.log.map(e =>
            `<div class="log-item">
              <span class="text-muted me-2">${e.ts}</span>
              <strong>${e.zona}</strong>
              <span class="mx-1 ${e.azione.includes('AC') ? 'text-primary' : 'text-warning'}">${e.azione}</span>
              <span class="text-muted">${e.dettaglio}</span>
            </div>`
          ).join('');
        }
      }
    } catch (e) {
      console.warn('Stato automazione non disponibile:', e);
    }
  }

  window.toggleAutomazione = async function (checkbox) {
    try {
      const res = await fetch('/api/automazione/toggle', { method: 'POST' });
      const data = await res.json();
      const badge = document.getElementById('autoStatusBadge');
      if (badge) {
        if (data.automazione_attiva) { badge.className = 'badge bg-success'; badge.textContent = 'Attiva'; }
        else                          { badge.className = 'badge bg-secondary'; badge.textContent = 'Non attiva'; }
      }
    } catch (e) {
      checkbox.checked = !checkbox.checked;
    }
  };

  caricaStatoAutomazione();
  setInterval(caricaStatoAutomazione, 30000);
})();
