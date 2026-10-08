// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — pagina Storico: grafici storici e contatore risparmi.

(function () {
  const grafici = {};
  const euro = (v) => v.toLocaleString('it-IT', { style: 'currency', currency: 'EUR' });
  const isMobile = window.matchMedia('(max-width: 576px)').matches;
  const TEMP_MAX_RISCALDAMENTO = 16; // allineato a storico.py
  let potenzaKw = 4.0;               // aggiornata da /api/risparmi

  function intervalloDaRange(range) {
    const oggi = new Date();
    const a = oggi.toISOString().slice(0, 10);
    let da;
    let risoluzione;
    if (range === '7g') {
      da = new Date(Date.now() - 6 * 86400000).toISOString().slice(0, 10);
      risoluzione = 'oraria';
    } else if (range === '30g') {
      da = new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10);
      risoluzione = 'giornaliera';
    } else {
      // Stagione termica: dal 1° ottobre (dell'anno scorso se siamo prima di ottobre)
      const annoInizio = oggi.getMonth() >= 9 ? oggi.getFullYear() : oggi.getFullYear() - 1;
      da = `${annoInizio}-10-01`;
      risoluzione = 'giornaliera';
    }
    return { da, a, risoluzione };
  }

  function opzioniBase(unitaY) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: isMobile ? 6 : 10, font: { size: 11 } }, grid: { color: 'rgba(127,127,127,.15)' } },
        y: { title: { display: !isMobile, text: unitaY, font: { size: 11 } }, ticks: { font: { size: 11 } }, grid: { color: 'rgba(127,127,127,.15)' } },
      },
    };
  }

  function creaOAggiornaGrafico(id, config) {
    const canvas = document.getElementById(id);
    if (!canvas || typeof Chart === 'undefined') return;
    if (grafici[id]) {
      grafici[id].data = config.data;
      grafici[id].update('none');
    } else {
      grafici[id] = new Chart(canvas, config);
    }
  }

  function etichette(punti, risoluzione) {
    if (risoluzione === 'oraria') {
      return punti.map(p => `${p.ora.slice(8, 10)}/${p.ora.slice(5, 7)} ${p.ora.slice(11, 16)}`);
    }
    return punti.map(p => `${p.giorno.slice(8, 10)}/${p.giorno.slice(5, 7)}`);
  }

  function aggiornaGrafici(punti, risoluzione) {
    const labels = etichette(punti, risoluzione);
    const oraria = risoluzione === 'oraria';

    const costoGas = punti.map(p => oraria ? p.costo_gas_kwh : p.costo_gas_medio);
    const costoAc = punti.map(p => oraria ? p.costo_ac_kwh : p.costo_ac_medio);
    creaOAggiornaGrafico('graficoPrezzi', {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Caldaia (€/kWh_th)', data: costoGas, borderColor: '#e07b39', backgroundColor: 'rgba(224,123,57,.1)', borderWidth: 2, pointRadius: 0, tension: 0.3 },
          { label: 'Pompa di calore (€/kWh_th)', data: costoAc, borderColor: '#2f80ed', backgroundColor: 'rgba(47,128,237,.1)', borderWidth: 2, pointRadius: 0, tension: 0.3 },
        ],
      },
      options: opzioniBase('€/kWh termico'),
    });

    const temp = punti.map(p => oraria ? p.temp_esterna : p.temp_media);
    creaOAggiornaGrafico('graficoTemperature', {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Temperatura (°C)', data: temp, borderColor: '#8a8e99', borderDash: [4, 3], backgroundColor: 'rgba(138,142,153,.08)', borderWidth: 1.5, pointRadius: 0, tension: 0.35, fill: true },
        ],
      },
      options: opzioniBase('°C'),
    });

    // Ore per fonte e risparmio cumulativo hanno senso solo per giorno:
    // in vista oraria aggreghiamo comunque per giorno.
    let giorni;
    if (oraria) {
      const mappa = new Map();
      punti.forEach(p => {
        const g = p.ora.slice(0, 10);
        if (!mappa.has(g)) mappa.set(g, { giorno: g, ore_gas: 0, ore_ac: 0, risparmio_eur: 0 });
        const agg = mappa.get(g);
        if (p.raccomandazione === 'ac') agg.ore_ac += 1; else agg.ore_gas += 1;
        if (p.raccomandazione === 'ac' && p.temp_esterna < TEMP_MAX_RISCALDAMENTO
            && p.costo_gas_kwh > p.costo_ac_kwh) {
          agg.risparmio_eur += (p.costo_gas_kwh - p.costo_ac_kwh) * potenzaKw;
        }
      });
      giorni = [...mappa.values()];
    } else {
      giorni = punti;
    }
    const labelsGiorni = giorni.map(g => `${g.giorno.slice(8, 10)}/${g.giorno.slice(5, 7)}`);

    creaOAggiornaGrafico('graficoOreFonte', {
      type: 'bar',
      data: {
        labels: labelsGiorni,
        datasets: [
          { label: 'Ore caldaia', data: giorni.map(g => g.ore_gas), backgroundColor: '#e07b39' },
          { label: 'Ore pompa di calore', data: giorni.map(g => g.ore_ac), backgroundColor: '#2f80ed' },
        ],
      },
      options: {
        ...opzioniBase('ore'),
        plugins: { legend: { display: true, labels: { font: { size: 11 } } } },
        scales: {
          x: { stacked: true, ticks: { maxTicksLimit: isMobile ? 6 : 12, font: { size: 11 } }, grid: { display: false } },
          y: { stacked: true, max: 24, ticks: { font: { size: 11 } }, grid: { color: 'rgba(127,127,127,.15)' } },
        },
      },
    });

    let cumulato = 0;
    const serieCumulata = giorni.map(g => {
      cumulato += g.risparmio_eur || 0;
      return Math.round(cumulato * 100) / 100;
    });
    creaOAggiornaGrafico('graficoRisparmio', {
      type: 'line',
      data: {
        labels: labelsGiorni,
        datasets: [
          { label: 'Risparmio cumulativo (€)', data: serieCumulata, borderColor: '#27ae60', backgroundColor: 'rgba(39,174,96,.12)', borderWidth: 2, pointRadius: 0, tension: 0.25, fill: true },
        ],
      },
      options: opzioniBase('€'),
    });
  }

  async function caricaStorico(range) {
    const { da, a, risoluzione } = intervalloDaRange(range);
    try {
      const res = await fetch(`/api/storico?da=${da}&a=${a}&risoluzione=${risoluzione}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const dati = await res.json();
      const vuoto = document.getElementById('storicoVuoto');
      if (vuoto) vuoto.style.display = dati.punti.length === 0 ? '' : 'none';
      aggiornaGrafici(dati.punti, risoluzione);
    } catch (e) {
      console.warn('Storico non disponibile:', e);
    }
  }

  async function caricaRisparmi() {
    try {
      const res = await fetch('/api/risparmi');
      if (!res.ok) return;
      const r = await res.json();
      potenzaKw = r.potenza_kw || potenzaKw;
      const set = (id, testo) => {
        const el = document.getElementById(id);
        if (el) el.textContent = testo;
      };
      set('risparmioStagione', euro(r.stagione_eur));
      set('risparmioOggi', euro(r.oggi_eur));
      set('risparmioSettimana', euro(r.settimana_eur));
      set('risparmioOre', `${r.ore_ac_stagione} ore in pompa di calore da ${r.inizio_stagione.slice(8, 10)}/${r.inizio_stagione.slice(5, 7)} · stima con ${r.potenza_kw} kW termici`);
    } catch (e) { /* silenzioso */ }
  }

  document.querySelectorAll('[data-range]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault();
      document.querySelectorAll('[data-range]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      caricaStorico(btn.dataset.range);
    });
  });

  // Prima i risparmi (fissa potenzaKw), poi i grafici che la usano.
  caricaRisparmi().then(() => caricaStorico('7g'));
})();
