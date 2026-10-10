// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — Previsioni: grafico costo 48h, temperatura e dettaglio orario.
// Primo paint dal server (window.datiOrari); poi refresh in place da /api/dashboard.

(function () {
  let datiOrari = window.datiOrari || [];
  let giornoAttivo = 'oggi';
  let graficoCosti = null;
  let graficoTemp = null;
  const G = TPGrafici;
  // Temperatura misurata dell'ora corrente (le altre ore sono previsione)
  const FONTI_MISURATE = {
    netatmo: { sigla: 'Netatmo', nome: 'stazione Netatmo' },
    cfr: { sigla: 'CFR', nome: 'stazione CFR' },
  };

  // ── Dati per i grafici ─────────────────────────────────────────────────
  function etichette() { return datiOrari.map(d => d.ora.slice(11, 16)); }
  const indiceAdesso = () => datiOrari.findIndex(d => d.ora === oraLocale());

  function plugins() {
    const indici = [];
    const nomi = [];
    datiOrari.forEach((d, i) => {
      if (i > 0 && d.ora.slice(11, 13) === '00') {
        indici.push(i);
        nomi.push(d.ora.slice(0, 10) === giornoLocale(new Date(Date.now() + 86400000)) ? 'domani' : `${d.ora.slice(8, 10)}/${d.ora.slice(5, 7)}`);
      }
    });
    return {
      adesso: { indice: indiceAdesso() },
      fasce: { valori: datiOrari.map(d => d.raccomandazione) },
      giorni: { indici, etichette: nomi },
    };
  }

  function creaGrafici() {
    const cCosti = document.getElementById('graficoComparazione');
    const cTemp = document.getElementById('graficoTemperatura');
    if (!cCosti || datiOrari.length === 0 || typeof Chart === 'undefined') return;
    const euro4 = v => `€${v.toFixed(3)}`;

    // Larghezza fissa dell'asse y: i due grafici restano allineati ora per ora
    const allineaY = (scala) => { scala.width = 48; };
    const opzCosti = G.opzioniBase({ tickY: v => `€${v.toFixed(2)}` });
    opzCosti.scales.y.afterFit = allineaY;
    Object.assign(opzCosti.plugins, plugins(), {
      tooltip: {
        callbacks: {
          label: ctx => ` ${ctx.dataset.label}: ${euro4(ctx.parsed.y)}`,
          footer(items) {
            const d = datiOrari[items[0].dataIndex];
            const righe = [`${d.meteo_icon} ${d.temp_esterna.toFixed(1)}°C · COP ${d.cop}`,
              `Consiglio: ${d.raccomandazione === 'gas' ? 'caldaia' : 'pompa di calore'}`];
            if (d.risparmio_pct != null) righe.push(`Risparmio ${d.risparmio_pct.toFixed(0)}%`);
            return righe;
          },
        },
      },
    });

    graficoCosti = new Chart(cCosti, {
      type: 'line',
      data: {
        labels: etichette(),
        datasets: [
          {
            label: 'Caldaia', coloreVar: '--gas-color',
            data: datiOrari.map(d => d.costo_gas_kwh),
            borderColor: G.colore('gas'), backgroundColor: G.gradiente('gas', 0.18),
            pointHoverBackgroundColor: G.colore('gas'), pointHoverBorderColor: G.colore('pannello'),
            fill: true,
          },
          {
            label: 'Pompa di calore', coloreVar: '--ac-color',
            data: datiOrari.map(d => d.costo_ac_kwh),
            borderColor: G.colore('ac'), backgroundColor: G.gradiente('ac', 0.18),
            pointHoverBackgroundColor: G.colore('ac'), pointHoverBorderColor: G.colore('pannello'),
            fill: true,
          },
        ],
      },
      options: opzCosti,
    });
    G.legenda('legendaCosti', graficoCosti);

    if (cTemp) {
      const opzTemp = G.opzioniBase({ tickY: v => `${v.toFixed(0)}°` });
      opzTemp.scales.y.ticks.maxTicksLimit = 4;
      opzTemp.scales.y.afterFit = allineaY;
      Object.assign(opzTemp.plugins, plugins(), {
        adesso: { indice: indiceAdesso(), etichetta: false },
        fasce: false,
        tooltip: { callbacks: { label: ctx => ` ${ctx.parsed.y.toFixed(1)}°C` } },
      });
      graficoTemp = new Chart(cTemp, {
        type: 'line',
        data: {
          labels: etichette(),
          datasets: [{
            label: 'Temperatura esterna',
            data: datiOrari.map(d => d.temp_esterna),
            borderColor: G.colore('neutro'), backgroundColor: G.gradiente('neutro', 0.16),
            pointHoverBackgroundColor: G.colore('neutro'), pointHoverBorderColor: G.colore('pannello'),
            fill: 'start',
          }],
        },
        options: opzTemp,
      });
    }
  }

  function aggiornaGrafici() {
    if (!graficoCosti) { creaGrafici(); return; }
    const p = plugins();
    graficoCosti.data.labels = etichette();
    graficoCosti.data.datasets[0].data = datiOrari.map(d => d.costo_gas_kwh);
    graficoCosti.data.datasets[1].data = datiOrari.map(d => d.costo_ac_kwh);
    Object.assign(graficoCosti.options.plugins, p);
    graficoCosti.update('none');
    if (graficoTemp) {
      graficoTemp.data.labels = etichette();
      graficoTemp.data.datasets[0].data = datiOrari.map(d => d.temp_esterna);
      graficoTemp.options.plugins.adesso = { indice: p.adesso.indice, etichetta: false };
      graficoTemp.options.plugins.giorni = p.giorni;
      graficoTemp.update('none');
    }
  }

  // ── Dettaglio orario ───────────────────────────────────────────────────
  function righeGiorno(giorno) {
    const prefisso = giorno === 'oggi' ? giornoLocale() : giornoLocale(new Date(Date.now() + 86400000));
    return datiOrari.filter(d => d.ora.startsWith(prefisso));
  }

  function badgeFonte(d) {
    return d.raccomandazione === 'gas'
      ? '<span class="tp-badge-fonte gas"><i class="bi bi-fire"></i>Caldaia</span>'
      : '<span class="tp-badge-fonte ac"><i class="bi bi-snow"></i>Pompa</span>';
  }

  function renderDettaglio(giorno) {
    giornoAttivo = giorno;
    const adesso = oraLocale();
    const righe = righeGiorno(giorno);
    const vuoto = '<div class="tp-muted small py-2">Nessun dato per questo giorno.</div>';

    const tbody = document.getElementById('tabellaOraria');
    if (tbody) {
      tbody.innerHTML = righe.length === 0 ? `<tr><td colspan="8">${vuoto}</td></tr>` : righe.map(d => {
        const isAdesso = d.ora === adesso;
        const misurata = isAdesso && FONTI_MISURATE[d.fonte_temp]
          ? ` <span class="badge text-bg-success">${FONTI_MISURATE[d.fonte_temp].sigla}</span>` : '';
        return `<tr class="${isAdesso ? 'evidenziata' : ''}">
          <td>${d.ora.slice(11, 16)}${isAdesso ? ' <span class="badge text-bg-warning">ADESSO</span>' : ''}${misurata}</td>
          <td>${d.meteo_icon} <span class="tp-muted small">${escapeHtml(d.meteo_desc)}</span></td>
          <td>${d.temp_esterna.toFixed(1)}°C</td>
          <td>${d.cop.toFixed(2)}</td>
          <td class="tp-gas">€${d.costo_gas_kwh.toFixed(4)}</td>
          <td class="tp-ac">€${d.costo_ac_kwh.toFixed(4)}</td>
          <td>${badgeFonte(d)}</td>
          <td>${d.risparmio_pct != null ? `<strong>${d.risparmio_pct.toFixed(0)}%</strong>` : '—'}</td>
        </tr>`;
      }).join('');
    }

    const lista = document.getElementById('listaOraria');
    if (lista) {
      lista.innerHTML = righe.length === 0 ? vuoto : righe.map(d => {
        const isAdesso = d.ora === adesso;
        const costo = d.raccomandazione === 'gas' ? d.costo_gas_kwh : d.costo_ac_kwh;
        return `<details class="tp-ora-row${isAdesso ? ' adesso' : ''}"${isAdesso ? ' open' : ''}>
          <summary>
            <span class="tp-ora">${isAdesso ? 'Ora' : d.ora.slice(11, 16)}</span>
            <span aria-hidden="true">${d.meteo_icon}</span>
            <span class="tp-num">${d.temp_esterna.toFixed(1)}°</span>
            <span>${badgeFonte(d)}</span>
            <span class="tp-costo">€${costo.toFixed(3)} <i class="bi bi-chevron-down ms-1"></i></span>
          </summary>
          <div class="tp-ora-dettagli">
            <span>Meteo <strong>${escapeHtml(d.meteo_desc)}</strong></span>
            <span>COP <strong>${d.cop.toFixed(2)}</strong></span>
            <span>Caldaia <strong class="tp-gas">€${d.costo_gas_kwh.toFixed(4)}</strong></span>
            <span>Pompa <strong class="tp-ac">€${d.costo_ac_kwh.toFixed(4)}</strong></span>
            <span>Risparmio <strong>${d.risparmio_pct != null ? d.risparmio_pct.toFixed(0) + '%' : '—'}</strong></span>
            ${isAdesso && FONTI_MISURATE[d.fonte_temp] ? `<span>Temperatura <strong>${FONTI_MISURATE[d.fonte_temp].nome}</strong></span>` : ''}
          </div>
        </details>`;
      }).join('');
    }
  }

  async function aggiorna() {
    try {
      const res = await fetch('/api/dashboard');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const dati = await res.json();
      if (dati.raccomandazioni && dati.raccomandazioni.length > 0) {
        datiOrari = dati.raccomandazioni;
        aggiornaGrafici();
        renderDettaglio(giornoAttivo);
      }
      segnalaAggiornamento(`Aggiornato alle ${dati.generato_alle}`, false);
    } catch (e) {
      console.warn('Refresh previsioni fallito:', e);
      segnalaAggiornamento('Aggiornamento fallito', true);
    }
  }

  document.querySelectorAll('[data-giorno]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-giorno]').forEach(b => {
        b.classList.toggle('active', b === btn);
        b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
      });
      renderDettaglio(btn.dataset.giorno);
    });
  });

  creaGrafici();
  renderDettaglio('oggi');
  if (window.generatoAlle) segnalaAggiornamento(`Aggiornato alle ${window.generatoAlle}`, false);
  ogni(5 * 60 * 1000, aggiorna);
})();
