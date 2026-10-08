// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — pagina Storico: grafici storici e contatore risparmi.

(function () {
  const grafici = {};
  const G = TPGrafici;
  const TEMP_MAX_RISCALDAMENTO = 16; // allineato a storico.py
  let potenzaKw = 4.0;               // aggiornata da /api/risparmi

  function intervalloDaRange(range) {
    const oggi = new Date();
    const a = giornoLocale(oggi);
    let da;
    let risoluzione;
    if (range === '7g') {
      da = giornoLocale(new Date(Date.now() - 6 * 86400000));
      risoluzione = 'oraria';
    } else if (range === '30g') {
      da = giornoLocale(new Date(Date.now() - 29 * 86400000));
      risoluzione = 'giornaliera';
    } else {
      // Stagione termica: dal 1° ottobre (dell'anno scorso se siamo prima di ottobre)
      const annoInizio = oggi.getMonth() >= 9 ? oggi.getFullYear() : oggi.getFullYear() - 1;
      da = `${annoInizio}-10-01`;
      risoluzione = 'giornaliera';
    }
    return { da, a, risoluzione };
  }

  function creaOAggiornaGrafico(id, config, legenda) {
    const canvas = document.getElementById(id);
    if (!canvas || typeof Chart === 'undefined') return;
    if (grafici[id]) {
      grafici[id].data.labels = config.data.labels;
      config.data.datasets.forEach((ds, i) => { grafici[id].data.datasets[i].data = ds.data; });
      grafici[id].update();
    } else {
      grafici[id] = new Chart(canvas, config);
      if (legenda) G.legenda(legenda, grafici[id]);
    }
  }

  function etichette(punti, risoluzione) {
    if (risoluzione === 'oraria') {
      return punti.map(p => `${p.ora.slice(8, 10)}/${p.ora.slice(5, 7)} ${p.ora.slice(11, 16)}`);
    }
    return punti.map(p => `${p.giorno.slice(8, 10)}/${p.giorno.slice(5, 7)}`);
  }

  const serie = (chiave, extra = {}) => ({
    borderColor: G.colore(chiave),
    backgroundColor: G.gradiente(chiave, 0.18),
    pointHoverBackgroundColor: G.colore(chiave),
    pointHoverBorderColor: G.colore('pannello'),
    ...extra,
  });

  function aggiornaGrafici(punti, risoluzione) {
    const labels = etichette(punti, risoluzione);
    const oraria = risoluzione === 'oraria';
    const maxTickX = oraria ? (G.mobile() ? 4 : 7) : undefined;

    const opzPrezzi = G.opzioniBase({ unitaY: '€/kWh termico', tickY: v => `€${v.toFixed(2)}`, maxTickX });
    opzPrezzi.plugins.tooltip = { callbacks: { label: ctx => ` ${ctx.dataset.label}: €${ctx.parsed.y.toFixed(3)}` } };
    creaOAggiornaGrafico('graficoPrezzi', {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Caldaia', coloreVar: '--gas-color', data: punti.map(p => oraria ? p.costo_gas_kwh : p.costo_gas_medio), ...serie('gas') },
          { label: 'Pompa di calore', coloreVar: '--ac-color', data: punti.map(p => oraria ? p.costo_ac_kwh : p.costo_ac_medio), ...serie('ac') },
        ],
      },
      options: opzPrezzi,
    }, 'legendaPrezzi');

    const opzTemp = G.opzioniBase({ unitaY: '°C', tickY: v => `${v.toFixed(0)}°`, maxTickX });
    opzTemp.plugins.tooltip = { callbacks: { label: ctx => ` ${ctx.parsed.y.toFixed(1)}°C` } };
    creaOAggiornaGrafico('graficoTemperature', {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Temperatura', data: punti.map(p => oraria ? p.temp_esterna : p.temp_media), ...serie('neutro', { fill: 'start' }) },
        ],
      },
      options: opzTemp,
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

    const opzOre = G.opzioniBase({ tickY: v => `${v}h` });
    opzOre.scales.x.stacked = true;
    opzOre.scales.y.stacked = true;
    opzOre.scales.y.max = 24;
    opzOre.plugins.mirino = false;
    opzOre.plugins.tooltip = { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y} h` } };
    const barra = (chiave) => ({
      backgroundColor: G.colore(chiave),
      hoverBackgroundColor: G.colore(chiave),
      borderColor: G.colore('pannello'),
      borderWidth: { top: 2 },
      borderRadius: 4,
      borderSkipped: false,
      maxBarThickness: 28,
    });
    creaOAggiornaGrafico('graficoOreFonte', {
      type: 'bar',
      data: {
        labels: labelsGiorni,
        datasets: [
          { label: 'Caldaia', coloreVar: '--gas-color', data: giorni.map(g => g.ore_gas), ...barra('gas') },
          { label: 'Pompa di calore', coloreVar: '--ac-color', data: giorni.map(g => g.ore_ac), ...barra('ac') },
        ],
      },
      options: opzOre,
    }, 'legendaOre');

    let cumulato = 0;
    const serieCumulata = giorni.map(g => {
      cumulato += g.risparmio_eur || 0;
      return Math.round(cumulato * 100) / 100;
    });
    const opzRisparmio = G.opzioniBase({ tickY: v => formatoEuro(v) });
    opzRisparmio.plugins.tooltip = { callbacks: { label: ctx => ` ${formatoEuro(ctx.parsed.y)}` } };
    creaOAggiornaGrafico('graficoRisparmio', {
      type: 'line',
      data: {
        labels: labelsGiorni,
        datasets: [
          { label: 'Risparmio cumulativo', data: serieCumulata, ...serie('verde', { fill: true, tension: 0.25 }) },
        ],
      },
      options: opzRisparmio,
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
      // La risoluzione cambia la forma dei dati: si ricreano i grafici
      Object.keys(grafici).forEach(id => { grafici[id].destroy(); delete grafici[id]; });
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
      set('risparmioStagione', formatoEuro(r.stagione_eur));
      set('risparmioOggi', formatoEuro(r.oggi_eur));
      set('risparmioSettimana', formatoEuro(r.settimana_eur));
      set('risparmioOre', `${r.ore_ac_stagione} ore in pompa di calore dal ${r.inizio_stagione.slice(8, 10)}/${r.inizio_stagione.slice(5, 7)} · stima con ${r.potenza_kw} kW termici`);
    } catch (e) { /* silenzioso */ }
  }

  document.querySelectorAll('[data-range]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-range]').forEach(b => {
        b.classList.toggle('active', b === btn);
        b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
      });
      caricaStorico(btn.dataset.range);
    });
  });

  // Prima i risparmi (fissa potenzaKw), poi i grafici che la usano.
  caricaRisparmi().then(() => caricaStorico('7g'));
})();
