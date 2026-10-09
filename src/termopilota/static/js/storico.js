// SPDX-FileCopyrightText: 2026 Giovanni Cerretani
// SPDX-License-Identifier: GPL-3.0-or-later
// TermoPilota — pagina Storico: grafici storici e contatore risparmi.

(function () {
  const grafici = {};
  const G = TPGrafici;

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

  // Un colore per condizionatore (fino a quattro, poi si ripetono)
  const COLORI_AC = [['ac', '--ac-color'], ['neutro', '--chart-neutral'], ['verde', '--green'], ['gas', '--gas-color']];

  function etichettaPeriodo(periodo) {
    const base = `${periodo.slice(8, 10)}/${periodo.slice(5, 7)}`;
    return periodo.length > 10 ? `${base} ${periodo.slice(11, 13)}:00` : base;
  }

  function graficoConsumo(energia, risoluzione) {
    const wrap = document.getElementById('consumoAcWrap');
    if (!wrap) return;
    wrap.style.display = energia.length ? '' : 'none';
    if (!energia.length) return;
    // Tutti i periodi tra il primo e l'ultimo, anche quelli senza consumo
    const presenti = [...new Set(energia.map(e => e.periodo))].sort();
    const periodi = [];
    const orario = presenti[0].length > 10;
    const formato = (d) => orario ? `${giornoLocale(d)}T${String(d.getHours()).padStart(2, '0')}` : giornoLocale(d);
    const verso = (p) => new Date(orario ? `${p}:00:00` : `${p}T00:00:00`);
    for (let d = verso(presenti[0]); formato(d) <= presenti[presenti.length - 1] && periodi.length < 2000;) {
      periodi.push(formato(d));
      if (orario) d.setHours(d.getHours() + 1); else d.setDate(d.getDate() + 1);
    }
    const ac = [...new Map(energia.map(e => [e.id, e.nome || 'Condizionatore'])).entries()];
    const totale = energia.reduce((s, e) => s + e.kwh, 0);
    const sub = document.getElementById('consumoAcSub');
    if (sub) sub.textContent = `kWh elettrici misurati dal contatore · totale ${totale.toFixed(1)} kWh`;
    const opz = G.opzioniBase({ tickY: v => `${v} kWh`, maxTickX: risoluzione === 'oraria' ? (G.mobile() ? 4 : 7) : undefined });
    opz.scales.x.stacked = true;
    opz.scales.y.stacked = true;
    opz.plugins.mirino = false;
    opz.plugins.tooltip = { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)} kWh` } };
    creaOAggiornaGrafico('graficoConsumo', {
      type: 'bar',
      data: {
        labels: periodi.map(etichettaPeriodo),
        datasets: ac.map(([id, nome], i) => {
          const [chiave, variabile] = COLORI_AC[i % COLORI_AC.length];
          const valori = new Map(energia.filter(e => e.id === id).map(e => [e.periodo, e.kwh]));
          return {
            label: nome, coloreVar: variabile, data: periodi.map(p => valori.get(p) || 0),
            backgroundColor: G.colore(chiave), hoverBackgroundColor: G.colore(chiave),
            borderRadius: 3, maxBarThickness: 28,
          };
        }),
      },
      options: opz,
    }, 'legendaConsumo');
  }

  function aggiornaGrafici(punti, risoluzione, misurati = {}) {
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
        agg.risparmio_eur += p.risparmio_eur || 0;     // dal server: solo le ore con l'AC acceso
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
    const datasetRisparmio = [
      { label: 'Stimato', coloreVar: '--green', data: serieCumulata, ...serie('verde', { fill: true, tension: 0.25 }) },
    ];
    if (Object.keys(misurati).length) {
      let cumulatoMisurato = 0;
      datasetRisparmio.push({
        label: 'Misurato (contatore AC)', coloreVar: '--ac-color',
        data: giorni.map(g => {
          cumulatoMisurato += (misurati[g.giorno] || {}).risparmio_eur || 0;
          return Math.round(cumulatoMisurato * 100) / 100;
        }),
        ...serie('ac', { tension: 0.25, borderDash: [5, 4] }),
      });
    }
    const opzRisparmio = G.opzioniBase({ tickY: v => formatoEuro(v) });
    opzRisparmio.plugins.tooltip = { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${formatoEuro(ctx.parsed.y)}` } };
    creaOAggiornaGrafico('graficoRisparmio', {
      type: 'line',
      data: { labels: labelsGiorni, datasets: datasetRisparmio },
      options: opzRisparmio,
    }, datasetRisparmio.length > 1 ? 'legendaRisparmio' : null);
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
      aggiornaGrafici(dati.punti, risoluzione, dati.misurati || {});
      graficoConsumo(dati.energia_ac || [], risoluzione);
    } catch (e) {
      console.warn('Storico non disponibile:', e);
    }
  }

  async function caricaRisparmi() {
    try {
      const res = await fetch('/api/risparmi');
      if (!res.ok) return;
      const r = await res.json();
      const set = (id, testo) => {
        const el = document.getElementById(id);
        if (el) el.textContent = testo;
      };
      const dal = `dal ${r.inizio_stagione.slice(8, 10)}/${r.inizio_stagione.slice(5, 7)}`;
      set('risparmioStagione', formatoEuro(r.stagione_principale_eur));
      set('risparmioOggi', formatoEuro(r.oggi_principale_eur));
      set('risparmioSettimana', formatoEuro(r.settimana_principale_eur));
      if (r.fonte === 'misurato') {
        set('risparmioOre', `Misurato dal contatore dei condizionatori ${dal}: ${r.kwh_ac_stagione.toFixed(1).replace('.', ',')} kWh in riscaldamento, `
          + `${formatoEuro(r.costo_ac_stagione_eur)} di elettricità al posto del gas.`);
        set('risparmioMisurato', `Stima con ${r.potenza_kw} kW termici: ${formatoEuro(r.stagione_eur)} in ${String(r.ore_ac_stagione).replace('.', ',')} ore di AC acceso.`);
      } else {
        set('risparmioOre', `Stima ${dal}: ${String(r.ore_ac_stagione).replace('.', ',')} ore di AC acceso in riscaldamento × ${r.potenza_kw} kW termici.`);
      }
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

  caricaRisparmi();
  caricaStorico('7g');
})();
